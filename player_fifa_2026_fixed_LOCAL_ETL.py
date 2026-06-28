#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
player_fifa_2026_fixed.py

Extrai TODOS os jogadores do PDF oficial da FIFA World Cup 2026 e, opcionalmente,
enriquece os jogadores com Wikidata/Wikipedia.

Por que esta versão existe:
- O PDF da FIFA quebra cabeçalhos como "SQUAD LISTBrazil (BRA)" entre páginas.
- O parser antigo pulava a página quando não achava o cabeçalho antes de ler a tabela.
- Esta versão usa fallback por ordem oficial das páginas + validação de 48 seleções/1248 jogadores.

Saídas principais:
  data/gold/gold_worldcup_players_2026_fifa_official.csv
  data/gold/gold_worldcup_players_2026_fifa_official_report.csv
  data/gold/player_sources_enriched.csv                  # quando --enrich
  data/gold/player_sources_scraping_report.csv           # quando --enrich
  data/raw/fifa_squad_lists_english_2026.pdf
  data/raw/player_sources_cache_fifa_2026.json

Uso rápido:
  python3 player_fifa_2026_fixed.py --extract-only --refresh-fifa-squad
  python3 player_fifa_2026_fixed.py --all --enrich --sources wikidata ptwiki --workers 8

Instalação:
  pip install pandas requests pdfplumber tqdm Unidecode python-dateutil
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from tqdm.auto import tqdm

try:
    from unidecode import unidecode
except Exception:  # pragma: no cover
    def unidecode(x: str) -> str:
        return x

# -----------------------------------------------------------------------------
# Paths - MODO LOCAL_ETL
# -----------------------------------------------------------------------------

# IMPORTANTE:
# Este projeto agora assume que tudo fica dentro da própria pasta etl.
# Exemplo:
#   /home/perri/Área de trabalho/analytica-model/model/etl/
#       player_fifa_2026_fixed.py
#       team_matches_free_open_sources_2022_2026.py
#       worldcup_2026_feature_builder.py
#       data/raw/
#       data/gold/
#
# Ou seja: NÃO tenta mais subir para ../../data.
SCRIPT_DIR = Path(__file__).resolve().parent
ETL_ROOT = SCRIPT_DIR
DATA_ROOT = ETL_ROOT / "data"
RAW_DIR = DATA_ROOT / "raw"
GOLD_DIR = DATA_ROOT / "gold"
RAW_DIR.mkdir(parents=True, exist_ok=True)
GOLD_DIR.mkdir(parents=True, exist_ok=True)

FIFA_SQUAD_PDF_URL = os.getenv(
    "FIFA_SQUAD_PDF_URL",
    "https://fdp.fifa.org/assetspublic/ce281/pdf/SquadLists-English.pdf",
)
FIFA_SQUAD_PDF_PATH = RAW_DIR / "fifa_squad_lists_english_2026.pdf"
FIFA_PLAYERS_OUT = GOLD_DIR / "gold_worldcup_players_2026_fifa_official.csv"
FIFA_PLAYERS_REPORT_OUT = GOLD_DIR / "gold_worldcup_players_2026_fifa_official_report.csv"
ENRICHED_OUT = GOLD_DIR / "player_sources_enriched.csv"
ENRICH_REPORT_OUT = GOLD_DIR / "player_sources_scraping_report.csv"
CACHE_PATH = RAW_DIR / "player_sources_cache_fifa_2026.json"

# -----------------------------------------------------------------------------
# FIFA PDF helpers
# -----------------------------------------------------------------------------

# Fallback oficial por ordem de páginas do PDF SquadLists-English.pdf.
# Isso conserta páginas cujo cabeçalho vem colado/quebrado pelo extrator PDF.
FIFA_TEAM_ORDER_BY_PAGE: list[tuple[str, str]] = [
    ("Algeria", "ALG"),
    ("Argentina", "ARG"),
    ("Australia", "AUS"),
    ("Austria", "AUT"),
    ("Belgium", "BEL"),
    ("Bosnia And Herzegovina", "BIH"),
    ("Brazil", "BRA"),
    ("Cabo Verde", "CPV"),
    ("Canada", "CAN"),
    ("Colombia", "COL"),
    ("Congo DR", "COD"),
    ("Côte D'Ivoire", "CIV"),
    ("Croatia", "CRO"),
    ("Curaçao", "CUW"),
    ("Czechia", "CZE"),
    ("Ecuador", "ECU"),
    ("Egypt", "EGY"),
    ("England", "ENG"),
    ("France", "FRA"),
    ("Germany", "GER"),
    ("Ghana", "GHA"),
    ("Haiti", "HAI"),
    ("IR Iran", "IRN"),
    ("Iraq", "IRQ"),
    ("Japan", "JPN"),
    ("Jordan", "JOR"),
    ("Korea Republic", "KOR"),
    ("Mexico", "MEX"),
    ("Morocco", "MAR"),
    ("Netherlands", "NED"),
    ("New Zealand", "NZL"),
    ("Norway", "NOR"),
    ("Panama", "PAN"),
    ("Paraguay", "PAR"),
    ("Portugal", "POR"),
    ("Qatar", "QAT"),
    ("Saudi Arabia", "KSA"),
    ("Scotland", "SCO"),
    ("Senegal", "SEN"),
    ("South Africa", "RSA"),
    ("Spain", "ESP"),
    ("Sweden", "SWE"),
    ("Switzerland", "SUI"),
    ("Tunisia", "TUN"),
    ("Türkiye", "TUR"),
    ("Uruguay", "URU"),
    ("USA", "USA"),
    ("Uzbekistan", "UZB"),
]

