#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
team_matches_free_open_sources_2022_2026.py

Coleta GRATUITA/SEM API PAGA de jogos de seleções masculinas entre 2022 e 2026.

Fonte principal:
  - Mart Jürisoo / international_results (GitHub raw CSV, CC0):
    results.csv, shootouts.csv, goalscorers.csv

O que gera:
  data/gold/team_matches_2022_2026_free_sources.csv
  data/gold/team_matches_2022_2026_unique_fixtures.csv
  data/gold/team_profile_2022_2026_free_sources.csv
  data/gold/team_goal_scorers_2022_2026_free_sources.csv
  data/gold/free_sources_collection_report.csv

Limitações honestas:
  - Odds históricas gratuitas de 2022-2026 não são disponibilizadas de forma completa por APIs grátis.
    O script cria colunas odds_* vazias para manter o schema pronto.
  - Formações/lineups históricos completos também não existem nesta fonte aberta; ficam como colunas vazias.
  - A base é de jogos internacionais masculinos principais; não inclui sub-23, times B, seleções olímpicas etc.

Uso:
  pip install pandas requests tqdm python-dateutil

  python3 team_matches_free_open_sources_2022_2026.py \
    --players-csv data/gold/gold_worldcup_players_2026_fifa_official.csv \
    --date-from 2022-01-01 \
    --date-to 2026-12-31 \
    --output-dir data/gold

  # ou filtrando manualmente:
  python3 team_matches_free_open_sources_2022_2026.py --teams Brazil Argentina France Morocco

Depois, plugar no seu builder, tudo dentro do próprio etl:
  python3 worldcup_2026_feature_builder.py \
    --players-csv data/gold/gold_worldcup_players_2026_fifa_official.csv \
    --matches-csv data/gold/team_matches_2022_2026_free_sources.csv \
    --output-dir data/gold
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import unicodedata
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from tqdm.auto import tqdm

RESULTS_URL = "https://raw.githubusercontent.com/martj42/international_results/master/results.csv"
SHOOTOUTS_URL = "https://raw.githubusercontent.com/martj42/international_results/master/shootouts.csv"
GOALSCORERS_URL = "https://raw.githubusercontent.com/martj42/international_results/master/goalscorers.csv"

USER_AGENT = "analytica-model-free-football-data/1.0 (academic; github open data)"

TEAM_ALIASES = {
    "bosnia and herzegovina": "Bosnia and Herzegovina",
    "bosnia herzegovina": "Bosnia and Herzegovina",
    "cabo verde": "Cape Verde",
    "cape verde": "Cape Verde",
    "congo dr": "DR Congo",
    "dr congo": "DR Congo",
    "democratic republic of the congo": "DR Congo",
    "cote d ivoire": "Ivory Coast",
    "cote divoire": "Ivory Coast",
    "côte d'ivoire": "Ivory Coast",
    "côte d ivoire": "Ivory Coast",
    "ivory coast": "Ivory Coast",
    "curacao": "Curaçao",
    "curaçao": "Curaçao",
    "ir iran": "Iran",
    "iran": "Iran",
    "korea republic": "South Korea",
    "republic of korea": "South Korea",
    "south korea": "South Korea",
    "turkiye": "Turkey",
    "türkiye": "Turkey",
    "turkey": "Turkey",
    "usa": "United States",
    "us": "United States",
    "u s a": "United States",
    "united states of america": "United States",
    "united states": "United States",
    "england": "England",
    "scotland": "Scotland",
    "wales": "Wales",
    "northern ireland": "Northern Ireland",
}

# Algumas bases usam nomes atuais ou variantes. Este mapa serve para reconciliar
# nomes do CSV de jogadores da FIFA com o dataset de resultados.
FIFA_TO_RESULTS_NAME = {
    "Cabo Verde": "Cape Verde",
    "Côte D'Ivoire": "Ivory Coast",
    "Côte d'Ivoire": "Ivory Coast",
    "Congo DR": "DR Congo",
    "IR Iran": "Iran",
    "Korea Republic": "South Korea",
    "Türkiye": "Turkey",
    "USA": "United States",
}

FRIENDLY_NAMES = {
    "friendly",
    "friendlies",
    "international friendly",
}


