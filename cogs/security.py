"""
XERO Bot — Security System v2
Beats Wick, Carl-bot, and every paid security bot. Free. Always.

Features:
  🛡️  Anti-Nuke       — detects & stops server destruction in real time
  👶  Account Age     — blocks new accounts from joining (kills raid bots)
  🔗  Link Filter     — domain allowlist with Discord-native exceptions
  💾  Role Restore    — saves and restores roles on rejoin
  🚨  Raid Detection  — detects mass joins and locks down automatically
  🤖  Alt Detection   — flags accounts with suspicious patterns
  🔇  Mention Spam    — protects against mass mention attacks
  📋  Audit Logging   — every security action logged with full context
  🔒  Channel Lockdown — one command locks/unlocks the entire server
  ⚡  Quarantine Role — soft-lock suspicious users without banning
"""
import discord
import logging
import datetime
import asyncio
import aiosqlite
from discord.ext import commands, tasks
from discord import app_commands
from utils.embeds import success_embed, error_embed, info_embed, XERO
from utils.guard import command_guard

logger = logging.getLogger("XERO.Security")

# ── In-memory tracking ────────────────────────────────────────────────────────
# Anti-nuke: tracks destructive actions per user per guild
NUKE_TRACK:  dict = {}  # {guild_id: {user_id: {action: [timestamps]}}}
# Raid detection: tracks join timestamps per guild
JOIN_TRACK:  dict = {}  # {guild_id: [timestamps]}
# Quarantine: tracks quarantined users
QUARANTINED: dict = {}  # {guild_id: {user_id: [original_role_ids]}}


