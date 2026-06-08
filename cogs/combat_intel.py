import discord
from discord.ext import commands

from helpers import db
from helpers import match_data


BOARD_ALIASES = {
    "kills": "kills",
    "damage": "damage",
    "kd": "kd",
    "kda": "kda",
    "played": "events_played",
    "events_played": "events_played",
    "hosted": "events_hosted",
    "events_hosted": "events_hosted",
    "medals": "medals",
    "medal_count": "medals",
    "medal_score": "medal_score",
    "proficiency": "proficiency",
}

BOARD_LABELS = {
    "kills": "Career Kills",
    "damage": "Career Damage",
    "kd": "K/D",
    "kda": "K/D/A",
    "events_played": "Events Played",
    "events_hosted": "Events Hosted",
    "medals": "Medals Earned",
    "medal_score": "Medal Score",
    "proficiency": "Proficiency Score",
}


class CombatIntel(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _scope_guild_id(self, ctx: commands.Context, scope: str | None) -> str | None:
        normalized = (scope or "global").strip().lower()
        if normalized in {"server", "guild", "local", "this"} and ctx.guild:
            return str(ctx.guild.id)
        return None

    def _metric_value(self, row: dict, board: str) -> str:
        key = BOARD_ALIASES.get(board, board)
        if key == "events_played":
            return str(int(row.get("events_played", 0) or 0))
        if key == "events_hosted":
            return str(int(row.get("events_hosted", 0) or 0))
        if key == "medals":
            return str(int(row.get("medal_count", 0) or 0))
        if key in {"kd", "kda"}:
            return f"{float(row.get(key, 0) or 0):.2f}"
        return f"{int(row.get(key, 0) or 0):,}"

    @commands.hybrid_command(name="combat_leaderboard", description="Show combat leaderboards: kills, damage, kd, kda, medals, proficiency.")
    async def combat_leaderboard(self, ctx: commands.Context, board: str = "kills", limit: int = 10, scope: str = "global"):
        normalized = BOARD_ALIASES.get((board or "").strip().lower().replace("-", "_"))
        if not normalized:
            await ctx.send(
                "Unknown board. Use one of: `kills`, `damage`, `kd`, `kda`, "
                "`played`, `hosted`, `medals`, `medal_score`, `proficiency`."
            )
            return

        guild_id = self._scope_guild_id(ctx, scope)
        rows = await db.get_combat_leaderboard(normalized, guild_id=guild_id, limit=limit)
        scope_label = f"server {ctx.guild.name}" if guild_id and ctx.guild else "global"
        title = BOARD_LABELS.get(normalized, normalized.replace("_", " ").title())

        if not rows:
            await ctx.send(f"No `{title}` data found for {scope_label}.")
            return

        lines = []
        for idx, row in enumerate(rows[: max(1, min(25, int(limit or 10)))], start=1):
            lines.append(f"`{idx:>2}.` <@{row['discord_id']}> - **{self._metric_value(row, normalized)}**")

        embed = discord.Embed(
            title=f"{title} Leaderboard",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"Scope: {scope_label}")
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="backfill_medals", description="Owner-only: fetch medals for existing logged games.")
    async def backfill_medals(self, ctx: commands.Context, limit: int = 25):
        owners = {str(x) for x in getattr(self.bot, "config", {}).get("owners", [])}
        # Older Isabel builds keep config module-global; fall back to bot owner check if present.
        if owners and str(ctx.author.id) not in owners:
            await ctx.send("Only bot owners can run this command.")
            return
        if not owners and not await self.bot.is_owner(ctx.author):
            await ctx.send("Only bot owners can run this command.")
            return

        if ctx.interaction and not ctx.interaction.response.is_done():
            await ctx.interaction.response.defer(thinking=True, ephemeral=True)
        result = await match_data.backfill_medals_for_logged_games(limit=limit)
        message = (
            f"Medal backfill checked `{result['games_seen']}` game(s) and wrote `{result['rows_written']}` medal row(s)."
        )
        if result["failures"]:
            message += "\nFailures:\n" + "\n".join(f"- {x}" for x in result["failures"][:10])
        if ctx.interaction:
            await ctx.interaction.followup.send(message, ephemeral=True)
        else:
            await ctx.send(message)


async def setup(bot: commands.Bot):
    await bot.add_cog(CombatIntel(bot))
