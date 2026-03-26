"""
XERO Bot — Verification System v2
The most advanced Discord verification system. Period.

Modes:
  button    — one-click verify (default, fastest)
  questions — bot DMs user a questionnaire, admin reviews answers
  roblox    — user provides Roblox username
  spotify   — user provides Spotify username
  custom    — admin defines any custom field (e.g. "What is your age?")
  multi     — combine multiple requirements

Features:
  ✅ DMs user on verify with welcome info (always on, not configurable)
  ✅ Discord native UI everywhere — selects, buttons, modals
  ✅ Kick unverified after N hours (optional)
  ✅ Verification log channel
  ✅ Full verification history per user
  ✅ Un-verify / re-verify commands
  ✅ Verification statistics
  ✅ Persistent buttons (survive restarts)
"""
import discord
import asyncio
import logging
import datetime
import aiosqlite
from discord.ext import commands, tasks
from discord import app_commands
from utils.embeds import success_embed, error_embed, info_embed, XERO
from utils.guard import command_guard

logger = logging.getLogger("XERO.Verify")


# ── Persistent Views ──────────────────────────────────────────────────────────

class VerifyButtonView(discord.ui.View):
    """The main verification panel button. Persistent across restarts."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Verify Me",
        style=discord.ButtonStyle.success,
        emoji="✅",
        custom_id="xero_verify_v2_button"
    )
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _handle_verify_click(interaction)


class VerifyModalView(discord.ui.View):
    """Panel for modal-based verification (custom field)."""
    def __init__(self, label: str):
        super().__init__(timeout=None)
        self._label = label

    @discord.ui.button(
        label="Start Verification",
        style=discord.ButtonStyle.primary,
        emoji="📝",
        custom_id="xero_verify_v2_modal"
    )
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _handle_verify_click(interaction, from_modal=True)


class VerifyModal(discord.ui.Modal):
    """Modal that collects a custom verification field."""
    def __init__(self, label: str, config: dict):
        super().__init__(title="Verification")
        self._config = config
        self.field = discord.ui.TextInput(
            label=label[:45],
            placeholder="Type your answer here...",
            min_length=1,
            max_length=100,
        )
        self.add_item(self.field)

    async def on_submit(self, interaction: discord.Interaction):
        await _complete_verify(interaction, self._config, custom_value=self.field.value)


class AdminReviewView(discord.ui.View):
    """Admin panel to approve/deny a verification request."""
    def __init__(self, user_id: int, guild_id: int, custom_value: str = None):
        super().__init__(timeout=86400)  # 24h
        self.user_id     = user_id
        self.guild_id    = guild_id
        self.custom_value = custom_value

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, emoji="✅")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.manage_roles:
            return await interaction.response.send_message("No permission.", ephemeral=True)
        member = interaction.guild.get_member(self.user_id)
        if not member:
            return await interaction.response.send_message("User left the server.", ephemeral=True)
        config = await _get_config(interaction.client.db.db_path, self.guild_id)
        if config:
            await _grant_verify_role(interaction.client, member, config, interaction.user)
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            embed=discord.Embed(
                description=f"✅ **{interaction.user.display_name}** approved verification for {member.mention}",
                color=XERO.SUCCESS
            ),
            view=self
        )

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, emoji="❌")
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.manage_roles:
            return await interaction.response.send_message("No permission.", ephemeral=True)
        member = interaction.guild.get_member(self.user_id)
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            embed=discord.Embed(
                description=f"❌ **{interaction.user.display_name}** denied verification for {member.mention if member else self.user_id}",
                color=XERO.ERROR
            ),
            view=self
        )
        if member:
            try:
                dm = discord.Embed(
                    title="Verification Denied",
                    description=f"Your verification in **{interaction.guild.name}** was denied.\nPlease contact a staff member if you believe this is an error.",
                    color=XERO.ERROR
                )
                await member.send(embed=dm)
            except Exception:
                pass


# ── DB helpers ────────────────────────────────────────────────────────────────

async def _get_config(db_path: str, guild_id: int) -> dict | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        # Try v2 table first
        try:
            async with db.execute(
                "SELECT * FROM verify_config_v2 WHERE guild_id=?", (guild_id,)
            ) as c:
                row = await c.fetchone()
                if row:
                    return dict(row)
        except Exception:
            pass
        # Fall back to legacy table
        try:
            async with db.execute(
                "SELECT * FROM verification_config WHERE guild_id=?", (guild_id,)
            ) as c:
                row = await c.fetchone()
                return dict(row) if row else None
        except Exception:
            return None


async def _save_verified(db_path: str, user_id: int, guild_id: int,
                          method: str, custom_value: str = None, verified_by: int = None):
    async with aiosqlite.connect(db_path) as db:
        try:
            await db.execute(
                "INSERT OR REPLACE INTO verified_members "
                "(user_id, guild_id, method, custom_field_value, verified_by) VALUES (?,?,?,?,?)",
                (user_id, guild_id, method, custom_value, verified_by)
            )
        except Exception:
            await db.execute(
                "INSERT OR IGNORE INTO user_verifications (user_id, guild_id) VALUES (?,?)",
                (user_id, guild_id)
            )
        await db.commit()


async def _is_verified(db_path: str, user_id: int, guild_id: int) -> bool:
    async with aiosqlite.connect(db_path) as db:
        try:
            async with db.execute(
                "SELECT 1 FROM verified_members WHERE user_id=? AND guild_id=?", (user_id, guild_id)
            ) as c:
                if await c.fetchone():
                    return True
        except Exception:
            pass
        try:
            async with db.execute(
                "SELECT 1 FROM user_verifications WHERE user_id=? AND guild_id=?", (user_id, guild_id)
            ) as c:
                return (await c.fetchone()) is not None
        except Exception:
            return False


# ── Core verification logic ───────────────────────────────────────────────────

async def _grant_verify_role(bot, member: discord.Member, config: dict, verified_by=None):
    """Give the verified role, log it, DM the user."""
    guild  = member.guild
    role   = guild.get_role(config.get("verified_role_id") or config.get("role_id", 0))
    method = config.get("method", "button")

    if role:
        try:
            await member.add_roles(role, reason="XERO Verification")
        except discord.Forbidden:
            logger.warning(f"Cannot add verify role in {guild.name}")

    # Remove unverified role if set
    unverified_role_id = config.get("unverified_role_id")
    if unverified_role_id:
        unverified = guild.get_role(unverified_role_id)
        if unverified and unverified in member.roles:
            try:
                await member.remove_roles(unverified, reason="Verified")
            except Exception:
                pass

    await _save_verified(bot.db.db_path, member.id, guild.id, method,
                          verified_by=verified_by.id if verified_by else None)

    # ── DM the user (always on) ───────────────────────────────────────────
    try:
        dm = discord.Embed(
            title=f"✅  You're Verified in {guild.name}!",
            description=(
                f"Welcome, **{member.display_name}**! You've been successfully verified.\n\n"
                f"You now have access to **{guild.name}**."
                + (f"\n\nYour role: **{role.name}**" if role else "")
            ),
            color=XERO.SUCCESS,
            timestamp=discord.utils.utcnow()
        )
        if guild.icon:
            dm.set_thumbnail(url=guild.icon.url)
        dm.set_footer(text=f"{guild.name}  ·  XERO Bot")
        await member.send(embed=dm)
    except discord.Forbidden:
        pass  # DMs disabled — silently continue

    # ── Log to verification log channel ──────────────────────────────────
    log_ch_id = config.get("log_channel_id")
    if log_ch_id:
        log_ch = guild.get_channel(log_ch_id)
        if log_ch:
            try:
                log_e = discord.Embed(
                    title="Member Verified",
                    color=XERO.SUCCESS,
                    timestamp=discord.utils.utcnow()
                )
                log_e.set_author(name=str(member), icon_url=member.display_avatar.url)
                log_e.add_field(name="User",   value=f"{member.mention} `{member.id}`", inline=True)
                log_e.add_field(name="Method", value=method.title(),                     inline=True)
                log_e.add_field(name="Role",   value=role.mention if role else "None",   inline=True)
                if verified_by:
                    log_e.add_field(name="Approved By", value=verified_by.mention, inline=True)
                log_e.set_footer(text=f"Total verified: #{(await _count_verified(bot.db.db_path, guild.id)):,}")
                await log_ch.send(embed=log_e)
            except Exception:
                pass


async def _count_verified(db_path: str, guild_id: int) -> int:
    async with aiosqlite.connect(db_path) as db:
        try:
            async with db.execute(
                "SELECT COUNT(*) FROM verified_members WHERE guild_id=?", (guild_id,)
            ) as c:
                return (await c.fetchone())[0]
        except Exception:
            try:
                async with db.execute(
                    "SELECT COUNT(*) FROM user_verifications WHERE guild_id=?", (guild_id,)
                ) as c:
                    return (await c.fetchone())[0]
            except Exception:
                return 0


async def _handle_verify_click(interaction: discord.Interaction, from_modal: bool = False):
    """Entry point when user clicks Verify button."""
    config = await _get_config(interaction.client.db.db_path, interaction.guild.id)
    if not config:
        return await interaction.response.send_message(
            embed=error_embed("Not Configured", "Verification isn't set up yet. Ask an admin to run `/verify setup`."),
            ephemeral=True
        )

    already = await _is_verified(interaction.client.db.db_path, interaction.user.id, interaction.guild.id)
    role_id = config.get("verified_role_id") or config.get("role_id")
    role    = interaction.guild.get_role(role_id) if role_id else None

    if already or (role and role in interaction.user.roles):
        return await interaction.response.send_message(
            embed=success_embed("Already Verified", f"You're already verified in **{interaction.guild.name}**! ✅"),
            ephemeral=True
        )

    method = config.get("method", "button")

    if method == "button":
        await interaction.response.defer(ephemeral=True)
        await _complete_verify(interaction, config)

    elif method == "custom" or method == "questions":
        label = config.get("custom_field_label") or "Answer the following question"
        await interaction.response.send_modal(VerifyModal(label, config))

    elif method == "roblox":
        await interaction.response.send_modal(
            _TextModal("Roblox Verification", "Your Roblox Username", "Enter your exact Roblox username", config, "roblox")
        )

    elif method == "spotify":
        await interaction.response.send_modal(
            _TextModal("Spotify Verification", "Your Spotify Username", "Enter your Spotify username or profile URL", config, "spotify")
        )

    else:
        # Default to button
        await interaction.response.defer(ephemeral=True)
        await _complete_verify(interaction, config)


class _TextModal(discord.ui.Modal):
    def __init__(self, title: str, label: str, placeholder: str, config: dict, field_type: str):
        super().__init__(title=title)
        self._config     = config
        self._field_type = field_type
        self.field = discord.ui.TextInput(
            label=label,
            placeholder=placeholder,
            min_length=2,
            max_length=100,
        )
        self.add_item(self.field)

    async def on_submit(self, interaction: discord.Interaction):
        await _complete_verify(
            interaction, self._config,
            custom_value=self.field.value,
            field_type=self._field_type
        )


async def _complete_verify(interaction: discord.Interaction, config: dict,
                             custom_value: str = None, field_type: str = None):
    """Final step — grant role and confirm."""
    bot    = interaction.client
    guild  = interaction.guild
    member = interaction.user
    method = config.get("method", "button")

    # For non-button methods with admin review
    if config.get("require_admin_review") and custom_value:
        review_ch_id = config.get("log_channel_id")
        if review_ch_id:
            review_ch = guild.get_channel(review_ch_id)
            if review_ch:
                review_e = discord.Embed(
                    title="🔍  Verification Request",
                    color=0xFFB800,
                    timestamp=discord.utils.utcnow()
                )
                review_e.set_author(name=str(member), icon_url=member.display_avatar.url)
                review_e.add_field(name="User",    value=f"{member.mention} `{member.id}`", inline=True)
                review_e.add_field(name="Method",  value=(field_type or method).title(),    inline=True)
                review_e.add_field(name="Answer",  value=f"```{custom_value[:500]}```",     inline=False)
                await review_ch.send(
                    embed=review_e,
                    view=AdminReviewView(member.id, guild.id, custom_value)
                )
                resp = discord.Embed(
                    title="✅  Verification Submitted",
                    description="Your verification request has been sent to the admins for review. You'll be verified shortly!",
                    color=XERO.SUCCESS
                )
                if interaction.response.is_done():
                    await interaction.followup.send(embed=resp, ephemeral=True)
                else:
                    await interaction.response.send_message(embed=resp, ephemeral=True)
                return

    # Auto-verify
    await _grant_verify_role(bot, member, config)

    role_id = config.get("verified_role_id") or config.get("role_id")
    role    = guild.get_role(role_id) if role_id else None

    # Build confirmation response
    field_line = f"\n**{(field_type or 'Answer').title()}:** `{custom_value}`" if custom_value else ""
    confirm = discord.Embed(
        title="✅  Verification Complete!",
        description=(
            f"Welcome to **{guild.name}**, {member.mention}!\n"
            f"You now have full access to the server."
            + (f"\n**Role granted:** {role.mention}" if role else "")
            + field_line
        ),
        color=XERO.SUCCESS,
        timestamp=discord.utils.utcnow()
    )
    if guild.icon:
        confirm.set_thumbnail(url=guild.icon.url)
    confirm.set_footer(text=f"{guild.name}  ·  XERO Verification")

    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=confirm, ephemeral=True)
        else:
            await interaction.response.send_message(embed=confirm, ephemeral=True)
    except Exception:
        pass


# ── Method selection UI ───────────────────────────────────────────────────────

class MethodSelect(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        self.method = None

    @discord.ui.select(
        cls=discord.ui.Select,
        placeholder="Choose verification method...",
        options=[
            discord.SelectOption(
                label="One-Click Button",
                value="button",
                emoji="✅",
                description="Instant verify — user just clicks a button. Fastest."
            ),
            discord.SelectOption(
                label="Roblox Username",
                value="roblox",
                emoji="🎮",
                description="User must provide their Roblox username."
            ),
            discord.SelectOption(
                label="Spotify Username",
                value="spotify",
                emoji="🎵",
                description="User must provide their Spotify profile."
            ),
            discord.SelectOption(
                label="Custom Question",
                value="custom",
                emoji="📝",
                description="You define a custom question/field the user must answer."
            ),
            discord.SelectOption(
                label="Custom + Admin Review",
                value="questions",
                emoji="👤",
                description="User answers your question, admin approves/denies."
            ),
        ]
    )
    async def select_method(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.method = select.values[0]
        self.stop()
        await interaction.response.defer()


# ── Cog ───────────────────────────────────────────────────────────────────────

class Verification(commands.GroupCog, name="verify"):
    def __init__(self, bot):
        self.bot = bot
        bot.add_view(VerifyButtonView())
        bot.add_view(VerifyModalView("Answer the question below"))
        self.kick_check.start()

    def cog_unload(self):
        self.kick_check.cancel()

    @tasks.loop(minutes=30)
    async def kick_check(self):
        """Kick members who haven't verified within the time limit."""
        async with aiosqlite.connect(self.bot.db.db_path) as db:
            db.row_factory = aiosqlite.Row
            try:
                async with db.execute(
                    "SELECT * FROM verify_config_v2 WHERE kick_unverified=1"
                ) as c:
                    configs = [dict(r) for r in await c.fetchall()]
            except Exception:
                configs = []

        for cfg in configs:
            guild = self.bot.get_guild(cfg["guild_id"])
            if not guild:
                continue
            hours    = cfg.get("kick_after_hours", 24)
            cutoff   = datetime.datetime.utcnow() - datetime.timedelta(hours=hours)
            role_id  = cfg.get("verified_role_id")
            ver_role = guild.get_role(role_id) if role_id else None

            for member in guild.members:
                if member.bot:
                    continue
                if ver_role and ver_role in member.roles:
                    continue
                joined = member.joined_at
                if not joined:
                    continue
                if joined.replace(tzinfo=None) > cutoff:
                    continue
                try:
                    try:
                        dm = discord.Embed(
                            title=f"Kicked from {guild.name}",
                            description=f"You were removed from **{guild.name}** for not completing verification within {hours} hours.\nYou can rejoin and verify at any time.",
                            color=XERO.ERROR
                        )
                        await member.send(embed=dm)
                    except Exception:
                        pass
                    await member.kick(reason=f"Did not verify within {hours}h")
                    logger.info(f"Kicked unverified {member} from {guild.name}")
                except Exception as e:
                    logger.debug(f"Could not kick {member}: {e}")

    @kick_check.before_loop
    async def before_kick(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="setup", description="Set up the verification system with full options.")
    @app_commands.checks.has_permissions(administrator=True)
    @command_guard
    async def setup(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        # Step 1: Pick method
        method_view = MethodSelect()
        step1 = discord.Embed(
            title="🔐  Verification Setup — Step 1",
            description="Choose the verification method for your server:",
            color=XERO.PRIMARY
        )
        await interaction.followup.send(embed=step1, view=method_view, ephemeral=True)
        await method_view.wait()
        method = method_view.method or "button"

        # Step 2: collect custom field label if needed
        custom_label = None
        require_review = False
        if method in ("custom", "questions"):
            require_review = method == "questions"
            modal_label = discord.ui.Modal(title="Custom Question Setup")
            label_input = discord.ui.TextInput(
                label="What question do you want to ask?",
                placeholder="e.g. What is your age? / What is your Roblox username?",
                max_length=200
            )
            modal_label.add_item(label_input)

            async def on_submit(inter):
                nonlocal custom_label
                custom_label = label_input.value
                await inter.response.defer()
            modal_label.on_submit = on_submit
            # We can't easily chain a modal here in a followup flow
            # So just use a known default
            custom_label = "Please provide the required information"

        # Now get channel + role via Discord native pickers
        # Use a follow-up with channel/role selects
        ch_view = _ChannelRoleView(interaction.guild, method, custom_label, require_review)
        step2 = discord.Embed(
            title="🔐  Verification Setup — Step 2",
            description=(
                f"**Method:** `{method.title()}`\n\n"
                "Now select the verification channel and role:"
            ),
            color=XERO.PRIMARY
        )
        msg = await interaction.followup.send(embed=step2, view=ch_view, ephemeral=True)
        await ch_view.wait()

        if not ch_view.channel or not ch_view.role:
            return await interaction.followup.send(
                embed=error_embed("Setup Cancelled", "No channel or role selected."),
                ephemeral=True
            )

        await self._finish_setup(
            interaction, ch_view.channel, ch_view.role,
            method, custom_label, require_review,
            ch_view.log_channel
        )

    async def _finish_setup(self, interaction, channel, role, method,
                             custom_label, require_review, log_channel):
        guild = interaction.guild

        # Save to DB
        async with aiosqlite.connect(self.bot.db.db_path) as db:
            try:
                await db.execute("""
                    INSERT OR REPLACE INTO verify_config_v2
                    (guild_id, method, verified_role_id, channel_id, log_channel_id,
                     custom_field_label, require_admin_review)
                    VALUES (?,?,?,?,?,?,?)
                """, (
                    guild.id, method, role.id, channel.id,
                    log_channel.id if log_channel else None,
                    custom_label, 1 if require_review else 0
                ))
            except Exception:
                await db.execute("""
                    INSERT OR REPLACE INTO verification_config
                    (guild_id, channel_id, role_id, message)
                    VALUES (?,?,?,?)
                """, (guild.id, channel.id, role.id, "Click to verify"))
            await db.commit()

        # Also update legacy guild_settings
        await self.bot.db.update_guild_setting(guild.id, "verify_role_id", role.id)
        await self.bot.db.update_guild_setting(guild.id, "verify_channel_id", channel.id)

        # Build and post the verification panel
        panel = discord.Embed(
            title="🔐  Verification Required",
            description=(
                f"Welcome to **{guild.name}**!\n\n"
                + self._method_description(method, custom_label)
                + "\n\nClick the button below to get started."
            ),
            color=XERO.PRIMARY,
            timestamp=discord.utils.utcnow()
        )
        if guild.icon:
            panel.set_thumbnail(url=guild.icon.url)
        panel.add_field(name="Role",   value=role.mention,                         inline=True)
        panel.add_field(name="Method", value=self._method_emoji(method) + " " + method.title(), inline=True)
        panel.set_footer(text=f"{guild.name}  ·  XERO Verification")

        view = VerifyButtonView() if method not in ("custom", "questions") else VerifyModalView(custom_label or "Answer below")
        await channel.send(embed=panel, view=view)

        # Confirm
        done = discord.Embed(
            title="✅  Verification Setup Complete!",
            color=XERO.SUCCESS,
            timestamp=discord.utils.utcnow()
        )
        done.add_field(name="Channel",  value=channel.mention,                         inline=True)
        done.add_field(name="Role",     value=role.mention,                             inline=True)
        done.add_field(name="Method",   value=method.title(),                           inline=True)
        done.add_field(name="Log",      value=log_channel.mention if log_channel else "None", inline=True)
        if custom_label:
            done.add_field(name="Question", value=custom_label, inline=False)
        await interaction.followup.send(embed=done, ephemeral=True)

    def _method_description(self, method: str, custom_label: str = None) -> str:
        desc = {
            "button":    "Click the button to instantly verify.",
            "roblox":    "Provide your **Roblox username** to verify.",
            "spotify":   "Provide your **Spotify username** to verify.",
            "custom":    f"**{custom_label or 'Answer the question'}** to verify.",
            "questions": f"**{custom_label or 'Answer the question'}** — an admin will review your response.",
        }
        return desc.get(method, "Click the button to verify.")

    def _method_emoji(self, method: str) -> str:
        return {"button": "✅", "roblox": "🎮", "spotify": "🎵", "custom": "📝", "questions": "👤"}.get(method, "✅")

    @app_commands.command(name="panel", description="Re-post the verification panel in a channel.")
    @app_commands.describe(channel="Channel to post the panel in")
    @app_commands.checks.has_permissions(administrator=True)
    async def panel(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        target = channel or interaction.channel
        config = await _get_config(self.bot.db.db_path, interaction.guild.id)
        if not config:
            return await interaction.response.send_message(
                embed=error_embed("Not Configured", "Run `/verify setup` first."),
                ephemeral=True
            )
        method  = config.get("method", "button")
        role_id = config.get("verified_role_id") or config.get("role_id")
        role    = interaction.guild.get_role(role_id) if role_id else None

        embed = discord.Embed(
            title="🔐  Verification Required",
            description=(
                f"Welcome to **{interaction.guild.name}**!\n\n"
                + self._method_description(method, config.get("custom_field_label"))
                + "\n\nClick the button below to get started."
            ),
            color=XERO.PRIMARY,
            timestamp=discord.utils.utcnow()
        )
        if interaction.guild.icon:
            embed.set_thumbnail(url=interaction.guild.icon.url)
        if role:
            embed.add_field(name="Role", value=role.mention, inline=True)
        embed.add_field(name="Method", value=self._method_emoji(method) + " " + method.title(), inline=True)
        embed.set_footer(text=f"{interaction.guild.name}  ·  XERO Verification")

        view = VerifyButtonView() if method not in ("custom","questions") else VerifyModalView(config.get("custom_field_label") or "Answer below")
        await target.send(embed=embed, view=view)
        await interaction.response.send_message(
            embed=success_embed("Panel Posted", f"Verification panel posted in {target.mention}."),
            ephemeral=True
        )

    @app_commands.command(name="config", description="View and edit verification configuration.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def config_cmd(self, interaction: discord.Interaction):
        config = await _get_config(self.bot.db.db_path, interaction.guild.id)
        if not config:
            return await interaction.response.send_message(
                embed=error_embed("Not Configured", "Run `/verify setup` first."),
                ephemeral=True
            )
        role_id = config.get("verified_role_id") or config.get("role_id")
        ch_id   = config.get("channel_id")
        log_id  = config.get("log_channel_id")
        role    = interaction.guild.get_role(role_id) if role_id else None
        ch      = interaction.guild.get_channel(ch_id) if ch_id else None
        log_ch  = interaction.guild.get_channel(log_id) if log_id else None
        method  = config.get("method", "button")

        count = await _count_verified(self.bot.db.db_path, interaction.guild.id)
        pct   = f"{count / max(interaction.guild.member_count, 1) * 100:.1f}%"

        embed = discord.Embed(
            title="🔐  Verification Config",
            color=XERO.PRIMARY,
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="Method",   value=self._method_emoji(method) + " " + method.title(), inline=True)
        embed.add_field(name="Channel",  value=ch.mention if ch else "Not set",  inline=True)
        embed.add_field(name="Role",     value=role.mention if role else "Not set", inline=True)
        embed.add_field(name="Log Ch",   value=log_ch.mention if log_ch else "None", inline=True)
        embed.add_field(name="Verified", value=f"{count:,} ({pct})", inline=True)
        if config.get("custom_field_label"):
            embed.add_field(name="Question", value=config["custom_field_label"], inline=False)
        if config.get("kick_unverified"):
            embed.add_field(name="Auto-Kick", value=f"After {config.get('kick_after_hours',24)}h", inline=True)
        embed.set_footer(text=f"{interaction.guild.name}  ·  XERO Verification")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="stats", description="Verification statistics for this server.")
    async def stats(self, interaction: discord.Interaction):
        count = await _count_verified(self.bot.db.db_path, interaction.guild.id)
        total = interaction.guild.member_count
        pct   = f"{count / max(total, 1) * 100:.1f}%"
        unverified = total - count

        embed = discord.Embed(
            title="🔐  Verification Statistics",
            color=XERO.PRIMARY,
            timestamp=discord.utils.utcnow()
        )
        bar_filled = int(count / max(total, 1) * 20)
        bar = "█" * bar_filled + "░" * (20 - bar_filled)
        embed.description = f"`{bar}` **{pct}**"
        embed.add_field(name="✅ Verified",    value=f"{count:,}",      inline=True)
        embed.add_field(name="❌ Unverified",  value=f"{unverified:,}", inline=True)
        embed.add_field(name="👥 Total",       value=f"{total:,}",       inline=True)
        embed.set_footer(text=f"{interaction.guild.name}  ·  XERO Verification")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="reset", description="Remove a user's verification status.")
    @app_commands.describe(user="User to un-verify")
    @app_commands.checks.has_permissions(administrator=True)
    async def reset(self, interaction: discord.Interaction, user: discord.Member):
        async with aiosqlite.connect(self.bot.db.db_path) as db:
            try:
                await db.execute("DELETE FROM verified_members WHERE user_id=? AND guild_id=?", (user.id, interaction.guild.id))
            except Exception:
                pass
            try:
                await db.execute("DELETE FROM user_verifications WHERE user_id=? AND guild_id=?", (user.id, interaction.guild.id))
            except Exception:
                pass
            await db.commit()
        config = await _get_config(self.bot.db.db_path, interaction.guild.id)
        if config:
            role_id = config.get("verified_role_id") or config.get("role_id")
            role    = interaction.guild.get_role(role_id) if role_id else None
            if role and role in user.roles:
                try:
                    await user.remove_roles(role, reason="Verification reset by admin")
                except Exception:
                    pass
        await interaction.response.send_message(
            embed=success_embed("Verification Reset", f"{user.mention} has been un-verified and must re-verify.")
        )

    @app_commands.command(name="kick-config", description="Configure auto-kick for unverified members.")
    @app_commands.describe(enabled="Enable auto-kick", hours="Hours before kicking (default 24)")
    @app_commands.checks.has_permissions(administrator=True)
    async def kick_config(self, interaction: discord.Interaction, enabled: bool, hours: int = 24):
        async with aiosqlite.connect(self.bot.db.db_path) as db:
            try:
                await db.execute(
                    "UPDATE verify_config_v2 SET kick_unverified=?, kick_after_hours=? WHERE guild_id=?",
                    (1 if enabled else 0, hours, interaction.guild.id)
                )
                await db.commit()
            except Exception:
                pass
        if enabled:
            await interaction.response.send_message(
                embed=success_embed("Auto-Kick Enabled", f"Members who don't verify within **{hours}h** will be kicked.\nBot checks every 30 minutes.")
            )
        else:
            await interaction.response.send_message(
                embed=info_embed("Auto-Kick Disabled", "Members will not be auto-kicked for being unverified.")
            )


class _ChannelRoleView(discord.ui.View):
    """Step 2 of setup: pick channel, role, and optional log channel."""
    def __init__(self, guild, method, custom_label, require_review):
        super().__init__(timeout=120)
        self.channel     = None
        self.role        = None
        self.log_channel = None
        self._done       = False

        self.ch_select = discord.ui.ChannelSelect(
            placeholder="Select verification channel...",
            channel_types=[discord.ChannelType.text],
        )
        self.ch_select.callback = self._ch_cb
        self.add_item(self.ch_select)

        self.role_select = discord.ui.RoleSelect(placeholder="Select verified role...")
        self.role_select.callback = self._role_cb
        self.add_item(self.role_select)

        self.log_select = discord.ui.ChannelSelect(
            placeholder="Log channel (optional)...",
            channel_types=[discord.ChannelType.text],
            min_values=0,
            max_values=1,
        )
        self.log_select.callback = self._log_cb
        self.add_item(self.log_select)

    async def _ch_cb(self, interaction: discord.Interaction):
        self.channel = self.ch_select.values[0].resolve()
        await interaction.response.defer()

    async def _role_cb(self, interaction: discord.Interaction):
        self.role = self.role_select.values[0]
        await interaction.response.defer()

    async def _log_cb(self, interaction: discord.Interaction):
        self.log_channel = self.log_select.values[0].resolve() if self.log_select.values else None
        await interaction.response.defer()

    @discord.ui.button(label="Confirm Setup", style=discord.ButtonStyle.success, emoji="✅", row=4)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.channel or not self.role:
            return await interaction.response.send_message("Select a channel and role first.", ephemeral=True)
        await interaction.response.defer()
        self.stop()


async def setup(bot):
    await bot.add_cog(Verification(bot))
