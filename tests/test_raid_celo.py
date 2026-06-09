import unittest
import tempfile
from pathlib import Path

import aiosqlite

from analytics import replay_cortana_celo_models as replay
from helpers import db


class RaidParityTests(unittest.TestCase):
    def test_defender_team_zero_is_eagle(self):
        self.assertEqual(db.RAID_DEFENDER_TEAM, "0")
        self.assertEqual(db.RAID_DEFENDER_LABEL, "Eagle")
        self.assertEqual(replay.RAID_DEFENDER_TEAM, "0")
        self.assertEqual(replay.RAID_DEFENDER_LABEL, "Eagle")

    def test_parity_is_high_for_even_stat_split(self):
        parity = db._parity_from_shares([0.50, 0.52, 0.48, 0.51])
        self.assertGreaterEqual(parity, 0.95)

    def test_parity_is_low_for_one_sided_stat_split(self):
        parity = db._parity_from_shares([0.85, 0.80, 0.82, 0.20])
        self.assertLess(parity, 0.45)


class RaidModelTests(unittest.TestCase):
    def test_raid_models_are_visible(self):
        keys = {row["model_key"] for row in db.list_celo_model_configs()}
        self.assertIn("raid_flat", keys)
        self.assertIn("raid_stepwise", keys)

    def test_hidden_models_are_not_public_by_default(self):
        db.CELO_MODEL_CONFIGS["_historical_test"] = {
            "label": "Historical Test",
            "description": "Hidden historical training model.",
            "new_player_k": 40.0,
            "experienced_k": 24.0,
            "deviation_floor": 70.0,
            "deviation_decay": 0.98,
            "performance_shift": 0.0,
            "public": False,
        }
        try:
            public_keys = {row["model_key"] for row in db.list_celo_model_configs()}
            all_keys = {row["model_key"] for row in db.list_celo_model_configs(include_hidden=True)}
            self.assertNotIn("_historical_test", public_keys)
            self.assertIn("_historical_test", all_keys)
        finally:
            db.CELO_MODEL_CONFIGS.pop("_historical_test", None)

    def test_raid_stepwise_multiplier_is_capped(self):
        config = db.CELO_MODEL_CONFIGS["raid_stepwise"]
        high = db._performance_multiplier_for_model(20.0, 1.0, config)
        low = db._performance_multiplier_for_model(20.0, 0.0, config)
        self.assertLessEqual(high, 1.05)
        self.assertGreaterEqual(low, 0.95)

    def test_baseline_ignores_performance_score(self):
        config = db.CELO_MODEL_CONFIGS["baseline"]
        self.assertEqual(db._performance_multiplier_for_model(20.0, 1.0, config), 1.0)
        self.assertEqual(db._performance_multiplier_for_model(-20.0, 0.0, config), 1.0)


