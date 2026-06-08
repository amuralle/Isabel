#!/usr/bin/env python3
"""
Replay Cortana's larger-than-4v4 match history through Isabel CELO candidates.

This is offline analytics only. It reads Cortana's SQLite database and writes
CSV/JSON/Markdown summaries under analytics/output without mutating Cortana or
Isabel production rating tables.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any


ISABEL_ROOT = Path(__file__).resolve().parents[1]
if str(ISABEL_ROOT) not in sys.path:
    sys.path.insert(0, str(ISABEL_ROOT))

from helpers import db as isabel_db  # noqa: E402


DEFAULT_CORTANA_DB = Path("/home/Cortana/database/database.db")
DEFAULT_OUTDIR = ISABEL_ROOT / "analytics" / "output" / "celo_replay"
RAID_DEFENDER_TEAM = "0"
RAID_DEFENDER_LABEL = "Eagle"
BRANCH_PRIOR_RATINGS = {
    "spartan": 1060.0,
    "odst": 1015.0,
    "space assault": 1015.0,
    "marine": 970.0,
    "unmc": 950.0,
    "reserve": 950.0,
}


@dataclass
class RatingProfile:
    rating: float = 1000.0
    deviation: float = 350.0
    games: int = 0
    wins: int = 0
    losses: int = 0
    draws: int = 0
    net_delta: float = 0.0
    total_abs_delta: float = 0.0


@dataclass
class ModelState:
    profiles: dict[str, RatingProfile] = field(default_factory=dict)
    game_predictions: list[dict[str, Any]] = field(default_factory=list)
    deltas: list[dict[str, Any]] = field(default_factory=list)


def expected_score(rating_a: float, rating_b: float) -> float:
    return 1.0 / (1.0 + (10.0 ** ((rating_b - rating_a) / 400.0)))


def percentile_rank(values: list[float], value: float, *, lower_is_better: bool = False) -> float:
    if not values:
        return 0.5
    if len(values) == 1:
        return 0.5
    less = sum(1 for item in values if item < value)
    equal = sum(1 for item in values if item == value)
    pct = (less + 0.5 * equal) / float(len(values))
    if lower_is_better:
        pct = 1.0 - pct
    return max(0.0, min(1.0, pct))


def team_performance_scores(entries: dict[str, dict[str, Any]]) -> dict[str, float]:
    if not entries:
        return {}
    if len(entries) == 1:
        return {next(iter(entries)): 0.5}

    kills = [float(row["kills"]) for row in entries.values()]
    assists = [float(row["assists"]) for row in entries.values()]
    damage = [float(row["damage_dealt"]) for row in entries.values()]
    deaths = [float(row["deaths"]) for row in entries.values()]

    scores: dict[str, float] = {}
    for discord_id, row in entries.items():
        scores[discord_id] = max(
            0.0,
            min(
                1.0,
                0.30 * percentile_rank(kills, float(row["kills"]))
                + 0.20 * percentile_rank(assists, float(row["assists"]))
                + 0.35 * percentile_rank(damage, float(row["damage_dealt"]))
                + 0.15 * percentile_rank(deaths, float(row["deaths"]), lower_is_better=True),
            ),
        )
    return scores


def normalize_outcome(value: str | None) -> str:
    key = (value or "").strip().upper()
    if key in {"WIN", "W"}:
        return "WIN"
    if key in {"LOSS", "L", "DID_NOT_FINISH"}:
        return "LOSS"
    if key in {"DRAW", "TIE"}:
        return "DRAW"
    return "N/A"


def actual_score(outcome: str) -> float:
    if outcome == "WIN":
        return 1.0
    if outcome == "LOSS":
        return 0.0
    return 0.5


def share(value: int, total: int) -> float:
    return float(value) / float(total) if total else 0.5


def parity_from_shares(shares: list[float]) -> float:
    if not shares:
        return 0.0
    return max(0.0, min(1.0, 1.0 - (sum(abs(item - 0.5) for item in shares) / len(shares)) * 2.0))


def k_factor(profile: RatingProfile, config: dict[str, Any]) -> float:
    base = float(config["new_player_k"] if profile.games < 20 else config["experienced_k"])
    dev_mult = max(0.75, min(1.5, profile.deviation / 220.0))
    return base * dev_mult


def performance_multiplier(base_delta: float, perf_score: float, config: dict[str, Any]) -> float:
    if config.get("performance_mode") == "stepwise":
        bounded_score = max(0.0, min(1.0, float(perf_score or 0.5)))
        shift = 0.0
        for threshold, value in config.get("performance_steps", []):
            if bounded_score <= float(threshold):
                shift = float(value)
                break
        if base_delta < 0:
            shift = -shift
        max_shift = max(0.0, float(config.get("performance_shift", 0.0)))
        shift = max(-max_shift, min(max_shift, shift))
        return max(1.0 - max_shift, min(1.0 + max_shift, 1.0 + shift))

    shift_limit = max(0.0, float(config.get("performance_shift", 0.0)))
    if shift_limit <= 0:
        return 1.0
    centered = max(-1.0, min(1.0, (perf_score - 0.5) * 2.0))
    shift = centered * shift_limit
    if base_delta < 0:
        shift = -shift
    return max(1.0 - shift_limit, min(1.0 + shift_limit, 1.0 + shift))


def branch_prior_rating(reference: dict[str, dict[str, Any]], discord_id: str) -> float:
    branch = str(reference.get(str(discord_id), {}).get("branch") or "").lower()
    unit = str(reference.get(str(discord_id), {}).get("unit_designation") or "").lower()
    combined = f"{branch} {unit}"
    for needle, rating in BRANCH_PRIOR_RATINGS.items():
        if needle in combined:
            return rating
    return 1000.0


def new_profile(discord_id: str, config: dict[str, Any], reference: dict[str, dict[str, Any]]) -> RatingProfile:
    if config.get("branch_priors"):
        return RatingProfile(rating=branch_prior_rating(reference, discord_id))
    return RatingProfile()


def connect_rows(db_path: Path, min_players: int) -> list[dict[str, Any]]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """
        SELECT
            e.id AS event_id,
            e.guild_id,
            e.category,
            e.opponent,
            e.outcome AS event_outcome,
            e.timestamp,
            g.id AS game_id,
            g.match_id,
            g.map_name,
            g.mode_name,
            pgs.xuid,
            COALESCE(pgs.gamertag, x.gamertag, pgs.xuid) AS gamertag,
            x.discord_id,
            pgs.team,
            pgs.outcome AS player_outcome,
            COALESCE(pgs.kills, 0) AS kills,
            COALESCE(pgs.deaths, 0) AS deaths,
            COALESCE(pgs.assists, 0) AS assists,
            COALESCE(pgs.damage_dealt, 0) AS damage_dealt,
            game_counts.player_count
        FROM player_game_stats pgs
        JOIN games g ON g.id = pgs.game_id
        JOIN events e ON e.id = g.event_id
        LEFT JOIN xuids x ON x.xuid = pgs.xuid
        JOIN (
            SELECT game_id, COUNT(DISTINCT xuid) AS player_count
            FROM player_game_stats
            GROUP BY game_id
        ) AS game_counts ON game_counts.game_id = g.id
        WHERE game_counts.player_count > ?
          AND x.discord_id IS NOT NULL
          AND TRIM(COALESCE(x.discord_id, '')) != ''
        ORDER BY COALESCE(g.start_time, e.timestamp), e.id, g.id, pgs.team, x.discord_id
        """,
        (int(min_players),),
    ).fetchall()
    con.close()
    return [dict(row) for row in rows]


def load_smell_test_reference(db_path: Path) -> dict[str, dict[str, Any]]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    reference: dict[str, dict[str, Any]] = defaultdict(dict)

    for row in con.execute(
        """
        SELECT discord_id, score AS cortana_lethality, title AS cortana_lethality_title
        FROM user_lethality
        """
    ):
        reference[str(row["discord_id"])].update(dict(row))

    for row in con.execute(
        """
        SELECT x.discord_id, COUNT(DISTINCT e.id) AS events_played
        FROM events e
        JOIN games g ON g.event_id = e.id
        JOIN player_game_stats pgs ON pgs.game_id = g.id
        JOIN xuids x ON x.xuid = pgs.xuid
        WHERE x.discord_id IS NOT NULL
        GROUP BY x.discord_id
        """
    ):
        reference[str(row["discord_id"])].update(dict(row))

    for row in con.execute(
        """
        SELECT x.discord_id,
               SUM(COALESCE(pgs.kills, 0)) AS kills,
               SUM(COALESCE(pgs.deaths, 0)) AS deaths,
               SUM(COALESCE(pgs.assists, 0)) AS assists,
               SUM(COALESCE(pgs.damage_dealt, 0)) AS damage
        FROM player_game_stats pgs
        JOIN xuids x ON x.xuid = pgs.xuid
        WHERE x.discord_id IS NOT NULL
        GROUP BY x.discord_id
        """
    ):
        payload = dict(row)
        discord_id = str(payload.pop("discord_id"))
        reference[discord_id].update(payload)

    for row in con.execute(
        """
        SELECT user_id AS discord_id, branch, unit_designation, rank_name
        FROM observed_user_ranks
        """
    ):
        payload = dict(row)
        discord_id = str(payload.pop("discord_id"))
        reference[discord_id].update(payload)

    con.close()
    return {key: dict(value) for key, value in reference.items()}


def grouped_games(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    games: dict[int, dict[str, Any]] = {}
    for row in rows:
        game_id = int(row["game_id"])
        game = games.setdefault(
            game_id,
            {
                "event_id": int(row["event_id"]),
                "game_id": game_id,
                "match_id": row["match_id"],
                "timestamp": row["timestamp"],
                "category": row["category"],
                "opponent": row["opponent"],
                "player_count": int(row["player_count"]),
                "players": [],
            },
        )
        game["players"].append(row)
    return list(games.values())


def replay_games(
    games: list[dict[str, Any]],
    model_configs: dict[str, dict[str, Any]],
    reference: dict[str, dict[str, Any]],
) -> dict[str, ModelState]:
    states = {key: ModelState() for key in model_configs}

    for game in games:
        teams: dict[str, dict[str, Any]] = {}
        for row in game["players"]:
            team_id = str(row["team"] or "unknown")
            payload = teams.setdefault(team_id, {"players": set(), "outcomes": [], "entries": {}})
            outcome = normalize_outcome(row["player_outcome"])
            payload["outcomes"].append(outcome)
            discord_id = str(row["discord_id"])
            payload["players"].add(discord_id)
            entry = payload["entries"].setdefault(
                discord_id,
                {
                    "kills": 0,
                    "deaths": 0,
                    "assists": 0,
                    "damage_dealt": 0,
                    "gamertag": row["gamertag"],
                },
            )
            entry["kills"] += int(row["kills"] or 0)
            entry["deaths"] += int(row["deaths"] or 0)
            entry["assists"] += int(row["assists"] or 0)
            entry["damage_dealt"] += int(row["damage_dealt"] or 0)

        if len(teams) < 2:
            continue

        resolved: dict[str, dict[str, Any]] = {}
        for team_id, payload in teams.items():
            counts = {
                "WIN": payload["outcomes"].count("WIN"),
                "LOSS": payload["outcomes"].count("LOSS"),
                "DRAW": payload["outcomes"].count("DRAW"),
            }
            outcome = max(counts, key=counts.get)
            if counts[outcome] == 0:
                outcome = "DRAW"
            resolved[team_id] = {
                "outcome": outcome,
                "players": sorted(payload["players"]),
                "entries": payload["entries"],
                "performance_scores": team_performance_scores(payload["entries"]),
            }

        for model_key, config in model_configs.items():
            state = states[model_key]
            team_avgs: dict[str, float] = {}
            for team_id, payload in resolved.items():
                profiles = [
                    state.profiles.setdefault(pid, new_profile(pid, config, reference))
                    for pid in payload["players"]
                ]
                team_avgs[team_id] = mean(profile.rating for profile in profiles) if profiles else 1000.0

            for team_id, payload in resolved.items():
                opponent_avgs = [value for key, value in team_avgs.items() if key != team_id]
                opponent_avg = mean(opponent_avgs) if opponent_avgs else 1000.0
                expected = expected_score(team_avgs[team_id], opponent_avg)
                actual = actual_score(payload["outcome"])
                state.game_predictions.append(
                    {
                        "event_id": game["event_id"],
                        "game_id": game["game_id"],
                        "model_key": model_key,
                        "team": team_id,
                        "expected": expected,
                        "actual": actual,
                        "correct_side": int((expected >= 0.5 and actual == 1.0) or (expected < 0.5 and actual == 0.0)),
                        "brier": (expected - actual) ** 2,
                    }
                )

                for discord_id in payload["players"]:
                    profile = state.profiles.setdefault(discord_id, new_profile(discord_id, config, reference))
                    old_rating = profile.rating
                    k_value = k_factor(profile, config)
                    base_delta = k_value * (actual - expected)
                    perf_score = float(payload["performance_scores"].get(discord_id, 0.5))
                    multiplier = performance_multiplier(base_delta, perf_score, config)
                    delta = base_delta * multiplier
                    profile.rating = max(100.0, profile.rating + delta)
                    profile.deviation = max(
                        float(config["deviation_floor"]),
                        profile.deviation * float(config["deviation_decay"]),
                    )
                    profile.games += 1
                    profile.net_delta += delta
                    profile.total_abs_delta += abs(delta)
                    if actual == 1.0:
                        profile.wins += 1
                    elif actual == 0.0:
                        profile.losses += 1
                    else:
                        profile.draws += 1
                    state.deltas.append(
                        {
                            "event_id": game["event_id"],
                            "game_id": game["game_id"],
                            "match_id": game["match_id"],
                            "discord_id": discord_id,
                            "old_rating": round(old_rating, 4),
                            "new_rating": round(profile.rating, 4),
                            "delta": round(delta, 4),
                            "perf_score": round(perf_score, 4),
                            "multiplier": round(multiplier, 4),
                        }
                    )

    return states


def raid_parity_rows(games: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for game in games:
        defender = {"players": set(), "kills": 0, "deaths": 0, "assists": 0, "damage": 0}
        attackers = {"players": set(), "kills": 0, "deaths": 0, "assists": 0, "damage": 0}
        for player in game["players"]:
            target = defender if str(player["team"] or "") == RAID_DEFENDER_TEAM else attackers
            target["players"].add(str(player["discord_id"]))
            target["kills"] += int(player["kills"] or 0)
            target["deaths"] += int(player["deaths"] or 0)
            target["assists"] += int(player["assists"] or 0)
            target["damage"] += int(player["damage_dealt"] or 0)

        totals = {
            key: int(defender[key]) + int(attackers[key])
            for key in ("kills", "deaths", "assists", "damage")
        }
        defender_kill_share = share(int(defender["kills"]), totals["kills"])
        defender_damage_share = share(int(defender["damage"]), totals["damage"])
        defender_assist_share = share(int(defender["assists"]), totals["assists"])
        defender_death_share = share(int(defender["deaths"]), totals["deaths"])
        parity = parity_from_shares(
            [
                defender_kill_share,
                defender_damage_share,
                defender_assist_share,
                defender_death_share,
            ]
        )
        rows.append(
            {
                "event_id": game["event_id"],
                "game_id": game["game_id"],
                "match_id": game["match_id"],
                "map_name": next((p.get("map_name") for p in game["players"] if p.get("map_name")), ""),
                "mode_name": next((p.get("mode_name") for p in game["players"] if p.get("mode_name")), ""),
                "defender_team": RAID_DEFENDER_TEAM,
                "defender_label": RAID_DEFENDER_LABEL,
                "defender_players": len(defender["players"]),
                "attacker_players": len(attackers["players"]),
                "defender_kills": int(defender["kills"]),
                "attacker_kills": int(attackers["kills"]),
                "defender_deaths": int(defender["deaths"]),
                "attacker_deaths": int(attackers["deaths"]),
                "defender_assists": int(defender["assists"]),
                "attacker_assists": int(attackers["assists"]),
                "defender_damage": int(defender["damage"]),
                "attacker_damage": int(attackers["damage"]),
                "defender_kill_share": round(defender_kill_share, 6),
                "defender_damage_share": round(defender_damage_share, 6),
                "defender_assist_share": round(defender_assist_share, 6),
                "defender_death_share": round(defender_death_share, 6),
                "raid_stat_parity": round(parity, 6),
            }
        )
    return rows


def load_raid_parity_rows(db_path: Path, min_players: int) -> list[dict[str, Any]]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    games = con.execute(
        """
        SELECT
            e.id AS event_id,
            g.id AS game_id,
            g.match_id,
            g.map_name,
            g.mode_name
        FROM games g
        JOIN events e ON e.id = g.event_id
        JOIN (
            SELECT game_id, COUNT(DISTINCT xuid) AS player_count
            FROM player_game_stats
            GROUP BY game_id
        ) AS game_counts ON game_counts.game_id = g.id
        WHERE game_counts.player_count > ?
        ORDER BY COALESCE(g.start_time, e.timestamp), e.id, g.id
        """,
        (int(min_players),),
    ).fetchall()

    rows: list[dict[str, Any]] = []
    for game in games:
        stat_rows = con.execute(
            """
            SELECT
                team,
                COUNT(*) AS players,
                COALESCE(SUM(kills), 0) AS kills,
                COALESCE(SUM(deaths), 0) AS deaths,
                COALESCE(SUM(assists), 0) AS assists,
                COALESCE(SUM(damage_dealt), 0) AS damage
            FROM player_game_stats
            WHERE game_id = ?
            GROUP BY team
            """,
            (int(game["game_id"]),),
        ).fetchall()

        defender = {"players": 0, "kills": 0, "deaths": 0, "assists": 0, "damage": 0}
        attackers = {"players": 0, "kills": 0, "deaths": 0, "assists": 0, "damage": 0}
        for row in stat_rows:
            target = defender if str(row["team"] or "") == RAID_DEFENDER_TEAM else attackers
            for key in ("players", "kills", "deaths", "assists", "damage"):
                target[key] += int(row[key] or 0)

        totals = {
            key: int(defender[key]) + int(attackers[key])
            for key in ("kills", "deaths", "assists", "damage")
        }
        defender_kill_share = share(int(defender["kills"]), totals["kills"])
        defender_damage_share = share(int(defender["damage"]), totals["damage"])
        defender_assist_share = share(int(defender["assists"]), totals["assists"])
        defender_death_share = share(int(defender["deaths"]), totals["deaths"])
        parity = parity_from_shares(
            [
                defender_kill_share,
                defender_damage_share,
                defender_assist_share,
                defender_death_share,
            ]
        )
        rows.append(
            {
                "event_id": int(game["event_id"]),
                "game_id": int(game["game_id"]),
                "match_id": game["match_id"],
                "map_name": game["map_name"] or "",
                "mode_name": game["mode_name"] or "",
                "defender_team": RAID_DEFENDER_TEAM,
                "defender_label": RAID_DEFENDER_LABEL,
                "defender_players": int(defender["players"]),
                "attacker_players": int(attackers["players"]),
                "defender_kills": int(defender["kills"]),
                "attacker_kills": int(attackers["kills"]),
                "defender_deaths": int(defender["deaths"]),
                "attacker_deaths": int(attackers["deaths"]),
                "defender_assists": int(defender["assists"]),
                "attacker_assists": int(attackers["assists"]),
                "defender_damage": int(defender["damage"]),
                "attacker_damage": int(attackers["damage"]),
                "defender_kill_share": round(defender_kill_share, 6),
                "defender_damage_share": round(defender_damage_share, 6),
                "defender_assist_share": round(defender_assist_share, 6),
                "defender_death_share": round(defender_death_share, 6),
                "raid_stat_parity": round(parity, 6),
            }
        )
    con.close()
    return rows


def pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    x_mean = mean(xs)
    y_mean = mean(ys)
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
    x_den = math.sqrt(sum((x - x_mean) ** 2 for x in xs))
    y_den = math.sqrt(sum((y - y_mean) ** 2 for y in ys))
    if x_den == 0 or y_den == 0:
        return None
    return numerator / (x_den * y_den)


def profile_rows(states: dict[str, ModelState], reference: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for model_key, state in states.items():
        ordered = sorted(state.profiles.items(), key=lambda item: item[1].rating, reverse=True)
        for rank, (discord_id, profile) in enumerate(ordered, start=1):
            ref = reference.get(discord_id, {})
            deaths = int(ref.get("deaths", 0) or 0)
            kills = int(ref.get("kills", 0) or 0)
            assists = int(ref.get("assists", 0) or 0)
            rows.append(
                {
                    "model_key": model_key,
                    "rank": rank,
                    "discord_id": discord_id,
                    "rating": round(profile.rating, 4),
                    "deviation": round(profile.deviation, 4),
                    "games": profile.games,
                    "wins": profile.wins,
                    "losses": profile.losses,
                    "draws": profile.draws,
                    "net_delta": round(profile.net_delta, 4),
                    "avg_abs_delta": round(profile.total_abs_delta / profile.games, 4) if profile.games else 0,
                    "cortana_lethality": ref.get("cortana_lethality", ""),
                    "cortana_lethality_title": ref.get("cortana_lethality_title", ""),
                    "events_played": ref.get("events_played", ""),
                    "kills": kills,
                    "deaths": deaths,
                    "assists": assists,
                    "damage": int(ref.get("damage", 0) or 0),
                    "kd": round(kills / deaths, 4) if deaths else kills,
                    "kda": round((kills + assists) / deaths, 4) if deaths else kills + assists,
                }
            )
    return rows


def model_summary_rows(states: dict[str, ModelState], profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    by_model_profiles: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in profiles:
        by_model_profiles[row["model_key"]].append(row)

    for model_key, state in states.items():
        preds = state.game_predictions
        model_profiles = by_model_profiles[model_key]
        rated = [row for row in model_profiles if int(row["games"]) > 0]
        ratings = [float(row["rating"]) for row in rated]
        lethality_pairs = [
            (float(row["rating"]), float(row["cortana_lethality"]))
            for row in rated
            if str(row["cortana_lethality"]).strip() != ""
        ]
        event_pairs = [
            (float(row["rating"]), float(row["events_played"]))
            for row in rated
            if str(row["events_played"]).strip() != ""
        ]
        rows.append(
            {
                "model_key": model_key,
                "rated_players": len(rated),
                "predicted_team_rows": len(preds),
                "avg_brier": round(mean([p["brier"] for p in preds]), 6) if preds else "",
                "pick_accuracy": round(mean([p["correct_side"] for p in preds]), 6) if preds else "",
                "avg_rating": round(mean(ratings), 4) if ratings else "",
                "rating_spread": round(max(ratings) - min(ratings), 4) if ratings else "",
                "avg_abs_delta": round(mean([float(row["avg_abs_delta"]) for row in rated]), 4) if rated else "",
                "corr_rating_cortana_lethality": round(pearson([x for x, _ in lethality_pairs], [y for _, y in lethality_pairs]) or 0, 6)
                if len(lethality_pairs) > 1 else "",
                "corr_rating_events_played": round(pearson([x for x, _ in event_pairs], [y for _, y in event_pairs]) or 0, 6)
                if len(event_pairs) > 1 else "",
            }
        )
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_markdown(
    path: Path,
    summaries: list[dict[str, Any]],
    profiles: list[dict[str, Any]],
    parity_rows: list[dict[str, Any]],
    args: argparse.Namespace,
) -> None:
    lines = [
        "# Isabel CELO Replay Smell Test",
        "",
        f"- Source DB: `{args.cortana_db}`",
        f"- Minimum logged players per game: `>{args.min_players}`",
        f"- Raid defender assumption: `{RAID_DEFENDER_LABEL}` = team `{RAID_DEFENDER_TEAM}`",
        "- Branch prior replay seeds: `Spartan 1060`, `ODST 1015`, `Marine 970`, `Reserve 950`",
        f"- Generated at: `{datetime.now(UTC).isoformat(timespec='seconds')}`",
        "",
        "## Model Summary",
        "",
        "| Model | Players | Brier | Pick Accuracy | Spread | Corr Lethality | Corr Events |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summaries:
        lines.append(
            f"| {row['model_key']} | {row['rated_players']} | {row['avg_brier']} | "
            f"{row['pick_accuracy']} | {row['rating_spread']} | "
            f"{row['corr_rating_cortana_lethality']} | {row['corr_rating_events_played']} |"
        )

    if parity_rows:
        avg_parity = mean(float(row["raid_stat_parity"]) for row in parity_rows)
        avg_def_kills = mean(float(row["defender_kill_share"]) for row in parity_rows)
        avg_def_damage = mean(float(row["defender_damage_share"]) for row in parity_rows)
        lines.extend(
            [
                "",
                "## Raid Stat Parity",
                "",
                f"- Average parity: `{avg_parity:.4f}`",
                f"- Average defender kill share: `{avg_def_kills:.2%}`",
                f"- Average defender damage share: `{avg_def_damage:.2%}`",
                "",
            ]
        )

    lines.extend(["", "## Top 15 By Model", ""])
    for model_key in sorted({row["model_key"] for row in profiles}):
        lines.extend([f"### {model_key}", ""])
        model_rows = [row for row in profiles if row["model_key"] == model_key and int(row["rank"]) <= 15]
        lines.append("| Rank | Discord ID | Rating | Games | W/L/D | Cortana Lethality | K/D |")
        lines.append("|---:|---|---:|---:|---|---:|---:|")
        for row in model_rows:
            lines.append(
                f"| {row['rank']} | `{row['discord_id']}` | {row['rating']} | {row['games']} | "
                f"{row['wins']}/{row['losses']}/{row['draws']} | {row['cortana_lethality']} | {row['kd']} |"
            )
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay Cortana >4v4 history through Isabel CELO candidates.")
    parser.add_argument("--cortana-db", type=Path, default=DEFAULT_CORTANA_DB)
    parser.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR)
    parser.add_argument("--min-players", type=int, default=8, help="Use games with player_count greater than this value.")
    args = parser.parse_args()

    rows = connect_rows(args.cortana_db, min_players=args.min_players)
    games = grouped_games(rows)
    reference = load_smell_test_reference(args.cortana_db)

    model_configs = {
        key: value
        for key, value in isabel_db.CELO_MODEL_CONFIGS.items()
        if key in {"baseline", "conservative", "performance", "raid_flat", "raid_stepwise"}
    }
    for base_key in ("baseline", "raid_stepwise"):
        seeded_key = f"{base_key}_branch_seeded"
        model_configs[seeded_key] = {
            **model_configs[base_key],
            "label": f"{model_configs[base_key]['label']} + Branch Priors",
            "description": f"{model_configs[base_key]['description']} Starts UNSC branches at conservative tier priors.",
            "branch_priors": True,
        }

    states = replay_games(games, model_configs, reference)
    profiles = profile_rows(states, reference)
    summaries = model_summary_rows(states, profiles)
    parity = load_raid_parity_rows(args.cortana_db, min_players=args.min_players)

    args.outdir.mkdir(parents=True, exist_ok=True)
    write_csv(args.outdir / "model_summary.csv", summaries)
    write_csv(args.outdir / "player_rankings.csv", profiles)
    write_csv(args.outdir / "raid_parity.csv", parity)
    prediction_rows = []
    for model_key, state in states.items():
        write_csv(args.outdir / f"{model_key}_deltas.csv", state.deltas)
        prediction_rows.extend(state.game_predictions)
    write_csv(args.outdir / "model_predictions.csv", prediction_rows)
    write_markdown(args.outdir / "README.md", summaries, profiles, parity, args)
    (args.outdir / "run_metadata.json").write_text(
        json.dumps(
            {
                "source_db": str(args.cortana_db),
                "min_players_exclusive": args.min_players,
                "games_replayed": len(games),
                "linked_stat_rows": len(rows),
                "models": list(model_configs.keys()),
                "branch_priors": BRANCH_PRIOR_RATINGS,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    print(f"Replayed {len(games)} games with {len(rows)} linked player rows.")
    print(f"Wrote {args.outdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
