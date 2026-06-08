import os
import json
from datetime import datetime
from typing import Optional
import uuid

import aiosqlite

DB_PATH = f"{os.path.realpath(os.path.dirname(__file__))}/../database/database.db"
DB_SCHEMA_PATH = f"{os.path.realpath(os.path.dirname(__file__))}/../database/schema.sql"
EVENT_PUBLIC_ID_NAMESPACE = uuid.UUID("3c7fe4bd-66ea-4f17-98fe-869f0c3ea1a5")
CELO_SCORE_MIN = 900
CELO_SCORE_MAX = 2500
CELO_SCORE_CENTER = 1500
CELO_RAW_CENTER = 1000.0
CELO_DISPLAY_SCALE = 3.0
CELO_OVERCAP_DISPLAY_SCALE = 0.75
TIER_SCORE_SEEDS = {
    "low": 1100,
    "mid": 1400,
    "mid-high": 1650,
    "midhigh": 1650,
    "mid high": 1650,
    "high": 1900,
}
UNSC_BRANCH_TIER_RULES = (
    ("spartan", "High"),
    ("13th marine battalion", "Low"),
    ("21st space assault battalion", "Mid"),
)

PROFICIENCY_FIELDS = (
    "Marksmanship",
    "Close Quarter Combat",
    "Heavy Weapons",
    "Support",
    "Armor",
    "Survival",
)

PROFICIENCY_MEDALS = {
    "Marksman": ("Marksmanship", "major"),
    "Sharpshooter": ("Marksmanship", "major"),
    "Rifleman": ("Marksmanship", "major"),
    "Perfect": ("Marksmanship", "minor"),
    "Snipe": ("Marksmanship", "minor"),
    "No Scope": ("Marksmanship", "minor"),
    "Boxer": ("Close Quarter Combat", "major"),
    "Scattergunner": ("Close Quarter Combat", "major"),
    "Breacher": ("Close Quarter Combat", "major"),
    "Beat Down": ("Close Quarter Combat", "minor"),
    "Back Smack": ("Close Quarter Combat", "minor"),
    "Spotter": ("Support", "major"),
    "Wingman": ("Support", "major"),
    "Bodyguard": ("Support", "major"),
    "Guardian Angel": ("Support", "minor"),
    "Shot Caller": ("Support", "minor"),
    "Bomber": ("Heavy Weapons", "major"),
    "Grenadier": ("Heavy Weapons", "major"),
    "Saboteur": ("Heavy Weapons", "major"),
    "Fire & Forget": ("Heavy Weapons", "minor"),
    "Hail Mary": ("Heavy Weapons", "minor"),
    "Driver": ("Armor", "major"),
    "Tanker": ("Armor", "major"),
    "Pilot": ("Armor", "major"),
    "Wheelman": ("Armor", "major"),
    "Gunner": ("Armor", "major"),
    "Splatter": ("Armor", "minor"),
    "Rampage": ("Survival", "major"),
    "Nightmare": ("Survival", "major"),
    "Boogeyman": ("Survival", "major"),
    "Grim Reaper": ("Survival", "major"),
    "Demon": ("Survival", "major"),
    "Killing Spree": ("Survival", "minor"),
    "Killing Frenzy": ("Survival", "minor"),
    "Running Riot": ("Survival", "minor"),
}

SURVIVAL_CUMULATIVE_MEDALS = (
    "Killing Spree",
    "Killing Frenzy",
    "Running Riot",
    "Rampage",
    "Nightmare",
    "Boogeyman",
    "Grim Reaper",
    "Demon",
)

PROFICIENCY_MAJOR_WEIGHT = 5
PROFICIENCY_MINOR_WEIGHT = 1


def celo_score(raw_rating: float | int | str | None) -> int:
    raw = float(raw_rating or CELO_RAW_CENTER)
    score = CELO_SCORE_CENTER + ((raw - CELO_RAW_CENTER) * CELO_DISPLAY_SCALE)
    if score > CELO_SCORE_MAX:
        overcap_raw = raw - raw_rating_from_score(CELO_SCORE_MAX)
        score = CELO_SCORE_MAX + (max(0.0, overcap_raw) * CELO_OVERCAP_DISPLAY_SCALE)
    return int(round(max(CELO_SCORE_MIN, score)))


def raw_rating_from_score(score: float | int | str | None) -> float:
    public_score = max(CELO_SCORE_MIN, float(score or CELO_SCORE_CENTER))
    if public_score > CELO_SCORE_MAX:
        return raw_rating_from_score(CELO_SCORE_MAX) + (
            (public_score - CELO_SCORE_MAX) / CELO_OVERCAP_DISPLAY_SCALE
        )
    return CELO_RAW_CENTER + ((public_score - CELO_SCORE_CENTER) / CELO_DISPLAY_SCALE)


def normalize_tier(tier: str | None) -> str:
    key = (tier or "").strip().lower().replace("_", "-")
    key = " ".join(key.split())
    if key in {"low", "mid", "high"}:
        return key.title()
    if key in {"mid-high", "midhigh", "mid high"}:
        return "Mid-High"
    raise ValueError("Tier must be one of: Low, Mid, Mid-High, High.")


def tier_seed_rating(tier: str | None) -> float:
    normalized = normalize_tier(tier)
    score = TIER_SCORE_SEEDS[normalized.lower()]
    return raw_rating_from_score(score)


def unsc_branch_seed_tier(branch: str | None, unit_designation: str | None = None) -> str | None:
    combined = " ".join(part for part in [branch, unit_designation] if part).strip().lower()
    if not combined:
        return None
    for needle, tier in UNSC_BRANCH_TIER_RULES:
        if needle in combined:
            return tier
    return None


def _normalize_event_category(category: str) -> str:
    key = (category or "").strip().upper()
    if key in {"4V4", "TOURNAMENT", "TOURNAMENTS", "SCRIM", "SCRIMS", "ARENA"}:
        return "4v4"
    if key in {"BLITZ"}:
        return "Blitz"
    if key in {"RAID", "RAIDS"}:
        return "Raid"
    return (category or "").strip() or "Uncategorized"


def _build_event_public_id(event_id: int | None = None) -> str:
    if event_id is None:
        return f"EVT-{uuid.uuid4()}"
    stable = uuid.uuid5(EVENT_PUBLIC_ID_NAMESPACE, f"isabel-event-{int(event_id)}")
    return f"EVT-{stable}"


def classify_proficiency_medal(medal_name: str | None, count: int = 1) -> dict:
    medal_name = (medal_name or "").strip()
    mapping = PROFICIENCY_MEDALS.get(medal_name)
    if not mapping:
        return {
            "field": None,
            "weight_class": None,
            "weight": 0,
            "rating_delta": 0,
        }

    field, weight_class = mapping
    weight = PROFICIENCY_MAJOR_WEIGHT if weight_class == "major" else PROFICIENCY_MINOR_WEIGHT
    normalized_count = max(0, int(count or 0))
    return {
        "field": field,
        "weight_class": weight_class,
        "weight": weight,
        "rating_delta": weight * normalized_count,
    }


def _effective_survival_counts(counts_by_medal: dict[str, int]) -> dict[str, int]:
    effective_counts: dict[str, int] = {}
    for index, medal_name in enumerate(SURVIVAL_CUMULATIVE_MEDALS):
        raw_count = max(0, int(counts_by_medal.get(medal_name, 0) or 0))
        if index + 1 < len(SURVIVAL_CUMULATIVE_MEDALS):
            next_count = max(0, int(counts_by_medal.get(SURVIVAL_CUMULATIVE_MEDALS[index + 1], 0) or 0))
            effective_counts[medal_name] = max(0, raw_count - next_count)
        else:
            effective_counts[medal_name] = raw_count
    return effective_counts


def _apply_effective_proficiency_counts(medal_rows: list[dict]) -> list[dict]:
    grouped_survival_counts: dict[str, dict[str, int]] = {}
    for row in medal_rows:
        medal_name = str(row.get("medal_name") or row.get("medal_name_id") or "")
        if medal_name not in SURVIVAL_CUMULATIVE_MEDALS:
            continue
        group_key = f"{row.get('game_id', '')}:{row.get('xuid', '')}"
        grouped_survival_counts.setdefault(group_key, {})[medal_name] = int(row.get("count", 0) or 0)

    effective_by_group = {
        group_key: _effective_survival_counts(counts)
        for group_key, counts in grouped_survival_counts.items()
    }

    adjusted_rows: list[dict] = []
    for row in medal_rows:
        adjusted = dict(row)
        medal_name = str(adjusted.get("medal_name") or adjusted.get("medal_name_id") or "")
        raw_count = int(adjusted.get("count", 0) or 0)
        if medal_name in SURVIVAL_CUMULATIVE_MEDALS:
            group_key = f"{adjusted.get('game_id', '')}:{adjusted.get('xuid', '')}"
            adjusted["effective_count"] = effective_by_group.get(group_key, {}).get(medal_name, raw_count)
        else:
            adjusted["effective_count"] = raw_count
        adjusted_rows.append(adjusted)
    return adjusted_rows


async def _column_exists(db: aiosqlite.Connection, table: str, column: str) -> bool:
    cursor = await db.execute(f"PRAGMA table_info({table})")
    rows = await cursor.fetchall()
    return any(str(r[1]) == column for r in rows)


async def _run_migrations(db: aiosqlite.Connection) -> None:
    if not await _column_exists(db, "events", "opponent_guild_id"):
        await db.execute("ALTER TABLE events ADD COLUMN opponent_guild_id TEXT")
    if not await _column_exists(db, "events", "public_id"):
        await db.execute("ALTER TABLE events ADD COLUMN public_id TEXT")

    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS event_channels (
            guild_id TEXT PRIMARY KEY,
            channel_id TEXT NOT NULL,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (guild_id) REFERENCES guild_registry(guild_id)
        )
        """
    )

    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS event_reporter_roles (
            guild_id TEXT NOT NULL,
            role_id TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (guild_id, role_id),
            FOREIGN KEY (guild_id) REFERENCES guild_registry(guild_id)
        )
        """
    )

    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS event_report_posts (
            event_id INTEGER NOT NULL,
            guild_id TEXT NOT NULL,
            forum_channel_id TEXT NOT NULL,
            thread_id TEXT NOT NULL,
            summary_message_id TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (event_id, guild_id),
            FOREIGN KEY (event_id) REFERENCES events(id),
            FOREIGN KEY (guild_id) REFERENCES guild_registry(guild_id)
        )
        """
    )

    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS event_contests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL,
            source_guild_id TEXT NOT NULL,
            report_thread_id TEXT,
            report_message_id TEXT,
            opened_by_discord_id TEXT NOT NULL,
            details TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            resolved_at DATETIME,
            resolved_by_discord_id TEXT,
            FOREIGN KEY (event_id) REFERENCES events(id),
            FOREIGN KEY (source_guild_id) REFERENCES guild_registry(guild_id)
        )
        """
    )

    await db.execute("CREATE INDEX IF NOT EXISTS idx_event_channels_guild ON event_channels(guild_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_event_reporter_roles_guild ON event_reporter_roles(guild_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_event_report_posts_event ON event_report_posts(event_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_event_report_posts_guild ON event_report_posts(guild_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_event_contests_event ON event_contests(event_id, created_at DESC)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_event_contests_status ON event_contests(status, created_at DESC)")
    await db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_events_public_id ON events(public_id)")
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS player_game_medals (
            game_id INTEGER NOT NULL,
            xuid TEXT NOT NULL,
            medal_name_id INTEGER NOT NULL,
            medal_name TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            total_personal_score_awarded INTEGER NOT NULL DEFAULT 0,
            proficiency_field TEXT,
            weight_class TEXT,
            weight INTEGER NOT NULL DEFAULT 0,
            rating_delta INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (game_id, xuid, medal_name_id),
            FOREIGN KEY (game_id) REFERENCES games(id)
        )
        """
    )
    await db.execute("CREATE INDEX IF NOT EXISTS idx_player_game_medals_game ON player_game_medals(game_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_player_game_medals_xuid ON player_game_medals(xuid)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_player_game_medals_field ON player_game_medals(proficiency_field)")

    if not await _column_exists(db, "celo_ledger", "base_delta"):
        await db.execute("ALTER TABLE celo_ledger ADD COLUMN base_delta REAL")
    if not await _column_exists(db, "celo_ledger", "performance_score"):
        await db.execute("ALTER TABLE celo_ledger ADD COLUMN performance_score REAL")
    if not await _column_exists(db, "celo_ledger", "performance_multiplier"):
        await db.execute("ALTER TABLE celo_ledger ADD COLUMN performance_multiplier REAL")

    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS celo_model_profiles (
            model_key TEXT NOT NULL,
            discord_id TEXT NOT NULL,
            rating REAL NOT NULL DEFAULT 1000.0,
            deviation REAL NOT NULL DEFAULT 350.0,
            volatility REAL NOT NULL DEFAULT 0.06,
            games_played INTEGER NOT NULL DEFAULT 0,
            wins INTEGER NOT NULL DEFAULT 0,
            losses INTEGER NOT NULL DEFAULT 0,
            draws INTEGER NOT NULL DEFAULT 0,
            last_played_at DATETIME,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (model_key, discord_id),
            FOREIGN KEY (discord_id) REFERENCES users(discord_id)
        )
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS celo_model_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_key TEXT NOT NULL,
            event_id INTEGER,
            game_id INTEGER,
            discord_id TEXT NOT NULL,
            old_rating REAL NOT NULL,
            new_rating REAL NOT NULL,
            delta REAL NOT NULL,
            expected_score REAL NOT NULL,
            actual_score REAL NOT NULL,
            k_factor REAL NOT NULL,
            base_delta REAL,
            performance_score REAL,
            performance_multiplier REAL,
            metadata_json TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(model_key, game_id, discord_id),
            FOREIGN KEY (discord_id) REFERENCES users(discord_id)
        )
        """
    )
    await db.execute("CREATE INDEX IF NOT EXISTS idx_celo_model_profiles_rating ON celo_model_profiles(model_key, rating DESC)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_celo_model_ledger_user ON celo_model_ledger(model_key, discord_id, created_at DESC)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_celo_model_ledger_event ON celo_model_ledger(model_key, event_id)")

    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS xuid_celo_profiles (
            xuid TEXT PRIMARY KEY,
            gamertag TEXT NOT NULL,
            rating REAL NOT NULL DEFAULT 1000.0,
            deviation REAL NOT NULL DEFAULT 350.0,
            volatility REAL NOT NULL DEFAULT 0.06,
            games_played INTEGER NOT NULL DEFAULT 0,
            wins INTEGER NOT NULL DEFAULT 0,
            losses INTEGER NOT NULL DEFAULT 0,
            draws INTEGER NOT NULL DEFAULT 0,
            performance_pressure REAL NOT NULL DEFAULT 0.0,
            seed_source TEXT,
            seed_tier TEXT,
            seed_rating REAL,
            seed_detail TEXT,
            seed_locked INTEGER NOT NULL DEFAULT 0,
            prior_historical_match_count INTEGER NOT NULL DEFAULT 0,
            prior_na_match_count INTEGER NOT NULL DEFAULT 0,
            prior_performance_score REAL,
            last_played_at DATETIME,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS xuid_celo_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER,
            game_id INTEGER,
            xuid TEXT NOT NULL,
            gamertag TEXT NOT NULL,
            old_rating REAL NOT NULL,
            new_rating REAL NOT NULL,
            delta REAL NOT NULL,
            expected_score REAL NOT NULL,
            actual_score REAL NOT NULL,
            k_factor REAL NOT NULL,
            base_delta REAL,
            performance_score REAL,
            performance_multiplier REAL,
            pressure_before REAL,
            pressure_delta REAL,
            pressure_applied REAL,
            pressure_after REAL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(game_id, xuid)
        )
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS xuid_celo_model_profiles (
            model_key TEXT NOT NULL,
            xuid TEXT NOT NULL,
            gamertag TEXT NOT NULL,
            rating REAL NOT NULL DEFAULT 1000.0,
            deviation REAL NOT NULL DEFAULT 350.0,
            volatility REAL NOT NULL DEFAULT 0.06,
            games_played INTEGER NOT NULL DEFAULT 0,
            wins INTEGER NOT NULL DEFAULT 0,
            losses INTEGER NOT NULL DEFAULT 0,
            draws INTEGER NOT NULL DEFAULT 0,
            performance_pressure REAL NOT NULL DEFAULT 0.0,
            last_played_at DATETIME,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (model_key, xuid)
        )
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS xuid_celo_model_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_key TEXT NOT NULL,
            event_id INTEGER,
            game_id INTEGER,
            xuid TEXT NOT NULL,
            gamertag TEXT NOT NULL,
            old_rating REAL NOT NULL,
            new_rating REAL NOT NULL,
            delta REAL NOT NULL,
            expected_score REAL NOT NULL,
            actual_score REAL NOT NULL,
            k_factor REAL NOT NULL,
            base_delta REAL,
            performance_score REAL,
            performance_multiplier REAL,
            metadata_json TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(model_key, game_id, xuid)
        )
        """
    )
    await db.execute("CREATE INDEX IF NOT EXISTS idx_xuid_celo_profiles_rating ON xuid_celo_profiles(rating DESC)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_xuid_celo_ledger_event ON xuid_celo_ledger(event_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_xuid_celo_ledger_xuid ON xuid_celo_ledger(xuid, created_at DESC)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_xuid_celo_model_profiles_rating ON xuid_celo_model_profiles(model_key, rating DESC)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_xuid_celo_model_ledger_event ON xuid_celo_model_ledger(model_key, event_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_xuid_celo_model_ledger_xuid ON xuid_celo_model_ledger(model_key, xuid, created_at DESC)")
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS xuid_celo_seed_overrides (
            xuid TEXT PRIMARY KEY,
            gamertag TEXT,
            seed_source TEXT NOT NULL,
            seed_tier TEXT,
            seed_rating REAL,
            seed_detail TEXT,
            seed_locked INTEGER NOT NULL DEFAULT 1,
            prior_historical_match_count INTEGER NOT NULL DEFAULT 0,
            prior_na_match_count INTEGER NOT NULL DEFAULT 0,
            prior_performance_score REAL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    await db.execute("CREATE INDEX IF NOT EXISTS idx_xuid_celo_seed_overrides_source ON xuid_celo_seed_overrides(seed_source)")
    if not await _column_exists(db, "xuid_celo_profiles", "performance_pressure"):
        await db.execute("ALTER TABLE xuid_celo_profiles ADD COLUMN performance_pressure REAL NOT NULL DEFAULT 0.0")
    if not await _column_exists(db, "xuid_celo_profiles", "seed_source"):
        await db.execute("ALTER TABLE xuid_celo_profiles ADD COLUMN seed_source TEXT")
    if not await _column_exists(db, "xuid_celo_profiles", "seed_tier"):
        await db.execute("ALTER TABLE xuid_celo_profiles ADD COLUMN seed_tier TEXT")
    if not await _column_exists(db, "xuid_celo_profiles", "seed_rating"):
        await db.execute("ALTER TABLE xuid_celo_profiles ADD COLUMN seed_rating REAL")
    if not await _column_exists(db, "xuid_celo_profiles", "seed_detail"):
        await db.execute("ALTER TABLE xuid_celo_profiles ADD COLUMN seed_detail TEXT")
    if not await _column_exists(db, "xuid_celo_profiles", "seed_locked"):
        await db.execute("ALTER TABLE xuid_celo_profiles ADD COLUMN seed_locked INTEGER NOT NULL DEFAULT 0")
    if not await _column_exists(db, "xuid_celo_profiles", "prior_historical_match_count"):
        await db.execute("ALTER TABLE xuid_celo_profiles ADD COLUMN prior_historical_match_count INTEGER NOT NULL DEFAULT 0")
    if not await _column_exists(db, "xuid_celo_profiles", "prior_na_match_count"):
        await db.execute("ALTER TABLE xuid_celo_profiles ADD COLUMN prior_na_match_count INTEGER NOT NULL DEFAULT 0")
    if not await _column_exists(db, "xuid_celo_profiles", "prior_performance_score"):
        await db.execute("ALTER TABLE xuid_celo_profiles ADD COLUMN prior_performance_score REAL")
    if not await _column_exists(db, "xuid_celo_ledger", "pressure_before"):
        await db.execute("ALTER TABLE xuid_celo_ledger ADD COLUMN pressure_before REAL")
    if not await _column_exists(db, "xuid_celo_ledger", "pressure_delta"):
        await db.execute("ALTER TABLE xuid_celo_ledger ADD COLUMN pressure_delta REAL")
    if not await _column_exists(db, "xuid_celo_ledger", "pressure_applied"):
        await db.execute("ALTER TABLE xuid_celo_ledger ADD COLUMN pressure_applied REAL")
    if not await _column_exists(db, "xuid_celo_ledger", "pressure_after"):
        await db.execute("ALTER TABLE xuid_celo_ledger ADD COLUMN pressure_after REAL")
    if not await _column_exists(db, "xuid_celo_model_profiles", "performance_pressure"):
        await db.execute("ALTER TABLE xuid_celo_model_profiles ADD COLUMN performance_pressure REAL NOT NULL DEFAULT 0.0")

    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS clan_roster_xuids (
            guild_id TEXT NOT NULL,
            xuid TEXT NOT NULL,
            gamertag TEXT NOT NULL,
            tier TEXT,
            registered_by TEXT NOT NULL,
            discord_id TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (guild_id, xuid),
            FOREIGN KEY (guild_id) REFERENCES guild_registry(guild_id)
        )
        """
    )
    await db.execute("CREATE INDEX IF NOT EXISTS idx_clan_roster_guild ON clan_roster_xuids(guild_id, gamertag)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_clan_roster_xuid ON clan_roster_xuids(xuid)")

    # Normalize legacy event categories from older Isabel/Cortana schemas.
    await db.execute(
        """
        UPDATE events
        SET category = '4v4'
        WHERE UPPER(TRIM(category)) IN ('4V4', 'TOURNAMENT', 'TOURNAMENTS', 'SCRIM', 'SCRIMS', 'ARENA')
        """
    )
    await db.execute(
        """
        UPDATE events
        SET category = 'Blitz'
        WHERE UPPER(TRIM(category)) = 'BLITZ'
        """
    )
    await db.execute(
        """
        UPDATE events
        SET category = 'Raid'
        WHERE UPPER(TRIM(category)) IN ('RAID', 'RAIDS')
        """
    )

    cursor = await db.execute(
        """
        SELECT id
        FROM events
        WHERE public_id IS NULL OR TRIM(public_id) = ''
        """
    )
    rows = await cursor.fetchall()
    for row in rows:
        event_id = int(row[0])
        await db.execute(
            "UPDATE events SET public_id = ? WHERE id = ?",
            (_build_event_public_id(event_id), event_id),
        )


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        with open(DB_SCHEMA_PATH, "r", encoding="utf-8") as schema_file:
            await db.executescript(schema_file.read())
        await _run_migrations(db)
        await db.commit()


