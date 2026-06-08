from __future__ import annotations

from pathlib import Path
from typing import Any
import re

import aiosqlite

from helpers import db as isabel_db


DEFAULT_CORTANA_DB_PATH = Path("/home/Cortana/database/database.db")


def _normalize_isabel_category(category: str | None) -> str:
    value = (category or "Raid").strip()
    return value or "Raid"


def _external_opponent_id(label: str | None) -> str:
    value = (label or "Unregistered Opponent").strip() or "Unregistered Opponent"
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:48]
    return f"external:{slug or 'opponent'}"


async def _find_registered_guild_by_name(name: str | None) -> dict[str, Any] | None:
    if not name:
        return None
    async with aiosqlite.connect(isabel_db.DB_PATH) as isabel:
        await isabel_db._run_migrations(isabel)
        isabel.row_factory = aiosqlite.Row
        cursor = await isabel.execute(
            """
            SELECT guild_id, guild_name
            FROM guild_registry
            WHERE is_active = 1
              AND lower(guild_name) = lower(?)
            LIMIT 1
            """,
            (name.strip(),),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def _fetch_cortana_event(cortana_db_path: str | Path, cortana_event_id: int) -> dict[str, Any] | None:
    async with aiosqlite.connect(str(cortana_db_path)) as cortana:
        cortana.row_factory = aiosqlite.Row
        event_cursor = await cortana.execute(
            """
            SELECT id, guild_id, category, coordinator_id, outcome, opponent, opponent_id, event_number
            FROM events
            WHERE id = ?
            LIMIT 1
            """,
            (int(cortana_event_id),),
        )
        event = await event_cursor.fetchone()
        if not event:
            return None

        games_cursor = await cortana.execute(
            """
            SELECT id, match_id, outcome, start_time, duration, map_name, mode_name
            FROM games
            WHERE event_id = ?
            ORDER BY id ASC
            """,
            (int(cortana_event_id),),
        )
        games = [dict(row) for row in await games_cursor.fetchall()]

        stats_by_game: dict[int, list[dict[str, Any]]] = {}
        medals_by_game: dict[int, list[dict[str, Any]]] = {}
        xuids: dict[str, dict[str, Any]] = {}
        for game in games:
            stats_cursor = await cortana.execute(
                """
                SELECT pgs.xuid, pgs.gamertag, pgs.team, pgs.outcome,
                       pgs.kills, pgs.deaths, pgs.assists, pgs.damage_dealt,
                       x.discord_id
                FROM player_game_stats pgs
                LEFT JOIN xuids x ON x.xuid = pgs.xuid
                WHERE pgs.game_id = ?
                ORDER BY pgs.team ASC, pgs.kills DESC
                """,
                (int(game["id"]),),
            )
            stats_rows = [dict(row) for row in await stats_cursor.fetchall()]
            stats_by_game[int(game["id"])] = stats_rows
            for row in stats_rows:
                if row.get("xuid"):
                    xuids[str(row["xuid"])] = {
                        "xuid": str(row["xuid"]),
                        "gamertag": row.get("gamertag") or str(row["xuid"]),
                        "discord_id": str(row["discord_id"]) if row.get("discord_id") else None,
                    }

            try:
                medals_cursor = await cortana.execute(
                    """
                    SELECT xuid, medal_name_id, medal_name, count, total_personal_score_awarded
                    FROM player_game_medals
                    WHERE game_id = ?
                    """,
                    (int(game["id"]),),
                )
                medals_by_game[int(game["id"])] = [dict(row) for row in await medals_cursor.fetchall()]
            except aiosqlite.Error:
                medals_by_game[int(game["id"])] = []

    return {
        "event": dict(event),
        "games": games,
        "stats_by_game": stats_by_game,
        "medals_by_game": medals_by_game,
        "xuids": list(xuids.values()),
    }


async def import_cortana_event(
    *,
    cortana_event_id: int,
    isabel_guild_id: str,
    isabel_opponent_guild_id: str | None = None,
    opponent_name: str | None = None,
    coordinator_id: str,
    category: str | None = "Raid",
    cortana_db_path: str | Path = DEFAULT_CORTANA_DB_PATH,
) -> dict[str, Any]:
    cortana_payload = await _fetch_cortana_event(cortana_db_path, int(cortana_event_id))
    if not cortana_payload:
        raise ValueError(f"Cortana event {cortana_event_id} was not found.")

    cortana_event = cortana_payload["event"]
    games = list(cortana_payload["games"])
    if not games:
        raise ValueError(f"Cortana event {cortana_event_id} has no logged games.")

    duplicate_matches = await isabel_db.find_existing_matches([str(game["match_id"]) for game in games])
    duplicate_by_match_id = {str(row["match_id"]): dict(row) for row in duplicate_matches}
    new_games = [game for game in games if str(game["match_id"]) not in duplicate_by_match_id]
    duplicate_labels = [
        f"{row['match_id']} -> event {row['event_id']}"
        for row in duplicate_matches[:10]
    ]

    imported_opponent_name = (opponent_name or cortana_event.get("opponent") or "Unregistered Opponent").strip()
    opponent_row = None
    if isabel_opponent_guild_id:
        opponent_row = await isabel_db.get_guild_registration(str(isabel_opponent_guild_id))
    elif imported_opponent_name:
        opponent_row = await _find_registered_guild_by_name(imported_opponent_name)

    resolved_opponent_guild_id = (
        str(opponent_row["guild_id"])
        if opponent_row
        else str(isabel_opponent_guild_id or _external_opponent_id(imported_opponent_name))
    )
    resolved_opponent_name = (
        str(opponent_row["guild_name"])
        if opponent_row
        else imported_opponent_name
    )
    outcome = str(cortana_event.get("outcome") or "N/A")

    async with aiosqlite.connect(isabel_db.DB_PATH) as isabel:
        await isabel_db._run_migrations(isabel)
        for row in cortana_payload["xuids"]:
            await isabel.execute(
                """
                INSERT INTO xuids (xuid, gamertag, discord_id, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(xuid) DO UPDATE SET
                    gamertag = COALESCE(excluded.gamertag, xuids.gamertag),
                    discord_id = COALESCE(excluded.discord_id, xuids.discord_id),
                    updated_at = CURRENT_TIMESTAMP
                """,
                (str(row["xuid"]), row.get("gamertag"), row.get("discord_id")),
            )
        await isabel.commit()

    if not new_games:
        existing_event_ids = sorted({int(row["event_id"]) for row in duplicate_matches if row.get("event_id") is not None})
        return {
            "status": "already_synced",
            "event_id": existing_event_ids[0] if len(existing_event_ids) == 1 else None,
            "existing_event_ids": existing_event_ids,
            "event_number": None,
            "event_code": None,
            "cortana_event_id": int(cortana_event_id),
            "opponent_guild_id": resolved_opponent_guild_id,
            "opponent_name": resolved_opponent_name,
            "opponent_registered": bool(opponent_row),
            "games": 0,
            "stats": 0,
            "medals": 0,
            "duplicate_games": len(duplicate_matches),
            "duplicate_matches": duplicate_labels,
            "official": {
                "event_id": None,
                "processed_games": 0,
                "skipped_already_processed": len(duplicate_matches),
                "skipped_insufficient_linked_teams": 0,
                "adjusted_users": 0,
                "net_changes": {},
            },
            "models": {"event_id": None, "models": {}},
        }

    event_id, event_number, event_code = await isabel_db.log_event(
        guild_id=str(isabel_guild_id),
        category=_normalize_isabel_category(category),
        coordinator_id=str(coordinator_id),
        outcome=outcome,
        opponent_guild_id=resolved_opponent_guild_id,
        opponent_name=resolved_opponent_name,
        notes=(
            f"Imported from Cortana event {int(cortana_event_id)}. "
            f"Opponent registration: {'registered' if opponent_row else 'unregistered'}. "
            f"Skipped duplicate games: {len(duplicate_matches)}."
        ),
    )

    imported_games = 0
    imported_stats = 0
    imported_medals = 0
    for game in new_games:
        game_id = await isabel_db.log_game(
            event_id=event_id,
            match_id=str(game["match_id"]),
            outcome=str(game.get("outcome") or outcome),
            start_time=str(game.get("start_time") or ""),
            duration=str(game.get("duration") or ""),
            map_name=str(game.get("map_name") or "Unknown"),
            mode_name=str(game.get("mode_name") or "Unknown"),
        )
        imported_games += 1

        stats_rows = cortana_payload["stats_by_game"].get(int(game["id"]), [])
        await isabel_db.log_player_game_stats(game_id, stats_rows)
        imported_stats += len(stats_rows)

        medal_rows = cortana_payload["medals_by_game"].get(int(game["id"]), [])
        if medal_rows:
            imported_medals += await isabel_db.log_player_game_medals(game_id, medal_rows)

    official = await isabel_db.apply_celo_for_event(event_id)
    models = await isabel_db.apply_celo_models_for_event(event_id)
    return {
        "status": "partial_import" if duplicate_matches else "imported",
        "event_id": event_id,
        "event_number": event_number,
        "event_code": event_code,
        "cortana_event_id": int(cortana_event_id),
        "opponent_guild_id": resolved_opponent_guild_id,
        "opponent_name": resolved_opponent_name,
        "opponent_registered": bool(opponent_row),
        "games": imported_games,
        "stats": imported_stats,
        "medals": imported_medals,
        "duplicate_games": len(duplicate_matches),
        "duplicate_matches": duplicate_labels,
        "official": official,
        "models": models,
    }