def clean(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return re.sub(r"\s+", " ", str(value).replace("\xa0", " ")).strip()


def norm_key(value: Any) -> str:
    s = clean(value)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def canonical_team(value: Any) -> str:
    raw = clean(value)
    if not raw:
        return ""
    if raw in FIFA_TO_RESULTS_NAME:
        raw = FIFA_TO_RESULTS_NAME[raw]
    return TEAM_ALIASES.get(norm_key(raw), raw)


def resolve_repo_root() -> Path:
    """Modo LOCAL_ETL: tudo fica dentro da pasta onde o script está.

    Se você rodar este arquivo em:
      /home/perri/Área de trabalho/analytica-model/model/etl

    então ele vai usar:
      /home/perri/Área de trabalho/analytica-model/model/etl/data/raw
      /home/perri/Área de trabalho/analytica-model/model/etl/data/gold

    Não tenta subir para ../../data.
    """
    return Path(__file__).resolve().parent


def get_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=5,
        connect=5,
        read=5,
        status=5,
        backoff_factor=1.25,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update({"User-Agent": USER_AGENT})
    return s


def download_to_cache(url: str, cache_dir: Path, refresh: bool = False) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    name = url.split("/")[-1]
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
    out = cache_dir / f"{digest}_{name}"
    if out.exists() and not refresh:
        return out
    print(f"Baixando fonte aberta: {url}")
    r = get_session().get(url, timeout=(10, 90))
    r.raise_for_status()
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_bytes(r.content)
    tmp.replace(out)
    time.sleep(0.5)
    return out


def read_open_csv(url: str, cache_dir: Path, refresh: bool = False) -> tuple[pd.DataFrame, Path]:
    path = download_to_cache(url, cache_dir, refresh=refresh)
    return pd.read_csv(path), path


def resolve_existing_file(path_value: str | None, root: Path | None = None) -> Path | None:
    """Resolve caminho em modo LOCAL_ETL.

    Aceita caminho absoluto ou caminho relativo à pasta onde o script está.
    Também testa o diretório atual para não quebrar se você chamar de fora.
    """
    if not path_value:
        return None

    raw = Path(path_value).expanduser()
    candidates: list[Path] = []

    if raw.is_absolute():
        candidates.append(raw)
    else:
        cwd = Path.cwd().resolve()
        root = root or resolve_repo_root()
        bases = [root, cwd]
        for base in bases:
            try:
                candidates.append((base / raw).resolve())
            except Exception:
                candidates.append(base / raw)

    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def resolve_output_dir(path_value: str | None, root: Path) -> Path:
    if not path_value:
        return root / "data" / "gold"
    raw = Path(path_value).expanduser()
    if raw.is_absolute():
        return raw
    # Modo LOCAL_ETL: qualquer caminho relativo é relativo à pasta do script.
    return (root / raw).resolve()


def load_target_teams(players_csv: str | None, teams: list[str] | None, root: Path | None = None) -> list[str] | None:
    if teams:
        return sorted({canonical_team(t) for t in teams if clean(t)})
    if not players_csv:
        return None

    p = resolve_existing_file(players_csv, root=root)
    if p is None:
        root = root or resolve_repo_root()
        raise FileNotFoundError(
            "Não achei --players-csv. No modo LOCAL_ETL, coloque o CSV aqui:\n"
            f"  {root / 'data/gold/gold_worldcup_players_2026_fifa_official.csv'}\n"
            "e rode com:\n"
            "  --players-csv data/gold/gold_worldcup_players_2026_fifa_official.csv"
        )

    print(f"CSV de seleções/jogadores encontrado: {p}")
    df = pd.read_csv(p)
    if "country_en" not in df.columns:
        raise ValueError("O CSV de jogadores precisa ter coluna country_en")
    return sorted({canonical_team(x) for x in df["country_en"].dropna().unique() if clean(x)})


def normalize_results(results: pd.DataFrame) -> pd.DataFrame:
    df = results.copy()
    required = ["date", "home_team", "away_team", "home_score", "away_score", "tournament"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"results.csv sem colunas esperadas: {missing}")

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).copy()
    df["home_team"] = df["home_team"].map(canonical_team)
    df["away_team"] = df["away_team"].map(canonical_team)
    df["home_score"] = pd.to_numeric(df["home_score"], errors="coerce")
    df["away_score"] = pd.to_numeric(df["away_score"], errors="coerce")
    df = df.dropna(subset=["home_score", "away_score"]).copy()
    df["home_score"] = df["home_score"].astype(int)
    df["away_score"] = df["away_score"].astype(int)
    df["tournament"] = df["tournament"].map(clean)
    df["city"] = df.get("city", "").map(clean) if "city" in df.columns else ""
    df["country"] = df.get("country", "").map(clean) if "country" in df.columns else ""
    if "neutral" in df.columns:
        df["neutral"] = df["neutral"].astype(str).str.lower().isin(["true", "1", "yes"])
    else:
        df["neutral"] = False

    # ID estável por data/time/placar/competição.
    df["fixture_id_open"] = (
        df["date"].dt.strftime("%Y%m%d") + "__" +
        df["home_team"].map(norm_key) + "__" +
        df["away_team"].map(norm_key) + "__" +
        df["home_score"].astype(str) + "_" + df["away_score"].astype(str) + "__" +
        df["tournament"].map(norm_key)
    )
    return df


