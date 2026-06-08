import tempfile
import unittest
from pathlib import Path

import aiosqlite

from helpers import cortana_import
from helpers import db


async def _init_isabel_db(path: Path) -> None:
    async with aiosqlite.connect(path) as conn:
        with open(db.DB_SCHEMA_PATH, "r", encoding="utf-8") as handle:
            await conn.executescript(handle.read())
        await db._run_migrations(conn)
        await conn.execute(
            """
            INSERT INTO guild_registry (guild_id, guild_name, registered_by, is_active)
            VALUES ('guild-a', 'Guild A', 'coordinator', 1)
            """
        )
        await conn.commit()


async def _init_cortana_db(path: Path, *, match_ids: list[str]) -> None:
    async with aiosqlite.connect(path) as conn:
        await conn.executescript(
            """
            CREATE TABLE events (
                id INTEGER PRIMARY KEY,
                guild_id TEXT,
                category TEXT,
                coordinator_id TEXT,
                opponent TEXT,
                event_number INTEGER,
                outcome TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                opponent_id INTEGER
            );
            CREATE TABLE games (
                id INTEGER PRIMARY KEY,
                event_id INTEGER,
                match_id TEXT,
                outcome TEXT,
                start_time TEXT,
                duration TEXT,
                map_name TEXT,
                mode_name TEXT
            );
            CREATE TABLE player_game_stats (
                game_id INTEGER,
                xuid TEXT,
                gamertag TEXT,
                team TEXT,
                outcome TEXT,
                kills INTEGER,
                deaths INTEGER,
                assists INTEGER,
                damage_dealt INTEGER
            );
            CREATE TABLE player_game_medals (
                game_id INTEGER,
                xuid TEXT,
                medal_name_id INTEGER,
                medal_name TEXT,
                count INTEGER,
                total_personal_score_awarded INTEGER
            );
            CREATE TABLE xuids (
                xuid TEXT PRIMARY KEY,
                gamertag TEXT,
                discord_id TEXT
            );
            """
        )
        await conn.execute(
            """
            INSERT INTO events (
                id, guild_id, category, coordinator_id, opponent, event_number, outcome
            ) VALUES (10, 'cortana-guild', 'External', 'coord', 'Opponent', 1, 'Win')
            """
        )
        for idx, match_id in enumerate(match_ids, start=1):
            await conn.execute(
                """
                INSERT INTO games (id, event_id, match_id, outcome, map_name, mode_name)
                VALUES (?, 10, ?, 'Win', 'Map', 'Raid')
                """,
                (idx, match_id),
            )
            await conn.executemany(
                """
                INSERT INTO player_game_stats (
                    game_id, xuid, gamertag, team, outcome, kills, deaths, assists, damage_dealt
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (idx, f"x{idx}a", f"Player {idx}A", "0", "Win", 10, 5, 2, 3000),
                    (idx, f"x{idx}b", f"Player {idx}B", "1", "Loss", 5, 10, 1, 1800),
                ],
            )
        await conn.commit()


class CortanaImportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.isabel_path = Path(self.tmp.name) / "isabel.db"
        self.cortana_path = Path(self.tmp.name) / "cortana.db"
        self.original_db_path = db.DB_PATH
        db.DB_PATH = str(self.isabel_path)
        await _init_isabel_db(self.isabel_path)

    async def asyncTearDown(self):
        db.DB_PATH = self.original_db_path
        self.tmp.cleanup()

    async def test_duplicate_only_cortana_import_is_already_synced(self):
        await _init_cortana_db(self.cortana_path, match_ids=["dupe-1"])
        event_id, _, _ = await db.log_event(
            guild_id="guild-a",
            category="Raid",
            coordinator_id="coordinator",
            outcome="Win",
            opponent_guild_id="external:opponent",
            opponent_name="Opponent",
        )
        await db.log_game(event_id, "dupe-1", "Win", "", "", "Map", "Raid")

        result = await cortana_import.import_cortana_event(
            cortana_event_id=10,
            isabel_guild_id="guild-a",
            coordinator_id="coordinator",
            opponent_name="Opponent",
            cortana_db_path=self.cortana_path,
        )

        self.assertEqual(result["status"], "already_synced")
        self.assertEqual(result["games"], 0)
        self.assertEqual(result["duplicate_games"], 1)
        self.assertEqual(result["official"]["processed_games"], 0)

    async def test_mixed_cortana_import_skips_duplicates_and_imports_new_games(self):
        await _init_cortana_db(self.cortana_path, match_ids=["dupe-1", "new-1"])
        event_id, _, _ = await db.log_event(
            guild_id="guild-a",
            category="Raid",
            coordinator_id="coordinator",
            outcome="Win",
            opponent_guild_id="external:opponent",
            opponent_name="Opponent",
        )
        await db.log_game(event_id, "dupe-1", "Win", "", "", "Map", "Raid")

        result = await cortana_import.import_cortana_event(
            cortana_event_id=10,
            isabel_guild_id="guild-a",
            coordinator_id="coordinator",
            opponent_name="Opponent",
            cortana_db_path=self.cortana_path,
        )

        self.assertEqual(result["status"], "partial_import")
        self.assertEqual(result["games"], 1)
        self.assertEqual(result["duplicate_games"], 1)
        self.assertEqual(result["official"]["processed_games"], 1)


if __name__ == "__main__":
    unittest.main()
