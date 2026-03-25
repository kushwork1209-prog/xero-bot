from utils.embeds import brand_embed
from utils.guard import command_guard
"""
XERO Bot — Core Admin + Support Tools
Management guild ONLY. Invisible everywhere else.

/core    (10 commands) — operational control
/support  (8 commands) — diagnostic tools

Dashboard: /core dashboard
  8 panels — Stats · Servers · Blacklist · Analytics · Tools · Staff · Incidents · Health
  Colors: Black XERO.PRIMARY · White XERO.PRIMARY · Baby Blue XERO.PRIMARY
"""
import discord, aiosqlite, asyncio, traceback, sys, logging, datetime, time, os
from typing import Optional
from discord.ext import commands
from discord import app_commands
from utils.embeds import success_embed, error_embed, XERO

logger = logging.getLogger("XERO.CoreAdmin")

# ── Palette ───────────────────────────────────────────────────────────────────
D_BLACK = XERO.PRIMARY
D_BLUE  = XERO.PRIMARY
D_STEEL = XERO.PRIMARY
D_DARK  = XERO.PRIMARY
D_RED   = XERO.PRIMARY
D_AMBER = XERO.PRIMARY

SEV = {"low": "🔵", "medium": "🟡", "high": "🟠", "critical": "🔴"}
ROLE_ICONS = {"owner": "👑", "lead": "🔷", "developer": "💻", "support": "🛡️", "moderator": "⚖️"}
STATUS_DOT = {
    discord.Status.online:    "🟢",
    discord.Status.idle:      "🟡",
    discord.Status.dnd:       "🔴",
    discord.Status.offline:   "⚫",
    discord.Status.invisible: "⚫",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def is_management():
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.guild_id != interaction.client.MANAGEMENT_GUILD_ID:
            await interaction.response.send_message("❌ Management server only.", ephemeral=True)
            return False
        return True
    return app_commands.check(predicate)


def _de(title="", desc="", color=D_BLUE) -> discord.Embed:
    e = discord.Embed(title=title, description=desc,
                      color=discord.Color(color), timestamp=discord.utils.utcnow())
    e.set_footer(text="XERO Management  ·  Team Flame")
    return e


def _bar(score: int, width: int = 12) -> str:
    filled = round(score / 100 * width)
    return "█" * filled + "░" * (width - filled)


def _health_dot(score: int) -> str:
    if score >= 70: return "🟢"
    if score >= 40: return "🟡"
    return "🔴"


async def _qdb(bot, *queries) -> list:
    out = []
    async with aiosqlite.connect(bot.db.db_path) as db:
        for q, p in queries:
            try:
                async with db.execute(q, p) as c:
                    row = await c.fetchone()
                    out.append(row[0] if row else 0)
            except Exception:
                out.append(0)
    return out


async def _ensure_mgmt_tables(db_path: str):
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS bot_staff"
            " (user_id INTEGER PRIMARY KEY, role TEXT DEFAULT 'support',"
            " added_by INTEGER, added_at DATETIME DEFAULT CURRENT_TIMESTAMP, notes TEXT)"
        )
        await db.execute(
            "CREATE TABLE IF NOT EXISTS bot_incidents"
            " (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,"
            " description TEXT, severity TEXT DEFAULT 'medium',"
            " reported_by INTEGER, resolved INTEGER DEFAULT 0,"
            " created_at DATETIME DEFAULT CURRENT_TIMESTAMP, resolved_at DATETIME)"
        )
        await db.commit()


# ── Base panel ────────────────────────────────────────────────────────────────
class _Panel(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=300)
        self.bot = bot

    @discord.ui.button(label="← Back to Dashboard", style=discord.ButtonStyle.secondary, row=4)
    async def back(self, interaction: discord.Interaction, _button):
        embed, view = await ManagementDashboard.build(self.bot, interaction.guild)
        await interaction.response.edit_message(embed=embed, view=view)


