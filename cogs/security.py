"""
XERO Bot — Security System
Anti-nuke, account age filter, link domain allowlist, role restore on rejoin.
Better than Carl-bot's paid tier — free, AI-enhanced, automatic.
"""
import discord
from discord.ext import commands
from discord import app_commands
import logging, datetime, asyncio, aiosqlite
from utils.embeds import success_embed, error_embed, info_embed, warning_embed, comprehensive_embed, XERO, FOOTER_MOD

logger = logging.getLogger("XERO.Security")

# Anti-nuke tracking: guild_id -> {user_id -> {action -> [timestamps]}}
NUKE_TRACK: dict = {}


class Security(commands.GroupCog, name="security"):
    def __init__(self, bot):
        self.bot = bot

    # ── Anti-nuke listener (called from events) ───────────────────────────
    async def check_nuke_action(self, guild: discord.Guild, user: discord.Member, action: str):
        """Track rapid destructive actions. Trigger at 3+ in 10 seconds."""
        settings = await self.bot.db.get_guild_settings(guild.id)
        if not settings.get("anti_nuke_enabled", 0):
            return

        gid = guild.id
        uid = user.id
        now = datetime.datetime.now()

        if gid not in NUKE_TRACK: NUKE_TRACK[gid] = {}
        if uid not in NUKE_TRACK[gid]: NUKE_TRACK[gid][uid] = {}
        if action not in NUKE_TRACK[gid][uid]: NUKE_TRACK[gid][uid][action] = []

        # Add timestamp and prune old ones
        NUKE_TRACK[gid][uid][action].append(now)
        cutoff = now - datetime.timedelta(seconds=10)
        NUKE_TRACK[gid][uid][action] = [t for t in NUKE_TRACK[gid][uid][action] if t > cutoff]

        count = len(NUKE_TRACK[gid][uid][action])
        threshold = settings.get("anti_nuke_threshold", 3)

        if count >= threshold:
            NUKE_TRACK[gid][uid][action] = []  # Reset to avoid repeat triggers
            await self._trigger_anti_nuke(guild, user, action, count, settings)

    async def _trigger_anti_nuke(self, guild, user, action, count, settings):
        """Strip permissions, log, alert admins."""
        logger.warning(f"🚨 ANTI-NUKE: {user} in {guild.name} — {count}x {action}")

        # Strip all dangerous permissions
        try:
            overwrite = discord.PermissionOverwrite(
                manage_channels=False, manage_roles=False, manage_guild=False,
                ban_members=False, kick_members=False, administrator=False
            )
            # Can't remove admin via overwrite — need to remove roles
            safe_roles = [r for r in user.roles if not r.managed and r != guild.default_role]
            if user.id != guild.owner_id:
                try:
                    await user.remove_roles(*safe_roles, reason=f"XERO Anti-Nuke: {count}x {action} in 10s")
                except Exception as e:
                    logger.error(f"Anti-nuke role removal failed: {e}")
        except Exception as e:
            logger.error(f"Anti-nuke action failed: {e}")

        # Build alert embed
        embed = discord.Embed(
            title="🚨  ANTI-NUKE TRIGGERED",
            description=(
                f"**{user.mention}** (`{user.id}`) performed **{count}x `{action}`** in 10 seconds.\n\n"
                f"**Action taken:** All administrative roles removed.\n"
                f"**Verify** this person and restore roles if it was a mistake.\n"
                f"Use `/security restore-roles {user.id}` to restore."
            ),
            color=XERO.DANGER if hasattr(XERO, 'DANGER') else discord.Color.red(),
            timestamp=discord.utils.utcnow()
        )
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.set_footer(text="XERO Anti-Nuke  •  Automatic Protection")

        # Alert log channel
        log_ch = guild.get_channel(settings.get("log_channel_id") or 0)
        if log_ch:
            try: await log_ch.send(content="@here", embed=embed)
            except Exception: pass

        # DM all admins
        for m in guild.members:
            if m.guild_permissions.administrator and not m.bot and m.id != user.id:
                try: await m.send(embed=embed)
                except Exception: pass

    # ── /security setup ───────────────────────────────────────────────────
    @app_commands.command(name="setup", description="Configure all security systems — anti-nuke, account age, link filter.")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup(self, interaction: discord.Interaction):
        settings = await self.bot.db.get_guild_settings(interaction.guild.id)

        def tog(val): return "✅ On" if val else "❌ Off"

        embed = discord.Embed(title="🔒  Security Configuration", color=XERO.PRIMARY)
        embed.add_field(name="🚨 Anti-Nuke",       value=tog(settings.get("anti_nuke_enabled",0)),     inline=True)
        embed.add_field(name="👶 Account Age",      value=f"{settings.get('min_account_age_days',0)}d minimum", inline=True)
        embed.add_field(name="🔗 Link Filter",      value=tog(settings.get("link_filter_enabled",0)),   inline=True)
        embed.add_field(name="💾 Role Restore",     value=tog(settings.get("role_restore_enabled",0)),  inline=True)
        embed.add_field(name="⚡ Nuke Threshold",   value=f"{settings.get('anti_nuke_threshold',3)} actions/10s", inline=True)
        embed.add_field(name="\u200b",              value="\u200b", inline=True)
        embed.add_field(name="📋 Commands", value=(
            "`/security anti-nuke` — toggle + threshold\n"
            "`/security account-age` — min account age\n"
            "`/security link-filter` — manage allowed domains\n"
            "`/security role-restore` — toggle role restore\n"
            "`/security restore-roles` — manually restore user roles"
        ), inline=False)
        embed.set_footer(text="XERO Security  •  Protecting your server 24/7")
        await interaction.response.send_message(embed=embed)

    # ── /security anti-nuke ───────────────────────────────────────────────
    @app_commands.command(name="anti-nuke", description="Enable anti-nuke: auto-strips roles from anyone mass-deleting channels/roles.")
    @app_commands.describe(enabled="Enable or disable", threshold="Actions in 10s to trigger (default 3)")
    @app_commands.checks.has_permissions(administrator=True)
    async def anti_nuke(self, interaction: discord.Interaction, enabled: bool, threshold: int = 3):
        threshold = max(2, min(10, threshold))
        await self.bot.db.update_guild_setting(interaction.guild.id, "anti_nuke_enabled",   1 if enabled else 0)
        await self.bot.db.update_guild_setting(interaction.guild.id, "anti_nuke_threshold", threshold)
        embed = success_embed(
            f"Anti-Nuke {'Enabled' if enabled else 'Disabled'}",
            f"**Status:** {'✅ Active' if enabled else '❌ Inactive'}\n"
            f"**Trigger:** {threshold} destructive actions within 10 seconds\n"
            f"**Actions watched:** channel delete, role delete, ban, kick\n"
            f"**Response:** Instantly strips all admin roles + alerts all admins via DM\n\n"
            f"*This is better than Carl-bot's anti-nuke — free, instant, AI-logged.*"
        )
        embed.set_footer(text="XERO Security  •  Anti-Nuke Protection")
        await interaction.response.send_message(embed=embed)

    # ── /security account-age ─────────────────────────────────────────────
    @app_commands.command(name="account-age", description="Block accounts younger than X days from joining. Kills most bot raids instantly.")
    @app_commands.describe(days="Minimum account age in days (0 = disabled)", action="What to do with underage accounts")
    @app_commands.choices(action=[
        app_commands.Choice(name="Kick silently",    value="kick"),
        app_commands.Choice(name="Kick + DM reason", value="kick_dm"),
        app_commands.Choice(name="Ban",               value="ban"),
        app_commands.Choice(name="Give quarantine role", value="quarantine"),
    ])
    @app_commands.checks.has_permissions(administrator=True)
    async def account_age(self, interaction: discord.Interaction, days: int, action: str = "kick_dm"):
        days = max(0, min(365, days))
        await self.bot.db.update_guild_setting(interaction.guild.id, "min_account_age_days", days)
        await self.bot.db.update_guild_setting(interaction.guild.id, "account_age_action",   action)
        if days == 0:
            await interaction.response.send_message(embed=success_embed("Account Age Filter Disabled", "All accounts can now join regardless of age."))
        else:
            await interaction.response.send_message(embed=success_embed(
                "Account Age Filter Set",
                f"**Minimum age:** {days} day(s)\n"
                f"**Action:** {action.replace('_',' ').title()}\n\n"
                f"Accounts younger than **{days} days** will be automatically {action.replace('_',' ')} on join.\n"
                f"*Most raid bots use accounts under 7 days old. Setting 7+ blocks ~90% of raids.*"
            ))

    # ── /security link-filter ─────────────────────────────────────────────
    @app_commands.command(name="link-filter", description="Control which links are allowed. Block all external links except approved domains.")
    @app_commands.describe(enabled="Enable link filter", add_domain="Add an allowed domain (e.g. youtube.com)", remove_domain="Remove an allowed domain")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def link_filter(self, interaction: discord.Interaction, enabled: bool = None, add_domain: str = None, remove_domain: str = None):
        import aiosqlite
        # Ensure table exists
        async with aiosqlite.connect(self.bot.db.db_path) as db:
            await db.execute("CREATE TABLE IF NOT EXISTS allowed_domains (guild_id INTEGER, domain TEXT, PRIMARY KEY(guild_id, domain))")
            await db.commit()

        if enabled is not None:
            await self.bot.db.update_guild_setting(interaction.guild.id, "link_filter_enabled", 1 if enabled else 0)

        if add_domain:
            domain = add_domain.lower().strip().replace("https://","").replace("http://","").split("/")[0]
            async with aiosqlite.connect(self.bot.db.db_path) as db:
                await db.execute("INSERT OR IGNORE INTO allowed_domains (guild_id, domain) VALUES (?,?)", (interaction.guild.id, domain))
                await db.commit()

        if remove_domain:
            domain = remove_domain.lower().strip().replace("https://","").replace("http://","").split("/")[0]
            async with aiosqlite.connect(self.bot.db.db_path) as db:
                await db.execute("DELETE FROM allowed_domains WHERE guild_id=? AND domain=?", (interaction.guild.id, domain))
                await db.commit()

        # Show current state
        async with aiosqlite.connect(self.bot.db.db_path) as db:
            async with db.execute("SELECT domain FROM allowed_domains WHERE guild_id=? ORDER BY domain", (interaction.guild.id,)) as c:
                domains = [r[0] for r in await c.fetchall()]

        settings = await self.bot.db.get_guild_settings(interaction.guild.id)
        status   = "✅ Active" if settings.get("link_filter_enabled",0) else "❌ Inactive"
        domain_list = "\n".join(f"• `{d}`" for d in domains) if domains else "*None — all links blocked*"

        embed = success_embed("Link Filter Updated",
            f"**Status:** {status}\n\n"
            f"**Allowed Domains ({len(domains)}):**\n{domain_list}\n\n"
            f"*discord.com, tenor.com, and giphy.com are always allowed.*"
        )
        embed.set_footer(text="XERO Security  •  Link Filter")
        await interaction.response.send_message(embed=embed)

    # ── /security role-restore ────────────────────────────────────────────
    @app_commands.command(name="role-restore", description="Automatically restore roles when a member rejoins. Never lose your roles again.")
    @app_commands.describe(enabled="Enable or disable role restore")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def role_restore(self, interaction: discord.Interaction, enabled: bool):
        await self.bot.db.update_guild_setting(interaction.guild.id, "role_restore_enabled", 1 if enabled else 0)
        if enabled:
            await interaction.response.send_message(embed=success_embed(
                "Role Restore Enabled",
                "When a member leaves and rejoins, their roles are automatically restored.\n\n"
                "**How it works:**\n"
                "• Roles are saved when a member leaves\n"
                "• Restored automatically when they rejoin\n"
                "• Managed/integration roles are excluded\n"
                "*MEE6 Pro charges for this. XERO does it free.*"
            ))
        else:
            await interaction.response.send_message(embed=success_embed("Role Restore Disabled", "Roles will no longer be saved or restored."))

    # ── /security restore-roles ───────────────────────────────────────────
    @app_commands.command(name="restore-roles", description="Manually restore saved roles for a member (or after anti-nuke).")
    @app_commands.describe(user="Member to restore roles for")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def restore_roles_cmd(self, interaction: discord.Interaction, user: discord.Member):
        await interaction.response.defer(ephemeral=True)
        import aiosqlite
        async with aiosqlite.connect(self.bot.db.db_path) as db:
            async with db.execute(
                "SELECT role_ids FROM member_roles WHERE user_id=? AND guild_id=?",
                (user.id, interaction.guild.id)
            ) as c:
                row = await c.fetchone()

        if not row or not row[0]:
            return await interaction.followup.send(embed=error_embed("No Saved Roles", f"No saved roles found for {user.mention}."), ephemeral=True)

        role_ids = [int(r) for r in row[0].split(",") if r.strip()]
        restored = []
        failed   = []
        for rid in role_ids:
            role = interaction.guild.get_role(rid)
            if role and role not in user.roles and not role.managed:
                try:
                    await user.add_roles(role, reason=f"XERO Role Restore by {interaction.user}")
                    restored.append(role.mention)
                except Exception:
                    failed.append(role.name)

        embed = success_embed(
            "Roles Restored",
            f"**{user.mention}** — restored **{len(restored)}** role(s)\n"
            + (f"✅ {', '.join(restored)}" if restored else "None restored") +
            (f"\n⚠️ Failed: {', '.join(failed)}" if failed else "")
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /security view ────────────────────────────────────────────────────
    @app_commands.command(name="view", description="View all active security settings for this server.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def view(self, interaction: discord.Interaction):
        s = await self.bot.db.get_guild_settings(interaction.guild.id)
        import aiosqlite
        async with aiosqlite.connect(self.bot.db.db_path) as db:
            try:
                async with db.execute("SELECT domain FROM allowed_domains WHERE guild_id=?", (interaction.guild.id,)) as c:
                    domains = [r[0] for r in await c.fetchall()]
            except Exception:
                domains = []

        def tog(v, on="✅ On", off="❌ Off"): return on if v else off

        embed = comprehensive_embed(title=f"🔒  Security Settings — {interaction.guild.name}", color=XERO.PRIMARY)
        embed.add_field(name="🚨 Anti-Nuke", value=(
            f"Status: {tog(s.get('anti_nuke_enabled',0))}\n"
            f"Threshold: {s.get('anti_nuke_threshold',3)} actions/10s"
        ), inline=True)
        embed.add_field(name="👶 Account Age", value=(
            f"Minimum: **{s.get('min_account_age_days',0)}** days\n"
            f"Action: {s.get('account_age_action','kick_dm')}"
        ), inline=True)
        embed.add_field(name="🔗 Link Filter", value=(
            f"Status: {tog(s.get('link_filter_enabled',0))}\n"
            f"Allowed: {len(domains)} domain(s)"
        ), inline=True)
        embed.add_field(name="💾 Role Restore", value=tog(s.get("role_restore_enabled",0)), inline=True)
        if domains:
            embed.add_field(name="✅ Allowed Domains", value="\n".join(f"`{d}`" for d in domains[:10]), inline=False)
        embed.set_footer(text="XERO Security  •  /security setup to change settings")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Security(bot))
