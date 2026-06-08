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

    @commands.hybrid_command(name="ask_isabel", description="Ask Isabel about CELO, events, or policy.")
    async def ask_isabel(self, ctx: commands.Context, *, prompt: str):
        client = getattr(self.bot, "openai_client", None)
        model = getattr(self.bot, "openai_model", "gpt-4o-mini")
        is_interaction = ctx.interaction is not None

        if client is None:
            if is_interaction and not ctx.interaction.response.is_done():
                await ctx.interaction.response.send_message(
                    "OpenAI client is not configured for this bot.",
                    ephemeral=True,
                )
            elif is_interaction:
                await ctx.interaction.followup.send(
                    "OpenAI client is not configured for this bot.",
                    ephemeral=True,
                )
            else:
                await ctx.send("OpenAI client is not configured for this bot.")
            return

        if is_interaction and not ctx.interaction.response.is_done():
            await ctx.interaction.response.defer(thinking=True)

        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=400,
            )
            text = (response.choices[0].message.content or "").strip()
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
