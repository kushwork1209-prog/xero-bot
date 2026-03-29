"""
XERO Bot — Core Administration (20 commands)
Management-only. Only accessible from the management guild or by staff IDs.
"""
import discord, logging, time, os, sys, asyncio, datetime, json, io
import aiosqlite
from discord.ext import commands
from discord import app_commands
from utils.embeds import success_embed, error_embed, info_embed, XERO
from utils.guard import command_guard

logger = logging.getLogger("XERO.Core")

# ── Auth check ────────────────────────────────────────────────────────────────
def is_management():
    async def predicate(interaction: discord.Interaction) -> bool:
        staff_ids = [1124403755459346514, 1104381387601199144]
        if interaction.user.id in staff_ids:
            return True
        if interaction.guild_id == interaction.client.MANAGEMENT_GUILD_ID:
            if interaction.user.guild_permissions.manage_guild:
                return True
        await interaction.response.send_message("❌ Management only.", ephemeral=True)
        return False
    return app_commands.check(predicate)


class CoreAdmin(commands.GroupCog, name="core"):
    def __init__(self, bot):
        self.bot = bot

    # ── 1. Sync ───────────────────────────────────────────────────────────────
    @app_commands.command(name="sync", description="Force sync all slash commands to Discord.")
    @app_commands.describe(scope="global = everywhere | guild = this server only")
    @app_commands.choices(scope=[
        app_commands.Choice(name="Global (all servers, up to 1hr)",  value="global"),
        app_commands.Choice(name="This guild only (instant)",        value="guild"),
    ])
    @is_management()
    @command_guard
    async def sync(self, interaction: discord.Interaction, scope: str = "global"):
        await interaction.response.defer(ephemeral=True)
        if scope == "global":
            synced = await self.bot.tree.sync()
            await interaction.followup.send(embed=success_embed("Global Sync", f"✅ `{len(synced)}` commands synced globally."), ephemeral=True)
        else:
            self.bot.tree.copy_global_to(guild=interaction.guild)
            synced = await self.bot.tree.sync(guild=interaction.guild)
            await interaction.followup.send(embed=success_embed("Guild Sync", f"✅ `{len(synced)}` commands synced to **{interaction.guild.name}**."), ephemeral=True)

    # ── 2. Stats ──────────────────────────────────────────────────────────────
    @app_commands.command(name="stats", description="Full system stats — memory, latency, guilds, uptime.")
    @is_management()
    async def stats(self, interaction: discord.Interaction):
        uptime = time.time() - self.bot.launch_time
        h, rem = divmod(int(uptime), 3600); m_, s_ = divmod(rem, 60)
        ws_ms = round(self.bot.latency * 1000)
        guilds = len(self.bot.guilds)
        users  = sum(g.member_count for g in self.bot.guilds)
        cogs   = len(self.bot.extensions)

        # DB size
        db_size = "N/A"
        try:
            db_size = f"{os.path.getsize(self.bot.db.db_path)/1024/1024:.2f} MB"
        except Exception: pass

        # RAM/CPU
        mem_str = cpu_str = "N/A"
        try:
            import psutil
            proc = psutil.Process(os.getpid())
            mem_str = f"{proc.memory_info().rss/1024/1024:.0f} MB"
            cpu_str = f"{psutil.cpu_percent()}%"
        except Exception: pass

        e = discord.Embed(title="⚙️  XERO System Stats", color=0x00D4FF, timestamp=discord.utils.utcnow())
        e.add_field(name="🌐 Guilds",      value=f"`{guilds:,}`",     inline=True)
        e.add_field(name="👥 Users",       value=f"`{users:,}`",     inline=True)
        e.add_field(name="📡 WS Latency",  value=f"`{ws_ms}ms`",     inline=True)
        e.add_field(name="⏱️ Uptime",      value=f"`{h}h {m_}m {s_}s`", inline=True)
        e.add_field(name="💾 RAM",         value=f"`{mem_str}`",      inline=True)
        e.add_field(name="🖥️ CPU",         value=f"`{cpu_str}`",      inline=True)
        e.add_field(name="📦 Cogs",        value=f"`{cogs}`",         inline=True)
        e.add_field(name="🗄️ DB Size",     value=f"`{db_size}`",      inline=True)
        e.add_field(name="🐍 Python",      value=f"`{sys.version[:10]}`", inline=True)
        e.add_field(name="📚 discord.py",  value=f"`{discord.__version__}`", inline=True)
        e.set_footer(text=f"Bot ID: {self.bot.user.id}")
        await interaction.response.send_message(embed=e, ephemeral=True)

    # ── 3. Status ─────────────────────────────────────────────────────────────
    @app_commands.command(name="status", description="Check which command groups are visible in Discord.")
    @is_management()
    @command_guard
    async def status(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        global_cmds = await self.bot.tree.fetch_commands()
        names = sorted(c.name for c in global_cmds)
        e = discord.Embed(title="📋  Command Sync Status", color=0x00D4FF, timestamp=discord.utils.utcnow())
        e.add_field(name=f"Global Commands ({len(global_cmds)})", value=", ".join(f"`{n}`" for n in names[:40]) or "None", inline=False)
        e.set_footer(text="Use /core sync if commands are missing")
        await interaction.followup.send(embed=e, ephemeral=True)

    # ── 4. Reload ─────────────────────────────────────────────────────────────
    @app_commands.command(name="reload", description="Reload a cog without restarting the bot.")
    @app_commands.describe(cog="Cog name e.g. automod, tickets, security")
    @is_management()
    @command_guard
    async def reload(self, interaction: discord.Interaction, cog: str):
        await interaction.response.defer(ephemeral=True)
        ext = f"cogs.{cog.lower().replace('cogs.','')}"
        try:
            await self.bot.reload_extension(ext)
            await interaction.followup.send(embed=success_embed("Cog Reloaded", f"✅ `{ext}` reloaded successfully."), ephemeral=True)
        except Exception as e:
            await interaction.followup.send(embed=error_embed("Reload Failed", f"```py\n{e}\n```"), ephemeral=True)

    # ── 5. Eval ───────────────────────────────────────────────────────────────
    @app_commands.command(name="eval", description="[OWNER] Execute Python code live.")
    @app_commands.describe(code="Python code to execute")
    @is_management()
    @command_guard
    async def eval_cmd(self, interaction: discord.Interaction, code: str):
        await interaction.response.defer(ephemeral=True)
        env = {"bot": self.bot, "discord": discord, "interaction": interaction, "asyncio": asyncio}
        src = code.strip().strip("`")
        if src.startswith("py\n"): src = src[3:]
        try:
            exec(f"async def _e():\n" + "\n".join(f"    {l}" for l in src.split("\n")), env)
            result = await env["_e"]()
            out = str(result) if result is not None else "✅ Done"
        except Exception as ex:
            out = f"❌ {type(ex).__name__}: {ex}"
        await interaction.followup.send(embed=discord.Embed(description=f"```py\n{out[:1900]}\n```", color=0x00D4FF), ephemeral=True)

    # ── 6. SQL ────────────────────────────────────────────────────────────────
    @app_commands.command(name="sql", description="[OWNER] Run a raw SQL query on the database.")
    @app_commands.describe(query="SQL query to run")
    @is_management()
    @command_guard
    async def sql(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer(ephemeral=True)
        try:
            async with self.bot.db._db_context() as db:
                async with db.execute(query) as cur:
                    rows = await cur.fetchmany(20)
                await db.commit()
            if rows:
                headers = [d[0] for d in cur.description] if cur.description else []
                lines = [" | ".join(headers)] + ["─"*40] + [" | ".join(str(v) for v in row) for row in rows]
                out = "\n".join(lines)[:1800]
            else:
                out = "✅ Query executed. No rows returned."
            await interaction.followup.send(embed=discord.Embed(description=f"```\n{out}\n```", color=0x00D4FF), ephemeral=True)
        except Exception as e:
            await interaction.followup.send(embed=error_embed("SQL Error", f"```py\n{e}\n```"), ephemeral=True)

    # ── 7. Broadcast ──────────────────────────────────────────────────────────
    @app_commands.command(name="broadcast", description="Send an announcement to all servers XERO is in.")
    @app_commands.describe(title="Announcement title", message="Announcement body", urgent="Ping @everyone in each server")
    @is_management()
    @command_guard
    async def broadcast(self, interaction: discord.Interaction, title: str, message: str, urgent: bool = False):
        await interaction.response.send_message(embed=discord.Embed(description=f"📡 Broadcasting to **{len(self.bot.guilds)}** servers...", color=0x00D4FF), ephemeral=True)
        embed = discord.Embed(title=f"📢  {title}", description=message, color=0x00D4FF, timestamp=discord.utils.utcnow())
        embed.set_author(name="XERO — Official Notice", icon_url=self.bot.user.display_avatar.url)
        embed.set_footer(text="XERO Bot  ·  Team Flame")
        sent = failed = 0
        for guild in self.bot.guilds:
            ch = guild.system_channel or next((c for c in guild.text_channels if guild.me.permissions_in(c).send_messages), None)
            if ch:
                try:
                    await ch.send(content="@everyone" if urgent and ch.permissions_for(guild.me).mention_everyone else None, embed=embed)
                    sent += 1
                except Exception: failed += 1
            await asyncio.sleep(0.3)
        try:
            await interaction.followup.send(embed=success_embed("Broadcast Complete", f"✅ {sent} delivered  ·  ❌ {failed} failed"), ephemeral=True)
        except Exception: pass

    # ── 8. DM User ────────────────────────────────────────────────────────────
    @app_commands.command(name="dm-user", description="DM any user via their ID.")
    @app_commands.describe(user_id="User ID to DM", message="Message to send")
    @is_management()
    async def dm_user(self, interaction: discord.Interaction, user_id: str, message: str):
        try:
            user = await self.bot.fetch_user(int(user_id))
            await user.send(embed=discord.Embed(description=message, color=0x00D4FF, timestamp=discord.utils.utcnow()).set_footer(text="Message from XERO Team Flame"))
            await interaction.response.send_message(embed=success_embed("DM Sent", f"Message sent to **{user}**."), ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(embed=error_embed("Failed", str(e)), ephemeral=True)

    # ── 9. Presence ───────────────────────────────────────────────────────────
    @app_commands.command(name="presence", description="Change the bot's status/activity.")
    @app_commands.describe(text="Status text", activity_type="Activity type")
    @app_commands.choices(activity_type=[
        app_commands.Choice(name="Watching",  value="watching"),
        app_commands.Choice(name="Playing",   value="playing"),
        app_commands.Choice(name="Listening", value="listening"),
        app_commands.Choice(name="Competing", value="competing"),
    ])
    @is_management()
    async def presence(self, interaction: discord.Interaction, text: str, activity_type: str = "watching"):
        types = {"watching": discord.ActivityType.watching, "playing": discord.ActivityType.playing,
                 "listening": discord.ActivityType.listening, "competing": discord.ActivityType.competing}
        await self.bot.change_presence(activity=discord.Activity(type=types[activity_type], name=text))
        await interaction.response.send_message(embed=success_embed("Presence Updated", f"{activity_type.title()} **{text}**"), ephemeral=True)

    # ── 10. Guild Info ────────────────────────────────────────────────────────
    @app_commands.command(name="guild-info", description="Detailed info about a server by ID.")
    @app_commands.describe(guild_id="Server ID")
    @is_management()
    async def guild_info(self, interaction: discord.Interaction, guild_id: str):
        guild = self.bot.get_guild(int(guild_id))
        if not guild:
            return await interaction.response.send_message(embed=error_embed("Not Found", f"Bot is not in guild `{guild_id}`."), ephemeral=True)
        e = discord.Embed(title=guild.name, color=0x00D4FF, timestamp=discord.utils.utcnow())
        e.set_thumbnail(url=guild.icon.url if guild.icon else discord.Embed.Empty)
        e.add_field(name="ID",          value=f"`{guild.id}`",           inline=True)
        e.add_field(name="Owner",       value=f"<@{guild.owner_id}>",   inline=True)
        e.add_field(name="Members",     value=f"`{guild.member_count:,}`", inline=True)
        e.add_field(name="Channels",    value=f"`{len(guild.channels)}`", inline=True)
        e.add_field(name="Roles",       value=f"`{len(guild.roles)}`",   inline=True)
        e.add_field(name="Boost Tier",  value=f"`{guild.premium_tier}`", inline=True)
        e.add_field(name="Created",     value=f"<t:{int(guild.created_at.timestamp())}:R>", inline=True)
        e.add_field(name="Bot Joined",  value=f"<t:{int(guild.me.joined_at.timestamp())}:R>" if guild.me.joined_at else "Unknown", inline=True)
        await interaction.response.send_message(embed=e, ephemeral=True)

    # ── 11. Blacklist ─────────────────────────────────────────────────────────
    @app_commands.command(name="blacklist", description="Blacklist a user globally (bot ignores all their commands).")
    @app_commands.describe(user_id="User ID to blacklist", reason="Reason")
    @is_management()
    async def blacklist(self, interaction: discord.Interaction, user_id: str, reason: str = "No reason"):
        try:
            async with self.bot.db._db_context() as db:
                await db.execute(
                    "INSERT OR IGNORE INTO blacklisted_users (user_id, reason) VALUES (?,?)",
                    (int(user_id), reason)
                )
                await db.commit()
            await interaction.response.send_message(embed=success_embed("Blacklisted", f"User `{user_id}` blacklisted.\n**Reason:** {reason}"), ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(embed=error_embed("Failed", str(e)), ephemeral=True)

    # ── 12. Unblacklist ───────────────────────────────────────────────────────
    @app_commands.command(name="unblacklist", description="Remove a user from the global blacklist.")
    @app_commands.describe(user_id="User ID to remove")
    @is_management()
    async def unblacklist(self, interaction: discord.Interaction, user_id: str):
        async with self.bot.db._db_context() as db:
            await db.execute("DELETE FROM blacklisted_users WHERE user_id=?", (int(user_id),))
            await db.commit()
        await interaction.response.send_message(embed=success_embed("Unblacklisted", f"User `{user_id}` removed from blacklist."), ephemeral=True)

    # ── 13. Leave Guild ───────────────────────────────────────────────────────
    @app_commands.command(name="leave-guild", description="Force the bot to leave a server.")
    @app_commands.describe(guild_id="Server ID to leave")
    @is_management()
    async def leave_guild(self, interaction: discord.Interaction, guild_id: str):
        guild = self.bot.get_guild(int(guild_id))
        if not guild:
            return await interaction.response.send_message(embed=error_embed("Not Found", "Bot is not in that server."), ephemeral=True)
        await guild.leave()
        await interaction.response.send_message(embed=success_embed("Left Guild", f"Left **{guild.name}**."), ephemeral=True)

    # ── 14. Backup Now ────────────────────────────────────────────────────────
    @app_commands.command(name="backup-now", description="Force an immediate DB backup to the backup channel.")
    @is_management()
    @command_guard
    async def backup_now(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=discord.Embed(description="💾 Creating backup...", color=0x00D4FF), ephemeral=True)
        try:
            from utils.db_backup import backup_now as _backup
            ok = await _backup(self.bot)
        except Exception: ok = False
        await interaction.followup.send(
            embed=success_embed("Backup Saved", "DB backed up to backup channel.") if ok
            else error_embed("Backup Failed", "Check BACKUP_CHANNEL_ID env var and bot permissions."),
            ephemeral=True
        )

    # ── 15. Restore Backup ────────────────────────────────────────────────────
    @app_commands.command(name="restore-backup", description="Restore DB from latest backup in backup channel.")
    @is_management()
    @command_guard
    async def restore_backup(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=discord.Embed(description="🔄 Scanning backup channel...", color=0x00D4FF), ephemeral=True)
        try:
            from utils.db_backup import restore_latest
            ok = await restore_latest(self.bot)
        except Exception: ok = False
        await interaction.followup.send(
            embed=success_embed("Restored", "DB restored from latest backup.") if ok
            else error_embed("No Backup Found", "No valid backup found in backup channel."),
            ephemeral=True
        )

    # ── 16. Set Avatar ────────────────────────────────────────────────────────
    @app_commands.command(name="set-avatar", description="Change the bot's avatar.")
    @app_commands.describe(url="Direct image URL")
    @is_management()
    async def set_avatar(self, interaction: discord.Interaction, url: str):
        try:
            import aiohttp
            async with aiohttp.ClientSession() as s:
                async with s.get(url) as r:
                    data = await r.read()
            await self.bot.user.edit(avatar=data)
            await interaction.response.send_message(embed=success_embed("Avatar Updated", "Bot avatar changed."), ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(embed=error_embed("Failed", str(e)[:200]), ephemeral=True)

    # ── 17. Bot Info ──────────────────────────────────────────────────────────
    @app_commands.command(name="bot-info", description="Technical info about this XERO instance.")
    @is_management()
    async def bot_info(self, interaction: discord.Interaction):
        uptime = time.time() - self.bot.launch_time
        h, rem = divmod(int(uptime), 3600); m_, s_ = divmod(rem, 60)
        e = discord.Embed(title="🤖  XERO Bot Info", color=0x00D4FF, timestamp=discord.utils.utcnow())
        e.add_field(name="Bot ID",        value=f"`{self.bot.user.id}`",              inline=True)
        e.add_field(name="Username",      value=f"`{self.bot.user}`",                 inline=True)
        e.add_field(name="Uptime",        value=f"`{h}h {m_}m {s_}s`",               inline=True)
        e.add_field(name="Ping",          value=f"`{round(self.bot.latency*1000)}ms`", inline=True)
        e.add_field(name="Guilds",        value=f"`{len(self.bot.guilds)}`",          inline=True)
        e.add_field(name="Cogs Loaded",   value=f"`{len(self.bot.extensions)}`",      inline=True)
        e.add_field(name="Mgmt Guild",    value=f"`{self.bot.MANAGEMENT_GUILD_ID}`",  inline=True)
        e.add_field(name="Backup Ch",     value=f"`{os.getenv('BACKUP_CHANNEL_ID','Not set')}`", inline=True)
        e.add_field(name="discord.py",    value=f"`{discord.__version__}`",           inline=True)
        e.set_thumbnail(url=self.bot.user.display_avatar.url)
        await interaction.response.send_message(embed=e, ephemeral=True)

    # ── 18. List Guilds ───────────────────────────────────────────────────────
    @app_commands.command(name="guilds", description="List all servers the bot is in.")
    @is_management()
    async def guilds(self, interaction: discord.Interaction):
        guilds = sorted(self.bot.guilds, key=lambda g: g.member_count, reverse=True)
        lines = [f"`{g.id}` **{g.name}** — {g.member_count:,} members" for g in guilds[:20]]
        e = discord.Embed(title=f"🌐  Guilds ({len(self.bot.guilds)})", description="\n".join(lines), color=0x00D4FF, timestamp=discord.utils.utcnow())
        if len(guilds) > 20:
            e.set_footer(text=f"Showing top 20 of {len(guilds)}")
        await interaction.response.send_message(embed=e, ephemeral=True)

    # ── 19. Global Config ─────────────────────────────────────────────────────
    @app_commands.command(name="global-config", description="Apply a setting to ALL servers in the DB.")
    @app_commands.describe(key="Setting key (must match a column in guild_settings)", value="Value to set")
    @is_management()
    @command_guard
    async def global_config(self, interaction: discord.Interaction, key: str, value: str):
        await interaction.response.defer(ephemeral=True)
        try:
            async with self.bot.db._db_context() as db:
                async with db.execute("PRAGMA table_info(guild_settings)") as cur:
                    cols = [r[1] for r in await cur.fetchall()]
            if key not in cols:
                return await interaction.followup.send(embed=error_embed("Invalid Key", f"`{key}` is not a column in guild_settings.\nValid columns: {', '.join(cols[:15])}"), ephemeral=True)
            final = 1 if value.lower() in ("true","on","yes","1") else 0 if value.lower() in ("false","off","no","0") else (int(value) if value.isdigit() else value)
            async with self.bot.db._db_context() as db:
                await db.execute(f"UPDATE guild_settings SET {key}=?", (final,))
                await db.commit()
            await interaction.followup.send(embed=success_embed("Global Config", f"Set `{key}` = `{final}` for all servers."), ephemeral=True)
        except Exception as e:
            await interaction.followup.send(embed=error_embed("Error", str(e)[:300]), ephemeral=True)

    # ── 20. Announce ──────────────────────────────────────────────────────────
    @app_commands.command(name="announce", description="Post to #announcements channels across all servers.")
    @app_commands.describe(title="Title", body="Body text", ping="Ping @everyone where allowed")
    @is_management()
    @command_guard
    async def announce(self, interaction: discord.Interaction, title: str, body: str, ping: bool = False):
        await interaction.response.send_message(embed=discord.Embed(description=f"📣 Announcing to **{len(self.bot.guilds)}** servers...", color=0x00D4FF), ephemeral=True)
        embed = discord.Embed(title=f"📣  {title}", description=body, color=0x00D4FF, timestamp=discord.utils.utcnow())
        embed.set_author(name="XERO — Announcement", icon_url=self.bot.user.display_avatar.url)
        embed.set_footer(text="XERO Bot  ·  Team Flame")
        sent = failed = 0
        for guild in self.bot.guilds:
            ch = next((c for c in guild.text_channels if "announce" in c.name.lower() and guild.me.permissions_in(c).send_messages), None) or guild.system_channel
            if ch:
                try:
                    await ch.send(content="@everyone" if ping and ch.permissions_for(guild.me).mention_everyone else None, embed=embed)
                    sent += 1
                except Exception: failed += 1
            await asyncio.sleep(0.2)
        try:
            await interaction.followup.send(embed=success_embed("Announce Complete", f"✅ {sent} delivered  ·  ❌ {failed} failed"), ephemeral=True)
        except Exception: pass


async def setup(bot):
    mguild = discord.Object(id=bot.MANAGEMENT_GUILD_ID)
    await bot.add_cog(CoreAdmin(bot), guilds=[mguild])
    logger.info(f"✓ /core (20 commands) bound to management guild {bot.MANAGEMENT_GUILD_ID}")
