"""Cliente da BrasilAPI (Tabela FIPE) + acesso ao PostgreSQL.

Substitui o bcb_client.py. Não precisa de chave de API.
Docs: https://brasilapi.com.br/docs#tag/FIPE

Variável de ambiente: AIVEN_DB_URL (string de conexão do PostgreSQL).
"""
import datetime
import os
import re
from contextlib import contextmanager

import psycopg2
import requests
from psycopg2.extras import RealDictCursor, execute_values

BASE_URL = "https://brasilapi.com.br/api/fipe"
TIMEOUT = 20  # segundos por requisição

# Veículos acompanhados no dashboard. A chave é o código FIPE.
#   ano_modelo: ano que você quer acompanhar (None = o mais novo disponível)
#   name:       nome mostrado no dashboard (None = usa o modelo vindo da API)
# TROQUE pelos carros que você quer. Para achar códigos: python fipe_client.py marcas
SERIES = {
    "095007-6": {"name": "Dolphin EV GS (Elétrico)", "ano_modelo": 2027, "unit": "R$", "casas": 2},
    "008030-6": {"name": "100 2.8 V6", "ano_modelo": 1995, "unit": "R$", "casas": 2},
    "001494-0": {"name": "ARGO DRIVE 1.0 6V Flex", "ano_modelo": 2026, "unit": "R$", "casas": 2},
}

N_TABELAS = 6            # quantos meses de histórico buscar
POINTS_PER_SERIES = 12   # pontos máximos por gráfico

MESES = {
    "janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4,
    "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9,
    "outubro": 10, "novembro": 11, "dezembro": 12,
}


# ----------------------------------------------------------------- banco ---
@contextmanager
def get_connection():
    conn = psycopg2.connect(os.environ["AIVEN_DB_URL"], cursor_factory=RealDictCursor)
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS fipe_precos (
                    codigo_fipe  TEXT        NOT NULL,
                    ano_modelo   INTEGER     NOT NULL,
                    ref_date     DATE        NOT NULL,
                    valor        NUMERIC(12,2) NOT NULL,
                    marca        TEXT,
                    modelo       TEXT,
                    combustivel  TEXT,
                    fetched_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (codigo_fipe, ano_modelo, ref_date)
                );
                CREATE TABLE IF NOT EXISTS fipe_visits (
                    id         SERIAL PRIMARY KEY,
                    path       TEXT NOT NULL,
                    visited_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
                """
            )
        conn.commit()


def upsert_series(codigo, points):
    """Grava os pontos de um código FIPE (insere ou atualiza)."""
    unicos = {}
    for p in points:  # evita 'ON CONFLICT' duplicado dentro do mesmo comando
        unicos[(p["ano_modelo"], p["ref_date"])] = p
    if not unicos:
        return
    linhas = [
        (codigo, p["ano_modelo"], p["ref_date"], p["valor"],
         p["marca"], p["modelo"], p["combustivel"])
        for p in unicos.values()
    ]
    with get_connection() as conn:
        with conn.cursor() as cur:
            execute_values(
                cur,
                """
                INSERT INTO fipe_precos
                    (codigo_fipe, ano_modelo, ref_date, valor, marca, modelo, combustivel)
                VALUES %s
                ON CONFLICT (codigo_fipe, ano_modelo, ref_date) DO UPDATE
                SET valor = EXCLUDED.valor, marca = EXCLUDED.marca,
                    modelo = EXCLUDED.modelo, combustivel = EXCLUDED.combustivel,
                    fetched_at = now();
                """,
                linhas,
            )
        conn.commit()


# ------------------------------------------------------------------ API ---
def _get(caminho, params=None):
    resp = requests.get(f"{BASE_URL}{caminho}", params=params, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def parse_valor(texto):
    """'R$ 59.900,00' -> 59900.0"""
    limpo = re.sub(r"[^\d,]", "", texto or "")
    if not limpo:
        raise ValueError(f"valor inválido: {texto!r}")
    return float(limpo.replace(",", "."))


def parse_mes(texto):
    """'novembro de 2026' ou 'novembro/2026' -> date(2026, 11, 1)"""
    t = (texto or "").lower()
    ano = re.search(r"(\d{4})", t)
    mes = next((n for nome, n in MESES.items() if nome in t), None)
    if not ano or not mes:
        raise ValueError(f"mês de referência não reconhecido: {texto!r}")
    return datetime.date(int(ano.group(1)), mes, 1)


def listar_tabelas(limite=N_TABELAS):
    """Tabelas de referência (meses) mais recentes. Cada item tem 'codigo' e 'mes'."""
    return _get("/tabelas/v1")[:limite]


def listar_marcas(tipo="carros"):
    return _get(f"/marcas/v1/{tipo}")


def listar_veiculos(tipo, codigo_marca):
    return _get(f"/veiculos/v1/{tipo}/{codigo_marca}")


def fetch_series_from_fipe(codigo):
    """Preço do veículo em cada um dos últimos meses. Levanta erro se a API falhar."""
    meta = SERIES[codigo]
    pontos = []
    for tabela in listar_tabelas():
        itens = _get(f"/preco/v1/{codigo}", {"tabela_referencia": tabela["codigo"]})
        if not itens:
            continue
        if meta["ano_modelo"] is not None:
            itens = [i for i in itens if i.get("anoModelo") == meta["ano_modelo"]]
        if not itens:
            continue
        item = max(itens, key=lambda i: i.get("anoModelo") or 0)
        try:
            ref = parse_mes(item.get("mesReferencia") or tabela.get("mes"))
        except ValueError:
            ref = parse_mes(tabela.get("mes"))
        pontos.append({
            "ano_modelo": item.get("anoModelo"),
            "ref_date": ref,
            "valor": parse_valor(item.get("valor")),
            "marca": item.get("marca"),
            "modelo": item.get("modelo"),
            "combustivel": item.get("combustivel"),
        })
    if not pontos:
        raise ValueError(f"nenhum preço encontrado para {codigo}")
    return pontos


if __name__ == "__main__":
    import sys
    # python fipe_client.py            -> testa a API com os códigos de SERIES
    # python fipe_client.py marcas     -> lista marcas de carros (para achar códigos)
    if len(sys.argv) > 1 and sys.argv[1] == "marcas":
        for m in listar_marcas()[:30]:
            print(m)
    else:
        for cod in SERIES:
            for p in fetch_series_from_fipe(cod)[:3]:
                print(cod, p)