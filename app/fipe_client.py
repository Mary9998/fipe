"""Cliente de preços FIPE + acesso ao PostgreSQL.

Fontes de dados (tenta nesta ordem; se uma falhar, usa a próxima):
  1. API v2 da FIPE (fipe.parallelum.com.br / fipe.api.br) — sem cadastro, 500 consultas/dia
  2. BrasilAPI (brasilapi.com.br/api/fipe) — sem chave

Variáveis de ambiente:
  AIVEN_DB_URL  -> string de conexão do PostgreSQL (obrigatória)
  FIPE_TOKEN    -> (opcional) token gratuito de fipe.api.br; dobra o limite diário
"""
import datetime
import os
import re
import time
from contextlib import contextmanager

import psycopg2
import requests
from psycopg2.extras import RealDictCursor, execute_values

# Fonte 1: API v2 da FIPE (o endereço mudou de dono; tenta os dois).
FIPE_BASES = [
    "https://fipe.parallelum.com.br/api/v2",
    "https://fipe.api.br/api/v2",
]
# Fonte 2: BrasilAPI
BRASILAPI_BASE = "https://brasilapi.com.br/api/fipe"

TIMEOUT = 20  # segundos por requisição

# Veículos acompanhados no dashboard. A chave é o código FIPE.
#   ano_modelo: ano que você quer acompanhar (None = o mais novo disponível)
#   name:       nome mostrado no dashboard (None = usa o modelo vindo da API)
SERIES = {
    "095007-6": {"name": "Dolphin EV GS (Elétrico)", "ano_modelo": None, "unit": "R$", "casas": 2},
    "008030-6": {"name": "100 2.8 V6", "ano_modelo": None, "unit": "R$", "casas": 2},
    "001494-0": {"name": "ARGO DRIVE 1.0 6V Flex", "ano_modelo": None, "unit": "R$", "casas": 2},
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


# ------------------------------------------------------------ utilitários ---
def _get(base, caminho, params=None, headers=None, tentativas=3):
    """GET com novas tentativas em erro 5xx/rede. Erros 4xx não são repetidos."""
    ultimo_erro = None
    for n in range(tentativas):
        try:
            resp = requests.get(f"{base}{caminho}", params=params,
                                headers=headers, timeout=TIMEOUT)
            if resp.status_code >= 500:
                raise requests.HTTPError(f"{resp.status_code} em {resp.url}", response=resp)
            resp.raise_for_status()
            return resp.json()
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as erro:
            ultimo_erro = erro
            resp_erro = getattr(erro, "response", None)
            if resp_erro is not None and resp_erro.status_code < 500:
                raise
            time.sleep(2 * (n + 1))
    raise ultimo_erro


def parse_valor(texto):
    """'R$ 59.900,00' -> 59900.0"""
    limpo = re.sub(r"[^\d,]", "", str(texto or ""))
    if not limpo:
        raise ValueError(f"valor inválido: {texto!r}")
    return float(limpo.replace(",", "."))


def parse_mes(texto):
    """'novembro de 2026', 'novembro/2026' ou '2026-11' -> date(2026, 11, 1)"""
    t = str(texto or "").lower()
    iso = re.search(r"(\d{4})-(\d{2})", t)
    if iso:
        return datetime.date(int(iso.group(1)), int(iso.group(2)), 1)
    ano = re.search(r"(\d{4})", t)
    mes = next((n for nome, n in MESES.items() if nome in t), None)
    if not ano or not mes:
        raise ValueError(f"mês de referência não reconhecido: {texto!r}")
    return datetime.date(int(ano.group(1)), mes, 1)


# ------------------------------------------------ fonte 1: API v2 da FIPE ---
def _fget(caminho, params=None):
    """Consulta a API v2 tentando cada endereço base."""
    headers = {"X-Subscription-Token": os.environ["FIPE_TOKEN"]} if os.environ.get("FIPE_TOKEN") else None
    ultimo = None
    for base in FIPE_BASES:
        try:
            return _get(base, caminho, params, headers)
        except requests.RequestException as erro:
            ultimo = erro
    raise ultimo


def _ano_de(item):
    m = re.match(r"(\d{4,5})", str(item.get("code", "")))
    return int(m.group(1)) if m else 0


def _escolher_ano(anos, ano_modelo):
    if not anos:
        raise ValueError("a API não devolveu anos para esse código")
    if ano_modelo is not None:
        cand = [a for a in anos if _ano_de(a) == ano_modelo]
        if not cand:
            disp = ", ".join(str(_ano_de(a)) for a in anos)
            raise ValueError(f"ano {ano_modelo} não existe para esse código (disponíveis: {disp})")
        return cand[0]["code"]
    normais = [a for a in anos if _ano_de(a) < 32000] or anos  # 32000 = zero km
    return max(normais, key=_ano_de)["code"]


def _ponto_fipe(veiculo, preco, mes, ano_fallback):
    return {
        "ano_modelo": veiculo.get("modelYear") or ano_fallback,
        "ref_date": parse_mes(mes),
        "valor": parse_valor(preco),
        "marca": veiculo.get("brand"),
        "modelo": veiculo.get("model"),
        "combustivel": veiculo.get("fuel"),
    }


def _fonte_fipe(codigo, meta):
    anos = _fget(f"/cars/{codigo}/years")
    ano_id = _escolher_ano(anos, meta["ano_modelo"])
    base = f"/cars/{codigo}/years/{ano_id}"
    ano_num = _ano_de({"code": ano_id})

    # 1) histórico de preços em uma chamada só
    try:
        v = _fget(base + "/history")
        pontos = [
            _ponto_fipe(v, h.get("price"), h.get("month"), ano_num)
            for h in (v.get("priceHistory") or [])
        ]
        if pontos:
            return pontos[:N_TABELAS]
    except (requests.RequestException, ValueError):
        pass  # histórico pode exigir token; segue para o plano seguinte

    # 2) um preço por mês de referência
    pontos = []
    try:
        referencias = _fget("/references")[:N_TABELAS]
    except requests.RequestException:
        referencias = []
    for ref in referencias:
        try:
            v = _fget(base, {"reference": ref["code"]})
            pontos.append(_ponto_fipe(v, v.get("price"),
                                      v.get("referenceMonth") or ref.get("month"), ano_num))
        except (requests.RequestException, ValueError):
            continue
    if pontos:
        return pontos

    # 3) só o preço atual
    v = _fget(base)
    return [_ponto_fipe(v, v.get("price"), v.get("referenceMonth"), ano_num)]


# ------------------------------------------------------ fonte 2: BrasilAPI ---
def listar_tabelas(limite=N_TABELAS):
    """Tabelas de referência (meses) mais recentes da BrasilAPI."""
    return _get(BRASILAPI_BASE, "/tabelas/v1")[:limite]


def listar_marcas(tipo="carros"):
    return _get(BRASILAPI_BASE, f"/marcas/v1/{tipo}")


def listar_veiculos(tipo, codigo_marca):
    return _get(BRASILAPI_BASE, f"/veiculos/v1/{tipo}/{codigo_marca}")


def _fonte_brasilapi(codigo, meta):
    pontos = []
    try:
        tabelas = listar_tabelas()
    except requests.RequestException:
        tabelas = [{"codigo": None, "mes": None}]  # sem lista de meses: só o preço atual
    for tabela in tabelas:
        params = {"tabela_referencia": tabela["codigo"]} if tabela["codigo"] else None
        try:
            itens = _get(BRASILAPI_BASE, f"/preco/v1/{codigo}", params)
        except requests.RequestException:
            continue
        if meta["ano_modelo"] is not None:
            itens = [i for i in itens if i.get("anoModelo") == meta["ano_modelo"]]
        if not itens:
            continue
        item = max(itens, key=lambda i: i.get("anoModelo") or 0)
        try:
            ref = parse_mes(item.get("mesReferencia") or tabela.get("mes"))
        except ValueError:
            if not tabela.get("mes"):
                raise
            ref = parse_mes(tabela.get("mes"))
        pontos.append({
            "ano_modelo": item.get("anoModelo"),
            "ref_date": ref,
            "valor": parse_valor(item.get("valor")),
            "marca": item.get("marca"),
            "modelo": item.get("modelo"),
            "combustivel": item.get("combustivel"),
        })
    return pontos


# ------------------------------------------------------------ entrada única ---
def fetch_series_from_fipe(codigo):
    """Preço do veículo nos últimos meses. Tenta cada fonte até uma funcionar."""
    meta = SERIES[codigo]
    erros = []
    for nome, fonte in (("API FIPE", _fonte_fipe), ("BrasilAPI", _fonte_brasilapi)):
        try:
            pontos = fonte(codigo, meta)
            if pontos:
                return pontos
            erros.append(f"{nome}: sem dados")
        except (requests.RequestException, ValueError, KeyError) as erro:
            erros.append(f"{nome}: {erro}")
    raise ValueError(f"nenhum preço para {codigo} -> " + " | ".join(erros))


if __name__ == "__main__":
    # python fipe_client.py   -> testa as fontes com os códigos de SERIES
    for cod in SERIES:
        try:
            for p in fetch_series_from_fipe(cod)[:3]:
                print(cod, p)
        except ValueError as e:
            print(cod, "ERRO:", e)