"""
XERO Bot — Core Admin + Support Tools
Management guild ONLY. Invisible everywhere else.

/core    (10 commands) — operational control
/support  (8 commands) — diagnostic tools

Dashboard: /core dashboard
  8 panels — Stats · Servers · Blacklist · Analytics · Tools · Staff · Incidents · Health
  Colors: Black #0A0A0A · White #F5F5F5 · Baby Blue #89CFF0
"""
import discord, aiosqlite, asyncio, traceback, sys, logging, datetime, time, os
from discord.ext import commands
from discord import app_commands
from utils.embeds import success_embed, error_embed, XERO

logger = logging.getLogger("XERO.CoreAdmin")

# ── Palette ───────────────────────────────────────────────────────────────────
D_BLACK = 0x0A0A0A
D_BLUE  = 0x89CFF0
D_STEEL = 0xB8D4E8
D_DARK  = 0x1C1C1C
D_RED   = 0xFF1744
D_AMBER = 0xFFB800

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
        upt      = int(time.time() - bot.start_time) if hasattr(bot, "start_time") else 0
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


# ══════════════════════════════════════════════════════════════════════════════
# PANEL 2 — SERVERS
# ══════════════════════════════════════════════════════════════════════════════
class _ServersPanel(_Panel):
    def __init__(self, bot, page=0, sort="members"):
        super().__init__(bot)
        self.page = page
        self.sort = sort
        self._upd()

    def _sorted(self):
        g = list(self.bot.guilds)
        if self.sort == "members": return sorted(g, key=lambda x: x.member_count, reverse=True)
        if self.sort == "name":    return sorted(g, key=lambda x: x.name.lower())
        if self.sort == "newest":  return sorted(g, key=lambda x: x.me.joined_at or discord.utils.utcnow(), reverse=True)
        if self.sort == "oldest":  return sorted(g, key=lambda x: x.me.joined_at or discord.utils.utcnow())
        return g

    def _upd(self):
        total = max(1, (len(self.bot.guilds) - 1) // 8 + 1)
        self.prev_btn.disabled = self.page <= 0
        self.next_btn.disabled = self.page >= total - 1

    async def _cfg_score(self, g) -> tuple[int, str]:
        try:
            s    = await self.bot.db.get_guild_settings(g.id) or {}
            keys = ["welcome_channel_id", "log_channel_id", "autorole_id",
                    "verify_channel_id", "ticket_support_role_id",
                    "level_up_channel_id", "automod_enabled", "anti_nuke_enabled"]
            hit  = sum(1 for k in keys if s.get(k))
            sc   = int(hit / len(keys) * 100)
            missing = [k.replace("_id", "").replace("_enabled", "").replace("_", "-")
                       for k in keys if not s.get(k)]
            return sc, (missing[0] if missing else "")
        except Exception:
            return 0, "no settings"

    async def make_embed(self) -> discord.Embed:
        guilds = self._sorted()
        total  = len(guilds)
        pages  = max(1, (total - 1) // 8 + 1)
        chunk  = guilds[self.page * 8:(self.page + 1) * 8]
        now    = discord.utils.utcnow()
        tu     = sum(g.member_count for g in guilds)

        e = _de(
            f"Servers  ·  {total:,} total  ·  {tu:,} users",
            f"Page **{self.page+1}/{pages}**  ·  Sorted by **{self.sort}**",
            color=D_DARK
        )

        for g in chunk:
            sc, fix     = await self._cfg_score(g)
            dot         = _health_dot(sc)
            bar         = _bar(sc, 8)
            days_ago    = (now - g.me.joined_at).days if g.me.joined_at else "?"
            boost_tier  = f"T{g.premium_tier}" if g.premium_tier else "—"
            channels    = len(g.channels)
            fix_str     = f"  ·  needs **{fix}**" if fix else ""

            e.add_field(
                name=f"{dot}  {g.name}",
                value=(
                    f"`{bar}` **{sc}%** configured{fix_str}\n"
                    f"`{g.id}`  ·  **{g.member_count:,}** members  ·  **{channels}** ch  ·  Boost {boost_tier}\n"
                    f"Joined **{days_ago}d** ago"
                ),
                inline=False
            )

        self._upd()
        return e

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary, row=0)
    async def prev_btn(self, i, _b):
        self.page -= 1; self._upd()
        await i.response.edit_message(embed=await self.make_embed(), view=self)

    @discord.ui.button(label="Sort: Members", style=discord.ButtonStyle.secondary, row=0)
    async def sort_btn(self, i, _b):
        cycle = ["members", "name", "newest", "oldest"]
        self.sort = cycle[(cycle.index(self.sort) + 1) % len(cycle)] if self.sort in cycle else "members"
        self.sort_btn.label = f"Sort: {self.sort.title()}"
        self.page = 0; self._upd()
        await i.response.edit_message(embed=await self.make_embed(), view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary, row=0)
    async def next_btn(self, i, _b):
        self.page += 1; self._upd()
        await i.response.edit_message(embed=await self.make_embed(), view=self)

    @discord.ui.button(label="🔍 Server Details", style=discord.ButtonStyle.primary, row=1)
    async def details(self, i, _b): await i.response.send_modal(_ServerDetailsModal(self.bot))

    @discord.ui.button(label="🚪 Leave Server",   style=discord.ButtonStyle.danger, row=1)
    async def leave(self, i, _b):   await i.response.send_modal(_LeaveServerModal(self.bot))


# ══════════════════════════════════════════════════════════════════════════════
# PANEL 3 — BLACKLIST
# ══════════════════════════════════════════════════════════════════════════════
class _BlacklistPanel(_Panel):
    async def make_embed(self) -> discord.Embed:
        async with aiosqlite.connect(self.bot.db.db_path) as db:
            try:
                async with db.execute(
                    "SELECT user_id, reason, blacklisted_at FROM blacklisted_users"
                    " ORDER BY blacklisted_at DESC LIMIT 15"
                ) as cu:
                    users = await cu.fetchall()
                async with db.execute("SELECT COUNT(*) FROM blacklisted_users") as cu:
                    u_total = (await cu.fetchone())[0]
            except Exception:
                users = []; u_total = 0
            try:
                async with db.execute(
                    "SELECT guild_id, reason, blacklisted_at FROM blacklisted_guilds"
                    " ORDER BY blacklisted_at DESC LIMIT 10"
                ) as cu:
                    guilds = await cu.fetchall()
                async with db.execute("SELECT COUNT(*) FROM blacklisted_guilds") as cu:
                    g_total = (await cu.fetchone())[0]
            except Exception:
                guilds = []; g_total = 0

        e = _de(
            f"Blacklist  ·  {u_total} users  ·  {g_total} servers",
            "Global ban registry — enforced across all XERO servers",
            color=D_DARK
        )

        if users:
            lines = []
            for uid, reason, ts in users:
                ts_str = (ts or "")[:10]
                r_str  = (reason or "no reason")[:50]
                lines.append(f"`{ts_str}` · <@{uid}> — {r_str}")
            e.add_field(
                name=f"🚫 Blacklisted Users ({u_total})" + ("  ·  showing 15" if u_total > 15 else ""),
                value="\n".join(lines)[:1000],
                inline=False
            )
        else:
            e.add_field(name="🚫 Users", value="None blacklisted.", inline=False)

        if guilds:
            lines = []
            for gid, reason, ts in guilds:
                g_obj  = self.bot.get_guild(gid)
                g_name = g_obj.name if g_obj else str(gid)
                ts_str = (ts or "")[:10]
                r_str  = (reason or "no reason")[:50]
                lines.append(f"`{ts_str}` · **{g_name}** — {r_str}")
            e.add_field(
                name=f"🏴 Blacklisted Servers ({g_total})",
                value="\n".join(lines)[:700],
                inline=False
            )
        else:
            e.add_field(name="🏴 Servers", value="None blacklisted.", inline=False)

        return e

    @discord.ui.button(label="⟳ Refresh",        style=discord.ButtonStyle.secondary, row=0)
    async def refresh(self, i, _b): await i.response.edit_message(embed=await self.make_embed(), view=self)

    @discord.ui.button(label="+ Blacklist User",  style=discord.ButtonStyle.danger,    row=0)
    async def add_user(self, i, _b): await i.response.send_modal(_BLUserModal(self.bot))

    @discord.ui.button(label="+ Blacklist Server",style=discord.ButtonStyle.danger,    row=0)
    async def add_guild(self, i, _b): await i.response.send_modal(_BLGuildModal(self.bot))

    @discord.ui.button(label="✓ Remove from BL",  style=discord.ButtonStyle.success,   row=1)
    async def remove(self, i, _b): await i.response.send_modal(_UnBLModal(self.bot))


# ══════════════════════════════════════════════════════════════════════════════
# PANEL 4 — ANALYTICS
# ══════════════════════════════════════════════════════════════════════════════
class _AnalyticsPanel(_Panel):
    async def make_embed(self) -> discord.Embed:
        bot    = self.bot
        now    = discord.utils.utcnow()
        guilds = bot.guilds

        new_7d  = sum(1 for g in guilds if g.me.joined_at and (now - g.me.joined_at).days < 7)
        new_30d = sum(1 for g in guilds if g.me.joined_at and (now - g.me.joined_at).days < 30)
        tu      = sum(g.member_count for g in guilds)
        avg_sz  = tu // max(len(guilds), 1)
        largest = max(guilds, key=lambda g: g.member_count) if guilds else None
        smallest= min(guilds, key=lambda g: g.member_count) if guilds else None
        top5_m  = sorted(guilds, key=lambda g: g.member_count, reverse=True)[:5]

        (tc, tm, active_u, total_xp, total_wealth,
         avg_rating, open_inc, total_staff, bl_u) = await _qdb(bot,
            ("SELECT SUM(commands_used) FROM user_stats", []),
            ("SELECT SUM(messages_sent) FROM user_stats", []),
            ("SELECT COUNT(DISTINCT user_id) FROM user_stats WHERE commands_used>0", []),
            ("SELECT SUM(total_xp) FROM levels", []),
            ("SELECT SUM(wallet+bank) FROM economy", []),
            ("SELECT AVG(rating) FROM tickets WHERE rating IS NOT NULL", []),
            ("SELECT COUNT(*) FROM bot_incidents WHERE resolved=0", []),
            ("SELECT COUNT(*) FROM bot_staff", []),
            ("SELECT COUNT(*) FROM blacklisted_users", []),
        )

        async with aiosqlite.connect(bot.db.db_path) as db:
            async with db.execute(
                "SELECT guild_id, SUM(commands_used) as cu FROM user_stats"
                " GROUP BY guild_id ORDER BY cu DESC LIMIT 5"
            ) as cu:
                top_cmd_srv = await cu.fetchall()
            async with db.execute(
                "SELECT guild_id, COUNT(*) as ct FROM tickets"
                " GROUP BY guild_id ORDER BY ct DESC LIMIT 5"
            ) as cu:
                top_tix_srv = await cu.fetchall()

        e = _de("Analytics  ·  Network Overview", color=D_BLUE)

        # Growth block
        e.add_field(
            name="📈 Growth",
            value=(
                f"**+{new_7d}** servers this week\n"
                f"**+{new_30d}** servers this month\n"
                f"**{len(guilds):,}** total  ·  avg **{avg_sz:,}** members"
            ),
            inline=True
        )

        # Activity block
        e.add_field(
            name="⚡ Activity",
            value=(
                f"**{(tc or 0):,}** commands run\n"
                f"**{(tm or 0):,}** messages tracked\n"
                f"**{(active_u or 0):,}** active users"
            ),
            inline=True
        )

        # Economy block
        e.add_field(
            name="💰 Economy",
            value=(
                f"**{(total_xp or 0):,}** total XP\n"
                f"**${(total_wealth or 0):,}** in circulation\n"
                f"Avg ticket rating: **{float(avg_rating or 0):.1f}/5** ⭐"
            ),
            inline=True
        )

        # Network extremes
        if largest and smallest:
            e.add_field(
                name="🌐 Network",
                value=(
                    f"Largest: **{largest.name}** ({largest.member_count:,})\n"
                    f"Smallest: **{smallest.name}** ({smallest.member_count:,})\n"
                    f"Staff: **{total_staff}**  ·  BL users: **{bl_u}**"
                ),
                inline=False
            )

        # Top servers by members
        if top5_m:
            e.add_field(
                name="🏆 Top 5 by Members",
                value="\n".join(f"`{i+1}.` **{g.name}** — {g.member_count:,}" for i, g in enumerate(top5_m)),
                inline=True
            )

        # Top by commands
        if top_cmd_srv:
            lines = []
            for gid, cu in top_cmd_srv:
                go = bot.get_guild(gid)
                lines.append(f"**{go.name if go else gid}** — {cu:,} cmds")
            e.add_field(name="⚡ Top 5 by Commands", value="\n".join(lines), inline=True)

        # Top by tickets
        if top_tix_srv:
            lines = []
            for gid, ct in top_tix_srv:
                go = bot.get_guild(gid)
                lines.append(f"**{go.name if go else gid}** — {ct} tickets")
            e.add_field(name="🎫 Top 5 by Tickets", value="\n".join(lines), inline=True)

        if open_inc:
            e.add_field(name="⚠️ Alert", value=f"**{open_inc}** unresolved incident(s) — check Incidents panel", inline=False)

        return e

    @discord.ui.button(label="⟳ Refresh", style=discord.ButtonStyle.secondary, row=0)
    async def refresh(self, i, _b): await i.response.edit_message(embed=await self.make_embed(), view=self)


# ══════════════════════════════════════════════════════════════════════════════
# PANEL 5 — TOOLS
# ══════════════════════════════════════════════════════════════════════════════
class _ToolsPanel(_Panel):
    _last_reload: str = "never"

    async def make_embed(self) -> discord.Embed:
        bot   = self.bot
        maint = getattr(bot, "maintenance_mode", False)
        cogs  = list(bot.extensions.keys())
        lat   = round(bot.latency * 1000)

        e = _de("Tools  ·  Bot Control Centre", color=D_STEEL)

        # Status row
        e.add_field(
            name="🔧 Status",
            value=(
                f"Maintenance: {'🔴 **ACTIVE**' if maint else '🟢 Off'}\n"
                f"Latency: `{lat}ms`  ·  Cogs: `{len(cogs)}`\n"
                f"Last reload: `{_ToolsPanel._last_reload}`"
            ),
            inline=False
        )

        # Cog grid — 3 per line for readability
        cog_names = [c.replace("cogs.", "") for c in cogs]
        rows      = [cog_names[i:i+3] for i in range(0, len(cog_names), 3)]
        grid      = "\n".join("  ".join(f"`{c}`" for c in row) for row in rows)
        e.add_field(name=f"📦 Loaded Cogs ({len(cogs)})", value=grid[:800] or "none", inline=False)

        e.add_field(
            name="⚡ Actions",
            value=(
                "**Reload All** — hot-reload every cog  ·  **Reload Cog** — one specific cog\n"
                "**Sync** — push slash commands to Discord\n"
                "**Toggle Maintenance** — DND mode for all commands\n"
                "**Clear Caches** — flush AI profile + logging caches"
            ),
            inline=False
        )
        return e

    @discord.ui.button(label="⟳ Reload All",     style=discord.ButtonStyle.primary,   row=0)
    async def reload_all(self, i: discord.Interaction, _b):
        await i.response.defer(ephemeral=True)
        ok = []; fail = []
        for ext in list(self.bot.extensions.keys()):
            if "core_admin" in ext: continue
            try:
                await self.bot.reload_extension(ext)
                ok.append(ext.replace("cogs.", ""))
            except Exception as ex:
                fail.append(f"{ext.replace('cogs.','')}:{str(ex)[:30]}")
        _ToolsPanel._last_reload = datetime.datetime.utcnow().strftime("%H:%M:%S UTC")
        result = f"✅ **{len(ok)}** reloaded"
        if fail: result += f"\n❌ **{len(fail)}** failed: {', '.join(fail[:4])}"
        await i.followup.send(result, ephemeral=True)

    @discord.ui.button(label="↻ Reload Cog",      style=discord.ButtonStyle.secondary, row=0)
    async def reload_cog(self, i, _b): await i.response.send_modal(_ReloadCogModal(self.bot))

    @discord.ui.button(label="↑ Sync Commands",   style=discord.ButtonStyle.primary,   row=0)
    @command_guard
    async def sync(self, i: discord.Interaction, _b):
        await i.response.defer(ephemeral=True)
        synced  = await self.bot.tree.sync()
        mg      = discord.Object(id=self.bot.MANAGEMENT_GUILD_ID)
        gsynced = await self.bot.tree.sync(guild=mg)
        await i.followup.send(
            f"✅ **{len(synced)}** global commands  ·  **{len(gsynced)}** management guild",
            ephemeral=True
        )

    @discord.ui.button(label="⚠ Toggle Maint",    style=discord.ButtonStyle.danger,    row=1)
    async def toggle_maint(self, i, _b):
        cur = getattr(self.bot, "maintenance_mode", False)
        self.bot.maintenance_mode = not cur
        if not cur:
            await self.bot.change_presence(
                status=discord.Status.dnd,
                activity=discord.Activity(type=discord.ActivityType.watching, name="🔧 Maintenance")
            )
        else:
            await self.bot.change_presence(
                status=discord.Status.online,
                activity=discord.Activity(type=discord.ActivityType.watching, name="/help")
            )
        await i.response.edit_message(embed=await self.make_embed(), view=self)

    @discord.ui.button(label="🗑 Clear Caches",    style=discord.ButtonStyle.secondary, row=1)
    async def clear_cache(self, i, _b):
        cleared = []
        try:
            from cogs.member_intelligence import PROFILE_CACHE, CHANNEL_CONTEXT, LAST_REPLY
            PROFILE_CACHE.clear(); CHANNEL_CONTEXT.clear(); LAST_REPLY.clear()
            cleared.append("AI profiles")
        except Exception: pass
        try:
            cog = self.bot.cogs.get("LoggingSystem")
            if cog and hasattr(cog, "_cache"): cog._cache.clear(); cleared.append("logging")
        except Exception: pass
        msg = "✅ Cleared: " + (", ".join(cleared) if cleared else "nothing to clear")
        await i.response.send_message(msg, ephemeral=True)

    @discord.ui.button(label="⟳ Refresh",          style=discord.ButtonStyle.secondary, row=2)
    async def refresh(self, i, _b): await i.response.edit_message(embed=await self.make_embed(), view=self)


# ══════════════════════════════════════════════════════════════════════════════
# PANEL 6 — STAFF
# ══════════════════════════════════════════════════════════════════════════════
class _StaffPanel(_Panel):
    async def make_embed(self, guild: discord.Guild) -> discord.Embed:
        await _ensure_mgmt_tables(self.bot.db.db_path)
        async with aiosqlite.connect(self.bot.db.db_path) as db:
            async with db.execute(
                "SELECT user_id, role, added_by, added_at, notes FROM bot_staff ORDER BY added_at ASC"
            ) as cu:
                rows = await cu.fetchall()

        e = _de(
            f"Staff  ·  Team Flame  ·  {len(rows)} member(s)",
            "Bot team roster — roles, status, joined date",
            color=D_BLACK
        )

        if not rows:
            e.description = (
                "No staff registered yet.\n\n"
                "Use **+ Add Staff** to register team members.\n"
                "Roles: `owner` `lead` `developer` `support` `moderator`"
            )
            return e

        role_order = ["owner", "lead", "developer", "moderator", "support"]
        sorted_rows = sorted(rows, key=lambda r: role_order.index(r[1]) if r[1] in role_order else 99)

        for uid, role, added_by, added_at, notes in sorted_rows:
            member  = guild.get_member(uid)
            name    = member.display_name if member else f"Unknown ({uid})"
            icon    = ROLE_ICONS.get(role or "support", "•")
            # Discord status dot
            dot     = STATUS_DOT.get(member.status, "⚫") if member else "⚫"
            # Who added them
            adder   = guild.get_member(added_by)
            add_str = f"  ·  added by {adder.display_name}" if adder else ""
            # Activity
            activity = ""
            if member and member.activity:
                act = member.activity
                if hasattr(act, "name") and act.name:
                    activity = f"\n*{act.name[:40]}*"
            note_str = f"\n> {notes[:60]}" if notes else ""

            e.add_field(
                name=f"{icon}  {dot} {name}",
                value=(
                    f"**{(role or 'support').title()}**"
                    f"  ·  since `{(added_at or '')[:10]}`"
                    f"{add_str}"
                    f"{activity}"
                    f"{note_str}"
                ),
                inline=False
            )

        return e

    @discord.ui.button(label="+ Add Staff",    style=discord.ButtonStyle.primary,   row=0)
    async def add(self, i, _b):    await i.response.send_modal(_AddStaffModal(self.bot))

    @discord.ui.button(label="✏ Edit Role",   style=discord.ButtonStyle.secondary, row=0)
    async def edit(self, i, _b):   await i.response.send_modal(_EditStaffModal(self.bot))

    @discord.ui.button(label="✕ Remove",      style=discord.ButtonStyle.danger,    row=0)
    async def remove(self, i, _b): await i.response.send_modal(_RemoveStaffModal(self.bot))

    @discord.ui.button(label="⟳ Refresh",     style=discord.ButtonStyle.secondary, row=1)
    async def refresh(self, i, _b): await i.response.edit_message(embed=await self.make_embed(i.guild), view=self)


# ══════════════════════════════════════════════════════════════════════════════
# PANEL 7 — INCIDENTS
# ══════════════════════════════════════════════════════════════════════════════
class _IncidentsPanel(_Panel):
    def __init__(self, bot, show_all: bool = False):
        super().__init__(bot)
        self.show_all = show_all

    async def make_embed(self, guild: discord.Guild) -> discord.Embed:
        await _ensure_mgmt_tables(self.bot.db.db_path)
        async with aiosqlite.connect(self.bot.db.db_path) as db:
            filt = "" if self.show_all else " WHERE resolved=0"
            async with db.execute(
                "SELECT id, title, description, severity, resolved,"
                " created_at, resolved_at, reported_by"
                f" FROM bot_incidents{filt} ORDER BY id DESC LIMIT 15"
            ) as cu:
                rows = await cu.fetchall()
            async with db.execute("SELECT COUNT(*) FROM bot_incidents WHERE resolved=0") as cu:
                open_c = (await cu.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM bot_incidents WHERE resolved=1") as cu:
                res_c  = (await cu.fetchone())[0]
            # Severity breakdown of open incidents
            async with db.execute(
                "SELECT severity, COUNT(*) FROM bot_incidents"
                " WHERE resolved=0 GROUP BY severity"
            ) as cu:
                sev_counts = dict(await cu.fetchall())

        mode = "All Incidents" if self.show_all else "Open Incidents"

        # Build severity summary
        sev_parts = []
        for sev in ["critical", "high", "medium", "low"]:
            n = sev_counts.get(sev, 0)
            if n: sev_parts.append(f"{SEV[sev]} {n} {sev}")
        sev_line = "  ·  ".join(sev_parts) if sev_parts else "none"

        e = _de(
            f"Incidents  ·  {mode}",
            f"Open: **{open_c}**  ·  Resolved: **{res_c}**  ·  {sev_line}",
            color=D_DARK if not open_c else D_AMBER
        )

        if not rows:
            e.description += "\n\n🟢 All systems operational. No incidents on record."
            return e

        for iid, title, desc, sev, resolved, created, resolved_at, reporter in rows:
            dot  = SEV.get(sev, "⚪")
            tick = "✅" if resolved else "⏳"

            # Calculate age
            try:
                dt  = datetime.datetime.fromisoformat((created or "").replace("Z", ""))
                now = datetime.datetime.utcnow()
                age_d = (now - dt).days
                age_h = int((now - dt).seconds / 3600)
                age_s = f"{age_d}d" if age_d else f"{age_h}h"
            except Exception:
                age_s = "?"

            # Duration if resolved
            dur_s = ""
            if resolved and resolved_at and created:
                try:
                    t1  = datetime.datetime.fromisoformat(created.replace("Z", ""))
                    t2  = datetime.datetime.fromisoformat(resolved_at.replace("Z", ""))
                    dur = int((t2 - t1).total_seconds() / 60)
                    dur_s = f"  ·  resolved in **{dur}m**" if dur < 60 else f"  ·  resolved in **{dur//60}h {dur%60}m**"
                except Exception:
                    pass

            reporter_m = guild.get_member(reporter) if reporter else None
            rep_s = f"  ·  by {reporter_m.display_name}" if reporter_m else ""

            e.add_field(
                name=f"{tick} {dot}  #{iid}  {title}",
                value=(
                    f"`{(sev or '?').upper()}`  ·  {age_s} ago{rep_s}{dur_s}\n"
                    f"{(desc or 'No description')[:100]}"
                ),
                inline=False
            )

        return e

    @discord.ui.button(label="+ Log Incident",   style=discord.ButtonStyle.danger,    row=0)
    async def log(self, i, _b):     await i.response.send_modal(_LogIncidentModal(self.bot))

    @discord.ui.button(label="✓ Resolve",        style=discord.ButtonStyle.success,   row=0)
    async def resolve(self, i, _b): await i.response.send_modal(_ResolveModal(self.bot))

    @discord.ui.button(label="📋 Toggle History",style=discord.ButtonStyle.secondary, row=1)
    async def toggle(self, i, _b):
        self.show_all = not self.show_all
        await i.response.edit_message(embed=await self.make_embed(i.guild), view=self)

    @discord.ui.button(label="⟳ Refresh",        style=discord.ButtonStyle.secondary, row=1)
    async def refresh(self, i, _b): await i.response.edit_message(embed=await self.make_embed(i.guild), view=self)


# ══════════════════════════════════════════════════════════════════════════════
# PANEL 8 — SERVER HEALTH
# ══════════════════════════════════════════════════════════════════════════════
class _HealthPanel(_Panel):
    CONFIG_CHECKS = [
        ("welcome_channel_id",     15, "welcome channel"),
        ("log_channel_id",         15, "log channel"),
        ("autorole_id",            10, "auto-role"),
        ("verify_channel_id",      10, "verification"),
        ("ticket_support_role_id", 10, "ticket role"),
        ("level_up_channel_id",     5, "level-up channel"),
        ("birthday_channel_id",     5, "birthday channel"),
        ("leveling_enabled",        5, "leveling on"),
        ("ai_enabled",              5, "AI on"),
        ("automod_enabled",        10, "automod"),
        ("anti_nuke_enabled",      10, "anti-nuke"),
    ]
    # total possible = 100

    def __init__(self, bot, page=0, sort="worst"):
        super().__init__(bot)
        self.page = page
        self.sort = sort

    async def _score_guild(self, g: discord.Guild) -> tuple[int, list[str]]:
        try:
            s = await self.bot.db.get_guild_settings(g.id) or {}
        except Exception:
            return 0, ["no config"]
        score   = 0
        missing = []
        for key, pts, label in self.CONFIG_CHECKS:
            val = s.get(key)
            if val and val not in (0, False):
                score += pts
            else:
                missing.append(label)
        return min(score, 100), missing

    async def make_embed(self) -> discord.Embed:
        guilds = list(self.bot.guilds)
        scored = []
        for g in guilds[:60]:
            sc, missing = await self._score_guild(g)
            scored.append((g, sc, missing))

        if self.sort == "worst":   scored.sort(key=lambda x: x[1])
        elif self.sort == "best":  scored.sort(key=lambda x: x[1], reverse=True)
        elif self.sort == "biggest": scored.sort(key=lambda x: x[0].member_count, reverse=True)
        elif self.sort == "newest":  scored.sort(key=lambda x: x[0].me.joined_at or discord.utils.utcnow(), reverse=True)

        total  = len(scored)
        pages  = max(1, (total - 1) // 8 + 1)
        chunk  = scored[self.page * 8:(self.page + 1) * 8]

        healthy   = sum(1 for _, sc, _ in scored if sc >= 70)
        partial   = sum(1 for _, sc, _ in scored if 40 <= sc < 70)
        broken    = sum(1 for _, sc, _ in scored if sc < 40)
        avg_score = sum(sc for _, sc, _ in scored) // max(total, 1)

        e = _de(
            "Server Health  ·  Config Audit",
            (
                f"🟢 **{healthy}** healthy  ·  🟡 **{partial}** partial  ·  🔴 **{broken}** unconfigured\n"
                f"Network avg: **{avg_score}%**  ·  Page **{self.page+1}/{pages}**  ·  sorted by **{self.sort}**"
            ),
            color=D_DARK
        )

        for g, sc, missing in chunk:
            dot   = _health_dot(sc)
            bar   = _bar(sc, 10)
            # Top 2 missing items
            fix   = ", ".join(missing[:2]) if missing else "fully configured"
            days  = (discord.utils.utcnow() - g.me.joined_at).days if g.me.joined_at else "?"
            e.add_field(
                name=f"{dot}  {g.name}",
                value=(
                    f"`{bar}` **{sc}/100**\n"
                    f"**{g.member_count:,}** members  ·  joined **{days}d** ago\n"
                    f"{'⚠️ Fix: ' + fix if missing else '✅ All configured'}"
                ),
                inline=False
            )

        self.prev_btn.disabled = self.page <= 0
        self.next_btn.disabled = self.page >= pages - 1
        return e

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary, row=0)
    async def prev_btn(self, i, _b):
        self.page -= 1
        await i.response.edit_message(embed=await self.make_embed(), view=self)

    @discord.ui.button(label="Sort: Worst", style=discord.ButtonStyle.secondary, row=0)
    async def sort_btn(self, i, _b):
        cycle = ["worst", "best", "biggest", "newest"]
        self.sort = cycle[(cycle.index(self.sort) + 1) % len(cycle)] if self.sort in cycle else "worst"
        self.sort_btn.label = f"Sort: {self.sort.title()}"
        self.page = 0
        await i.response.edit_message(embed=await self.make_embed(), view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary, row=0)
    async def next_btn(self, i, _b):
        self.page += 1
        await i.response.edit_message(embed=await self.make_embed(), view=self)

    @discord.ui.button(label="⟳ Refresh", style=discord.ButtonStyle.secondary, row=1)
    async def refresh(self, i, _b): await i.response.edit_message(embed=await self.make_embed(), view=self)


# ══════════════════════════════════════════════════════════════════════════════
# MODALS
# ══════════════════════════════════════════════════════════════════════════════

class _BLUserModal(discord.ui.Modal, title="Blacklist User"):
    uid    = discord.ui.TextInput(label="User ID")
    reason = discord.ui.TextInput(label="Reason", placeholder="Why is this user being blacklisted?")
    def __init__(self, bot): super().__init__(); self.bot = bot
    async def on_submit(self, i):
        try:
            u = int(self.uid.value.strip())
            async with aiosqlite.connect(self.bot.db.db_path) as db:
                await db.execute(
                    "CREATE TABLE IF NOT EXISTS blacklisted_users"
                    " (user_id INTEGER PRIMARY KEY, reason TEXT, blacklisted_at TEXT)"
                )
                await db.execute(
                    "INSERT OR REPLACE INTO blacklisted_users VALUES (?,?,?)",
                    (u, self.reason.value, discord.utils.utcnow().isoformat())
                )
                await db.commit()
            await i.response.send_message(f"✅ User `{u}` blacklisted globally.", ephemeral=True)
        except Exception as ex:
            await i.response.send_message(f"❌ {ex}", ephemeral=True)


class _BLGuildModal(discord.ui.Modal, title="Blacklist Server"):
    gid    = discord.ui.TextInput(label="Server ID")
    reason = discord.ui.TextInput(label="Reason")
    def __init__(self, bot): super().__init__(); self.bot = bot
    async def on_submit(self, i):
        try:
            gv = int(self.gid.value.strip())
            async with aiosqlite.connect(self.bot.db.db_path) as db:
                await db.execute(
                    "CREATE TABLE IF NOT EXISTS blacklisted_guilds"
                    " (guild_id INTEGER PRIMARY KEY, reason TEXT, blacklisted_at TEXT)"
                )
                await db.execute(
                    "INSERT OR REPLACE INTO blacklisted_guilds VALUES (?,?,?)",
                    (gv, self.reason.value, discord.utils.utcnow().isoformat())
                )
                await db.commit()
            g = self.bot.get_guild(gv)
            if g:
                try: await g.leave()
                except Exception: pass
            await i.response.send_message(f"✅ Server `{gv}` blacklisted and left.", ephemeral=True)
        except Exception as ex:
            await i.response.send_message(f"❌ {ex}", ephemeral=True)


class _UnBLModal(discord.ui.Modal, title="Remove from Blacklist"):
    eid = discord.ui.TextInput(label="User ID or Server ID to remove")
    def __init__(self, bot): super().__init__(); self.bot = bot
    async def on_submit(self, i):
        try:
            v = int(self.eid.value.strip())
            async with aiosqlite.connect(self.bot.db.db_path) as db:
                await db.execute("DELETE FROM blacklisted_users WHERE user_id=?", (v,))
                await db.execute("DELETE FROM blacklisted_guilds WHERE guild_id=?", (v,))
                await db.commit()
            await i.response.send_message(f"✅ `{v}` removed from all blacklists.", ephemeral=True)
        except Exception as ex:
            await i.response.send_message(f"❌ {ex}", ephemeral=True)


class _AddStaffModal(discord.ui.Modal, title="Add Staff Member"):
    uid   = discord.ui.TextInput(label="User ID")
    role  = discord.ui.TextInput(label="Role: owner / lead / developer / support / moderator", placeholder="support")
    notes = discord.ui.TextInput(label="Notes / responsibilities", required=False)
    def __init__(self, bot): super().__init__(); self.bot = bot
    async def on_submit(self, i):
        try:
            u = int(self.uid.value.strip())
            r = self.role.value.strip().lower() or "support"
            if r not in ("owner", "lead", "developer", "support", "moderator"): r = "support"
            await _ensure_mgmt_tables(self.bot.db.db_path)
            async with aiosqlite.connect(self.bot.db.db_path) as db:
                await db.execute(
                    "INSERT OR REPLACE INTO bot_staff (user_id, role, added_by, notes) VALUES (?,?,?,?)",
                    (u, r, i.user.id, self.notes.value or None)
                )
                await db.commit()
            await i.response.send_message(f"✅ <@{u}> added as **{r}**.", ephemeral=True)
        except Exception as ex:
            await i.response.send_message(f"❌ {ex}", ephemeral=True)


class _EditStaffModal(discord.ui.Modal, title="Edit Staff Member"):
    uid   = discord.ui.TextInput(label="User ID to edit")
    role  = discord.ui.TextInput(label="New role", required=False)
    notes = discord.ui.TextInput(label="New notes", required=False)
    def __init__(self, bot): super().__init__(); self.bot = bot
    async def on_submit(self, i):
        try:
            u = int(self.uid.value.strip())
            sets = []; params = []
            if self.role.value:  sets.append("role=?");  params.append(self.role.value.strip().lower())
            if self.notes.value: sets.append("notes=?"); params.append(self.notes.value)
            if not sets: return await i.response.send_message("Nothing to update.", ephemeral=True)
            params.append(u)
            async with aiosqlite.connect(self.bot.db.db_path) as db:
                await db.execute(f"UPDATE bot_staff SET {', '.join(sets)} WHERE user_id=?", params)
                await db.commit()
            await i.response.send_message(f"✅ <@{u}> updated.", ephemeral=True)
        except Exception as ex:
            await i.response.send_message(f"❌ {ex}", ephemeral=True)


class _RemoveStaffModal(discord.ui.Modal, title="Remove Staff Member"):
    uid = discord.ui.TextInput(label="User ID to remove")
    def __init__(self, bot): super().__init__(); self.bot = bot
    async def on_submit(self, i):
        try:
            u = int(self.uid.value.strip())
            async with aiosqlite.connect(self.bot.db.db_path) as db:
                await db.execute("DELETE FROM bot_staff WHERE user_id=?", (u,))
                await db.commit()
            await i.response.send_message(f"✅ <@{u}> removed from staff.", ephemeral=True)
        except Exception as ex:
            await i.response.send_message(f"❌ {ex}", ephemeral=True)


class _LogIncidentModal(discord.ui.Modal, title="Log Incident"):
    title_   = discord.ui.TextInput(label="Incident title")
    desc     = discord.ui.TextInput(label="Description", style=discord.TextStyle.paragraph, required=False)
    severity = discord.ui.TextInput(label="Severity: low / medium / high / critical", placeholder="medium")
    def __init__(self, bot): super().__init__(); self.bot = bot
    async def on_submit(self, i):
        await _ensure_mgmt_tables(self.bot.db.db_path)
        sev = self.severity.value.strip().lower()
        if sev not in ("low", "medium", "high", "critical"): sev = "medium"
        async with aiosqlite.connect(self.bot.db.db_path) as db:
            await db.execute(
                "INSERT INTO bot_incidents (title, description, severity, reported_by) VALUES (?,?,?,?)",
                (self.title_.value, self.desc.value or None, sev, i.user.id)
            )
            await db.commit()
        await i.response.send_message(
            f"{SEV[sev]} Incident logged: **{self.title_.value}** ({sev})", ephemeral=True
        )


class _ResolveModal(discord.ui.Modal, title="Resolve Incident"):
    iid = discord.ui.TextInput(label="Incident ID to mark resolved")
    def __init__(self, bot): super().__init__(); self.bot = bot
    async def on_submit(self, i):
        try:
            n = int(self.iid.value.strip())
            async with aiosqlite.connect(self.bot.db.db_path) as db:
                await db.execute(
                    "UPDATE bot_incidents SET resolved=1, resolved_at=datetime('now') WHERE id=?", (n,)
                )
                await db.commit()
            await i.response.send_message(f"✅ Incident #{n} marked resolved.", ephemeral=True)
        except Exception as ex:
            await i.response.send_message(f"❌ {ex}", ephemeral=True)


class _ReloadCogModal(discord.ui.Modal, title="Reload Specific Cog"):
    cog = discord.ui.TextInput(label="Cog name (e.g. economy, tickets, levels)", placeholder="economy")
    def __init__(self, bot): super().__init__(); self.bot = bot
    async def on_submit(self, i):
        ext = "cogs." + self.cog.value.strip().lower().replace("cogs.", "")
        try:
            await self.bot.reload_extension(ext)
            _ToolsPanel._last_reload = datetime.datetime.utcnow().strftime("%H:%M:%S UTC")
            await i.response.send_message(f"✅ `{ext}` reloaded.", ephemeral=True)
        except commands.ExtensionNotLoaded:
            try:
                await self.bot.load_extension(ext)
                await i.response.send_message(f"✅ `{ext}` loaded fresh.", ephemeral=True)
            except Exception as ex:
                await i.response.send_message(f"❌ {ex}", ephemeral=True)
        except Exception as ex:
            await i.response.send_message(f"❌ {str(ex)[:300]}", ephemeral=True)


class _ServerDetailsModal(discord.ui.Modal, title="Server Deep Dive"):
    gid = discord.ui.TextInput(label="Server ID")
    def __init__(self, bot): super().__init__(); self.bot = bot
    async def on_submit(self, i: discord.Interaction):
        await i.response.defer(ephemeral=True)
        try: g = self.bot.get_guild(int(self.gid.value.strip()))
        except ValueError: g = None
        if not g:
            return await i.followup.send("❌ XERO is not in that server.", ephemeral=True)

        s = await self.bot.db.get_guild_settings(g.id) or {}
        lv, mc, tk, warns, cmds, msgs, eco, gw = await _qdb(self.bot,
            ("SELECT COUNT(*) FROM levels WHERE guild_id=?",                     [g.id]),
            ("SELECT COUNT(*) FROM mod_cases WHERE guild_id=?",                  [g.id]),
            ("SELECT COUNT(*) FROM tickets WHERE guild_id=?",                    [g.id]),
            ("SELECT COUNT(*) FROM warnings WHERE guild_id=?",                   [g.id]),
            ("SELECT SUM(commands_used) FROM user_stats WHERE guild_id=?",       [g.id]),
            ("SELECT SUM(messages_sent) FROM user_stats WHERE guild_id=?",       [g.id]),
            ("SELECT COUNT(*) FROM economy WHERE guild_id=? AND wallet+bank>0",  [g.id]),
            ("SELECT COUNT(*) FROM giveaways WHERE guild_id=?",                  [g.id]),
        )

        def t(v): return "✅" if v else "❌"
        mp = g.me.guild_permissions

        e = discord.Embed(title=g.name, color=discord.Color(D_BLUE), timestamp=discord.utils.utcnow())
        if g.icon: e.set_thumbnail(url=g.icon.url)

        e.add_field(name="Identity", value=(
            f"ID: `{g.id}`\n"
            f"Owner: <@{g.owner_id}>\n"
            f"Created: <t:{int(g.created_at.timestamp())}:D>"
        ), inline=True)

        e.add_field(name="Size", value=(
            f"Members: **{g.member_count:,}**\n"
            f"Channels: **{len(g.channels)}**\n"
            f"Roles: **{len(g.roles)}**"
        ), inline=True)

        e.add_field(name="Boost", value=(
            f"Tier: **{g.premium_tier}**\n"
            f"Boosts: **{g.premium_subscription_count}**\n"
            f"Verification: **{g.verification_level}**"
        ), inline=True)

        e.add_field(name="XERO Config", value=(
            f"Welcome: {t(s.get('welcome_channel_id'))}  "
            f"Logs: {t(s.get('log_channel_id'))}  "
            f"AutoRole: {t(s.get('autorole_id'))}\n"
            f"Verify: {t(s.get('verify_channel_id'))}  "
            f"Tickets: {t(s.get('ticket_support_role_id'))}  "
            f"AutoMod: {t(s.get('automod_enabled'))}\n"
            f"AntiNuke: {t(s.get('anti_nuke_enabled'))}  "
            f"Leveling: {t(s.get('leveling_enabled', 1))}  "
            f"AI: {t(s.get('ai_enabled', 1))}"
        ), inline=False)

        e.add_field(name="Bot Permissions", value=(
            f"Admin: {t(mp.administrator)}  "
            f"Ban: {t(mp.ban_members)}  "
            f"Kick: {t(mp.kick_members)}\n"
            f"ManageRoles: {t(mp.manage_roles)}  "
            f"ManageCh: {t(mp.manage_channels)}\n"
            f"AuditLog: {t(mp.view_audit_log)}  "
            f"ManageMsgs: {t(mp.manage_messages)}"
        ), inline=False)

        e.add_field(name="XERO Activity", value=(
            f"Ranked users: **{lv}**  ·  Economy users: **{eco}**\n"
            f"Cmds used: **{(cmds or 0):,}**  ·  Msgs: **{(msgs or 0):,}**\n"
            f"Cases: **{mc}**  ·  Warns: **{warns}**  ·  Tickets: **{tk}**  ·  GAs: **{gw}**"
        ), inline=False)

        e.set_footer(text=f"Guild ID: {g.id}  ·  XERO Core")
        await i.followup.send(embed=e, ephemeral=True)


class _LeaveServerModal(discord.ui.Modal, title="Force Leave Server"):
    gid    = discord.ui.TextInput(label="Server ID")
    reason = discord.ui.TextInput(label="Reason", required=False, placeholder="Optional reason for records")
    def __init__(self, bot): super().__init__(); self.bot = bot
    async def on_submit(self, i):
        try:
            g = self.bot.get_guild(int(self.gid.value.strip()))
            if not g: return await i.response.send_message("❌ XERO is not in that server.", ephemeral=True)
            name = g.name
            await g.leave()
            await i.response.send_message(f"✅ Left **{name}**.", ephemeral=True)
        except Exception as ex:
            await i.response.send_message(f"❌ {ex}", ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# MASTER MANAGEMENT DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════
class ManagementDashboard(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=600)
        self.bot = bot

    @staticmethod
    async def build(bot, guild: discord.Guild):
        view  = ManagementDashboard(bot)
        embed = await ManagementDashboard._home(bot, guild)
        return embed, view

    @staticmethod
    async def _home(bot, guild: discord.Guild) -> discord.Embed:
        try:
            import psutil
            ram = f"{psutil.Process().memory_info().rss/1024/1024:.0f}MB"
        except Exception:
            ram = "—"

        lat   = round(bot.latency * 1000)
        upt   = int(time.time() - bot.start_time) if hasattr(bot, "start_time") else 0
        h, r  = divmod(upt, 3600); m_ = r // 60
        maint = getattr(bot, "maintenance_mode", False)
        lat_i = "🟢" if lat < 100 else ("🟡" if lat < 250 else "🔴")

        open_inc = 0
        try:
            async with aiosqlite.connect(bot.db.db_path) as db:
                async with db.execute("SELECT COUNT(*) FROM bot_incidents WHERE resolved=0") as cu:
                    open_inc = (await cu.fetchone())[0]
        except Exception: pass

        status_line = (
            f"{'🔴 **MAINTENANCE**' if maint else lat_i + ' **Operational**'}"
            f"  ·  `{lat}ms`  ·  `{h}h {m_}m` uptime"
        )

        e = discord.Embed(
            title="XERO  ·  Management Dashboard",
            description=f"{status_line}\n\nSelect a panel below.",
            color=discord.Color(D_BLUE),
            timestamp=discord.utils.utcnow()
        )
        e.set_thumbnail(url=bot.user.display_avatar.url)

        tu = sum(g.member_count for g in bot.guilds)
        e.add_field(name="Servers",  value=f"**{len(bot.guilds):,}**",  inline=True)
        e.add_field(name="Users",    value=f"**{tu:,}**",               inline=True)
        e.add_field(name="RAM",      value=f"**{ram}**",                 inline=True)
        e.add_field(name="Cogs",     value=f"**{len(bot.extensions)}**", inline=True)
        e.add_field(name="Latency",  value=f"**{lat}ms**",               inline=True)
        e.add_field(name="Uptime",   value=f"**{h}h {m_}m**",           inline=True)

        if open_inc:
            e.add_field(
                name="⚠️ Alert",
                value=f"**{open_inc}** unresolved incident(s) — open Incidents panel",
                inline=False
            )

        e.set_footer(text=f"XERO Management Dashboard  ·  {guild.name}  ·  Team Flame")
        return e

    # Row 0
    @discord.ui.button(label="📊 Stats",     style=discord.ButtonStyle.primary,   row=0)
    async def btn_stats(self, i, _b):
        p = _StatsPanel(self.bot)
        await i.response.edit_message(embed=await p.make_embed(), view=p)

    @discord.ui.button(label="🌐 Servers",   style=discord.ButtonStyle.secondary, row=0)
    async def btn_servers(self, i, _b):
        p = _ServersPanel(self.bot)
        await i.response.edit_message(embed=await p.make_embed(), view=p)

    @discord.ui.button(label="🚫 Blacklist", style=discord.ButtonStyle.secondary, row=0)
    async def btn_blacklist(self, i, _b):
        p = _BlacklistPanel(self.bot)
        await i.response.edit_message(embed=await p.make_embed(), view=p)

    @discord.ui.button(label="📈 Analytics", style=discord.ButtonStyle.secondary, row=0)
    async def btn_analytics(self, i, _b):
        p = _AnalyticsPanel(self.bot)
        await i.response.edit_message(embed=await p.make_embed(), view=p)

    # Row 1
    @discord.ui.button(label="⚙️ Tools",     style=discord.ButtonStyle.secondary, row=1)
    async def btn_tools(self, i, _b):
        p = _ToolsPanel(self.bot)
        await i.response.edit_message(embed=await p.make_embed(), view=p)

    @discord.ui.button(label="👥 Staff",     style=discord.ButtonStyle.secondary, row=1)
    async def btn_staff(self, i, _b):
        p = _StaffPanel(self.bot)
        await i.response.edit_message(embed=await p.make_embed(i.guild), view=p)

    @discord.ui.button(label="📋 Incidents", style=discord.ButtonStyle.secondary, row=1)
    async def btn_incidents(self, i, _b):
        p = _IncidentsPanel(self.bot)
        await i.response.edit_message(embed=await p.make_embed(i.guild), view=p)

    @discord.ui.button(label="🏥 Health",    style=discord.ButtonStyle.secondary, row=1)
    @command_guard
    async def btn_health(self, i: discord.Interaction, _b):
        await i.response.defer()
        p = _HealthPanel(self.bot)
        await i.edit_original_response(embed=await p.make_embed(), view=p)

    # Row 2
    @discord.ui.button(label="⟳ Refresh",   style=discord.ButtonStyle.secondary, row=2)
    async def btn_refresh(self, i, _b):
        await i.response.edit_message(embed=await self._home(self.bot, i.guild), view=self)


# ══════════════════════════════════════════════════════════════════════════════
# /core — 10 COMMANDS (dashboard + parameterised ops only)
# ══════════════════════════════════════════════════════════════════════════════
class CoreAdmin(commands.GroupCog, name="core"):
    def __init__(self, bot): self.bot = bot

    @app_commands.command(name="dashboard", description="Open the XERO Management Dashboard — 8-panel black/white/blue control centre.")
    @is_management()
    @command_guard
    async def dashboard(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await _ensure_mgmt_tables(self.bot.db.db_path)
        embed, view = await ManagementDashboard.build(self.bot, interaction.guild)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="guild-info", description="Full deep-dive into any server XERO is in — config, DB stats, permissions, boost tier.")
    @app_commands.describe(guild_id="Server ID")
    @is_management()
    async def guild_info(self, interaction: discord.Interaction, guild_id: str):
        await interaction.response.defer(ephemeral=True)
        try: g = self.bot.get_guild(int(guild_id))
        except ValueError: g = None
        if not g:
            return await interaction.followup.send(embed=error_embed("Not Found", "XERO is not in that server."), ephemeral=True)
        # Reuse the modal's detailed embed logic inline
        s = await self.bot.db.get_guild_settings(g.id) or {}
        lv, mc, tk, warns, cmds, msgs, eco, gw = await _qdb(self.bot,
            ("SELECT COUNT(*) FROM levels WHERE guild_id=?",                    [g.id]),
            ("SELECT COUNT(*) FROM mod_cases WHERE guild_id=?",                 [g.id]),
            ("SELECT COUNT(*) FROM tickets WHERE guild_id=?",                   [g.id]),
            ("SELECT COUNT(*) FROM warnings WHERE guild_id=?",                  [g.id]),
            ("SELECT SUM(commands_used) FROM user_stats WHERE guild_id=?",      [g.id]),
            ("SELECT SUM(messages_sent) FROM user_stats WHERE guild_id=?",      [g.id]),
            ("SELECT COUNT(*) FROM economy WHERE guild_id=? AND wallet+bank>0", [g.id]),
            ("SELECT COUNT(*) FROM giveaways WHERE guild_id=?",                 [g.id]),
        )
        def t(v): return "✅" if v else "❌"
        mp = g.me.guild_permissions
        e = discord.Embed(title=g.name, color=discord.Color(D_BLUE), timestamp=discord.utils.utcnow())
        if g.icon: e.set_thumbnail(url=g.icon.url)
        e.add_field(name="Identity", value=f"ID: `{g.id}`\nOwner: <@{g.owner_id}>\nCreated: <t:{int(g.created_at.timestamp())}:D>", inline=True)
        e.add_field(name="Size",     value=f"Members: **{g.member_count:,}**\nChannels: **{len(g.channels)}**\nRoles: **{len(g.roles)}**", inline=True)
        e.add_field(name="Boost",    value=f"Tier: **{g.premium_tier}**\nBoosts: **{g.premium_subscription_count}**\nVerification: **{g.verification_level}**", inline=True)
        e.add_field(name="Config", value=(
            f"Welcome: {t(s.get('welcome_channel_id'))}  Logs: {t(s.get('log_channel_id'))}  AutoRole: {t(s.get('autorole_id'))}\n"
            f"Verify: {t(s.get('verify_channel_id'))}  Tickets: {t(s.get('ticket_support_role_id'))}  AutoMod: {t(s.get('automod_enabled'))}\n"
            f"AntiNuke: {t(s.get('anti_nuke_enabled'))}  Leveling: {t(s.get('leveling_enabled',1))}  AI: {t(s.get('ai_enabled',1))}"
        ), inline=False)
        e.add_field(name="Bot Perms", value=(
            f"Admin: {t(mp.administrator)}  Ban: {t(mp.ban_members)}  Kick: {t(mp.kick_members)}\n"
            f"ManageRoles: {t(mp.manage_roles)}  ManageCh: {t(mp.manage_channels)}\n"
            f"AuditLog: {t(mp.view_audit_log)}  ManageMsgs: {t(mp.manage_messages)}"
        ), inline=False)
        e.add_field(name="Activity", value=(
            f"Ranked: **{lv}**  Eco users: **{eco}**  Cmds: **{(cmds or 0):,}**\n"
            f"Cases: **{mc}**  Warns: **{warns}**  Tickets: **{tk}**  GAs: **{gw}**"
        ), inline=False)
        e.set_footer(text=f"Guild ID: {g.id}  ·  XERO Core")
        await interaction.followup.send(embed=e, ephemeral=True)

    @app_commands.command(name="broadcast", description="Send an embed to every server's system or log channel.")
    @app_commands.describe(title="Embed title", message="Body text", urgent="Ping @everyone where allowed")
    @is_management()
    async def broadcast(self, interaction: discord.Interaction, title: str, message: str, urgent: bool = False):
        await interaction.response.send_message(
            embed=success_embed("📢 Broadcast Started", f"Sending to **{len(self.bot.guilds)}** servers..."),
            ephemeral=True
        )
        embed = discord.Embed(title=f"📢  {title}", description=message, color=discord.Color(D_BLUE), timestamp=discord.utils.utcnow())
        embed.set_author(name="XERO Bot  ·  Official Notice", icon_url=self.bot.user.display_avatar.url)
        embed.set_footer(text="XERO Bot  ·  Team Flame")
        sent = failed = 0
        for guild in self.bot.guilds:
            target = guild.system_channel
            if not target:
                gs = await self.bot.db.get_guild_settings(guild.id)
                cid = (gs or {}).get("log_channel_id") or (gs or {}).get("welcome_channel_id")
                target = guild.get_channel(cid) if cid else None
            if not target:
                target = next((c for c in guild.text_channels if guild.me.permissions_in(c).send_messages), None)
            if target:
                try:
                    content = "@everyone" if urgent and target.permissions_for(guild.me).mention_everyone else None
                    await target.send(content=content, embed=embed); sent += 1
                except Exception: failed += 1
            await asyncio.sleep(0.2)
        try:
            await interaction.followup.send(
                embed=success_embed("✅ Broadcast Complete", f"**{sent}** delivered  ·  **{failed}** failed"),
                ephemeral=True
            )
        except Exception: pass

    @app_commands.command(name="announce", description="Post to #announcement channels across all servers.")
    @app_commands.describe(title="Title", body="Body", ping="@everyone where allowed")
    @is_management()
    async def announce(self, interaction: discord.Interaction, title: str, body: str, ping: bool = False):
        await interaction.response.send_message(
            embed=success_embed("📣 Announce Started", f"Sending to **{len(self.bot.guilds)}** servers..."),
            ephemeral=True
        )
        embed = discord.Embed(title=f"📣  {title}", description=body, color=discord.Color(D_BLUE), timestamp=discord.utils.utcnow())
        embed.set_author(name="XERO Bot  ·  Announcement", icon_url=self.bot.user.display_avatar.url)
        embed.set_footer(text="XERO Bot  ·  Team Flame")
        sent = failed = 0
        for guild in self.bot.guilds:
            ch = next((c for c in guild.text_channels if "announce" in c.name.lower() and guild.me.permissions_in(c).send_messages), None)
            if not ch:
                gs = await self.bot.db.get_guild_settings(guild.id)
                cid = (gs or {}).get("log_channel_id")
                ch = guild.get_channel(cid) if cid else guild.system_channel
            if ch:
                try:
                    content = "@everyone" if ping and ch.permissions_for(guild.me).mention_everyone else None
                    await ch.send(content=content, embed=embed); sent += 1
                except Exception: failed += 1
            await asyncio.sleep(0.2)
        try:
            await interaction.followup.send(
                embed=success_embed("✅ Announce Complete", f"**{sent}** delivered  ·  **{failed}** failed"),
                ephemeral=True
            )
        except Exception: pass

    @app_commands.command(name="eval", description="[OWNER] Execute Python code in the live bot context.")
    @app_commands.describe(code="Python code to run")
    @is_management()
    async def eval_cmd(self, interaction: discord.Interaction, code: str):
        await interaction.response.defer(ephemeral=True)
        env = {"bot": self.bot, "discord": discord, "interaction": interaction,
               "db": self.bot.db, "asyncio": asyncio, "aiosqlite": aiosqlite}
        src = code.strip().strip("`")
        if src.startswith("py\n"): src = src[3:]
        try:
            exec("async def _eval():\n" + "\n".join("    " + l for l in src.split("\n")), env)
            result = await env["_eval"]()
            output = str(result) if result is not None else "✅ Done — no return value"
        except Exception:
            output = "❌ " + traceback.format_exc()[-1200:]
        await interaction.followup.send(
            embed=discord.Embed(title="⚡ Eval", description=f"```py\n{output[:1800]}\n```", color=discord.Color(D_BLUE)),
            ephemeral=True
        )

    @app_commands.command(name="sql", description="[OWNER] Run raw SQL on the XERO database.")
    @app_commands.describe(query="SQL to execute — SELECT, UPDATE, DELETE, etc.")
    @is_management()
    async def sql(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer(ephemeral=True)
        try:
            async with aiosqlite.connect(self.bot.db.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(query) as c:
                    rows = await c.fetchmany(20)
                if rows:
                    cols   = rows[0].keys()
                    header = " | ".join(cols)
                    sep    = "─" * len(header)
                    lines  = [header, sep] + [" | ".join(str(r[col]) for col in cols) for r in rows]
                    output = "\n".join(lines)
                else:
                    await db.commit()
                    output = "✅ Executed — 0 rows returned"
        except Exception as e:
            output = f"❌ {e}"
        await interaction.followup.send(
            embed=discord.Embed(title="🗄️ SQL Result", description=f"```\n{output[:1800]}\n```", color=discord.Color(D_BLUE)),
            ephemeral=True
        )

    @app_commands.command(name="reload", description="Reload a specific cog without restarting the bot.")
    @app_commands.describe(cog="Cog name e.g. economy, levels, tickets, ai")
    @is_management()
    async def reload(self, interaction: discord.Interaction, cog: str):
        await interaction.response.defer(ephemeral=True)
        ext = "cogs." + cog.lower().replace("cogs.", "")
        try:
            await self.bot.reload_extension(ext)
            _ToolsPanel._last_reload = datetime.datetime.utcnow().strftime("%H:%M:%S UTC")
            await interaction.followup.send(embed=success_embed("Reloaded", f"✅ `{ext}` reloaded."), ephemeral=True)
        except commands.ExtensionNotLoaded:
            try:
                await self.bot.load_extension(ext)
                await interaction.followup.send(embed=success_embed("Loaded", f"✅ `{ext}` loaded fresh."), ephemeral=True)
            except Exception as e:
                await interaction.followup.send(embed=error_embed("Failed", str(e)[:400]), ephemeral=True)
        except Exception:
            await interaction.followup.send(
                embed=error_embed("Failed", f"```{traceback.format_exc()[-600:]}```"), ephemeral=True
            )

    @app_commands.command(name="presence", description="Change XERO's global activity and online status.")
    @app_commands.describe(text="Activity text", type="Activity type", status="Online status")
    @app_commands.choices(
        type=[
            app_commands.Choice(name="Watching",  value="watching"),
            app_commands.Choice(name="Playing",   value="playing"),
            app_commands.Choice(name="Listening", value="listening"),
            app_commands.Choice(name="Competing", value="competing"),
        ],
        status=[
            app_commands.Choice(name="Online", value="online"),
            app_commands.Choice(name="Idle",   value="idle"),
            app_commands.Choice(name="DND",    value="dnd"),
        ]
    )
    @is_management()
    async def presence(self, interaction: discord.Interaction, text: str, type: str = "watching", status: str = "online"):
        atype  = getattr(discord.ActivityType, type, discord.ActivityType.watching)
        astatus= getattr(discord.Status, status, discord.Status.online)
        await self.bot.change_presence(status=astatus, activity=discord.Activity(type=atype, name=text))
        await interaction.response.send_message(
            embed=success_embed("Presence Updated", f"Now **{type}** `{text}`  ·  status: `{status}`"),
            ephemeral=True
        )

    @app_commands.command(name="dm-user", description="Send an official XERO team DM to any user.")
    @app_commands.describe(user_id="Target user ID", message="Message body")
    @is_management()
    async def dm_user(self, interaction: discord.Interaction, user_id: str, message: str):
        await interaction.response.defer(ephemeral=True)
        try:
            user = await self.bot.fetch_user(int(user_id))
            e = discord.Embed(
                title="📨  Message from the XERO Team",
                description=message,
                color=discord.Color(D_BLUE),
                timestamp=discord.utils.utcnow()
            )
            e.set_author(name="XERO Bot  ·  Team Flame", icon_url=self.bot.user.display_avatar.url)
            e.set_footer(text="This is an official message from the XERO development team.")
            await user.send(embed=e)
            await interaction.followup.send(embed=success_embed("DM Sent", f"✅ Delivered to **{user}**"), ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send(embed=error_embed("Failed", "User has DMs disabled."), ephemeral=True)
        except Exception as e:
            await interaction.followup.send(embed=error_embed("Failed", str(e)), ephemeral=True)

    @app_commands.command(name="set-avatar", description="Change XERO's global avatar from a direct image URL.")
    @app_commands.describe(url="Direct image URL (PNG / JPG / GIF)")
    @is_management()
    async def set_avatar(self, interaction: discord.Interaction, url: str):
        await interaction.response.defer(ephemeral=True)
        import aiohttp
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200: raise Exception(f"HTTP {resp.status}")
                    data = await resp.read()
            await self.bot.user.edit(avatar=data)
            e = success_embed("Avatar Updated", "XERO's avatar changed globally.")
            e.set_thumbnail(url=url)
            await interaction.followup.send(embed=e, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(embed=error_embed("Failed", str(e)), ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# /support — 8 COMMANDS (deep diagnostic tools, all require parameters)
# ══════════════════════════════════════════════════════════════════════════════
    @app_commands.command(name="backup-now", description="Force an immediate full DB backup to BACKUP_CHANNEL_ID.")
    @is_management()
    async def backup_now_cmd(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            embed=discord.Embed(description="💾 Creating backup...", color=discord.Color(0x00D4FF)),
            ephemeral=True
        )
        try:
            from utils.db_backup import backup_now
            ok = await backup_now(self.bot)
        except Exception as e:
            ok = False
        await interaction.followup.send(
            embed=discord.Embed(
                description="✅ Backup saved to backup channel." if ok
                else "❌ Failed — check BACKUP_CHANNEL_ID env var and bot permissions in that channel.",
                color=discord.Color(0x00FF94 if ok else 0xFF1744)
            ), ephemeral=True
        )

    @app_commands.command(name="restore-backup", description="Force restore DB from the latest backup in BACKUP_CHANNEL_ID.")
    @is_management()
    async def restore_backup(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            embed=discord.Embed(description="🔄 Scanning backup channel for latest file...", color=discord.Color(0x00D4FF)),
            ephemeral=True
        )
        try:
            from utils.db_backup import restore_latest
            ok = await restore_latest(self.bot)
        except Exception as e:
            ok = False
        await interaction.followup.send(
            embed=discord.Embed(
                description="✅ Restored successfully from latest backup." if ok
                else "❌ No valid backup found. Ensure `BACKUP_CHANNEL_ID` is set and the channel has backup files.",
                color=discord.Color(0x00FF94 if ok else 0xFF1744)
            ), ephemeral=True
        )

    @app_commands.command(name="bot-info", description="Technical info about this XERO instance.")
    @is_management()
    async def bot_info(self, interaction: discord.Interaction):
        cogs = list(self.bot.extensions.keys())
        upt  = int(time.time() - self.bot.start_time) if hasattr(self.bot, "start_time") else 0
        h, rem = divmod(upt, 3600); m_, s_ = divmod(rem, 60)
        import os
        backup_ch = os.getenv("BACKUP_CHANNEL_ID", "Not set")
        e = discord.Embed(title="XERO Instance Info", color=discord.Color(D_BLUE), timestamp=discord.utils.utcnow())
        e.add_field(name="Bot ID",       value=f"`{self.bot.user.id}`",                  inline=True)
        e.add_field(name="Uptime",       value=f"`{h}h {m_}m {s_}s`",                   inline=True)
        e.add_field(name="Ping",         value=f"`{round(self.bot.latency*1000)}ms`",    inline=True)
        e.add_field(name="Python",       value=f"`{sys.version[:10]}`",                  inline=True)
        e.add_field(name="discord.py",   value=f"`{discord.__version__}`",               inline=True)
        e.add_field(name="Cogs",         value=f"`{len(cogs)}`",                         inline=True)
        e.add_field(name="Mgmt Guild",   value=f"`{self.bot.MANAGEMENT_GUILD_ID}`",      inline=True)
        e.add_field(name="Backup Ch",    value=f"`{backup_ch}`",                         inline=True)
        e.add_field(name="DB Path",      value=f"`{self.bot.db.db_path}`",               inline=True)
        e.add_field(name="Loaded Cogs",  value="```\n" + "  ".join(c.replace("cogs.","") for c in cogs)[:500] + "\n```", inline=False)
        await interaction.response.send_message(embed=e, ephemeral=True)



class SupportTools(commands.GroupCog, name="support"):
    def __init__(self, bot): self.bot = bot

    @app_commands.command(name="diagnose", description="Full config + permission health check for any server. Scored 0-100.")
    @app_commands.describe(guild_id="Server ID to diagnose")
    @is_management()
    async def diagnose(self, interaction: discord.Interaction, guild_id: str):
        await interaction.response.defer(ephemeral=True)
        try: gid = int(guild_id); g = self.bot.get_guild(gid)
        except ValueError: g = None
        if not g:
            return await interaction.followup.send(embed=error_embed("Not Found", f"XERO isn't in `{guild_id}`."), ephemeral=True)

        s = await self.bot.db.get_guild_settings(gid) or {}
        good = []; warnings = []; issues = []

        # Channel checks
        ch_checks = [
            ("Log Channel",      "log_channel_id"),
            ("Welcome Channel",  "welcome_channel_id"),
            ("Message Logs",     "message_log_channel_id"),
            ("Member Logs",      "member_log_channel_id"),
            ("Voice Logs",       "voice_log_channel_id"),
            ("Server Logs",      "server_log_channel_id"),
            ("Level-Up Channel", "level_up_channel_id"),
            ("Ticket Log Ch",    "ticket_log_channel_id"),
        ]
        for label, key in ch_checks:
            cid = s.get(key)
            if not cid:
                issues.append(f"❌ **{label}** — not configured")
                continue
            ch = g.get_channel(cid)
            if not ch:
                issues.append(f"❌ **{label}** — channel deleted (was `{cid}`)")
                continue
            p = ch.permissions_for(g.me)
            if not p.send_messages:  issues.append(f"❌ **{label}** — can't send in {ch.mention}")
            elif not p.embed_links:  warnings.append(f"⚠️ **{label}** — no Embed Links in {ch.mention}")
            else:                    good.append(f"✅ **{label}**: {ch.mention}")

        # Role checks
        ar = g.get_role(s.get("autorole_id") or 0)
        if s.get("autorole_id") and not ar:
            issues.append(f"❌ **Auto-Role** — role deleted (was `{s['autorole_id']}`)")
        elif ar:
            if ar >= g.me.top_role: issues.append(f"❌ **Auto-Role** `{ar.name}` — above XERO's role, can't assign")
            else:                   good.append(f"✅ **Auto-Role**: {ar.mention}")

        tr = g.get_role(s.get("ticket_support_role_id") or 0)
        if s.get("ticket_support_role_id") and not tr:
            issues.append("❌ **Ticket Support Role** — role deleted")
        elif tr:
            good.append(f"✅ **Ticket Role**: {tr.mention}")

        # Guild-wide permissions
        mp = g.me.guild_permissions
        perm_checks = [
            ("Ban Members",       "ban_members"),
            ("Kick Members",      "kick_members"),
            ("Manage Roles",      "manage_roles"),
            ("Manage Channels",   "manage_channels"),
            ("Manage Messages",   "manage_messages"),
            ("View Audit Log",    "view_audit_log"),
            ("Embed Links",       "embed_links"),
            ("Add Reactions",     "add_reactions"),
            ("Attach Files",      "attach_files"),
            ("Read Msg History",  "read_message_history"),
            ("Manage Threads",    "manage_threads"),
        ]
        for name, perm in perm_checks:
            if getattr(mp, perm, False): good.append(f"✅ **{name}**")
            else:                        issues.append(f"❌ Missing perm: **{name}**")

        # Feature toggles
        if not s.get("leveling_enabled", 1): warnings.append("⚠️ **Leveling** is disabled")
        if not s.get("economy_enabled",  1): warnings.append("⚠️ **Economy** is disabled")
        if not s.get("ai_enabled",        1): warnings.append("⚠️ **AI features** are disabled")

        score = int(len(good) / max(len(good) + len(issues), 1) * 100)
        bar   = _bar(score, 12)

        embed = discord.Embed(
            title=f"🔍  Diagnosis  ·  {g.name}",
            description=f"`{bar}` **{score}/100**",
            color=discord.Color(0x2ECC71 if score >= 70 else (D_AMBER if score >= 40 else D_RED)),
            timestamp=discord.utils.utcnow()
        )
        if g.icon: embed.set_thumbnail(url=g.icon.url)
        if good:     embed.add_field(name=f"✅ Passing ({len(good)})",    value="\n".join(good[:14])[:900],     inline=False)
        if warnings: embed.add_field(name=f"⚠️ Warnings ({len(warnings)})", value="\n".join(warnings)[:600],   inline=False)
        if issues:   embed.add_field(name=f"❌ Issues ({len(issues)})",   value="\n".join(issues[:15])[:900],   inline=False)
        embed.set_footer(text=f"Guild ID: {gid}  ·  XERO Support")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="user-lookup", description="Full XERO network profile on any user — all activity, economy, mod history, AI skills.")
    @app_commands.describe(user_id="Discord User ID")
    @is_management()
    async def user_lookup(self, interaction: discord.Interaction, user_id: str):
        await interaction.response.defer(ephemeral=True)
        try:
            uid  = int(user_id)
            user = await self.bot.fetch_user(uid)
        except Exception as e:
            return await interaction.followup.send(embed=error_embed("Not Found", str(e)), ephemeral=True)

        shared   = [g for g in self.bot.guilds if g.get_member(uid)]
        age_days = (discord.utils.utcnow() - user.created_at).days

        (cmds_used, msgs_sent, total_xp, total_wealth,
         warns, cases, tickets, gw_entries, verifications) = await _qdb(self.bot,
            ("SELECT SUM(commands_used) FROM user_stats WHERE user_id=?",    [uid]),
            ("SELECT SUM(messages_sent) FROM user_stats WHERE user_id=?",    [uid]),
            ("SELECT SUM(total_xp) FROM levels WHERE user_id=?",             [uid]),
            ("SELECT SUM(wallet+bank) FROM economy WHERE user_id=?",         [uid]),
            ("SELECT COUNT(*) FROM warnings WHERE user_id=?",                [uid]),
            ("SELECT COUNT(*) FROM mod_cases WHERE user_id=?",               [uid]),
            ("SELECT COUNT(*) FROM tickets WHERE user_id=?",                 [uid]),
            ("SELECT COUNT(*) FROM giveaway_participants WHERE user_id=?",   [uid]),
            ("SELECT COUNT(*) FROM user_verifications WHERE user_id=?",      [uid]),
        )

        async with aiosqlite.connect(self.bot.db.db_path) as db:
            # Blacklist
            try:
                async with db.execute("SELECT reason, blacklisted_at FROM blacklisted_users WHERE user_id=?", (uid,)) as cu:
                    bl = await cu.fetchone()
            except Exception: bl = None

            # Cross-server ban history
            async with db.execute(
                "SELECT guild_id, action, reason, timestamp FROM mod_cases"
                " WHERE user_id=? AND action IN ('ban','tempban','kick') ORDER BY case_id DESC LIMIT 8",
                (uid,)
            ) as cu:
                action_history = await cu.fetchall()

            # AI skill profile
            async with db.execute(
                "SELECT skills, interests, personality FROM member_profiles WHERE user_id=? LIMIT 1", (uid,)
            ) as cu:
                prof = await cu.fetchone()

        import json
        top_skills = top_interests = []
        if prof:
            try:
                if prof[0]: top_skills    = sorted(json.loads(prof[0]).items(), key=lambda x: x[1], reverse=True)[:4]
                if prof[1]: top_interests = sorted(json.loads(prof[1]).items(), key=lambda x: x[1], reverse=True)[:3]
            except Exception: pass

        bans_count = sum(1 for r in action_history if r[1] == "ban")
        kick_count = sum(1 for r in action_history if r[1] == "kick")

        embed = discord.Embed(
            title=f"🔍  User Profile  ·  {user}",
            color=discord.Color(D_RED if bl else D_BLUE),
            timestamp=discord.utils.utcnow()
        )
        embed.set_thumbnail(url=user.display_avatar.url)

        # Identity
        embed.add_field(name="Identity", value=(
            f"ID: `{uid}`\n"
            f"Account age: **{age_days}** days\n"
            f"Bot account: **{'Yes' if user.bot else 'No'}**\n"
            f"Shared servers: **{len(shared)}**"
        ), inline=True)

        # Activity
        embed.add_field(name="Activity", value=(
            f"Commands: **{(cmds_used or 0):,}**\n"
            f"Messages: **{(msgs_sent or 0):,}**\n"
            f"Giveaway entries: **{gw_entries:,}**\n"
            f"Verifications: **{verifications}**"
        ), inline=True)

        # Economy & XP
        embed.add_field(name="Economy & XP", value=(
            f"Total XP: **{(total_xp or 0):,}**\n"
            f"Network wealth: **${(total_wealth or 0):,}**"
        ), inline=True)

        # Moderation
        embed.add_field(name="Moderation", value=(
            f"Warnings: **{warns}**\n"
            f"Total mod cases: **{cases}**\n"
            f"Cross-server bans: **{bans_count}**\n"
            f"Cross-server kicks: **{kick_count}**\n"
            f"Tickets opened: **{tickets}**"
        ), inline=True)

        # AI Profile
        if top_skills or top_interests:
            skill_str   = "  ".join(f"**{k}** {int(v*100)}%" for k, v in top_skills)    if top_skills    else "—"
            int_str     = "  ".join(f"**{k}** {int(v*100)}%" for k, v in top_interests) if top_interests else "—"
            embed.add_field(name="🤖 AI Profile", value=f"Skills: {skill_str}\nInterests: {int_str}", inline=False)

        # Blacklist banner
        if bl:
            embed.add_field(name="🚨 GLOBALLY BLACKLISTED", value=(
                f"Reason: **{bl[0]}**\n"
                f"Since: `{(bl[1] or '?')[:10]}`"
            ), inline=False)

        # Cross-server action history
        if action_history:
            lines = []
            for gid, action, reason, ts in action_history[:6]:
                go = self.bot.get_guild(gid)
                lines.append(f"`{(ts or '')[:10]}` **{action.upper()}** in **{go.name if go else gid}** — {(reason or 'no reason')[:40]}")
            embed.add_field(name="📋 Cross-Server History", value="\n".join(lines), inline=False)

        if shared:
            embed.add_field(name="🌐 Mutual Servers", value=", ".join(g.name for g in shared[:12]), inline=False)

        embed.set_footer(text="XERO Support  ·  Team Flame")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="check-perms", description="Audit XERO's permissions in every single channel of a server.")
    @app_commands.describe(guild_id="Server ID")
    @is_management()
    async def check_perms(self, interaction: discord.Interaction, guild_id: str):
        await interaction.response.defer(ephemeral=True)
        try: g = self.bot.get_guild(int(guild_id))
        except ValueError: g = None
        if not g: return await interaction.followup.send(embed=error_embed("Not Found", "Not in that server."), ephemeral=True)

        ok_count = 0; text_issues = []; voice_issues = []
        for ch in g.channels:
            p = ch.permissions_for(g.me)
            if isinstance(ch, discord.TextChannel):
                bad = []
                if not p.view_channel:         bad.append("view")
                if not p.send_messages:        bad.append("send")
                if not p.embed_links:          bad.append("embeds")
                if not p.read_message_history: bad.append("history")
                if not p.attach_files:         bad.append("files")
                if bad: text_issues.append(f"#{ch.name}: `{'` `'.join(bad)}`")
                else:   ok_count += 1
            elif isinstance(ch, discord.VoiceChannel):
                if not p.view_channel or not p.connect:
                    voice_issues.append(f"🔊 {ch.name}: can't view/connect")

        mp = g.me.guild_permissions
        embed = discord.Embed(
            title=f"🔐  Permission Audit  ·  {g.name}",
            description=(
                f"Text channels: **{ok_count}** clean  ·  **{len(text_issues)}** issues\n"
                f"Voice issues: **{len(voice_issues)}**"
            ),
            color=discord.Color(D_RED if text_issues else 0x2ECC71)
        )

        perm_grid = (
            f"Admin: {'✅' if mp.administrator else '❌'}  "
            f"Ban: {'✅' if mp.ban_members else '❌'}  "
            f"Kick: {'✅' if mp.kick_members else '❌'}  "
            f"ManageRoles: {'✅' if mp.manage_roles else '❌'}\n"
            f"ManageCh: {'✅' if mp.manage_channels else '❌'}  "
            f"AuditLog: {'✅' if mp.view_audit_log else '❌'}  "
            f"ManageMsgs: {'✅' if mp.manage_messages else '❌'}  "
            f"ManageThreads: {'✅' if mp.manage_threads else '❌'}"
        )
        embed.add_field(name="Guild-Wide Permissions", value=perm_grid, inline=False)

        if text_issues:
            embed.add_field(
                name=f"⚠️ Text Channel Issues ({len(text_issues)})",
                value="\n".join(text_issues[:18])[:900],
                inline=False
            )
        else:
            embed.add_field(name="✅ Text Channels", value="All channels have full send + embed permissions.", inline=False)

        if voice_issues:
            embed.add_field(name=f"🔊 Voice Issues ({len(voice_issues)})", value="\n".join(voice_issues[:6]), inline=False)

        embed.set_footer(text=f"Guild ID: {g.id}")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="guild-errors", description="Recent mod actions, error log, and activity snapshot for any server.")
    @app_commands.describe(guild_id="Server ID", limit="How many recent mod cases to show (default 10)")
    @is_management()
    async def guild_errors(self, interaction: discord.Interaction, guild_id: str, limit: int = 10):
        await interaction.response.defer(ephemeral=True)
        try: gid = int(guild_id)
        except ValueError:
            return await interaction.followup.send(embed=error_embed("Invalid", "Numeric ID only."), ephemeral=True)

        g = self.bot.get_guild(gid)
        gname = g.name if g else f"Guild {gid}"

        async with aiosqlite.connect(self.bot.db.db_path) as db:
            async with db.execute(
                "SELECT action, reason, moderator_id, timestamp FROM mod_cases"
                " WHERE guild_id=? ORDER BY case_id DESC LIMIT ?", (gid, limit)
            ) as c: cases = await c.fetchall()
            async with db.execute(
                "SELECT warning_text, moderator_id, timestamp FROM warnings"
                " WHERE guild_id=? ORDER BY id DESC LIMIT 5", (gid,)
            ) as c: recent_warns = await c.fetchall()

        (warns, open_tix, total_tix, cmds, msgs,
         active_users, xp_users, eco_users, gw) = await _qdb(self.bot,
            ("SELECT COUNT(*) FROM warnings WHERE guild_id=?",                              [gid]),
            ("SELECT COUNT(*) FROM tickets WHERE guild_id=? AND status='open'",             [gid]),
            ("SELECT COUNT(*) FROM tickets WHERE guild_id=?",                               [gid]),
            ("SELECT SUM(commands_used) FROM user_stats WHERE guild_id=?",                  [gid]),
            ("SELECT SUM(messages_sent) FROM user_stats WHERE guild_id=?",                  [gid]),
            ("SELECT COUNT(DISTINCT user_id) FROM user_stats WHERE guild_id=? AND commands_used>0", [gid]),
            ("SELECT COUNT(*) FROM levels WHERE guild_id=? AND total_xp>0",                 [gid]),
            ("SELECT COUNT(*) FROM economy WHERE guild_id=? AND wallet+bank>0",             [gid]),
            ("SELECT COUNT(*) FROM giveaways WHERE guild_id=?",                             [gid]),
        )

        async with aiosqlite.connect(self.bot.db.db_path) as db:
            try:
                async with db.execute("SELECT reason, blacklisted_at FROM blacklisted_guilds WHERE guild_id=?", (gid,)) as cu:
                    bl = await cu.fetchone()
            except Exception: bl = None

        embed = discord.Embed(
            title=f"📋  Activity Report  ·  {gname}",
            color=discord.Color(D_RED if bl else D_DARK),
            timestamp=discord.utils.utcnow()
        )

        if bl:
            embed.add_field(
                name="🚨 BLACKLISTED SERVER",
                value=f"Reason: **{bl[0]}**  ·  Since `{(bl[1] or '?')[:10]}`",
                inline=False
            )

        embed.add_field(name="Activity Snapshot", value=(
            f"Commands used: **{(cmds or 0):,}**  ·  Messages tracked: **{(msgs or 0):,}**\n"
            f"Active users: **{active_users}**  ·  Ranked users: **{xp_users}**  ·  Eco users: **{eco_users}**\n"
            f"Tickets: **{open_tix}** open / **{total_tix}** total  ·  Giveaways: **{gw}**"
        ), inline=False)

        embed.add_field(name="Moderation Summary", value=(
            f"Total warnings: **{warns}**  ·  Total mod cases: **{len(cases) if cases else 0}**"
        ), inline=False)

        if cases:
            lines = []
            for action, reason, mod_id, ts in cases:
                mod_name = f"<@{mod_id}>" if mod_id else "unknown"
                lines.append(f"`{(ts or '')[:10]}` **{action.upper()}** by {mod_name} — {(reason or 'no reason')[:45]}")
            embed.add_field(
                name=f"📌 Recent Mod Actions ({len(cases)})",
                value="\n".join(lines)[:900],
                inline=False
            )

        if recent_warns:
            lines = [f"`{(r[2] or '')[:10]}` {(r[0] or 'no text')[:50]}" for r in recent_warns]
            embed.add_field(name="⚠️ Recent Warnings", value="\n".join(lines)[:400], inline=False)

        embed.set_footer(text=f"Guild ID: {gid}  ·  XERO Support")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="reset-guild", description="Reset a server's XERO config to factory defaults. All user data preserved.")
    @app_commands.describe(guild_id="Server ID", confirm="Type CONFIRM to proceed")
    @is_management()
    async def reset_guild(self, interaction: discord.Interaction, guild_id: str, confirm: str):
        if confirm != "CONFIRM":
            return await interaction.response.send_message(
                embed=error_embed("Cancelled", "Pass `CONFIRM` exactly as the confirm parameter to proceed."),
                ephemeral=True
            )
        try: gid = int(guild_id)
        except ValueError:
            return await interaction.response.send_message(embed=error_embed("Invalid", "Numeric ID only."), ephemeral=True)

        async with aiosqlite.connect(self.bot.db.db_path) as db:
            await db.execute("DELETE FROM guild_settings WHERE guild_id=?", (gid,))
            await db.commit()
        await self.bot.db.create_guild_settings(gid)

        g = self.bot.get_guild(gid)
        await interaction.response.send_message(embed=success_embed(
            "Settings Reset",
            f"**{g.name if g else gid}** config wiped and reset to defaults.\n"
            "✅ XP, economy, tickets, mod cases — all preserved.\n"
            "Server admin can reconfigure with `/config dashboard`."
        ), ephemeral=True)

    @app_commands.command(name="global-ban-check", description="Check any user's global blacklist status and full cross-server ban/kick history.")
    @app_commands.describe(user_id="Discord User ID")
    @is_management()
    async def global_ban_check(self, interaction: discord.Interaction, user_id: str):
        await interaction.response.defer(ephemeral=True)
        try: uid = int(user_id)
        except ValueError:
            return await interaction.followup.send(embed=error_embed("Invalid", "Numeric ID only."), ephemeral=True)

        warns, cases = await _qdb(self.bot,
            ("SELECT COUNT(*) FROM warnings WHERE user_id=?",  [uid]),
            ("SELECT COUNT(*) FROM mod_cases WHERE user_id=?", [uid]),
        )

        async with aiosqlite.connect(self.bot.db.db_path) as db:
            try:
                async with db.execute("SELECT reason, blacklisted_at FROM blacklisted_users WHERE user_id=?", (uid,)) as cu:
                    bl = await cu.fetchone()
            except Exception: bl = None
            async with db.execute(
                "SELECT guild_id, action, reason, timestamp FROM mod_cases"
                " WHERE user_id=? ORDER BY case_id DESC LIMIT 12", (uid,)
            ) as cu:
                all_cases = await cu.fetchall()

        bans     = [r for r in all_cases if r[1].lower() in ("ban", "tempban")]
        kicks    = [r for r in all_cases if r[1].lower() == "kick"]
        mutes    = [r for r in all_cases if "mute" in r[1].lower()]
        shared   = [g for g in self.bot.guilds if g.get_member(uid)]

        try:
            user   = await self.bot.fetch_user(uid)
            name   = str(user)
            avatar = user.display_avatar.url
        except Exception:
            name = f"User {uid}"; avatar = None

        embed = discord.Embed(
            title=f"🌐  Global Ban Check  ·  {name}",
            color=discord.Color(D_RED if bl else (D_AMBER if bans else D_BLUE))
        )
        if avatar: embed.set_thumbnail(url=avatar)

        embed.add_field(name="Blacklisted", value=f"**{'🚨 YES' if bl else '✅ No'}**",  inline=True)
        embed.add_field(name="Warnings",    value=f"**{warns}**",                          inline=True)
        embed.add_field(name="Total Cases", value=f"**{cases}**",                          inline=True)
        embed.add_field(name="Bans",        value=f"**{len(bans)}**",                      inline=True)
        embed.add_field(name="Kicks",       value=f"**{len(kicks)}**",                     inline=True)
        embed.add_field(name="Mutes",       value=f"**{len(mutes)}**",                     inline=True)
        embed.add_field(name="In Servers",  value=f"**{len(shared)}**",                    inline=True)

        if bl:
            embed.add_field(name="🚨 Blacklist Details", value=(
                f"Reason: **{bl[0]}**\n"
                f"Date: `{(bl[1] or '?')[:10]}`"
            ), inline=False)

        if bans:
            lines = []
            for gid, action, reason, ts in bans[:6]:
                go = self.bot.get_guild(gid)
                lines.append(f"`{(ts or '')[:10]}` **{action.upper()}** — {go.name if go else gid} — {(reason or 'no reason')[:40]}")
            embed.add_field(name=f"🔨 Ban History ({len(bans)})", value="\n".join(lines), inline=False)

        if kicks:
            lines = []
            for gid, action, reason, ts in kicks[:4]:
                go = self.bot.get_guild(gid)
                lines.append(f"`{(ts or '')[:10]}` {go.name if go else gid} — {(reason or 'no reason')[:40]}")
            embed.add_field(name=f"👢 Kick History ({len(kicks)})", value="\n".join(lines), inline=False)

        if shared:
            embed.add_field(name="📍 Currently In", value=", ".join(g.name for g in shared[:12]), inline=False)

        embed.set_footer(text="XERO Support  ·  Team Flame")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="set-name", description="Change XERO's global bot username.")
    @app_commands.describe(name="New username (2-32 characters)")
    @is_management()
    async def set_name(self, interaction: discord.Interaction, name: str):
        await interaction.response.defer(ephemeral=True)
        if not 2 <= len(name) <= 32:
            return await interaction.followup.send(embed=error_embed("Invalid", "Username must be 2-32 characters."), ephemeral=True)
        old = self.bot.user.name
        try:
            await self.bot.user.edit(username=name)
            await interaction.followup.send(
                embed=success_embed("Name Changed", f"**{old}** → **{name}**\n*Note: Discord rate-limits username changes.*"),
                ephemeral=True
            )
        except discord.HTTPException as e:
            await interaction.followup.send(embed=error_embed("Failed", str(e)), ephemeral=True)

    @app_commands.command(name="server-compare", description="Side-by-side deep comparison of two servers — config, activity, size, health.")
    @app_commands.describe(guild_id_a="First server ID", guild_id_b="Second server ID")
    @is_management()
    async def server_compare(self, interaction: discord.Interaction, guild_id_a: str, guild_id_b: str):
        await interaction.response.defer(ephemeral=True)
        try:
            ga = self.bot.get_guild(int(guild_id_a))
            gb = self.bot.get_guild(int(guild_id_b))
        except ValueError:
            return await interaction.followup.send(embed=error_embed("Invalid", "Numeric IDs only."), ephemeral=True)
        if not ga or not gb:
            return await interaction.followup.send(embed=error_embed("Not Found", "XERO isn't in one of those servers."), ephemeral=True)

        sa = await self.bot.db.get_guild_settings(ga.id) or {}
        sb = await self.bot.db.get_guild_settings(gb.id) or {}

        cfg_keys = ["welcome_channel_id","log_channel_id","autorole_id",
                    "verify_channel_id","ticket_support_role_id","automod_enabled","anti_nuke_enabled"]

        def cfg_score(s): return sum(1 for k in cfg_keys if s.get(k))

        a_cmds, a_lvl, a_tix, a_warns = await _qdb(self.bot,
            ("SELECT SUM(commands_used) FROM user_stats WHERE guild_id=?",          [ga.id]),
            ("SELECT COUNT(*) FROM levels WHERE guild_id=? AND total_xp>0",         [ga.id]),
            ("SELECT COUNT(*) FROM tickets WHERE guild_id=?",                       [ga.id]),
            ("SELECT COUNT(*) FROM warnings WHERE guild_id=?",                      [ga.id]),
        )
        b_cmds, b_lvl, b_tix, b_warns = await _qdb(self.bot,
            ("SELECT SUM(commands_used) FROM user_stats WHERE guild_id=?",          [gb.id]),
            ("SELECT COUNT(*) FROM levels WHERE guild_id=? AND total_xp>0",         [gb.id]),
            ("SELECT COUNT(*) FROM tickets WHERE guild_id=?",                       [gb.id]),
            ("SELECT COUNT(*) FROM warnings WHERE guild_id=?",                      [gb.id]),
        )

        now = discord.utils.utcnow()
        a_days = (now - ga.me.joined_at).days if ga.me.joined_at else "?"
        b_days = (now - gb.me.joined_at).days if gb.me.joined_at else "?"

        embed = discord.Embed(
            title="⚖️  Server Comparison",
            color=discord.Color(D_BLUE),
            timestamp=discord.utils.utcnow()
        )

        def row(label, a_val, b_val, better="higher"):
            a_str = str(a_val); b_str = str(b_val)
            try:
                a_n = float(str(a_val).replace(",","").replace("$",""))
                b_n = float(str(b_val).replace(",","").replace("$",""))
                if better == "higher":
                    a_str = ("**" + str(a_val) + "** ✓") if a_n > b_n else str(a_val)
                    b_str = ("**" + str(b_val) + "** ✓") if b_n > a_n else str(b_val)
                else:
                    a_str = ("**" + str(a_val) + "** ✓") if a_n < b_n else str(a_val)
                    b_str = ("**" + str(b_val) + "** ✓") if b_n < a_n else str(b_val)
            except Exception: pass
            return f"`{label}` {a_str}  ·  {b_str}"

        embed.add_field(
            name="Server",
            value=f"**{ga.name}**\n`{ga.id}`",
            inline=True
        )
        embed.add_field(name="vs", value="\u200b", inline=True)
        embed.add_field(
            name="Server",
            value=f"**{gb.name}**\n`{gb.id}`",
            inline=True
        )

        comparison = "\n".join([
            row("Members",    f"{ga.member_count:,}", f"{gb.member_count:,}"),
            row("Channels",   len(ga.channels),       len(gb.channels)),
            row("Boost Tier", ga.premium_tier,        gb.premium_tier),
            row("Config",     f"{cfg_score(sa)}/7",   f"{cfg_score(sb)}/7"),
            row("Cmds Used",  f"{(a_cmds or 0):,}",  f"{(b_cmds or 0):,}"),
            row("Ranked",     a_lvl,                  b_lvl),
            row("Tickets",    a_tix,                  b_tix),
            row("Warnings",   a_warns,                b_warns, better="lower"),
            row("XERO age",   f"{a_days}d",           f"{b_days}d", better="higher"),
        ])
        embed.add_field(name="Comparison", value=comparison, inline=False)
        embed.set_footer(text="XERO Support  ·  Team Flame  ·  ✓ = winner")
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot):
    mguild = discord.Object(id=bot.MANAGEMENT_GUILD_ID)
    await bot.add_cog(CoreAdmin(bot),    guilds=[mguild])
    await bot.add_cog(SupportTools(bot), guilds=[mguild])
    logger.info(f"✓ /core + /support bound to management guild ({bot.MANAGEMENT_GUILD_ID})")
