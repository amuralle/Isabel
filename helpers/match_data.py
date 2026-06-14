import asyncio
from typing import Dict, List, Union

from aiohttp import ClientSession
from spnkr.tools import MEDAL_NAME_MAP

from helpers import db
from helpers import spnkr_auth


async def fetch_gamertag_from_api(xuid: str | int) -> str:
    async with ClientSession() as session:
        client = await spnkr_auth.get_authenticated_client(session)
        response = await client.profile.get_user_by_id(str(xuid))
        user_data = await response.parse()
        return user_data.gamertag


async def get_gamertag_from_xuid(xuid: str | int) -> str:
    return await db.get_or_create_gamertag(str(xuid), fetch_gamertag_from_api)


async def lookup_match_stats_by_id(match_id: str):
    async with ClientSession() as session:
        client = await spnkr_auth.get_authenticated_client(session)
        response = await client.stats.get_match_stats(match_id)
        return await response.parse()


async def refresh_halo_auth() -> None:
    async with ClientSession() as session:
        await spnkr_auth.get_authenticated_client(session)


async def lookup_match_by_id(match_id: str) -> Dict[str, Union[str, float]]:
    async with ClientSession() as session:
        client = await spnkr_auth.get_authenticated_client(session)
        response = await client.stats.get_match_stats(match_id)
        match = await response.parse()
        return await _match_metadata(client, match)


async def _match_metadata(client, match) -> Dict[str, Union[str, float]]:
    match_info = match.match_info

    async def fetch_map_name() -> str:
        try:
            map_resp = await client.discovery_ugc.get_map(
                match_info.map_variant.asset_id,
                match_info.map_variant.version_id,
            )
            map_data = await map_resp.parse()
            return map_data.public_name
        except Exception:
            return "Unknown"

    async def fetch_mode_name() -> str:
        try:
            mode_resp = await client.discovery_ugc.get_ugc_game_variant(
                match_info.ugc_game_variant.asset_id,
                match_info.ugc_game_variant.version_id,
            )
            mode_data = await mode_resp.parse()
            return mode_data.public_name
        except Exception:
            return "Unknown"

    map_name, mode_name = await asyncio.gather(fetch_map_name(), fetch_mode_name())
    return {
        "match_id": str(match.match_id),
        "start_time": str(match_info.start_time),
        "duration_minutes": round(match_info.duration.total_seconds() / 60, 2),
        "map_name": map_name,
        "mode_name": mode_name,
        "score_mode": match_info.game_variant_category.name,
    }


async def lookup_recent_matches(player: str, count: int = 5) -> List[Dict[str, Union[str, float]]]:
    async with ClientSession() as session:
        client = await spnkr_auth.get_authenticated_client(session)
        response = await client.stats.get_match_history(player, count=count)
        history = await response.parse()

        semaphore = asyncio.Semaphore(4)

        async def enrich(result) -> Dict[str, Union[str, float]]:
            async with semaphore:
                return await _match_metadata(client, result)

        return list(await asyncio.gather(*(enrich(result) for result in history.results)))


async def _resolve_gamertags(xuids: list[str]) -> dict[str, str | None]:
    semaphore = asyncio.Semaphore(6)

    async def resolve(xuid: str) -> tuple[str, str | None]:
        async with semaphore:
            try:
                return xuid, await db.get_or_create_gamertag(xuid, fetch_gamertag_from_api)
            except Exception:
                return xuid, None

    return dict(await asyncio.gather(*(resolve(xuid) for xuid in xuids)))


async def ingest_match_to_event(match_id: str, event_id: int, event_outcome: str) -> int:
    async with ClientSession() as session:
        client = await spnkr_auth.get_authenticated_client(session)
        response = await client.stats.get_match_stats(match_id)
        match_data = await response.parse()
        match_metadata = await _match_metadata(client, match_data)

    game_id = await db.log_game(
        event_id=event_id,
        match_id=match_id,
        outcome=event_outcome,
        start_time=str(match_data.match_info.start_time),
        duration=str(match_data.match_info.duration),
        map_name=match_metadata["map_name"],
        mode_name=match_metadata["mode_name"],
    )

    xuids = [
        player.player_id.replace("xuid(", "").replace(")", "")
        for player in match_data.players
    ]
    gamertags_by_xuid = await _resolve_gamertags(list(dict.fromkeys(xuids)))

    player_stats_list = []
    for player in match_data.players:
        xuid = player.player_id.replace("xuid(", "").replace(")", "")
        gamertag = gamertags_by_xuid.get(xuid)

        stats = player.player_team_stats[0].stats.core_stats
        if player.outcome.name and player.outcome.name == "DID_NOT_FINISH":
            p_outcome = "LOSS"
        else:
            p_outcome = player.outcome.name.upper() if player.outcome else "N/A"

        player_stats_list.append(
            {
                "xuid": xuid,
                "gamertag": gamertag,
                "team": str(player.last_team_id),
                "outcome": p_outcome,
                "kills": stats.kills,
                "deaths": stats.deaths,
                "assists": stats.assists,
                "damage_dealt": stats.damage_dealt,
            }
        )

    await db.log_player_game_stats(game_id, player_stats_list)
    await log_medals_for_match_data(game_id, match_data)
    return game_id


def build_player_medal_rows(match_data) -> list[dict]:
    medal_rows: list[dict] = []
    for player in match_data.players:
        xuid = player.player_id.replace("xuid(", "").replace(")", "")
        if not player.player_team_stats:
            continue

        for team_stats in player.player_team_stats:
            core_stats = team_stats.stats.core_stats
            for medal in getattr(core_stats, "medals", []) or []:
                medal_rows.append(
                    {
                        "xuid": xuid,
                        "medal_name_id": medal.name_id,
                        "medal_name": MEDAL_NAME_MAP.get(medal.name_id, str(medal.name_id)),
                        "count": medal.count,
                        "total_personal_score_awarded": medal.total_personal_score_awarded,
                    }
                )
    return medal_rows


async def log_medals_for_match_data(game_id: int, match_data) -> int:
    return await db.log_player_game_medals(
        game_id=game_id,
        medal_rows=build_player_medal_rows(match_data),
    )


async def backfill_medals_for_logged_games(limit: int = 50) -> dict:
    async with ClientSession() as session:
        client = await spnkr_auth.get_authenticated_client(session)
        games = await db.get_games_missing_medals(limit=limit)
        rows_written = 0
        failures: list[str] = []
        for game in games:
            try:
                response = await client.stats.get_match_stats(str(game["match_id"]))
                parsed = await response.parse()
                rows_written += await log_medals_for_match_data(int(game["id"]), parsed)
            except Exception as exc:
                failures.append(f"{game['match_id']}: {type(exc).__name__}")
        return {
            "games_seen": len(games),
            "rows_written": rows_written,
            "failures": failures,
        }
