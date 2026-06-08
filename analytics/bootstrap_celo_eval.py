#!/usr/bin/env python3
"""
Bootstrap Isabel CELO replay predictions and simulate constructed roster fairness.

This is offline analytics. It reads files produced by replay_cortana_celo_models.py
and writes confidence intervals plus random roster matchup distributions.
"""

from __future__ import annotations

import argparse
import csv
import random
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any


ISABEL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPLAY_DIR = ISABEL_ROOT / "analytics" / "output" / "celo_replay"
DEFAULT_MODEL = "raid_stepwise_branch_seeded"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * pct)))
    return ordered[idx]


def expected_score(rating_a: float, rating_b: float) -> float:
    return 1.0 / (1.0 + (10.0 ** ((rating_b - rating_a) / 400.0)))


def grouped_predictions(rows: list[dict[str, str]]) -> dict[str, dict[int, list[dict[str, float]]]]:
    grouped: dict[str, dict[int, list[dict[str, float]]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        grouped[row["model_key"]][int(row["game_id"])].append(
            {
                "expected": float(row["expected"]),
                "actual": float(row["actual"]),
                "correct_side": float(row["correct_side"]),
                "brier": float(row["brier"]),
            }
        )
    return {model: dict(games) for model, games in grouped.items()}


def summarize_prediction_rows(rows: list[dict[str, float]]) -> dict[str, float]:
    if not rows:
        return {"pick_accuracy": 0.0, "brier": 0.0}
    return {
        "pick_accuracy": mean(row["correct_side"] for row in rows),
        "brier": mean(row["brier"] for row in rows),
    }


def summarize_games(games: dict[int, list[dict[str, float]]], game_ids: list[int]) -> dict[str, float]:
    rows = []
    for game_id in game_ids:
        rows.extend(games[game_id])
    return summarize_prediction_rows(rows)


def bootstrap_predictions(
    grouped: dict[str, dict[int, list[dict[str, float]]]],
    iterations: int,
    rng: random.Random,
) -> list[dict[str, Any]]:
    output = []
    for model_key, games in grouped.items():
        game_ids = sorted(games)
        if not game_ids:
            continue
        acc_values = []
        brier_values = []
        for _ in range(iterations):
            sample_rows = []
            for _ in game_ids:
                sampled_game = rng.choice(game_ids)
                sample_rows.extend(games[sampled_game])
            metrics = summarize_prediction_rows(sample_rows)
            acc_values.append(metrics["pick_accuracy"])
            brier_values.append(metrics["brier"])
        output.append(
            {
                "model_key": model_key,
                "games": len(game_ids),
                "bootstrap_iterations": iterations,
                "accuracy_mean": round(mean(acc_values), 6),
                "accuracy_p05": round(percentile(acc_values, 0.05), 6),
                "accuracy_p50": round(percentile(acc_values, 0.50), 6),
                "accuracy_p95": round(percentile(acc_values, 0.95), 6),
                "brier_mean": round(mean(brier_values), 6),
                "brier_p05": round(percentile(brier_values, 0.05), 6),
                "brier_p50": round(percentile(brier_values, 0.50), 6),
                "brier_p95": round(percentile(brier_values, 0.95), 6),
            }
        )
    output.sort(key=lambda row: (row["brier_mean"], -row["accuracy_mean"], row["model_key"]))
    return output


def bootstrap_model_deltas(
    grouped: dict[str, dict[int, list[dict[str, float]]]],
    candidate_model: str,
    iterations: int,
    rng: random.Random,
) -> list[dict[str, Any]]:
    candidate_games = grouped.get(candidate_model, {})
    output = []
    if not candidate_games:
        return output

    for model_key, games in grouped.items():
        if model_key == candidate_model:
            continue
        common_game_ids = sorted(set(candidate_games) & set(games))
        if not common_game_ids:
            continue

        acc_deltas = []
        brier_improvements = []
        for _ in range(iterations):
            sampled_ids = [rng.choice(common_game_ids) for _ in common_game_ids]
            candidate_metrics = summarize_games(candidate_games, sampled_ids)
            other_metrics = summarize_games(games, sampled_ids)
            acc_deltas.append(candidate_metrics["pick_accuracy"] - other_metrics["pick_accuracy"])
            brier_improvements.append(other_metrics["brier"] - candidate_metrics["brier"])

        output.append(
            {
                "candidate_model": candidate_model,
                "comparison_model": model_key,
                "games": len(common_game_ids),
                "bootstrap_iterations": iterations,
                "accuracy_delta_mean": round(mean(acc_deltas), 6),
                "accuracy_delta_p05": round(percentile(acc_deltas, 0.05), 6),
                "accuracy_delta_p50": round(percentile(acc_deltas, 0.50), 6),
                "accuracy_delta_p95": round(percentile(acc_deltas, 0.95), 6),
                "brier_improvement_mean": round(mean(brier_improvements), 6),
                "brier_improvement_p05": round(percentile(brier_improvements, 0.05), 6),
                "brier_improvement_p50": round(percentile(brier_improvements, 0.50), 6),
                "brier_improvement_p95": round(percentile(brier_improvements, 0.95), 6),
            }
        )

    output.sort(key=lambda row: (-row["accuracy_delta_mean"], -row["brier_improvement_mean"]))
    return output


def load_players(rankings: list[dict[str, str]], model_key: str, min_games: int) -> list[dict[str, Any]]:
    players = []
    for row in rankings:
        if row["model_key"] != model_key:
            continue
        games = int(float(row["games"] or 0))
        if games < min_games:
            continue
        players.append(
            {
                "discord_id": row["discord_id"],
                "rating": float(row["rating"]),
                "games": games,
                "rank": int(float(row["rank"])),
                "kd": float(row["kd"] or 0),
            }
        )
    return players


def simulate_rosters(
    players: list[dict[str, Any]],
    team_size: int,
    iterations: int,
    rng: random.Random,
) -> list[dict[str, Any]]:
    rows = []
    if len(players) < team_size * 2:
        return rows
    for idx in range(1, iterations + 1):
        sampled = rng.sample(players, team_size * 2)
        team_a = sampled[:team_size]
        team_b = sampled[team_size:]
        rating_a = mean(player["rating"] for player in team_a)
        rating_b = mean(player["rating"] for player in team_b)
        win_prob_a = expected_score(rating_a, rating_b)
        gap = abs(win_prob_a - 0.5)
        if gap <= 0.03:
            assessment = "very_fair"
        elif gap <= 0.07:
            assessment = "fair"
        elif gap <= 0.12:
            assessment = "slight_lean"
        elif gap <= 0.20:
            assessment = "uneven"
        else:
            assessment = "lopsided"
        rows.append(
            {
                "sample": idx,
                "team_size": team_size,
                "team_a_avg": round(rating_a, 4),
                "team_b_avg": round(rating_b, 4),
                "rating_gap": round(abs(rating_a - rating_b), 4),
                "team_a_win_probability": round(win_prob_a, 6),
                "fairness_gap": round(gap, 6),
                "assessment": assessment,
                "team_a": " ".join(player["discord_id"] for player in team_a),
                "team_b": " ".join(player["discord_id"] for player in team_b),
            }
        )
    return rows


def fairness_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    counts = defaultdict(int)
    for row in rows:
        counts[row["assessment"]] += 1
    total = len(rows)
    order = ["very_fair", "fair", "slight_lean", "uneven", "lopsided"]
    return [
        {
            "assessment": key,
            "count": counts[key],
            "share": round(counts[key] / total, 6),
        }
        for key in order
    ]


def write_markdown(
    path: Path,
    bootstrap: list[dict[str, Any]],
    deltas: list[dict[str, Any]],
    fairness: list[dict[str, Any]],
    args: argparse.Namespace,
) -> None:
    lines = [
        "# CELO Bootstrap And Roster Simulation",
        "",
        f"- Replay directory: `{args.replay_dir}`",
        f"- Public candidate model: `{args.model}`",
        f"- Bootstrap iterations: `{args.bootstrap_iterations}`",
        f"- Roster simulations: `{args.roster_iterations}`",
        f"- Team size: `{args.team_size}`",
        f"- Minimum games per simulated player: `{args.min_games}`",
        "",
        "## Bootstrap Model Evaluation",
        "",
        "| Model | Accuracy Mean | Accuracy 5-95% | Brier Mean | Brier 5-95% |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in bootstrap:
        lines.append(
            f"| {row['model_key']} | {row['accuracy_mean']} | "
            f"{row['accuracy_p05']} - {row['accuracy_p95']} | "
            f"{row['brier_mean']} | {row['brier_p05']} - {row['brier_p95']} |"
        )

    lines.extend(
        [
            "",
            "## Paired Public Model Deltas",
            "",
            "Positive accuracy delta means the public candidate picked more winners. "
            "Positive Brier improvement means the public candidate was better calibrated.",
            "",
            "| Comparison | Accuracy Delta Mean | Accuracy Delta 5-95% | Brier Improvement Mean | Brier Improvement 5-95% |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for row in deltas:
        lines.append(
            f"| {row['comparison_model']} | {row['accuracy_delta_mean']} | "
            f"{row['accuracy_delta_p05']} - {row['accuracy_delta_p95']} | "
            f"{row['brier_improvement_mean']} | "
            f"{row['brier_improvement_p05']} - {row['brier_improvement_p95']} |"
        )

    lines.extend(
        [
            "",
            "## Constructed Roster Fairness Distribution",
            "",
            "| Assessment | Count | Share |",
            "|---|---:|---:|",
        ]
    )
    for row in fairness:
        lines.append(f"| {row['assessment']} | {row['count']} | {row['share']:.2%} |")

    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap CELO replay metrics and simulate roster fairness.")
    parser.add_argument("--replay-dir", type=Path, default=DEFAULT_REPLAY_DIR)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--bootstrap-iterations", type=int, default=2000)
    parser.add_argument("--roster-iterations", type=int, default=5000)
    parser.add_argument("--team-size", type=int, default=8)
    parser.add_argument("--min-games", type=int, default=5)
    parser.add_argument("--seed", type=int, default=452)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    prediction_rows = read_csv(args.replay_dir / "model_predictions.csv")
    grouped = grouped_predictions(prediction_rows)
    bootstrap = bootstrap_predictions(grouped, max(100, int(args.bootstrap_iterations)), rng)
    write_csv(args.replay_dir / "bootstrap_model_eval.csv", bootstrap)
    deltas = bootstrap_model_deltas(grouped, args.model, max(100, int(args.bootstrap_iterations)), rng)
    write_csv(args.replay_dir / "bootstrap_model_deltas.csv", deltas)

    ranking_rows = read_csv(args.replay_dir / "player_rankings.csv")
    players = load_players(ranking_rows, args.model, min_games=max(1, int(args.min_games)))
    roster_rows = simulate_rosters(players, max(1, int(args.team_size)), max(100, int(args.roster_iterations)), rng)
    write_csv(args.replay_dir / "constructed_roster_simulations.csv", roster_rows)
    fairness = fairness_summary(roster_rows)
    write_csv(args.replay_dir / "constructed_roster_fairness_summary.csv", fairness)
    write_markdown(args.replay_dir / "bootstrap_and_roster_sim.md", bootstrap, deltas, fairness, args)

    print(f"Wrote bootstrap and roster simulation outputs to {args.replay_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
