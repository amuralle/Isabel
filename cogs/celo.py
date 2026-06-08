import os

import discord
from discord.ext import commands

from helpers import db


MODEL_ALIASES = {
    "official": "official",
    "current": "official",
    "baseline": "baseline",
    "conservative": "conservative",
    "performance": "performance",
    "raid_flat": "raid_flat",
    "raid": "raid_stepwise",
    "raid_stepwise": "raid_stepwise",
}


def _configured_mainframe_guild_ids(bot: commands.Bot) -> set[str]:
    config = getattr(bot, "config", {}) or {}
    configured = os.getenv("ISABEL_MAINFRAME_GUILD_IDS") or config.get("mainframe_guild_ids")
    if configured is None:
        configured = config.get("mainframe_guild_id")
    if isinstance(configured, (list, tuple, set)):
        return {str(value).strip() for value in configured if str(value).strip()}
    return {
        part.strip()
        for part in str(configured or "").split(",")
        if part.strip()
    }


class CELOLeaderboardView(discord.ui.View):
    def __init__(
        self,
        *,
        guild: discord.Guild,
        requester_id: int,
        model_key: str,
        model_label: str,
        guild_scope_id: str | None,
        scope_label: str,
        page_size: int,
    ):
        super().__init__(timeout=180)
        self.guild = guild
        self.requester_id = requester_id
        self.model_key = model_key
        self.model_label = model_label
        self.guild_scope_id = guild_scope_id
        self.scope_label = scope_label
        self.page_size = max(1, min(25, int(page_size or 10)))
        self.offset = 0
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.requester_id:
            return True
        await interaction.response.send_message(
            "Open your own CELO leaderboard to page through it.",
            ephemeral=True,
        )
        return False

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass

    async def _load_page(self) -> tuple[discord.Embed, bool]:
        rows = await db.get_clan_celo_model_leaderboard(
            model_key=self.model_key,
            guild_id=self.guild_scope_id,
            limit=self.page_size + 1,
            offset=self.offset,
        )
        has_next = len(rows) > self.page_size
        display_rows = rows[: self.page_size]

        lines = []
        for idx, row in enumerate(display_rows, start=self.offset + 1):
            owner = f" • <@{row['discord_id']}>" if row.get("discord_id") else ""
            tier = f" • {row['tier']}" if row.get("tier") else ""
            record = f"{int(row['wins'] or 0)}-{int(row['losses'] or 0)}-{int(row['draws'] or 0)}"
            lines.append(
                f"`{idx:>2}.` **{row['gamertag']}**{owner}{tier} - "
                f"**{db.celo_score(row['rating'])}** "
                f"({int(row['games_played'] or 0)} games, {record})"
            )

        page_number = (self.offset // self.page_size) + 1
        start_rank = self.offset + 1
        end_rank = self.offset + len(display_rows)
        range_text = f"Ranks {start_rank}-{end_rank}" if display_rows else "No ranked entries"
        embed = discord.Embed(
            title=f"{self.scope_label} • {self.model_label} Leaderboard",
            description="\n".join(lines) if lines else "No CELO profiles found for this scope.",
            color=discord.Color.green(),
        )
        embed.set_footer(
            text=f"Page {page_number} • {range_text} • Model: {self.model_key}"
        )
        return embed, has_next

    def _sync_buttons(self, has_next: bool) -> None:
        self.previous_button.disabled = self.offset <= 0
        self.next_button.disabled = not has_next

    async def refresh(self, interaction: discord.Interaction | None = None):
        embed, has_next = await self._load_page()
        self._sync_buttons(has_next)
        if interaction:
            await interaction.response.edit_message(embed=embed, view=self)
        return embed

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.offset = max(0, self.offset - self.page_size)
        await self.refresh(interaction)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.primary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.offset += self.page_size
        await self.refresh(interaction)


class CELO(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _normalize_model(self, model: str | None) -> str:
        return MODEL_ALIASES.get((model or "official").strip().lower(), "official")

    @commands.hybrid_command(name="celo", description="Show weighted Discord-user CELO model ratings for you or another member.")
    async def celo(self, ctx: commands.Context, member: discord.Member | None = None):
        target = member or ctx.author
        target_id = str(target.id)
        target_label = target.mention

        rows = await db.get_user_weighted_xuid_celo_models(target_id)
        if not rows:
            await ctx.send(f"No linked gamertags found for {target_label}. Use `/link_xuid` first.")
            return

        official = next((row for row in rows if row["model_key"] == "official"), rows[0])
        components = sorted(
            official["components"],
            key=lambda row: (int(row.get("games_played") or 0), float(row.get("rating") or 0)),
            reverse=True,
        )
        embed = discord.Embed(
            title="CELO Training Comparison",
            description=(
                f"Weighted Discord-user ratings for {target_label}. "
                "Isabel calculates CELO at the XUID/gamertag level, then weights linked identities by match frequency."
            ),
            color=discord.Color.blurple(),
        )
        for row in rows:
            if row["weight_basis"] == "games_played":
                basis = f"{row['active_xuid_count']} active of {row['xuid_count']} linked gamertag(s)"
            else:
                basis = f"{row['xuid_count']} linked seed profile(s)"
            embed.add_field(
                name=f"{row['label']} (`{row['model_key']}`)",
                value=(
                    f"Score: **{db.celo_score(row['rating'])}**\n"
                    f"Deviation: **{float(row['deviation']):.1f}**\n"
                    f"Games: **{row['games_played']}** across **{basis}**\n"
                    f"Record: **{row['wins']} / {row['losses']} / {row['draws']}**"
                ),
                inline=False,
            )
        linked_text = "\n".join(
            f"- **{row['gamertag']}**: {db.celo_score(row['rating'])} "
            f"({int(row['games_played'] or 0)} games)"
            for row in components[:10]
        )
        if len(components) > 10:
            linked_text += f"\n- +{len(components) - 10} more linked gamertag(s)"
        embed.add_field(name="Linked Gamertags", value=linked_text, inline=False)
        embed.set_footer(text="Training mode: ratings are keyed by XUID, with Discord ownership optional.")
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="celo_models", description="List CELO models currently displayed during training mode.")
    async def celo_models(self, ctx: commands.Context):
        rows = db.list_celo_model_configs()
        embed = discord.Embed(
            title="CELO Models",
            description="These models run side by side while Isabel is in training mode.",
            color=discord.Color.dark_teal(),
        )
        for row in rows:
            embed.add_field(
                name=f"{row['label']} (`{row['model_key']}`)",
                value=row["description"],
                inline=False,
            )
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="celo_leaderboard", description="Show CELO rankings for this clan or the mainframe global ladder.")
    async def celo_leaderboard(self, ctx: commands.Context, limit: int = 10, model: str = "official"):
        if not ctx.guild:
            await ctx.send("Use this command inside a clan server.")
            return

        model_key = self._normalize_model(model)
        page_size = max(1, min(25, int(limit)))
        label = "Official CELO"
        for config in db.list_celo_model_configs():
            if config["model_key"] == model_key:
                label = config["label"]
                break

        guild_scope_id = None if str(ctx.guild.id) in _configured_mainframe_guild_ids(self.bot) else str(ctx.guild.id)
        scope_label = "Global CELO Mainframe" if guild_scope_id is None else ctx.guild.name

        view = CELOLeaderboardView(
            guild=ctx.guild,
            requester_id=ctx.author.id,
            model_key=model_key,
            model_label=label,
            guild_scope_id=guild_scope_id,
            scope_label=scope_label,
            page_size=page_size,
        )
        embed = await view.refresh()
        message = await ctx.send(embed=embed, view=view)
        view.message = message


async def setup(bot: commands.Bot):
    await bot.add_cog(CELO(bot))
