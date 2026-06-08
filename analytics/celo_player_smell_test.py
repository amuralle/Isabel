#!/usr/bin/env python3
"""
Build a player-focused CELO smell-test report from replay output.

This report is intentionally analytical, not authoritative. It helps compare
which players each candidate model lifts, which names are consensus picks, and
which players appear stat-inflated versus outcome-reliable.
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any


ISABEL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPLAY_DIR = ISABEL_ROOT / "analytics" / "output" / "celo_replay"
DEFAULT_CORTANA_DB = Path("/home/Cortana/database/database.db")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def load_roster_context(cortana_db: Path) -> dict[str, dict[str, str]]:
    con = sqlite3.connect(cortana_db)
    con.row_factory = sqlite3.Row
    roster_rows = con.execute(
        """
        SELECT user_id, branch, rank_name, unit_designation, deployment_status, allegiance
        FROM observed_user_ranks
        """
    ).fetchall()
    gamertag_rows = con.execute(
        """
        SELECT discord_id, gamertag, games
        FROM (
            SELECT
                x.discord_id,
                COALESCE(pgs.gamertag, x.gamertag) AS gamertag,
                COUNT(*) AS games,
                ROW_NUMBER() OVER (
                    PARTITION BY x.discord_id
                    ORDER BY COUNT(*) DESC, COALESCE(pgs.gamertag, x.gamertag) ASC
                ) AS rn
            FROM xuids x
            JOIN player_game_stats pgs ON pgs.xuid = x.xuid
            WHERE x.discord_id IS NOT NULL
              AND TRIM(COALESCE(x.discord_id, '')) != ''
              AND TRIM(COALESCE(pgs.gamertag, x.gamertag, '')) != ''
            GROUP BY x.discord_id, COALESCE(pgs.gamertag, x.gamertag)
        )
        WHERE rn = 1
        """
    ).fetchall()
    con.close()
    context = {str(row["user_id"]): dict(row) for row in roster_rows}
    for row in gamertag_rows:
        payload = context.setdefault(str(row["discord_id"]), {})
        payload["gamertag"] = str(row["gamertag"])
        payload["gamertag_games"] = str(row["games"])
    return context


def label_for(discord_id: str, context: dict[str, dict[str, str]]) -> str:
    row = context.get(str(discord_id))
    if not row:
        return f"<@{discord_id}>"
    primary = row.get("gamertag") or f"<@{discord_id}>"
    parts = []
    if row.get("rank_name"):
        parts.append(str(row["rank_name"]))
    if row.get("unit_designation"):
        parts.append(str(row["unit_designation"]))
    if row.get("branch"):
        parts.append(str(row["branch"]))
    secondary = f" ({', '.join(parts)})" if parts else ""
    return f"**{primary}** / <@{discord_id}>{secondary}"


def index_by_model(rows: list[dict[str, str]]) -> dict[str, list[dict[str, Any]]]:
    by_model: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        payload: dict[str, Any] = dict(row)
        for key in (
            "rank",
            "games",
            "wins",
            "losses",
            "draws",
            "events_played",
            "kills",
            "deaths",
            "assists",
            "damage",
        ):
            payload[key] = int(float(payload[key] or 0))
        for key in ("rating", "net_delta", "avg_abs_delta", "kd", "kda"):
            payload[key] = float(payload[key] or 0)
        payload["cortana_lethality"] = int(float(payload["cortana_lethality"] or 0))
        by_model[payload["model_key"]].append(payload)
    for rows_for_model in by_model.values():
        rows_for_model.sort(key=lambda item: int(item["rank"]))
    return dict(by_model)


def row_by_user(by_model: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, dict[str, Any]]]:
    output: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for model, rows in by_model.items():
        for row in rows:
            output[str(row["discord_id"])][model] = row
    return dict(output)


def top_overlap(by_model: dict[str, list[dict[str, Any]]], top_n: int) -> list[dict[str, Any]]:
    counts: dict[str, dict[str, Any]] = {}
    for model, rows in by_model.items():
        for row in rows[:top_n]:
            discord_id = str(row["discord_id"])
            payload = counts.setdefault(discord_id, {"discord_id": discord_id, "models": [], "ranks": []})
            payload["models"].append(model)
            payload["ranks"].append(int(row["rank"]))
    output = []
    for payload in counts.values():
        output.append(
            {
                **payload,
                "model_count": len(payload["models"]),
                "avg_rank": mean(payload["ranks"]),
            }
        )
    output.sort(key=lambda item: (-item["model_count"], item["avg_rank"], item["discord_id"]))
    return output


def rank_delta_table(users: dict[str, dict[str, dict[str, Any]]], left: str, right: str) -> list[dict[str, Any]]:
    rows = []
    for discord_id, payload in users.items():
        if left not in payload or right not in payload:
            continue
        left_row = payload[left]
        right_row = payload[right]
        rows.append(
            {
                "discord_id": discord_id,
                "left_rank": int(left_row["rank"]),
                "right_rank": int(right_row["rank"]),
                "rank_delta": int(left_row["rank"]) - int(right_row["rank"]),
                "left_rating": float(left_row["rating"]),
                "right_rating": float(right_row["rating"]),
                "rating_delta": float(right_row["rating"]) - float(left_row["rating"]),
                "games": int(left_row["games"]),
                "wins": int(left_row["wins"]),
                "losses": int(left_row["losses"]),
                "kd": float(left_row["kd"]),
                "kda": float(left_row["kda"]),
                "lethality": int(left_row["cortana_lethality"]),
            }
        )
    return rows


def format_top_model(model: str, rows: list[dict[str, Any]], context: dict[str, dict[str, str]], top_n: int) -> list[str]:
    lines = [f"### {model}", "", "| Rank | Player | Rating | Games | W/L/D | K/D | Lethality |", "|---:|---|---:|---:|---|---:|---:|"]
    for row in rows[:top_n]:
        lines.append(
            f"| {row['rank']} | {label_for(str(row['discord_id']), context)} | {row['rating']:.1f} | "
            f"{row['games']} | {row['wins']}/{row['losses']}/{row['draws']} | {row['kd']:.2f} | {row['cortana_lethality']} |"
        )
    lines.append("")
    return lines


def write_report(
    output_path: Path,
    by_model: dict[str, list[dict[str, Any]]],
    context: dict[str, dict[str, str]],
    top_n: int,
) -> None:
    users = row_by_user(by_model)
    consensus = top_overlap(by_model, top_n)
    perf_minus_baseline = rank_delta_table(users, "baseline", "performance")
    perf_lifts = sorted(perf_minus_baseline, key=lambda row: (-row["rank_delta"], -row["rating_delta"]))[:15]
    perf_drops = sorted(perf_minus_baseline, key=lambda row: (row["rank_delta"], row["rating_delta"]))[:15]
    raid_vs_perf = rank_delta_table(users, "raid_stepwise", "performance")
    stat_sensitive = sorted(raid_vs_perf, key=lambda row: (-row["rank_delta"], -row["rating_delta"]))[:15]
    raid_protected = sorted(raid_vs_perf, key=lambda row: (row["rank_delta"], row["rating_delta"]))[:15]
    branch_seeded = rank_delta_table(users, "raid_stepwise", "raid_stepwise_branch_seeded")
    branch_lifts = sorted(branch_seeded, key=lambda row: (-row["rank_delta"], -row["rating_delta"]))[:15]
    branch_drops = sorted(branch_seeded, key=lambda row: (row["rank_delta"], row["rating_delta"]))[:15]

    lines = [
        "# CELO Player Smell Test",
        "",
        "This is a model-comparison aid, not an official player ranking.",
        "",
        "## Consensus Top Players",
        "",
        f"Players appearing in the top {top_n} of multiple models.",
        "",
        "| Player | Model Count | Avg Rank | Models |",
        "|---|---:|---:|---|",
    ]
    for row in consensus[:25]:
        lines.append(
            f"| {label_for(row['discord_id'], context)} | {row['model_count']} | {row['avg_rank']:.1f} | "
            f"{', '.join(row['models'])} |"
        )

    lines.extend(["", "## Top Players By Model", ""])
    for model in (
        "baseline",
        "baseline_branch_seeded",
        "raid_flat",
        "raid_stepwise",
        "raid_stepwise_branch_seeded",
        "conservative",
        "performance",
    ):
        if model in by_model:
            lines.extend(format_top_model(model, by_model[model], context, top_n))

    lines.extend(
        [
            "",
            "## Performance Model Lifts",
            "",
            "Players who rise most when the model gives stronger weight to individual stats.",
            "",
            "| Player | Baseline Rank | Performance Rank | Rating Lift | Games | K/D | Lethality |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in perf_lifts:
        lines.append(
            f"| {label_for(row['discord_id'], context)} | {row['left_rank']} | {row['right_rank']} | "
            f"{row['rating_delta']:.1f} | {row['games']} | {row['kd']:.2f} | {row['lethality']} |"
        )

    lines.extend(
        [
            "",
            "## Performance Model Drops",
            "",
            "Players who are valued more by outcome reliability than by individual stat weighting.",
            "",
            "| Player | Baseline Rank | Performance Rank | Rating Change | Games | K/D | Lethality |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in perf_drops:
        lines.append(
            f"| {label_for(row['discord_id'], context)} | {row['left_rank']} | {row['right_rank']} | "
            f"{row['rating_delta']:.1f} | {row['games']} | {row['kd']:.2f} | {row['lethality']} |"
        )

    lines.extend(
        [
            "",
            "## Stat-Sensitive Players",
            "",
            "Players who rise most in `performance` compared with `raid_stepwise`; these are worth human review for role/stat inflation versus genuine carry value.",
            "",
            "| Player | Raid Stepwise Rank | Performance Rank | Rating Lift | Games | K/D | Lethality |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in stat_sensitive:
        lines.append(
            f"| {label_for(row['discord_id'], context)} | {row['left_rank']} | {row['right_rank']} | "
            f"{row['rating_delta']:.1f} | {row['games']} | {row['kd']:.2f} | {row['lethality']} |"
        )

    lines.extend(
        [
            "",
            "## Raid-Protected Players",
            "",
            "Players who rank better in `raid_stepwise` than in `performance`; often outcome-reliable or less stat-forward profiles.",
            "",
            "| Player | Raid Stepwise Rank | Performance Rank | Rating Change | Games | K/D | Lethality |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in raid_protected:
        lines.append(
            f"| {label_for(row['discord_id'], context)} | {row['left_rank']} | {row['right_rank']} | "
            f"{row['rating_delta']:.1f} | {row['games']} | {row['kd']:.2f} | {row['lethality']} |"
        )

    if branch_lifts or branch_drops:
        lines.extend(
            [
                "",
                "## Branch Prior Lifts",
                "",
                "Players who rise most when `raid_stepwise` starts Spartans/ODSTs/Marines at conservative branch priors.",
                "",
                "| Player | Raid Stepwise Rank | Branch-Seeded Rank | Rating Lift | Games | K/D | Lethality |",
                "|---|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for row in branch_lifts:
            lines.append(
                f"| {label_for(row['discord_id'], context)} | {row['left_rank']} | {row['right_rank']} | "
                f"{row['rating_delta']:.1f} | {row['games']} | {row['kd']:.2f} | {row['lethality']} |"
            )

        lines.extend(
            [
                "",
                "## Branch Prior Drops",
                "",
                "Players who fall most under branch seeding; useful for spotting high performers from lower-seeded branches or over-seeded profiles.",
                "",
                "| Player | Raid Stepwise Rank | Branch-Seeded Rank | Rating Change | Games | K/D | Lethality |",
                "|---|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for row in branch_drops:
            lines.append(
                f"| {label_for(row['discord_id'], context)} | {row['left_rank']} | {row['right_rank']} | "
                f"{row['rating_delta']:.1f} | {row['games']} | {row['kd']:.2f} | {row['lethality']} |"
            )

    output_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a CELO player smell-test report.")
    parser.add_argument("--replay-dir", type=Path, default=DEFAULT_REPLAY_DIR)
    parser.add_argument("--cortana-db", type=Path, default=DEFAULT_CORTANA_DB)
    parser.add_argument("--top-n", type=int, default=15)
    args = parser.parse_args()

    rows = read_csv(args.replay_dir / "player_rankings.csv")
    by_model = index_by_model(rows)
    context = load_roster_context(args.cortana_db)
    output_path = args.replay_dir / "player_smell_test.md"
    write_report(output_path, by_model, context, max(1, int(args.top_n)))
    print(f"Wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
