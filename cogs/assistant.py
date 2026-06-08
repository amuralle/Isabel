import asyncio
import time

from discord.ext import commands


SYSTEM_PROMPT = (
    "You are Isabel from Halo Wars, serving as a professional operations and analytics assistant "
    "for a cross-community Halo CELO system in Discord. "
    "Be concise, clear, and practical. "
    "Primary responsibilities: explain CELO ratings/deviation/confidence, event ingestion flow, "
    "reporting permissions, clan registration/allegiance/XUID linking, and operation report behavior. "
    "Use command-accurate guidance for this bot: "
    "setup and identity commands include /register_clan, /unregister_clan, /clan_profile, "
    "/registered_clans, /clan_roster, /set_allegiance, /my_allegiances, /link_xuid, /unlink_xuid, /my_xuids, /bulk_register; "
    "event commands include /set_event_channel, /add_event_reporter_role, /remove_event_reporter_role, "
    "/list_event_reporter_roles, /report_event, /import_cortana_event, and /recalc_event_celo; "
    "profile commands include /career, /celo, /celo_models, /celo_leaderboard, "
    "and /combat_leaderboard; /career is the main personal profile and includes combat stats, medals, and proficiency. "
    "assistant and distribution commands include /ask_isabel and /invite_isabel. "
    "Owner-only maintenance commands exist: /push_updated_report, /complete_ticket, /backfill_medals, and /sync_commands. "
    "The current soft launch is raid-only: /report_event should be described as logging raid events, not Blitz or 4v4. "
    "Cortana compatibility: UNSC External events can be pushed from Cortana into Isabel automatically; "
    "opponents may be registered Isabel clans or unregistered external teams, and Isabel report posting can stay suppressed. "
    "During training mode, Isabel displays multiple CELO models side by side: official, baseline, conservative, performance, raid_flat, and raid_stepwise. "
    "Do not claim unsupported capabilities, do not invent data, and when uncertain ask for specifics "
    "(event ID, clan, or command context)."
)


class Assistant(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        config = getattr(bot, "config", {})
        self.max_prompt_chars = _config_int(config, "ask_isabel_max_prompt_chars", 1200, minimum=100)
        self.max_output_tokens = _config_int(config, "ask_isabel_max_output_tokens", 350, minimum=50, maximum=1000)
        self.user_cooldown_seconds = _config_int(config, "ask_isabel_user_cooldown_seconds", 60, minimum=0)
        self.guild_cooldown_seconds = _config_int(config, "ask_isabel_guild_cooldown_seconds", 10, minimum=0)
        self._user_last_used: dict[int, float] = {}
        self._guild_last_used: dict[int, float] = {}
        self._cooldown_lock = asyncio.Lock()

    async def _send_private(self, ctx: commands.Context, message: str) -> None:
        is_interaction = ctx.interaction is not None
        if is_interaction and not ctx.interaction.response.is_done():
            await ctx.interaction.response.send_message(message, ephemeral=True)
        elif is_interaction:
            await ctx.interaction.followup.send(message, ephemeral=True)
        else:
            await ctx.send(message)

    async def _check_rate_limit(self, ctx: commands.Context) -> int:
        now = time.monotonic()
        guild_id = ctx.guild.id if ctx.guild else 0
        owner_ids = {str(owner_id) for owner_id in getattr(self.bot, "config", {}).get("owners", [])}
        if str(ctx.author.id) in owner_ids:
            return 0

        async with self._cooldown_lock:
            remaining = 0
            if self.user_cooldown_seconds:
                elapsed = now - self._user_last_used.get(ctx.author.id, 0.0)
                remaining = max(remaining, int(self.user_cooldown_seconds - elapsed))
            if guild_id and self.guild_cooldown_seconds:
                elapsed = now - self._guild_last_used.get(guild_id, 0.0)
                remaining = max(remaining, int(self.guild_cooldown_seconds - elapsed))
            if remaining > 0:
                return remaining
            self._user_last_used[ctx.author.id] = now
            if guild_id:
                self._guild_last_used[guild_id] = now
            return 0

    @commands.hybrid_command(name="ask_isabel", description="Ask Isabel about CELO, events, or policy.")
    async def ask_isabel(self, ctx: commands.Context, *, prompt: str):
        client = getattr(self.bot, "openai_client", None)
        model = getattr(self.bot, "openai_model", "gpt-4o-mini")
        is_interaction = ctx.interaction is not None
        prompt = (prompt or "").strip()

        if client is None:
            await self._send_private(ctx, "OpenAI client is not configured for this bot.")
            return

        if not prompt:
            await self._send_private(ctx, "Give Isabel a question to answer.")
            return

        if len(prompt) > self.max_prompt_chars:
            await self._send_private(
                ctx,
                f"That prompt is too long. Keep /ask_isabel under {self.max_prompt_chars:,} characters.",
            )
            return

        remaining = await self._check_rate_limit(ctx)
        if remaining > 0:
            await self._send_private(ctx, f"Isabel is cooling down. Try again in {remaining} second(s).")
            return

        if is_interaction and not ctx.interaction.response.is_done():
            await ctx.interaction.response.defer(thinking=True)

        try:
            response = await asyncio.to_thread(
                client.chat.completions.create,
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=self.max_output_tokens,
            )
            text = (response.choices[0].message.content or "").strip()
            if len(text) > 1900:
                text = f"{text[:1897]}..."
            if is_interaction:
                await ctx.interaction.followup.send(text or "No response generated.")
            else:
                await ctx.send(text or "No response generated.")
        except Exception as exc:
            error_message = f"Isabel hit an upstream error: `{type(exc).__name__}`"
            if is_interaction:
                await ctx.interaction.followup.send(error_message, ephemeral=True)
            else:
                await ctx.send(error_message)


async def setup(bot: commands.Bot):
    await bot.add_cog(Assistant(bot))


def _config_int(config: dict, key: str, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        value = int(config.get(key, default))
    except (TypeError, ValueError):
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value
