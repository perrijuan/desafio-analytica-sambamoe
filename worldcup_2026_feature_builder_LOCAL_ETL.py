#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
worldcup_2026_feature_builder.py

Gera tabelas analíticas com informações de:
- jogadores convocados FIFA 2026: clube/time, jogos pela seleção (caps), gols, posição, idade;
- partidas das seleções desde 2022: jogos, vitórias, derrotas, empates, gols pró/contra;
- lesões: via CSV opcional auditável, quando disponível.

Entradas esperadas em data/gold:
  gold_worldcup_players_2026_fifa_official.csv
  team_matches_2022_2026_free_sources.csv   OU team_matches_2022_2026_selenium.csv

Entrada opcional de lesões em data/raw ou data/gold:
  player_injuries_manual.csv

Formato sugerido de player_injuries_manual.csv:
  player_name,country_en,injury_type,start_date,end_date,days_out,games_missed,source_name,source_url

Saídas em data/gold:
  worldcup_2026_player_profile_features.csv
  worldcup_2026_team_profile_features.csv
  worldcup_2026_club_distribution.csv
  worldcup_2026_feature_build_report.csv

Uso:
  python3 worldcup_2026_feature_builder.py
  python3 worldcup_2026_feature_builder.py --players-csv data/gold/gold_worldcup_players_2026_fifa_official.csv --matches-csv data/gold/team_matches_2022_2026_free_sources.csv
