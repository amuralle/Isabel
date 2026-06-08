#!/usr/bin/env python3
"""
Run cyclical historical raid breadth discovery without an interactive agent.

This orchestrates historical_raid_backfill.py in repeatable cycles. Each cycle
uses the current frontier of high-overlap raid opponents as seeds, writes outputs
to a cycle folder, updates a checkpoint, and prepares the next frontier.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ISABEL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTDIR = ISABEL_ROOT / "analytics" / "output" / "historical_raid_loop"
BACKFILL_SCRIPT = ISABEL_ROOT / "analytics" / "historical_raid_backfill.py"


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
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


def load_state(path: Path, seeds: list[str]) -> dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "completed_cycles": 0,
        "seed_players": seeds,
        "frontier": seeds,
        "crawled_players": [],
        "known_match_ids": [],
        "cycles": [],
    }


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def repair_failed_frontier(state: dict[str, Any]) -> None:
    crawled = set(state.get("crawled_players", []))
    frontier = unique_ordered(state.get("frontier", []))
    for cycle in state.get("cycles", []):
        if int(cycle.get("returncode") or 0) == 0:
            continue
        failed_seeds = unique_ordered(str(cycle.get("seeds") or "").split(","))
        for seed in failed_seeds:
            crawled.discard(seed)
            if seed not in frontier:
                frontier.insert(0, seed)
    state["crawled_players"] = sorted(crawled)
    state["frontier"] = unique_ordered(frontier)


def unique_ordered(values: list[str]) -> list[str]:
    output = []
    seen = set()
    for value in values:
        cleaned = str(value).strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        output.append(cleaned)
    return output


def pick_batch(frontier: list[str], crawled: set[str], size: int) -> list[str]:
    return [player for player in unique_ordered(frontier) if player not in crawled][: max(1, size)]


def graph_score(row: dict[str, str]) -> tuple[int, int, int, int, str]:
    return (
        int(float(row.get("opponent_raid_overlap_matches") or 0)),
        int(float(row.get("likely_raid_overlap_matches") or 0)),
        int(float(row.get("raid_overlap_matches") or 0)),
        int(float(row.get("source_players_seen_with") or 0)),
        str(row.get("xuid") or ""),
    )


def next_frontier_from_cycle(cycle_dir: Path, crawled: set[str], limit: int) -> list[str]:
    graph_rows = read_csv(cycle_dir / "historical_raid_player_graph_aggregate.csv")
    graph_rows.sort(key=graph_score, reverse=True)
    output = []
    for row in graph_rows:
        xuid = str(row.get("xuid") or "").strip()
        if not xuid or xuid in crawled:
            continue
        if int(float(row.get("opponent_raid_overlap_matches") or 0)) <= 0:
            continue
        output.append(xuid)
        if len(output) >= limit:
            break
    return output


def cycle_summary(cycle_num: int, cycle_dir: Path, seeds: list[str], command: list[str], returncode: int) -> dict[str, Any]:
    candidate_rows = read_csv(cycle_dir / "historical_raid_candidates.csv")
    graph_rows = read_csv(cycle_dir / "historical_raid_player_graph_aggregate.csv")
    buckets: dict[str, int] = {}
    for row in candidate_rows:
        bucket = str(row.get("raid_bucket") or "unknown")
        buckets[bucket] = buckets.get(bucket, 0) + 1
    return {
        "cycle": cycle_num,
        "cycle_dir": str(cycle_dir),
        "seeds": ",".join(seeds),
        "returncode": returncode,
        "candidate_matches": len(candidate_rows),
        "likely_raid": buckets.get("likely_raid", 0),
        "possible_raid": buckets.get("possible_raid", 0),
        "not_raid": buckets.get("not_raid", 0),
        "graph_players": len(graph_rows),
        "command": " ".join(command),
    }


def write_loop_summary(outdir: Path, state: dict[str, Any]) -> None:
    cycle_rows = state.get("cycles", [])
    write_csv(outdir / "loop_cycles.csv", cycle_rows)
    lines = [
        "# Historical Raid Loop",
        "",
        "Analytics-only cyclic breadth search. This does not write to live Isabel event tables.",
        "",
        f"- Completed cycles: `{state.get('completed_cycles', 0)}`",
        f"- Crawled players: `{len(state.get('crawled_players', []))}`",
        f"- Known match IDs: `{len(state.get('known_match_ids', []))}`",
        f"- Frontier players queued: `{len(state.get('frontier', []))}`",
        "",
        "## Cycles",
        "",
        "| Cycle | Seeds | Candidates | Likely | Possible | Graph Players | Return |",
        "|---:|---|---:|---:|---:|---:|---:|",
    ]
    for row in cycle_rows:
        lines.append(
            f"| {row['cycle']} | `{row['seeds']}` | {row['candidate_matches']} | "
            f"{row['likely_raid']} | {row['possible_raid']} | {row['graph_players']} | {row['returncode']} |"
        )
    (outdir / "README.md").write_text("\n".join(lines), encoding="utf-8")


def build_command(args: argparse.Namespace, seeds: list[str], cycle_dir: Path) -> list[str]:
    return [
        sys.executable,
        str(BACKFILL_SCRIPT),
        *seeds,
        "--cutoff",
        args.cutoff,
        "--max-pages",
        str(args.max_pages),
        "--breadth-depth",
        str(args.breadth_depth),
        "--max-breadth-players",
        str(args.max_breadth_players),
        "--expand-buckets",
        args.expand_buckets,
        "--frontier-side",
        args.frontier_side,
        "--inspect-stats-mode",
        args.inspect_stats_mode,
        "--match-type",
        args.match_type,
        "--concurrency",
        str(args.concurrency),
        "--outdir",
        str(cycle_dir),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run checkpointed cyclical historical raid breadth discovery.")
    parser.add_argument("seeds", nargs="+", help="Initial gamertags or XUIDs, e.g. Flubso.")
    parser.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR)
    parser.add_argument("--cycles", type=int, default=20)
    parser.add_argument("--players-per-cycle", type=int, default=5)
    parser.add_argument("--frontier-limit", type=int, default=250)
    parser.add_argument("--sleep-seconds", type=float, default=20.0)
    parser.add_argument("--error-sleep-seconds", type=float, default=300.0)
    parser.add_argument("--cutoff", default="2023-01-01T00:00:00+00:00")
    parser.add_argument("--max-pages", type=int, default=6)
    parser.add_argument("--breadth-depth", type=int, default=1)
    parser.add_argument("--max-breadth-players", type=int, default=10)
    parser.add_argument("--expand-buckets", default="likely_raid")
    parser.add_argument("--frontier-side", choices=["opponents", "teammates", "all"], default="opponents")
    parser.add_argument("--inspect-stats-mode", choices=["none", "raidish", "all"], default="raidish")
    parser.add_argument("--match-type", choices=["all", "matchmaking", "custom", "local"], default="all")
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--stop-on-error", action="store_true")
    parser.add_argument("--reset-state", action="store_true", help="Discard loop_state.json and start from the provided seeds.")
    args = parser.parse_args()

    args.outdir.mkdir(parents=True, exist_ok=True)
    state_path = args.outdir / "loop_state.json"
    if args.reset_state and state_path.exists():
        state_path.unlink()
    state = load_state(state_path, unique_ordered(args.seeds))
    repair_failed_frontier(state)

    for _ in range(max(0, int(args.cycles))):
        crawled = set(state.get("crawled_players", []))
        frontier = unique_ordered(state.get("frontier", []))
        seeds = pick_batch(frontier, crawled, int(args.players_per_cycle))
        if not seeds:
            print("No uncrawled frontier players remain.")
            break

        cycle_num = int(state.get("completed_cycles", 0)) + 1
        cycle_dir = args.outdir / f"cycle_{cycle_num:04d}"
        command = build_command(args, seeds, cycle_dir)
        print(f"[cycle {cycle_num}] crawling {', '.join(seeds)}")
        completed = subprocess.run(command, cwd=ISABEL_ROOT)
        cycle_row = cycle_summary(cycle_num, cycle_dir, seeds, command, completed.returncode)

        if completed.returncode != 0:
            state["completed_cycles"] = cycle_num
            state["frontier"] = unique_ordered(seeds + frontier)[: max(1, int(args.frontier_limit))]
            state.setdefault("cycles", []).append(cycle_row)
            save_state(state_path, state)
            write_loop_summary(args.outdir, state)
            if args.stop_on_error:
                return completed.returncode
            print(
                f"[cycle {cycle_num}] failed with return code {completed.returncode}; "
                f"keeping seeds queued and sleeping {args.error_sleep_seconds:g}s"
            )
            if args.error_sleep_seconds > 0:
                time.sleep(float(args.error_sleep_seconds))
            continue

        candidate_rows = read_csv(cycle_dir / "historical_raid_candidates.csv")
        known_match_ids = set(state.get("known_match_ids", []))
        known_match_ids.update(str(row.get("match_id")) for row in candidate_rows if row.get("match_id"))

        crawled.update(seeds)
        for row in candidate_rows:
            source = str(row.get("source_player") or "").strip()
            if source:
                crawled.add(source)

        next_frontier = next_frontier_from_cycle(cycle_dir, crawled, int(args.frontier_limit))
        merged_frontier = [player for player in unique_ordered(next_frontier + frontier) if player not in crawled]

        state["completed_cycles"] = cycle_num
        state["crawled_players"] = sorted(crawled)
        state["known_match_ids"] = sorted(known_match_ids)
        state["frontier"] = merged_frontier[: max(1, int(args.frontier_limit))]
        state.setdefault("cycles", []).append(cycle_row)
        save_state(state_path, state)
        write_loop_summary(args.outdir, state)

        if args.sleep_seconds > 0:
            time.sleep(float(args.sleep_seconds))

    save_state(state_path, state)
    write_loop_summary(args.outdir, state)
    print(f"Wrote loop outputs to {args.outdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
