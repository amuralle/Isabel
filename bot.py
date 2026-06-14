import asyncio
import json
import logging
import os
from pathlib import Path
from itertools import cycle

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv
from openai import OpenAI

from helpers import db


BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"

load_dotenv()

if not CONFIG_PATH.exists():
    raise RuntimeError("config.json not found in Isabel project root.")

with open(CONFIG_PATH, "r", encoding="utf-8") as cfg_file:
    config = json.load(cfg_file)


def config_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def build_logger() -> logging.Logger:
    logger = logging.getLogger("isabel")
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return logger

    formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] %(name)s: %(message)s")

    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    logger.addHandler(stream)

    file_handler = logging.FileHandler(BASE_DIR / "isabel.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


class IsabelBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        message_content_enabled = config_bool(config.get("enable_message_content_intent"), False)
        intents.message_content = message_content_enabled

        prefix = config.get("prefix", "&")
        command_prefix = commands.when_mentioned_or(prefix) if message_content_enabled else commands.when_mentioned
        application_id_raw = str(config.get("application_id", "0"))
        application_id = int(application_id_raw) if application_id_raw.isdigit() else 0

        super().__init__(
            command_prefix=command_prefix,
            intents=intents,
            help_command=None,
            application_id=application_id if application_id > 0 else None,
        )

        self.logger = build_logger()
        self.config = config
        self.prefix = prefix
        self.message_content_enabled = message_content_enabled
        self.synced_once = False
        self.status_messages = cycle(
            [
                (discord.ActivityType.watching, "UNSC telemetry for CELO anomalies"),
                (discord.ActivityType.listening, "field reports from Spartan commanders"),
                (discord.ActivityType.watching, "operation threads across allied guilds"),
                (discord.ActivityType.playing, "war games simulations in the command net"),
                (discord.ActivityType.listening, "contested ticket escalations"),
                (discord.ActivityType.watching, "XUID links and roster synchronization"),
                (discord.ActivityType.playing, "logistics planning for the next operation"),
                (discord.ActivityType.watching, "Banished pressure across the sector"),
            ]
        )

        api_key = os.getenv("OPENAI_API_KEY") or config.get("openai_token")
        self.openai_client = OpenAI(api_key=api_key) if api_key else None
        self.openai_model = config.get("openai_model", "gpt-4o-mini")

    async def setup_hook(self):
        await db.init_db()

        extensions = [
            "cogs.registry",
            "cogs.identity",
            "cogs.events",
            "cogs.career",
            "cogs.combat_intel",
            "cogs.celo",
            "cogs.assistant",
            "cogs.auth_keepalive",
            "cogs.help",
        ]

        for ext in extensions:
            try:
                await self.load_extension(ext)
                self.logger.info("Loaded extension: %s", ext)
            except Exception as exc:
                self.logger.exception("Failed to load extension %s: %s", ext, exc)

    async def on_ready(self):
        self.logger.info("Isabel connected as %s (%s)", self.user, self.user.id if self.user else "unknown")
        self.logger.info(
            "Message content intent is %s. Prefix commands are %s.",
            "enabled" if self.message_content_enabled else "disabled",
            f"enabled with prefix {self.prefix!r}" if self.message_content_enabled else "disabled; slash commands remain primary",
        )
        if not self.synced_once:
            synced = await self.tree.sync()
            self.synced_once = True
            self.logger.info("Synced %s application command(s).", len(synced))
        if not self.rotate_status.is_running():
            await self._set_next_presence()
            self.rotate_status.start()

    async def _set_next_presence(self):
        activity_type, status_text = next(self.status_messages)
        await self.change_presence(activity=discord.Activity(type=activity_type, name=status_text))

    @tasks.loop(minutes=5)
    async def rotate_status(self):
        await self._set_next_presence()

    @rotate_status.before_loop
    async def before_rotate_status(self):
        await self.wait_until_ready()


bot = IsabelBot()


@bot.hybrid_command(name="ping", description="Check if Isabel is online.")
async def ping(ctx: commands.Context):
    await ctx.send(f"Pong. Latency: {round(bot.latency * 1000)}ms")


@bot.hybrid_command(name="sync_commands", description="Sync slash commands (owner-only).")
async def sync_commands(ctx: commands.Context, scope: str = "global"):
    is_interaction = ctx.interaction is not None

    owners = {str(x) for x in config.get("owners", [])}
    if str(ctx.author.id) not in owners:
        if is_interaction and not ctx.interaction.response.is_done():
            await ctx.interaction.response.send_message(
                "Only bot owners can run this command.",
                ephemeral=True,
            )
        elif is_interaction:
            await ctx.interaction.followup.send(
                "Only bot owners can run this command.",
                ephemeral=True,
            )
        else:
            await ctx.send("Only bot owners can run this command.")
        return

    if is_interaction and not ctx.interaction.response.is_done():
        await ctx.interaction.response.defer(thinking=True, ephemeral=True)

    normalized_scope = (scope or "global").strip().lower()
    if normalized_scope not in {"global", "guild"}:
        message = "Invalid scope. Use `global` or `guild`."
        if is_interaction:
            await ctx.interaction.followup.send(message, ephemeral=True)
        else:
            await ctx.send(message)
        return

    try:
        if normalized_scope == "guild":
            if not ctx.guild:
                message = "Guild sync can only be used inside a server."
                if is_interaction:
                    await ctx.interaction.followup.send(message, ephemeral=True)
                else:
                    await ctx.send(message)
                return
            synced = await bot.tree.sync(guild=ctx.guild)
        else:
            # Global sync can occasionally be slow; fail fast with a clear message.
            synced = await asyncio.wait_for(bot.tree.sync(), timeout=90)
    except TimeoutError:
        message = (
            "Sync timed out while waiting on Discord. "
            "Try `scope:guild` first, then rerun global sync."
        )
        if is_interaction:
            await ctx.interaction.followup.send(message, ephemeral=True)
        else:
            await ctx.send(message)
        return

    result_scope = "guild" if normalized_scope == "guild" else "global"
    if is_interaction:
        await ctx.interaction.followup.send(
            f"Synced {len(synced)} command(s) for `{result_scope}` scope.",
            ephemeral=True,
        )
    else:
        await ctx.send(f"Synced {len(synced)} command(s) for `{result_scope}` scope.")


def main():
    token = config.get("token")
    if not token or token == "DISCORD_BOT_TOKEN_PLACEHOLDER":
        raise RuntimeError("Set a real Discord bot token in config.json before running Isabel.")

    bot.run(token)


if __name__ == "__main__":
    main()
