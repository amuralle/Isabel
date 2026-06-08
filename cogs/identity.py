import csv
import re
from io import StringIO

import discord
from discord.ext import commands

from helpers import db
from helpers import xuid as xuid_helper


class Identity(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _can_bulk_register(self, ctx: commands.Context) -> bool:
        if not ctx.guild:
            return False
        role_ids = [str(role.id) for role in getattr(ctx.author, "roles", [])]
        return await db.can_user_report_events(
            guild_id=str(ctx.guild.id),
            user_id=str(ctx.author.id),
            role_ids=role_ids,
            is_admin=bool(ctx.author.guild_permissions.administrator),
        )

    def _parse_bulk_line(self, line: str) -> list[str]:
        normalized = line.strip()
        if "|" in normalized:
            return [part.strip() for part in normalized.split("|")]
        return [part.strip() for part in next(csv.reader(StringIO(normalized)))]

    def _extract_discord_id(self, raw: str | None) -> str | None:
        if not raw:
            return None
        match = re.search(r"(\d{15,25})", raw)
        return match.group(1) if match else None

    @commands.hybrid_command(name="link_xuid", description="Link your Discord account to an XUID and gamertag.")
    async def link_xuid(self, ctx: commands.Context, gamertag: str, xuid: str | None = None):
        if not ctx.guild:
            await ctx.send("Use this command inside your clan server so Isabel can bind your registration to a clan.")
            return

        user_id = str(ctx.author.id)
        current_guild_id = str(ctx.guild.id)

        allegiances = await db.list_user_allegiances(user_id)
        linked_xuids = await db.get_xuid_links(user_id)
        existing_allegiance = allegiances[0] if allegiances else None

        if existing_allegiance:
            allegiance_guild_id = str(existing_allegiance["guild_id"])
            if allegiance_guild_id != current_guild_id and linked_xuids:
                await ctx.send(
                    "You already have a registered allegiance and linked XUID(s) in another server. "
                    f"Your allegiance clan is `{allegiance_guild_id}`."
                )
                return
        else:
            allegiance_set = await db.set_user_allegiance(user_id, current_guild_id)
            if not allegiance_set:
                await ctx.send(
                    "This server is not an active registered clan, so I can't set allegiance yet. "
                    "Ask an admin to run `/register_clan` first."
                )
                return

        resolved_xuid = xuid
        if not resolved_xuid:
            result = xuid_helper.resolve_xuid(gamertag)
            if not result["xuid"]:
                await ctx.send(f"Could not resolve XUID: {result['error']}")
                return
            resolved_xuid = str(result["xuid"])

        existing = await db.get_xuid_link_record(str(resolved_xuid))
        if existing and existing.get("discord_id") and str(existing["discord_id"]) != user_id:
            await ctx.send(
                f"XUID `{resolved_xuid}` is already linked to <@{existing['discord_id']}>."
            )
            return

        await db.upsert_xuid(str(resolved_xuid), gamertag.strip(), user_id)
        await db.register_clan_roster_xuid(
            guild_id=current_guild_id,
            xuid=str(resolved_xuid),
            gamertag=gamertag.strip(),
            tier=None,
            registered_by=user_id,
            discord_id=user_id,
        )

        logged_games = await db.count_logged_games_for_xuid(str(resolved_xuid))
        if logged_games > 0:
            rebuild = await db.rebuild_all_celo()
            await ctx.send(
                f"Linked **{gamertag}** to XUID `{resolved_xuid}` for {ctx.author.mention}.\n"
                f"Found `{logged_games}` previously logged game(s) for that XUID, so I rebuilt CELO from logged events. "
                f"Processed `{rebuild['official_processed_games']}` official CELO game(s)."
            )
            return

        await ctx.send(f"Linked **{gamertag}** to XUID `{resolved_xuid}` for {ctx.author.mention}.")

    @commands.hybrid_command(
        name="bulk_register",
        description="Admin/coordinator: bulk register clan gamertags with initial CELO tiers.",
    )
    async def bulk_register(self, ctx: commands.Context, *, roster: str):
        if not ctx.guild:
            await ctx.send("Use this command inside the clan server.")
            return

        if not await db.is_registered_guild(str(ctx.guild.id)):
            await ctx.send("This clan is not registered yet. Ask an admin to run `/register_clan` first.")
            return

        if not await self._can_bulk_register(ctx):
            await ctx.send("You must be an administrator or whitelisted event coordinator to bulk register members.")
            return

        if ctx.interaction and not ctx.interaction.response.is_done():
            await ctx.interaction.response.defer(thinking=True, ephemeral=True)

        successes: list[str] = []
        failures: list[str] = []
        needs_rebuild = False
        lines = [line.strip() for line in roster.splitlines() if line.strip()]
        for line in lines[:50]:
            try:
                parts = self._parse_bulk_line(line)
                if len(parts) < 2:
                    raise ValueError("Expected `Gamertag, Tier[, XUID][, Discord ID]`.")
                gamertag = parts[0].strip()
                tier = db.normalize_tier(parts[1])
                xuid_value = parts[2].strip() if len(parts) >= 3 and parts[2].strip() else None
                discord_id = self._extract_discord_id(parts[3] if len(parts) >= 4 else None)

                if not xuid_value:
                    result = xuid_helper.resolve_xuid(gamertag)
                    if not result["xuid"]:
                        raise ValueError(f"Could not resolve XUID: {result['error']}")
                    xuid_value = str(result["xuid"])

                registered = await db.register_clan_roster_xuid(
                    guild_id=str(ctx.guild.id),
                    xuid=str(xuid_value),
                    gamertag=gamertag,
                    tier=tier,
                    registered_by=str(ctx.author.id),
                    discord_id=discord_id,
                )
                owner = f" -> <@{discord_id}>" if discord_id else ""
                if registered.get("rebuild_recommended"):
                    needs_rebuild = True
                successes.append(
                    f"- **{registered['gamertag']}** `{registered['xuid']}` • {registered['tier']} "
                    f"seed **{registered['seed_score']}**{owner}"
                )
            except Exception as exc:
                failures.append(f"- `{line}`: {exc}")

        if len(lines) > 50:
            failures.append(f"- Only the first 50 rows were processed; {len(lines) - 50} row(s) were skipped.")

        chunks = ["## Bulk Clan Registration"]
        chunks.append(f"Registered: **{len(successes)}**")
        if successes:
            chunks.append("\n".join(successes[:20]))
            if len(successes) > 20:
                chunks.append(f"...and {len(successes) - 20} more")
        if failures:
            chunks.append(f"Failures: **{len(failures)}**")
            chunks.append("\n".join(failures[:15]))
        if needs_rebuild:
            rebuild = await db.rebuild_all_celo()
            chunks.append(
                "Rebuilt CELO from logged events after reseeding previously seen XUIDs. "
                f"Processed **{rebuild['official_processed_games']}** official CELO game(s)."
            )
        message = "\n".join(chunks)
        if ctx.interaction:
            await ctx.interaction.followup.send(message, ephemeral=True)
        else:
            await ctx.send(message)

    @commands.hybrid_command(name="unlink_xuid", description="Unlink one of your XUIDs.")
    async def unlink_xuid(self, ctx: commands.Context, xuid: str):
        success = await db.unlink_xuid_for_user(str(ctx.author.id), str(xuid))
        if success:
            await ctx.send(f"Unlinked XUID `{xuid}` from your account.")
            return
        await ctx.send("That XUID is not linked to your account.")

    @commands.hybrid_command(name="my_xuids", description="List XUID links for you or another member.")
    async def my_xuids(self, ctx: commands.Context, member_id: str | None = None):
        target_id = str(ctx.author.id) if member_id is None else str(member_id)
        links = await db.get_xuid_links(target_id)
        if not links:
            await ctx.send(f"No XUID links found for <@{target_id}>.")
            return

        lines = [f"- `{row['xuid']}` • **{row['gamertag']}**" for row in links]
        await ctx.send(f"XUID links for <@{target_id}>:\n" + "\n".join(lines[:20]))


async def setup(bot: commands.Bot):
    await bot.add_cog(Identity(bot))