def normalize_shootouts(shootouts: pd.DataFrame) -> pd.DataFrame:
    if shootouts.empty:
        return pd.DataFrame(columns=["date", "home_team", "away_team", "shootout_winner", "first_shooter"])
    df = shootouts.copy()
    for col in ["date", "home_team", "away_team", "winner"]:
        if col not in df.columns:
            return pd.DataFrame(columns=["date", "home_team", "away_team", "shootout_winner", "first_shooter"])
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["home_team"] = df["home_team"].map(canonical_team)
    df["away_team"] = df["away_team"].map(canonical_team)
    df["shootout_winner"] = df["winner"].map(canonical_team)
    df["first_shooter"] = df.get("first_shooter", "").map(canonical_team) if "first_shooter" in df.columns else ""
    return df[["date", "home_team", "away_team", "shootout_winner", "first_shooter"]]


def normalize_goalscorers(goalscorers: pd.DataFrame) -> pd.DataFrame:
    if goalscorers.empty:
        return pd.DataFrame()
    df = goalscorers.copy()
    required = ["date", "home_team", "away_team", "team", "scorer"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        print(f"Aviso: goalscorers.csv sem colunas {missing}. Pulando artilheiros.")
        return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).copy()
    for col in ["home_team", "away_team", "team"]:
        df[col] = df[col].map(canonical_team)
    df["scorer"] = df["scorer"].map(clean)
    for col in ["minute", "own_goal", "penalty"]:
        if col not in df.columns:
            df[col] = ""
    df["own_goal"] = df["own_goal"].astype(str).str.lower().isin(["true", "1", "yes"])
    df["penalty"] = df["penalty"].astype(str).str.lower().isin(["true", "1", "yes"])
    return df


def classify_competition(tournament: str) -> tuple[str, str, bool]:
    t = clean(tournament)
    k = norm_key(t)
    is_friendly = k in FRIENDLY_NAMES or "friendly" in k
    match_category = "friendly" if is_friendly else "competitive"
    return t, match_category, is_friendly


def result_code(goals_for: int, goals_against: int) -> str:
    if goals_for > goals_against:
        return "W"
    if goals_for < goals_against:
        return "L"
    return "D"


def build_unique_fixtures(results: pd.DataFrame, shootouts: pd.DataFrame) -> pd.DataFrame:
    df = results.copy()
    if not shootouts.empty:
        df = df.merge(shootouts, on=["date", "home_team", "away_team"], how="left")
    else:
        df["shootout_winner"] = ""
        df["first_shooter"] = ""

    cats = df["tournament"].map(classify_competition)
    df["competition"] = [x[0] for x in cats]
    df["match_category"] = [x[1] for x in cats]
    df["is_friendly"] = [x[2] for x in cats]

    df = df.rename(columns={
        "date": "match_date",
        "home_team": "home_team",
        "away_team": "away_team",
        "home_score": "home_score",
        "away_score": "away_score",
        "city": "venue_city",
        "country": "venue_country",
    })
    keep = [
        "fixture_id_open", "match_date", "home_team", "away_team", "home_score", "away_score",
        "competition", "match_category", "is_friendly", "venue_city", "venue_country", "neutral",
        "shootout_winner", "first_shooter",
    ]
    for col in keep:
        if col not in df.columns:
            df[col] = ""
    return df[keep].sort_values(["match_date", "home_team", "away_team"]).reset_index(drop=True)


