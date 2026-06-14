import asyncio

from discord.ext import commands, tasks

from helpers import match_data


class AuthKeepalive(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.halo_auth_keepalive.start()

    def cog_unload(self):
        self.halo_auth_keepalive.cancel()

    @tasks.loop(hours=12)
    async def halo_auth_keepalive(self):
        try:
            await match_data.refresh_halo_auth()
            self.bot.logger.info("Halo auth keepalive completed successfully.")
        except Exception as exc:
            self.bot.logger.exception("Halo auth keepalive failed: %s", type(exc).__name__)

    @halo_auth_keepalive.before_loop
    async def before_halo_auth_keepalive(self):
        await self.bot.wait_until_ready()
        await asyncio.sleep(300)


async def setup(bot):
    await bot.add_cog(AuthKeepalive(bot))
