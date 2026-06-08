#!/usr/bin/env python3
"""
Discover historical raid candidates from high-volume player match histories.

This is analytics-only. It does not write to Isabel's live event tables and does
not affect public CELO, career totals, or user-facing reports.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import re
import sqlite3
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aiohttp import ClientResponseError, ClientSession


ISABEL_ROOT = Path(__file__).resolve().parents[1]
if str(ISABEL_ROOT) not in sys.path:
    sys.path.insert(0, str(ISABEL_ROOT))

from helpers import spnkr_auth  # noqa: E402

DEFAULT_DB = ISABEL_ROOT / "database" / "isabel.db"
DEFAULT_OUTDIR = ISABEL_ROOT / "analytics" / "output" / "historical_raids"
DEFAULT_CUTOFF = "2023-01-01T00:00:00+00:00"
MAX_HISTORY_COUNT = 25
MAP_NAME_CACHE: dict[tuple[str, str], str] = {}
MODE_NAME_CACHE: dict[tuple[str, str], str] = {}
API_SEMAPHORE: asyncio.Semaphore | None = None
KNOWN_RAID_MODE_NAMES = {
    "avalon raid",
    "panthea official raid gametype",
    "raid evo",
    "raid type",
    "resilience official gametype",
    "unsc",
    "unsc raid",
    "unsc raid outlines",
    "vanguard raid official",
}
EXCLUDED_RAID_MODE_PREFIXES = {
    "husky raid",
}
KNOWN_NON_RAID_MODE_NAMES = {
    "aimbotz",
    "assault multi bomb btb",
    "assault neutral bomb squad",
    "assault one bomb btb",
    "avalon blitz",
    "blitz evo",
    "blitz nh evo",
    "btb fiesta ctf",
    "btb multiflag lan",
    "btb ctf",
    "btb one flag lan",
    "btb one flag ctf",
    "btb one flag v1 8",
    "btb sentry defense",
    "btb heavies slayer",
    "crucible",
    "ctf btb",
    "ctf btb fiesta",
    "ctf btb heavies",
    "day 2",
    "deadly seas",
    "df endgame",
    "elimination arena",
    "evny s assassination ninjas",
    "gladiator fiesta",
    "hefr aimbotz unlimited",
    "operation green furnace",
    "parkour",
    "pnt blitz gametype",
    "slayer arena",
    "squad ctf",
    "squad ctf 3 captures",
    "squad king of the hill",
    "squad one flag ctf",
    "tank battle",
    "total control btb",
    "total control btb fiesta",
    "weapon combos slayer",
}


def normalize_mode_name(mode_name: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", str(mode_name or "").lower()).strip()
    return " ".join(normalized.split())


def is_excluded_raid_mode(normalized_mode: str) -> bool:
    return normalized_mode in KNOWN_NON_RAID_MODE_NAMES or any(
        normalized_mode.startswith(prefix) for prefix in EXCLUDED_RAID_MODE_PREFIXES
    )


def excluded_raid_reason(normalized_mode: str) -> str:
    if normalized_mode in KNOWN_NON_RAID_MODE_NAMES:
        return "excluded_known_non_raid_mode"
    return "excluded_husky_raid"


def is_raid_gametype(normalized_mode: str) -> bool:
    if is_excluded_raid_mode(normalized_mode):
        return False
    return "raid" in normalized_mode or normalized_mode in KNOWN_RAID_MODE_NAMES


async def api_call(factory, *, attempts: int = 8):
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            if API_SEMAPHORE is None:
                return await factory()
            async with API_SEMAPHORE:
                return await factory()
        except ClientResponseError as exc:
            last_exc = exc
            if exc.status != 429 or attempt >= attempts - 1:
                raise
            retry_after = 0.0
            if exc.headers:
                try:
                    retry_after = float(exc.headers.get("Retry-After", "0") or 0)
                except ValueError:
                    retry_after = 0.0
            await asyncio.sleep(max(retry_after, 15.0 * (attempt + 1)))
    if last_exc:
        raise last_exc
    raise RuntimeError("API call failed without an exception.")


@dataclass
class CandidateMatch:
    match_id: str
    source_player: str
    source_gamertag: str
    source_xuid: str
    source_team: str
    start_time: str
    duration_minutes: float
    map_name: str
    mode_name: str
    score_mode: str
    match_type: str
    player_count: int | None
    team_count: int | None
    defender_team: str | None
    defender_players: int | None
    attacker_players: int | None
    defender_kill_share: float | None
    defender_damage_share: float | None
    raid_score: int
    raid_bucket: str
    reasons: str
    participant_xuids: str
    teammate_xuids: str
    opponent_xuids: str
    already_logged: bool


def parse_dt(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def existing_match_ids(db_path: Path) -> set[str]:
    if not db_path.exists():
        return set()
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute("SELECT match_id FROM games").fetchall()
    except sqlite3.Error:
        return set()
    finally:
        con.close()
    return {str(row[0]) for row in rows if row and row[0]}


async def resolve_map_name(client, match_info) -> str:
    key = (
        str(getattr(match_info.map_variant, "asset_id", "")),
        str(getattr(match_info.map_variant, "version_id", "")),
    )
    if key in MAP_NAME_CACHE:
        return MAP_NAME_CACHE[key]
    try:
        map_resp = await api_call(
            lambda: client.discovery_ugc.get_map(
                match_info.map_variant.asset_id,
                match_info.map_variant.version_id,
            )
        )
        map_data = await map_resp.parse()
        name = str(map_data.public_name or "Unknown")
    except Exception:
        name = "Unknown"
    MAP_NAME_CACHE[key] = name
    return name


async def resolve_mode_name(client, match_info) -> str:
    key = (
        str(getattr(match_info.ugc_game_variant, "asset_id", "")),
        str(getattr(match_info.ugc_game_variant, "version_id", "")),
    )
    if key in MODE_NAME_CACHE:
        return MODE_NAME_CACHE[key]
    try:
        mode_resp = await api_call(
            lambda: client.discovery_ugc.get_ugc_game_variant(
                match_info.ugc_game_variant.asset_id,
                match_info.ugc_game_variant.version_id,
            )
        )
        mode_data = await mode_resp.parse()
        name = str(mode_data.public_name or "Unknown")
    except Exception:
        name = "Unknown"
    MODE_NAME_CACHE[key] = name
    return name


def player_xuid(player) -> str:
    return str(player.player_id).replace("xuid(", "").replace(")", "")


def safe_outcome(player) -> str:
    try:
        return str(player.outcome.name).upper()
    except Exception:
        return ""


def stat_rows_from_match(match_data) -> list[dict[str, Any]]:
    rows = []
    for player in getattr(match_data, "players", []) or []:
        if not getattr(player, "player_team_stats", None):
            continue
        stats = player.player_team_stats[0].stats.core_stats
        rows.append(
            {
                "xuid": player_xuid(player),
                "team": str(getattr(player, "last_team_id", "")),
                "outcome": safe_outcome(player),
                "kills": int(getattr(stats, "kills", 0) or 0),
                "deaths": int(getattr(stats, "deaths", 0) or 0),
                "assists": int(getattr(stats, "assists", 0) or 0),
                "damage_dealt": int(getattr(stats, "damage_dealt", 0) or 0),
            }
        )
    return rows


async def resolve_player_identity(client, player: str) -> tuple[str, str]:
    cleaned = str(player).strip()
    if cleaned.isdigit():
        try:
            response = await api_call(lambda: client.profile.get_user_by_id(cleaned))
            user = await response.parse()
            return str(user.xuid), str(user.gamertag)
        except Exception:
            return cleaned, cleaned
    try:
        response = await api_call(lambda: client.profile.get_user_by_gamertag(cleaned))
        user = await response.parse()
        return str(user.xuid), str(user.gamertag)
    except Exception:
        return cleaned, cleaned


def split_participants_by_source_team(stats: list[dict[str, Any]], source_xuid: str) -> tuple[str, str, str]:
    source_team = ""
    for row in stats:
        if str(row.get("xuid")) == str(source_xuid):
            source_team = str(row.get("team") or "")
            break
    if not source_team:
        return "", "", ""

    teammates = []
    opponents = []
    for row in stats:
        xuid = str(row.get("xuid") or "")
        if not xuid or xuid == str(source_xuid):
            continue
        if str(row.get("team") or "") == source_team:
            teammates.append(xuid)
        else:
            opponents.append(xuid)
    return source_team, ",".join(sorted(set(teammates))), ",".join(sorted(set(opponents)))


def score_raid_candidate(
    *,
    map_name: str,
    mode_name: str,
    score_mode: str,
    duration_minutes: float,
    stats: list[dict[str, Any]] | None,
) -> tuple[int, str, str]:
    score = 0
    reasons: list[str] = []
    haystack = f"{map_name} {mode_name} {score_mode}".lower()
    normalized_mode = normalize_mode_name(mode_name)

    if is_excluded_raid_mode(normalized_mode):
        return 0, "not_raid", excluded_raid_reason(normalized_mode)

    if "raid" in haystack:
        score += 4
        reasons.append("name_contains_raid")
    if is_raid_gametype(normalized_mode):
        score = max(score, 7)
        reasons.append("raid_gametype")
    if any(token in haystack for token in ("unsc", "outpost", "base", "invasion")):
        score += 1
        reasons.append("raidish_name_token")
    if duration_minutes >= 8:
        score += 1
        reasons.append("duration_8m_plus")
    if duration_minutes >= 12:
        score += 1
        reasons.append("duration_12m_plus")

    if stats is not None:
        player_count = len({row["xuid"] for row in stats})
        teams = {row["team"] for row in stats if row["team"] != ""}
        if player_count > 8:
            score += 2
            reasons.append("player_count_gt_8")
        if player_count >= 12:
            score += 1
            reasons.append("player_count_12_plus")
        if len(teams) == 2:
            score += 1
            reasons.append("two_teams")

    if score >= 7:
        bucket = "likely_raid"
    elif score >= 4:
        bucket = "possible_raid"
    else:
        bucket = "not_raid"
    return score, bucket, ",".join(reasons)


def is_raidish_metadata(map_name: str, mode_name: str, score_mode: str, duration_minutes: float) -> bool:
    haystack = f"{map_name} {mode_name} {score_mode}".lower()
    if "raid" in haystack:
        return True
    if any(token in haystack for token in ("unsc", "outpost", "firebase", "base", "warband")):
        return True
    if duration_minutes >= 12 and not any(token in haystack for token in ("ranked", "hcs:ffa", "ffa slayer")):
        return True
    return False


def raid_stat_shape(stats: list[dict[str, Any]]) -> dict[str, Any]:
    defender_team = "0"
    defender = {"players": set(), "kills": 0, "damage": 0}
    attackers = {"players": set(), "kills": 0, "damage": 0}
    teams = set()
    for row in stats:
        teams.add(str(row["team"]))
        target = defender if str(row["team"]) == defender_team else attackers
        target["players"].add(str(row["xuid"]))
        target["kills"] += int(row["kills"] or 0)
        target["damage"] += int(row["damage_dealt"] or 0)

    total_kills = defender["kills"] + attackers["kills"]
    total_damage = defender["damage"] + attackers["damage"]
    return {
        "player_count": len({str(row["xuid"]) for row in stats}),
        "team_count": len({team for team in teams if team != ""}),
        "defender_team": defender_team,
        "defender_players": len(defender["players"]),
        "attacker_players": len(attackers["players"]),
        "defender_kill_share": round(defender["kills"] / total_kills, 6) if total_kills else None,
        "defender_damage_share": round(defender["damage"] / total_damage, 6) if total_damage else None,
    }


async def history_page(client, player: str, start: int, match_type: str):
    response = await api_call(
        lambda: client.stats.get_match_history(player, start=start, count=MAX_HISTORY_COUNT, match_type=match_type)
    )
    return await response.parse()


async def build_candidate(
    client,
    *,
    result,
    source_player: str,
    source_gamertag: str,
    source_xuid: str,
    match_type: str,
    existing_ids: set[str],
    inspect_stats_mode: str,
) -> CandidateMatch:
    match_info = result.match_info
    match_id = str(result.match_id)
    map_name, mode_name = await asyncio.gather(
        resolve_map_name(client, match_info),
        resolve_mode_name(client, match_info),
    )
    duration_minutes = round(match_info.duration.total_seconds() / 60, 2)
    score_mode = str(match_info.game_variant_category.name)
    stats = None
    participant_xuids = ""
    teammate_xuids = ""
    opponent_xuids = ""
    source_team = ""
    shape = {
        "player_count": None,
        "team_count": None,
        "defender_team": None,
        "defender_players": None,
        "attacker_players": None,
        "defender_kill_share": None,
        "defender_damage_share": None,
    }
    should_inspect = inspect_stats_mode == "all" or (
        inspect_stats_mode == "raidish"
        and is_raidish_metadata(map_name, mode_name, score_mode, duration_minutes)
    )
    if should_inspect:
        response = await api_call(lambda: client.stats.get_match_stats(match_id))
        match_data = await response.parse()
        stats = stat_rows_from_match(match_data)
        shape = raid_stat_shape(stats)
        participant_xuids = ",".join(sorted({str(row["xuid"]) for row in stats if row.get("xuid")}))
        source_team, teammate_xuids, opponent_xuids = split_participants_by_source_team(stats, source_xuid)

    raid_score, raid_bucket, reasons = score_raid_candidate(
        map_name=map_name,
        mode_name=mode_name,
        score_mode=score_mode,
        duration_minutes=duration_minutes,
        stats=stats,
    )
    return CandidateMatch(
        match_id=match_id,
        source_player=source_player,
        source_gamertag=source_gamertag,
        source_xuid=source_xuid,
        source_team=source_team,
        start_time=str(match_info.start_time),
        duration_minutes=duration_minutes,
        map_name=map_name,
        mode_name=mode_name,
        score_mode=score_mode,
        match_type=match_type,
        player_count=shape["player_count"],
        team_count=shape["team_count"],
        defender_team=shape["defender_team"],
        defender_players=shape["defender_players"],
        attacker_players=shape["attacker_players"],
        defender_kill_share=shape["defender_kill_share"],
        defender_damage_share=shape["defender_damage_share"],
        raid_score=raid_score,
        raid_bucket=raid_bucket,
        reasons=reasons,
        participant_xuids=participant_xuids,
        teammate_xuids=teammate_xuids,
        opponent_xuids=opponent_xuids,
        already_logged=match_id in existing_ids,
    )


async def crawl_player(
    client,
    *,
    player: str,
    cutoff: datetime,
    max_pages: int,
    match_type: str,
    existing_ids: set[str],
    inspect_stats_mode: str,
) -> list[CandidateMatch]:
    candidates: list[CandidateMatch] = []
    seen_ids: set[str] = set()
    source_xuid, source_gamertag = await resolve_player_identity(client, player)

    for page_idx in range(max_pages):
        start = page_idx * MAX_HISTORY_COUNT
        page = await history_page(client, player, start, match_type)
        results = list(getattr(page, "results", []) or [])
        if not results:
            break

        stop_for_cutoff = False
        for result in results:
            match_info = result.match_info
            start_dt = parse_dt(str(match_info.start_time))
            if start_dt and start_dt < cutoff:
                stop_for_cutoff = True
                continue

            match_id = str(result.match_id)
            if match_id in seen_ids:
                continue
            seen_ids.add(match_id)
            candidates.append(
                await build_candidate(
                    client,
                    result=result,
                    source_player=player,
                    source_gamertag=source_gamertag,
                    source_xuid=source_xuid,
                    match_type=match_type,
                    existing_ids=existing_ids,
                    inspect_stats_mode=inspect_stats_mode,
                )
            )

        if stop_for_cutoff or len(results) < MAX_HISTORY_COUNT:
            break

    return candidates


def expansion_xuids_for_row(row: CandidateMatch, frontier_side: str) -> list[str]:
    if frontier_side == "opponents":
        raw = row.opponent_xuids
    elif frontier_side == "teammates":
        raw = row.teammate_xuids
    else:
        raw = row.participant_xuids
    return [item.strip() for item in raw.split(",") if item.strip()]


def participant_graph_rows(
    rows: list[CandidateMatch],
    *,
    expand_buckets: set[str],
    crawled: set[str],
    frontier_side: str,
) -> list[dict[str, Any]]:
    player_matches: dict[str, set[str]] = defaultdict(set)
    player_likely_matches: dict[str, set[str]] = defaultdict(set)
    player_possible_matches: dict[str, set[str]] = defaultdict(set)
    player_opponent_matches: dict[str, set[str]] = defaultdict(set)
    player_teammate_matches: dict[str, set[str]] = defaultdict(set)
    player_sources: dict[str, set[str]] = defaultdict(set)
    player_maps: dict[str, set[str]] = defaultdict(set)
    player_modes: dict[str, set[str]] = defaultdict(set)

    for row in rows:
        if row.raid_bucket not in expand_buckets:
            continue
        opponent_set = set(expansion_xuids_for_row(row, "opponents"))
        teammate_set = set(expansion_xuids_for_row(row, "teammates"))
        for xuid in expansion_xuids_for_row(row, frontier_side):
            cleaned = xuid.strip()
            if not cleaned:
                continue
            player_matches[cleaned].add(row.match_id)
            if row.raid_bucket == "likely_raid":
                player_likely_matches[cleaned].add(row.match_id)
            elif row.raid_bucket == "possible_raid":
                player_possible_matches[cleaned].add(row.match_id)
            if cleaned in opponent_set:
                player_opponent_matches[cleaned].add(row.match_id)
            if cleaned in teammate_set:
                player_teammate_matches[cleaned].add(row.match_id)
            player_sources[cleaned].add(row.source_player)
            player_maps[cleaned].add(row.map_name)
            player_modes[cleaned].add(row.mode_name)

    graph_rows = []
    for xuid, match_ids in player_matches.items():
        graph_rows.append(
            {
                "xuid": xuid,
                "raid_overlap_matches": len(match_ids),
                "likely_raid_overlap_matches": len(player_likely_matches[xuid]),
                "possible_raid_overlap_matches": len(player_possible_matches[xuid]),
                "opponent_raid_overlap_matches": len(player_opponent_matches[xuid]),
                "teammate_raid_overlap_matches": len(player_teammate_matches[xuid]),
                "source_players_seen_with": len(player_sources[xuid]),
                "source_players": ",".join(sorted(player_sources[xuid])),
                "maps_seen": len(player_maps[xuid]),
                "modes_seen": len(player_modes[xuid]),
                "already_crawled": xuid in crawled,
            }
        )
    graph_rows.sort(
        key=lambda row: (
            -int(row["opponent_raid_overlap_matches"]),
            -int(row["likely_raid_overlap_matches"]),
            -int(row["raid_overlap_matches"]),
            -int(row["source_players_seen_with"]),
            str(row["xuid"]),
        )
    )
    return graph_rows


def aggregate_graph_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        xuid = str(row["xuid"])
        payload = grouped.setdefault(
            xuid,
            {
                "xuid": xuid,
                "depth_first_seen": row["depth_discovered"],
                "raid_overlap_matches": 0,
                "likely_raid_overlap_matches": 0,
                "possible_raid_overlap_matches": 0,
                "opponent_raid_overlap_matches": 0,
                "teammate_raid_overlap_matches": 0,
                "source_players": set(),
                "maps_seen": 0,
                "modes_seen": 0,
                "already_crawled": False,
            },
        )
        payload["depth_first_seen"] = min(int(payload["depth_first_seen"]), int(row["depth_discovered"]))
        payload["raid_overlap_matches"] += int(row["raid_overlap_matches"])
        payload["likely_raid_overlap_matches"] += int(row["likely_raid_overlap_matches"])
        payload["possible_raid_overlap_matches"] += int(row["possible_raid_overlap_matches"])
        payload["opponent_raid_overlap_matches"] += int(row.get("opponent_raid_overlap_matches", 0))
        payload["teammate_raid_overlap_matches"] += int(row.get("teammate_raid_overlap_matches", 0))
        payload["source_players"].update(item for item in str(row.get("source_players", "")).split(",") if item)
        payload["maps_seen"] = max(int(payload["maps_seen"]), int(row["maps_seen"]))
        payload["modes_seen"] = max(int(payload["modes_seen"]), int(row["modes_seen"]))
        payload["already_crawled"] = bool(payload["already_crawled"]) or bool(row["already_crawled"])

    output = []
    for payload in grouped.values():
        source_players = sorted(payload.pop("source_players"))
        payload["source_players_seen_with"] = len(source_players)
        payload["source_players"] = ",".join(source_players)
        output.append(payload)
    output.sort(
        key=lambda row: (
            -int(row["opponent_raid_overlap_matches"]),
            -int(row["likely_raid_overlap_matches"]),
            -int(row["raid_overlap_matches"]),
            -int(row["source_players_seen_with"]),
            int(row["depth_first_seen"]),
            str(row["xuid"]),
        )
    )
    return output


def participant_frontier(
    rows: list[CandidateMatch],
    *,
    expand_buckets: set[str],
    crawled: set[str],
    frontier_side: str,
) -> list[str]:
    graph_rows = participant_graph_rows(
        rows,
        expand_buckets=expand_buckets,
        crawled=crawled,
        frontier_side=frontier_side,
    )
    return [str(row["xuid"]) for row in graph_rows if not row["already_crawled"]]


async def crawl_breadth(
    client,
    *,
    seeds: list[str],
    cutoff: datetime,
    max_pages: int,
    match_type: str,
    existing_ids: set[str],
    inspect_stats_mode: str,
    breadth_depth: int,
    max_breadth_players: int,
    expand_buckets: set[str],
    frontier_side: str,
) -> tuple[list[CandidateMatch], list[dict[str, Any]], list[dict[str, Any]]]:
    all_rows: list[CandidateMatch] = []
    expansion_rows: list[dict[str, Any]] = []
    graph_rows: list[dict[str, Any]] = []
    crawled: set[str] = set()
    frontier = list(dict.fromkeys(seeds))

    for depth in range(max(0, breadth_depth) + 1):
        current = [player for player in frontier if player not in crawled]
        if depth > 0:
            current = current[: max(0, max_breadth_players)]
        if not current:
            break

        nested = await asyncio.gather(
            *[
                crawl_player(
                    client,
                    player=player,
                    cutoff=cutoff,
                    max_pages=max_pages,
                    match_type=match_type,
                    existing_ids=existing_ids,
                    inspect_stats_mode=inspect_stats_mode,
                )
                for player in current
            ]
        )
        crawled.update(current)
        depth_rows = [row for rows in nested for row in rows]
        all_rows.extend(depth_rows)

        found_graph = participant_graph_rows(
            depth_rows,
            expand_buckets=expand_buckets,
            crawled=crawled,
            frontier_side=frontier_side,
        )
        for graph_row in found_graph:
            graph_rows.append({"depth_discovered": depth, **graph_row})
        found_frontier = [str(row["xuid"]) for row in found_graph if not row["already_crawled"]]
        next_frontier = [player for player in found_frontier if player not in crawled]
        expansion_rows.append(
            {
                "depth": depth,
                "players_crawled": len(current),
                "candidate_matches": len(depth_rows),
                "likely_raid_matches": sum(1 for row in depth_rows if row.raid_bucket == "likely_raid"),
                "possible_raid_matches": sum(1 for row in depth_rows if row.raid_bucket == "possible_raid"),
                "next_frontier_players": len(next_frontier),
                "crawled_players": ",".join(current),
                "next_frontier_top": ",".join(next_frontier[:25]),
            }
        )
        frontier = next_frontier

    return all_rows, expansion_rows, graph_rows


def write_markdown(
    path: Path,
    rows: list[CandidateMatch],
    expansion_rows: list[dict[str, Any]],
    args: argparse.Namespace,
) -> None:
    by_bucket: dict[str, int] = {}
    for row in rows:
        by_bucket[row.raid_bucket] = by_bucket.get(row.raid_bucket, 0) + 1

    lines = [
        "# Historical Raid Candidate Crawl",
        "",
        "Analytics-only. These matches are not public Isabel events and do not affect user-facing ratings.",
        "",
        f"- Players: `{', '.join(args.players)}`",
        f"- Cutoff: `{args.cutoff}`",
        f"- Match type: `{args.match_type}`",
        f"- Pages per player: `{args.max_pages}`",
        f"- Full stat inspection mode: `{args.inspect_stats_mode}`",
        f"- Breadth depth: `{args.breadth_depth}`",
        f"- Expand buckets: `{args.expand_buckets}`",
        f"- Frontier side: `{args.frontier_side}`",
        f"- Total candidates: `{len(rows)}`",
        "",
        "## Buckets",
        "",
    ]
    for bucket in ("likely_raid", "possible_raid", "not_raid"):
        lines.append(f"- `{bucket}`: `{by_bucket.get(bucket, 0)}`")

    if expansion_rows:
        lines.extend(
            [
                "",
                "## Breadth Expansion",
                "",
                "| Depth | Players Crawled | Candidate Matches | Likely Raids | Possible Raids | Next Frontier |",
                "|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for row in expansion_rows:
            lines.append(
                f"| {row['depth']} | {row['players_crawled']} | {row['candidate_matches']} | "
                f"{row['likely_raid_matches']} | {row['possible_raid_matches']} | {row['next_frontier_players']} |"
            )
        top_frontier = str(expansion_rows[-1].get("next_frontier_top", "")).split(",") if expansion_rows else []
        top_frontier = [item for item in top_frontier if item]
        if top_frontier:
            lines.extend(
                [
                    "",
                    "Top next frontier XUIDs by raid overlap:",
                    "",
                    ", ".join(f"`{item}`" for item in top_frontier[:25]),
                ]
            )

    lines.extend(
        [
            "",
            "## Top Candidates",
            "",
            "| Bucket | Score | Date | Map | Mode | Players | Logged | Match ID | Reasons |",
            "|---|---:|---|---|---|---:|---|---|---|",
        ]
    )
    ordered = sorted(rows, key=lambda item: (-item.raid_score, item.start_time))
    for row in ordered[:50]:
        logged = "yes" if row.already_logged else "no"
        players = "" if row.player_count is None else str(row.player_count)
        lines.append(
            f"| {row.raid_bucket} | {row.raid_score} | {row.start_time[:10]} | "
            f"{row.map_name} | {row.mode_name} | {players} | {logged} | `{row.match_id}` | {row.reasons} |"
        )
    path.write_text("\n".join(lines), encoding="utf-8")


async def async_main(args: argparse.Namespace) -> int:
    cutoff = parse_dt(args.cutoff)
    if cutoff is None:
        raise SystemExit(f"Invalid cutoff datetime: {args.cutoff}")

    existing_ids = existing_match_ids(args.db)
    args.outdir.mkdir(parents=True, exist_ok=True)
    global API_SEMAPHORE
    API_SEMAPHORE = asyncio.Semaphore(max(1, int(args.concurrency)))

    async with ClientSession() as session:
        client = await spnkr_auth.get_authenticated_client(session)
        if args.breadth_depth > 0:
            rows, expansion_rows, graph_rows = await crawl_breadth(
                client,
                seeds=args.players,
                cutoff=cutoff,
                max_pages=max(1, int(args.max_pages)),
                match_type=args.match_type,
                existing_ids=existing_ids,
                inspect_stats_mode=args.inspect_stats_mode,
                breadth_depth=max(0, int(args.breadth_depth)),
                max_breadth_players=max(1, int(args.max_breadth_players)),
                expand_buckets={bucket.strip() for bucket in args.expand_buckets.split(",") if bucket.strip()},
                frontier_side=args.frontier_side,
            )
        else:
            tasks = [
                crawl_player(
                    client,
                    player=player,
                    cutoff=cutoff,
                    max_pages=max(1, int(args.max_pages)),
                    match_type=args.match_type,
                    existing_ids=existing_ids,
                    inspect_stats_mode=args.inspect_stats_mode,
                )
                for player in args.players
            ]
            nested = await asyncio.gather(*tasks)
            rows = [row for batch in nested for row in batch]
            expansion_rows = []
            graph_rows = participant_graph_rows(
                rows,
                expand_buckets=set(),
                crawled=set(),
                frontier_side=args.frontier_side,
            )

    seen: dict[str, CandidateMatch] = {}
    for row in rows:
        current = seen.get(row.match_id)
        if current is None or row.raid_score > current.raid_score:
            seen[row.match_id] = row

    rows = sorted(seen.values(), key=lambda item: (item.start_time, item.match_id), reverse=True)
    payload = [asdict(row) for row in rows]
    write_csv(args.outdir / "historical_raid_candidates.csv", payload)
    write_csv(args.outdir / "historical_raid_breadth_expansion.csv", expansion_rows)
    write_csv(args.outdir / "historical_raid_player_graph.csv", graph_rows)
    write_csv(args.outdir / "historical_raid_player_graph_aggregate.csv", aggregate_graph_rows(graph_rows))
    (args.outdir / "historical_raid_candidates.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    write_markdown(args.outdir / "README.md", rows, expansion_rows, args)
    print(f"Wrote {len(rows)} historical candidates to {args.outdir}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Discover historical raid candidates from player match histories.")
    parser.add_argument("players", nargs="+", help="Gamertags or XUIDs to crawl.")
    parser.add_argument("--cutoff", default=DEFAULT_CUTOFF, help="Ignore matches before this ISO datetime.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="Isabel DB path for duplicate detection.")
    parser.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR)
    parser.add_argument("--max-pages", type=int, default=20, help="25 matches per page.")
    parser.add_argument("--concurrency", type=int, default=2, help="Maximum concurrent Halo API calls.")
    parser.add_argument("--breadth-depth", type=int, default=0, help="Expand from raid participants this many hops.")
    parser.add_argument(
        "--max-breadth-players",
        type=int,
        default=25,
        help="Maximum newly discovered players to crawl at each breadth depth after the seed.",
    )
    parser.add_argument(
        "--expand-buckets",
        default="likely_raid",
        help="Comma-separated buckets that can add participants to the breadth frontier.",
    )
    parser.add_argument(
        "--frontier-side",
        choices=["opponents", "teammates", "all"],
        default="opponents",
        help="Which participants from source-player raid matches should become breadth seeds.",
    )
    parser.add_argument("--match-type", choices=["all", "matchmaking", "custom", "local"], default="all")
    parser.add_argument(
        "--inspect-stats-mode",
        choices=["none", "raidish", "all"],
        default="raidish",
        help="Fetch full match stats for no matches, raid-ish metadata only, or every match.",
    )
    parser.add_argument(
        "--inspect-stats",
        action="store_true",
        help="Compatibility alias for --inspect-stats-mode all.",
    )
    args = parser.parse_args()
    if args.inspect_stats:
        args.inspect_stats_mode = "all"
    return asyncio.run(async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
