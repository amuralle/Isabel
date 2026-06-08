from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sqlite3
import sys
from collections import Counter
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from helpers import db as isabel_db


DEFAULT_ISABEL_DB = PROJECT_ROOT / "database" / "database.db"
DEFAULT_CORTANA_DB = Path("/home/Cortana/database/database.db")
DEFAULT_BACKUP_DIR = PROJECT_ROOT / "database" / "backups"


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _backup_database(source: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"{source.stem}.cortana-unsc-roster-sync.{_timestamp()}{source.suffix}"
    shutil.copy2(source, target)
    return target


def _cortana_roster_rows(cortana_db: Path, *, branch_seeded_only: bool) -> list[dict[str, Any]]:
    con = sqlite3.connect(cortana_db)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """
        SELECT
            x.xuid,
            COALESCE(x.gamertag, x.xuid) AS gamertag,
            x.discord_id,
            r.branch,
            r.unit_designation,
            r.rank_name,
            r.deployment_status,
            r.allegiance,
            r.observed_at
        FROM xuids x
        JOIN observed_user_ranks r
          ON r.user_id = x.discord_id
        WHERE x.xuid IS NOT NULL
          AND x.discord_id IS NOT NULL
        ORDER BY x.gamertag COLLATE NOCASE ASC, x.xuid ASC
        """
    ).fetchall()
    con.close()

    deduped: dict[str, dict[str, Any]] = {}
    for row in rows:
        payload = dict(row)
        tier = isabel_db.unsc_branch_seed_tier(payload.get("branch"), payload.get("unit_designation"))
        if branch_seeded_only and not tier:
            continue
        payload["tier"] = tier
        deduped[str(payload["xuid"])] = payload
    return list(deduped.values())


async def _sync_rows(args: argparse.Namespace) -> dict[str, Any]:
    source_db = Path(args.isabel_db).resolve()
    backup_path = _backup_database(source_db, Path(args.backup_dir).resolve()) if args.apply else None
    isabel_db.DB_PATH = str(source_db)

    if args.register_guild:
        if not args.guild_name:
            raise ValueError("--guild-name is required with --register-guild")
        if args.apply:
            await isabel_db.register_guild(args.guild_id, args.guild_name, args.registered_by)

    rows = _cortana_roster_rows(Path(args.cortana_db).resolve(), branch_seeded_only=args.branch_seeded_only)
    tier_counts: Counter[str] = Counter(str(row.get("tier") or "Unmapped") for row in rows)

    synced = 0
    allegiance_set = 0
    samples: list[dict[str, Any]] = []
    if args.apply:
        for row in rows:
            result = await isabel_db.upsert_clan_roster_membership_xuid(
                guild_id=args.guild_id,
                xuid=str(row["xuid"]),
                gamertag=str(row["gamertag"]),
                tier=row.get("tier"),
                registered_by=args.registered_by,
                discord_id=str(row["discord_id"]) if row.get("discord_id") else None,
                set_allegiance_flag=not args.no_allegiance,
            )
            synced += 1
            if result["allegiance_set"]:
                allegiance_set += 1
            if len(samples) < 10:
                samples.append(result)
    else:
        samples = [
            {
                "xuid": str(row["xuid"]),
                "gamertag": str(row["gamertag"]),
                "discord_id": str(row["discord_id"]),
                "tier": row.get("tier"),
            }
            for row in rows[:10]
        ]

    report = {
        "mode": "apply" if args.apply else "dry_run",
        "guild_id": str(args.guild_id),
        "guild_name": args.guild_name,
        "cortana_db": str(Path(args.cortana_db).resolve()),
        "isabel_db": str(source_db),
        "backup_db": str(backup_path) if backup_path else None,
        "eligible_rows": len(rows),
        "synced_rows": synced,
        "allegiances_set": allegiance_set,
        "branch_seeded_only": bool(args.branch_seeded_only),
        "set_allegiance": not args.no_allegiance,
        "tier_counts": dict(tier_counts),
        "samples": samples,
    }
    report_path = Path(args.report_path).resolve() if args.report_path else (
        Path(args.backup_dir).resolve() / f"cortana-unsc-roster-sync-report.{_timestamp()}.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    report["report_path"] = str(report_path)
    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync UNSC roster membership from live Cortana XUID/user-rank tables into Isabel."
    )
    parser.add_argument("--apply", action="store_true", help="Write roster rows. Default is dry-run only.")
    parser.add_argument("--guild-id", required=True, help="Registered Isabel/Discord guild ID for UNSC.")
    parser.add_argument("--guild-name", help="Guild name to register when --register-guild is used.")
    parser.add_argument("--registered-by", required=True, help="Discord ID recorded as the roster sync operator.")
    parser.add_argument("--register-guild", action="store_true", help="Register/reactivate this guild before syncing.")
    parser.add_argument("--no-allegiance", action="store_true", help="Do not set Discord user allegiance to the guild.")
    parser.add_argument(
        "--include-unmapped",
        action="store_true",
        help="Include Cortana users whose branch/unit does not map to Low/Mid/High.",
    )
    parser.add_argument("--isabel-db", default=str(DEFAULT_ISABEL_DB))
    parser.add_argument("--cortana-db", default=str(DEFAULT_CORTANA_DB))
    parser.add_argument("--backup-dir", default=str(DEFAULT_BACKUP_DIR))
    parser.add_argument("--report-path")
    args = parser.parse_args()
    args.branch_seeded_only = not args.include_unmapped
    return args


async def _main() -> None:
    report = await _sync_rows(_parse_args())
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(_main())
