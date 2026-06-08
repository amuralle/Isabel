import discord
from discord.ext import commands

from helpers import db


class Registry(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(name="register_clan", description="Register this clan in Isabel's CELO registry.")
    async def register_clan(self, ctx: commands.Context):
        if not ctx.guild:
            await ctx.send("This command can only be used in a server.")
            return

        if not ctx.author.guild_permissions.administrator:
            await ctx.send("You must be an administrator to register a clan.")
            return

        await db.register_guild(str(ctx.guild.id), ctx.guild.name, str(ctx.author.id))
        await ctx.send(f"Registered **{ctx.guild.name}** (`{ctx.guild.id}`) as an Isabel clan.")

    @commands.hybrid_command(name="unregister_clan", description="Deactivate this clan in Isabel's CELO registry.")
    async def unregister_clan(self, ctx: commands.Context):
        if not ctx.guild:
            await ctx.send("This command can only be used in a server.")
            return

        if not ctx.author.guild_permissions.administrator:
            await ctx.send("You must be an administrator to unregister a clan.")
            return

        success = await db.unregister_guild(str(ctx.guild.id), str(ctx.author.id))
        if not success:
            await ctx.send("This clan is not currently registered.")
            return
        await ctx.send(f"Unregistered **{ctx.guild.name}** (`{ctx.guild.id}`) from active CELO operations.")

    @commands.hybrid_command(name="clan_profile", description="View this clan's Isabel registry profile.")
    async def clan_profile(self, ctx: commands.Context):
        if not ctx.guild:
            await ctx.send("This command can only be used in a server.")
            return

        row = await db.get_guild_registration(str(ctx.guild.id))
        if not row:
            await ctx.send("This clan is not registered yet. Use `/register_clan`.")
            return

        embed = discord.Embed(title="Clan Registry Profile", color=discord.Color.blurple())
        embed.add_field(name="Clan", value=f"{row['guild_name']} (`{row['guild_id']}`)", inline=False)
        embed.add_field(name="Registered By", value=f"<@{row['registered_by']}>", inline=True)
        embed.add_field(name="Active", value="Yes" if int(row["is_active"]) == 1 else "No", inline=True)
        embed.set_footer(text=f"Created: {row['created_at']} | Updated: {row['updated_at']}")
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="registered_clans", description="List active clans in the CELO registry.")
    async def registered_clans(self, ctx: commands.Context):
        rows = await db.list_registered_guilds()
        if not rows:
            await ctx.send("No active clans are registered yet.")
            return

        lines = [f"- **{r['guild_name']}** (`{r['guild_id']}`)" for r in rows[:50]]
        await ctx.send("**Active Clan Registry**\n" + "\n".join(lines))

    @commands.hybrid_command(name="set_allegiance", description="Set your allegiance to this server's registered clan.")
    async def set_allegiance(self, ctx: commands.Context):
        if not ctx.guild:
            await ctx.send("Use this command in the server you want to set as your allegiance.")
            return

        target_guild_id = str(ctx.guild.id)
        success = await db.set_user_allegiance(str(ctx.author.id), target_guild_id)
        if not success:
            await ctx.send(
                "This server is not an active registered clan. "
                "Ask an admin to run `/register_clan` first."
            )
            return

        await ctx.send(f"Allegiance set for <@{ctx.author.id}> -> **{ctx.guild.name}** (`{target_guild_id}`).")

    @commands.hybrid_command(name="my_allegiances", description="Show your current clan allegiance.")
    async def my_allegiances(self, ctx: commands.Context, member: discord.Member | None = None):
        target = member or ctx.author
        allegiances = await db.list_user_allegiances(str(target.id))

        if not allegiances:
            await ctx.send(f"No allegiance found for {target.mention}.")
            return

        row = allegiances[0]
        guild_name = row.get("guild_name") or f"Clan-{row['guild_id']}"

        embed = discord.Embed(
            title=f"Allegiance • {target.display_name}",
            description=f"**{guild_name}** `{row['guild_id']}`",
            color=discord.Color.green(),
        )
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="clan_roster", description="Show this clan's registered CELO roster.")
    async def clan_roster(self, ctx: commands.Context, limit: int = 25):
        if not ctx.guild:
            await ctx.send("Use this command inside a clan server.")
            return

        rows = await db.list_clan_roster(str(ctx.guild.id), limit=limit)
        if not rows:
            await ctx.send("No clan roster entries found yet. Use `/bulk_register` or `/link_xuid`.")
            return

        lines = []
        for idx, row in enumerate(rows[: max(1, min(50, limit))], start=1):
            owner = f" • <@{row['discord_id']}>" if row.get("discord_id") else ""
            tier = f" • {row['tier']}" if row.get("tier") else ""
            rating = row.get("rating") if row.get("rating") is not None else 1000
            games = int(row.get("games_played") or 0)
            lines.append(f"`{idx:>2}.` **{row['gamertag']}**{owner}{tier} - **{db.celo_score(rating)}** ({games} games)")

        embed = discord.Embed(
            title=f"Clan Roster • {ctx.guild.name}",
            description="\n".join(lines),
            color=discord.Color.green(),
        )
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Registry(bot))
