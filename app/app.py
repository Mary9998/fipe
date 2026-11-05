"""
Dashboard de Preços FIPE — App Flask
Lê os preços do Postgres da Aiven (tabela fipe_precos). Quando o cache está
velho, o próprio app busca dados novos na BrasilAPI (refresh_stale_series).
Não depende de Kafka.

Variáveis de ambiente esperadas:
  AIVEN_DB_URL   -> string de conexão fornecida pela Aiven
  PORT           -> porta HTTP (default 8080)
  CACHE_HOURS    -> horas antes de buscar dados novos na API (default 24)
"""
import datetime
import os
import time

import requests
from flask import Flask, jsonify, render_template

import fipe_client as fipe

app = Flask(__name__)

CACHE_HOURS = int(os.environ.get("CACHE_HOURS", 24))
START_TIME = time.time()

# Em produção o app roda via gunicorn (`gunicorn app:app`), que nunca executa o
# bloco `if __name__ == "__main__":`; por isso as tabelas são criadas aqui.
fipe.init_db()


def log_visit(path: str):
    with fipe.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO fipe_visits (path) VALUES (%s);", (path,))
        conn.commit()


def is_cache_stale(codigo: str) -> bool:
    with fipe.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT MAX(fetched_at) AS last_fetch FROM fipe_precos WHERE codigo_fipe = %s;",
                (codigo,),
            )
            row = cur.fetchone()
    if not row or not row["last_fetch"]:
        return True
    age = datetime.datetime.now(datetime.timezone.utc) - row["last_fetch"]
    return age > datetime.timedelta(hours=CACHE_HOURS)


def refresh_series(codigo: str):
    points = fipe.fetch_series_from_fipe(codigo)
    fipe.upsert_series(codigo, points)


def refresh_stale_series():
    for codigo in fipe.SERIES:
        if is_cache_stale(codigo):
            try:
                refresh_series(codigo)
            except (requests.RequestException, ValueError) as exc:  # noqa: BLE001
                app.logger.warning("Falha ao atualizar %s: %s", codigo, exc)


def get_series_data(codigo: str):
    """Últimos pontos em ordem cronológica."""
    with fipe.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ref_date, valor AS value, marca, modelo, ano_modelo
                FROM (
                    SELECT * FROM fipe_precos
                    WHERE codigo_fipe = %s
                    ORDER BY ref_date DESC
                    LIMIT %s
                ) t
                ORDER BY ref_date ASC;
                """,
                (codigo, fipe.POINTS_PER_SERIES),
            )
            return cur.fetchall()


def get_dashboard_data():
    refresh_stale_series()

    indicators = []
    for codigo, meta in fipe.SERIES.items():
        rows = get_series_data(codigo)
        latest = rows[-1] if rows else None
        previous = rows[-2] if len(rows) > 1 else None
        delta = None
        if latest and previous:
            delta = round(float(latest["value"]) - float(previous["value"]), meta["casas"])

        nome = meta["name"] or (
            f'{latest["marca"]} {latest["modelo"]} {latest["ano_modelo"]}' if latest else codigo
        )
        indicators.append(
            {
                "code": codigo,
                "name": nome,
                "unit": meta["unit"],
                "latest_value": round(float(latest["value"]), meta["casas"]) if latest else None,
                "latest_date": latest["ref_date"].strftime("%m/%Y") if latest else None,
                "delta": delta,
                "labels": [r["ref_date"].strftime("%m/%Y") for r in rows],
                "values": [round(float(r["value"]), meta["casas"]) for r in rows],
            }
        )

    with fipe.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS total FROM fipe_visits;")
            total_visits = cur.fetchone()["total"]

    return {
        "indicators": indicators,
        "total_visits": total_visits,
        "uptime_seconds": int(time.time() - START_TIME),
        "server_time": datetime.datetime.utcnow().isoformat() + "Z",
        "cache_hours": CACHE_HOURS,
    }


@app.route("/")
def dashboard():
    log_visit("/")
    data = get_dashboard_data()
    return render_template("dashboard.html", data=data)


@app.route("/api/indicators")
def api_indicators():
    log_visit("/api/indicators")
    return jsonify(get_dashboard_data())


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    """Força a atualização de todos os veículos, ignorando a idade do cache."""
    for codigo in fipe.SERIES:
        try:
            refresh_series(codigo)
        except (requests.RequestException, ValueError) as exc:  # noqa: BLE001
            return jsonify({"status": "error", "series": codigo, "detail": str(exc)}), 502
    return jsonify({"status": "ok", "refreshed": list(fipe.SERIES.keys())})


@app.route("/healthz")
def healthz():
    """Usado pelo pipeline de CI/CD e pela plataforma de deploy (health check)."""
    try:
        with fipe.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1;")
        return jsonify({"status": "ok"}), 200
    except Exception as exc:  # noqa: BLE001
        return jsonify({"status": "error", "detail": str(exc)}), 503


if __name__ == "__main__":
    refresh_stale_series()
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)