class CeloDisplayTests(unittest.TestCase):
    def test_tier_seed_scores_stay_stable(self):
        self.assertEqual(db.celo_score(db.tier_seed_rating("Low")), 1100)
        self.assertEqual(db.celo_score(db.tier_seed_rating("Mid")), 1400)
        self.assertEqual(db.celo_score(db.tier_seed_rating("Mid-High")), 1650)
        self.assertEqual(db.celo_score(db.tier_seed_rating("High")), 1900)

    def test_public_score_continues_above_old_ceiling(self):
        threshold_raw = db.raw_rating_from_score(db.CELO_SCORE_MAX)
        self.assertEqual(db.celo_score(threshold_raw), db.CELO_SCORE_MAX)
        self.assertGreater(db.celo_score(threshold_raw + 100), db.CELO_SCORE_MAX)
        self.assertGreater(db.celo_score(threshold_raw + 200), db.celo_score(threshold_raw + 100))

    def test_overcap_score_inverse_is_monotonic(self):
        self.assertGreater(db.raw_rating_from_score(2600), db.raw_rating_from_score(2500))

    def test_discord_user_celo_weights_by_games_played(self):
        summary = db._weighted_xuid_celo_summary([
            {
                "gamertag": "Primary",
                "rating": 1000.0,
                "deviation": 200.0,
                "games_played": 10,
                "wins": 5,
                "losses": 5,
                "draws": 0,
            },
            {
                "gamertag": "Frequent",
                "rating": 1300.0,
                "deviation": 100.0,
                "games_played": 30,
                "wins": 20,
                "losses": 8,
                "draws": 2,
            },
            {
                "gamertag": "Fresh Alt",
                "rating": 900.0,
                "deviation": 350.0,
                "games_played": 0,
                "wins": 0,
                "losses": 0,
                "draws": 0,
            },
        ])
        self.assertIsNotNone(summary)
        self.assertAlmostEqual(summary["rating"], 1225.0)
        self.assertAlmostEqual(summary["deviation"], 125.0)
        self.assertEqual(summary["games_played"], 40)
        self.assertEqual(summary["wins"], 25)
        self.assertEqual(summary["losses"], 13)
        self.assertEqual(summary["draws"], 2)
        self.assertEqual(summary["xuid_count"], 3)
        self.assertEqual(summary["active_xuid_count"], 2)
        self.assertEqual(summary["weight_basis"], "games_played")

    def test_discord_user_celo_equal_averages_seed_profiles_without_games(self):
        summary = db._weighted_xuid_celo_summary([
            {
                "gamertag": "Seed One",
                "rating": 900.0,
                "deviation": 350.0,
                "games_played": 0,
                "wins": 0,
                "losses": 0,
                "draws": 0,
            },
            {
                "gamertag": "Seed Two",
                "rating": 1100.0,
                "deviation": 250.0,
                "games_played": 0,
                "wins": 0,
                "losses": 0,
                "draws": 0,
            },
        ])
        self.assertIsNotNone(summary)
        self.assertAlmostEqual(summary["rating"], 1000.0)
        self.assertAlmostEqual(summary["deviation"], 300.0)
        self.assertEqual(summary["weight_basis"], "equal_seed")