"""
from __future__ import annotations

import argparse
import re
import unicodedata
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from dateutil import parser as dtparser


def norm_key(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    s = str(value).replace("\xa0", " ").strip()
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def clean(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return re.sub(r"\s+", " ", str(value).replace("\xa0", " ")).strip()


TEAM_ALIASES = {
    "bosnia herzegovina": "Bosnia and Herzegovina",
    "bosnia and herzegovina": "Bosnia and Herzegovina",
    "cabo verde": "Cabo Verde",
    "cape verde": "Cabo Verde",
    "congo dr": "DR Congo",
    "dr congo": "DR Congo",
    "democratic republic of the congo": "DR Congo",
    "cote d ivoire": "Ivory Coast",
    "ivory coast": "Ivory Coast",
    "curacao": "Curaçao",
    "ir iran": "Iran",
    "iran": "Iran",
    "korea republic": "South Korea",
    "south korea": "South Korea",
    "republic of korea": "South Korea",
    "turkiye": "Turkey",
    "turkey": "Turkey",
    "usa": "United States",
    "united states": "United States",
    "united states of america": "United States",
}


def canonical_team(value: Any) -> str:
    raw = clean(value)
    if not raw:
        return ""
    return TEAM_ALIASES.get(norm_key(raw), raw)


def resolve_repo_root() -> Path:
    """Modo LOCAL_ETL: usa a pasta onde o script está como raiz.

    Tudo fica dentro de etl/data/gold e etl/data/raw.
    Não tenta subir para ../../data.
    """
    return Path(__file__).resolve().parent


def resolve_path(path_value: str | None, candidates: list[Path]) -> Path | None:
    root = resolve_repo_root()
    if path_value:
        raw = Path(path_value).expanduser()
        checks = [raw] if raw.is_absolute() else [root / raw, Path.cwd() / raw]
        for p in checks:
            if p.exists():
                return p.resolve()
        return None
    for p in candidates:
        if p.exists():
            return p.resolve()
    return None


def to_num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").fillna(0)


def parse_date(value: Any) -> pd.Timestamp | pd.NaT:
    s = clean(value)
    if not s:
        return pd.NaT
    try:
        return pd.Timestamp(dtparser.parse(s, dayfirst=True, fuzzy=True)).normalize()
    except Exception:
        return pd.NaT


def load_players(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    required = ["player_name", "country_en"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"CSV de jogadores sem colunas obrigatórias: {missing}")

    out = df.copy()
    out["country_en"] = out["country_en"].map(canonical_team)
    out["team_key_norm"] = out["country_en"].map(norm_key)
    out["player_key_norm"] = out["player_name"].map(norm_key)

    if "caps" not in out.columns:
        out["caps"] = 0
    if "goals" not in out.columns:
        out["goals"] = 0
    if "club" not in out.columns and "club_raw" in out.columns:
        out["club"] = out["club_raw"]
    if "club" not in out.columns:
        out["club"] = ""
    if "position" not in out.columns:
        out["position"] = ""
    if "date_of_birth" not in out.columns:
        out["date_of_birth"] = ""

    out["caps"] = to_num(out["caps"]).astype(int)
    out["international_goals"] = to_num(out["goals"]).astype(int)
    out["club"] = out["club"].map(clean)
    out["position"] = out["position"].map(clean)

    out["date_of_birth_parsed"] = out["date_of_birth"].map(parse_date)
    ref_date = pd.Timestamp("2026-06-11")
    out["age_at_world_cup_start"] = ((ref_date - out["date_of_birth_parsed"]).dt.days / 365.25).round(2)
    return out


def load_matches(path: Path | None) -> pd.DataFrame:
    if path is None or not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    out = df.copy()

    # Normaliza nomes de colunas comuns dos scrapers anteriores.
    rename = {
        "match_date": "match_date",
        "data": "match_date",
        "team_name": "team_name",
        "selection": "team_name",
        "goals_for": "goals_for",
        "goals_against": "goals_against",
        "goal_diff": "goal_diff",
        "result": "result",
        "competition": "competition",
        "competicao": "competition",
        "match_category": "match_category",
        "source_url": "source_url",
    }
    cols = {c: rename[c] for c in out.columns if c in rename and c != rename[c]}
    out = out.rename(columns=cols)

    if "team_name" not in out.columns:
        return pd.DataFrame()
    out["team_name"] = out["team_name"].map(canonical_team)
    out["team_key_norm"] = out["team_name"].map(norm_key)
    out["match_date"] = pd.to_datetime(out.get("match_date", pd.NaT), errors="coerce")
    out = out.dropna(subset=["match_date"])
    out = out.loc[(out["match_date"] >= "2022-01-01") & (out["match_date"] <= "2026-12-31")].copy()

    for col in ["goals_for", "goals_against"]:
        if col not in out.columns:
            out[col] = 0
        out[col] = to_num(out[col]).astype(int)
    if "goal_diff" not in out.columns:
        out["goal_diff"] = out["goals_for"] - out["goals_against"]
    if "result" not in out.columns:
        out["result"] = np.select(
            [out["goal_diff"] > 0, out["goal_diff"] < 0],
            ["W", "L"],
            default="D",
        )
    return out


def build_team_match_features(matches: pd.DataFrame) -> pd.DataFrame:
    if matches.empty:
        return pd.DataFrame(columns=["team_key_norm", "team_name"])

    rows = []
    for team_key, g in matches.groupby("team_key_norm"):
        g = g.sort_values("match_date")
        n = len(g)
        wins = int((g["result"] == "W").sum())
        draws = int((g["result"] == "D").sum())
        losses = int((g["result"] == "L").sum())
        gf = int(g["goals_for"].sum())
        ga = int(g["goals_against"].sum())
        last5 = g.tail(5)
        last10 = g.tail(10)
        rows.append({
            "team_key_norm": team_key,
            "team_name": canonical_team(g["team_name"].iloc[0]),
            "team_matches_2022_2026": n,
            "team_wins_2022_2026": wins,
            "team_draws_2022_2026": draws,
            "team_losses_2022_2026": losses,
            "team_loss_rate_2022_2026": losses / n if n else 0,
            "team_win_rate_2022_2026": wins / n if n else 0,
            "team_goals_for_2022_2026": gf,
            "team_goals_against_2022_2026": ga,
            "team_goal_diff_2022_2026": gf - ga,
            "team_goals_for_per_match": gf / n if n else 0,
            "team_goals_against_per_match": ga / n if n else 0,
            "team_last5_losses": int((last5["result"] == "L").sum()),
            "team_last5_win_rate": float((last5["result"] == "W").mean()) if len(last5) else 0,
            "team_last10_losses": int((last10["result"] == "L").sum()),
            "team_last10_win_rate": float((last10["result"] == "W").mean()) if len(last10) else 0,
            "team_first_match_date": g["match_date"].min().date().isoformat(),
            "team_last_match_date": g["match_date"].max().date().isoformat(),
        })
    return pd.DataFrame(rows)


def load_injuries(path: Path | None) -> pd.DataFrame:
    if path is None or not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    if "player_name" not in df.columns or "country_en" not in df.columns:
        raise ValueError("CSV de lesões precisa ter pelo menos player_name,country_en")
    out = df.copy()
    out["country_en"] = out["country_en"].map(canonical_team)
    out["team_key_norm"] = out["country_en"].map(norm_key)
    out["player_key_norm"] = out["player_name"].map(norm_key)
    out["start_date"] = pd.to_datetime(out.get("start_date", pd.NaT), errors="coerce")
    out["end_date"] = pd.to_datetime(out.get("end_date", pd.NaT), errors="coerce")
    for col in ["days_out", "games_missed"]:
        if col not in out.columns:
            out[col] = 0
        out[col] = to_num(out[col])
    return out


def build_injury_features(injuries: pd.DataFrame) -> pd.DataFrame:
    cols = [
        "team_key_norm", "player_key_norm", "injury_history_count_since_2022",
        "injury_days_out_since_2022", "injury_games_missed_since_2022",
        "latest_injury_type", "latest_injury_start_date", "latest_injury_end_date",
        "current_injury_flag", "injury_source_urls",
    ]
    if injuries.empty:
        return pd.DataFrame(columns=cols)

    df = injuries.copy()
    df = df.loc[df["start_date"].isna() | (df["start_date"] >= "2022-01-01")].copy()
    today = pd.Timestamp.today().normalize()
    rows = []
    for (team_key, player_key), g in df.groupby(["team_key_norm", "player_key_norm"]):
        g = g.sort_values("start_date", na_position="last")
        latest = g.iloc[-1]
        urls = []
        if "source_url" in g.columns:
            urls = [clean(u) for u in g["source_url"].dropna().astype(str).unique() if clean(u)]
        rows.append({
            "team_key_norm": team_key,
            "player_key_norm": player_key,
            "injury_history_count_since_2022": int(len(g)),
            "injury_days_out_since_2022": float(g["days_out"].sum()),
            "injury_games_missed_since_2022": float(g["games_missed"].sum()),
            "latest_injury_type": clean(latest.get("injury_type", "")),
            "latest_injury_start_date": latest.get("start_date").date().isoformat() if pd.notna(latest.get("start_date")) else "",
            "latest_injury_end_date": latest.get("end_date").date().isoformat() if pd.notna(latest.get("end_date")) else "",
            "current_injury_flag": bool(pd.notna(latest.get("end_date")) and latest.get("end_date") >= today),
            "injury_source_urls": " | ".join(urls[:5]),
        })
    return pd.DataFrame(rows, columns=cols)


def build_team_squad_features(players: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for team_key, g in players.groupby("team_key_norm"):
        club_counts = g["club"].replace("", np.nan).dropna().value_counts()
        rows.append({
            "team_key_norm": team_key,
            "team_name": canonical_team(g["country_en"].iloc[0]),
            "squad_players": int(len(g)),
            "squad_caps_total": int(g["caps"].sum()),
            "squad_caps_avg": float(g["caps"].mean()) if len(g) else 0,
            "squad_international_goals_total": int(g["international_goals"].sum()),
            "squad_international_goals_avg": float(g["international_goals"].mean()) if len(g) else 0,
            "squad_avg_age": float(g["age_at_world_cup_start"].mean()) if "age_at_world_cup_start" in g else np.nan,
            "squad_clubs_nunique": int(g["club"].replace("", np.nan).nunique()),
            "squad_top_club": club_counts.index[0] if len(club_counts) else "",
            "squad_top_club_players": int(club_counts.iloc[0]) if len(club_counts) else 0,
        })
    return pd.DataFrame(rows)


def build_club_distribution(players: pd.DataFrame) -> pd.DataFrame:
    df = players.copy()
    df["club"] = df["club"].replace("", np.nan)
    out = (
        df.dropna(subset=["club"])
        .groupby(["club", "country_en"], as_index=False)
        .agg(
            players=("player_name", "count"),
            caps_total=("caps", "sum"),
            goals_total=("international_goals", "sum"),
        )
        .sort_values(["players", "club"], ascending=[False, True])
    )
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--players-csv", default=None)
    parser.add_argument("--matches-csv", default=None)
    parser.add_argument("--injuries-csv", default=None)
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()

    root = resolve_repo_root()
    gold = root / "data" / "gold"
    raw = root / "data" / "raw"
    if args.output_dir:
        raw_out = Path(args.output_dir).expanduser()
        out_dir = raw_out if raw_out.is_absolute() else (root / raw_out)
    else:
        out_dir = gold
    out_dir.mkdir(parents=True, exist_ok=True)

    players_path = resolve_path(args.players_csv, [
        gold / "gold_worldcup_players_2026_fifa_official.csv",
        Path.cwd() / "data/gold/gold_worldcup_players_2026_fifa_official.csv",
    ])
    if players_path is None:
        raise FileNotFoundError("Não achei gold_worldcup_players_2026_fifa_official.csv. Rode primeiro o extrator do PDF da FIFA.")

    matches_path = resolve_path(args.matches_csv, [
        gold / "team_matches_2022_2026_free_sources.csv",
        gold / "team_matches_2022_2026_selenium.csv",
        Path.cwd() / "data/gold/team_matches_2022_2026_free_sources.csv",
    ])

    injuries_path = resolve_path(args.injuries_csv, [
        gold / "player_injuries_manual.csv",
        raw / "player_injuries_manual.csv",
        Path.cwd() / "player_injuries_manual.csv",
    ])

    players = load_players(players_path)
    matches = load_matches(matches_path)
    injuries = load_injuries(injuries_path)

    team_match_features = build_team_match_features(matches)
    injury_features = build_injury_features(injuries)
    team_squad_features = build_team_squad_features(players)

    player_profile = players.merge(
        team_match_features.drop(columns=["team_name"], errors="ignore"),
        on="team_key_norm",
        how="left",
    ).merge(
        injury_features,
        on=["team_key_norm", "player_key_norm"],
        how="left",
    )

    for col in ["injury_history_count_since_2022", "injury_days_out_since_2022", "injury_games_missed_since_2022"]:
        if col in player_profile.columns:
            player_profile[col] = player_profile[col].fillna(0)
    if "current_injury_flag" in player_profile.columns:
        player_profile["current_injury_flag"] = player_profile["current_injury_flag"].fillna(False)

    team_profile = team_squad_features.merge(
        team_match_features.drop(columns=["team_name"], errors="ignore"),
        on="team_key_norm",
        how="left",
    )

    if not injuries.empty:
        team_inj = (
            build_injury_features(injuries)
            .groupby("team_key_norm", as_index=False)
            .agg(
                squad_injury_records_since_2022=("injury_history_count_since_2022", "sum"),
                squad_injury_days_out_since_2022=("injury_days_out_since_2022", "sum"),
                squad_injury_games_missed_since_2022=("injury_games_missed_since_2022", "sum"),
                current_injured_players=("current_injury_flag", "sum"),
            )
        )
        team_profile = team_profile.merge(team_inj, on="team_key_norm", how="left")

    club_dist = build_club_distribution(players)

    player_out = out_dir / "worldcup_2026_player_profile_features.csv"
    team_out = out_dir / "worldcup_2026_team_profile_features.csv"
    club_out = out_dir / "worldcup_2026_club_distribution.csv"
    report_out = out_dir / "worldcup_2026_feature_build_report.csv"

    player_profile.to_csv(player_out, index=False)
    team_profile.to_csv(team_out, index=False)
    club_dist.to_csv(club_out, index=False)

    report = pd.DataFrame([{
        "players_csv": str(players_path),
        "players_rows": len(players),
        "players_teams": players["country_en"].nunique(),
        "matches_csv": str(matches_path) if matches_path else "not_found",
        "matches_rows": len(matches),
        "matches_teams": matches["team_name"].nunique() if not matches.empty else 0,
        "injuries_csv": str(injuries_path) if injuries_path else "not_found_optional",
        "injury_rows": len(injuries),
        "player_profile_out": str(player_out),
        "team_profile_out": str(team_out),
        "club_distribution_out": str(club_out),
    }])
    report.to_csv(report_out, index=False)

    print("Arquivos gerados:")
    print(f"- {player_out} ({len(player_profile)} jogadores)")
    print(f"- {team_out} ({len(team_profile)} seleções)")
    print(f"- {club_out} ({len(club_dist)} linhas clube/seleção)")
    print(f"- {report_out}")


if __name__ == "__main__":
    main()