async def ensure_user(discord_id: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (discord_id) VALUES (?)",
            (str(discord_id),),
        )
        await db.commit()


async def register_guild(guild_id: str, guild_name: str, registered_by: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO guild_registry (guild_id, guild_name, registered_by, is_active, updated_at)
            VALUES (?, ?, ?, 1, CURRENT_TIMESTAMP)
            ON CONFLICT(guild_id) DO UPDATE SET
                guild_name = excluded.guild_name,
                registered_by = excluded.registered_by,
                is_active = 1,
                updated_at = CURRENT_TIMESTAMP
            """,
            (str(guild_id), guild_name.strip(), str(registered_by)),
        )
        await _run_migrations(db)
        await db.commit()


async def unregister_guild(guild_id: str, requested_by: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        await _run_migrations(db)
        cursor = await db.execute(
            """
            UPDATE guild_registry
            SET is_active = 0,
                registered_by = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE guild_id = ?
            """,
            (str(requested_by), str(guild_id)),
        )
        await db.execute("DELETE FROM event_channels WHERE guild_id = ?", (str(guild_id),))
        await db.execute("DELETE FROM event_reporter_roles WHERE guild_id = ?", (str(guild_id),))
        await db.commit()
        return cursor.rowcount > 0


async def get_guild_registration(guild_id: str) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT guild_id, guild_name, registered_by, created_at, updated_at, is_active
            FROM guild_registry
            WHERE guild_id = ?
            """,
            (str(guild_id),),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def is_registered_guild(guild_id: str) -> bool:
    row = await get_guild_registration(str(guild_id))
    return bool(row and int(row.get("is_active") or 0) == 1)


async def list_registered_guilds() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT guild_id, guild_name, created_at, updated_at
            FROM guild_registry
            WHERE is_active = 1
            ORDER BY guild_name ASC
            """
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def set_event_channel(guild_id: str, channel_id: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await _run_migrations(db)
        await db.execute(
            """
            INSERT INTO event_channels (guild_id, channel_id, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(guild_id) DO UPDATE SET
                channel_id = excluded.channel_id,
                updated_at = CURRENT_TIMESTAMP
            """,
            (str(guild_id), str(channel_id)),
        )
        await db.commit()


async def get_event_channel(guild_id: str) -> Optional[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        await _run_migrations(db)
        cursor = await db.execute(
            "SELECT channel_id FROM event_channels WHERE guild_id = ?",
            (str(guild_id),),
        )
        row = await cursor.fetchone()
        return str(row[0]) if row else None


async def upsert_event_report_post(
    event_id: int,
    guild_id: str,
    forum_channel_id: str,
    thread_id: str,
    summary_message_id: str,
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await _run_migrations(db)
        await db.execute(
            """
            INSERT INTO event_report_posts (
                event_id, guild_id, forum_channel_id, thread_id, summary_message_id, updated_at
            )
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(event_id, guild_id) DO UPDATE SET
                forum_channel_id = excluded.forum_channel_id,
                thread_id = excluded.thread_id,
                summary_message_id = excluded.summary_message_id,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                int(event_id),
                str(guild_id),
                str(forum_channel_id),
                str(thread_id),
                str(summary_message_id),
            ),
        )
        await db.commit()