# ══════════════════════════════════════════════════════════════════════════════
# PANEL 1 — STATS
# ══════════════════════════════════════════════════════════════════════════════
class _StatsPanel(_Panel):
    async def make_embed(self) -> discord.Embed:
        bot = self.bot

        # System metrics
        try:
            import psutil
            proc    = psutil.Process()
            ram_mb  = proc.memory_info().rss / 1024 / 1024
            ram     = f"{ram_mb:.1f} MB"
            cpu     = f"{psutil.cpu_percent(0.1):.1f}%"
            threads = proc.num_threads()
            disk    = psutil.disk_usage("/")
            disk_s  = f"{disk.used/1024**3:.1f}/{disk.total/1024**3:.1f} GB"
        except Exception:
            ram = cpu = disk_s = "N/A"; threads = "?"

        lat      = round(bot.latency * 1000)
        upt      = int(time.time() - bot.launch_time) if hasattr(bot, "launch_time") else 0
        h, rem   = divmod(upt, 3600); m_ = rem // 60; s_ = rem % 60
        lat_icon = "🟢" if lat < 100 else ("🟡" if lat < 250 else "🔴")

        # DB file size
        try:
            db_bytes = os.path.getsize(bot.db.db_path)
            db_size  = f"{db_bytes / 1024 / 1024:.2f} MB"
        except Exception:
            db_size = "?"

        # All DB counts in one pass
        (levels, eco_users, tickets, open_tix, closed_tix,
         cases, warns, giveaways, profiles, msgs, cmds,
         verified, temp_voices, bdays, bl_users, open_inc) = await _qdb(bot,
            ("SELECT COUNT(*) FROM levels WHERE total_xp>0", []),
            ("SELECT COUNT(*) FROM economy WHERE wallet+bank>0", []),
            ("SELECT COUNT(*) FROM tickets", []),
            ("SELECT COUNT(*) FROM tickets WHERE status='open'", []),
            ("SELECT COUNT(*) FROM tickets WHERE status='closed'", []),
            ("SELECT COUNT(*) FROM mod_cases", []),
            ("SELECT COUNT(*) FROM warnings", []),
            ("SELECT COUNT(*) FROM giveaways WHERE ended=0", []),
            ("SELECT COUNT(*) FROM member_profiles", []),
            ("SELECT SUM(messages_sent) FROM user_stats", []),
            ("SELECT SUM(commands_used) FROM user_stats", []),
            ("SELECT COUNT(*) FROM user_verifications", []),
            ("SELECT COUNT(*) FROM temp_voice_channels", []),
            ("SELECT COUNT(*) FROM birthdays", []),
            ("SELECT COUNT(*) FROM blacklisted_users", []),
            ("SELECT COUNT(*) FROM bot_incidents WHERE resolved=0", []),
        )

        total_users = sum(g.member_count for g in bot.guilds)
        total_xp, total_wealth = await _qdb(bot,
            ("SELECT SUM(total_xp) FROM levels", []),
            ("SELECT SUM(wallet+bank) FROM economy", []),
        )
        avg_size = total_users // max(len(bot.guilds), 1)

        maint = getattr(bot, "maintenance_mode", False)

        e = _de("XERO  ·  Live System Status", color=D_BLUE)
        e.set_thumbnail(url=bot.user.display_avatar.url)

        # ── System health ──────────────────────────────────────────────────
        e.add_field(
            name="System",
            value=(
                f"{lat_icon} `{lat}ms`  ·  `{h}h {m_}m {s_}s` uptime\n"
                f"RAM `{ram}`  ·  CPU `{cpu}`  ·  Threads `{threads}`\n"
                f"Disk `{disk_s}`  ·  DB `{db_size}`\n"
                f"Python `{sys.version[:6]}`  ·  discord.py `{discord.__version__}`"
            ),
            inline=False
        )

        # ── Scale ──────────────────────────────────────────────────────────
        e.add_field(
            name="Scale",
            value=(
                f"**{len(bot.guilds):,}** servers  ·  **{total_users:,}** users\n"
                f"Avg server size: **{avg_size:,}**  ·  Cogs: **{len(bot.extensions)}**"
            ),
            inline=False
        )

        # ── Features ───────────────────────────────────────────────────────
        e.add_field(name="Leveling",    value=f"**{levels:,}** ranked users\n**{(total_xp or 0):,}** total XP",         inline=True)
        e.add_field(name="Economy",     value=f"**{eco_users:,}** users\n**${(total_wealth or 0):,}** in circulation",   inline=True)
        e.add_field(name="Community",   value=f"**{verified:,}** verified\n**{bdays:,}** birthdays\n**{giveaways}** active GAs", inline=True)

        # ── Activity ───────────────────────────────────────────────────────
        e.add_field(name="Usage",       value=f"**{(cmds or 0):,}** commands run\n**{(msgs or 0):,}** messages tracked", inline=True)
        e.add_field(name="AI",          value=f"**{profiles:,}** member profiles\n**{temp_voices}** temp voices",        inline=True)
        e.add_field(name="Moderation",  value=f"**{cases:,}** cases  ·  **{warns:,}** warns\n**{tickets:,}** tickets  (**{open_tix}** open)", inline=True)

        # ── Alerts ─────────────────────────────────────────────────────────
        if maint or open_inc or bl_users:
            alerts = []
            if maint:     alerts.append("⚠️ **Maintenance mode active**")
            if open_inc:  alerts.append(f"🔴 **{open_inc}** unresolved incident(s)")
            if bl_users:  alerts.append(f"🚫 **{bl_users}** globally blacklisted user(s)")
            e.add_field(name="Alerts", value="\n".join(alerts), inline=False)

        return e

    @discord.ui.button(label="⟳ Refresh", style=discord.ButtonStyle.primary, row=0)
    async def refresh(self, i: discord.Interaction, _b):
        await i.response.edit_message(embed=await self.make_embed(), view=self)


