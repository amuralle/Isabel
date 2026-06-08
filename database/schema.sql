CREATE TABLE IF NOT EXISTS guild_registry (
    guild_id TEXT PRIMARY KEY,
    guild_name TEXT NOT NULL,
    registered_by TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS users (
    discord_id TEXT PRIMARY KEY,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_allegiances (
    discord_id TEXT NOT NULL,
    guild_id TEXT NOT NULL,
    is_primary INTEGER NOT NULL DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (discord_id, guild_id),
    FOREIGN KEY (discord_id) REFERENCES users(discord_id),
    FOREIGN KEY (guild_id) REFERENCES guild_registry(guild_id)
);

CREATE TABLE IF NOT EXISTS xuids (
    xuid TEXT PRIMARY KEY,
    gamertag TEXT NOT NULL,
    discord_id TEXT,
    added_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (discord_id) REFERENCES users(discord_id)
);

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
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id TEXT NOT NULL,
    category TEXT NOT NULL,
    coordinator_id TEXT NOT NULL,
    opponent TEXT DEFAULT 'none',
    opponent_guild_id TEXT,
    public_id TEXT UNIQUE,
    event_number INTEGER,
    outcome TEXT NOT NULL CHECK(outcome IN ('Win', 'Loss', 'Draw', 'N/A')),
    notes TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (guild_id) REFERENCES guild_registry(guild_id)
);

CREATE TABLE IF NOT EXISTS games (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    match_id TEXT NOT NULL UNIQUE,
    outcome TEXT NOT NULL,
    start_time TEXT,
    duration TEXT,
    map_name TEXT,
    mode_name TEXT,
    FOREIGN KEY (event_id) REFERENCES events(id)
);

CREATE TABLE IF NOT EXISTS player_game_stats (
    game_id INTEGER NOT NULL,
    xuid TEXT NOT NULL,
    gamertag TEXT,
    team TEXT,
    outcome TEXT NOT NULL,
    kills INTEGER,
    deaths INTEGER,
    assists INTEGER,
    damage_dealt INTEGER,
    PRIMARY KEY (game_id, xuid),
    FOREIGN KEY (game_id) REFERENCES games(id)
);

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
);

CREATE TABLE IF NOT EXISTS celo_profiles (
    discord_id TEXT PRIMARY KEY,
    rating REAL NOT NULL DEFAULT 1000.0,
    deviation REAL NOT NULL DEFAULT 350.0,
    volatility REAL NOT NULL DEFAULT 0.06,
    games_played INTEGER NOT NULL DEFAULT 0,
    wins INTEGER NOT NULL DEFAULT 0,
    losses INTEGER NOT NULL DEFAULT 0,
    draws INTEGER NOT NULL DEFAULT 0,
    last_played_at DATETIME,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (discord_id) REFERENCES users(discord_id)
);

CREATE TABLE IF NOT EXISTS celo_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (discord_id) REFERENCES users(discord_id)
);

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
);

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
);

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
);

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
);

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
);

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
);

CREATE TABLE IF NOT EXISTS event_channels (
    guild_id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (guild_id) REFERENCES guild_registry(guild_id)
);

CREATE TABLE IF NOT EXISTS event_reporter_roles (
    guild_id TEXT NOT NULL,
    role_id TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (guild_id, role_id),
    FOREIGN KEY (guild_id) REFERENCES guild_registry(guild_id)
);

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
);

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
);

CREATE INDEX IF NOT EXISTS idx_allegiances_discord ON user_allegiances(discord_id);
CREATE INDEX IF NOT EXISTS idx_allegiances_guild ON user_allegiances(guild_id);
CREATE INDEX IF NOT EXISTS idx_xuids_discord ON xuids(discord_id);
CREATE INDEX IF NOT EXISTS idx_events_guild ON events(guild_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_games_event ON games(event_id);
CREATE INDEX IF NOT EXISTS idx_stats_xuid ON player_game_stats(xuid);
CREATE INDEX IF NOT EXISTS idx_player_game_medals_game ON player_game_medals(game_id);
CREATE INDEX IF NOT EXISTS idx_player_game_medals_xuid ON player_game_medals(xuid);
CREATE INDEX IF NOT EXISTS idx_player_game_medals_field ON player_game_medals(proficiency_field);
CREATE INDEX IF NOT EXISTS idx_celo_rating ON celo_profiles(rating DESC);
CREATE INDEX IF NOT EXISTS idx_celo_ledger_user ON celo_ledger(discord_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_celo_model_profiles_rating ON celo_model_profiles(model_key, rating DESC);
CREATE INDEX IF NOT EXISTS idx_celo_model_ledger_user ON celo_model_ledger(model_key, discord_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_celo_model_ledger_event ON celo_model_ledger(model_key, event_id);
CREATE INDEX IF NOT EXISTS idx_xuid_celo_profiles_rating ON xuid_celo_profiles(rating DESC);
CREATE INDEX IF NOT EXISTS idx_xuid_celo_ledger_event ON xuid_celo_ledger(event_id);
CREATE INDEX IF NOT EXISTS idx_xuid_celo_ledger_xuid ON xuid_celo_ledger(xuid, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_xuid_celo_model_profiles_rating ON xuid_celo_model_profiles(model_key, rating DESC);
CREATE INDEX IF NOT EXISTS idx_xuid_celo_model_ledger_event ON xuid_celo_model_ledger(model_key, event_id);
CREATE INDEX IF NOT EXISTS idx_xuid_celo_model_ledger_xuid ON xuid_celo_model_ledger(model_key, xuid, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_xuid_celo_seed_overrides_source ON xuid_celo_seed_overrides(seed_source);
CREATE INDEX IF NOT EXISTS idx_event_channels_guild ON event_channels(guild_id);
CREATE INDEX IF NOT EXISTS idx_event_reporter_roles_guild ON event_reporter_roles(guild_id);
CREATE INDEX IF NOT EXISTS idx_event_report_posts_event ON event_report_posts(event_id);
CREATE INDEX IF NOT EXISTS idx_event_report_posts_guild ON event_report_posts(guild_id);
CREATE INDEX IF NOT EXISTS idx_event_contests_event ON event_contests(event_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_event_contests_status ON event_contests(status, created_at DESC);
