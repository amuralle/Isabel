import discord
from discord.ext import commands
from urllib.parse import urlencode


PUBLIC_HELP = [
    (
        "Start Here",
        [
            ("/clan_setup_help", "Set up Isabel for a clan server."),
            ("/invite_isabel", "Invite Isabel into another server."),
            ("/ask_isabel", "Ask questions about CELO, events, and setup."),
        ],
    ),
    (
        "Player Identity",
        [
            ("/link_xuid", "Link your gamertag/XUID."),
            ("/my_xuids", "Check linked gamertags."),
            ("/career", "View your CELO, combat stats, medals, and event history."),
        ],
    ),
    (
        "Clan Operations",
        [
            ("/register_clan", "Register this server as a CELO clan."),
            ("/set_event_channel", "Choose the forum for operation reports."),
            ("/bulk_register", "Seed a roster from gamertags and tiers."),
            ("/report_event", "Report a raid event through Isabel."),
            ("/import_cortana_event", "Bring a Cortana-logged external event into Isabel."),
        ],
    ),
    (
        "Leaderboards",
        [
            ("/celo_leaderboard", "Show this clan's CELO leaderboard."),
            ("/combat_leaderboard", "Show combat leaderboards such as kills, damage, K/D, medals, and proficiency."),
            ("/clan_roster", "Show this clan's registered roster."),
        ],
    ),
]


class Help(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(name="help", description="Show Isabel's most useful commands.")
    async def help_command(self, ctx: commands.Context):
        embed = discord.Embed(
            title="Isabel Field Guide",
            description="A small set of commands covers most CELO clan work.",
            color=discord.Color.blurple(),
        )
        for section, commands_list in PUBLIC_HELP:
            embed.add_field(
                name=section,
                value="\n".join(f"`{name}` - {description}" for name, description in commands_list),
                inline=False,
            )
        embed.set_footer(text="Advanced maintenance commands exist, but are intentionally left out of this guide.")
        await ctx.send(embed=embed)

    @commands.hybrid_command(
        name="clan_setup_help",
        description="Step-by-step guide for clan setup and event reporting permissions.",
    )
    async def clan_setup_help(self, ctx: commands.Context):
        embed = discord.Embed(
            title="Clan Setup Guide",
            description="Use this sequence to fully configure Isabel for a clan.",
            color=discord.Color.green(),
        )

        embed.add_field(
            name="1) Register Your Clan",
            value="`/register_clan`\nRegisters this server as a clan in Isabel.",
            inline=False,
        )
        embed.add_field(
            name="2) Set Operation Report Forum",
            value="`/set_event_channel #forum`\nSets the forum where operation report threads are created for events involving this clan.",
            inline=False,
        )
        embed.add_field(
            name="3) Configure Reporter Permissions",
            value=(
                "Admins can always report.\n"
                "Optional role whitelist:\n"
                "`/add_event_reporter_role @role`\n"
                "`/remove_event_reporter_role @role`\n"
                "`/list_event_reporter_roles`"
            ),
            inline=False,
        )
        embed.add_field(
            name="4) User Allegiances",
            value=(
                "Users can have one allegiance at a time to an active registered clan:\n"
                "`/registered_clans`\n"
                "`/set_allegiance` (run it in the target server)\n"
                "`/my_allegiances`"
            ),
            inline=False,
        )
        embed.add_field(
            name="5) Identity + Event Logging",
            value=(
                "Link player identities:\n"
                "`/link_xuid <gamertag> [xuid]`\n"
                "Report raid events:\n"
                "`/report_event`\n"
                "Soft launch is raid-only. Isabel DMs you for outcome, opponent clan, and match selection.\n"
                "Bulk seed the roster:\n"
                "`/bulk_register roster:<Gamertag, Tier, optional XUID, optional Discord ID>`\n"
                "You must already have a linked XUID. Matches can be selected from recent history or entered manually.\n"
                "Notes: duplicate match IDs are rejected; opponent must be a selectable registered clan. "
                "Use thread replies for follow-up notes. "
                "In single-clan test environments, self-opponent is allowed for testing."
            ),
            inline=False,
        )
        embed.add_field(
            name="6) Disable Clan (If Needed)",
            value="`/unregister_clan`\nDeactivates this clan in Isabel (admin-only, current clan only).",
            inline=False,
        )

        await ctx.send(embed=embed)

    @commands.hybrid_command(name="invite_isabel", description="Get the invite link for adding Isabel to another server.")
    async def invite_isabel(self, ctx: commands.Context):
        application_id = str(getattr(self.bot, "config", {}).get("application_id") or "").strip()
        if not application_id:
            await ctx.send("Isabel's application ID is not configured, so I cannot build an invite link.")
            return

        permissions = str(getattr(self.bot, "config", {}).get("invite_permissions", "8"))
        query = urlencode(
            {
                "client_id": application_id,
                "permissions": permissions,
                "scope": "bot applications.commands",
            }
        )
        url = f"https://discord.com/oauth2/authorize?{query}"

        embed = discord.Embed(
            title="Invite Isabel",
            description=f"[Add Isabel to a server]({url})",
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="After Inviting",
            value=(
                "`/register_clan`\n"
                "`/clan_setup_help`\n"
                "`/set_event_channel` if the server wants Isabel report threads"
            ),
            inline=False,
        )
        embed.set_footer(text="Requires Manage Server permission in the target server.")
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Help(bot))