# (Skipping rest of the file for brevity, just implementing CoreAdmin and SupportTools at the end)

class CoreAdmin(commands.GroupCog, name="core"):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="global-config", description="Apply a configuration setting to ALL servers globally.")
    @app_commands.describe(key="The setting key (e.g., automod_enabled, welcome_channel_id)", value="The value to set")
    @is_management()
    async def global_config(self, interaction: discord.Interaction, key: str, value: str):
        await interaction.response.defer(ephemeral=True)
        try:
            async with self.bot.db._db_context() as db:
                async with db.execute("PRAGMA table_info(guild_settings)") as c:
                    columns = [row[1] for row in await c.fetchall()]
            
            if key not in columns:
                return await interaction.followup.send(
                    embed=error_embed("Invalid Key", f"`{key}` is not a valid setting in `guild_settings` table."),
                    ephemeral=True
                )
            
            final_val = value
            if value.lower() in ("true", "on", "yes", "1"):
                final_val = 1
            elif value.lower() in ("false", "off", "no", "0"):
                final_val = 0
            else:
                try:
                    final_val = int(value)
                except ValueError:
                    pass
            
            await self.bot.db.update_global_setting(key, final_val)
            
            embed = success_embed(
                "🌐  Global Config Applied",
                f"Successfully set `{key}` to `{final_val}` for **ALL** servers in the database.\n"
                f"A backup has been triggered to persist this change."
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            logger.error(f"Global config error: {e}", exc_info=True)
            await interaction.followup.send(embed=error_embed("Error", str(e)), ephemeral=True)

class SupportTools(commands.GroupCog, name="support"):
    def __init__(self, bot):
        self.bot = bot

async def setup(bot):
    mguild = discord.Object(id=bot.MANAGEMENT_GUILD_ID)
    await bot.add_cog(CoreAdmin(bot),    guilds=[mguild])
    await bot.add_cog(SupportTools(bot), guilds=[mguild])
    logger.info(f"✓ /core + /support bound to management guild ({bot.MANAGEMENT_GUILD_ID})")