FIFA_TEAM_NAME_ALIASES = {
    "bosnia and herzegovina": "Bosnia and Herzegovina",
    "bosnia & herzegovina": "Bosnia and Herzegovina",
    "cabo verde": "Cabo Verde",
    "cape verde": "Cabo Verde",
    "congo dr": "DR Congo",
    "dr congo": "DR Congo",
    "democratic republic of the congo": "DR Congo",
    "cote d ivoire": "Ivory Coast",
    "cote divoire": "Ivory Coast",
    "côte d'ivoire": "Ivory Coast",
    "côte d ivoire": "Ivory Coast",
    "côte d'ivoire": "Ivory Coast",
    "ivory coast": "Ivory Coast",
    "curacao": "Curaçao",
    "curaçao": "Curaçao",
    "ir iran": "Iran",
    "iran": "Iran",
    "korea republic": "South Korea",
    "south korea": "South Korea",
    "republic of korea": "South Korea",
    "turkiye": "Turkey",
    "türkiye": "Turkey",
    "türkiye": "Turkey",
    "turkey": "Turkey",
    "usa": "United States",
    "us": "United States",
    "united states": "United States",
    "united states of america": "United States",
}

USER_AGENT = os.getenv(
    "PLAYER_SOURCES_USER_AGENT",
    "analytica-model-copa2026/0.3 (fifa squad extraction; academic use)",
)

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
WIKIPEDIA_API_PT = "https://pt.wikipedia.org/w/api.php"
FOOTBALLER_QID = "Q937857"

DATE_RE = re.compile(r"\b\d{2}/\d{2}/\d{4}\b")
POS_SET = {"GK", "DF", "MF", "FW"}


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean_value(value: Any) -> str:
    if value is None:
        return ""
    s = str(value).replace("\xa0", " ").replace("\n", " ").strip()
    return re.sub(r"\s+", " ", s)


def norm_text(value: Any) -> str:
    s = clean_value(value)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def slugify_id(value: Any) -> str:
    s = unidecode(clean_value(value)).lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return re.sub(r"_+", "_", s).strip("_")


def as_int(value: Any) -> int | None:
    s = clean_value(value)
    if not s:
        return None
    m = re.search(r"-?\d+", s)
    return int(m.group(0)) if m else None


def normalize_country_alias(value: Any) -> str:
    raw = clean_value(value)
    if not raw:
        return ""
    return FIFA_TEAM_NAME_ALIASES.get(norm_text(raw), raw)


def clean_fifa_person_name(value: Any) -> str:
    s = clean_value(value)
    if not s:
        return ""
    # Nomes do PDF misturam sobrenome em caixa alta. Title melhora busca externa.
    parts = []
    for part in s.split():
        if part.isupper() and len(part) > 1:
            parts.append(part.title())
        else:
            parts.append(part)
    return clean_value(" ".join(parts))


def fifa_player_search_name(first_names: str, last_names: str, raw_player_name: str) -> str:
    first_names = clean_fifa_person_name(first_names)
    last_names = clean_fifa_person_name(last_names)
    if first_names and last_names:
        return clean_value(f"{first_names} {last_names}")
    raw = clean_value(raw_player_name)
    parts = raw.split()
    if len(parts) >= 2 and parts[0].isupper():
        return clean_fifa_person_name(" ".join(parts[1:] + [parts[0]]))
    return clean_fifa_person_name(raw)


def get_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=4,
        connect=4,
        read=4,
        status=4,
        backoff_factor=1.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=32, pool_maxsize=32)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def download_fifa_squad_pdf(url: str = FIFA_SQUAD_PDF_URL, refresh: bool = False) -> Path:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    if FIFA_SQUAD_PDF_PATH.exists() and not refresh:
        return FIFA_SQUAD_PDF_PATH
    print(f"Baixando PDF oficial da FIFA: {url}")
    r = get_session().get(url, timeout=(10, 90), stream=True)
    r.raise_for_status()
    tmp = FIFA_SQUAD_PDF_PATH.with_suffix(".tmp")
    with tmp.open("wb") as f:
        for chunk in r.iter_content(chunk_size=1024 * 256):
            if chunk:
                f.write(chunk)
    tmp.replace(FIFA_SQUAD_PDF_PATH)
    return FIFA_SQUAD_PDF_PATH


def parse_fifa_team_header(page_text: str, page_index: int) -> tuple[str, str, str]:
    """Extrai seleção/código do cabeçalho. Se falhar, usa a ordem oficial da página."""
    text = page_text or ""
    text = text.replace("\xa0", " ")

    patterns = [
        r"SQUAD\s*LIST\s*([^\n()]{2,80}?)\s*\(([A-Z]{3})\)",
        r"^\s*([^\n()]{2,80}?)\s*\(([A-Z]{3})\)\s*(?:\n|#\s*POS)",
    ]
    for pat in patterns:
        m = re.search(pat, text, flags=re.IGNORECASE | re.MULTILINE)
        if m:
            team = clean_value(m.group(1)).replace("SQUAD LIST", "").strip()
            code = clean_value(m.group(2)).upper()
            if team and code:
                return team, code, "pdf_header"

    if 0 <= page_index < len(FIFA_TEAM_ORDER_BY_PAGE):
        team, code = FIFA_TEAM_ORDER_BY_PAGE[page_index]
        return team, code, "fallback_page_order"

    return "", "", "not_found"