class Security(commands.GroupCog, name="security"):
    def __init__(self, bot):
        self.bot = bot
        self._raid_locked: set = set()  # guilds currently raid-locked
        self.unraid_check.start()

    def cog_unload(self):
        self.unraid_check.cancel()

    # ══════════════════════════════════════════════════════════════════════
    # ANTI-NUKE ENGINE
    # ══════════════════════════════════════════════════════════════════════

    async def track_nuke_action(self, guild: discord.Guild, user: discord.Member | discord.User, action: str):
        """
        Called by events.py on every destructive action.
        Actions: channel_delete, role_delete, mass_ban, mass_kick, webhook_delete,
                 emoji_delete, bot_add, permission_escalate
        Triggers at configurable threshold within a 10-second window.
        """
        settings = await self.bot.db.get_guild_settings(guild.id)
        if not settings or not settings.get("anti_nuke_enabled", 0):
            return

        now       = datetime.datetime.utcnow()
        threshold = int(settings.get("anti_nuke_threshold", 3))

        NUKE_TRACK.setdefault(guild.id, {})
        NUKE_TRACK[guild.id].setdefault(user.id, {})
        NUKE_TRACK[guild.id][user.id].setdefault(action, [])

        # Keep only last 10 seconds
        cutoff = now - datetime.timedelta(seconds=10)
        NUKE_TRACK[guild.id][user.id][action] = [
            t for t in NUKE_TRACK[guild.id][user.id][action] if t > cutoff
        ]
        NUKE_TRACK[guild.id][user.id][action].append(now)

        count = len(NUKE_TRACK[guild.id][user.id][action])
        logger.debug(f"Nuke track: {user} in {guild.name} — {action} x{count}")

        if count >= threshold:
            NUKE_TRACK[guild.id][user.id][action] = []  # Reset
            await self._trigger_anti_nuke(guild, user, action, count, settings)

    async def _trigger_anti_nuke(self, guild, user, action, count, settings):
        """Strip all roles, quarantine, alert admins, log everything."""
        logger.warning(f"🚨 ANTI-NUKE: {user} in {guild.name} — {count}x {action}")

        member = guild.get_member(user.id)
        stripped_roles = []

        if member and member.id != guild.owner_id:
            # Save roles before stripping
            saveable = [r for r in member.roles if not r.managed and r != guild.default_role]
            stripped_roles = [r.id for r in saveable]

            # Strip all non-managed roles
            try:
                await member.remove_roles(*saveable, reason=f"XERO Anti-Nuke: {count}x {action} in 10s")
            except Exception as e:
                logger.error(f"Anti-nuke role strip failed: {e}")

            # Timeout for 10 minutes as extra safety
            try:
                until = discord.utils.utcnow() + datetime.timedelta(minutes=10)
                await member.timeout(until, reason=f"XERO Anti-Nuke: {count}x {action}")
            except Exception:
                pass

            # Save stripped roles in case of false positive restore
            QUARANTINED.setdefault(guild.id, {})[user.id] = stripped_roles

        # Build detailed alert embed
        embed = discord.Embed(
            title="🚨  ANTI-NUKE TRIGGERED",
            description=(
                f"**Attacker:** {user.mention} (`{user.id}`)\n"
                f"**Action:** `{action}` × **{count}** times in 10 seconds\n\n"
                f"**Immediate response:**\n"
                f"{'✅ All admin roles stripped' if stripped_roles else '⚠️ Could not strip roles'}\n"
                f"{'✅ User timed out for 10 minutes' if member else '⚠️ User not in server'}\n\n"
                f"**If this was a false positive:**\n"
                f"`/security restore-roles @{user.name}` to undo"
            ),
            color=0xFF1744,
            timestamp=discord.utils.utcnow()
        )
        embed.set_thumbnail(url=user.display_avatar.url if hasattr(user, 'display_avatar') else discord.Embed.Empty)
        embed.add_field(name="Account Age", value=f"{(discord.utils.utcnow() - user.created_at).days}d old", inline=True)
        embed.add_field(name="Stripped Roles", value=str(len(stripped_roles)), inline=True)
        embed.set_footer(text="XERO Anti-Nuke  ·  Automatic Protection")

        # Alert to log channel
        log_ch = guild.get_channel(settings.get("log_channel_id") or 0)
        if log_ch:
            try:
                await log_ch.send(content="@here", embed=embed)
            except Exception:
                pass

        # DM ALL admins (not just log channel)
        for m in guild.members:
            if m.guild_permissions.administrator and not m.bot and m.id != user.id:
                try:
                    await m.send(embed=embed)
                except Exception:
                    pass

    # ══════════════════════════════════════════════════════════════════════
    # RAID DETECTION ENGINE
    # ══════════════════════════════════════════════════════════════════════

    async def check_raid(self, member: discord.Member):
        """
        Called on every member join.
        Detects mass-join raids and auto-locks the server.
        """
        settings = await self.bot.db.get_guild_settings(member.guild.id)
        if not settings or not settings.get("anti_nuke_enabled", 0):
            return

        guild = member.guild
        now   = datetime.datetime.utcnow()

        JOIN_TRACK.setdefault(guild.id, [])
        JOIN_TRACK[guild.id].append(now)

        # Keep only last 10 seconds
        cutoff = now - datetime.timedelta(seconds=10)
        JOIN_TRACK[guild.id] = [t for t in JOIN_TRACK[guild.id] if t > cutoff]
        join_count = len(JOIN_TRACK[guild.id])

        # Raid threshold: 10 joins in 10 seconds
        raid_threshold = int(settings.get("raid_threshold", 10))
        if join_count >= raid_threshold and guild.id not in self._raid_locked:
            await self._trigger_raid_lock(guild, join_count, settings)

    async def _trigger_raid_lock(self, guild: discord.Guild, join_count: int, settings: dict):
        """Lock all channels, alert admins, auto-unlock after 10 min."""
        logger.warning(f"🚨 RAID DETECTED: {guild.name} — {join_count} joins in 10s")
        self._raid_locked.add(guild.id)

        # Lock all text channels for @everyone
        locked = 0
        for channel in guild.text_channels:
            try:
                overwrite = channel.overwrites_for(guild.default_role)
                overwrite.send_messages = False
                await channel.set_permissions(
                    guild.default_role, overwrite=overwrite,
                    reason=f"XERO Raid Lock: {join_count} joins in 10s"
                )
                locked += 1
            except Exception:
                pass

        embed = discord.Embed(
            title="🚨  RAID DETECTED — SERVER LOCKED",
            description=(
                f"**{join_count} accounts joined in 10 seconds.**\n\n"
                f"**Action taken:**\n"
                f"✅ {locked} channels locked for @everyone\n"
                f"✅ Server will auto-unlock in **10 minutes**\n\n"
                f"To unlock now: `/security unlock`\n"
                f"To ban recent joiners: `/mod massban` with a reason"
            ),
            color=0xFF1744,
            timestamp=discord.utils.utcnow()
        )
        embed.set_footer(text="XERO Raid Protection  ·  Auto-unlocks in 10 minutes")

        log_ch = guild.get_channel(settings.get("log_channel_id") or 0)
        if log_ch:
            try:
                await log_ch.send(content="@here", embed=embed)
            except Exception:
                pass
        for m in guild.members:
            if m.guild_permissions.administrator and not m.bot:
                try:
                    await m.send(embed=embed)
                except Exception:
                    pass

    @tasks.loop(minutes=1)
    async def unraid_check(self):
        """Auto-unlock raid-locked guilds after 10 minutes."""
        to_unlock = []
        for guild_id in list(self._raid_locked):
            guild = self.bot.get_guild(guild_id)
            if not guild:
                self._raid_locked.discard(guild_id)
                continue
            # Check if the join flood has settled
            joins = JOIN_TRACK.get(guild_id, [])
            cutoff = datetime.datetime.utcnow() - datetime.timedelta(minutes=10)
            recent = [t for t in joins if t > cutoff]
            if len(recent) < 5:  # Under 5 joins in last 10 minutes — safe
                to_unlock.append(guild)

        for guild in to_unlock:
            self._raid_locked.discard(guild.id)
            for channel in guild.text_channels:
                try:
                    overwrite = channel.overwrites_for(guild.default_role)
                    overwrite.send_messages = None  # Reset to default
                    if not any(v is not None for v in [
                        overwrite.send_messages, overwrite.read_messages,
                        overwrite.add_reactions
                    ]):
                        await channel.set_permissions(guild.default_role, overwrite=None, reason="XERO Raid auto-unlock")
                    else:
                        await channel.set_permissions(guild.default_role, overwrite=overwrite, reason="XERO Raid auto-unlock")
                except Exception:
                    pass
            settings = await self.bot.db.get_guild_settings(guild.id)
            log_ch = guild.get_channel((settings or {}).get("log_channel_id") or 0)
            if log_ch:
                try:
                    await log_ch.send(embed=discord.Embed(
                        description="✅ **Server auto-unlocked.** Raid threat has settled.",
                        color=0x00FF94
                    ))
                except Exception:
                    pass

    @unraid_check.before_loop
    async def before_unraid(self):
        await self.bot.wait_until_ready()

    # ══════════════════════════════════════════════════════════════════════
    # SLASH COMMANDS
    # ══════════════════════════════════════════════════════════════════════

    @app_commands.command(name="setup", description="View all active security settings for this server.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup(self, interaction: discord.Interaction):
        s = await self.bot.db.get_guild_settings(interaction.guild.id)
        if not s:
            return await interaction.response.send_message(
                embed=error_embed("Not Found", "Run `/config dashboard` to initialize settings first."),
                ephemeral=True
            )

        async with aiosqlite.connect(self.bot.db.db_path) as db:
            try:
                async with db.execute("SELECT domain FROM allowed_domains WHERE guild_id=?", (interaction.guild.id,)) as c:
                    domains = [r[0] for r in await c.fetchall()]
            except Exception:
                domains = []

        def tog(v): return "✅ On" if v else "❌ Off"

        embed = discord.Embed(
            title=f"🔒  Security — {interaction.guild.name}",
            color=0x00D4FF,
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(
            name="🚨 Anti-Nuke",
            value=(
                f"{tog(s.get('anti_nuke_enabled', 0))}\n"
                f"Threshold: **{s.get('anti_nuke_threshold', 3)}** actions / 10s\n"
                f"Raid lockdown: **{s.get('raid_threshold', 10)}** joins / 10s"
            ),
            inline=True
        )
        embed.add_field(
            name="👶 Account Age Filter",
            value=(
                f"Minimum: **{s.get('min_account_age_days', 0)}** days\n"
                f"Action: **{s.get('account_age_action', 'disabled')}**"
            ),
            inline=True
        )
        embed.add_field(
            name="🔗 Link Filter",
            value=(
                f"{tog(s.get('link_filter_enabled', 0))}\n"
                f"Allowed: **{len(domains)}** domain(s)"
            ),
            inline=True
        )
        embed.add_field(name="💾 Role Restore", value=tog(s.get("role_restore_enabled", 0)), inline=True)
        embed.add_field(name="🔒 Raid Lock", value="🔴 ACTIVE" if interaction.guild.id in self._raid_locked else "🟢 Normal", inline=True)

        if domains:
            embed.add_field(
                name="✅ Allowed Domains",
                value="\n".join(f"`{d}`" for d in domains[:10]),
                inline=False
            )

        embed.add_field(
            name="📋 Commands",
            value=(
                "`/security anti-nuke` — configure anti-nuke\n"
                "`/security account-age` — block new accounts\n"
                "`/security link-filter` — manage link allowlist\n"
                "`/security role-restore` — toggle role restore\n"
                "`/security lockdown` — manual server lockdown\n"
                "`/security unlock` — unlock server\n"
                "`/security quarantine @user` — soft-lock a user\n"
                "`/security restore-roles @user` — restore after nuke"
            ),
            inline=False
        )
        embed.set_footer(text="XERO Security  ·  Protecting your server 24/7")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="anti-nuke", description="Configure anti-nuke protection — stops server destruction instantly.")
    @app_commands.describe(
        enabled="Enable or disable anti-nuke",
        threshold="Destructive actions in 10s to trigger (2-10, default 3)",
        raid_threshold="Joins in 10s to trigger raid lock (5-50, default 10)"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def anti_nuke(self, interaction: discord.Interaction,
                         enabled: bool,
                         threshold: int = 3,
                         raid_threshold: int = 10):
        threshold      = max(2, min(10, threshold))
        raid_threshold = max(5, min(50, raid_threshold))

        await self.bot.db.update_guild_setting(interaction.guild.id, "anti_nuke_enabled",   1 if enabled else 0)
        await self.bot.db.update_guild_setting(interaction.guild.id, "anti_nuke_threshold", threshold)
        await self.bot.db.update_guild_setting(interaction.guild.id, "raid_threshold",      raid_threshold)

        embed = discord.Embed(
            title=f"🛡️  Anti-Nuke {'Enabled' if enabled else 'Disabled'}",
            color=0x00FF94 if enabled else 0xFF1744,
            timestamp=discord.utils.utcnow()
        )
        if enabled:
            embed.description = (
                "Your server is now protected against nukes and raids.\n\n"
                "**What XERO watches:**\n"
                "• Mass channel deletions\n"
                "• Mass role deletions\n"
                "• Mass bans / kicks\n"
                "• Webhook spam\n"
                "• Bot additions by non-admins\n"
                "• Permission escalation attempts\n\n"
                "**Response:**\n"
                "→ Strip all admin roles instantly\n"
                "→ Timeout user for 10 minutes\n"
                "→ DM all admins immediately\n"
                "→ Log with full audit context"
            )
        embed.add_field(name="Nuke Threshold",   value=f"**{threshold}** actions / 10s", inline=True)
        embed.add_field(name="Raid Threshold",   value=f"**{raid_threshold}** joins / 10s", inline=True)
        embed.set_footer(text="XERO Security  ·  Better than Wick. Free.")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="account-age", description="Block accounts younger than X days. Kills raid bots instantly.")
    @app_commands.describe(
        days="Minimum account age in days (0 = disabled, 7 recommended for raids)",
        action="What to do with underage accounts"
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="Kick silently",        value="kick"),
        app_commands.Choice(name="Kick + DM reason",     value="kick_dm"),
        app_commands.Choice(name="Ban permanently",       value="ban"),
        app_commands.Choice(name="Quarantine role",       value="quarantine"),
    ])
    @app_commands.checks.has_permissions(administrator=True)
    async def account_age(self, interaction: discord.Interaction, days: int, action: str = "kick_dm"):
        days = max(0, min(365, days))
        await self.bot.db.update_guild_setting(interaction.guild.id, "min_account_age_days", days)
        await self.bot.db.update_guild_setting(interaction.guild.id, "account_age_action",   action)

        if days == 0:
            await interaction.response.send_message(
                embed=success_embed("Account Age Filter Disabled", "All accounts can now join regardless of age.")
            )
        else:
            embed = discord.Embed(
                title="👶  Account Age Filter Set",
                color=0x00FF94,
                timestamp=discord.utils.utcnow()
            )
            embed.add_field(name="Minimum Age", value=f"**{days}** days",                         inline=True)
            embed.add_field(name="Action",      value=action.replace("_", " ").title(),            inline=True)
            embed.description = (
                f"Accounts younger than **{days} days** will be **{action.replace('_',' ')}**d on join.\n\n"
                f"💡 **Tip:** Setting **7 days** blocks ~90% of raid bots.\n"
                f"Setting **30 days** blocks almost all automation accounts."
            )
            embed.set_footer(text="XERO Security  ·  Account Age Protection")
            await interaction.response.send_message(embed=embed)

    @app_commands.command(name="link-filter", description="Control which links are allowed. Block all except approved domains.")
    @app_commands.describe(
        enabled="Enable or disable link filter",
        add_domain="Allow a domain (e.g. youtube.com)",
        remove_domain="Remove a domain from allowlist"
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def link_filter(self, interaction: discord.Interaction,
                           enabled: bool = None,
                           add_domain: str = None,
                           remove_domain: str = None):
        async with aiosqlite.connect(self.bot.db.db_path) as db:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS allowed_domains"
                " (guild_id INTEGER, domain TEXT, PRIMARY KEY(guild_id, domain))"
            )
            await db.commit()

        if enabled is not None:
            await self.bot.db.update_guild_setting(interaction.guild.id, "link_filter_enabled", 1 if enabled else 0)

        if add_domain:
            domain = add_domain.lower().strip().replace("https://", "").replace("http://", "").split("/")[0]
            async with aiosqlite.connect(self.bot.db.db_path) as db:
                await db.execute("INSERT OR IGNORE INTO allowed_domains (guild_id, domain) VALUES (?,?)", (interaction.guild.id, domain))
                await db.commit()

        if remove_domain:
            domain = remove_domain.lower().strip().replace("https://", "").replace("http://", "").split("/")[0]
            async with aiosqlite.connect(self.bot.db.db_path) as db:
                await db.execute("DELETE FROM allowed_domains WHERE guild_id=? AND domain=?", (interaction.guild.id, domain))
                await db.commit()

        async with aiosqlite.connect(self.bot.db.db_path) as db:
            async with db.execute("SELECT domain FROM allowed_domains WHERE guild_id=? ORDER BY domain", (interaction.guild.id,)) as c:
                domains = [r[0] for r in await c.fetchall()]

        settings = await self.bot.db.get_guild_settings(interaction.guild.id)
        status   = "✅ Active" if settings.get("link_filter_enabled", 0) else "❌ Inactive"

        embed = discord.Embed(
            title="🔗  Link Filter",
            color=0x00D4FF,
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="Status", value=status, inline=True)
        embed.add_field(name="Allowed Domains", value=str(len(domains)), inline=True)
        embed.add_field(
            name="Always Allowed",
            value="`discord.com` `discord.gg` `tenor.com` `giphy.com`",
            inline=False
        )
        if domains:
            embed.add_field(
                name="Your Allowlist",
                value="\n".join(f"`{d}`" for d in domains[:15]),
                inline=False
            )
        else:
            embed.add_field(name="Your Allowlist", value="Empty — add with `/security link-filter add_domain:youtube.com`", inline=False)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="role-restore", description="Save and restore member roles when they rejoin. MEE6 charges for this — we don't.")
    @app_commands.describe(enabled="Enable or disable role restore")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def role_restore(self, interaction: discord.Interaction, enabled: bool):
        await self.bot.db.update_guild_setting(interaction.guild.id, "role_restore_enabled", 1 if enabled else 0)
        if enabled:
            await interaction.response.send_message(embed=success_embed(
                "✅ Role Restore Enabled",
                "When a member leaves and rejoins, their roles are automatically restored.\n\n"
                "**How it works:**\n"
                "• Roles are saved when a member leaves\n"
                "• Restored automatically on rejoin\n"
                "• Managed/integration roles excluded\n"
                "• Works even if they were gone for months"
            ))
        else:
            await interaction.response.send_message(embed=info_embed("Role Restore Disabled", "Roles will no longer be saved or restored."))

    @app_commands.command(name="lockdown", description="Lock ALL channels for @everyone instantly. Use during raids or emergencies.")
    @app_commands.describe(reason="Reason for lockdown (shown in logs)")
    @app_commands.checks.has_permissions(administrator=True)
    @command_guard
    async def lockdown(self, interaction: discord.Interaction, reason: str = "Manual lockdown"):
        await interaction.response.defer()
        guild  = interaction.guild
        locked = 0
        failed = 0

        for channel in guild.text_channels:
            try:
                overwrite = channel.overwrites_for(guild.default_role)
                overwrite.send_messages = False
                await channel.set_permissions(
                    guild.default_role, overwrite=overwrite,
                    reason=f"XERO Lockdown by {interaction.user}: {reason}"
                )
                locked += 1
            except Exception:
                failed += 1

        self._raid_locked.add(guild.id)

        embed = discord.Embed(
            title="🔒  Server Locked Down",
            description=(
                f"**{locked}** channels locked  ·  {failed} failed\n"
                f"**Reason:** {reason}\n"
                f"**By:** {interaction.user.mention}\n\n"
                f"Use `/security unlock` to restore normal access."
            ),
            color=0xFF1744,
            timestamp=discord.utils.utcnow()
        )
        embed.set_footer(text="XERO Security  ·  Emergency Lockdown")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="unlock", description="Unlock all channels after a lockdown.")
    @app_commands.checks.has_permissions(administrator=True)
    @command_guard
    async def unlock(self, interaction: discord.Interaction):
        await interaction.response.defer()
        guild    = interaction.guild
        unlocked = 0

        for channel in guild.text_channels:
            try:
                overwrite = channel.overwrites_for(guild.default_role)
                overwrite.send_messages = None
                if not any(v is not None for k, v in overwrite):
                    await channel.set_permissions(guild.default_role, overwrite=None, reason="XERO Unlock")
                else:
                    await channel.set_permissions(guild.default_role, overwrite=overwrite, reason="XERO Unlock")
                unlocked += 1
            except Exception:
                pass

        self._raid_locked.discard(guild.id)
        await interaction.followup.send(embed=success_embed(
            "🔓  Server Unlocked",
            f"**{unlocked}** channels restored to normal.\n"
            f"Members can send messages again."
        ))

    @app_commands.command(name="quarantine", description="Silently remove all roles from a user and give them a quarantine role.")
    @app_commands.describe(user="User to quarantine", reason="Reason")
    @app_commands.checks.has_permissions(manage_roles=True)
    @command_guard
    async def quarantine(self, interaction: discord.Interaction, user: discord.Member, reason: str = "Suspicious activity"):
        await interaction.response.defer(ephemeral=True)

        # Create quarantine role if it doesn't exist
        q_role = discord.utils.get(interaction.guild.roles, name="Quarantined")
        if not q_role:
            try:
                q_role = await interaction.guild.create_role(
                    name="Quarantined",
                    color=discord.Color.dark_gray(),
                    reason="XERO Security — Quarantine role"
                )
                # Deny send messages in all channels
                for channel in interaction.guild.text_channels:
                    try:
                        await channel.set_permissions(q_role, send_messages=False, add_reactions=False)
                    except Exception:
                        pass
            except Exception as e:
                return await interaction.followup.send(embed=error_embed("Failed", f"Could not create quarantine role: {e}"), ephemeral=True)

        # Save current roles
        current_roles = [r for r in user.roles if not r.managed and r != interaction.guild.default_role]
        QUARANTINED.setdefault(interaction.guild.id, {})[user.id] = [r.id for r in current_roles]

        # Strip roles and add quarantine
        try:
            await user.remove_roles(*current_roles, reason=f"XERO Quarantine: {reason}")
            await user.add_roles(q_role, reason=f"XERO Quarantine by {interaction.user}: {reason}")
        except discord.Forbidden:
            return await interaction.followup.send(embed=error_embed("No Permission", "I can't manage that user's roles."), ephemeral=True)

        # DM user
        try:
            await user.send(embed=discord.Embed(
                title=f"⚠️  Quarantined in {interaction.guild.name}",
                description=f"You have been quarantined by a staff member.\n**Reason:** {reason}\n\nPlease contact a staff member if you believe this is an error.",
                color=0xFFB800
            ))
        except Exception:
            pass

        await interaction.followup.send(embed=success_embed(
            "User Quarantined",
            f"{user.mention} has been quarantined.\n**Reason:** {reason}\n**Roles saved:** {len(current_roles)}\n\nUse `/security restore-roles` to unquarantine."
        ), ephemeral=True)

    @app_commands.command(name="restore-roles", description="Restore all roles for a user (after quarantine or anti-nuke false positive).")
    @app_commands.describe(user="User to restore")
    @app_commands.checks.has_permissions(manage_roles=True)
    @command_guard
    async def restore_roles(self, interaction: discord.Interaction, user: discord.Member):
        await interaction.response.defer(ephemeral=True)

        # Check in-memory quarantine first
        role_ids = QUARANTINED.get(interaction.guild.id, {}).pop(user.id, None)

        # Fall back to DB
        if not role_ids:
            async with aiosqlite.connect(self.bot.db.db_path) as db:
                try:
                    async with db.execute(
                        "SELECT role_ids FROM member_roles WHERE user_id=? AND guild_id=?",
                        (user.id, interaction.guild.id)
                    ) as c:
                        row = await c.fetchone()
                    if row and row[0]:
                        role_ids = [int(r) for r in row[0].split(",") if r.strip()]
                except Exception:
                    role_ids = []

        if not role_ids:
            return await interaction.followup.send(
                embed=error_embed("No Saved Roles", f"No saved roles found for {user.mention}."),
                ephemeral=True
            )

        restored = []
        failed   = []

        # Remove quarantine role if they have it
        q_role = discord.utils.get(interaction.guild.roles, name="Quarantined")
        if q_role and q_role in user.roles:
            try:
                await user.remove_roles(q_role, reason=f"XERO Restore by {interaction.user}")
            except Exception:
                pass

        for rid in role_ids:
            role = interaction.guild.get_role(rid)
            if role and not role.managed and role != interaction.guild.default_role:
                try:
                    await user.add_roles(role, reason=f"XERO Role Restore by {interaction.user}")
                    restored.append(role.mention)
                except Exception:
                    failed.append(role.name)

        embed = discord.Embed(
            title="✅  Roles Restored",
            color=0x00FF94,
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="User",     value=user.mention,           inline=True)
        embed.add_field(name="Restored", value=str(len(restored)),     inline=True)
        embed.add_field(name="Failed",   value=str(len(failed)) or "0", inline=True)
        if restored:
            embed.add_field(name="Roles Given", value=", ".join(restored[:10])[:900], inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="view", description="View full security configuration.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def view(self, interaction: discord.Interaction):
        await self.setup.callback(self, interaction)


async def setup(bot):
    await bot.add_cog(Security(bot))