def build_team_rows(unique_fixtures: pd.DataFrame, target_teams: list[str] | None, goal_summary: pd.DataFrame) -> pd.DataFrame:
    targets = {canonical_team(t) for t in target_teams} if target_teams else None
    rows: list[dict[str, Any]] = []

    for _, m in tqdm(unique_fixtures.iterrows(), total=len(unique_fixtures), desc="Montando linhas por seleção", unit="jogo"):
        home = canonical_team(m["home_team"])
        away = canonical_team(m["away_team"])
        if targets is not None and home not in targets and away not in targets:
            continue

        for team, opponent, is_home, gf, ga in [
            (home, away, True, int(m["home_score"]), int(m["away_score"])),
            (away, home, False, int(m["away_score"]), int(m["home_score"])),
        ]:
            if targets is not None and team not in targets:
                continue

            res = result_code(gf, ga)
            shootout_winner = canonical_team(m.get("shootout_winner", ""))
            result_after_penalties = res
            won_on_penalties = False
            lost_on_penalties = False
            if res == "D" and shootout_winner:
                if shootout_winner == team:
                    result_after_penalties = "Wp"
                    won_on_penalties = True
                else:
                    result_after_penalties = "Lp"
                    lost_on_penalties = True

            key = (m["match_date"], home, away, team)
            scorers = []
            penalties = 0
            own_goals = 0
            if not goal_summary.empty and key in goal_summary.index:
                g = goal_summary.loc[key]
                scorers = g.get("goal_scorers", [])
                penalties = int(g.get("penalty_goals", 0))
                own_goals = int(g.get("own_goals", 0))

            rows.append({
                "fixture_id_open": m["fixture_id_open"],
                "match_date": pd.Timestamp(m["match_date"]).date().isoformat(),
                "team_name": team,
                "team_key_norm": norm_key(team),
                "opponent": opponent,
                "opponent_key_norm": norm_key(opponent),
                "is_home": bool(is_home),
                "home_team": home,
                "away_team": away,
                "home_score": int(m["home_score"]),
                "away_score": int(m["away_score"]),
                "goals_for": gf,
                "goals_against": ga,
                "goal_diff": gf - ga,
                "result": res,
                "result_after_penalties": result_after_penalties,
                "won_on_penalties": won_on_penalties,
                "lost_on_penalties": lost_on_penalties,
                "competition": m["competition"],
                "match_category": m["match_category"],
                "is_friendly": bool(m["is_friendly"]),
                "venue_city": clean(m.get("venue_city", "")),
                "venue_country": clean(m.get("venue_country", "")),
                "neutral": bool(m.get("neutral", False)),
                "shootout_winner": shootout_winner,
                "first_shooter": canonical_team(m.get("first_shooter", "")),
                "goal_scorers": " | ".join(scorers) if isinstance(scorers, list) else clean(scorers),
                "penalty_goals": penalties,
                "own_goals_recorded": own_goals,
                # Mantém schema pronto para quando você quiser enriquecer com API paga/free-tier.
                "formation": "",
                "coach_name": "",
                "lineup_source": "not_available_in_free_open_dataset",
                "odds_home": np.nan,
                "odds_draw": np.nan,
                "odds_away": np.nan,
                "odds_for": np.nan,
                "odds_against": np.nan,
                "odds_source": "not_available_in_free_open_dataset",
                "data_source": "martj42/international_results",
                "source_url": RESULTS_URL,
            })

    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values(["team_name", "match_date", "opponent"]).reset_index(drop=True)
    return out