def looks_like_player_table_row(cells: list[str]) -> bool:
    if not cells:
        return False
    joined = " ".join(cells)
    if not DATE_RE.search(joined):
        return False
    return any(c in POS_SET for c in cells[:4]) or bool(re.match(r"^\s*(?:\d+\s+)?(?:GK|DF|MF|FW)\b", joined))


def parse_table_row_known_layout(cells: list[str], page_no: int, team: str, code: str, header_source: str) -> dict[str, Any] | None:
    """Parser para o layout de tabela do PDF da FIFA.

    Layout observado pelo pdfplumber:
    0 #, 1 POS, 2 PLAYER NAME, 4 FIRST NAME(S), 5 LAST NAME(S),
    7 NAME ON SHIRT, 8 DOB, 10 CLUB, 12 HEIGHT, 13 CAPS, 14 GOALS.
    """
    cells = [clean_value(x) for x in cells]
    if len(cells) < 12 or not looks_like_player_table_row(cells):
        return None

    # Caso padrão: primeira coluna é o número da camisa.
    if cells[0].isdigit() and len(cells) >= 15:
        squad_number = as_int(cells[0])
        pos = cells[1]
        raw_player_name = cells[2]
        first_names = cells[4] if len(cells) > 4 else ""
        last_names = cells[5] if len(cells) > 5 else ""
        name_on_shirt = cells[7] if len(cells) > 7 else ""
        dob = cells[8] if len(cells) > 8 else ""
        club = cells[10] if len(cells) > 10 else ""
        height_cm = as_int(cells[12] if len(cells) > 12 else None)
        caps = as_int(cells[13] if len(cells) > 13 else None)
        goals = as_int(cells[14] if len(cells) > 14 else None)
    else:
        # Layout alternativo: tenta achar posição e data em qualquer coluna.
        pos_idx = next((i for i, c in enumerate(cells) if c in POS_SET), -1)
        dob_idx = next((i for i, c in enumerate(cells) if DATE_RE.search(c)), -1)
        if pos_idx < 0 or dob_idx < 0:
            return None
        squad_number = as_int(cells[0]) if cells[0].isdigit() else None
        pos = cells[pos_idx]
        raw_player_name = cells[pos_idx + 1] if pos_idx + 1 < len(cells) else ""
        first_names = cells[pos_idx + 3] if pos_idx + 3 < len(cells) else ""
        last_names = cells[pos_idx + 4] if pos_idx + 4 < len(cells) else ""
        name_on_shirt = cells[dob_idx - 1] if dob_idx - 1 >= 0 else ""
        dob = cells[dob_idx]
        club = cells[dob_idx + 1] if dob_idx + 1 < len(cells) else ""
        height_cm = as_int(cells[dob_idx + 2] if dob_idx + 2 < len(cells) else None)
        caps = as_int(cells[dob_idx + 3] if dob_idx + 3 < len(cells) else None)
        goals = as_int(cells[dob_idx + 4] if dob_idx + 4 < len(cells) else None)

    if pos not in POS_SET or not DATE_RE.search(dob):
        return None
    if not raw_player_name and not (first_names or last_names):
        return None

    player_name = fifa_player_search_name(first_names, last_names, raw_player_name)
    country_en = normalize_country_alias(team)
    squad_num_str = str(squad_number or 0).zfill(2)
    player_id = f"fifa_{code}_{squad_num_str}_{slugify_id(player_name)}"

    return {
        "player_id": player_id,
        "source_player_id": f"FIFA:{code}:{squad_number or ''}",
        "player_name": player_name,
        "fifa_player_name": clean_fifa_person_name(raw_player_name),
        "first_names": clean_fifa_person_name(first_names),
        "last_names": clean_fifa_person_name(last_names),
        "name_on_shirt": clean_value(name_on_shirt),
        "country_en": country_en,
        "fifa_team_name": clean_value(team),
        "fifa_team_code": code,
        "team_key": slugify_id(country_en),
        "squad_number": squad_number,
        "position": pos,
        "date_of_birth": clean_value(dob),
        "club": clean_value(club),
        "club_raw": clean_value(club),
        "height_cm": height_cm,
        "caps": caps,
        "goals": goals,
        "is_called_up": True,
        "squad_status": "fifa_official_final_squad",
        "input_source": "fifa_official_squad_pdf",
        "source_url": FIFA_SQUAD_PDF_URL,
        "source_page": page_no,
        "pdf_header_source": header_source,
        "parse_method": "pdfplumber_table",
    }



