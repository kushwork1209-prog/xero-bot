"""XERO Bot — /help command
Paginated, category-based help. Every command listed. No one gets lost.
"""
import discord
from discord.ext import commands
from discord import app_commands
from utils.embeds import XERO
import logging

logger = logging.getLogger("XERO.Help")

CATEGORIES = {
    "🤖 AI": {
        "color": discord.Color.blurple(),
        "desc":  "Powered by NVIDIA Llama — free AI for everyone in your server.",
        "cmds": [
            ("/ai ask",            "Ask any question, optionally analyze an image URL"),
            ("/ai chat",           "Context-aware conversation (remembers history)"),
            ("/ai imagine",        "🆕 Generate AI images — 5 style options, no key needed"),
            ("/ai analyze-image",  "Deep NVIDIA vision analysis of any image URL"),
            ("/ai summarize",      "Summarize text or the last N chat messages"),
            ("/ai translate",      "Translate text to any language"),
            ("/ai brainstorm",     "Generate creative ideas on any topic"),
            ("/ai code-explain",   "Detailed explanation of any code"),
            ("/ai code-debug",     "Find and fix bugs in your code"),
            ("/ai sentiment",      "Deep emotional & sentiment analysis"),
            ("/ai rewrite",        "Rewrite text in a different style"),
            ("/ai grammar",        "Full grammar & spelling correction"),
            ("/ai generate",       "Generate stories, emails, posts, scripts"),
            ("/ai fact-check",     "Verify any claim with AI analysis"),
            ("/ai roast",          "Funny AI-generated roast of anything"),
            ("/ai persona",        "Change AI personality for this server (Admin)"),
            ("/ai clear-memory",   "Reset conversation memory (Mod+)"),
        ],
    },
    "🎵 Music": {
        "color": discord.Color.purple(),
        "desc":  "High-quality music via SoundCloud. No extra bots needed.",
        "cmds": [
            ("/music play",        "Play a song by name or URL (SoundCloud / YouTube)"),
            ("/music stop",        "Stop playback and leave voice channel"),
            ("/music skip",        "Skip the current song"),
            ("/music pause",       "Pause playback"),
            ("/music resume",      "Resume paused playback"),
            ("/music nowplaying",  "Show current song with full details"),
            ("/music queue",       "View the full queue with modes & volume"),
            ("/music volume",      "Set volume 1–100%"),
            ("/music loop",        "Toggle loop mode for current song"),
            ("/music shuffle",     "Toggle shuffle mode for the queue"),
            ("/music remove",      "Remove a song from queue by position"),
            ("/music clear",       "Clear the entire queue"),
            ("/music lyrics",      "🆕 Fetch lyrics for current or any song"),
        ],
    },
    "🎮 Fun": {
        "color": discord.Color.orange(),
        "desc":  "22 AI-powered fun commands. Every response unique.",
        "cmds": [
            ("/fun 8ball",         "AI magic 8-ball — real answers to your questions"),
            ("/fun would-you-rather", "AI-generated dilemmas, fresh every time"),
            ("/fun never-have-i-ever", "AI-generated statements by theme"),
            ("/fun fortune",       "Personalized AI fortune cookie"),
            ("/fun roast",         "Get AI-roasted (brutal but harmless)"),
            ("/fun compliment",    "Genuine AI compliment for someone"),
            ("/fun trivia",        "Live trivia — first correct answer wins"),
            ("/fun trivia-scores", "Trivia leaderboard for this server"),
            ("/fun joke",          "AI joke in any style (dark/pun/dad/nerd...)"),
            ("/fun ship",          "Calculate love compatibility between two users"),
            ("/fun roll",          "Roll custom dice — NdS+M notation (e.g. 2d20+5)"),
            ("/fun choose",        "XERO picks from your options"),
            ("/fun meme",          "Fresh meme from Reddit"),
            ("/fun cat",           "Random cat photo"),
            ("/fun dog",           "Random dog photo"),
            ("/fun rps",           "Rock Paper Scissors vs XERO"),
            ("/fun rate",          "AI rates anything you throw at it"),
            ("/fun fact",          "Surprising AI fact about any topic"),
            ("/fun debate",        "AI argues both sides of any topic"),
        ],
    },
    "💰 Economy": {
        "color": discord.Color.gold(),
        "desc":  "Full economy system — earn, invest, trade, and compete.",
        "cmds": [
            ("/economy balance",   "Check your wallet and bank balance"),
            ("/economy daily",     "Claim daily coins with streak bonus"),
            ("/economy work",      "Work for coins every 30 minutes"),
            ("/economy weekly",    "Claim your weekly bonus"),
            ("/economy deposit",   "Move coins from wallet to bank"),
            ("/economy withdraw",  "Move coins from bank to wallet"),
            ("/economy pay",       "Send coins to another user"),
            ("/economy rob",       "Attempt to rob another user"),
            ("/economy shop",      "Browse the server shop"),
            ("/economy buy",       "Purchase an item from the shop"),
            ("/economy inventory", "View your items"),
            ("/economy leaderboard", "Top earners in this server"),
            ("/economy slots",     "Play the slot machine"),
            ("/economy flip",      "Coin flip gambling"),
            ("/economy heist",     "Start a crew heist (group game)"),
            ("/economy stocks",    "View and trade the stock market"),
        ],
    },
    "📊 Levels": {
        "color": discord.Color.green(),
        "desc":  "XP system with level rewards, voice XP, and leaderboards.",
        "cmds": [
            ("/levels rank",       "Your level card with XP bar and server rank"),
            ("/levels leaderboard","Top 10 members by XP"),
            ("/levels setxp",      "Set a user's XP (Admin)"),
            ("/levels setlevel",   "Set a user's level (Admin)"),
            ("/levels rewards",    "View level-up role rewards"),
            ("/levels addreward",  "Add a role reward for a level (Admin)"),
            ("/levels removereward","Remove a level reward (Admin)"),
            ("/levels reset",      "Reset a user's XP (Admin)"),
        ],
    },
    "🛡️ Moderation": {
        "color": discord.Color.red(),
        "desc":  "Full moderation suite — cases logged, AI-assisted, audit-trail ready.",
        "cmds": [
            ("/mod ban",           "Ban a member with reason and duration"),
            ("/mod kick",          "Kick a member"),
            ("/mod timeout",       "Timeout a member for a duration"),
            ("/mod warn",          "Issue a warning (logged to DB)"),
            ("/mod warnings",      "View all warnings for a user"),
            ("/mod clearwarnings", "Clear all warnings for a user"),
            ("/mod mute",          "Mute a member"),
            ("/mod unmute",        "Unmute a member"),
            ("/mod purge",         "Bulk delete messages"),
            ("/mod slowmode",      "Set channel slowmode"),
            ("/mod lock",          "Lock a channel"),
            ("/mod unlock",        "Unlock a channel"),
            ("/mod case",          "View a mod case by ID"),
            ("/mod cases",         "View all cases for a user"),
            ("/mod nick",          "Change a member's nickname"),
        ],
    },
    "⚙️ Config": {
        "color": discord.Color.light_grey(),
        "desc":  "All server settings in one place. One command → everything.",
        "cmds": [
            ("/config",            "Master dashboard — all 12 feature panels"),
            ("/setup welcome",     "Configure welcome messages and cards"),
            ("/setup farewell",    "Configure farewell messages"),
            ("/setup logging",     "Set up logging channels"),
            ("/setup autorole",    "Set a role given to all new members"),
            ("/setup verification","Set up button verification"),
            ("/setup tickets",     "Set up the ticket support system"),
            ("/setup automod",     "Configure AutoMod rules"),
            ("/setup leveling",    "Configure XP and level-up settings"),
            ("/setup economy",     "Configure economy settings"),
            ("/setup ai",          "Configure AI features"),
        ],
    },
    "🔧 Utility": {
        "color": discord.Color.teal(),
        "desc":  "Productivity tools — reminders, polls, AFK, and more.",
        "cmds": [
            ("/utility remind",    "Set a reminder for yourself"),
            ("/utility poll",      "Create a poll with up to 10 options"),
            ("/utility afk",       "Set AFK status — bot notifies when you're mentioned"),
            ("/utility avatar",    "Get anyone's avatar in full resolution"),
            ("/utility banner",    "Get anyone's profile banner"),
            ("/utility calc",      "Calculator with unit conversions"),
            ("/utility color",     "Convert and visualize any color"),
            ("/utility timestamp", "Generate Discord timestamp codes"),
            ("/utility weather",   "Current weather for any city"),
            ("/utility translate", "Quick text translation"),
        ],
    },
    "ℹ️ Info": {
        "color": discord.Color.blue(),
        "desc":  "Deep info commands — user, server, role, channel, bot stats.",
        "cmds": [
            ("/info user",         "Full profile — level, economy, roles, mod history"),
            ("/info server",       "Complete server breakdown"),
            ("/info role",         "Everything about a role — perms, members, style"),
            ("/info channel",      "Channel details — settings, permissions, topic"),
            ("/info bot",          "XERO system stats — uptime, latency, reach"),
            ("/info emoji",        "Details on any custom server emoji"),
            ("/info invite",       "Look up any Discord invite link"),
            ("/info permissions",  "Full permission breakdown for any user in any channel"),
            ("/invite",            "Get the invite link to add XERO to your server"),
        ],
    },
    "🎉 Events & Social": {
        "color": discord.Color.pink(),
        "desc":  "Birthday tracking, starboard, giveaways, announcements.",
        "cmds": [
            ("/birthday set",      "Set your birthday — bot announces it every year"),
            ("/birthday list",     "View upcoming birthdays in this server"),
            ("/giveaway start",    "Start a giveaway with timer and winner count"),
            ("/giveaway end",      "End a giveaway early"),
            ("/giveaway reroll",   "Reroll a giveaway winner"),
            ("/suggest",           "Submit a suggestion to server staff"),
            ("/suggest review",    "Review suggestions (Mod+)"),
            ("/starboard",         "View starboard configuration"),
            ("/announce",          "Post a rich server announcement"),
            ("/social profile",    "View your social profile and badges"),
            ("/social rep",        "Give reputation to a user (once per day)"),
            ("/social marry",      "Propose marriage to a user"),
            ("/social divorce",    "End a marriage"),
        ],
    },
    "🔒 Security": {
        "color": discord.Color.dark_red(),
        "desc":  "Anti-raid, anti-nuke, age gates, AutoMod — always on guard.",
        "cmds": [
            ("/security status",   "View current security configuration"),
            ("/security lockdown", "Emergency server lockdown"),
            ("/security unlockdown","Remove server lockdown"),
            ("/automod status",    "View AutoMod rules"),
            ("/automod toggle",    "Enable/disable AutoMod"),
            ("/autoresponder add", "Add an auto-response trigger"),
            ("/autoresponder list","View all auto-responses"),
            ("/autoresponder remove","Remove an auto-response"),
        ],
    },
}


class HelpCategorySelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label=name.split(" ", 1)[1] if " " in name else name,
                emoji=name.split(" ")[0],
                description=info["desc"][:50],
                value=name,
            )
            for name, info in CATEGORIES.items()
        ]
        super().__init__(
            placeholder="Browse a category...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        name = self.values[0]
        info = CATEGORIES[name]
        embed = _build_category_embed(name, info)
        await interaction.response.edit_message(embed=embed, view=self.view)


class HelpView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(HelpCategorySelect())

    @discord.ui.button(label="Home", style=discord.ButtonStyle.secondary, emoji="🏠", row=1)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=_build_home_embed(interaction.client), view=self)


def _build_home_embed(bot: discord.Client) -> discord.Embed:
    embed = discord.Embed(
        title="🤖  XERO Bot — Help Center",
        description=(
            "**300+ commands.** Everything free. Use the dropdown to browse any category.\n\n"
            "**Quick links:**\n"
            "• `/ai imagine` — generate AI images\n"
            "• `/music play` — play any song\n"
            "• `/config` — configure your server\n"
            "• `/economy daily` — claim daily coins\n"
            "• `/invite` — add XERO to another server\n"
        ),
        color=discord.Color.blurple(),
    )
    total_guilds = len(bot.guilds) if bot.guilds else 0
    total_users  = sum(g.member_count for g in bot.guilds) if bot.guilds else 0
    embed.add_field(
        name="📊 Stats",
        value=f"**{total_guilds:,}** servers  •  **{total_users:,}** users  •  **{len(CATEGORIES)}** categories",
        inline=False,
    )
    cats = "\n".join(
        f"{name}  — {len(info['cmds'])} commands"
        for name, info in CATEGORIES.items()
    )
    embed.add_field(name="📂 Categories", value=cats, inline=False)
    embed.set_footer(text="XERO Bot | Built by Team Flame | All premium features, free.")
    if bot.user:
        embed.set_thumbnail(url=bot.user.display_avatar.url)
    return embed


def _build_category_embed(name: str, info: dict) -> discord.Embed:
    embed = discord.Embed(
        title=f"{name}  Commands",
        description=info["desc"],
        color=info["color"],
    )
    lines = "\n".join(
        f"`{cmd}` — {desc}" for cmd, desc in info["cmds"]
    )
    embed.add_field(name=f"{len(info['cmds'])} Commands", value=lines, inline=False)
    embed.set_footer(text="XERO Bot  •  Use the dropdown to switch category  •  /help to return home")
    return embed


class Help(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="help", description="Browse all XERO commands by category.")
    async def help(self, interaction: discord.Interaction):
        embed = _build_home_embed(self.bot)
        view  = HelpView()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(Help(bot))