def build_goal_summary(goals: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if goals.empty:
        return pd.DataFrame(), pd.DataFrame()

    team_goals = goals.copy()
    team_goals["match_date"] = team_goals["date"]
    team_goals["goal_type"] = np.select(
        [team_goals["penalty"], team_goals["own_goal"]],
        ["penalty", "own_goal"],
        default="normal",
    )

    grouped = []
    for (date, home, away, team), g in team_goals.groupby(["match_date", "home_team", "away_team", "team"]):
        scorer_counts = g["scorer"].replace("", np.nan).dropna().value_counts()
        scorer_text = [f"{name} ({int(n)})" if n > 1 else str(name) for name, n in scorer_counts.items()]
        grouped.append({
            "match_date": date,
            "home_team": home,
            "away_team": away,
            "team": team,
            "goal_scorers": scorer_text,
            "penalty_goals": int(g["penalty"].sum()),
            "own_goals": int(g["own_goal"].sum()),
            "recorded_goals_rows": int(len(g)),
        })
    summary = pd.DataFrame(grouped)
    if not summary.empty:
        summary = summary.set_index(["match_date", "home_team", "away_team", "team"])

    scorers = (
        team_goals.groupby(["team", "scorer"], as_index=False)
        .agg(
            goals_recorded=("scorer", "count"),
            penalty_goals=("penalty", "sum"),
            own_goal_rows=("own_goal", "sum"),
            first_goal_date=("match_date", "min"),
            last_goal_date=("match_date", "max"),
        )
        .rename(columns={"team": "team_name", "scorer": "player_name"})
        .sort_values(["team_name", "goals_recorded", "player_name"], ascending=[True, False, True])
    )
    scorers["first_goal_date"] = pd.to_datetime(scorers["first_goal_date"]).dt.date.astype(str)
    scorers["last_goal_date"] = pd.to_datetime(scorers["last_goal_date"]).dt.date.astype(str)
    return summary, scorers


def build_team_profile(team_matches: pd.DataFrame) -> pd.DataFrame:
    if team_matches.empty:
        return pd.DataFrame()

    rows = []
    df = team_matches.copy()
    df["match_date_dt"] = pd.to_datetime(df["match_date"], errors="coerce")
    for team, g in df.groupby("team_name"):
        g = g.sort_values("match_date_dt")
        n = len(g)
        wins = int((g["result"] == "W").sum())
        draws = int((g["result"] == "D").sum())
        losses = int((g["result"] == "L").sum())
        friendly = g[g["is_friendly"]]
        competitive = g[~g["is_friendly"]]
        last5 = g.tail(5)
        last10 = g.tail(10)
        form5 = "".join(last5["result"].tolist())
        form10 = "".join(last10["result"].tolist())

        formation_counts = g["formation"].replace("", np.nan).dropna().value_counts()
        rows.append({
            "team_name": team,
            "team_key_norm": norm_key(team),
            "matches_2022_2026": n,
            "wins_2022_2026": wins,
            "draws_2022_2026": draws,
            "losses_2022_2026": losses,
            "win_rate_2022_2026": wins / n if n else 0,
            "draw_rate_2022_2026": draws / n if n else 0,
            "loss_rate_2022_2026": losses / n if n else 0,
            "goals_for_2022_2026": int(g["goals_for"].sum()),
            "goals_against_2022_2026": int(g["goals_against"].sum()),
            "goal_diff_2022_2026": int(g["goal_diff"].sum()),
            "goals_for_per_match": float(g["goals_for"].mean()),
            "goals_against_per_match": float(g["goals_against"].mean()),
            "friendly_matches": int(len(friendly)),
            "friendly_wins": int((friendly["result"] == "W").sum()),
            "friendly_draws": int((friendly["result"] == "D").sum()),
            "friendly_losses": int((friendly["result"] == "L").sum()),
            "competitive_matches": int(len(competitive)),
            "competitive_wins": int((competitive["result"] == "W").sum()),
            "competitive_draws": int((competitive["result"] == "D").sum()),
            "competitive_losses": int((competitive["result"] == "L").sum()),
            "last5_form": form5,
            "last5_win_rate": float((last5["result"] == "W").mean()) if len(last5) else 0,
            "last10_form": form10,
            "last10_win_rate": float((last10["result"] == "W").mean()) if len(last10) else 0,
            "first_match_date": g["match_date_dt"].min().date().isoformat(),
            "last_match_date": g["match_date_dt"].max().date().isoformat(),
            "most_used_formation": formation_counts.index[0] if len(formation_counts) else "",
            "avg_odds_for": float(pd.to_numeric(g["odds_for"], errors="coerce").mean()) if g["odds_for"].notna().any() else np.nan,
            "avg_odds_against": float(pd.to_numeric(g["odds_against"], errors="coerce").mean()) if g["odds_against"].notna().any() else np.nan,
        })
    return pd.DataFrame(rows).sort_values("team_name").reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--players-csv", default=None, help="CSV da FIFA com country_en. Se informado, filtra só as seleções da Copa 2026.")
    parser.add_argument("--teams", nargs="*", default=None, help="Filtro manual: --teams Brazil Argentina France")
    parser.add_argument("--date-from", default="2022-01-01")
    parser.add_argument("--date-to", default="2026-12-31")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--refresh", action="store_true", help="Baixa novamente os CSVs abertos, ignorando cache local.")
    args = parser.parse_args()

    root = resolve_repo_root()
    raw_dir = root / "data" / "raw" / "free_football_sources"
    out_dir = resolve_output_dir(args.output_dir, root)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Raiz do projeto detectada: {root}")
    print(f"Diretório de saída: {out_dir}")

    target_teams = load_target_teams(args.players_csv, args.teams, root=root)
    if target_teams:
        print(f"Filtro de seleções: {len(target_teams)}")
        print(", ".join(target_teams[:20]) + (" ..." if len(target_teams) > 20 else ""))
    else:
        print("Sem filtro de seleções: usando todas as seleções encontradas na fonte aberta.")

    results_raw, results_path = read_open_csv(RESULTS_URL, raw_dir, refresh=args.refresh)
    shootouts_raw, shootouts_path = read_open_csv(SHOOTOUTS_URL, raw_dir, refresh=args.refresh)
    goals_raw, goals_path = read_open_csv(GOALSCORERS_URL, raw_dir, refresh=args.refresh)

    results = normalize_results(results_raw)
    date_from = pd.to_datetime(args.date_from)
    date_to = pd.to_datetime(args.date_to)
    results = results[(results["date"] >= date_from) & (results["date"] <= date_to)].copy()

    shootouts = normalize_shootouts(shootouts_raw)
    if not shootouts.empty:
        shootouts = shootouts[(shootouts["date"] >= date_from) & (shootouts["date"] <= date_to)].copy()

    goals = normalize_goalscorers(goals_raw)
    if not goals.empty:
        goals = goals[(goals["date"] >= date_from) & (goals["date"] <= date_to)].copy()
        if target_teams:
            goals = goals[goals["team"].isin(set(target_teams))].copy()

    goal_summary, scorers = build_goal_summary(goals)
    unique_fixtures = build_unique_fixtures(results, shootouts)
    team_matches = build_team_rows(unique_fixtures, target_teams, goal_summary)
    team_profile = build_team_profile(team_matches)

    matches_out = out_dir / "team_matches_2022_2026_free_sources.csv"
    fixtures_out = out_dir / "team_matches_2022_2026_unique_fixtures.csv"
    profile_out = out_dir / "team_profile_2022_2026_free_sources.csv"
    scorers_out = out_dir / "team_goal_scorers_2022_2026_free_sources.csv"
    report_out = out_dir / "free_sources_collection_report.csv"

    team_matches.to_csv(matches_out, index=False)
    unique_fixtures.to_csv(fixtures_out, index=False)
    team_profile.to_csv(profile_out, index=False)
    scorers.to_csv(scorers_out, index=False)

    report = {
        "date_from": str(date_from.date()),
        "date_to": str(date_to.date()),
        "target_teams_count": len(target_teams) if target_teams else 0,
        "target_teams": target_teams or [],
        "raw_results_rows_after_date_filter": int(len(results)),
        "team_match_rows": int(len(team_matches)),
        "unique_fixture_rows": int(len(unique_fixtures)),
        "team_profile_rows": int(len(team_profile)),
        "scorer_rows": int(len(scorers)),
        "sources": {
            "results_url": RESULTS_URL,
            "shootouts_url": SHOOTOUTS_URL,
            "goalscorers_url": GOALSCORERS_URL,
            "results_cache_path": str(results_path),
            "shootouts_cache_path": str(shootouts_path),
            "goalscorers_cache_path": str(goals_path),
        },
        "limitations": [
            "odds historicas gratuitas completas nao disponiveis nesta fonte",
            "formacoes/lineups historicos completos nao disponiveis nesta fonte",
            "dataset cobre selecoes masculinas principais, nao sub-23/time B/olimpicas",
        ],
        "outputs": {
            "team_matches": str(matches_out),
            "unique_fixtures": str(fixtures_out),
            "team_profile": str(profile_out),
            "team_goal_scorers": str(scorers_out),
        },
    }
    report_out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\nArquivos gerados:")
    print(f"- {matches_out} ({len(team_matches)} linhas seleção/jogo)")
    print(f"- {fixtures_out} ({len(unique_fixtures)} jogos únicos)")
    print(f"- {profile_out} ({len(team_profile)} seleções)")
    print(f"- {scorers_out} ({len(scorers)} linhas de artilheiros)")
    print(f"- {report_out}")


if __name__ == "__main__":
    main()
