import re
import asyncio
import json
import os
import time
from pathlib import Path

import discord
from discord.ext import commands

from helpers import db
from helpers import cortana_import
from helpers import match_data


VALID_OUTCOMES = {"WIN": "Win", "LOSS": "Loss", "DRAW": "Draw", "N/A": "N/A", "NA": "N/A"}
CANCEL_KEYWORDS = {"cancel", "/cancel", "exit", "/exit"}
EVENT_CATEGORIES = ["Raid"]
CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.json"


def _load_runtime_config() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as cfg_file:
            return json.load(cfg_file)
    except (OSError, json.JSONDecodeError):
        return {}


_RUNTIME_CONFIG = _load_runtime_config()
MAINFRAME_GUILD_ID = str(os.getenv("ISABEL_MAINFRAME_GUILD_ID") or _RUNTIME_CONFIG.get("mainframe_guild_id") or "")
MAINFRAME_EVENT_DUMP_CHANNEL_ID = str(
    os.getenv("ISABEL_MAINFRAME_EVENT_DUMP_CHANNEL_ID")
    or _RUNTIME_CONFIG.get("mainframe_event_dump_channel_id")
    or ""
)
MAINFRAME_TICKETS_CHANNEL_ID = str(
    os.getenv("ISABEL_MAINFRAME_TICKETS_CHANNEL_ID")
    or _RUNTIME_CONFIG.get("mainframe_tickets_channel_id")
    or ""
)


def _load_owner_ids() -> set[str]:
    cfg = _load_runtime_config()
    return {str(x) for x in cfg.get("owners", [])}


def _inverse_outcome(outcome: str) -> str:
    normalized = (outcome or "").strip().lower()
    if normalized == "win":
        return "Loss"
    if normalized == "loss":
        return "Win"
    return outcome