def parse_name_block_before_dob(pre_text: str) -> tuple[str, str, str, str]:
    """Extrai nome a partir do bloco textual antes da data de nascimento.

    O texto bruto do PDF costuma trazer colunas coladas, por exemplo:
    "ACEVEDO Carlos 123456 Carlos ACEVEDO ACEVEDO".
    Quando existe ID FIFA numérico, usa tudo antes dele como PLAYER NAME.
    Quando não existe, tenta detectar prefixo repetido e usa esse prefixo.
    """
    pre_text = clean_value(pre_text)
    tokens = pre_text.split()
    if not tokens:
        return "", "", "", ""

    # Se houver um ID FIFA no meio da linha, tudo antes dele é o PLAYER NAME.
    id_idx = next((i for i, t in enumerate(tokens) if re.fullmatch(r"\d{5,}", t)), -1)
    if id_idx > 0:
        raw = " ".join(tokens[:id_idx])
        rest = tokens[id_idx + 1:]
        # Heurística leve: não tenta adivinhar todas as colunas duplicadas.
        return raw, "", "", " ".join(rest[-2:]) if rest else ""

    # Procura o menor prefixo que se repete depois. Isso captura casos em que
    # PLAYER NAME aparece de novo nas colunas FIRST/LAST/POPULAR/SHIRT.
    max_k = min(6, max(1, len(tokens) // 2))
    lower = [norm_text(t) for t in tokens]
    for k in range(1, max_k + 1):
        prefix = lower[:k]
        if not any(prefix):
            continue
        for j in range(k, len(tokens) - k + 1):
            if lower[j:j + k] == prefix:
                return " ".join(tokens[:k]), "", "", ""

    # Caso sem repetição: usa uma janela curta e segura para não incorporar clube/colunas.
    if len(tokens) >= 2:
        if tokens[0].isupper() and not tokens[1].isupper():
            return " ".join(tokens[:2]), "", "", ""
        if len(tokens) >= 3 and tokens[0].isupper() and tokens[1].isupper() and not tokens[2].isupper():
            return " ".join(tokens[:3]), "", "", ""
    return " ".join(tokens[:min(3, len(tokens))]), "", "", ""


def parse_compact_player_line(line: str, page_no: int, team: str, code: str, header_source: str, parse_method: str) -> dict[str, Any] | None:
    """Parser de fallback para linhas de texto/words do PDF.

    Corrige o problema visto em IR Iran, Jordan, Mexico e Portugal: nessas páginas
    o pdfplumber pode não detectar a tabela, mas o texto da linha vem como:
    "1 GK ... 01/01/2000 Club Name 190 10 0".
    """
    line = clean_value(line)
    if not line:
        return None
    line = re.sub(r"^\s*#\s+", "", line)
    m = re.match(
        r"^(?:(?P<num>\d{1,2})\s+)?(?P<pos>GK|DF|MF|FW)\s+(?P<pre>.+?)\s+"
        r"(?P<dob>\d{2}/\d{2}/\d{4})\s+(?P<after>.+?)\s*$",
        line,
    )
    if not m:
        return None

    num_raw = m.group("num") or ""
    pos = m.group("pos")
    pre = m.group("pre")
    dob = m.group("dob")
    after = m.group("after")

    tail = re.match(r"^(?P<club>.+?)\s+(?P<height>\d{2,3})\s+(?P<caps>\d+)\s+(?P<goals>-?\d+)\s*$", after)
    if not tail:
        # Algumas linhas trazem hífen/traço em gols/caps. Mantém jogador mesmo assim.
        tail = re.match(r"^(?P<club>.+?)\s+(?P<height>\d{2,3})\s+(?P<caps>[-–—]?|\d+)\s+(?P<goals>[-–—]?|\d+)\s*$", after)
    if not tail:
        return None

    raw_player_name, first_names, last_names, name_on_shirt = parse_name_block_before_dob(pre)
    if not raw_player_name:
        return None

    squad_number = as_int(num_raw)
    # Se não houver número no texto, preenche pela ordem de extração depois.
    country_en = normalize_country_alias(team)
    player_name = fifa_player_search_name(first_names, last_names, raw_player_name)
    squad_num_str = str(squad_number or 0).zfill(2)

    return {
        "player_id": f"fifa_{code}_{squad_num_str}_{slugify_id(player_name)}",
        "source_player_id": f"FIFA:{code}:{squad_number or ''}",
        "player_name": player_name,
        "fifa_player_name": clean_fifa_person_name(raw_player_name),
        "first_names": clean_fifa_person_name(first_names),
        "last_names": clean_fifa_person_name(last_names),
        "name_on_shirt": clean_value(name_on_shirt),
        "country_en": country_en,
        "fifa_team_name": clean_value(team),
        "fifa_team_code": code,
        "team_key": slugify_id(country_en),
        "squad_number": squad_number,
        "position": pos,
        "date_of_birth": dob,
        "club": clean_value(tail.group("club")),
        "club_raw": clean_value(tail.group("club")),
        "height_cm": as_int(tail.group("height")),
        "caps": as_int(tail.group("caps")),
        "goals": as_int(tail.group("goals")),
        "is_called_up": True,
        "squad_status": "fifa_official_final_squad",
        "input_source": "fifa_official_squad_pdf",
        "source_url": FIFA_SQUAD_PDF_URL,
        "source_page": page_no,
        "pdf_header_source": header_source,
        "parse_method": parse_method,
    }


def parse_text_player_lines(page_text: str, page_no: int, team: str, code: str, header_source: str) -> list[dict[str, Any]]:
    """Fallback textual para quando extract_tables falha.

    A versão anterior só aceitava linhas começando por GK/DF/MF/FW. O PDF da FIFA
    em algumas páginas começa cada linha por número + posição, por exemplo
    "1 GK ..."; por isso IR Iran/Jordan/Mexico/Portugal ficaram com 0.
    """
    out: list[dict[str, Any]] = []
    for line in (page_text or "").splitlines():
        item = parse_compact_player_line(line, page_no, team, code, header_source, "text_line_fallback_v2")
        if item:
            out.append(item)
    return out


def parse_word_player_rows(page: Any, page_no: int, team: str, code: str, header_source: str) -> list[dict[str, Any]]:
    """Fallback por coordenadas de palavras do pdfplumber.

    É mais robusto que extract_text() quando a tabela existe visualmente, mas
    não é reconhecida como tabela pelo pdfplumber.
    """
    try:
        words = page.extract_words(x_tolerance=1, y_tolerance=3, keep_blank_chars=False) or []
    except TypeError:
        words = page.extract_words() or []
    if not words:
        return []

    # Agrupa palavras em linhas por coordenada vertical.
    words = sorted(words, key=lambda w: (float(w.get("top", 0)), float(w.get("x0", 0))))
    grouped: list[list[dict[str, Any]]] = []
    for w in words:
        txt = clean_value(w.get("text", ""))
        if not txt:
            continue
        top = float(w.get("top", 0))
        if not grouped or abs(top - float(grouped[-1][0].get("top", 0))) > 3.5:
            grouped.append([w])
        else:
            grouped[-1].append(w)

    out: list[dict[str, Any]] = []
    for line_words in grouped:
        line_words = sorted(line_words, key=lambda w: float(w.get("x0", 0)))
        line = " ".join(clean_value(w.get("text", "")) for w in line_words)
        # Remove linhas de cabeçalho/rodapé rapidamente.
        if not re.search(r"\b(?:GK|DF|MF|FW)\b", line) or not DATE_RE.search(line):
            continue
        item = parse_compact_player_line(line, page_no, team, code, header_source, "pdfplumber_words_fallback")
        if item:
            out.append(item)
    return out


def fill_missing_squad_numbers(page_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Preenche números ausentes preservando a ordem visual da página."""
    used = {int(r["squad_number"]) for r in page_rows if r.get("squad_number")}
    next_num = 1
    for r in page_rows:
        if r.get("squad_number"):
            continue
        while next_num in used:
            next_num += 1
        r["squad_number"] = next_num
        r["source_player_id"] = f"FIFA:{r.get('fifa_team_code', '')}:{next_num}"
        player_name = r.get("player_name", "")
        r["player_id"] = f"fifa_{r.get('fifa_team_code', '')}_{str(next_num).zfill(2)}_{slugify_id(player_name)}"
        used.add(next_num)
        next_num += 1
    return page_rows

def extract_fifa_players_from_pdf(pdf_path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("Instale: pip install pdfplumber") from exc

    rows: list[dict[str, Any]] = []
    report_rows: list[dict[str, Any]] = []

    print(f"Lendo PDF oficial da FIFA: {pdf_path}")
    with pdfplumber.open(str(pdf_path)) as pdf:
        total_pages = len(pdf.pages)
        for page_index, page in enumerate(tqdm(pdf.pages, desc="Extraindo PDF FIFA", unit="pág"), start=0):
            page_no = page_index + 1
            text = page.extract_text(x_tolerance=1, y_tolerance=3) or ""
            team, code, header_source = parse_fifa_team_header(text, page_index)

            page_rows: list[dict[str, Any]] = []
            tables = page.extract_tables() or []
            for table in tables:
                for raw_row in table:
                    cells = [clean_value(c) for c in (raw_row or [])]
                    item = parse_table_row_known_layout(cells, page_no, team, code, header_source)
                    if item:
                        page_rows.append(item)

            # Dedup na página: quando pdfplumber repete tabela/linha.
            seen = set()
            deduped: list[dict[str, Any]] = []
            for item in page_rows:
                key = (item["fifa_team_code"], item.get("squad_number"), item["player_name"], item["date_of_birth"])
                if key in seen:
                    continue
                seen.add(key)
                deduped.append(item)
            page_rows = deduped

            if len(page_rows) < 26:
                # 1) Tenta fallback por coordenadas de palavras, que resolve páginas onde
                #    a tabela não é reconhecida, mas as linhas estão visualmente corretas.
                word_fallback = parse_word_player_rows(page, page_no, team, code, header_source)
                if len(word_fallback) > len(page_rows):
                    page_rows = word_fallback

            if len(page_rows) < 26:
                # 2) Último fallback: texto extraído linha a linha.
                text_fallback = parse_text_player_lines(text, page_no, team, code, header_source)
                if len(text_fallback) > len(page_rows):
                    page_rows = text_fallback

            page_rows = fill_missing_squad_numbers(page_rows)

            rows.extend(page_rows)
            report_rows.append({
                "source_page": page_no,
                "fifa_team_name": team,
                "fifa_team_code": code,
                "country_en": normalize_country_alias(team),
                "players_extracted": len(page_rows),
                "header_source": header_source,
                "parse_methods": ";".join(sorted({r.get("parse_method", "") for r in page_rows if r})),
                "status": "ok" if len(page_rows) == 26 else "check" if len(page_rows) >= 23 else "bad",
            })

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.drop_duplicates(subset=["fifa_team_code", "squad_number", "player_name", "date_of_birth"])
        # Corrige números ausentes por seleção, preservando a ordem.
        df["_order"] = range(len(df))
        df = df.sort_values(["fifa_team_code", "squad_number", "_order"], kind="stable")
        df = df.drop(columns=["_order"])

    report = pd.DataFrame(report_rows)
    return df.reset_index(drop=True), report.reset_index(drop=True)


def validate_players(df: pd.DataFrame, report: pd.DataFrame, strict: bool = True) -> None:
    n_players = len(df)
    n_teams = df["country_en"].nunique() if not df.empty else 0
    bad_pages = report.loc[report["players_extracted"] != 26] if not report.empty else pd.DataFrame()

    print(f"FIFA PDF: {n_players} jogadores extraídos de {n_teams} seleções")
    if not bad_pages.empty:
        print("Páginas com contagem diferente de 26 jogadores:")
        print(bad_pages[["source_page", "fifa_team_name", "fifa_team_code", "players_extracted", "header_source", "status"]].to_string(index=False))

    if strict and (n_players != 1248 or n_teams != 48 or not bad_pages.empty):
        raise RuntimeError(
            f"Validação falhou: esperado 1248 jogadores/48 seleções/26 por página; "
            f"obtido {n_players} jogadores/{n_teams} seleções. "
            "Rode com --no-strict para salvar mesmo assim e analise o report."
        )


def save_fifa_outputs(df: pd.DataFrame, report: pd.DataFrame, pdf_path: Path) -> None:
    GOLD_DIR.mkdir(parents=True, exist_ok=True)
    df = df.copy()
    df["input_source_pdf"] = str(pdf_path)
    # Colunas principais primeiro.
    preferred = [
        "player_id", "source_player_id", "player_name", "fifa_player_name",
        "first_names", "last_names", "name_on_shirt", "country_en", "fifa_team_name",
        "fifa_team_code", "team_key", "squad_number", "position", "date_of_birth",
        "club", "club_raw", "height_cm", "caps", "goals", "is_called_up",
        "squad_status", "input_source", "source_url", "source_page", "input_source_pdf",
        "pdf_header_source", "parse_method",
    ]
    cols = [c for c in preferred if c in df.columns] + [c for c in df.columns if c not in preferred]
    df[cols].to_csv(FIFA_PLAYERS_OUT, index=False)
    report.to_csv(FIFA_PLAYERS_REPORT_OUT, index=False)
    print(f"Salvo: {FIFA_PLAYERS_OUT} ({len(df)} linhas)")
    print(f"Salvo: {FIFA_PLAYERS_REPORT_OUT} ({len(report)} páginas)")

# -----------------------------------------------------------------------------
# Enrichment: Wikidata / PT Wiki
# -----------------------------------------------------------------------------


def load_cache() -> dict[str, Any]:
    if not CACHE_PATH.exists():
        return {}
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_cache(cache: dict[str, Any]) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CACHE_PATH)


def cache_key(row: pd.Series) -> str:
    return f"{row.get('fifa_team_code','')}::{row.get('source_player_id','')}::{slugify_id(row.get('player_name',''))}"


def http_get_json(url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        r = get_session().get(url, params=params, timeout=(8, 25))
        if r.status_code != 200:
            return {"error": f"http_{r.status_code}", "url": r.url, "text": r.text[:200]}
        return r.json()
    except Exception as exc:
        return {"error": type(exc).__name__, "message": str(exc), "url": url}


def label_of(entity: dict[str, Any]) -> str:
    labels = entity.get("labels", {}) if isinstance(entity, dict) else {}
    for lang in ("pt", "en", "fr", "de", "es", "it"):
        if lang in labels:
            return labels[lang].get("value", "")
    if labels:
        return next(iter(labels.values())).get("value", "")
    return ""


def qids_from_claim(entity: dict[str, Any], prop: str) -> list[str]:
    out = []
    for claim in entity.get("claims", {}).get(prop, []):
        value = claim.get("mainsnak", {}).get("datavalue", {}).get("value")
        if isinstance(value, dict) and value.get("entity-type") == "item":
            out.append(f"Q{value.get('numeric-id')}")
    return out


def first_time_claim(entity: dict[str, Any], prop: str) -> str:
    for claim in entity.get("claims", {}).get(prop, []):
        value = claim.get("mainsnak", {}).get("datavalue", {}).get("value")
        if isinstance(value, dict) and value.get("time"):
            return value["time"].lstrip("+").split("T")[0]
    return ""


def first_quantity_claim(entity: dict[str, Any], prop: str) -> float | None:
    for claim in entity.get("claims", {}).get(prop, []):
        value = claim.get("mainsnak", {}).get("datavalue", {}).get("value")
        if isinstance(value, dict) and value.get("amount") is not None:
            try:
                return float(value["amount"])
            except Exception:
                return None
    return None


def has_occupation_footballer(entity: dict[str, Any]) -> bool:
    return FOOTBALLER_QID in qids_from_claim(entity, "P106")


def claim_has_end_time(claim: dict[str, Any]) -> bool:
    return "P582" in claim.get("qualifiers", {})


def current_p54_qids(entity: dict[str, Any]) -> list[str]:
    out = []
    for claim in entity.get("claims", {}).get("P54", []):
        if claim_has_end_time(claim):
            continue
        value = claim.get("mainsnak", {}).get("datavalue", {}).get("value")
        if isinstance(value, dict) and value.get("entity-type") == "item":
            out.append(f"Q{value.get('numeric-id')}")
    return out


def wbsearch(name: str, country: str = "") -> list[str]:
    queries = [f"{name} footballer"]
    if country:
        queries.append(f"{name} {country} footballer")
    seen, out = set(), []
    for query in queries:
        for lang in ("en", "pt", "es", "fr"):
            data = http_get_json(WIKIDATA_API, {
                "action": "wbsearchentities",
                "format": "json",
                "language": lang,
                "uselang": lang,
                "search": query,
                "type": "item",
                "limit": 6,
            })
            for item in data.get("search", []):
                qid = item.get("id")
                if qid and qid not in seen:
                    seen.add(qid)
                    out.append(qid)
            if out:
                return out
    return out


def wbgetentities(qids: list[str]) -> dict[str, dict[str, Any]]:
    if not qids:
        return {}
    entities: dict[str, dict[str, Any]] = {}
    for i in range(0, len(qids), 40):
        batch = qids[i:i+40]
        data = http_get_json(WIKIDATA_API, {
            "action": "wbgetentities",
            "format": "json",
            "ids": "|".join(batch),
            "props": "labels|claims|sitelinks|descriptions",
            "languages": "pt|en|fr|de|es|it",
        })
        entities.update(data.get("entities", {}) if isinstance(data, dict) else {})
    return entities


def is_probably_national_team(label: str) -> bool:
    k = norm_text(label)
    return any(x in k for x in ["national", "equipe nationale", "selecao", "under", "u 20", "u 21", "olympic"])


def fetch_wikidata(row: pd.Series) -> dict[str, Any]:
    player_name = clean_value(row.get("player_name"))
    country = clean_value(row.get("country_en"))
    result = {"status": "not_found", "source": "wikidata", "queried_at": now_utc()}
    qids = wbsearch(player_name, country)
    entities = wbgetentities(qids)
    player_qid, ent = "", None
    for qid in qids:
        e = entities.get(qid, {})
        if has_occupation_footballer(e) or e.get("claims", {}).get("P54"):
            player_qid, ent = qid, e
            break
    if not ent:
        return result

    current_qids = current_p54_qids(ent)
    club_entities = wbgetentities(current_qids)
    club_qid = ""
    club_label = ""
    for qid in current_qids:
        label = label_of(club_entities.get(qid, {}))
        if label and not is_probably_national_team(label):
            club_qid, club_label = qid, label
            break

    pos_qids = qids_from_claim(ent, "P413")[:3]
    aux = wbgetentities(pos_qids)
    ptwiki_title = ent.get("sitelinks", {}).get("ptwiki", {}).get("title", "")
    enwiki_title = ent.get("sitelinks", {}).get("enwiki", {}).get("title", "")

    result.update({
        "status": "found" if player_qid else "not_found",
        "wikidata_qid": player_qid,
        "wikidata_url": f"https://www.wikidata.org/wiki/{player_qid}" if player_qid else "",
        "wikidata_label": label_of(ent),
        "date_of_birth_wikidata": first_time_claim(ent, "P569"),
        "height_m_wikidata": first_quantity_claim(ent, "P2048"),
        "weight_kg_wikidata": first_quantity_claim(ent, "P2067"),
        "position_wikidata": "; ".join(label_of(aux.get(q, {})) for q in pos_qids if label_of(aux.get(q, {}))),
        "current_club_wikidata": club_label,
        "current_club_qid": club_qid,
        "ptwiki_title": ptwiki_title,
        "ptwiki_url": f"https://pt.wikipedia.org/wiki/{quote(ptwiki_title.replace(' ', '_'))}" if ptwiki_title else "",
        "enwiki_title": enwiki_title,
        "enwiki_url": f"https://en.wikipedia.org/wiki/{quote(enwiki_title.replace(' ', '_'))}" if enwiki_title else "",
    })
    return result


def fetch_ptwiki(row: pd.Series, wikidata_item: dict[str, Any] | None = None) -> dict[str, Any]:
    title = clean_value((wikidata_item or {}).get("ptwiki_title"))
    name = clean_value(row.get("player_name"))
    country = clean_value(row.get("country_en"))
    result = {"status": "not_found", "source": "ptwiki", "queried_at": now_utc()}
    if not title:
        data = http_get_json(WIKIPEDIA_API_PT, {
            "action": "query",
            "format": "json",
            "list": "search",
            "srsearch": f'"{name}" futebolista {country}',
            "srlimit": 5,
            "utf8": 1,
        })
        best_score, best_title = 0, ""
        for item in data.get("query", {}).get("search", []):
            t = item.get("title", "")
            snippet = re.sub(r"<.*?>", " ", item.get("snippet", ""))
            score = 0
            nt = norm_text(t)
            nn = norm_text(name)
            if nn and nn in nt:
                score += 80
            if "futebol" in norm_text(snippet + " " + t):
                score += 20
            if score > best_score:
                best_score, best_title = score, t
        if best_score >= 50:
            title = best_title
    if not title:
        return result

    data = http_get_json(WIKIPEDIA_API_PT, {
        "action": "query",
        "format": "json",
        "prop": "extracts|info|pageprops",
        "titles": title,
        "exintro": 1,
        "explaintext": 1,
        "inprop": "url",
        "ppprop": "wikibase_item",
        "redirects": 1,
        "utf8": 1,
    })
    pages = data.get("query", {}).get("pages", {}) if isinstance(data, dict) else {}
    for p in pages.values():
        if isinstance(p, dict) and p.get("missing") is None:
            result.update({
                "status": "found",
                "ptwiki_title": p.get("title", title),
                "ptwiki_pageid": p.get("pageid"),
                "ptwiki_url": p.get("fullurl", ""),
                "ptwiki_summary": clean_value(p.get("extract", "")),
                "ptwiki_wikidata_item": p.get("pageprops", {}).get("wikibase_item", ""),
            })
            break
    return result


def enrich_one(row_dict: dict[str, Any], sources: set[str]) -> tuple[str, dict[str, Any]]:
    row = pd.Series(row_dict)
    key = cache_key(row)
    wd = fetch_wikidata(row) if "wikidata" in sources else {}
    pt = fetch_ptwiki(row, wd) if "ptwiki" in sources else {}
    final = {
        "player_id": row.get("player_id", ""),
        "source_player_id": row.get("source_player_id", ""),
        "player_name": row.get("player_name", ""),
        "country_en": row.get("country_en", ""),
        "fifa_team_code": row.get("fifa_team_code", ""),
        "squad_number": row.get("squad_number", ""),
        "position": row.get("position", ""),
        "date_of_birth_fifa": row.get("date_of_birth", ""),
        "club_fifa": row.get("club", ""),
        "height_cm_fifa": row.get("height_cm", ""),
        "caps_fifa": row.get("caps", ""),
        "goals_fifa": row.get("goals", ""),
        "queried_at": now_utc(),
        **{f"wikidata_{k}": v for k, v in wd.items() if k not in {"source"}},
        **{k: v for k, v in pt.items() if k.startswith("ptwiki_") or k == "status"},
        "wikidata_status": wd.get("status", "disabled") if wd else "disabled",
        "ptwiki_status": pt.get("status", "disabled") if pt else "disabled",
    }
    return key, final


def run_enrichment(players: pd.DataFrame, teams: list[str] | None, sources: set[str], workers: int, force: bool, max_new: int | None, save_every: int) -> pd.DataFrame:
    df = players.copy()
    if teams:
        teams_norm = {norm_text(normalize_country_alias(t)) for t in teams}
        df = df[df["country_en"].map(lambda x: norm_text(normalize_country_alias(x))).isin(teams_norm)].copy()

    cache = load_cache()
    if force:
        for _, row in df.iterrows():
            cache.pop(cache_key(row), None)

    targets = [row.to_dict() for _, row in df.iterrows() if cache_key(row) not in cache]
    if max_new is not None:
        targets = targets[:max_new]

    print(f"Enriquecimento: alvos={len(df)} | em cache={len(df)-len(targets)} | novas buscas={len(targets)} | fontes={','.join(sorted(sources))}")

    done = 0
    if targets:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            futs = {ex.submit(enrich_one, row, sources): row for row in targets}
            for fut in tqdm(as_completed(futs), total=len(futs), desc="Enriquecendo jogadores", unit="jog"):
                try:
                    key, item = fut.result()
                    cache[key] = item
                except Exception as exc:
                    row = pd.Series(futs[fut])
                    key = cache_key(row)
                    cache[key] = {
                        "player_id": row.get("player_id", ""),
                        "source_player_id": row.get("source_player_id", ""),
                        "player_name": row.get("player_name", ""),
                        "country_en": row.get("country_en", ""),
                        "error": f"{type(exc).__name__}: {exc}",
                        "queried_at": now_utc(),
                    }
                done += 1
                if done % max(1, save_every) == 0:
                    save_cache(cache)
                    materialize_enrichment(cache, players)
                time.sleep(0.02)
        save_cache(cache)

    return materialize_enrichment(cache, players)


def materialize_enrichment(cache: dict[str, Any], players: pd.DataFrame) -> pd.DataFrame:
    rows = []
    report = []
    for _, row in players.iterrows():
        key = cache_key(row)
        item = cache.get(key, {})
        if item:
            rows.append(item)
        report.append({
            "player_id": row.get("player_id", ""),
            "source_player_id": row.get("source_player_id", ""),
            "player_name": row.get("player_name", ""),
            "country_en": row.get("country_en", ""),
            "cached": bool(item),
            "wikidata_status": item.get("wikidata_status", ""),
            "ptwiki_status": item.get("ptwiki_status", ""),
            "error": item.get("error", ""),
            "queried_at": item.get("queried_at", ""),
        })
    enriched = pd.DataFrame(rows)
    enriched.to_csv(ENRICHED_OUT, index=False)
    pd.DataFrame(report).to_csv(ENRICH_REPORT_OUT, index=False)
    print(f"Salvo: {ENRICHED_OUT} ({len(enriched)} linhas)")
    print(f"Salvo: {ENRICH_REPORT_OUT} ({len(report)} linhas)")
    return enriched

# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------


def main() -> None:
    global FIFA_SQUAD_PDF_URL
    print(f"Modo LOCAL_ETL ativo. Pasta base: {ETL_ROOT}")
    print(f"Saídas em: {GOLD_DIR}")
    parser = argparse.ArgumentParser()
    parser.add_argument("--extract-only", action="store_true", help="Só extrai o PDF da FIFA e gera o CSV oficial.")
    parser.add_argument("--enrich", action="store_true", help="Enriquece jogadores com Wikidata/Wikipedia.")
    parser.add_argument("--all", action="store_true", help="Usa todas as seleções do PDF. Sem isso, use --teams.")
    parser.add_argument("--teams", nargs="*", default=None, help="Ex.: --teams Brazil France 'South Korea'")
    parser.add_argument("--fifa-pdf-url", default=FIFA_SQUAD_PDF_URL)
    parser.add_argument("--refresh-fifa-squad", action="store_true")
    parser.add_argument("--no-strict", action="store_true", help="Não falha se não der exatamente 1248/48/26 por página.")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--sources", nargs="+", choices=["wikidata", "ptwiki"], default=["wikidata", "ptwiki"])
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--max-new", type=int, default=None)
    parser.add_argument("--save-every", type=int, default=50)
    args = parser.parse_args()

    FIFA_SQUAD_PDF_URL = args.fifa_pdf_url

    pdf_path = download_fifa_squad_pdf(args.fifa_pdf_url, refresh=args.refresh_fifa_squad)
    players, report = extract_fifa_players_from_pdf(pdf_path)
    validate_players(players, report, strict=not args.no_strict)
    save_fifa_outputs(players, report, pdf_path)

    if args.extract_only and not args.enrich:
        return

    if args.enrich:
        teams = None if args.all else args.teams
        if not args.all and not teams:
            raise SystemExit("Use --all para enriquecer todas as seleções ou informe --teams Brazil France")
        run_enrichment(
            players=players,
            teams=teams,
            sources=set(args.sources),
            workers=args.workers,
            force=args.force,
            max_new=args.max_new,
            save_every=args.save_every,
        )


if __name__ == "__main__":
    main()
