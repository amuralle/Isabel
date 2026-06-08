import discord
from discord.ext import commands

from helpers import db
from helpers import proficiency_graph


class Career(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(name="career", description="View CELO + event-only service stats.")
    async def career(self, ctx: commands.Context, member: discord.Member | None = None):
        target = member or ctx.author
        guild_scope = str(ctx.guild.id) if ctx.guild else None
        scope_key = ctx.guild.name if ctx.guild else "global"

        stats = await db.fetch_user_career_data(str(target.id), guild_id=guild_scope)
        medal_summary = await db.get_user_medal_summary(str(target.id), guild_id=guild_scope, limit=8)
        celo_summary = await db.get_user_weighted_xuid_celo(str(target.id))
        gamertags = await db.get_gamertags(str(target.id))
        allegiances = await db.list_user_allegiances(str(target.id))
        allegiance_text = "None"
        if allegiances:
            row = allegiances[0]
            allegiance_text = f"{row.get('guild_name') or row['guild_id']} (`{row['guild_id']}`)"

        embed = discord.Embed(
            title=f"Isabel Career Report • {target.display_name}",
            description=f"Member: {target.mention}\nClan scope: `{scope_key}`",
            color=discord.Color.blue(),
        )
        embed.set_thumbnail(url=target.display_avatar.url)

        if celo_summary:
            components = sorted(
                celo_summary["components"],
                key=lambda row: (int(row.get("games_played") or 0), float(row.get("rating") or 0)),
                reverse=True,
            )
            component_text = "\n".join(
                f"- **{row['gamertag']}**: {db.celo_score(row['rating'])} "
                f"({int(row['games_played'] or 0)} games)"
                for row in components[:6]
            )
            if len(components) > 6:
                component_text += f"\n- +{len(components) - 6} more linked gamertag(s)"
            if celo_summary["weight_basis"] == "games_played":
                weight_note = f"Weighted by match frequency across {celo_summary['active_xuid_count']} active gamertag(s)."
            else:
                weight_note = "No CELO games yet; showing an equal seed average."
            celo_text = (
                f"Overall CELO: **{db.celo_score(celo_summary['rating'])}** "
                f"(dev {float(celo_summary['deviation']):.1f})\n"
                f"Record: **{celo_summary['wins']} / {celo_summary['losses']} / {celo_summary['draws']}** "
                f"across **{celo_summary['games_played']}** games\n"
                f"{weight_note}\n\n"
                f"{component_text}"
            )
        else:
            celo_text = "No linked gamertag CELO profiles yet."
        embed.add_field(name="CELO Snapshot", value=celo_text, inline=False)
        embed.add_field(
            name="Combat Totals",
            value=(
                f"K/D/KDA: **{stats['kd']} / {stats['kda']}**\n"
                f"Kills / Deaths / Assists: **{stats['tot_kills']} / {stats['tot_deaths']} / {stats['tot_assists']}**\n"
                f"Total Damage: **{stats['total_damage']:,}**"
            ),
            inline=False,
        )

        top_medals = "\n".join(
            f"- **{row['medal_name']}** x{int(row['count'] or 0):,}"
            for row in medal_summary["top_medals"]
        ) or "No medal data logged yet."
        top_proficiencies = "\n".join(
            f"- **{row['field']}**: {int(row['score'] or 0):,}"
            for row in medal_summary["proficiencies"][:6]
        ) or "No proficiency-classed medals logged yet."
        embed.add_field(
            name="Medal Record",
            value=(
                f"Total medals: **{medal_summary['medal_count']:,}**\n"
                f"Proficiency score: **{medal_summary['proficiency_score']:,}**\n\n"
                f"**Top Medals**\n{top_medals}"
            ),
            inline=False,
        )
        embed.add_field(name="Proficiencies", value=top_proficiencies, inline=False)

        hosted_text = "\n".join(f"- {k}: {v}" for k, v in stats["hosted"].items()) or "None"
        attended_text = "\n".join(f"- {k}: {v}" for k, v in stats["attended"].items()) or "None"
        embed.add_field(name="Event Activity • Hosted", value=hosted_text, inline=False)
        embed.add_field(name="Event Activity • Attended", value=attended_text, inline=False)
        embed.add_field(name="Clan Allegiance", value=allegiance_text, inline=False)

        gt_text = "\n".join(f"- {g}" for g in gamertags) if gamertags else "No linked gamertags"
        embed.add_field(name="Identity", value=gt_text, inline=False)

        embed.set_footer(text="Isabel Career Service Record")
        chart_title = gamertags[0] if len(gamertags) == 1 else target.display_name
        chart = proficiency_graph.render_proficiency_chart(chart_title, medal_summary)
        if chart:
            file = discord.File(chart, filename="career_proficiency_profile.png")
            embed.set_image(url="attachment://career_proficiency_profile.png")
            await ctx.send(embed=embed, file=file)
        else:
            await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Career(bot))