class ContestReportView(discord.ui.View):
    def __init__(self, events_cog: "Events"):
        super().__init__(timeout=None)
        self.events_cog = events_cog

    @discord.ui.button(
        label="Contest Report",
        style=discord.ButtonStyle.danger,
        custom_id="isabel:contest_event_report",
    )
    async def contest_report(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.events_cog.handle_contest_report(interaction)


class TicketControlView(discord.ui.View):
    def __init__(self, events_cog: "Events"):
        super().__init__(timeout=None)
        self.events_cog = events_cog

    @discord.ui.button(
        label="Swap Winner",
        style=discord.ButtonStyle.primary,
        custom_id="isabel:ticket_swap_winner",
    )
    async def swap_winner(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.events_cog.handle_ticket_outcome_change(interaction, "swap_winner")

    @discord.ui.button(
        label="Declare Tie",
        style=discord.ButtonStyle.secondary,
        custom_id="isabel:ticket_declare_tie",
    )
    async def declare_tie(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.events_cog.handle_ticket_outcome_change(interaction, "declare_tie")

    @discord.ui.button(
        label="Mark Ticket Complete",
        style=discord.ButtonStyle.success,
        custom_id="isabel:resolve_event_ticket",
    )
    async def resolve_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.events_cog.handle_ticket_resolution(interaction)


class Events(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.owner_ids = _load_owner_ids()
        self._active_report_flows: dict[str, float] = {}
        self._report_flow_lock = asyncio.Lock()
        self.contest_report_view = ContestReportView(self)
        self.ticket_control_view = TicketControlView(self)
        self.bot.add_view(self.contest_report_view)
        self.bot.add_view(self.ticket_control_view)

    async def _begin_report_flow(self, user_id: str) -> bool:
        now = time.monotonic()
        async with self._report_flow_lock:
            started_at = self._active_report_flows.get(str(user_id))
            if started_at and now - started_at < 1800:
                return False
            self._active_report_flows[str(user_id)] = now
            return True

    async def _end_report_flow(self, user_id: str) -> None:
        async with self._report_flow_lock:
            self._active_report_flows.pop(str(user_id), None)

    def _parse_outcome(self, raw: str) -> str:
        return VALID_OUTCOMES.get((raw or "").strip().upper(), "N/A")

    def _parse_match_ids(self, text: str) -> list[str]:
        chunks = re.split(r"[\s,]+", (text or "").strip())
        deduped: list[str] = []
        seen = set()
        for chunk in chunks:
            mid = chunk.strip()
            if not mid or mid in seen:
                continue
            seen.add(mid)
            deduped.append(mid)
        return deduped

    async def _send_ctx(self, ctx: commands.Context, content: str, ephemeral: bool = False) -> None:
        if ctx.interaction:
            if not ctx.interaction.response.is_done():
                await ctx.interaction.response.send_message(content, ephemeral=ephemeral)
                return
            await ctx.interaction.followup.send(content, ephemeral=ephemeral)
            return
        await ctx.send(content)

    async def _defer_ctx(self, ctx: commands.Context, ephemeral: bool = False) -> None:
        if ctx.interaction and not ctx.interaction.response.is_done():
            await ctx.interaction.response.defer(thinking=True, ephemeral=ephemeral)

    async def _wait_for_dm(self, user: discord.abc.User, timeout: int = 300) -> discord.Message:
        msg = await self.bot.wait_for(
            "message",
            check=lambda m: (
                m.author.id == user.id
                and isinstance(m.channel, discord.DMChannel)
            ),
            timeout=timeout,
        )
        if msg.content.strip().lower() in CANCEL_KEYWORDS:
            raise asyncio.CancelledError
        return msg

    async def _prompt_dm(
        self,
        dm: discord.DMChannel,
        user: discord.abc.User,
        text: str,
        timeout: int = 300,
    ) -> str:
        await dm.send(text)
        msg = await self._wait_for_dm(user, timeout=timeout)
        return msg.content.strip()

    async def _prompt_index_dm(
        self,
        dm: discord.DMChannel,
        user: discord.abc.User,
        text: str,
        min_value: int,
        max_value: int,
    ) -> int:
        while True:
            raw = await self._prompt_dm(dm, user, text)
            try:
                value = int(raw)
            except ValueError:
                await dm.send(f"Enter a number from `{min_value}` to `{max_value}`.")
                continue
            if value < min_value or value > max_value:
                await dm.send(f"Enter a number from `{min_value}` to `{max_value}`.")
                continue
            return value

    async def _collect_match_ids_via_dm(self, dm: discord.DMChannel, user: discord.abc.User, xuid: str) -> list[str]:
        match_ids: list[str] = []
        while True:
            action = await self._prompt_dm(
                dm,
                user,
                "**Add a match**\n"
                "• `recent` to pick from recent matches\n"
                "• paste a match ID manually\n"
                "• `done` to finish",
            )
            lower = action.strip().lower()
            if lower == "done":
                break

            if lower == "recent":
                try:
                    recent = await match_data.lookup_recent_matches(xuid, count=10)
                except Exception as exc:
                    await dm.send(f"Could not fetch recent matches right now: `{type(exc).__name__}`.")
                    continue
                if not recent:
                    await dm.send("No recent matches were returned.")
                    continue

                lines = [
                    f"{i+1}. {m.get('map_name', 'Unknown')} / {m.get('mode_name', 'Unknown')} (`{m['match_id']}`)"
                    for i, m in enumerate(recent)
                ]
                pick = await self._prompt_index_dm(
                    dm,
                    user,
                    "Select a match:\n" + "\n".join(lines),
                    1,
                    len(recent),
                )
                match_id = str(recent[pick - 1]["match_id"]).strip()
            else:
                match_id = action.strip()

            if not match_id:
                continue
            if match_id in match_ids:
                await dm.send("Match already added.")
                continue
            match_ids.append(match_id)
            await dm.send(f"Added `{match_id}`.")

        return match_ids

    async def _opponent_guild_choices(self, guild_id: str) -> tuple[list[dict], bool]:
        all_guilds = await db.list_registered_guilds()
        others = [r for r in all_guilds if str(r["guild_id"]) != str(guild_id)]
        if others:
            return others, False

        # Single-guild sandbox mode: allow self-opponent strictly for testing.
        self_guild = [r for r in all_guilds if str(r["guild_id"]) == str(guild_id)]
        if self_guild:
            return self_guild, True
        return [], False

    async def _can_report_event(self, ctx: commands.Context) -> bool:
        if not ctx.guild:
            return False

        role_ids = [str(role.id) for role in getattr(ctx.author, "roles", [])]
        return await db.can_user_report_events(
            guild_id=str(ctx.guild.id),
            user_id=str(ctx.author.id),
            role_ids=role_ids,
            is_admin=bool(ctx.author.guild_permissions.administrator),
        )

    def _is_owner(self, user_id: str) -> bool:
        return str(user_id) in self.owner_ids

    def _can_administer_celo_mainframe(self, interaction: discord.Interaction) -> bool:
        if self._is_owner(str(interaction.user.id)):
            return True
        if not interaction.guild or str(interaction.guild.id) != MAINFRAME_GUILD_ID:
            return False
        permissions = getattr(interaction.user, "guild_permissions", None)
        return bool(permissions and permissions.administrator)

    async def _replace_thread_match_embeds(
        self,
        thread: discord.Thread,
        summary_message_id: str,
        match_embeds: list[discord.Embed],
    ) -> None:
        try:
            existing_bot_match_messages: list[discord.Message] = []
            async for msg in thread.history(limit=200, oldest_first=True):
                if str(msg.id) == str(summary_message_id):
                    continue
                if not msg.embeds:
                    continue
                if not self.bot.user or msg.author.id != self.bot.user.id:
                    continue
                title = str(msg.embeds[0].title or "")
                if title.startswith("Match "):
                    existing_bot_match_messages.append(msg)

            for msg in existing_bot_match_messages:
                try:
                    await msg.delete()
                except Exception:
                    continue

            for embed in match_embeds:
                await thread.send(embed=embed)
        except Exception:
            return

    def _format_roster(self, entries: list[dict], limit: int = 12) -> str:
        if not entries:
            return "None"
        lines = []
        for row in entries[:limit]:
            who = row["gamertag"]
            if row.get("discord_id"):
                who = f"{who} (<@{row['discord_id']}>)"
            damage = int(row.get("damage") or 0)
            lines.append(f"- {who} | {row['kills']}/{row['deaths']}/{row['assists']} | {damage:,} dmg")
        if len(entries) > limit:
            lines.append(f"...and {len(entries) - limit} more")
        return "\n".join(lines)

    def _format_stat_summary(self, summary: dict) -> str:
        return (
            f"Players: **{int(summary.get('players') or 0)}**\n"
            f"K/D/A: **{int(summary.get('kills') or 0)} / {int(summary.get('deaths') or 0)} / {int(summary.get('assists') or 0)}**\n"
            f"Damage: **{int(summary.get('damage') or 0):,}**"
        )

    def _format_performer(self, row: dict | None, metric: str) -> str:
        if not row:
            return "None"
        who = row.get("gamertag") or row.get("xuid") or "Unknown"
        if row.get("discord_id"):
            who = f"{who} (<@{row['discord_id']}>)"
        value = int(row.get(metric) or 0)
        suffix = "damage" if metric == "damage" else metric
        return f"{who} - **{value:,} {suffix}**"

    def _format_top_performers(self, performers: dict) -> str:
        if not performers:
            return "No linked performers found."
        return "\n".join(
            [
                f"Most Kills: {self._format_performer(performers.get('kills'), 'kills')}",
                f"Most Assists: {self._format_performer(performers.get('assists'), 'assists')}",
                f"Highest Damage: {self._format_performer(performers.get('damage'), 'damage')}",
                f"Fewest Deaths: {self._format_performer(performers.get('survivor'), 'deaths')}",
            ]
        )

    def _format_match_meta(self, game: dict, summary: dict) -> str:
        duration = str(game.get("duration") or "").strip()
        duration_line = f"\nDuration: **{duration}**" if duration else ""
        return (
            f"{game.get('map_name') or 'Unknown Map'} - {game.get('mode_name') or 'Unknown Mode'}\n"
            f"[View match](https://leafapp.co/game/{game['match_id']}){duration_line}\n"
            f"Players: **{int(summary.get('players') or 0)}** | "
            f"K/D/A: **{int(summary.get('kills') or 0)} / {int(summary.get('deaths') or 0)} / {int(summary.get('assists') or 0)}** | "
            f"Damage: **{int(summary.get('damage') or 0):,}**"
        )

    def _format_adjustments(self, entries: list[dict], limit: int = 12) -> str:
        if not entries:
            return "None"
        lines = []
        for row in entries[:limit]:
            delta = float(row["delta"])
            sign = "+" if delta >= 0 else ""
            who = row.get("gamertag") or row.get("xuid") or "Unknown"
            if row.get("discord_id"):
                who = f"{who} (<@{row['discord_id']}>)"
            old_score = db.celo_score(row["old_rating"])
            new_score = db.celo_score(row["new_rating"])
            score_delta = new_score - old_score
            score_sign = "+" if score_delta >= 0 else ""
            lines.append(
                f"- {who} {score_sign}{score_delta} "
                f"({old_score} -> {new_score}, {int(row['games'])} game(s))"
            )
        if len(entries) > limit:
            lines.append(f"...and {len(entries) - limit} more")
        return "\n".join(lines)

    def _format_medal_highlights(self, entries: list[dict], limit: int = 8) -> str:
        if not entries:
            return "No medal data logged for this match yet."
        lines = []
        for row in entries[:limit]:
            who = row.get("gamertag") or "Unknown"
            if row.get("discord_id"):
                who = f"{who} (<@{row['discord_id']}>)"
            count = int(row.get("count") or 0)
            score = int(row.get("score") or 0)
            lines.append(f"- **{row['medal_name']}** x{count} - {who} ({score:,} score)")
        if len(entries) > limit:
            lines.append(f"...and {len(entries) - limit} more")
        return "\n".join(lines)

    def _format_raid_profile(self, profile: dict) -> str:
        defender = profile["defender"]
        attackers = profile["attackers"]
        shares = profile["shares"]
        return (
            f"Parity: **{float(profile['parity']):.2f}** - {profile['interpretation']}\n"
            f"Defenders (**{profile['defender_label']} / team {profile['defender_team']}**): "
            f"{defender['kills']}/{defender['deaths']}/{defender['assists']}, {defender['damage']:,} damage\n"
            f"Attackers: {attackers['kills']}/{attackers['deaths']}/{attackers['assists']}, "
            f"{attackers['damage']:,} damage\n"
            f"Defender shares: {shares['defender_kill_share']:.0%} kills, "
            f"{shares['defender_damage_share']:.0%} damage, {shares['defender_death_share']:.0%} deaths"
        )

    def _extract_event_id_from_embed(self, embed: discord.Embed) -> int | None:
        for field in embed.fields:
            if str(field.name).strip().lower() == "event id":
                raw = str(field.value or "").strip().strip("`")
                if raw.isdigit():
                    return int(raw)
        return None

    def _extract_ticket_id_from_embed(self, embed: discord.Embed) -> int | None:
        for field in embed.fields:
            if str(field.name).strip().lower() == "ticket id":
                raw = str(field.value or "").strip().strip("`").upper().replace("TKT-", "")
                if raw.isdigit():
                    return int(raw)
        return None

    def _format_ticket_label(self, ticket_id: int) -> str:
        return f"TKT-{int(ticket_id):06d}"

    def _event_public_label(self, event: dict) -> str:
        value = str(event.get("public_id") or "").strip()
        if value:
            return value
        # Fallback for legacy rows before public IDs were added.
        return f"EVT-{int(event['id']):06d}"

    def _set_embed_field(self, embed: discord.Embed, name: str, value: str, inline: bool = True) -> None:
        for index, field in enumerate(embed.fields):
            if str(field.name).strip().lower() == name.strip().lower():
                embed.set_field_at(index, name=name, value=value, inline=inline)
                return
        embed.add_field(name=name, value=value, inline=inline)

    async def _notify_owners_of_contest(
        self,
        contest_id: int,
        event_id: int,
        source_guild_id: str,
        opener_id: str,
        details: str,
        jump_url: str | None = None,
    ) -> int:
        sent_count = 0
        message = (
            f"Contest #{contest_id} opened for event `{event_id}`\n"
            f"Source guild: `{source_guild_id}`\n"
            f"Opened by: <@{opener_id}>\n"
            f"Details: {details or 'No extra details'}\n"
            f"Report link: {jump_url or 'Unavailable'}\n"
            "Use `/push_updated_report` after resolving database changes."
        )
        for owner_id in self.owner_ids:
            try:
                owner = self.bot.get_user(int(owner_id)) or await self.bot.fetch_user(int(owner_id))
                await owner.send(message)
                sent_count += 1
            except Exception:
                continue
        return sent_count

    async def _open_contest_ticket_in_mainframe(
        self,
        contest_id: int,
        event: dict,
        source_guild_id: str,
        opened_by_discord_id: str,
        details: str,
        report_jump_url: str | None = None,
    ) -> str | None:
        if not MAINFRAME_TICKETS_CHANNEL_ID:
            return None
        ticket_label = self._format_ticket_label(contest_id)
        target = self.bot.get_channel(int(MAINFRAME_TICKETS_CHANNEL_ID))
        if target is None:
            return None

        event_guild_a_id = str(event.get("guild_id") or "")
        event_guild_b_id = str(event.get("opponent_guild_id") or "")
        if source_guild_id == event_guild_a_id:
            opponent_guild_id = event_guild_b_id
        elif source_guild_id == event_guild_b_id:
            opponent_guild_id = event_guild_a_id
        else:
            opponent_guild_id = event_guild_b_id or event_guild_a_id

        source_guild = await db.get_guild_registration(source_guild_id)
        opponent_guild = await db.get_guild_registration(opponent_guild_id) if opponent_guild_id else None
        source_guild_name = source_guild["guild_name"] if source_guild else f"Guild-{source_guild_id}"
        opponent_guild_name = opponent_guild["guild_name"] if opponent_guild else (event.get("opponent") or f"Guild-{opponent_guild_id or 'Unknown'}")
        home_guild = await db.get_guild_registration(event_guild_a_id) if event_guild_a_id else None
        away_guild = await db.get_guild_registration(event_guild_b_id) if event_guild_b_id else None
        home_name = home_guild["guild_name"] if home_guild else f"Guild-{event_guild_a_id or 'Unknown'}"
        away_name = away_guild["guild_name"] if away_guild else (event.get("opponent") or f"Guild-{event_guild_b_id or 'Unknown'}")
        current_outcome = str(event.get("outcome") or "N/A")
        source_perspective = current_outcome if source_guild_id == event_guild_a_id else _inverse_outcome(current_outcome)

        embed = discord.Embed(
            title=f"Contest Ticket {ticket_label}",
            description=(
                f"**Status:** OPEN\n"
                f"**Event:** `{self._event_public_label(event)}` / `{event['id']}`\n"
                f"**Matchup:** {home_name} vs {away_name}\n"
                f"**Current recorded outcome:** {home_name} **{current_outcome}**\n"
                f"**Contest source perspective:** {source_guild_name} sees **{source_perspective}**\n"
                f"**Opened by:** <@{opened_by_discord_id}>"
            ),
            color=discord.Color.orange(),
        )
        embed.add_field(name="Ticket ID", value=f"`{ticket_label}`", inline=True)
        embed.add_field(name="Event Code", value=f"`{self._event_public_label(event)}`", inline=True)
        embed.add_field(name="Event ID", value=f"`{event['id']}`", inline=True)
        embed.add_field(name="Category", value=str(event.get("category") or "N/A"), inline=True)
        embed.add_field(name="Recorded Outcome", value=f"{home_name}: **{current_outcome}**", inline=True)
        embed.add_field(name="Contest Source", value=f"**{source_guild_name}**\n`{source_guild_id}`", inline=True)
        embed.add_field(name="Other Clan", value=f"**{opponent_guild_name}**\n`{opponent_guild_id or 'N/A'}`", inline=True)
        embed.add_field(name="Coordinator", value=f"<@{event.get('coordinator_id')}>", inline=True)
        embed.add_field(name="Available Resolutions", value="Use **Swap Winner** if the other side won, **Declare Tie** for a draw, or **Mark Ticket Complete** for no database change.", inline=False)
        embed.add_field(name="Report Message", value=report_jump_url or "Unavailable", inline=False)
        if details:
            embed.add_field(name="Details", value=details[:1000], inline=False)
        embed.set_footer(text="Outcome buttons update CELO, training models, and operation report threads.")

        if isinstance(target, discord.ForumChannel):
            seed = await target.create_thread(
                name=f"{ticket_label} • Event {event['id']}",
                embed=embed,
                view=self.ticket_control_view,
            )
            return seed.thread.jump_url if seed and seed.thread else None

        if isinstance(target, (discord.TextChannel, discord.Thread)):
            msg = await target.send(embed=embed, view=self.ticket_control_view)
            return msg.jump_url

        return None

    async def _dump_event_to_mainframe(self, event: dict) -> str | None:
        if not MAINFRAME_EVENT_DUMP_CHANNEL_ID:
            return None
        target = self.bot.get_channel(int(MAINFRAME_EVENT_DUMP_CHANNEL_ID))
        if target is None:
            return None

        guild_a_id = str(event["guild_id"])
        guild_b_id = str(event.get("opponent_guild_id") or "")
        guild_a = await db.get_guild_registration(guild_a_id)
        guild_b = await db.get_guild_registration(guild_b_id) if guild_b_id else None
        guild_a_name = guild_a["guild_name"] if guild_a else f"Guild-{guild_a_id}"
        guild_b_name = guild_b["guild_name"] if guild_b else (event.get("opponent") or f"Guild-{guild_b_id}")
        embeds = await self._build_operation_embeds_for_guild(event, guild_a_id)

        if isinstance(target, discord.ForumChannel):
            seed = await target.create_thread(
                name=f"ARCHIVE • {self._event_public_label(event)} • {guild_a_name} vs {guild_b_name}",
                embed=embeds[0],
            )
            thread = seed.thread
            for embed in embeds[1:]:
                await thread.send(embed=embed)
            return f"{target.guild.name}#{target.name}"

        if isinstance(target, (discord.TextChannel, discord.Thread)):
            await target.send(embed=embeds[0])
            for embed in embeds[1:]:
                await target.send(embed=embed)
            return f"{target.guild.name}#{target.name}" if getattr(target, "guild", None) else str(target.id)

        return None

    async def handle_contest_report(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Contest can only be submitted from a server report thread.", ephemeral=True)
            return

        if not interaction.message or not interaction.message.embeds:
            await interaction.response.send_message("Could not read event details from this report message.", ephemeral=True)
            return

        summary_embed = interaction.message.embeds[0]
        event_id = self._extract_event_id_from_embed(summary_embed)
        if not event_id:
            await interaction.response.send_message("Unable to identify the event ID from this report.", ephemeral=True)
            return

        event = await db.get_event(event_id)
        if not event:
            await interaction.response.send_message(f"Event `{event_id}` was not found in the database.", ephemeral=True)
            return

        guild_id = str(interaction.guild.id)
        event_guild_id = str(event["guild_id"])
        opponent_guild_id = str(event.get("opponent_guild_id") or "")
        if guild_id not in {event_guild_id, opponent_guild_id}:
            await interaction.response.send_message("This report is not linked to your guild in Isabel.", ephemeral=True)
            return

        existing_open = await db.get_open_event_contest(event_id=event_id, source_guild_id=guild_id)
        if existing_open:
            await interaction.response.send_message(
                f"There is already an open contest ticket for this report: "
                f"`{self._format_ticket_label(int(existing_open['id']))}`.",
                ephemeral=True,
            )
            return

        details = (
            f"Outcome shown: {event.get('outcome')}; "
            f"Opponent guild: {event.get('opponent_guild_id') or 'N/A'}; "
            f"Coordinator: {event.get('coordinator_id')}"
        )
        contest_id = await db.create_event_contest(
            event_id=event_id,
            source_guild_id=guild_id,
            opened_by_discord_id=str(interaction.user.id),
            report_thread_id=str(interaction.channel.id) if interaction.channel else None,
            report_message_id=str(interaction.message.id),
            details=details,
        )
        mainframe_ticket_url = await self._open_contest_ticket_in_mainframe(
            contest_id=contest_id,
            event=event,
            source_guild_id=guild_id,
            opened_by_discord_id=str(interaction.user.id),
            details=details,
            report_jump_url=getattr(interaction.message, "jump_url", None),
        )

        owner_notifications = 0
        if not mainframe_ticket_url:
            owner_notifications = await self._notify_owners_of_contest(
                contest_id=contest_id,
                event_id=event_id,
                source_guild_id=guild_id,
                opener_id=str(interaction.user.id),
                details=details,
                jump_url=getattr(interaction.message, "jump_url", None),
            )

        ticket_label = self._format_ticket_label(contest_id)
        if mainframe_ticket_url:
            msg = f"Contest ticket created (`{ticket_label}`). Ticket: {mainframe_ticket_url}"
        else:
            msg = (
                f"Contest ticket created (`{ticket_label}`), but Mainframe ticket channel was unreachable. "
                f"Owner notifications sent: `{owner_notifications}`."
            )
        await interaction.response.send_message(
            msg,
            ephemeral=True,
        )

    async def handle_ticket_resolution(self, interaction: discord.Interaction) -> None:
        if not interaction.message or not interaction.message.embeds:
            await interaction.response.send_message("Could not read ticket details from this message.", ephemeral=True)
            return
        if not self._can_administer_celo_mainframe(interaction):
            await interaction.response.send_message(
                "Only CELO administration server admins can resolve tickets.",
                ephemeral=True,
            )
            return

        ticket_id = self._extract_ticket_id_from_embed(interaction.message.embeds[0])
        if not ticket_id:
            await interaction.response.send_message("Ticket ID was not found in this message.", ephemeral=True)
            return

        ticket = await db.get_event_contest(int(ticket_id))
        if not ticket:
            await interaction.response.send_message("This ticket no longer exists in the database.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        resolved = await db.resolve_event_contest(ticket_id, str(interaction.user.id))
        if not resolved:
            await interaction.followup.send("This ticket is already resolved or no longer valid.", ephemeral=True)
            return

        embed = interaction.message.embeds[0].copy()
        embed.color = discord.Color.green()
        embed.description = (embed.description or "").replace("**Status:** OPEN", "**Status:** RESOLVED - NO OUTCOME CHANGE")
        self._set_embed_field(embed, "Resolution", "Completed without changing the recorded outcome.", inline=False)
        embed.set_footer(text=f"Resolved by {interaction.user} ({interaction.user.id})")

        await interaction.message.edit(embed=embed, view=None)
        refresh_result = await self._refresh_operation_reports_for_event(int(ticket["event_id"]))
        refreshed = int(refresh_result.get("refreshed", 0))
        recreated = int(refresh_result.get("recreated", 0))
        failures = list(refresh_result.get("failures", []))
        republished = bool(refresh_result.get("republished"))
        posted_labels = list(refresh_result.get("posted_labels", []))

        lines = [
            f"Resolved `{self._format_ticket_label(ticket_id)}` for event `{ticket['event_id']}`.",
        ]
        if republished:
            if posted_labels:
                lines.append("No tracked report posts were found, so I republished to: " + ", ".join(posted_labels))
            else:
                lines.append("No tracked report posts were found, and no report forums were reachable.")
        else:
            lines.append(f"Auto-refresh complete: `{refreshed}` updated thread(s), `{recreated}` recreated thread(s).")
            if failures:
                lines.append(f"Refresh failures: `{len(failures)}`")

        await interaction.followup.send("\n".join(lines), ephemeral=True)

        if isinstance(interaction.channel, discord.Thread):
            try:
                new_name = interaction.channel.name
                if not new_name.lower().startswith("resolved"):
                    new_name = f"resolved-{new_name}"[:100]
                await interaction.channel.edit(name=new_name, archived=True, locked=True)
            except Exception:
                pass

    async def handle_ticket_outcome_change(self, interaction: discord.Interaction, action: str) -> None:
        if not interaction.message or not interaction.message.embeds:
            await interaction.response.send_message("Could not read ticket details from this message.", ephemeral=True)
            return
        if not self._can_administer_celo_mainframe(interaction):
            await interaction.response.send_message(
                "Only CELO administration server admins can change contested outcomes.",
                ephemeral=True,
            )
            return

        ticket_id = self._extract_ticket_id_from_embed(interaction.message.embeds[0])
        if not ticket_id:
            await interaction.response.send_message("Ticket ID was not found in this message.", ephemeral=True)
            return

        ticket = await db.get_event_contest(int(ticket_id))
        if not ticket:
            await interaction.response.send_message("This ticket no longer exists in the database.", ephemeral=True)
            return
        if str(ticket.get("status") or "").lower() == "resolved":
            await interaction.response.send_message("This ticket is already resolved.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            revision = await db.revise_event_outcome(int(ticket["event_id"]), action)
            celo_result = await db.apply_celo_for_event(int(ticket["event_id"]))
            model_result = await db.apply_celo_models_for_event(int(ticket["event_id"]))
        except Exception as exc:
            await interaction.followup.send(
                f"Outcome update failed: `{type(exc).__name__}`. No ticket status was changed.",
                ephemeral=True,
            )
            return

        resolved = await db.resolve_event_contest(ticket_id, str(interaction.user.id))
        if not resolved:
            await interaction.followup.send("Outcome updated, but this ticket was already resolved.", ephemeral=True)
            return

        refresh_result = await self._refresh_operation_reports_for_event(int(ticket["event_id"]))
        refreshed = int(refresh_result.get("refreshed", 0))
        recreated = int(refresh_result.get("recreated", 0))
        failures = list(refresh_result.get("failures", []))
        republished = bool(refresh_result.get("republished"))
        posted_labels = list(refresh_result.get("posted_labels", []))

        action_label = "Swapped winner" if action == "swap_winner" else "Declared tie"
        embed = interaction.message.embeds[0].copy()
        embed.color = discord.Color.green()
        embed.description = (embed.description or "").replace("**Status:** OPEN", f"**Status:** RESOLVED - {action_label.upper()}")
        self._set_embed_field(
            embed,
            "Recorded Outcome",
            f"Updated: **{revision['old_outcome']}** -> **{revision['new_outcome']}**",
            inline=True,
        )
        self._set_embed_field(
            embed,
            "Resolution",
            (
                f"{action_label} by <@{interaction.user.id}>.\n"
                f"Official CELO: `{celo_result['processed_games']}` game(s), `{celo_result['adjusted_users']}` adjusted profile(s).\n"
                f"Training models: `{sum(int(payload.get('processed_games', 0)) for payload in model_result.get('models', {}).values())}` model-game pass(es).\n"
                f"Reports: `{refreshed}` refreshed, `{recreated}` recreated."
            ),
            inline=False,
        )
        if failures:
            self._set_embed_field(embed, "Refresh Warnings", "\n".join(f"- {f}" for f in failures[:6]), inline=False)
        embed.set_footer(text=f"Resolved by {interaction.user} ({interaction.user.id})")

        await interaction.message.edit(embed=embed, view=None)

        lines = [
            f"{action_label} for `{self._format_ticket_label(ticket_id)}` / event `{ticket['event_id']}`.",
            f"Outcome: `{revision['old_outcome']}` -> `{revision['new_outcome']}`.",
            f"Official CELO processed `{celo_result['processed_games']}` game(s), adjusted `{celo_result['adjusted_users']}` profile(s).",
        ]
        if republished:
            if posted_labels:
                lines.append("No tracked report posts were found, so I republished to: " + ", ".join(posted_labels))
            else:
                lines.append("No tracked report posts were found, and no report forums were reachable.")
        else:
            lines.append(f"Reports refreshed: `{refreshed}`, recreated: `{recreated}`.")
        if failures:
            lines.append(f"Refresh failures: `{len(failures)}`.")

        await interaction.followup.send("\n".join(lines), ephemeral=True)

        if isinstance(interaction.channel, discord.Thread):
            try:
                new_name = interaction.channel.name
                if not new_name.lower().startswith("resolved"):
                    new_name = f"resolved-{new_name}"[:100]
                await interaction.channel.edit(name=new_name, archived=True, locked=True)
            except Exception:
                pass

    async def _build_operation_embeds_for_guild(self, event: dict, perspective_guild_id: str) -> list[discord.Embed]:
        guild_a_id = str(event["guild_id"])
        guild_b_id = str(event["opponent_guild_id"] or "")

        guild_a = await db.get_guild_registration(guild_a_id)
        guild_b = await db.get_guild_registration(guild_b_id) if guild_b_id else None

        guild_a_name = guild_a["guild_name"] if guild_a else f"Guild-{guild_a_id}"
        guild_b_name = guild_b["guild_name"] if guild_b else (event.get("opponent") or f"Guild-{guild_b_id}")
        is_team_a_perspective = str(perspective_guild_id) == guild_a_id
        home_name = guild_a_name if is_team_a_perspective else guild_b_name
        opponent_name = guild_b_name if is_team_a_perspective else guild_a_name
        outcome = event.get("outcome", "N/A")
        perspective_outcome = outcome if is_team_a_perspective else _inverse_outcome(outcome)

        games = await db.get_games_for_event(int(event["id"]))
        celo_impact = await db.get_event_celo_impact(int(event["id"]))
        adjustments = await db.get_event_celo_adjustments(int(event["id"]))
        event_summary = await db.get_event_stat_summary(int(event["id"]))
        top_performers = await db.get_event_top_performers_for_guild(
            int(event["id"]),
            str(perspective_guild_id),
        )

        summary = discord.Embed(
            title=f"Operation Report • {self._event_public_label(event)}",
            description=(
                f"**{home_name}** vs **{opponent_name}**\n"
                f"Result for {home_name}: **{perspective_outcome}**\n"
                f"Category: **{event['category']}**\n"
                f"Coordinator: <@{event['coordinator_id']}>"
            ),
            color=discord.Color.dark_blue(),
        )
        summary.add_field(name="Event Code", value=f"`{self._event_public_label(event)}`", inline=True)
        summary.add_field(name="Event ID", value=f"`{event['id']}`", inline=True)
        summary.add_field(name="Matches", value=str(len(games)), inline=True)
        summary.add_field(name="Event Totals", value=self._format_stat_summary(event_summary), inline=False)
        summary.add_field(name=f"Top Performers • {home_name}", value=self._format_top_performers(top_performers), inline=False)
        summary.add_field(
            name="CELO Impact",
            value=f"{celo_impact['adjusted_users']} players adjusted across {celo_impact['processed_games']} games",
            inline=False,
        )
        notes = str(event.get("notes") or "").strip()
        if notes:
            summary.add_field(name="Notes", value=notes[:1000], inline=False)
        if adjustments:
            guild_a_adjustments = [r for r in adjustments if r.get("in_guild_a") and not r.get("in_guild_b")]
            guild_b_adjustments = [r for r in adjustments if r.get("in_guild_b") and not r.get("in_guild_a")]
            dual_adjustments = [r for r in adjustments if r.get("in_guild_a") and r.get("in_guild_b")]
            unknown_adjustments = [r for r in adjustments if not r.get("in_guild_a") and not r.get("in_guild_b")]

            home_adjustments = guild_a_adjustments if is_team_a_perspective else guild_b_adjustments
            opponent_adjustments = guild_b_adjustments if is_team_a_perspective else guild_a_adjustments

            summary.add_field(
                name=f"CELO Adjustments • {home_name}",
                value=self._format_adjustments(home_adjustments),
                inline=False,
            )
            summary.add_field(
                name=f"CELO Adjustments • {opponent_name}",
                value=self._format_adjustments(opponent_adjustments),
                inline=False,
            )
            if dual_adjustments:
                summary.add_field(
                    name="CELO Adjustments • Dual Allegiance",
                    value=self._format_adjustments(dual_adjustments, limit=8),
                    inline=False,
                )
            if unknown_adjustments:
                summary.add_field(
                    name="CELO Adjustments • Unregistered/Unknown",
                    value=self._format_adjustments(unknown_adjustments, limit=8),
                    inline=False,
                )

        embeds = [summary]

        for game in games:
            breakdown = await db.get_game_attendance_breakdown(
                game_id=int(game["id"]),
                guild_a_id=guild_a_id,
                guild_b_id=guild_b_id,
            )
            home_entries = breakdown["guild_a"] if is_team_a_perspective else breakdown["guild_b"]
            opponent_entries = breakdown["guild_b"] if is_team_a_perspective else breakdown["guild_a"]
            game_summary = await db.get_game_stat_summary(int(game["id"]))
            match_embed = discord.Embed(
                title=f"Match • {game['match_id']}",
                description=self._format_match_meta(game, game_summary),
                color=discord.Color.blurple(),
            )
            match_embed.add_field(name=f"{home_name} Forces", value=self._format_roster(home_entries), inline=False)
            match_embed.add_field(name=f"{opponent_name} Forces", value=self._format_roster(opponent_entries), inline=False)
            medal_highlights = await db.get_game_medal_highlights(int(game["id"]), limit=8)
            match_embed.add_field(
                name="Medal Highlights",
                value=self._format_medal_highlights(medal_highlights),
                inline=False,
            )
            if breakdown["dual"]:
                match_embed.add_field(name="Dual Allegiance", value=self._format_roster(breakdown["dual"], limit=6), inline=False)
            if breakdown["unknown"]:
                match_embed.add_field(name="Unregistered/Unknown", value=self._format_roster(breakdown["unknown"], limit=6), inline=False)
            if str(event.get("category") or "").strip().lower() == "raid":
                raid_profile = await db.get_game_raid_profile(int(game["id"]))
                match_embed.add_field(
                    name="Raid Stat Parity",
                    value=self._format_raid_profile(raid_profile),
                    inline=False,
                )
            embeds.append(match_embed)

        return embeds

    async def _dispatch_operation_report(self, event: dict) -> list[str]:
        guild_a_id = str(event["guild_id"])
        guild_b_id = str(event["opponent_guild_id"] or "")
        guild_a = await db.get_guild_registration(guild_a_id)
        guild_b = await db.get_guild_registration(guild_b_id) if guild_b_id else None
        guild_a_name = guild_a["guild_name"] if guild_a else f"Guild-{guild_a_id}"
        guild_b_name = guild_b["guild_name"] if guild_b else (event.get("opponent") or f"Guild-{guild_b_id}")

        target_guild_ids = [guild_a_id]
        if guild_b_id and guild_b_id != guild_a_id:
            target_guild_ids.append(guild_b_id)

        posted_labels: list[str] = []

        for gid in target_guild_ids:
            channel_id = await db.get_event_channel(gid)
            if not channel_id:
                continue

            forum = self.bot.get_channel(int(channel_id))
            if not isinstance(forum, discord.ForumChannel):
                continue

            posted_labels.append(f"{forum.guild.name}#{forum.name}")
            embeds = await self._build_operation_embeds_for_guild(event, gid)

            thread_seed = (
                await forum.create_thread(
                    name=f"{self._event_public_label(event)} • {guild_a_name} vs {guild_b_name}",
                    embed=embeds[0],
                    view=self.contest_report_view,
                )
            )
            thread = thread_seed.thread
            summary_message = thread_seed.message

            if summary_message:
                await db.upsert_event_report_post(
                    event_id=int(event["id"]),
                    guild_id=str(gid),
                    forum_channel_id=str(forum.id),
                    thread_id=str(thread.id),
                    summary_message_id=str(summary_message.id),
                )
            for embed in embeds[1:]:
                await thread.send(embed=embed)

        mainframe_label = await self._dump_event_to_mainframe(event)
        if mainframe_label:
            posted_labels.append(mainframe_label)

        return posted_labels

    async def _refresh_operation_reports_for_event(self, event_id: int) -> dict:
        event = await db.get_event(int(event_id))
        if not event:
            return {
                "event_found": False,
                "republished": False,
                "posted_labels": [],
                "refreshed": 0,
                "recreated": 0,
                "failures": ["Event not found."],
            }

        report_posts = await db.get_event_report_posts(int(event_id))
        if not report_posts:
            posted = await self._dispatch_operation_report(event)
            return {
                "event_found": True,
                "republished": True,
                "posted_labels": posted,
                "refreshed": 0,
                "recreated": 0,
                "failures": [],
            }

        refreshed = 0
        recreated = 0
        failures: list[str] = []

        for post in report_posts:
            guild_id = str(post["guild_id"])
            forum_channel_id = str(post["forum_channel_id"])
            thread_id = str(post["thread_id"])
            summary_message_id = str(post["summary_message_id"])
            embeds = await self._build_operation_embeds_for_guild(event, guild_id)

            thread = self.bot.get_channel(int(thread_id))
            if not isinstance(thread, discord.Thread):
                forum = self.bot.get_channel(int(forum_channel_id))
                if not isinstance(forum, discord.ForumChannel):
                    failures.append(f"Guild `{guild_id}`: forum or thread not reachable.")
                    continue

                guild_a_id = str(event["guild_id"])
                guild_b_id = str(event.get("opponent_guild_id") or "")
                guild_a = await db.get_guild_registration(guild_a_id)
                guild_b = await db.get_guild_registration(guild_b_id) if guild_b_id else None
                guild_a_name = guild_a["guild_name"] if guild_a else f"Guild-{guild_a_id}"
                guild_b_name = guild_b["guild_name"] if guild_b else (event.get("opponent") or f"Guild-{guild_b_id}")

                thread_seed = await forum.create_thread(
                    name=f"{self._event_public_label(event)} • {guild_a_name} vs {guild_b_name}",
                    embed=embeds[0],
                    view=self.contest_report_view,
                )
                new_thread = thread_seed.thread
                summary_message = thread_seed.message
                if summary_message:
                    await db.upsert_event_report_post(
                        event_id=int(event_id),
                        guild_id=guild_id,
                        forum_channel_id=forum_channel_id,
                        thread_id=str(new_thread.id),
                        summary_message_id=str(summary_message.id),
                    )
                    for embed in embeds[1:]:
                        await new_thread.send(embed=embed)
                    recreated += 1
                    continue

                failures.append(f"Guild `{guild_id}`: could not recreate thread summary message.")
                continue

            try:
                summary_message = await thread.fetch_message(int(summary_message_id))
                await summary_message.edit(embed=embeds[0], view=self.contest_report_view)
                await self._replace_thread_match_embeds(
                    thread=thread,
                    summary_message_id=summary_message_id,
                    match_embeds=embeds[1:],
                )
                refreshed += 1
            except Exception:
                failures.append(f"Guild `{guild_id}`: failed to update thread `{thread_id}`.")
                continue

        return {
            "event_found": True,
            "republished": False,
            "posted_labels": [],
            "refreshed": refreshed,
            "recreated": recreated,
            "failures": failures,
        }

    @commands.hybrid_command(name="set_event_channel", description="Set this clan's operation report forum.")
    async def set_event_channel(self, ctx: commands.Context, channel: discord.abc.GuildChannel):
        if not ctx.guild:
            await ctx.send("This command can only be used in a server.")
            return
        if not ctx.author.guild_permissions.administrator:
            await ctx.send("You must be an administrator to set the event forum.")
            return

        if not await db.is_registered_guild(str(ctx.guild.id)):
            await ctx.send("Register this clan first with `/register_clan`.")
            return

        if not isinstance(channel, discord.ForumChannel):
            await ctx.send("Please select a forum channel for operation reports.")
            return

        await db.set_event_channel(str(ctx.guild.id), str(channel.id))
        await ctx.send(f"Operation report forum set to {channel.mention}.")

    @commands.hybrid_command(name="add_event_reporter_role", description="Allow a role to report events for this clan.")
    async def add_event_reporter_role(self, ctx: commands.Context, role: discord.Role):
        if not ctx.guild:
            await ctx.send("This command can only be used in a server.")
            return
        if not ctx.author.guild_permissions.administrator:
            await ctx.send("You must be an administrator to manage reporter roles.")
            return

        await db.add_event_reporter_role(str(ctx.guild.id), str(role.id))
        await ctx.send(f"Added {role.mention} to event reporter whitelist.")

    @commands.hybrid_command(name="remove_event_reporter_role", description="Remove a role from this clan's event reporter whitelist.")
    async def remove_event_reporter_role(self, ctx: commands.Context, role: discord.Role):
        if not ctx.guild:
            await ctx.send("This command can only be used in a server.")
            return
        if not ctx.author.guild_permissions.administrator:
            await ctx.send("You must be an administrator to manage reporter roles.")
            return

        removed = await db.remove_event_reporter_role(str(ctx.guild.id), str(role.id))
        if removed:
            await ctx.send(f"Removed {role.mention} from event reporter whitelist.")
            return
        await ctx.send(f"{role.mention} was not in the event reporter whitelist.")

    @commands.hybrid_command(name="list_event_reporter_roles", description="List whitelisted event reporter roles for this clan.")
    async def list_event_reporter_roles(self, ctx: commands.Context):
        if not ctx.guild:
            await ctx.send("This command can only be used in a server.")
            return

        role_ids = await db.list_event_reporter_roles(str(ctx.guild.id))
        if not role_ids:
            await ctx.send("No whitelisted event reporter roles configured.")
            return

        labels = []
        for rid in role_ids:
            role = ctx.guild.get_role(int(rid))
            labels.append(role.mention if role else f"`{rid}`")
        await ctx.send("Event reporter whitelist:\n" + "\n".join(f"- {x}" for x in labels))

    @commands.hybrid_command(name="report_event", description="Start a DM-driven event report flow.")
    async def report_event(self, ctx: commands.Context):
        command_started_at = time.perf_counter()
        if not ctx.guild:
            await self._send_ctx(ctx, "This command can only be used in a server.")
            return

        if not await db.is_registered_guild(str(ctx.guild.id)):
            await self._send_ctx(ctx, "This clan is not registered. Use `/register_clan` first.")
            return

        if not await self._can_report_event(ctx):
            await self._send_ctx(ctx, "You must be an administrator or whitelisted event-reporter role to report events.")
            return

        links = await db.get_xuid_links(str(ctx.author.id))
        if not links:
            await self._send_ctx(
                ctx,
                "You need at least one linked XUID before reporting events. Use `/link_xuid` first.",
                ephemeral=bool(ctx.interaction),
            )
            return

        if not await self._begin_report_flow(str(ctx.author.id)):
            await self._send_ctx(
                ctx,
                "You already have an event report in progress. Finish it in DMs, or type `cancel` there before starting another.",
                ephemeral=bool(ctx.interaction),
            )
            return

        single_guild_test_mode = False
        try:
            await self._send_ctx(
                ctx,
                "Check your DMs to continue the event report.",
                ephemeral=bool(ctx.interaction),
            )

            try:
                dm = await ctx.author.create_dm()
            except discord.Forbidden:
                await self._send_ctx(
                    ctx,
                    "I could not DM you. Enable DMs from server members, then try again.",
                    ephemeral=bool(ctx.interaction),
                )
                await self._end_report_flow(str(ctx.author.id))
                return

            await dm.send(
                "You may cancel at any time by typing `cancel`.\n"
                "Let’s build this event report."
            )

            category_idx = await self._prompt_index_dm(
                dm,
                ctx.author,
                "Soft launch is currently raid-only.\n"
                "Select event category:\n"
                + "\n".join(f"{i+1}. {name}" for i, name in enumerate(EVENT_CATEGORIES)),
                1,
                len(EVENT_CATEGORIES),
            )
            category = EVENT_CATEGORIES[category_idx - 1]
            parsed_outcome = self._parse_outcome(
                await self._prompt_dm(
                    dm,
                    ctx.author,
                    "Enter outcome (`Win`, `Loss`, `Draw`, `N/A`):",
                )
            )

            opponent_choices, single_guild_test_mode = await self._opponent_guild_choices(str(ctx.guild.id))
            if not opponent_choices:
                await dm.send("No selectable opponent clans found. Register clans first.")
                await self._end_report_flow(str(ctx.author.id))
                return

            opponent_lines = []
            for i, row in enumerate(opponent_choices):
                gid = str(row["guild_id"])
                gname = row.get("guild_name") or f"Clan-{gid}"
                if single_guild_test_mode and gid == str(ctx.guild.id):
                    gname = f"[TEST] {gname}"
                opponent_lines.append(f"{i+1}. {gname} (`{gid}`)")

            opp_idx = await self._prompt_index_dm(
                dm,
                ctx.author,
                "Select opponent clan:\n" + "\n".join(opponent_lines),
                1,
                len(opponent_choices),
            )
            opponent_row = opponent_choices[opp_idx - 1]
            opponent_guild_id = str(opponent_row["guild_id"])

            if len(links) == 1:
                selected = links[0]
            else:
                link_lines = "\n".join(
                    f"{i+1}. **{row.get('gamertag') or 'Unknown'}** (`{row['xuid']}`)"
                    for i, row in enumerate(links[:20])
                )
                x_idx = await self._prompt_index_dm(
                    dm,
                    ctx.author,
                    "Select your XUID for recent match lookup:\n" + link_lines,
                    1,
                    min(20, len(links)),
                )
                selected = links[x_idx - 1]
            xuid = str(selected["xuid"])
            await dm.send(f"Using **{selected.get('gamertag') or xuid}** (`{xuid}`).")

            match_id_list = await self._collect_match_ids_via_dm(dm, ctx.author, xuid)
            if not match_id_list:
                await dm.send("No matches added. Event report cancelled.")
                await self._end_report_flow(str(ctx.author.id))
                return

        except asyncio.TimeoutError:
            await self._send_ctx(
                ctx,
                "DM flow timed out. Run `/report_event` again when ready.",
                ephemeral=bool(ctx.interaction),
            )
            await self._end_report_flow(str(ctx.author.id))
            return
        except asyncio.CancelledError:
            await self._send_ctx(
                ctx,
                "Event report cancelled in DM. No data was saved.",
                ephemeral=bool(ctx.interaction),
            )
            await self._end_report_flow(str(ctx.author.id))
            return

        post_dm_started_at = time.perf_counter()
        existing = await db.find_existing_matches(match_id_list)
        if existing:
            lines = [f"- `{row['match_id']}` already logged on event `{row['event_id']}`" for row in existing[:20]]
            if len(existing) > 20:
                lines.append(f"...and {len(existing) - 20} more")
            await dm.send("One or more selected matches are already logged:\n" + "\n".join(lines))
            await self._send_ctx(
                ctx,
                "Cannot log this event because one or more matches are already recorded:\n" + "\n".join(lines)
            )
            await self._end_report_flow(str(ctx.author.id))
            return

        await self._defer_ctx(ctx)

        event_id, event_number, event_code = await db.log_event(
            guild_id=str(ctx.guild.id),
            category=category.strip(),
            coordinator_id=str(ctx.author.id),
            outcome=parsed_outcome,
            opponent_guild_id=opponent_guild_id,
            opponent_name=opponent_row["guild_name"],
            notes="",
        )
        log_event_elapsed = time.perf_counter() - post_dm_started_at

        try:
            for match_id in match_id_list:
                match_started_at = time.perf_counter()
                await match_data.ingest_match_to_event(match_id, event_id, parsed_outcome)
                self.bot.logger.info(
                    "report_event ingest match_id=%s event_id=%s elapsed=%.2fs",
                    match_id,
                    event_id,
                    time.perf_counter() - match_started_at,
                )
        except Exception as exc:
            await db.delete_event(event_id)
            await dm.send(
                f"Event ingest failed on match `{match_id}` ({type(exc).__name__}). No data was saved."
            )
            await self._send_ctx(
                ctx,
                f"Event ingest failed on match `{match_id}` ({type(exc).__name__}). "
                "No data was saved for this event."
            )
            await self._end_report_flow(str(ctx.author.id))
            return

        celo_started_at = time.perf_counter()
        celo_result = await db.apply_celo_for_event(event_id)
        celo_elapsed = time.perf_counter() - celo_started_at

        models_started_at = time.perf_counter()
        model_result = await db.apply_celo_models_for_event(event_id)
        models_elapsed = time.perf_counter() - models_started_at

        dispatch_started_at = time.perf_counter()
        event = await db.get_event(event_id)
        posted_labels = await self._dispatch_operation_report(event) if event else []
        dispatch_elapsed = time.perf_counter() - dispatch_started_at

        self.bot.logger.info(
            "report_event complete event_id=%s matches=%s log_event=%.2fs celo=%.2fs models=%.2fs dispatch=%.2fs total_after_dm=%.2fs total_command=%.2fs",
            event_id,
            len(match_id_list),
            log_event_elapsed,
            celo_elapsed,
            models_elapsed,
            dispatch_elapsed,
            time.perf_counter() - post_dm_started_at,
            time.perf_counter() - command_started_at,
        )

        lines = [
            "## Event Logged",
            f"**Code:** `{event_code}`",
            f"**Event ID:** `{event_id}`",
            "",
            f"- Matches: **{len(match_id_list)}**",
            f"- CELO adjusted users: **{celo_result['adjusted_users']}**",
        ]
        if int(celo_result.get("processed_games", 0)) == 0:
            skipped = int(celo_result.get("skipped_insufficient_linked_teams", 0))
            if skipped > 0:
                lines.append(
                    "- CELO status: skipped (no eligible gamertags were found across enough teams)."
                )
            else:
                lines.append("- CELO status: skipped (games were already processed or had no eligible participants).")
        else:
            lines.append(f"- CELO status: processed **{int(celo_result.get('processed_games', 0))}** game(s)")
        model_lines = []
        for model_key, payload in model_result.get("models", {}).items():
            model_lines.append(
                f"{model_key}: {payload['adjusted_users']} users / {payload['processed_games']} games"
            )
        if model_lines:
            lines.append(f"- Training models: {'; '.join(model_lines)}")
        if single_guild_test_mode and opponent_guild_id == str(ctx.guild.id):
            lines.append("- Mode: single-guild test (self-opponent)")
        if posted_labels:
            lines.append("")
            lines.append("**Reports posted to:**")
            lines.extend(f"- {label}" for label in posted_labels)
        else:
            lines.append("")
            lines.append(
                "**Reports:** none posted (no report forums configured/reachable). "
                "Use `/set_event_channel` in participating guilds."
            )

        await dm.send("\n".join(lines))
        await self._send_ctx(ctx, "\n".join(lines))
        await self._end_report_flow(str(ctx.author.id))

    @commands.hybrid_command(
        name="import_cortana_event",
        description="Import a Cortana logged event into Isabel CELO without re-entering match data.",
    )
    async def import_cortana_event(
        self,
        ctx: commands.Context,
        cortana_event_id: int,
        opponent: str,
        category: str = "Raid",
        post_reports: bool = False,
    ):
        if not ctx.guild:
            await self._send_ctx(ctx, "This command can only be used in a server.", ephemeral=bool(ctx.interaction))
            return

        if not await db.is_registered_guild(str(ctx.guild.id)):
            await self._send_ctx(ctx, "This clan is not registered. Use `/register_clan` first.", ephemeral=bool(ctx.interaction))
            return

        if not await self._can_report_event(ctx):
            await self._send_ctx(
                ctx,
                "You must be an administrator or whitelisted event-reporter role to import Cortana events.",
                ephemeral=bool(ctx.interaction),
            )
            return

        opponent_text = str(opponent or "").strip()
        opponent_id = "".join(ch for ch in opponent_text if ch.isdigit())
        opponent_name = None if opponent_id else opponent_text
        if not opponent_id and not opponent_name:
            await self._send_ctx(ctx, "Pass a registered Isabel clan/server ID or an unregistered opponent name.", ephemeral=bool(ctx.interaction))
            return

        await self._defer_ctx(ctx, ephemeral=bool(ctx.interaction))
        try:
            result = await cortana_import.import_cortana_event(
                cortana_event_id=int(cortana_event_id),
                isabel_guild_id=str(ctx.guild.id),
                isabel_opponent_guild_id=opponent_id or None,
                opponent_name=opponent_name,
                coordinator_id=str(ctx.author.id),
                category=category,
            )
        except Exception as exc:
            await self._send_ctx(
                ctx,
                f"Cortana import failed: `{type(exc).__name__}` - {str(exc)[:900]}",
                ephemeral=bool(ctx.interaction),
            )
            return

        status = str(result.get("status") or "imported")
        event_id = result.get("event_id")

        posted_labels = []
        if post_reports and event_id:
            event = await db.get_event(int(event_id))
            posted_labels = await self._dispatch_operation_report(event) if event else []

        model_games = sum(
            int(payload.get("processed_games", 0))
            for payload in result.get("models", {}).get("models", {}).values()
        )
        event_label = (
            f"`{result['event_code']}` / `{event_id}`"
            if event_id and result.get("event_code")
            else "Already present in Isabel"
        )
        title = "## Cortana Event Already Synced" if status == "already_synced" else "## Cortana Event Imported"
        lines = [
            title,
            f"**Cortana Event:** `{result['cortana_event_id']}`",
            f"**Isabel Event:** {event_label}",
            f"**Opponent:** {result['opponent_name']} (`{result['opponent_guild_id']}`)",
            f"- Games: **{result['games']}**",
            f"- Duplicate games skipped: **{result.get('duplicate_games', 0)}**",
            f"- Player stat rows: **{result['stats']}**",
            f"- Medal rows: **{result['medals']}**",
            f"- Official CELO: **{result['official']['processed_games']}** game(s), **{result['official']['adjusted_users']}** adjusted profile(s)",
            f"- Training model passes: **{model_games}**",
        ]
        if status == "already_synced":
            existing = ", ".join(f"`{eid}`" for eid in result.get("existing_event_ids", [])[:5])
            lines.append(f"- CELO status: **no-op**; match IDs were already logged{f' in event(s) {existing}' if existing else ''}.")
        elif status == "partial_import":
            lines.append("- CELO status: **partial import**; duplicate games were skipped and new games were processed.")
        if post_reports:
            lines.append("- Isabel report posting: **enabled**")
            if status == "already_synced":
                lines.append("  - No new report posted for already-synced matches.")
            else:
                lines.extend(f"  - {label}" for label in posted_labels) if posted_labels else lines.append("  - No report forums were reachable.")
        else:
            lines.append("- Isabel report posting: **suppressed**")
        await self._send_ctx(ctx, "\n".join(lines), ephemeral=bool(ctx.interaction))

    @commands.hybrid_command(
        name="push_updated_report",
        description="Owner-only: push the latest DB state to all guild event report threads.",
    )
    async def push_updated_report(self, ctx: commands.Context, event_id: int):
        if not self._is_owner(str(ctx.author.id)):
            await self._send_ctx(
                ctx,
                "Only configured bot owners can run this command.",
                ephemeral=bool(ctx.interaction),
            )
            return

        event = await db.get_event(int(event_id))
        if not event:
            await self._send_ctx(ctx, f"Event `{event_id}` not found.", ephemeral=bool(ctx.interaction))
            return

        refresh_result = await self._refresh_operation_reports_for_event(int(event_id))
        if bool(refresh_result.get("republished")):
            posted = list(refresh_result.get("posted_labels", []))
            if posted:
                await self._send_ctx(
                    ctx,
                    f"No tracked report posts were found, so I republished event `{event_id}` to: {', '.join(posted)}",
                    ephemeral=bool(ctx.interaction),
                )
            else:
                await self._send_ctx(
                    ctx,
                    "No report posts found and no configured report forums were reachable.",
                    ephemeral=bool(ctx.interaction),
                )
            return

        refreshed = int(refresh_result.get("refreshed", 0))
        recreated = int(refresh_result.get("recreated", 0))
        failures = list(refresh_result.get("failures", []))

        lines = [
            f"Report push complete for event `{event_id}`.",
            f"Updated threads: `{refreshed}`",
            f"Recreated threads: `{recreated}`",
        ]
        if failures:
            lines.append("Failures:")
            lines.extend(f"- {f}" for f in failures[:20])
            if len(failures) > 20:
                lines.append(f"- ...and {len(failures) - 20} more")

        await self._send_ctx(ctx, "\n".join(lines), ephemeral=bool(ctx.interaction))

    @commands.hybrid_command(
        name="complete_ticket",
        description="Owner-only: resolve a contest ticket by ticket ID.",
    )
    async def complete_ticket(self, ctx: commands.Context, ticket_id: int):
        if not self._is_owner(str(ctx.author.id)):
            await self._send_ctx(
                ctx,
                "Only configured bot owners can run this command.",
                ephemeral=bool(ctx.interaction),
            )
            return

        ticket = await db.get_event_contest(int(ticket_id))
        if not ticket:
            await self._send_ctx(ctx, f"Ticket `{self._format_ticket_label(ticket_id)}` was not found.", ephemeral=bool(ctx.interaction))
            return

        resolved = await db.resolve_event_contest(int(ticket_id), str(ctx.author.id))
        if not resolved:
            await self._send_ctx(
                ctx,
                f"Ticket `{self._format_ticket_label(ticket_id)}` is already resolved.",
                ephemeral=bool(ctx.interaction),
            )
            return

        refresh_result = await self._refresh_operation_reports_for_event(int(ticket["event_id"]))
        refreshed = int(refresh_result.get("refreshed", 0))
        recreated = int(refresh_result.get("recreated", 0))
        failures = list(refresh_result.get("failures", []))
        republished = bool(refresh_result.get("republished"))
        posted_labels = list(refresh_result.get("posted_labels", []))

        lines = [f"Resolved ticket `{self._format_ticket_label(ticket_id)}` for event `{ticket['event_id']}`."]
        if republished:
            if posted_labels:
                lines.append("No tracked report posts were found, so I republished to: " + ", ".join(posted_labels))
            else:
                lines.append("No tracked report posts were found, and no report forums were reachable.")
        else:
            lines.append(f"Auto-refresh complete: `{refreshed}` updated thread(s), `{recreated}` recreated thread(s).")
            if failures:
                lines.append(f"Refresh failures: `{len(failures)}`")

        await self._send_ctx(ctx, "\n".join(lines), ephemeral=bool(ctx.interaction))

    @commands.hybrid_command(name="recalc_event_celo", description="Recalculate CELO for a previously ingested event.")
    async def recalc_event_celo(self, ctx: commands.Context, event_id: int):
        if not ctx.guild:
            await ctx.send("This command can only be used in a server.")
            return

        if not await self._can_report_event(ctx):
            await ctx.send("You must be an administrator or whitelisted event-reporter role.")
            return

        event = await db.get_event(int(event_id))
        if not event:
            await ctx.send(f"Event `{event_id}` not found.")
            return

        if str(event["guild_id"]) != str(ctx.guild.id) and str(event.get("opponent_guild_id") or "") != str(ctx.guild.id):
            await ctx.send("This event does not involve your guild.")
            return

        result = await db.apply_celo_for_event(int(event_id))
        model_result = await db.apply_celo_models_for_event(int(event_id))
        model_summary = ", ".join(
            f"{model_key}: {payload['processed_games']} game(s)"
            for model_key, payload in model_result.get("models", {}).items()
        )
        await ctx.send(
            f"CELO recalculation complete for event `{event_id}`. "
            f"Processed games: `{result['processed_games']}`, adjusted users: `{result['adjusted_users']}`. "
            f"Training models: {model_summary or 'none'}."
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Events(bot))
