from __future__ import annotations

import argparse
import asyncio
import csv
import json
import shutil
import sqlite3
import sys
from collections import Counter
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

import aiosqlite

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from helpers import cortana_import
from helpers import db as isabel_db


DEFAULT_ISABEL_DB = PROJECT_ROOT / "database" / "database.db"
DEFAULT_CORTANA_DB = Path("/home/Cortana/database/database.db")
DEFAULT_BACKUP_DIR = PROJECT_ROOT / "database" / "backups"
DEFAULT_HISTORICAL_CANDIDATES = PROJECT_ROOT / "analytics" / "output" / "historical_raids" / "historical_raid_candidates.csv"


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _connect(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


def backup_database(source: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"{source.stem}.celo-production-seed.{_timestamp()}{source.suffix}"
    shutil.copy2(source, target)
    return target


def dry_run_database(source: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"{source.stem}.celo-production-seed-dry-run.{_timestamp()}{source.suffix}"
    shutil.copy2(source, target)
    return target


def cortana_unsc_seed_rows(cortana_db: Path) -> list[dict[str, Any]]:
    con = _connect(cortana_db)
    rows = con.execute(
        """
        SELECT
            x.xuid,
            x.gamertag,
            x.discord_id,
            r.branch,
            r.unit_designation,
            r.rank_name
        FROM xuids x
        JOIN observed_user_ranks r
          ON r.user_id = x.discord_id
        WHERE x.xuid IS NOT NULL
        ORDER BY x.xuid ASC
        """
    ).fetchall()
    con.close()
    output: list[dict[str, Any]] = []
    for row in rows:
        tier = isabel_db.unsc_branch_seed_tier(row["branch"], row["unit_designation"])
        if not tier:
            continue
        output.append(
            {
                "xuid": str(row["xuid"]),
                "gamertag": row["gamertag"] or str(row["xuid"]),
                "discord_id": str(row["discord_id"]) if row["discord_id"] else None,
                "tier": tier,
                "branch": row["branch"],
                "unit_designation": row["unit_designation"],
                "rank_name": row["rank_name"],
            }
        )
    return output


def cortana_na_prior_rows(cortana_db: Path) -> list[dict[str, Any]]:
    con = _connect(cortana_db)
    rows = con.execute(
        """
        SELECT
            pgs.xuid,
            COALESCE(pgs.gamertag, x.gamertag, pgs.xuid) AS gamertag,
            COUNT(DISTINCT pgs.game_id) AS na_match_count
        FROM player_game_stats pgs
        JOIN games g
          ON g.id = pgs.game_id
        JOIN events e
          ON e.id = g.event_id
        LEFT JOIN xuids x
          ON x.xuid = pgs.xuid
        WHERE e.outcome = 'N/A'
           OR g.outcome = 'N/A'
           OR pgs.outcome = 'N/A'
        GROUP BY pgs.xuid
        """
    ).fetchall()
    con.close()
    return [dict(row) for row in rows]


def historical_prior_counts(paths: list[Path]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for path in paths:
        if not path.exists():
            continue
        with path.open(newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames or "participant_xuids" not in reader.fieldnames:
                continue
            for row in reader:
                bucket = str(row.get("raid_bucket") or row.get("adjusted_bucket") or "").lower()
                if bucket and "raid" not in bucket:
                    continue
                raw_participants = str(row.get("participant_xuids") or "")
                for xuid in raw_participants.replace(";", ",").split(","):
                    cleaned = xuid.strip()
                    if cleaned:
                        counts[cleaned] += 1
    return counts


def cortana_external_outcome_event_ids(cortana_db: Path) -> list[int]:
    con = _connect(cortana_db)
    rows = con.execute(
        """
        SELECT id
        FROM events
        WHERE category = 'External'
          AND outcome IN ('Win', 'Loss', 'Draw')
        ORDER BY timestamp ASC, id ASC
        """
    ).fetchall()
    con.close()
    return [int(row["id"]) for row in rows]


async def stage_unsc_seeds(cortana_db: Path) -> dict[str, Any]:
    rows = cortana_unsc_seed_rows(cortana_db)
    staged = 0
    tiers: Counter[str] = Counter()
    for row in rows:
        detail = " | ".join(
            part
            for part in [
                row.get("branch"),
                row.get("unit_designation"),
                row.get("rank_name"),
                f"discord:{row.get('discord_id')}" if row.get("discord_id") else None,
            ]
            if part
        )
        await isabel_db.upsert_xuid_celo_seed_override(
            xuid=row["xuid"],
            gamertag=row["gamertag"],
            seed_source="unsc_branch",
            seed_tier=row["tier"],
            seed_detail=detail,
            seed_locked=True,
        )
        staged += 1
        tiers[row["tier"]] += 1
    return {"staged": staged, "tiers": dict(tiers)}


async def stage_prior_evidence(cortana_db: Path, historical_paths: list[Path]) -> dict[str, Any]:
    na_rows = cortana_na_prior_rows(cortana_db)
    for row in na_rows:
        await isabel_db.record_xuid_celo_prior_evidence(
            xuid=str(row["xuid"]),
            gamertag=row.get("gamertag"),
            na_match_count=int(row["na_match_count"] or 0),
        )

    historical_counts = historical_prior_counts(historical_paths)
    for xuid, count in historical_counts.items():
        await isabel_db.record_xuid_celo_prior_evidence(
            xuid=str(xuid),
            historical_match_count=int(count),
        )

    return {
        "cortana_na_xuids": len(na_rows),
        "historical_xuids": len(historical_counts),
        "historical_paths": [str(path) for path in historical_paths if path.exists()],
    }


async def import_cortana_externals(
    cortana_db: Path,
    *,
    isabel_guild_id: str,
    coordinator_id: str,
    max_events: int | None = None,
) -> dict[str, Any]:
    event_ids = cortana_external_outcome_event_ids(cortana_db)
    if max_events is not None:
        event_ids = event_ids[: max(0, int(max_events))]

    imported = 0
    skipped_duplicates = 0
    failed: list[dict[str, str]] = []
    for cortana_event_id in event_ids:
        try:
            result = await cortana_import.import_cortana_event(
                cortana_event_id=int(cortana_event_id),
                isabel_guild_id=str(isabel_guild_id),
                coordinator_id=str(coordinator_id),
                category="Raid",
                cortana_db_path=cortana_db,
            )
            if result.get("status") == "already_synced":
                skipped_duplicates += 1
            else:
                imported += 1
        except ValueError as exc:
            failed.append({"event_id": str(cortana_event_id), "error": str(exc)})
        except Exception as exc:
            failed.append({"event_id": str(cortana_event_id), "error": f"{type(exc).__name__}: {exc}"})
    return {
        "eligible": len(event_ids),
        "imported": imported,
        "skipped_duplicates": skipped_duplicates,
        "failed": failed[:25],
        "failed_count": len(failed),
    }


async def run_migration(args: argparse.Namespace) -> dict[str, Any]:
    source_db = Path(args.isabel_db).resolve()
    backup_dir = Path(args.backup_dir).resolve()
    backup_path = backup_database(source_db, backup_dir)
    target_db = source_db if args.apply else dry_run_database(source_db, backup_dir)

    isabel_db.DB_PATH = str(target_db)
    cortana_db = Path(args.cortana_db).resolve()

    async with aiosqlite.connect(isabel_db.DB_PATH) as live:
        await isabel_db._run_migrations(live)
        await live.commit()

    unsc = await stage_unsc_seeds(cortana_db)
    historical_paths = [Path(path).resolve() for path in args.historical_candidates]
    prior = await stage_prior_evidence(cortana_db, historical_paths)
    imports = await import_cortana_externals(
        cortana_db,
        isabel_guild_id=args.isabel_guild_id,
        coordinator_id=args.coordinator_id,
        max_events=args.max_events,
    )
    rebuild = await isabel_db.rebuild_all_celo()

    report = {
        "mode": "apply" if args.apply else "dry_run",
        "source_db": str(source_db),
        "target_db": str(target_db),
        "backup_db": str(backup_path),
        "cortana_db": str(cortana_db),
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "unsc_seeds": unsc,
        "prior_evidence": prior,
        "cortana_external_imports": imports,
        "rebuild": rebuild,
    }
    report_path = Path(args.report_path).resolve() if args.report_path else (
        backup_dir / f"celo-production-seed-report.{_timestamp()}.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    report["report_path"] = str(report_path)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stage production CELO seeds and replay outcome-bearing raid history.")
    parser.add_argument("--apply", action="store_true", help="Mutate the Isabel DB. Without this, a copied DB is used.")
    parser.add_argument("--isabel-db", default=str(DEFAULT_ISABEL_DB))
    parser.add_argument("--cortana-db", default=str(DEFAULT_CORTANA_DB))
    parser.add_argument("--backup-dir", default=str(DEFAULT_BACKUP_DIR))
    parser.add_argument("--isabel-guild-id", required=True)
    parser.add_argument("--coordinator-id", required=True)
    parser.add_argument("--max-events", type=int)
    parser.add_argument("--report-path")
    parser.add_argument(
        "--historical-candidates",
        action="append",
        default=[str(DEFAULT_HISTORICAL_CANDIDATES)] if DEFAULT_HISTORICAL_CANDIDATES.exists() else [],
        help="Historical raid candidate CSV with participant_xuids. May be passed multiple times.",
    )
    return parser.parse_args()


async def _main() -> None:
    report = await run_migration(parse_args())
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(_main())