async def get_event_report_posts(event_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        await _run_migrations(db)
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT event_id, guild_id, forum_channel_id, thread_id, summary_message_id, created_at, updated_at
            FROM event_report_posts
            WHERE event_id = ?
            ORDER BY guild_id ASC
            """,
            (int(event_id),),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def create_event_contest(
    event_id: int,
    source_guild_id: str,
    opened_by_discord_id: str,
    report_thread_id: str | None = None,
    report_message_id: str | None = None,
    details: str | None = None,
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        await _run_migrations(db)
        cursor = await db.execute(
            """
            INSERT INTO event_contests (
                event_id, source_guild_id, report_thread_id, report_message_id, opened_by_discord_id, details, status
            )
            VALUES (?, ?, ?, ?, ?, ?, 'open')
            """,
            (
                int(event_id),
                str(source_guild_id),
                str(report_thread_id) if report_thread_id else None,
                str(report_message_id) if report_message_id else None,
                str(opened_by_discord_id),
                (details or "").strip(),
            ),
        )
        contest_id = int(cursor.lastrowid)
        await db.commit()
        return contest_id


async def get_open_event_contest(event_id: int, source_guild_id: str) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        await _run_migrations(db)
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT id, event_id, source_guild_id, report_thread_id, report_message_id,
                   opened_by_discord_id, details, status, created_at
            FROM event_contests
            WHERE event_id = ? AND source_guild_id = ? AND status = 'open'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (int(event_id), str(source_guild_id)),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_event_contest(contest_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        await _run_migrations(db)
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT id, event_id, source_guild_id, report_thread_id, report_message_id,
                   opened_by_discord_id, details, status, created_at, resolved_at, resolved_by_discord_id
            FROM event_contests
            WHERE id = ?
            LIMIT 1
            """,
            (int(contest_id),),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def resolve_event_contest(contest_id: int, resolved_by_discord_id: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        await _run_migrations(db)
        cursor = await db.execute(
            """
            UPDATE event_contests
            SET status = 'resolved',
                resolved_at = CURRENT_TIMESTAMP,
                resolved_by_discord_id = ?
            WHERE id = ? AND status != 'resolved'
            """,
            (str(resolved_by_discord_id), int(contest_id)),
        )
        await db.commit()
        return cursor.rowcount > 0


def _inverse_display_outcome(outcome: str | None) -> str:
    normalized = (outcome or "").strip().lower()
    if normalized == "win":
        return "Loss"
    if normalized == "loss":
        return "Win"
    return outcome or "N/A"


async def revise_event_outcome(event_id: int, mode: str) -> dict:
    normalized_mode = (mode or "").strip().lower().replace("-", "_")
    if normalized_mode not in {"swap_winner", "declare_tie"}:
        raise ValueError("mode must be swap_winner or declare_tie")

    async with aiosqlite.connect(DB_PATH) as db:
        await _run_migrations(db)
        db.row_factory = aiosqlite.Row

        event_cursor = await db.execute(
            "SELECT id, outcome FROM events WHERE id = ? LIMIT 1",
            (int(event_id),),
        )
        event = await event_cursor.fetchone()
        if not event:
            raise ValueError(f"Event {event_id} not found")

        old_outcome = str(event["outcome"] or "N/A")
        new_outcome = "Draw" if normalized_mode == "declare_tie" else _inverse_display_outcome(old_outcome)

        official_cursor = await db.execute(
            """
            SELECT
                xuid,
                COALESCE(SUM(delta), 0) AS rating_delta,
                COUNT(*) AS games,
                SUM(CASE WHEN actual_score = 1.0 THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN actual_score = 0.0 THEN 1 ELSE 0 END) AS losses,
                SUM(CASE WHEN actual_score = 0.5 THEN 1 ELSE 0 END) AS draws,
                (
                    SELECT pressure_before
                    FROM xuid_celo_ledger l2
                    WHERE l2.event_id = xuid_celo_ledger.event_id
                      AND l2.xuid = xuid_celo_ledger.xuid
                      AND l2.pressure_before IS NOT NULL
                    ORDER BY l2.id ASC
                    LIMIT 1
                ) AS restore_pressure
            FROM xuid_celo_ledger
            WHERE event_id = ?
            GROUP BY xuid
            """,
            (int(event_id),),
        )
        official_rows = await official_cursor.fetchall()
        for row in official_rows:
            await db.execute(
                """
                UPDATE xuid_celo_profiles
                SET rating = MAX(100.0, rating - ?),
                    games_played = MAX(0, games_played - ?),
                    wins = MAX(0, wins - ?),
                    losses = MAX(0, losses - ?),
                    draws = MAX(0, draws - ?),
                    performance_pressure = COALESCE(?, performance_pressure),
                    updated_at = CURRENT_TIMESTAMP
                WHERE xuid = ?
                """,
                (
                    float(row["rating_delta"] or 0),
                    int(row["games"] or 0),
                    int(row["wins"] or 0),
                    int(row["losses"] or 0),
                    int(row["draws"] or 0),
                    float(row["restore_pressure"]) if row["restore_pressure"] is not None else None,
                    str(row["xuid"]),
                ),
            )

        model_cursor = await db.execute(
            """
            SELECT
                model_key,
                xuid,
                COALESCE(SUM(delta), 0) AS rating_delta,
                COUNT(*) AS games,
                SUM(CASE WHEN actual_score = 1.0 THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN actual_score = 0.0 THEN 1 ELSE 0 END) AS losses,
                SUM(CASE WHEN actual_score = 0.5 THEN 1 ELSE 0 END) AS draws
            FROM xuid_celo_model_ledger
            WHERE event_id = ?
            GROUP BY model_key, xuid
            """,
            (int(event_id),),
        )
        model_rows = await model_cursor.fetchall()
        for row in model_rows:
            await db.execute(
                """
                UPDATE xuid_celo_model_profiles
                SET rating = MAX(100.0, rating - ?),
                    games_played = MAX(0, games_played - ?),
                    wins = MAX(0, wins - ?),
                    losses = MAX(0, losses - ?),
                    draws = MAX(0, draws - ?),
                    updated_at = CURRENT_TIMESTAMP
                WHERE model_key = ? AND xuid = ?
                """,
                (
                    float(row["rating_delta"] or 0),
                    int(row["games"] or 0),
                    int(row["wins"] or 0),
                    int(row["losses"] or 0),
                    int(row["draws"] or 0),
                    str(row["model_key"]),
                    str(row["xuid"]),
                ),
            )

        await db.execute("DELETE FROM xuid_celo_ledger WHERE event_id = ?", (int(event_id),))
        await db.execute("DELETE FROM xuid_celo_model_ledger WHERE event_id = ?", (int(event_id),))
        await db.execute("DELETE FROM celo_ledger WHERE event_id = ?", (int(event_id),))
        await db.execute("DELETE FROM celo_model_ledger WHERE event_id = ?", (int(event_id),))

        await db.execute(
            "UPDATE events SET outcome = ?, notes = TRIM(COALESCE(notes, '') || ?) WHERE id = ?",
            (
                new_outcome,
                f"\nContest revision: {old_outcome} -> {new_outcome} ({normalized_mode})",
                int(event_id),
            ),
        )
        await db.execute("UPDATE games SET outcome = ? WHERE event_id = ?", (new_outcome, int(event_id)))

        if normalized_mode == "declare_tie":
            await db.execute(
                """
                UPDATE player_game_stats
                SET outcome = 'DRAW'
                WHERE game_id IN (SELECT id FROM games WHERE event_id = ?)
                """,
                (int(event_id),),
            )
        else:
            await db.execute(
                """
                UPDATE player_game_stats
                SET outcome = CASE
                    WHEN UPPER(outcome) IN ('WIN', 'W') THEN 'LOSS'
                    WHEN UPPER(outcome) IN ('LOSS', 'L', 'DID_NOT_FINISH') THEN 'WIN'
                    ELSE outcome
                END
                WHERE game_id IN (SELECT id FROM games WHERE event_id = ?)
                """,
                (int(event_id),),
            )

        await db.commit()

    return {
        "event_id": int(event_id),
        "mode": normalized_mode,
        "old_outcome": old_outcome,
        "new_outcome": new_outcome,
        "official_profiles_reverted": len(official_rows),
        "model_profiles_reverted": len(model_rows),
    }


async def add_event_reporter_role(guild_id: str, role_id: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await _run_migrations(db)
        await db.execute(
            """
            INSERT OR IGNORE INTO event_reporter_roles (guild_id, role_id)
            VALUES (?, ?)
            """,
            (str(guild_id), str(role_id)),
        )
        await db.commit()


async def remove_event_reporter_role(guild_id: str, role_id: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        await _run_migrations(db)
        cursor = await db.execute(
            "DELETE FROM event_reporter_roles WHERE guild_id = ? AND role_id = ?",
            (str(guild_id), str(role_id)),
        )
        await db.commit()
        return cursor.rowcount > 0


async def list_event_reporter_roles(guild_id: str) -> list[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        await _run_migrations(db)
        cursor = await db.execute(
            "SELECT role_id FROM event_reporter_roles WHERE guild_id = ? ORDER BY created_at ASC",
            (str(guild_id),),
        )
        rows = await cursor.fetchall()
        return [str(r[0]) for r in rows]


async def can_user_report_events(guild_id: str, user_id: str, role_ids: list[str], is_admin: bool) -> bool:
    if is_admin:
        return True
    allowed = set(await list_event_reporter_roles(str(guild_id)))
    if not allowed:
        return False
    return any(str(role_id) in allowed for role_id in role_ids)


async def set_user_allegiance(discord_id: str, guild_id: str, primary: bool = False) -> bool:
    if not await is_registered_guild(str(guild_id)):
        return False

    await ensure_user(discord_id)
    async with aiosqlite.connect(DB_PATH) as db:
        # Isabel enforces a single active allegiance per user.
        # Replace any prior allegiance with the new guild.
        await db.execute(
            "DELETE FROM user_allegiances WHERE discord_id = ?",
            (str(discord_id),),
        )

        await db.execute(
            """
            INSERT INTO user_allegiances (discord_id, guild_id, is_primary)
            VALUES (?, ?, ?)
            """,
            (str(discord_id), str(guild_id), 1),
        )
        await db.commit()
    return True


async def list_user_allegiances(discord_id: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT ua.guild_id, ua.is_primary, ua.created_at, gr.guild_name
            FROM user_allegiances ua
            LEFT JOIN guild_registry gr ON gr.guild_id = ua.guild_id
            WHERE ua.discord_id = ? AND COALESCE(gr.is_active, 0) = 1
            ORDER BY ua.is_primary DESC, ua.created_at ASC
            LIMIT 1
            """,
            (str(discord_id),),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def upsert_xuid(xuid: str, gamertag: str, discord_id: str) -> None:
    await ensure_user(discord_id)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO xuids (xuid, gamertag, discord_id, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(xuid) DO UPDATE SET
                gamertag = excluded.gamertag,
                discord_id = excluded.discord_id,
                updated_at = CURRENT_TIMESTAMP
            """,
            (str(xuid), str(gamertag), str(discord_id)),
        )
        await db.commit()


async def upsert_xuid_identity(xuid: str, gamertag: str, discord_id: str | None = None) -> None:
    if discord_id:
        await ensure_user(str(discord_id))
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO xuids (xuid, gamertag, discord_id, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(xuid) DO UPDATE SET
                gamertag = excluded.gamertag,
                discord_id = COALESCE(excluded.discord_id, xuids.discord_id),
                updated_at = CURRENT_TIMESTAMP
            """,
            (str(xuid), str(gamertag), str(discord_id) if discord_id else None),
        )
        await db.commit()


async def upsert_xuid_celo_seed_override(
    xuid: str,
    gamertag: str | None,
    seed_source: str,
    seed_tier: str | None = None,
    seed_rating: float | None = None,
    seed_detail: str | None = None,
    *,
    seed_locked: bool = True,
    prior_historical_match_count: int | None = None,
    prior_na_match_count: int | None = None,
    prior_performance_score: float | None = None,
) -> dict:
    normalized_tier = normalize_tier(seed_tier) if seed_tier else None
    resolved_rating = float(seed_rating) if seed_rating is not None else (
        tier_seed_rating(normalized_tier) if normalized_tier else None
    )
    label = (gamertag or str(xuid)).strip() or str(xuid)
    async with aiosqlite.connect(DB_PATH) as db:
        await _run_migrations(db)
        await db.execute(
            """
            INSERT INTO xuid_celo_seed_overrides (
                xuid, gamertag, seed_source, seed_tier, seed_rating, seed_detail,
                seed_locked, prior_historical_match_count, prior_na_match_count,
                prior_performance_score, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(xuid) DO UPDATE SET
                gamertag = COALESCE(excluded.gamertag, xuid_celo_seed_overrides.gamertag),
                seed_source = excluded.seed_source,
                seed_tier = COALESCE(excluded.seed_tier, xuid_celo_seed_overrides.seed_tier),
                seed_rating = COALESCE(excluded.seed_rating, xuid_celo_seed_overrides.seed_rating),
                seed_detail = COALESCE(excluded.seed_detail, xuid_celo_seed_overrides.seed_detail),
                seed_locked = excluded.seed_locked,
                prior_historical_match_count = CASE
                    WHEN excluded.prior_historical_match_count > 0 THEN excluded.prior_historical_match_count
                    ELSE xuid_celo_seed_overrides.prior_historical_match_count
                END,
                prior_na_match_count = CASE
                    WHEN excluded.prior_na_match_count > 0 THEN excluded.prior_na_match_count
                    ELSE xuid_celo_seed_overrides.prior_na_match_count
                END,
                prior_performance_score = COALESCE(
                    excluded.prior_performance_score,
                    xuid_celo_seed_overrides.prior_performance_score
                ),
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                str(xuid),
                label,
                str(seed_source),
                normalized_tier,
                resolved_rating,
                seed_detail,
                1 if seed_locked else 0,
                int(prior_historical_match_count or 0),
                int(prior_na_match_count or 0),
                prior_performance_score,
            ),
        )
        if resolved_rating is not None:
            await db.execute(
                """
                UPDATE xuid_celo_profiles
                SET seed_source = ?,
                    seed_tier = ?,
                    seed_rating = ?,
                    seed_detail = ?,
                    seed_locked = ?,
                    prior_historical_match_count = CASE
                        WHEN ? > 0 THEN ?
                        ELSE prior_historical_match_count
                    END,
                    prior_na_match_count = CASE
                        WHEN ? > 0 THEN ?
                        ELSE prior_na_match_count
                    END,
                    prior_performance_score = COALESCE(?, prior_performance_score),
                    updated_at = CURRENT_TIMESTAMP
                WHERE xuid = ?
                """,
                (
                    str(seed_source),
                    normalized_tier,
                    resolved_rating,
                    seed_detail,
                    1 if seed_locked else 0,
                    int(prior_historical_match_count or 0),
                    int(prior_historical_match_count or 0),
                    int(prior_na_match_count or 0),
                    int(prior_na_match_count or 0),
                    prior_performance_score,
                    str(xuid),
                ),
            )
        await db.commit()
    return {
        "xuid": str(xuid),
        "gamertag": label,
        "seed_source": str(seed_source),
        "seed_tier": normalized_tier,
        "seed_rating": resolved_rating,
        "seed_score": celo_score(resolved_rating) if resolved_rating is not None else None,
    }


async def record_xuid_celo_prior_evidence(
    xuid: str,
    gamertag: str | None = None,
    *,
    historical_match_count: int = 0,
    na_match_count: int = 0,
    prior_performance_score: float | None = None,
) -> dict:
    label = (gamertag or str(xuid)).strip() or str(xuid)
    historical_count = max(0, int(historical_match_count or 0))
    na_count = max(0, int(na_match_count or 0))
    async with aiosqlite.connect(DB_PATH) as db:
        await _run_migrations(db)
        await db.execute(
            """
            INSERT INTO xuid_celo_seed_overrides (
                xuid, gamertag, seed_source, seed_locked,
                prior_historical_match_count, prior_na_match_count,
                prior_performance_score, updated_at
            ) VALUES (?, ?, 'prior_only', 0, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(xuid) DO UPDATE SET
                gamertag = COALESCE(excluded.gamertag, xuid_celo_seed_overrides.gamertag),
                prior_historical_match_count = MAX(
                    xuid_celo_seed_overrides.prior_historical_match_count,
                    excluded.prior_historical_match_count
                ),
                prior_na_match_count = MAX(
                    xuid_celo_seed_overrides.prior_na_match_count,
                    excluded.prior_na_match_count
                ),
                prior_performance_score = COALESCE(
                    excluded.prior_performance_score,
                    xuid_celo_seed_overrides.prior_performance_score
                ),
                updated_at = CURRENT_TIMESTAMP
            """,
            (str(xuid), label, historical_count, na_count, prior_performance_score),
        )
        await db.execute(
            """
            UPDATE xuid_celo_profiles
            SET prior_historical_match_count = MAX(prior_historical_match_count, ?),
                prior_na_match_count = MAX(prior_na_match_count, ?),
                prior_performance_score = COALESCE(?, prior_performance_score),
                updated_at = CURRENT_TIMESTAMP
            WHERE xuid = ?
            """,
            (historical_count, na_count, prior_performance_score, str(xuid)),
        )
        await db.commit()
    return {
        "xuid": str(xuid),
        "gamertag": label,
        "historical_match_count": historical_count,
        "na_match_count": na_count,
        "prior_performance_score": prior_performance_score,
    }


async def register_clan_roster_xuid(
    guild_id: str,
    xuid: str,
    gamertag: str,
    tier: str | None,
    registered_by: str,
    discord_id: str | None = None,
    rebuild_existing_history: bool = False,
) -> dict:
    normalized_tier = normalize_tier(tier) if tier else None
    seed_rating = tier_seed_rating(normalized_tier) if normalized_tier else None
    await upsert_xuid_identity(str(xuid), gamertag.strip(), discord_id)
    if discord_id:
        await set_user_allegiance(str(discord_id), str(guild_id))
    logged_games = await count_logged_games_for_xuid(str(xuid))

    async with aiosqlite.connect(DB_PATH) as db:
        await _run_migrations(db)
        await db.execute(
            """
            INSERT INTO clan_roster_xuids (
                guild_id, xuid, gamertag, tier, registered_by, discord_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(guild_id, xuid) DO UPDATE SET
                gamertag = excluded.gamertag,
                tier = COALESCE(excluded.tier, clan_roster_xuids.tier),
                registered_by = excluded.registered_by,
                discord_id = COALESCE(excluded.discord_id, clan_roster_xuids.discord_id),
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                str(guild_id),
                str(xuid),
                gamertag.strip(),
                normalized_tier,
                str(registered_by),
                str(discord_id) if discord_id else None,
            ),
        )
        if seed_rating is not None:
            seed_detail = f"Registered in clan {guild_id} by {registered_by}"
            await db.execute(
                """
                INSERT INTO xuid_celo_seed_overrides (
                    xuid, gamertag, seed_source, seed_tier, seed_rating,
                    seed_detail, seed_locked, updated_at
                ) VALUES (?, ?, 'user_tier', ?, ?, ?, 1, CURRENT_TIMESTAMP)
                ON CONFLICT(xuid) DO UPDATE SET
                    gamertag = excluded.gamertag,
                    seed_source = excluded.seed_source,
                    seed_tier = excluded.seed_tier,
                    seed_rating = excluded.seed_rating,
                    seed_detail = excluded.seed_detail,
                    seed_locked = excluded.seed_locked,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (str(xuid), gamertag.strip(), normalized_tier, seed_rating, seed_detail),
            )
            await db.execute(
                """
                INSERT INTO xuid_celo_profiles (
                    xuid, gamertag, rating, seed_source, seed_tier,
                    seed_rating, seed_detail, seed_locked
                ) VALUES (?, ?, ?, 'user_tier', ?, ?, ?, 1)
                ON CONFLICT(xuid) DO UPDATE SET
                    gamertag = excluded.gamertag,
                    rating = CASE
                        WHEN xuid_celo_profiles.games_played = 0 THEN excluded.rating
                        ELSE xuid_celo_profiles.rating
                    END,
                    seed_source = excluded.seed_source,
                    seed_tier = excluded.seed_tier,
                    seed_rating = excluded.seed_rating,
                    seed_detail = excluded.seed_detail,
                    seed_locked = excluded.seed_locked,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (str(xuid), gamertag.strip(), seed_rating, normalized_tier, seed_rating, seed_detail),
            )
            for model_key in CELO_MODEL_CONFIGS:
                await db.execute(
                    """
                    INSERT INTO xuid_celo_model_profiles (model_key, xuid, gamertag, rating)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(model_key, xuid) DO UPDATE SET
                        gamertag = excluded.gamertag,
                        rating = CASE
                            WHEN xuid_celo_model_profiles.games_played = 0 THEN excluded.rating
                            ELSE xuid_celo_model_profiles.rating
                        END,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (model_key, str(xuid), gamertag.strip(), seed_rating),
                )
        else:
            await db.execute(
                """
                INSERT INTO xuid_celo_profiles (xuid, gamertag)
                VALUES (?, ?)
                ON CONFLICT(xuid) DO UPDATE SET
                    gamertag = excluded.gamertag,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (str(xuid), gamertag.strip()),
            )
        await db.commit()

    rebuild_result = None
    rebuild_recommended = bool(seed_rating is not None and logged_games > 0)
    if rebuild_existing_history and rebuild_recommended:
        rebuild_result = await rebuild_all_celo()

    return {
        "guild_id": str(guild_id),
        "xuid": str(xuid),
        "gamertag": gamertag.strip(),
        "tier": normalized_tier,
        "seed_score": celo_score(seed_rating) if seed_rating is not None else None,
        "logged_games": logged_games,
        "rebuild_recommended": rebuild_recommended,
        "rebuild": rebuild_result,
    }


async def upsert_clan_roster_membership_xuid(
    guild_id: str,
    xuid: str,
    gamertag: str,
    registered_by: str,
    tier: str | None = None,
    discord_id: str | None = None,
    set_allegiance_flag: bool = True,
) -> dict:
    normalized_tier = normalize_tier(tier) if tier else None
    await upsert_xuid_identity(str(xuid), gamertag.strip(), discord_id)
    allegiance_set = False
    if discord_id and set_allegiance_flag:
        allegiance_set = await set_user_allegiance(str(discord_id), str(guild_id))

    async with aiosqlite.connect(DB_PATH) as db:
        await _run_migrations(db)
        await db.execute(
            """
            INSERT INTO clan_roster_xuids (
                guild_id, xuid, gamertag, tier, registered_by, discord_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(guild_id, xuid) DO UPDATE SET
                gamertag = excluded.gamertag,
                tier = COALESCE(excluded.tier, clan_roster_xuids.tier),
                registered_by = excluded.registered_by,
                discord_id = COALESCE(excluded.discord_id, clan_roster_xuids.discord_id),
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                str(guild_id),
                str(xuid),
                gamertag.strip(),
                normalized_tier,
                str(registered_by),
                str(discord_id) if discord_id else None,
            ),
        )
        await db.commit()

    return {
        "guild_id": str(guild_id),
        "xuid": str(xuid),
        "gamertag": gamertag.strip(),
        "tier": normalized_tier,
        "discord_id": str(discord_id) if discord_id else None,
        "allegiance_set": allegiance_set,
    }


async def list_clan_roster(guild_id: str, limit: int = 100) -> list[dict]:
    max_rows = max(1, min(500, int(limit or 100)))
    async with aiosqlite.connect(DB_PATH) as db:
        await _run_migrations(db)
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT r.guild_id, r.xuid, r.gamertag, r.tier, x.discord_id,
                   p.rating, p.deviation, p.games_played, p.wins, p.losses, p.draws
            FROM clan_roster_xuids r
            LEFT JOIN xuids x
              ON x.xuid = r.xuid
            LEFT JOIN xuid_celo_profiles p
              ON p.xuid = r.xuid
            WHERE r.guild_id = ?
            ORDER BY COALESCE(p.rating, 1000.0) DESC, r.gamertag ASC
            LIMIT ?
            """,
            (str(guild_id), max_rows),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def find_xuid_identity(query: str, guild_id: str | None = None) -> Optional[dict]:
    needle = (query or "").strip()
    if not needle:
        return None

    async with aiosqlite.connect(DB_PATH) as db:
        await _run_migrations(db)
        db.row_factory = aiosqlite.Row
        if guild_id is not None:
            cursor = await db.execute(
                """
                SELECT x.xuid, x.gamertag, x.discord_id, r.tier AS clan_tier
                FROM xuids x
                JOIN clan_roster_xuids r
                  ON r.xuid = x.xuid
                 AND r.guild_id = ?
                WHERE x.xuid = ?
                   OR lower(x.gamertag) = ?
                   OR lower(x.gamertag) LIKE '%' || ? || '%'
                ORDER BY
                    CASE
                        WHEN x.xuid = ? THEN 0
                        WHEN lower(x.gamertag) = ? THEN 1
                        ELSE 2
                    END,
                    x.gamertag ASC
                LIMIT 1
                """,
                (str(guild_id), needle, needle.lower(), needle.lower(), needle, needle.lower()),
            )
        else:
            cursor = await db.execute(
                """
                SELECT x.xuid, x.gamertag, x.discord_id, NULL AS clan_tier
                FROM xuids x
                WHERE x.xuid = ?
                   OR lower(x.gamertag) = ?
                   OR lower(x.gamertag) LIKE '%' || ? || '%'
                ORDER BY
                    CASE
                        WHEN x.xuid = ? THEN 0
                        WHEN lower(x.gamertag) = ? THEN 1
                        ELSE 2
                    END,
                    x.gamertag ASC
                LIMIT 1
                """,
                (needle, needle.lower(), needle.lower(), needle, needle.lower()),
            )
        row = await cursor.fetchone()
        if row:
            return dict(row)

        if guild_id is None:
            return None

        roster_cursor = await db.execute(
            """
            SELECT r.xuid, r.gamertag, COALESCE(x.discord_id, r.discord_id) AS discord_id, r.tier AS clan_tier
            FROM clan_roster_xuids r
            LEFT JOIN xuids x ON x.xuid = r.xuid
            WHERE r.guild_id = ?
              AND (
                    r.xuid = ?
                 OR lower(r.gamertag) = ?
                 OR lower(r.gamertag) LIKE '%' || ? || '%'
              )
            ORDER BY
                CASE
                    WHEN r.xuid = ? THEN 0
                    WHEN lower(r.gamertag) = ? THEN 1
                    ELSE 2
                END,
                r.gamertag ASC
            LIMIT 1
            """,
            (str(guild_id), needle, needle.lower(), needle.lower(), needle, needle.lower()),
        )
        roster_row = await roster_cursor.fetchone()
        return dict(roster_row) if roster_row else None


async def count_logged_games_for_xuid(xuid: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            SELECT COUNT(DISTINCT game_id)
            FROM player_game_stats
            WHERE xuid = ?
            """,
            (str(xuid),),
        )
        row = await cursor.fetchone()
        return int(row[0] or 0)


async def unlink_xuid_for_user(discord_id: str, xuid: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            UPDATE xuids
            SET discord_id = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE discord_id = ?
              AND xuid = ?
            """,
            (str(discord_id), str(xuid)),
        )
        await db.commit()
        return cursor.rowcount > 0


async def get_xuid_link_record(xuid: str) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT xuid, gamertag, discord_id, added_at, updated_at
            FROM xuids
            WHERE xuid = ?
            LIMIT 1
            """,
            (str(xuid),),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_xuid_links(discord_id: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT xuid, gamertag, added_at, updated_at
            FROM xuids
            WHERE discord_id = ?
            ORDER BY added_at DESC
            """,
            (str(discord_id),),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def get_gamertags(discord_id: str) -> list[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            SELECT DISTINCT gamertag
            FROM xuids
            WHERE discord_id = ?
            ORDER BY added_at ASC
            """,
            (str(discord_id),),
        )
        rows = await cursor.fetchall()
        return [r[0] for r in rows]


async def get_or_create_gamertag(xuid: str, gamertag_fetcher) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT gamertag FROM xuids WHERE xuid = ?", (str(xuid),))
        row = await cursor.fetchone()
        if row and row[0]:
            return row[0]

    gamertag = await gamertag_fetcher(str(xuid))
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO xuids (xuid, gamertag, discord_id)
            VALUES (?, ?, NULL)
            ON CONFLICT(xuid) DO UPDATE SET
                gamertag = excluded.gamertag,
                updated_at = CURRENT_TIMESTAMP
            """,
            (str(xuid), gamertag),
        )
        await db.commit()
    return gamertag


async def find_existing_matches(match_ids: list[str]) -> list[dict]:
    if not match_ids:
        return []

    placeholders = ",".join("?" for _ in match_ids)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            f"""
            SELECT g.match_id, g.id AS game_id, g.event_id
            FROM games g
            WHERE g.match_id IN ({placeholders})
            ORDER BY g.match_id ASC
            """,
            tuple(str(mid) for mid in match_ids),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def log_event(
    guild_id: str,
    category: str,
    coordinator_id: str,
    outcome: str,
    opponent_guild_id: str,
    opponent_name: str,
    notes: str = "",
) -> tuple[int, int, str]:
    await ensure_user(str(coordinator_id))
    normalized_category = _normalize_event_category(category)

    async with aiosqlite.connect(DB_PATH) as db:
        await _run_migrations(db)
        cursor = await db.execute(
            """
            SELECT MAX(event_number)
            FROM events
            WHERE guild_id = ? AND category = ?
            """,
            (str(guild_id), normalized_category),
        )
        row = await cursor.fetchone()
        next_number = (row[0] or 0) + 1

        event_public_id = _build_event_public_id()

        insert = await db.execute(
            """
            INSERT INTO events (
                guild_id, category, coordinator_id, opponent, opponent_guild_id,
                event_number, outcome, notes, public_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(guild_id),
                normalized_category,
                str(coordinator_id),
                opponent_name.strip(),
                str(opponent_guild_id),
                int(next_number),
                outcome,
                notes.strip(),
                event_public_id,
            ),
        )
        event_id = int(insert.lastrowid)
        await db.commit()

    event_code = event_public_id
    return event_id, next_number, event_code


async def delete_event(event_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await _run_migrations(db)
        await db.execute("DELETE FROM player_game_medals WHERE game_id IN (SELECT id FROM games WHERE event_id = ?)", (int(event_id),))
        await db.execute("DELETE FROM player_game_stats WHERE game_id IN (SELECT id FROM games WHERE event_id = ?)", (int(event_id),))
        await db.execute("DELETE FROM games WHERE event_id = ?", (int(event_id),))
        await db.execute("DELETE FROM event_report_posts WHERE event_id = ?", (int(event_id),))
        await db.execute("DELETE FROM event_contests WHERE event_id = ?", (int(event_id),))
        await db.execute("DELETE FROM events WHERE id = ?", (int(event_id),))
        await db.execute("DELETE FROM celo_ledger WHERE event_id = ?", (int(event_id),))
        await db.execute("DELETE FROM celo_model_ledger WHERE event_id = ?", (int(event_id),))
        await db.execute("DELETE FROM xuid_celo_ledger WHERE event_id = ?", (int(event_id),))
        await db.execute("DELETE FROM xuid_celo_model_ledger WHERE event_id = ?", (int(event_id),))
        await db.commit()


async def log_game(
    event_id: int,
    match_id: str,
    outcome: str,
    start_time: str,
    duration: str,
    map_name: str,
    mode_name: str,
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            INSERT INTO games (event_id, match_id, outcome, start_time, duration, map_name, mode_name)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (event_id, str(match_id), outcome, start_time, duration, map_name, mode_name),
        )
        game_id = int(cursor.lastrowid)
        await db.commit()
        return game_id


async def log_player_game_stats(game_id: int, player_stats: list[dict]) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        for stat in player_stats:
            await db.execute(
                """
                INSERT OR REPLACE INTO player_game_stats (
                    game_id, xuid, gamertag, team, outcome, kills, deaths, assists, damage_dealt
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(game_id),
                    str(stat["xuid"]),
                    stat.get("gamertag"),
                    stat.get("team"),
                    stat.get("outcome", "N/A"),
                    int(stat.get("kills", 0) or 0),
                    int(stat.get("deaths", 0) or 0),
                    int(stat.get("assists", 0) or 0),
                    int(stat.get("damage_dealt", 0) or 0),
                ),
            )
        await db.commit()


async def log_player_game_medals(game_id: int, medal_rows: list[dict]) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        await _run_migrations(db)
        inserted = 0
        scoped_rows = [dict(row, game_id=int(game_id)) for row in medal_rows]
        for row in _apply_effective_proficiency_counts(scoped_rows):
            medal_name = str(row.get("medal_name") or row.get("medal_name_id"))
            effective_count = int(row.get("effective_count", row.get("count", 0)) or 0)
            scoring = classify_proficiency_medal(medal_name, effective_count)
            await db.execute(
                """
                INSERT INTO player_game_medals (
                    game_id, xuid, medal_name_id, medal_name, count,
                    total_personal_score_awarded, proficiency_field, weight_class,
                    weight, rating_delta, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(game_id, xuid, medal_name_id) DO UPDATE SET
                    medal_name = excluded.medal_name,
                    count = excluded.count,
                    total_personal_score_awarded = excluded.total_personal_score_awarded,
                    proficiency_field = excluded.proficiency_field,
                    weight_class = excluded.weight_class,
                    weight = excluded.weight,
                    rating_delta = excluded.rating_delta,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    int(game_id),
                    str(row["xuid"]),
                    int(row["medal_name_id"]),
                    medal_name,
                    effective_count,
                    int(row.get("total_personal_score_awarded", 0) or 0),
                    scoring["field"],
                    scoring["weight_class"],
                    int(scoring["weight"]),
                    int(scoring["rating_delta"]),
                ),
            )
            inserted += 1
        await db.commit()
        return inserted


async def get_event(event_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT id, guild_id, category, coordinator_id, opponent, opponent_guild_id,
                   event_number, outcome, notes, public_id, timestamp
            FROM events
            WHERE id = ?
            """,
            (int(event_id),),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_games_for_event(event_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT id, match_id, outcome, start_time, duration, map_name, mode_name
            FROM games
            WHERE event_id = ?
            ORDER BY id ASC
            """,
            (int(event_id),),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def get_games_missing_medals(limit: int = 50) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        await _run_migrations(db)
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT g.id, g.match_id
            FROM games g
            LEFT JOIN player_game_medals pgm ON pgm.game_id = g.id
            GROUP BY g.id, g.match_id
            HAVING COUNT(pgm.medal_name_id) = 0
            ORDER BY g.id DESC
            LIMIT ?
            """,
            (max(1, min(200, int(limit or 50))),),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_game_medal_highlights(game_id: int, limit: int = 8) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        await _run_migrations(db)
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT
                x.discord_id,
                COALESCE(x.gamertag, pgm.xuid) AS gamertag,
                pgm.medal_name,
                pgm.count,
                pgm.total_personal_score_awarded AS score
            FROM player_game_medals pgm
            LEFT JOIN xuids x ON x.xuid = pgm.xuid
            WHERE pgm.game_id = ?
              AND pgm.count > 0
            ORDER BY pgm.total_personal_score_awarded DESC, pgm.count DESC, pgm.medal_name ASC
            LIMIT ?
            """,
            (int(game_id), max(1, min(20, int(limit or 8)))),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_event_stat_summary(event_id: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT
                COUNT(DISTINCT g.id) AS games,
                COUNT(DISTINCT pgs.xuid) AS players,
                COALESCE(SUM(pgs.kills), 0) AS kills,
                COALESCE(SUM(pgs.deaths), 0) AS deaths,
                COALESCE(SUM(pgs.assists), 0) AS assists,
                COALESCE(SUM(pgs.damage_dealt), 0) AS damage,
                MIN(g.start_time) AS first_start,
                MAX(g.start_time) AS last_start
            FROM games g
            LEFT JOIN player_game_stats pgs ON pgs.game_id = g.id
            WHERE g.event_id = ?
            """,
            (int(event_id),),
        )
        row = await cursor.fetchone()
        return {
            "games": int(row["games"] or 0) if row else 0,
            "players": int(row["players"] or 0) if row else 0,
            "kills": int(row["kills"] or 0) if row else 0,
            "deaths": int(row["deaths"] or 0) if row else 0,
            "assists": int(row["assists"] or 0) if row else 0,
            "damage": int(row["damage"] or 0) if row else 0,
            "first_start": row["first_start"] if row else None,
            "last_start": row["last_start"] if row else None,
        }


async def get_event_top_performers(event_id: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT
                pgs.xuid,
                COALESCE(x.gamertag, pgs.gamertag, pgs.xuid) AS gamertag,
                x.discord_id,
                COALESCE(SUM(pgs.kills), 0) AS kills,
                COALESCE(SUM(pgs.deaths), 0) AS deaths,
                COALESCE(SUM(pgs.assists), 0) AS assists,
                COALESCE(SUM(pgs.damage_dealt), 0) AS damage
            FROM player_game_stats pgs
            JOIN games g ON g.id = pgs.game_id
            LEFT JOIN xuids x ON x.xuid = pgs.xuid
            WHERE g.event_id = ?
            GROUP BY pgs.xuid
            """,
            (int(event_id),),
        )
        rows = [dict(row) for row in await cursor.fetchall()]

    if not rows:
        return {}

    def enriched(row: dict) -> dict:
        out = dict(row)
        out["kills"] = int(out.get("kills") or 0)
        out["deaths"] = int(out.get("deaths") or 0)
        out["assists"] = int(out.get("assists") or 0)
        out["damage"] = int(out.get("damage") or 0)
        return out

    players = [enriched(row) for row in rows]
    return {
        "kills": max(players, key=lambda row: (row["kills"], row["assists"], row["damage"])),
        "assists": max(players, key=lambda row: (row["assists"], row["kills"], row["damage"])),
        "damage": max(players, key=lambda row: (row["damage"], row["kills"], row["assists"])),
        "survivor": min(players, key=lambda row: (row["deaths"], -row["kills"], -row["assists"])),
    }


async def get_event_top_performers_for_guild(event_id: int, guild_id: str) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT
                pgs.xuid,
                COALESCE(x.gamertag, pgs.gamertag, pgs.xuid) AS gamertag,
                x.discord_id,
                COALESCE(SUM(pgs.kills), 0) AS kills,
                COALESCE(SUM(pgs.deaths), 0) AS deaths,
                COALESCE(SUM(pgs.assists), 0) AS assists,
                COALESCE(SUM(pgs.damage_dealt), 0) AS damage
            FROM player_game_stats pgs
            JOIN games g ON g.id = pgs.game_id
            LEFT JOIN xuids x ON x.xuid = pgs.xuid
            JOIN user_allegiances ua
              ON ua.discord_id = x.discord_id
             AND ua.guild_id = ?
            WHERE g.event_id = ?
            GROUP BY pgs.xuid
            """,
            (str(guild_id), int(event_id)),
        )
        rows = [dict(row) for row in await cursor.fetchall()]

    if not rows:
        return {}

    def enriched(row: dict) -> dict:
        out = dict(row)
        out["kills"] = int(out.get("kills") or 0)
        out["deaths"] = int(out.get("deaths") or 0)
        out["assists"] = int(out.get("assists") or 0)
        out["damage"] = int(out.get("damage") or 0)
        return out

    players = [enriched(row) for row in rows]
    return {
        "kills": max(players, key=lambda row: (row["kills"], row["assists"], row["damage"])),
        "assists": max(players, key=lambda row: (row["assists"], row["kills"], row["damage"])),
        "damage": max(players, key=lambda row: (row["damage"], row["kills"], row["assists"])),
        "survivor": min(players, key=lambda row: (row["deaths"], -row["kills"], -row["assists"])),
    }


async def get_game_stat_summary(game_id: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT
                COUNT(DISTINCT xuid) AS players,
                COALESCE(SUM(kills), 0) AS kills,
                COALESCE(SUM(deaths), 0) AS deaths,
                COALESCE(SUM(assists), 0) AS assists,
                COALESCE(SUM(damage_dealt), 0) AS damage
            FROM player_game_stats
            WHERE game_id = ?
            """,
            (int(game_id),),
        )
        row = await cursor.fetchone()
        return {
            "players": int(row["players"] or 0) if row else 0,
            "kills": int(row["kills"] or 0) if row else 0,
            "deaths": int(row["deaths"] or 0) if row else 0,
            "assists": int(row["assists"] or 0) if row else 0,
            "damage": int(row["damage"] or 0) if row else 0,
        }


async def get_game_raid_profile(game_id: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
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
            (int(game_id),),
        )
        rows = [dict(row) for row in await cursor.fetchall()]

    defender = {
        "team": RAID_DEFENDER_TEAM,
        "label": RAID_DEFENDER_LABEL,
        "players": 0,
        "kills": 0,
        "deaths": 0,
        "assists": 0,
        "damage": 0,
    }
    attackers = {
        "team": "not_0",
        "label": RAID_ATTACKER_LABEL,
        "players": 0,
        "kills": 0,
        "deaths": 0,
        "assists": 0,
        "damage": 0,
    }
    team_rows = []
    for row in rows:
        normalized = {
            "team": str(row.get("team") or "?"),
            "players": int(row.get("players") or 0),
            "kills": int(row.get("kills") or 0),
            "deaths": int(row.get("deaths") or 0),
            "assists": int(row.get("assists") or 0),
            "damage": int(row.get("damage") or 0),
        }
        team_rows.append(normalized)
        target = defender if normalized["team"] == RAID_DEFENDER_TEAM else attackers
        for key in ("players", "kills", "deaths", "assists", "damage"):
            target[key] += normalized[key]

    totals = {
        key: defender[key] + attackers[key]
        for key in ("players", "kills", "deaths", "assists", "damage")
    }
    shares = {
        "defender_kill_share": _share(defender["kills"], totals["kills"]),
        "defender_damage_share": _share(defender["damage"], totals["damage"]),
        "defender_assist_share": _share(defender["assists"], totals["assists"]),
        "defender_death_share": _share(defender["deaths"], totals["deaths"]),
    }
    parity = _parity_from_shares(
        [
            shares["defender_kill_share"],
            shares["defender_damage_share"],
            shares["defender_assist_share"],
            shares["defender_death_share"],
        ]
    )
    if parity >= 0.85:
        interpretation = "highly contested"
    elif parity >= 0.7:
        interpretation = "contested"
    elif parity >= 0.55:
        interpretation = "noticeable imbalance"
    else:
        interpretation = "one-sided stat profile"

    return {
        "defender_team": RAID_DEFENDER_TEAM,
        "defender_label": RAID_DEFENDER_LABEL,
        "attacker_label": RAID_ATTACKER_LABEL,
        "defender": defender,
        "attackers": attackers,
        "totals": totals,
        "shares": shares,
        "parity": round(parity, 4),
        "interpretation": interpretation,
        "teams": team_rows,
    }


async def get_event_celo_impact(event_id: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        await _run_migrations(db)
        cursor = await db.execute(
            """
            SELECT
                COUNT(DISTINCT game_id) AS processed_games,
                COUNT(DISTINCT xuid) AS adjusted_users
            FROM xuid_celo_ledger
            WHERE event_id = ?
            """,
            (int(event_id),),
        )
        row = await cursor.fetchone()
        processed_games = int((row[0] if row else 0) or 0)
        adjusted_users = int((row[1] if row else 0) or 0)
        return {"processed_games": processed_games, "adjusted_users": adjusted_users}


async def get_event_celo_adjustments(event_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        await _run_migrations(db)
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT
                cl.id,
                cl.xuid,
                cl.gamertag,
                x.discord_id,
                cl.old_rating,
                cl.new_rating,
                cl.delta,
                ua_a.guild_id AS in_guild_a,
                ua_b.guild_id AS in_guild_b
            FROM xuid_celo_ledger cl
            LEFT JOIN events e
              ON e.id = cl.event_id
            LEFT JOIN xuids x
              ON x.xuid = cl.xuid
            LEFT JOIN user_allegiances ua_a
              ON ua_a.discord_id = x.discord_id
             AND ua_a.guild_id = e.guild_id
            LEFT JOIN user_allegiances ua_b
              ON ua_b.discord_id = x.discord_id
             AND ua_b.guild_id = e.opponent_guild_id
            WHERE event_id = ?
            ORDER BY cl.id ASC
            """,
            (int(event_id),),
        )
        rows = await cursor.fetchall()

    if not rows:
        return []

    by_user: dict[str, dict] = {}
    for row in rows:
        xuid = str(row["xuid"])
        entry = by_user.get(xuid)
        if entry is None:
            by_user[xuid] = {
                "xuid": xuid,
                "gamertag": str(row["gamertag"] or xuid),
                "discord_id": row["discord_id"],
                "old_rating": float(row["old_rating"] or 0.0),
                "new_rating": float(row["new_rating"] or 0.0),
                "delta": float(row["delta"] or 0.0),
                "games": 1,
                "in_guild_a": row["in_guild_a"] is not None,
                "in_guild_b": row["in_guild_b"] is not None,
            }
            continue

        entry["new_rating"] = float(row["new_rating"] or entry["new_rating"])
        entry["delta"] = float(entry["delta"]) + float(row["delta"] or 0.0)
        entry["games"] = int(entry["games"]) + 1
        entry["in_guild_a"] = bool(entry["in_guild_a"] or (row["in_guild_a"] is not None))
        entry["in_guild_b"] = bool(entry["in_guild_b"] or (row["in_guild_b"] is not None))

    adjustments = list(by_user.values())
    adjustments.sort(key=lambda x: abs(float(x["delta"])), reverse=True)
    return adjustments


async def get_game_attendance_breakdown(game_id: int, guild_a_id: str, guild_b_id: str) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT
                pgs.xuid,
                pgs.gamertag,
                pgs.kills,
                pgs.deaths,
                pgs.assists,
                pgs.damage_dealt,
                pgs.team,
                x.discord_id,
                ua_a.guild_id AS in_guild_a,
                ua_b.guild_id AS in_guild_b
            FROM player_game_stats pgs
            LEFT JOIN xuids x
              ON x.xuid = pgs.xuid
            LEFT JOIN user_allegiances ua_a
              ON ua_a.discord_id = x.discord_id AND ua_a.guild_id = ?
            LEFT JOIN user_allegiances ua_b
              ON ua_b.discord_id = x.discord_id AND ua_b.guild_id = ?
            WHERE pgs.game_id = ?
            ORDER BY pgs.kills DESC, pgs.assists DESC, pgs.deaths ASC
            """,
            (str(guild_a_id), str(guild_b_id), int(game_id)),
        )
        rows = await cursor.fetchall()

    guild_a = []
    guild_b = []
    dual = []
    unknown = []

    for row in rows:
        entry = {
            "xuid": row["xuid"],
            "gamertag": row["gamertag"] or row["xuid"],
            "discord_id": row["discord_id"],
            "kills": int(row["kills"] or 0),
            "deaths": int(row["deaths"] or 0),
            "assists": int(row["assists"] or 0),
            "damage": int(row["damage_dealt"] or 0),
            "team": str(row["team"] or "?"),
        }
        in_a = row["in_guild_a"] is not None
        in_b = row["in_guild_b"] is not None

        if in_a and not in_b:
            guild_a.append(entry)
        elif in_b and not in_a:
            guild_b.append(entry)
        elif in_a and in_b:
            dual.append(entry)
        else:
            unknown.append(entry)

    return {
        "guild_a": guild_a,
        "guild_b": guild_b,
        "dual": dual,
        "unknown": unknown,
    }


async def fetch_user_career_data(discord_id: str, guild_id: Optional[str] = None) -> dict:
    filters = ""
    params: list[str] = [str(discord_id)]

    if guild_id is not None:
        filters = " AND e.guild_id = ?"
        params.append(str(guild_id))

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        hosted_cursor = await db.execute(
            f"""
            SELECT e.category, COUNT(*) AS hosted_count
            FROM events e
            WHERE e.coordinator_id = ? {filters}
            GROUP BY e.category
            """,
            tuple(params),
        )
        hosted_rows = await hosted_cursor.fetchall()

        attended_cursor = await db.execute(
            f"""
            SELECT e.category, COUNT(DISTINCT e.id) AS attended_count
            FROM events e
            JOIN games g ON g.event_id = e.id
            JOIN player_game_stats pgs ON pgs.game_id = g.id
            JOIN xuids x ON x.xuid = pgs.xuid
            WHERE x.discord_id = ? {filters}
            GROUP BY e.category
            """,
            tuple(params),
        )
        attended_rows = await attended_cursor.fetchall()

        totals_cursor = await db.execute(
            f"""
            SELECT
                COALESCE(SUM(pgs.kills), 0),
                COALESCE(SUM(pgs.deaths), 0),
                COALESCE(SUM(pgs.assists), 0),
                COALESCE(SUM(pgs.damage_dealt), 0)
            FROM player_game_stats pgs
            JOIN xuids x ON x.xuid = pgs.xuid
            JOIN games g ON g.id = pgs.game_id
            JOIN events e ON e.id = g.event_id
            WHERE x.discord_id = ? {filters}
            """,
            tuple(params),
        )
        totals = await totals_cursor.fetchone()

    tot_kills, tot_deaths, tot_assists, total_damage = totals
    kd = (tot_kills / tot_deaths) if tot_deaths else float(tot_kills)
    kda = ((tot_kills + tot_assists) / tot_deaths) if tot_deaths else float(tot_kills + tot_assists)

    hosted_counts: dict[str, int] = {}
    for row in hosted_rows:
        category = _normalize_event_category(row["category"])
        hosted_counts[category] = hosted_counts.get(category, 0) + int(row["hosted_count"])

    attended_counts: dict[str, int] = {}
    for row in attended_rows:
        category = _normalize_event_category(row["category"])
        attended_counts[category] = attended_counts.get(category, 0) + int(row["attended_count"])

    return {
        "hosted": dict(sorted(hosted_counts.items(), key=lambda kv: kv[0])),
        "attended": dict(sorted(attended_counts.items(), key=lambda kv: kv[0])),
        "tot_kills": int(tot_kills or 0),
        "tot_deaths": int(tot_deaths or 0),
        "tot_assists": int(tot_assists or 0),
        "total_damage": int(total_damage or 0),
        "kd": round(kd, 3),
        "kda": round(kda, 3),
    }


async def get_user_medal_summary(discord_id: str, guild_id: Optional[str] = None, limit: int = 10) -> dict:
    filters = ""
    params: list[str] = [str(discord_id)]
    if guild_id is not None:
        filters = " AND e.guild_id = ?"
        params.append(str(guild_id))

    async with aiosqlite.connect(DB_PATH) as db:
        await _run_migrations(db)
        db.row_factory = aiosqlite.Row
        totals_cursor = await db.execute(
            f"""
            SELECT
                COALESCE(SUM(pgm.count), 0) AS medal_count,
                COALESCE(SUM(pgm.total_personal_score_awarded), 0) AS medal_score,
                COALESCE(SUM(pgm.rating_delta), 0) AS proficiency_score
            FROM player_game_medals pgm
            JOIN games g ON g.id = pgm.game_id
            JOIN events e ON e.id = g.event_id
            JOIN xuids x ON x.xuid = pgm.xuid
            WHERE x.discord_id = ? {filters}
            """,
            tuple(params),
        )
        totals = await totals_cursor.fetchone()

        top_cursor = await db.execute(
            f"""
            SELECT
                pgm.medal_name,
                COALESCE(SUM(pgm.count), 0) AS count,
                COALESCE(SUM(pgm.total_personal_score_awarded), 0) AS score
            FROM player_game_medals pgm
            JOIN games g ON g.id = pgm.game_id
            JOIN events e ON e.id = g.event_id
            JOIN xuids x ON x.xuid = pgm.xuid
            WHERE x.discord_id = ? {filters}
            GROUP BY pgm.medal_name
            HAVING count > 0
            ORDER BY count DESC, score DESC, pgm.medal_name ASC
            LIMIT ?
            """,
            tuple(params + [str(max(1, int(limit)))]),
        )
        top_rows = await top_cursor.fetchall()

        fields_cursor = await db.execute(
            f"""
            SELECT
                pgm.proficiency_field AS field,
                COALESCE(SUM(pgm.rating_delta), 0) AS score,
                COALESCE(SUM(pgm.count), 0) AS medal_count
            FROM player_game_medals pgm
            JOIN games g ON g.id = pgm.game_id
            JOIN events e ON e.id = g.event_id
            JOIN xuids x ON x.xuid = pgm.xuid
            WHERE x.discord_id = ? {filters}
              AND pgm.proficiency_field IS NOT NULL
            GROUP BY pgm.proficiency_field
            ORDER BY score DESC, field ASC
            """,
            tuple(params),
        )
        field_rows = await fields_cursor.fetchall()

    return {
        "medal_count": int(totals["medal_count"] or 0) if totals else 0,
        "medal_score": int(totals["medal_score"] or 0) if totals else 0,
        "proficiency_score": int(totals["proficiency_score"] or 0) if totals else 0,
        "top_medals": [dict(row) for row in top_rows],
        "proficiencies": [dict(row) for row in field_rows],
    }


async def get_xuid_medal_summary(xuid: str, guild_id: Optional[str] = None, limit: int = 10) -> dict:
    filters = ""
    params: list[str] = [str(xuid)]
    if guild_id is not None:
        filters = " AND e.guild_id = ?"
        params.append(str(guild_id))

    async with aiosqlite.connect(DB_PATH) as db:
        await _run_migrations(db)
        db.row_factory = aiosqlite.Row
        totals_cursor = await db.execute(
            f"""
            SELECT
                COALESCE(SUM(pgm.count), 0) AS medal_count,
                COALESCE(SUM(pgm.total_personal_score_awarded), 0) AS medal_score,
                COALESCE(SUM(pgm.rating_delta), 0) AS proficiency_score
            FROM player_game_medals pgm
            JOIN games g ON g.id = pgm.game_id
            JOIN events e ON e.id = g.event_id
            WHERE pgm.xuid = ? {filters}
            """,
            tuple(params),
        )
        totals = await totals_cursor.fetchone()

        top_cursor = await db.execute(
            f"""
            SELECT
                pgm.medal_name,
                COALESCE(SUM(pgm.count), 0) AS count,
                COALESCE(SUM(pgm.total_personal_score_awarded), 0) AS score,
                COALESCE(SUM(pgm.rating_delta), 0) AS proficiency_score
            FROM player_game_medals pgm
            JOIN games g ON g.id = pgm.game_id
            JOIN events e ON e.id = g.event_id
            WHERE pgm.xuid = ? {filters}
            GROUP BY pgm.medal_name
            HAVING count > 0
            ORDER BY count DESC, proficiency_score DESC, pgm.medal_name ASC
            LIMIT ?
            """,
            tuple(params + [str(max(1, min(25, int(limit or 10))))]),
        )
        top_rows = await top_cursor.fetchall()

        fields_cursor = await db.execute(
            f"""
            SELECT
                pgm.proficiency_field AS field,
                COALESCE(SUM(pgm.rating_delta), 0) AS score,
                COALESCE(SUM(pgm.count), 0) AS medal_count
            FROM player_game_medals pgm
            JOIN games g ON g.id = pgm.game_id
            JOIN events e ON e.id = g.event_id
            WHERE pgm.xuid = ? {filters}
              AND pgm.proficiency_field IS NOT NULL
            GROUP BY pgm.proficiency_field
            ORDER BY score DESC, field ASC
            """,
            tuple(params),
        )
        field_rows = await fields_cursor.fetchall()

    return {
        "medal_count": int(totals["medal_count"] or 0) if totals else 0,
        "medal_score": int(totals["medal_score"] or 0) if totals else 0,
        "proficiency_score": int(totals["proficiency_score"] or 0) if totals else 0,
        "top_medals": [dict(row) for row in top_rows],
        "proficiencies": [dict(row) for row in field_rows],
    }


async def fetch_user_combat_profile(discord_id: str, guild_id: Optional[str] = None) -> dict:
    career = await fetch_user_career_data(discord_id, guild_id=guild_id)
    medals = await get_user_medal_summary(discord_id, guild_id=guild_id, limit=8)
    return {
        **career,
        **medals,
    }


async def get_combat_leaderboard(board: str, guild_id: Optional[str] = None, limit: int = 10) -> list[dict]:
    normalized = (board or "").strip().lower().replace("-", "_")
    max_rows = max(1, min(50, int(limit or 10)))
    filters = ""
    params: list[str] = []
    if guild_id is not None:
        filters = " AND e.guild_id = ?"
        params.append(str(guild_id))

    async with aiosqlite.connect(DB_PATH) as db:
        await _run_migrations(db)
        db.row_factory = aiosqlite.Row

        if normalized in {"kills", "damage", "kd", "kda"}:
            cursor = await db.execute(
                f"""
                SELECT
                    x.discord_id,
                    COALESCE(SUM(pgs.kills), 0) AS kills,
                    COALESCE(SUM(pgs.deaths), 0) AS deaths,
                    COALESCE(SUM(pgs.assists), 0) AS assists,
                    COALESCE(SUM(pgs.damage_dealt), 0) AS damage
                FROM player_game_stats pgs
                JOIN games g ON g.id = pgs.game_id
                JOIN events e ON e.id = g.event_id
                JOIN xuids x ON x.xuid = pgs.xuid
                WHERE x.discord_id IS NOT NULL {filters}
                GROUP BY x.discord_id
                """,
                tuple(params),
            )
            rows = [dict(row) for row in await cursor.fetchall()]
            for row in rows:
                kills = int(row["kills"] or 0)
                deaths = int(row["deaths"] or 0)
                assists = int(row["assists"] or 0)
                damage = int(row["damage"] or 0)
                row["kd"] = round(kills / deaths if deaths else float(kills), 2)
                row["kda"] = round((kills + assists) / deaths if deaths else float(kills + assists), 2)
            rows.sort(key=lambda r: (float(r.get(normalized, 0) or 0), int(r.get("kills", 0) or 0)), reverse=True)
            return rows[:max_rows]

        if normalized in {"events_played", "played"}:
            cursor = await db.execute(
                f"""
                SELECT x.discord_id, COUNT(DISTINCT e.id) AS events_played
                FROM events e
                JOIN games g ON g.event_id = e.id
                JOIN player_game_stats pgs ON pgs.game_id = g.id
                JOIN xuids x ON x.xuid = pgs.xuid
                WHERE x.discord_id IS NOT NULL {filters}
                GROUP BY x.discord_id
                ORDER BY events_played DESC, x.discord_id ASC
                LIMIT ?
                """,
                tuple(params + [str(max_rows)]),
            )
            return [dict(row) for row in await cursor.fetchall()]

        if normalized in {"events_hosted", "hosted"}:
            cursor = await db.execute(
                f"""
                SELECT coordinator_id AS discord_id, COUNT(*) AS events_hosted
                FROM events e
                WHERE coordinator_id IS NOT NULL {filters}
                GROUP BY coordinator_id
                ORDER BY events_hosted DESC, coordinator_id ASC
                LIMIT ?
                """,
                tuple(params + [str(max_rows)]),
            )
            return [dict(row) for row in await cursor.fetchall()]

        if normalized in {"medals", "medal_count", "medal_score", "proficiency"}:
            metric = "medal_count"
            expression = "COALESCE(SUM(pgm.count), 0)"
            if normalized == "medal_score":
                metric = "medal_score"
                expression = "COALESCE(SUM(pgm.total_personal_score_awarded), 0)"
            elif normalized == "proficiency":
                metric = "proficiency"
                expression = "COALESCE(SUM(pgm.rating_delta), 0)"
            cursor = await db.execute(
                f"""
                SELECT x.discord_id, {expression} AS {metric}
                FROM player_game_medals pgm
                JOIN games g ON g.id = pgm.game_id
                JOIN events e ON e.id = g.event_id
                JOIN xuids x ON x.xuid = pgm.xuid
                WHERE x.discord_id IS NOT NULL {filters}
                GROUP BY x.discord_id
                HAVING {metric} > 0
                ORDER BY {metric} DESC, x.discord_id ASC
                LIMIT ?
                """,
                tuple(params + [str(max_rows)]),
            )
            return [dict(row) for row in await cursor.fetchall()]

    return []


async def ensure_celo_profile(discord_id: str) -> None:
    await ensure_user(discord_id)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT OR IGNORE INTO celo_profiles (discord_id)
            VALUES (?)
            """,
            (str(discord_id),),
        )
        await db.commit()


async def get_user_celo(discord_id: str) -> dict:
    await ensure_celo_profile(discord_id)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT discord_id, rating, deviation, volatility, games_played, wins, losses, draws, last_played_at
            FROM celo_profiles
            WHERE discord_id = ?
            """,
            (str(discord_id),),
        )
        row = await cursor.fetchone()
        return dict(row)


async def get_celo_leaderboard(limit: int = 25) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT discord_id, rating, deviation, games_played, wins, losses, draws
            FROM celo_profiles
            ORDER BY rating DESC, games_played DESC
            LIMIT ?
            """,
            (int(limit),),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


def _is_public_celo_model(model_key: str) -> bool:
    if model_key == "official":
        return True
    return bool(CELO_MODEL_CONFIGS.get(model_key, {}).get("public", True))


def list_celo_model_configs(*, include_hidden: bool = False) -> list[dict]:
    rows = [
        {
            "model_key": "official",
            "label": "Official CELO",
            "description": "Current Isabel CELO used by the existing profile table.",
            "public": True,
        }
    ]
    for key, config in CELO_MODEL_CONFIGS.items():
        is_public = bool(config.get("public", True))
        if not include_hidden and not is_public:
            continue
        rows.append(
            {
                "model_key": key,
                "label": str(config["label"]),
                "description": str(config["description"]),
                "public": is_public,
            }
        )
    return rows


async def ensure_celo_model_profile(model_key: str, discord_id: str) -> None:
    if model_key not in CELO_MODEL_CONFIGS:
        raise ValueError(f"Unknown CELO model: {model_key}")
    await ensure_user(discord_id)
    async with aiosqlite.connect(DB_PATH) as db:
        await _run_migrations(db)
        await db.execute(
            """
            INSERT OR IGNORE INTO celo_model_profiles (model_key, discord_id)
            VALUES (?, ?)
            """,
            (str(model_key), str(discord_id)),
        )
        await db.commit()


async def get_user_celo_models(discord_id: str) -> list[dict]:
    official = await get_user_celo(discord_id)
    rows = [
        {
            "model_key": "official",
            "label": "Official CELO",
            **official,
        }
    ]
    async with aiosqlite.connect(DB_PATH) as db:
        await _run_migrations(db)
        db.row_factory = aiosqlite.Row
        for model_key, config in CELO_MODEL_CONFIGS.items():
            if not _is_public_celo_model(model_key):
                continue
            await db.execute(
                """
                INSERT OR IGNORE INTO celo_model_profiles (model_key, discord_id)
                VALUES (?, ?)
                """,
                (model_key, str(discord_id)),
            )
            cursor = await db.execute(
                """
                SELECT model_key, discord_id, rating, deviation, volatility,
                       games_played, wins, losses, draws, last_played_at
                FROM celo_model_profiles
                WHERE model_key = ? AND discord_id = ?
                """,
                (model_key, str(discord_id)),
            )
            row = await cursor.fetchone()
            payload = dict(row)
            payload["label"] = str(config["label"])
            rows.append(payload)
        await db.commit()
    return rows


async def get_celo_model_leaderboard(model_key: str, limit: int = 25, offset: int = 0) -> list[dict]:
    return await get_clan_celo_model_leaderboard(model_key=model_key, guild_id=None, limit=limit, offset=offset)


async def get_clan_celo_model_leaderboard(
    model_key: str,
    guild_id: str | None = None,
    limit: int = 25,
    offset: int = 0,
) -> list[dict]:
    max_rows = max(1, min(50, int(limit or 25)))
    row_offset = max(0, int(offset or 0))
    if model_key == "official":
        return await get_xuid_celo_leaderboard(guild_id=guild_id, limit=max_rows, offset=row_offset)
    if model_key not in CELO_MODEL_CONFIGS:
        return []
    if not _is_public_celo_model(model_key):
        return []
    roster_join = ""
    tier_select = "NULL AS tier"
    filters = ""
    params: list[str | int] = [str(model_key)]
    if guild_id is not None:
        roster_join = "JOIN clan_roster_xuids r ON r.xuid = p.xuid AND r.guild_id = ?"
        tier_select = "r.tier AS tier"
        filters = ""
        params = [str(guild_id), str(model_key)]
    params.extend([max_rows, row_offset])
    async with aiosqlite.connect(DB_PATH) as db:
        await _run_migrations(db)
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            f"""
            SELECT p.xuid, p.gamertag, x.discord_id, p.rating, p.deviation,
                   p.games_played, p.wins, p.losses, p.draws,
                   {tier_select}
            FROM xuid_celo_model_profiles p
            {roster_join}
            LEFT JOIN xuids x
              ON x.xuid = p.xuid
            WHERE p.model_key = ?
            {filters}
            ORDER BY p.rating DESC, p.games_played DESC
            LIMIT ? OFFSET ?
            """,
            tuple(params),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def _seed_override_for_xuid(db, xuid: str) -> dict | None:
    cursor = await db.execute(
        """
        SELECT xuid, gamertag, seed_source, seed_tier, seed_rating, seed_detail,
               seed_locked, prior_historical_match_count, prior_na_match_count,
               prior_performance_score
        FROM xuid_celo_seed_overrides
        WHERE xuid = ?
        LIMIT 1
        """,
        (str(xuid),),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def _lobby_average_seed_rating(db, xuid: str, lobby_xuids: list[str] | None) -> float | None:
    peers = [str(peer) for peer in (lobby_xuids or []) if str(peer) != str(xuid)]
    if not peers:
        return None
    placeholders = ",".join("?" for _ in peers)
    ratings: list[float] = []
    override_cursor = await db.execute(
        f"""
        SELECT seed_rating
        FROM xuid_celo_seed_overrides
        WHERE xuid IN ({placeholders})
          AND seed_rating IS NOT NULL
        """,
        tuple(peers),
    )
    ratings.extend(float(row["seed_rating"]) for row in await override_cursor.fetchall())

    profile_cursor = await db.execute(
        f"""
        SELECT rating
        FROM xuid_celo_profiles
        WHERE xuid IN ({placeholders})
          AND (
                games_played > 0
             OR seed_source IN ('unsc_branch', 'user_tier', 'lobby_average')
          )
        """,
        tuple(peers),
    )
    ratings.extend(float(row["rating"]) for row in await profile_cursor.fetchall())
    if not ratings:
        return None
    return sum(ratings) / float(len(ratings))


async def _resolve_xuid_seed(
    db,
    xuid: str,
    gamertag: str | None = None,
    *,
    lobby_xuids: list[str] | None = None,
    game_id: int | None = None,
) -> dict:
    override = await _seed_override_for_xuid(db, str(xuid))
    if override and override.get("seed_rating") is not None:
        return {
            "rating": float(override["seed_rating"]),
            "seed_source": str(override["seed_source"] or "override"),
            "seed_tier": override.get("seed_tier"),
            "seed_rating": float(override["seed_rating"]),
            "seed_detail": override.get("seed_detail"),
            "seed_locked": int(override.get("seed_locked") or 0),
            "prior_historical_match_count": int(override.get("prior_historical_match_count") or 0),
            "prior_na_match_count": int(override.get("prior_na_match_count") or 0),
            "prior_performance_score": override.get("prior_performance_score"),
        }

    lobby_average = await _lobby_average_seed_rating(db, str(xuid), lobby_xuids)
    if lobby_average is not None:
        return {
            "rating": float(lobby_average),
            "seed_source": "lobby_average",
            "seed_tier": None,
            "seed_rating": float(lobby_average),
            "seed_detail": f"First seen in game {game_id}" if game_id is not None else "First seen in seeded lobby",
            "seed_locked": 0,
            "prior_historical_match_count": int(override.get("prior_historical_match_count") or 0) if override else 0,
            "prior_na_match_count": int(override.get("prior_na_match_count") or 0) if override else 0,
            "prior_performance_score": override.get("prior_performance_score") if override else None,
        }

    return {
        "rating": CELO_RAW_CENTER,
        "seed_source": "default",
        "seed_tier": None,
        "seed_rating": CELO_RAW_CENTER,
        "seed_detail": "Default center seed",
        "seed_locked": 0,
        "prior_historical_match_count": int(override.get("prior_historical_match_count") or 0) if override else 0,
        "prior_na_match_count": int(override.get("prior_na_match_count") or 0) if override else 0,
        "prior_performance_score": override.get("prior_performance_score") if override else None,
    }


async def _ensure_xuid_celo_profile_row(
    db,
    xuid: str,
    gamertag: str | None = None,
    *,
    lobby_xuids: list[str] | None = None,
    game_id: int | None = None,
):
    label = (gamertag or str(xuid)).strip() or str(xuid)
    existing_cursor = await db.execute(
        """
        SELECT xuid, gamertag, rating, deviation, volatility, games_played, wins, losses, draws,
               performance_pressure
        FROM xuid_celo_profiles
        WHERE xuid = ?
        """,
        (str(xuid),),
    )
    existing = await existing_cursor.fetchone()
    if existing:
        await db.execute(
            """
            UPDATE xuid_celo_profiles
            SET gamertag = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE xuid = ?
            """,
            (label, str(xuid)),
        )
        refreshed_cursor = await db.execute(
            """
            SELECT xuid, gamertag, rating, deviation, volatility, games_played, wins, losses, draws,
                   performance_pressure
            FROM xuid_celo_profiles
            WHERE xuid = ?
            """,
            (str(xuid),),
        )
        return await refreshed_cursor.fetchone()

    seed = await _resolve_xuid_seed(db, str(xuid), label, lobby_xuids=lobby_xuids, game_id=game_id)
    await db.execute(
        """
        INSERT INTO xuid_celo_profiles (
            xuid, gamertag, rating, seed_source, seed_tier, seed_rating,
            seed_detail, seed_locked, prior_historical_match_count,
            prior_na_match_count, prior_performance_score
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(xuid),
            label,
            float(seed["rating"]),
            seed["seed_source"],
            seed["seed_tier"],
            float(seed["seed_rating"]),
            seed["seed_detail"],
            int(seed["seed_locked"]),
            int(seed["prior_historical_match_count"]),
            int(seed["prior_na_match_count"]),
            seed["prior_performance_score"],
        ),
    )
    cursor = await db.execute(
        """
        SELECT xuid, gamertag, rating, deviation, volatility, games_played, wins, losses, draws,
               performance_pressure
        FROM xuid_celo_profiles
        WHERE xuid = ?
        """,
        (str(xuid),),
    )
    return await cursor.fetchone()


async def ensure_xuid_celo_profile(xuid: str, gamertag: str | None = None) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await _run_migrations(db)
        db.row_factory = aiosqlite.Row
        await _ensure_xuid_celo_profile_row(db, str(xuid), gamertag)
        await db.commit()


async def get_xuid_celo(xuid: str, gamertag: str | None = None) -> dict:
    await ensure_xuid_celo_profile(str(xuid), gamertag)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT xuid, gamertag, rating, deviation, volatility, games_played,
                   wins, losses, draws, performance_pressure, last_played_at
            FROM xuid_celo_profiles
            WHERE xuid = ?
            """,
            (str(xuid),),
        )
        row = await cursor.fetchone()
        return dict(row)


async def get_user_xuid_celo(discord_id: str) -> list[dict]:
    links = await get_xuid_links(str(discord_id))
    rows = []
    for link in links:
        rows.append(await get_xuid_celo(str(link["xuid"]), str(link["gamertag"])))
    return rows


async def get_user_xuid_celo_models(discord_id: str) -> list[dict]:
    links = await get_xuid_links(str(discord_id))
    if not links:
        return []

    output: list[dict] = []
    async with aiosqlite.connect(DB_PATH) as db:
        await _run_migrations(db)
        db.row_factory = aiosqlite.Row
        for link in links:
            xuid = str(link["xuid"])
            gamertag = str(link["gamertag"])
            await _ensure_xuid_celo_profile_row(db, xuid, gamertag)
            cursor = await db.execute(
                """
                SELECT xuid, gamertag, rating, deviation, volatility,
                       games_played, wins, losses, draws, performance_pressure, last_played_at
                FROM xuid_celo_profiles
                WHERE xuid = ?
                """,
                (xuid,),
            )
            official = dict(await cursor.fetchone())
            output.append({"model_key": "official", "label": "Official CELO", **official})

            for model_key, config in CELO_MODEL_CONFIGS.items():
                if not _is_public_celo_model(model_key):
                    continue
                existing_model_cursor = await db.execute(
                    """
                    SELECT 1
                    FROM xuid_celo_model_profiles
                    WHERE model_key = ? AND xuid = ?
                    LIMIT 1
                    """,
                    (model_key, xuid),
                )
                if await existing_model_cursor.fetchone():
                    await db.execute(
                        """
                        UPDATE xuid_celo_model_profiles
                        SET gamertag = ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE model_key = ? AND xuid = ?
                        """,
                        (gamertag, model_key, xuid),
                    )
                else:
                    seed = await _resolve_xuid_seed(db, xuid, gamertag)
                    await db.execute(
                        """
                        INSERT INTO xuid_celo_model_profiles (model_key, xuid, gamertag, rating)
                        VALUES (?, ?, ?, ?)
                        """,
                        (model_key, xuid, gamertag, float(seed["rating"])),
                    )
                model_cursor = await db.execute(
                    """
                    SELECT model_key, xuid, gamertag, rating, deviation, volatility,
                           games_played, wins, losses, draws, last_played_at
                    FROM xuid_celo_model_profiles
                    WHERE model_key = ? AND xuid = ?
                    """,
                    (model_key, xuid),
                )
                payload = dict(await model_cursor.fetchone())
                payload["label"] = str(config["label"])
                output.append(payload)
        await db.commit()
    return output


def _weighted_xuid_celo_summary(rows: list[dict]) -> dict | None:
    if not rows:
        return None

    active_rows = [
        row for row in rows
        if int(row.get("games_played") or 0) > 0
    ]
    weighted_rows = active_rows or rows
    weight_basis = "games_played" if active_rows else "equal_seed"

    weighted_total = 0.0
    rating_total = 0.0
    deviation_total = 0.0
    for row in weighted_rows:
        weight = int(row.get("games_played") or 0) if active_rows else 1
        weighted_total += float(weight)
        rating_total += float(row.get("rating") or CELO_RAW_CENTER) * weight
        deviation_total += float(row.get("deviation") or 0.0) * weight

    if weighted_total <= 0:
        return None

    return {
        "rating": rating_total / weighted_total,
        "deviation": deviation_total / weighted_total,
        "games_played": sum(int(row.get("games_played") or 0) for row in rows),
        "wins": sum(int(row.get("wins") or 0) for row in rows),
        "losses": sum(int(row.get("losses") or 0) for row in rows),
        "draws": sum(int(row.get("draws") or 0) for row in rows),
        "xuid_count": len(rows),
        "active_xuid_count": len(active_rows),
        "weight_basis": weight_basis,
        "components": rows,
    }


async def get_user_weighted_xuid_celo(discord_id: str) -> dict | None:
    return _weighted_xuid_celo_summary(await get_user_xuid_celo(str(discord_id)))


async def get_user_weighted_xuid_celo_models(discord_id: str) -> list[dict]:
    rows = await get_user_xuid_celo_models(str(discord_id))
    grouped: dict[str, list[dict]] = {}
    labels: dict[str, str] = {}
    for row in rows:
        model_key = str(row.get("model_key") or "official")
        grouped.setdefault(model_key, []).append(row)
        labels[model_key] = str(row.get("label") or model_key)

    output: list[dict] = []
    for model_key, model_rows in grouped.items():
        summary = _weighted_xuid_celo_summary(model_rows)
        if not summary:
            continue
        output.append({
            "model_key": model_key,
            "label": labels.get(model_key, model_key),
            **summary,
        })
    return output


async def get_xuid_celo_leaderboard(
    guild_id: str | None = None,
    limit: int = 25,
    offset: int = 0,
) -> list[dict]:
    max_rows = max(1, min(50, int(limit or 25)))
    row_offset = max(0, int(offset or 0))
    roster_join = ""
    tier_select = "NULL AS tier"
    params: list[str | int] = []
    if guild_id is not None:
        roster_join = "JOIN clan_roster_xuids r ON r.xuid = p.xuid AND r.guild_id = ?"
        tier_select = "r.tier AS tier"
        params.append(str(guild_id))
    params.extend([max_rows, row_offset])
    async with aiosqlite.connect(DB_PATH) as db:
        await _run_migrations(db)
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            f"""
            SELECT p.xuid, p.gamertag, x.discord_id, p.rating, p.deviation,
                   p.games_played, p.wins, p.losses, p.draws, p.performance_pressure,
                   {tier_select}
            FROM xuid_celo_profiles p
            {roster_join}
            LEFT JOIN xuids x
              ON x.xuid = p.xuid
            ORDER BY p.rating DESC, p.games_played DESC
            LIMIT ? OFFSET ?
            """,
            tuple(params),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


def _expected_score(rating_a: float, rating_b: float) -> float:
    return 1.0 / (1.0 + (10.0 ** ((rating_b - rating_a) / 400.0)))


def _k_factor(games_played: int, deviation: float) -> float:
    base = 44.0 if games_played < 20 else 26.0
    dev_mult = max(0.75, min(1.5, deviation / 220.0))
    return base * dev_mult


PERF_MIN_MULTIPLIER = 0.85
PERF_MAX_MULTIPLIER = 1.15
PRESSURE_DECAY = 0.80
PRESSURE_ACCUMULATION = 5.0
PRESSURE_APPLY_RATE = 0.35
PRESSURE_MAX = 18.0
PRESSURE_APPLY_MAX = 5.0
UNLINKED_TEAM_BASE_RATING = 1000.0
RAID_DEFENDER_TEAM = "0"
RAID_DEFENDER_LABEL = "Eagle"
RAID_ATTACKER_LABEL = "Attackers"
PERF_WEIGHTS = {
    "kills": 0.30,
    "assists": 0.20,
    "damage": 0.35,
    "low_deaths": 0.15,
}

CELO_MODEL_CONFIGS = {
    "baseline": {
        "label": "Baseline Elo",
        "description": "Team result only; no individual performance modifier.",
        "new_player_k": 40.0,
        "experienced_k": 24.0,
        "deviation_floor": 70.0,
        "deviation_decay": 0.98,
        "performance_shift": 0.0,
    },
    "conservative": {
        "label": "Conservative CELO",
        "description": "Team result with a light individual performance modifier.",
        "new_player_k": 44.0,
        "experienced_k": 26.0,
        "deviation_floor": 70.0,
        "deviation_decay": 0.98,
        "performance_shift": 0.05,
    },
    "performance": {
        "label": "Performance CELO",
        "description": "Team result with a stronger individual performance modifier.",
        "new_player_k": 44.0,
        "experienced_k": 26.0,
        "deviation_floor": 70.0,
        "deviation_decay": 0.98,
        "performance_shift": 0.25,
    },
    "raid_flat": {
        "label": "Raid Flat CELO",
        "description": "Raid-friendly model: team result dominates; individual stats have a tiny capped effect.",
        "new_player_k": 42.0,
        "experienced_k": 24.0,
        "deviation_floor": 70.0,
        "deviation_decay": 0.98,
        "performance_shift": 0.03,
    },
    "raid_stepwise": {
        "label": "Raid Stepwise CELO",
        "description": "Raid-friendly model: team result dominates; stat impact is bucketed to avoid power-weapon stat inflation.",
        "new_player_k": 42.0,
        "experienced_k": 24.0,
        "deviation_floor": 70.0,
        "deviation_decay": 0.98,
        "performance_shift": 0.05,
        "performance_mode": "stepwise",
        "performance_steps": [
            [0.10, -0.05],
            [0.30, -0.025],
            [0.70, 0.0],
            [0.90, 0.025],
            [1.01, 0.05],
        ],
    },
}


def _percentile_rank(values: list[float], value: float, *, lower_is_better: bool = False) -> float:
    if not values:
        return 0.5
    total = len(values)
    if total == 1:
        return 0.5
    less = sum(1 for x in values if x < value)
    equal = sum(1 for x in values if x == value)
    pct = (less + 0.5 * equal) / float(total)
    if lower_is_better:
        pct = 1.0 - pct
    return max(0.0, min(1.0, pct))


def _team_performance_scores(team_entries: dict[str, dict]) -> dict[str, float]:
    if not team_entries:
        return {}
    if len(team_entries) == 1:
        only_id = next(iter(team_entries))
        return {only_id: 0.5}

    kills_values = [float(v.get("kills", 0)) for v in team_entries.values()]
    assists_values = [float(v.get("assists", 0)) for v in team_entries.values()]
    damage_values = [float(v.get("damage_dealt", 0)) for v in team_entries.values()]
    deaths_values = [float(v.get("deaths", 0)) for v in team_entries.values()]

    scores: dict[str, float] = {}
    for discord_id, row in team_entries.items():
        kills_pct = _percentile_rank(kills_values, float(row.get("kills", 0)))
        assists_pct = _percentile_rank(assists_values, float(row.get("assists", 0)))
        damage_pct = _percentile_rank(damage_values, float(row.get("damage_dealt", 0)))
        low_deaths_pct = _percentile_rank(
            deaths_values,
            float(row.get("deaths", 0)),
            lower_is_better=True,
        )
        composite = (
            PERF_WEIGHTS["kills"] * kills_pct
            + PERF_WEIGHTS["assists"] * assists_pct
            + PERF_WEIGHTS["damage"] * damage_pct
            + PERF_WEIGHTS["low_deaths"] * low_deaths_pct
        )
        scores[discord_id] = max(0.0, min(1.0, composite))
    return scores


def _performance_multiplier(base_delta: float, perf_score: float) -> float:
    centered = max(-1.0, min(1.0, (perf_score - 0.5) * 2.0))
    max_shift = PERF_MAX_MULTIPLIER - 1.0
    shift = centered * max_shift
    if base_delta < 0:
        shift = -shift
    multiplier = 1.0 + shift
    return max(PERF_MIN_MULTIPLIER, min(PERF_MAX_MULTIPLIER, multiplier))


def _bounded_pressure(value: float) -> float:
    return max(-PRESSURE_MAX, min(PRESSURE_MAX, float(value or 0.0)))


def _pressure_residual(perf_score: float, actual: float, expected: float) -> float:
    # Residual is positive when a player performs above what the result context implied.
    performance_result = max(0.0, min(1.0, float(perf_score or 0.5)))
    result_baseline = (max(0.0, min(1.0, float(actual))) + max(0.0, min(1.0, float(expected)))) / 2.0
    return max(-1.0, min(1.0, performance_result - result_baseline))


def _pressure_application(pressure_before: float, actual: float) -> float:
    if actual == 0.5:
        return 0.0
    direction = 1.0 if actual > 0.5 else -1.0
    aligned = max(0.0, float(pressure_before or 0.0) * direction)
    if aligned <= 0:
        return 0.0
    return direction * min(PRESSURE_APPLY_MAX, aligned * PRESSURE_APPLY_RATE)


def _soft_spring_pressure(pressure_before: float, perf_score: float, actual: float, expected: float) -> dict:
    before = _bounded_pressure(pressure_before)
    applied = _pressure_application(before, actual)
    residual = _pressure_residual(perf_score, actual, expected)
    pressure_delta = residual * PRESSURE_ACCUMULATION
    after = _bounded_pressure((before - applied) * PRESSURE_DECAY + pressure_delta)
    return {
        "before": before,
        "delta": pressure_delta,
        "applied": applied,
        "after": after,
    }


def _performance_multiplier_for_shift(base_delta: float, perf_score: float, max_shift: float) -> float:
    shift_limit = max(0.0, float(max_shift or 0.0))
    if shift_limit <= 0:
        return 1.0
    centered = max(-1.0, min(1.0, (perf_score - 0.5) * 2.0))
    shift = centered * shift_limit
    if base_delta < 0:
        shift = -shift
    return max(1.0 - shift_limit, min(1.0 + shift_limit, 1.0 + shift))


def _performance_multiplier_for_model(base_delta: float, perf_score: float, config: dict) -> float:
    if config.get("performance_mode") != "stepwise":
        return _performance_multiplier_for_shift(
            base_delta,
            perf_score,
            float(config.get("performance_shift", 0.0)),
        )

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


def _model_k_factor(games_played: int, deviation: float, config: dict) -> float:
    base = float(config["new_player_k"] if games_played < 20 else config["experienced_k"])
    dev_mult = max(0.75, min(1.5, float(deviation) / 220.0))
    return base * dev_mult


def _normalize_outcome(outcome: str) -> str:
    key = (outcome or "").strip().upper()
    if key in {"WIN", "W"}:
        return "WIN"
    if key in {"LOSS", "L", "DID_NOT_FINISH"}:
        return "LOSS"
    if key in {"DRAW", "TIE"}:
        return "DRAW"
    return "N/A"


def _share(value: int, total: int) -> float:
    return float(value) / float(total) if total else 0.5


def _parity_from_shares(shares: list[float]) -> float:
    if not shares:
        return 0.0
    return max(0.0, min(1.0, 1.0 - (sum(abs(share - 0.5) for share in shares) / len(shares)) * 2.0))


async def apply_celo_for_event(event_id: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        await _run_migrations(db)
        db.row_factory = aiosqlite.Row

        games_cursor = await db.execute(
            "SELECT id FROM games WHERE event_id = ? ORDER BY id ASC",
            (int(event_id),),
        )
        game_rows = await games_cursor.fetchall()
        game_ids = [int(r["id"]) for r in game_rows]

        adjusted_users: dict[str, float] = {}
        processed_games = 0
        skipped_already_processed = 0
        skipped_insufficient_linked_teams = 0

        for game_id in game_ids:
            dupe_cursor = await db.execute(
                "SELECT 1 FROM xuid_celo_ledger WHERE game_id = ? LIMIT 1",
                (game_id,),
            )
            if await dupe_cursor.fetchone():
                skipped_already_processed += 1
                continue

            rows_cursor = await db.execute(
                """
                SELECT
                    pgs.team,
                    pgs.outcome,
                    pgs.xuid,
                    COALESCE(x.gamertag, pgs.gamertag, pgs.xuid) AS gamertag,
                    x.discord_id,
                    pgs.kills,
                    pgs.deaths,
                    pgs.assists,
                    pgs.damage_dealt
                FROM player_game_stats pgs
                LEFT JOIN xuids x ON x.xuid = pgs.xuid
                WHERE pgs.game_id = ?
                """,
                (game_id,),
            )
            stat_rows = await rows_cursor.fetchall()
            game_player_xuids = [str(row["xuid"]) for row in stat_rows if row["xuid"] is not None]

            teams: dict[str, dict] = {}
            for row in stat_rows:
                team_id = str(row["team"] or "unknown")
                if team_id not in teams:
                    teams[team_id] = {"players": set(), "outcomes": [], "player_entries": {}}
                normalized = _normalize_outcome(row["outcome"] or "N/A")
                teams[team_id]["outcomes"].append(normalized)
                xuid = str(row["xuid"])
                teams[team_id]["players"].add(xuid)
                teams[team_id]["player_entries"][xuid] = {
                    "xuid": xuid,
                    "gamertag": str(row["gamertag"] or xuid),
                    "kills": int(row["kills"] or 0),
                    "deaths": int(row["deaths"] or 0),
                    "assists": int(row["assists"] or 0),
                    "damage_dealt": int(row["damage_dealt"] or 0),
                }

            if len(teams) < 2:
                skipped_insufficient_linked_teams += 1
                continue
            linked_player_count = sum(len(payload["players"]) for payload in teams.values())
            if linked_player_count == 0:
                skipped_insufficient_linked_teams += 1
                continue

            team_profiles: dict[str, dict] = {}
            for team_id, payload in teams.items():
                player_ids = sorted(payload["players"])
                profile_rows = []
                for xuid in player_ids:
                    gamertag = str(payload["player_entries"].get(xuid, {}).get("gamertag") or xuid)
                    profile_rows.append(
                        await _ensure_xuid_celo_profile_row(
                            db,
                            xuid,
                            gamertag,
                            lobby_xuids=game_player_xuids,
                            game_id=game_id,
                        )
                    )

                if profile_rows:
                    avg_rating = sum(float(r["rating"]) for r in profile_rows) / float(len(profile_rows))
                else:
                    avg_rating = UNLINKED_TEAM_BASE_RATING
                outcome_counts = {
                    "WIN": payload["outcomes"].count("WIN"),
                    "LOSS": payload["outcomes"].count("LOSS"),
                    "DRAW": payload["outcomes"].count("DRAW"),
                }
                resolved_outcome = max(outcome_counts, key=outcome_counts.get)
                if outcome_counts[resolved_outcome] == 0:
                    resolved_outcome = "DRAW"

                team_profiles[team_id] = {
                    "players": profile_rows,
                    "avg_rating": avg_rating,
                    "resolved_outcome": resolved_outcome,
                    "performance_scores": _team_performance_scores(payload["player_entries"]),
                }

            for team_id, payload in team_profiles.items():
                opponent_teams = [v for k, v in team_profiles.items() if k != team_id]
                opponent_avg = sum(t["avg_rating"] for t in opponent_teams) / max(len(opponent_teams), 1)
                expected = _expected_score(payload["avg_rating"], opponent_avg)

                if payload["resolved_outcome"] == "WIN":
                    actual = 1.0
                elif payload["resolved_outcome"] == "LOSS":
                    actual = 0.0
                else:
                    actual = 0.5

                for profile in payload["players"]:
                    xuid = str(profile["xuid"])
                    gamertag = str(profile["gamertag"] or xuid)
                    old_rating = float(profile["rating"])
                    deviation = float(profile["deviation"])
                    games_played = int(profile["games_played"])
                    wins = int(profile["wins"])
                    losses = int(profile["losses"])
                    draws = int(profile["draws"])
                    pressure_before = float(profile["performance_pressure"] or 0.0)

                    k_value = _k_factor(games_played, deviation)
                    base_delta = k_value * (actual - expected)
                    perf_score = float(payload["performance_scores"].get(xuid, 0.5))
                    perf_multiplier = _performance_multiplier(base_delta, perf_score)
                    spring = _soft_spring_pressure(pressure_before, perf_score, actual, expected)
                    delta = (base_delta * perf_multiplier) + float(spring["applied"])
                    new_rating = max(100.0, old_rating + delta)
                    new_deviation = max(70.0, deviation * 0.98)

                    if actual == 1.0:
                        wins += 1
                    elif actual == 0.0:
                        losses += 1
                    else:
                        draws += 1

                    await db.execute(
                        """
                        UPDATE xuid_celo_profiles
                        SET rating = ?,
                            gamertag = ?,
                            deviation = ?,
                            games_played = ?,
                            wins = ?,
                            losses = ?,
                            draws = ?,
                            performance_pressure = ?,
                            last_played_at = ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE xuid = ?
                        """,
                        (
                            new_rating,
                            gamertag,
                            new_deviation,
                            games_played + 1,
                            wins,
                            losses,
                            draws,
                            float(spring["after"]),
                            datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                            xuid,
                        ),
                    )

                    await db.execute(
                        """
                        INSERT INTO xuid_celo_ledger (
                            event_id, game_id, xuid, gamertag, old_rating, new_rating, delta,
                            expected_score, actual_score, k_factor, base_delta,
                            performance_score, performance_multiplier,
                            pressure_before, pressure_delta, pressure_applied, pressure_after
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            int(event_id),
                            int(game_id),
                            xuid,
                            gamertag,
                            old_rating,
                            new_rating,
                            delta,
                            expected,
                            actual,
                            k_value,
                            base_delta,
                            perf_score,
                            perf_multiplier,
                            float(spring["before"]),
                            float(spring["delta"]),
                            float(spring["applied"]),
                            float(spring["after"]),
                        ),
                    )

                    adjusted_users[xuid] = adjusted_users.get(xuid, 0.0) + delta

            processed_games += 1

        await db.commit()

    return {
        "event_id": int(event_id),
        "processed_games": processed_games,
        "skipped_already_processed": skipped_already_processed,
        "skipped_insufficient_linked_teams": skipped_insufficient_linked_teams,
        "adjusted_users": len(adjusted_users),
        "net_changes": {k: round(v, 2) for k, v in adjusted_users.items()},
    }


async def apply_celo_models_for_event(event_id: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        await _run_migrations(db)
        db.row_factory = aiosqlite.Row

        games_cursor = await db.execute(
            "SELECT id FROM games WHERE event_id = ? ORDER BY id ASC",
            (int(event_id),),
        )
        game_rows = await games_cursor.fetchall()
        game_ids = [int(row["id"]) for row in game_rows]

        results = {
            model_key: {
                "processed_games": 0,
                "skipped_already_processed": 0,
                "skipped_insufficient_linked_teams": 0,
                "adjusted_users": {},
            }
            for model_key in CELO_MODEL_CONFIGS
        }

        for game_id in game_ids:
            rows_cursor = await db.execute(
                """
                SELECT
                    pgs.team,
                    pgs.outcome,
                    pgs.xuid,
                    COALESCE(x.gamertag, pgs.gamertag, pgs.xuid) AS gamertag,
                    x.discord_id,
                    pgs.kills,
                    pgs.deaths,
                    pgs.assists,
                    pgs.damage_dealt
                FROM player_game_stats pgs
                LEFT JOIN xuids x ON x.xuid = pgs.xuid
                WHERE pgs.game_id = ?
                """,
                (game_id,),
            )
            stat_rows = await rows_cursor.fetchall()
            game_player_xuids = [str(row["xuid"]) for row in stat_rows if row["xuid"] is not None]

            teams: dict[str, dict] = {}
            for row in stat_rows:
                team_id = str(row["team"] or "unknown")
                if team_id not in teams:
                    teams[team_id] = {"players": set(), "outcomes": [], "player_entries": {}}
                normalized = _normalize_outcome(row["outcome"] or "N/A")
                teams[team_id]["outcomes"].append(normalized)
                xuid = str(row["xuid"])
                teams[team_id]["players"].add(xuid)
                teams[team_id]["player_entries"][xuid] = {
                    "xuid": xuid,
                    "gamertag": str(row["gamertag"] or xuid),
                    "kills": int(row["kills"] or 0),
                    "deaths": int(row["deaths"] or 0),
                    "assists": int(row["assists"] or 0),
                    "damage_dealt": int(row["damage_dealt"] or 0),
                }

            linked_player_count = sum(len(payload["players"]) for payload in teams.values())
            insufficient = len(teams) < 2 or linked_player_count == 0

            for model_key, config in CELO_MODEL_CONFIGS.items():
                dupe_cursor = await db.execute(
                    """
                    SELECT 1
                    FROM xuid_celo_model_ledger
                    WHERE model_key = ? AND game_id = ?
                    LIMIT 1
                    """,
                    (model_key, game_id),
                )
                if await dupe_cursor.fetchone():
                    results[model_key]["skipped_already_processed"] += 1
                    continue

                if insufficient:
                    results[model_key]["skipped_insufficient_linked_teams"] += 1
                    continue

                team_profiles: dict[str, dict] = {}
                for team_id, payload in teams.items():
                    player_ids = sorted(payload["players"])
                    profile_rows = []
                    for xuid in player_ids:
                        gamertag = str(payload["player_entries"].get(xuid, {}).get("gamertag") or xuid)
                        existing_cursor = await db.execute(
                            """
                            SELECT model_key, xuid, gamertag, rating, deviation, volatility,
                                   games_played, wins, losses, draws
                            FROM xuid_celo_model_profiles
                            WHERE model_key = ? AND xuid = ?
                            """,
                            (model_key, xuid),
                        )
                        existing = await existing_cursor.fetchone()
                        if existing:
                            await db.execute(
                                """
                                UPDATE xuid_celo_model_profiles
                                SET gamertag = ?,
                                    updated_at = CURRENT_TIMESTAMP
                                WHERE model_key = ? AND xuid = ?
                                """,
                                (gamertag, model_key, xuid),
                            )
                            profile_rows.append(existing)
                            continue

                        seed = await _resolve_xuid_seed(
                            db,
                            xuid,
                            gamertag,
                            lobby_xuids=game_player_xuids,
                            game_id=game_id,
                        )
                        await db.execute(
                            """
                            INSERT INTO xuid_celo_model_profiles (model_key, xuid, gamertag, rating)
                            VALUES (?, ?, ?, ?)
                            """,
                            (model_key, xuid, gamertag, float(seed["rating"])),
                        )
                        c = await db.execute(
                            """
                            SELECT model_key, xuid, gamertag, rating, deviation, volatility,
                                   games_played, wins, losses, draws
                            FROM xuid_celo_model_profiles
                            WHERE model_key = ? AND xuid = ?
                            """,
                            (model_key, xuid),
                        )
                        profile_rows.append(await c.fetchone())

                    if profile_rows:
                        avg_rating = sum(float(row["rating"]) for row in profile_rows) / float(len(profile_rows))
                    else:
                        avg_rating = UNLINKED_TEAM_BASE_RATING

                    outcome_counts = {
                        "WIN": payload["outcomes"].count("WIN"),
                        "LOSS": payload["outcomes"].count("LOSS"),
                        "DRAW": payload["outcomes"].count("DRAW"),
                    }
                    resolved_outcome = max(outcome_counts, key=outcome_counts.get)
                    if outcome_counts[resolved_outcome] == 0:
                        resolved_outcome = "DRAW"

                    team_profiles[team_id] = {
                        "players": profile_rows,
                        "avg_rating": avg_rating,
                        "resolved_outcome": resolved_outcome,
                        "performance_scores": _team_performance_scores(payload["player_entries"]),
                    }

                for team_id, payload in team_profiles.items():
                    opponent_teams = [v for k, v in team_profiles.items() if k != team_id]
                    opponent_avg = sum(t["avg_rating"] for t in opponent_teams) / max(len(opponent_teams), 1)
                    expected = _expected_score(payload["avg_rating"], opponent_avg)

                    if payload["resolved_outcome"] == "WIN":
                        actual = 1.0
                    elif payload["resolved_outcome"] == "LOSS":
                        actual = 0.0
                    else:
                        actual = 0.5

                    for profile in payload["players"]:
                        xuid = str(profile["xuid"])
                        gamertag = str(profile["gamertag"] or xuid)
                        old_rating = float(profile["rating"])
                        deviation = float(profile["deviation"])
                        games_played = int(profile["games_played"])
                        wins = int(profile["wins"])
                        losses = int(profile["losses"])
                        draws = int(profile["draws"])

                        k_value = _model_k_factor(games_played, deviation, config)
                        base_delta = k_value * (actual - expected)
                        perf_score = float(payload["performance_scores"].get(xuid, 0.5))
                        perf_multiplier = _performance_multiplier_for_model(base_delta, perf_score, config)
                        delta = base_delta * perf_multiplier
                        new_rating = max(100.0, old_rating + delta)
                        new_deviation = max(
                            float(config["deviation_floor"]),
                            deviation * float(config["deviation_decay"]),
                        )

                        if actual == 1.0:
                            wins += 1
                        elif actual == 0.0:
                            losses += 1
                        else:
                            draws += 1

                        await db.execute(
                            """
                            UPDATE xuid_celo_model_profiles
                            SET rating = ?,
                                gamertag = ?,
                                deviation = ?,
                                games_played = ?,
                                wins = ?,
                                losses = ?,
                                draws = ?,
                                last_played_at = ?,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE model_key = ? AND xuid = ?
                            """,
                            (
                                new_rating,
                                gamertag,
                                new_deviation,
                                games_played + 1,
                                wins,
                                losses,
                                draws,
                                datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                                model_key,
                                xuid,
                            ),
                        )
                        await db.execute(
                            """
                            INSERT OR IGNORE INTO xuid_celo_model_ledger (
                                model_key, event_id, game_id, xuid, gamertag, old_rating,
                                new_rating, delta, expected_score, actual_score,
                                k_factor, base_delta, performance_score,
                                performance_multiplier, metadata_json
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                model_key,
                                int(event_id),
                                int(game_id),
                                xuid,
                                gamertag,
                                old_rating,
                                new_rating,
                                delta,
                                expected,
                                actual,
                                k_value,
                                base_delta,
                                perf_score,
                                perf_multiplier,
                                json.dumps(
                                    {
                                        "label": config["label"],
                                        "performance_shift": config["performance_shift"],
                                        "performance_mode": config.get("performance_mode", "linear"),
                                    },
                                    sort_keys=True,
                                ),
                            ),
                        )
                        adjusted = results[model_key]["adjusted_users"]
                        adjusted[xuid] = adjusted.get(xuid, 0.0) + delta

                results[model_key]["processed_games"] += 1

        await db.commit()

    return {
        "event_id": int(event_id),
        "models": {
            model_key: {
                "processed_games": int(payload["processed_games"]),
                "skipped_already_processed": int(payload["skipped_already_processed"]),
                "skipped_insufficient_linked_teams": int(payload["skipped_insufficient_linked_teams"]),
                "adjusted_users": len(payload["adjusted_users"]),
                "net_changes": {k: round(v, 2) for k, v in payload["adjusted_users"].items()},
            }
            for model_key, payload in results.items()
        },
    }


async def rebuild_all_celo() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        await _run_migrations(db)
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT DISTINCT e.id
            FROM events e
            JOIN games g ON g.event_id = e.id
            ORDER BY e.timestamp ASC, e.id ASC
            """
        )
        rows = await cursor.fetchall()
        event_ids = [int(row["id"]) for row in rows]

        await db.execute("DELETE FROM celo_ledger")
        await db.execute("DELETE FROM celo_model_ledger")
        await db.execute("DELETE FROM celo_profiles")
        await db.execute("DELETE FROM celo_model_profiles")
        await db.execute("DELETE FROM xuid_celo_ledger")
        await db.execute("DELETE FROM xuid_celo_model_ledger")
        await db.execute("DELETE FROM xuid_celo_profiles")
        await db.execute("DELETE FROM xuid_celo_model_profiles")
        await db.commit()

    official_processed_games = 0
    official_adjusted_users: set[str] = set()
    model_processed_games: dict[str, int] = {model_key: 0 for model_key in CELO_MODEL_CONFIGS}

    for event_id in event_ids:
        official = await apply_celo_for_event(event_id)
        official_processed_games += int(official.get("processed_games", 0))
        official_adjusted_users.update(str(user_id) for user_id in official.get("net_changes", {}).keys())

        models = await apply_celo_models_for_event(event_id)
        for model_key, payload in models.get("models", {}).items():
            model_processed_games[model_key] = model_processed_games.get(model_key, 0) + int(
                payload.get("processed_games", 0)
            )

    return {
        "events": len(event_ids),
        "official_processed_games": official_processed_games,
        "official_adjusted_users": len(official_adjusted_users),
        "model_processed_games": model_processed_games,
    }