class CeloSeedTests(unittest.IsolatedAsyncioTestCase):
    async def _memory_db(self):
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        with open(db.DB_SCHEMA_PATH, "r", encoding="utf-8") as handle:
            await conn.executescript(handle.read())
        await db._run_migrations(conn)
        return conn

    def test_unsc_branch_tier_mapping(self):
        self.assertEqual(db.unsc_branch_seed_tier("13th Marine Battalion"), "Low")
        self.assertEqual(db.unsc_branch_seed_tier("21st Space Assault Battalion"), "Mid")
        self.assertEqual(db.unsc_branch_seed_tier("SPARTAN-II Program"), "High")
        self.assertEqual(db.unsc_branch_seed_tier("SPARTAN-III Program"), "High")
        self.assertEqual(db.unsc_branch_seed_tier("SPARTAN-IV Program"), "High")
        self.assertIsNone(db.unsc_branch_seed_tier("UNMC Reserves"))

    async def test_seed_override_wins_before_lobby_average(self):
        conn = await self._memory_db()
        try:
            await conn.execute(
                """
                INSERT INTO xuid_celo_seed_overrides (
                    xuid, gamertag, seed_source, seed_tier, seed_rating
                ) VALUES ('target', 'Target', 'user_tier', 'High', ?)
                """,
                (db.tier_seed_rating("High"),),
            )
            await conn.execute(
                """
                INSERT INTO xuid_celo_seed_overrides (
                    xuid, gamertag, seed_source, seed_tier, seed_rating
                ) VALUES ('peer', 'Peer', 'user_tier', 'Low', ?)
                """,
                (db.tier_seed_rating("Low"),),
            )
            seed = await db._resolve_xuid_seed(conn, "target", "Target", lobby_xuids=["target", "peer"])
            self.assertEqual(seed["seed_source"], "user_tier")
            self.assertEqual(seed["seed_tier"], "High")
            self.assertAlmostEqual(seed["rating"], db.tier_seed_rating("High"))
        finally:
            await conn.close()

    async def test_first_seen_uses_seeded_lobby_average(self):
        conn = await self._memory_db()
        try:
            await conn.execute(
                """
                INSERT INTO xuid_celo_seed_overrides (
                    xuid, gamertag, seed_source, seed_tier, seed_rating
                ) VALUES ('peer', 'Peer', 'unsc_branch', 'Mid', ?)
                """,
                (db.tier_seed_rating("Mid"),),
            )
            seed = await db._resolve_xuid_seed(conn, "newbie", "Newbie", lobby_xuids=["newbie", "peer"], game_id=42)
            self.assertEqual(seed["seed_source"], "lobby_average")
            self.assertAlmostEqual(seed["rating"], db.tier_seed_rating("Mid"))
            self.assertIn("42", seed["seed_detail"])
        finally:
            await conn.close()

    async def test_first_seen_without_seeded_lobby_uses_default(self):
        conn = await self._memory_db()
        try:
            seed = await db._resolve_xuid_seed(conn, "newbie", "Newbie", lobby_xuids=["newbie", "unknown"])
            self.assertEqual(seed["seed_source"], "default")
            self.assertAlmostEqual(seed["rating"], db.CELO_RAW_CENTER)
        finally:
            await conn.close()

    async def test_roster_membership_sync_preserves_existing_seed_override(self):
        original_path = db.DB_PATH
        tmp = tempfile.TemporaryDirectory()
        path = Path(tmp.name) / "isabel.db"
        try:
            async with aiosqlite.connect(path) as conn:
                with open(db.DB_SCHEMA_PATH, "r", encoding="utf-8") as handle:
                    await conn.executescript(handle.read())
                await db._run_migrations(conn)
                await conn.execute(
                    """
                    INSERT INTO guild_registry (guild_id, guild_name, registered_by, is_active)
                    VALUES ('unsc', 'UNSC', 'admin', 1)
                    """
                )
                await conn.execute(
                    """
                    INSERT INTO xuid_celo_seed_overrides (
                        xuid, gamertag, seed_source, seed_tier, seed_rating
                    ) VALUES ('x1', 'Marine', 'unsc_branch', 'Low', ?)
                    """,
                    (db.tier_seed_rating("Low"),),
                )
                await conn.commit()

            db.DB_PATH = str(path)
            await db.upsert_clan_roster_membership_xuid(
                guild_id="unsc",
                xuid="x1",
                gamertag="Marine",
                tier="Low",
                registered_by="admin",
                discord_id="discord-1",
                set_allegiance_flag=True,
            )

            async with aiosqlite.connect(path) as conn:
                conn.row_factory = aiosqlite.Row
                seed = await conn.execute_fetchall(
                    """
                    SELECT seed_source, seed_tier, seed_rating
                    FROM xuid_celo_seed_overrides
                    WHERE xuid = 'x1'
                    """
                )
                roster = await conn.execute_fetchall(
                    """
                    SELECT guild_id, tier, discord_id
                    FROM clan_roster_xuids
                    WHERE xuid = 'x1'
                    """
                )
                allegiance = await conn.execute_fetchall(
                    """
                    SELECT guild_id
                    FROM user_allegiances
                    WHERE discord_id = 'discord-1'
                    """
                )
            self.assertEqual(seed[0]["seed_source"], "unsc_branch")
            self.assertEqual(seed[0]["seed_tier"], "Low")
            self.assertAlmostEqual(seed[0]["seed_rating"], db.tier_seed_rating("Low"))
            self.assertEqual(roster[0]["guild_id"], "unsc")
            self.assertEqual(roster[0]["tier"], "Low")
            self.assertEqual(roster[0]["discord_id"], "discord-1")
            self.assertEqual(allegiance[0]["guild_id"], "unsc")
        finally:
            db.DB_PATH = original_path
            tmp.cleanup()

    async def test_upsert_xuid_refreshes_related_gamertag_copies(self):
        original_path = db.DB_PATH
        tmp = tempfile.TemporaryDirectory()
        path = Path(tmp.name) / "isabel.db"
        try:
            async with aiosqlite.connect(path) as conn:
                with open(db.DB_SCHEMA_PATH, "r", encoding="utf-8") as handle:
                    await conn.executescript(handle.read())
                await db._run_migrations(conn)
                await conn.execute(
                    """
                    INSERT INTO guild_registry (guild_id, guild_name, registered_by, is_active)
                    VALUES ('guild-1', 'Guild One', 'admin', 1)
                    """
                )
                await conn.execute(
                    """
                    INSERT INTO users (discord_id)
                    VALUES ('user-1')
                    """
                )
                await conn.execute(
                    """
                    INSERT INTO xuids (xuid, gamertag, discord_id)
                    VALUES ('x1', 'Old Tag', 'user-1')
                    """
                )
                await conn.execute(
                    """
                    INSERT INTO clan_roster_xuids (guild_id, xuid, gamertag, registered_by, discord_id)
                    VALUES ('guild-1', 'x1', 'Old Tag', 'admin', 'user-1')
                    """
                )
                await conn.execute(
                    """
                    INSERT INTO xuid_celo_profiles (xuid, gamertag)
                    VALUES ('x1', 'Old Tag')
                    """
                )
                await conn.execute(
                    """
                    INSERT INTO xuid_celo_model_profiles (model_key, xuid, gamertag)
                    VALUES ('official', 'x1', 'Old Tag')
                    """
                )
                await conn.execute(
                    """
                    INSERT INTO xuid_celo_seed_overrides (xuid, gamertag, seed_source)
                    VALUES ('x1', 'Old Tag', 'user_tier')
                    """
                )
                await conn.commit()

            db.DB_PATH = str(path)
            await db.upsert_xuid("x1", "New Tag", "user-1")

            async with aiosqlite.connect(path) as conn:
                conn.row_factory = aiosqlite.Row
                xuid_row = (await conn.execute_fetchall("SELECT gamertag FROM xuids WHERE xuid = 'x1'"))[0]
                roster_row = (await conn.execute_fetchall("SELECT gamertag FROM clan_roster_xuids WHERE xuid = 'x1'"))[0]
                profile_row = (await conn.execute_fetchall("SELECT gamertag FROM xuid_celo_profiles WHERE xuid = 'x1'"))[0]
                model_row = (await conn.execute_fetchall("SELECT gamertag FROM xuid_celo_model_profiles WHERE xuid = 'x1'"))[0]
                seed_row = (await conn.execute_fetchall("SELECT gamertag FROM xuid_celo_seed_overrides WHERE xuid = 'x1'"))[0]

            self.assertEqual(xuid_row["gamertag"], "New Tag")
            self.assertEqual(roster_row["gamertag"], "New Tag")
            self.assertEqual(profile_row["gamertag"], "New Tag")
            self.assertEqual(model_row["gamertag"], "New Tag")
            self.assertEqual(seed_row["gamertag"], "New Tag")
        finally:
            db.DB_PATH = original_path
            tmp.cleanup()


class ReplayMathTests(unittest.TestCase):
    def test_expected_score_is_symmetric(self):
        a = replay.expected_score(1100.0, 1000.0)
        b = replay.expected_score(1000.0, 1100.0)
        self.assertAlmostEqual(a + b, 1.0, places=6)

    def test_team_performance_scores_are_bounded(self):
        scores = replay.team_performance_scores(
            {
                "a": {"kills": 10, "deaths": 5, "assists": 4, "damage_dealt": 4000},
                "b": {"kills": 2, "deaths": 12, "assists": 1, "damage_dealt": 1000},
            }
        )
        self.assertEqual(set(scores), {"a", "b"})
        for value in scores.values():
            self.assertGreaterEqual(value, 0.0)
            self.assertLessEqual(value, 1.0)


if __name__ == "__main__":
    unittest.main()
