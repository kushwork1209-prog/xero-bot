"""
XERO Bot — Verification System v2
Simple to set up. Powerful under the hood.

/verify setup  — one command, picks channel + role, posts panel
/verify panel  — repost panel anywhere
/verify method — change verification method
/verify stats  — see verification stats
/verify reset  — un-verify a user

5 verification modes:
  button   — click to verify instantly (default)
  roblox   — user types their Roblox username
  spotify  — user types their Spotify username
  custom   — admin sets a custom question
  review   — custom question + admin must approve

Always DMs user on successful verification.
Persistent buttons survive bot restarts.
"""
import discord
import logging
import datetime
import aiosqlite
from discord.ext import commands, tasks
from discord import app_commands
from utils.embeds import success_embed, error_embed, info_embed, XERO
from utils.guard import command_guard

logger = logging.getLogger("XERO.Verify")

# ── Persistent button views ───────────────────────────────────────────────────

class VerifyView(discord.ui.View):
    """One-click verify button. Survives restarts via custom_id."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Verify Me",
        style=discord.ButtonStyle.success,
        emoji="✅",
        custom_id="xero:verify:button"
    )
    async def click(self, interaction: discord.Interaction, _btn):
        await _process_verify(interaction)


class VerifyInputView(discord.ui.View):
    """Button that opens a modal for text input verification."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Start Verification",
        style=discord.ButtonStyle.primary,
        emoji="📝",
        custom_id="xero:verify:input"
    )
    async def click(self, interaction: discord.Interaction, _btn):
        await _process_verify(interaction, needs_input=True)


# ── Modals ────────────────────────────────────────────────────────────────────

class VerifyModal(discord.ui.Modal):
    def __init__(self, label: str, placeholder: str, config: dict):
        super().__init__(title="Verification")
        self._config = config
        self.answer = discord.ui.TextInput(
            label=label[:45],
            placeholder=placeholder[:100],
            min_length=1,
            max_length=100
        )
        self.add_item(self.answer)

    async def on_submit(self, interaction: discord.Interaction):
        method = self._config.get("method", "button")
        needs_review = method == "review"

        if needs_review:
            await _send_for_review(interaction, self._config, self.answer.value)
        else:
            await _grant_and_respond(interaction, self._config, custom_value=self.answer.value)


class AdminApproveView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=86400)
        self.user_id = user_id

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, emoji="✅")
    async def approve(self, interaction: discord.Interaction, _btn):
        if not interaction.user.guild_permissions.manage_roles:
            return await interaction.response.send_message("No permission.", ephemeral=True)
        member = interaction.guild.get_member(self.user_id)
        if not member:
            return await interaction.response.send_message("User left the server.", ephemeral=True)

        config = await _load_config(interaction.client.db.db_path, interaction.guild.id)
        if config:
            await _do_grant(interaction.client, member, config)

        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            embed=discord.Embed(
                description=f"✅ Approved by {interaction.user.mention}",
                color=discord.Color(0x00FF94)
            ),
            view=self
        )

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, emoji="❌")
    async def deny(self, interaction: discord.Interaction, _btn):
        if not interaction.user.guild_permissions.manage_roles:
            return await interaction.response.send_message("No permission.", ephemeral=True)
        member = interaction.guild.get_member(self.user_id)
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            embed=discord.Embed(
                description=f"❌ Denied by {interaction.user.mention}",
                color=discord.Color(0xFF1744)
            ),
            view=self
        )
        if member:
            try:
                await member.send(embed=discord.Embed(
                    title="Verification Denied",
                    description=f"Your verification in **{interaction.guild.name}** was denied. Please contact a staff member.",
                    color=discord.Color(0xFF1744)
                ))
            except Exception:
                pass


# ── DB helpers ────────────────────────────────────────────────────────────────

async def _load_config(db_path: str, guild_id: int) -> dict | None:
    """Load verify config, try v2 table then fall back to legacy."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        try:
            async with db.execute(
                "SELECT * FROM verify_config_v2 WHERE guild_id=?", (guild_id,)
            ) as c:
                row = await c.fetchone()
                if row:
                    return dict(row)
        except Exception:
            pass
        try:
            async with db.execute(
                "SELECT * FROM verification_config WHERE guild_id=?", (guild_id,)
            ) as c:
                row = await c.fetchone()
                if row:
                    d = dict(row)
                    d["verified_role_id"] = d.get("role_id")
                    d["method"] = "button"
                    return d
        except Exception:
            pass
    return None


async def _save_config(db_path: str, guild_id: int, **kwargs):
    """Save or update verify config in v2 table."""
    async with aiosqlite.connect(db_path) as db:
        # Check exists
        async with db.execute(
            "SELECT 1 FROM verify_config_v2 WHERE guild_id=?", (guild_id,)
        ) as c:
            exists = await c.fetchone()

        if exists:
            sets   = ", ".join(f"{k}=?" for k in kwargs)
            vals   = list(kwargs.values()) + [guild_id]
            await db.execute(f"UPDATE verify_config_v2 SET {sets} WHERE guild_id=?", vals)
        else:
            cols = ["guild_id"] + list(kwargs.keys())
            vals = [guild_id] + list(kwargs.values())
            ph   = ", ".join("?" * len(cols))
            await db.execute(
                f"INSERT INTO verify_config_v2 ({', '.join(cols)}) VALUES ({ph})", vals
            )
        await db.commit()


async def _mark_verified(db_path: str, user_id: int, guild_id: int, method: str, custom: str = None):
    async with aiosqlite.connect(db_path) as db:
        try:
            await db.execute(
                "INSERT OR REPLACE INTO verified_members (user_id, guild_id, method, custom_field_value) VALUES (?,?,?,?)",
                (user_id, guild_id, method, custom)
            )
        except Exception:
            try:
                await db.execute(
                    "INSERT OR IGNORE INTO user_verifications (user_id, guild_id) VALUES (?,?)",
                    (user_id, guild_id)
                )
            except Exception:
                pass
        await db.commit()


async def _is_verified(db_path: str, user_id: int, guild_id: int) -> bool:
    async with aiosqlite.connect(db_path) as db:
        try:
            async with db.execute(
                "SELECT 1 FROM verified_members WHERE user_id=? AND guild_id=?",
                (user_id, guild_id)
            ) as c:
                if await c.fetchone():
                    return True
        except Exception:
            pass
        try:
            async with db.execute(
                "SELECT 1 FROM user_verifications WHERE user_id=? AND guild_id=?",
                (user_id, guild_id)
            ) as c:
                return bool(await c.fetchone())
        except Exception:
            return False


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


# ── Core verify logic ─────────────────────────────────────────────────────────

async def _process_verify(interaction: discord.Interaction, needs_input: bool = False):
    """Entry point from button click."""
    config = await _load_config(interaction.client.db.db_path, interaction.guild.id)
    if not config:
        return await interaction.response.send_message(
            embed=error_embed("Not Configured", "Verification isn't set up. Ask an admin to run `/verify setup`."),
            ephemeral=True
        )

    # Already verified?
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
        await _grant_and_respond(interaction, config)

    elif method in ("roblox", "spotify", "custom", "review"):
        labels = {
            "roblox":  ("Roblox Username", "Enter your exact Roblox username"),
            "spotify": ("Spotify Username", "Enter your Spotify username or profile URL"),
            "custom":  (config.get("custom_label") or "Verification Question", config.get("custom_placeholder") or "Enter your answer"),
            "review":  (config.get("custom_label") or "Verification Answer",   config.get("custom_placeholder") or "Enter your answer — staff will review it"),
        }
        label, placeholder = labels[method]
        await interaction.response.send_modal(VerifyModal(label, placeholder, config))

    else:
        await interaction.response.defer(ephemeral=True)
        await _grant_and_respond(interaction, config)


async def _grant_and_respond(interaction: discord.Interaction, config: dict, custom_value: str = None):
    """Grant the verified role and send confirmation."""
    await _do_grant(interaction.client, interaction.user, config, custom_value=custom_value)
    role_id = config.get("verified_role_id") or config.get("role_id")
    role    = interaction.guild.get_role(role_id) if role_id else None

    embed = discord.Embed(
        title="✅  Verification Complete!",
        description=(
            f"Welcome to **{interaction.guild.name}**, {interaction.user.mention}!\n"
            f"You now have full access to the server."
            + (f"\n**Role:** {role.mention}" if role else "")
            + (f"\n**Answer saved:** `{custom_value}`" if custom_value else "")
        ),
        color=discord.Color(0x00FF94),
        timestamp=discord.utils.utcnow()
    )
    if interaction.guild.icon:
        embed.set_thumbnail(url=interaction.guild.icon.url)
    embed.set_footer(text=f"{interaction.guild.name}  ·  XERO Verification")

    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception:
        pass


async def _send_for_review(interaction: discord.Interaction, config: dict, answer: str):
    """Send verification for admin review."""
    log_ch_id = config.get("log_channel_id")
    if log_ch_id:
        log_ch = interaction.guild.get_channel(log_ch_id)
        if log_ch:
            embed = discord.Embed(
                title="📋  Verification Request",
                color=discord.Color(0xFFB800),
                timestamp=discord.utils.utcnow()
            )
            embed.set_author(name=str(interaction.user), icon_url=interaction.user.display_avatar.url)
            embed.add_field(name="User",   value=f"{interaction.user.mention} `{interaction.user.id}`", inline=True)
            embed.add_field(name="Answer", value=f"```{answer[:400]}```", inline=False)
            await log_ch.send(embed=embed, view=AdminApproveView(interaction.user.id))

    await interaction.response.send_message(
        embed=discord.Embed(
            title="✅  Submitted for Review",
            description="Your verification has been submitted. Staff will review it shortly!",
            color=discord.Color(0x00D4FF)
        ),
        ephemeral=True
    )


async def _do_grant(bot, member: discord.Member, config: dict, custom_value: str = None):
    """Actually grant the role, DM user, log it."""
    guild   = member.guild
    role_id = config.get("verified_role_id") or config.get("role_id")
    role    = guild.get_role(role_id) if role_id else None
    method  = config.get("method", "button")

    # Grant role
    if role:
        try:
            await member.add_roles(role, reason="XERO Verification")
        except Exception as e:
            logger.warning(f"Could not add verify role: {e}")

    # Remove unverified role if set
    unver_id = config.get("unverified_role_id")
    if unver_id:
        unver = guild.get_role(unver_id)
        if unver and unver in member.roles:
            try:
                await member.remove_roles(unver, reason="Verified")
            except Exception:
                pass

    # Save to DB
    await _mark_verified(bot.db.db_path, member.id, guild.id, method, custom_value)

    # DM user — always on
    try:
        dm = discord.Embed(
            title=f"✅  Verified in {guild.name}!",
            description=(
                f"Hey **{member.display_name}**, you're now verified!\n\n"
                f"You have full access to **{guild.name}**."
                + (f"\nYour role: **{role.name}**" if role else "")
            ),
            color=discord.Color(0x00FF94),
            timestamp=discord.utils.utcnow()
        )
        if guild.icon:
            dm.set_thumbnail(url=guild.icon.url)
        dm.set_footer(text=f"{guild.name}  ·  Verified via XERO Bot")
        await member.send(embed=dm)
    except discord.Forbidden:
        pass  # DMs disabled — silent, don't fail

    # Log
    log_ch_id = config.get("log_channel_id")
    if log_ch_id:
        log_ch = guild.get_channel(log_ch_id)
        if log_ch:
            try:
                count = await _count_verified(bot.db.db_path, guild.id)
                log_e = discord.Embed(
                    title="Member Verified",
                    color=discord.Color(0x00FF94),
                    timestamp=discord.utils.utcnow()
                )
                log_e.set_author(name=str(member), icon_url=member.display_avatar.url)
                log_e.add_field(name="User",   value=f"{member.mention} `{member.id}`", inline=True)
                log_e.add_field(name="Method", value=method.title(),                     inline=True)
                log_e.add_field(name="Role",   value=role.mention if role else "None",   inline=True)
                log_e.set_footer(text=f"Verified member #{count:,}")
                await log_ch.send(embed=log_e)
            except Exception:
                pass


# ── Build the panel embed ─────────────────────────────────────────────────────

def _build_panel(guild: discord.Guild, config: dict) -> tuple[discord.Embed, discord.ui.View]:
    method = config.get("method", "button")
    role_id = config.get("verified_role_id") or config.get("role_id")
    role    = guild.get_role(role_id) if role_id else None

    descriptions = {
        "button":  "Click the **Verify Me** button below to gain instant access.",
        "roblox":  "Click below and enter your **Roblox username** to verify.",
        "spotify": "Click below and enter your **Spotify username** to verify.",
        "custom":  f"Click below and answer the question: **{config.get('custom_label', 'Answer the question')}**",
        "review":  f"Click below and answer: **{config.get('custom_label', 'Answer the question')}**\nStaff will review your answer.",
    }

    embed = discord.Embed(
        title="🔐  Verification Required",
        description=(
            f"Welcome to **{guild.name}**!\n\n"
            + descriptions.get(method, "Click below to verify.")
        ),
        color=discord.Color(0x00D4FF),
        timestamp=discord.utils.utcnow()
    )
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    if role:
        embed.add_field(name="You'll receive", value=role.mention, inline=True)
    embed.add_field(
        name="Method",
        value={"button":"✅ One-click","roblox":"🎮 Roblox","spotify":"🎵 Spotify",
               "custom":"📝 Question","review":"👤 Staff Review"}.get(method, "✅ Button"),
        inline=True
    )
    embed.set_footer(text=f"{guild.name}  ·  Powered by XERO Bot")

    view = VerifyInputView() if method in ("roblox","spotify","custom","review") else VerifyView()
    return embed, view


# ── Cog ───────────────────────────────────────────────────────────────────────

class Verification(commands.GroupCog, name="verify"):
    def __init__(self, bot):
        self.bot = bot
        # Register persistent views
        bot.add_view(VerifyView())
        bot.add_view(VerifyInputView())
        self._kick_task.start()

    def cog_unload(self):
        self._kick_task.cancel()

    @tasks.loop(minutes=30)
    async def _kick_task(self):
        """Auto-kick unverified members after configured time."""
        for guild in self.bot.guilds:
            config = await _load_config(self.bot.db.db_path, guild.id)
            if not config or not config.get("kick_unverified"):
                continue
            hours   = int(config.get("kick_after_hours") or 24)
            role_id = config.get("verified_role_id") or config.get("role_id")
            role    = guild.get_role(role_id) if role_id else None
            cutoff  = discord.utils.utcnow() - datetime.timedelta(hours=hours)

            for member in guild.members:
                if member.bot: continue
                if role and role in member.roles: continue
                if not member.joined_at: continue
                if member.joined_at > cutoff: continue
                try:
                    try:
                        await member.send(embed=discord.Embed(
                            title=f"Removed from {guild.name}",
                            description=f"You were removed for not verifying within {hours} hours. You may rejoin and verify at any time.",
                            color=discord.Color(0xFF1744)
                        ))
                    except Exception:
                        pass
                    await member.kick(reason=f"Did not verify within {hours}h")
                except Exception:
                    pass

    @_kick_task.before_loop
    async def _before_kick(self):
        await self.bot.wait_until_ready()

    # ── /verify setup ─────────────────────────────────────────────────────────
    @app_commands.command(name="setup", description="Set up verification — pick a channel, role, and method. Done.")
    @app_commands.describe(
        channel="Channel to post the verification panel in",
        role="Role to give when verified",
        method="How users verify",
        log_channel="Where to log verifications (optional)"
    )
    @app_commands.choices(method=[
        app_commands.Choice(name="✅ One-click button (default, fastest)",   value="button"),
        app_commands.Choice(name="🎮 Roblox username",                        value="roblox"),
        app_commands.Choice(name="🎵 Spotify username",                       value="spotify"),
        app_commands.Choice(name="📝 Custom question (auto-approve)",         value="custom"),
        app_commands.Choice(name="👤 Custom question + staff review",         value="review"),
    ])
    @app_commands.checks.has_permissions(administrator=True)
    @command_guard
    async def setup(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        role: discord.Role,
        method: str = "button",
        log_channel: discord.TextChannel = None,
    ):
        await interaction.response.defer(ephemeral=True)

        # If custom/review, ask for the question label via a modal
        custom_label = None
        if method in ("custom", "review"):
            # We can't show a modal after defer, so use a simple default
            # Admins can update via /verify method later
            custom_label = "What is your answer?"

        # Save config
        await _save_config(
            self.bot.db.db_path, interaction.guild.id,
            method=method,
            verified_role_id=role.id,
            channel_id=channel.id,
            log_channel_id=log_channel.id if log_channel else None,
            custom_label=custom_label,
        )

        # Also update legacy tables so other parts of bot see it
        await self.bot.db.update_guild_setting(interaction.guild.id, "verify_role_id",    role.id)
        await self.bot.db.update_guild_setting(interaction.guild.id, "verify_channel_id", channel.id)

        # Post the panel
        embed, view = _build_panel(interaction.guild, {
            "method": method, "verified_role_id": role.id, "custom_label": custom_label
        })
        await channel.send(embed=embed, view=view)

        # Confirm
        done = discord.Embed(
            title="✅  Verification Setup Complete!",
            color=discord.Color(0x00FF94),
            timestamp=discord.utils.utcnow()
        )
        done.add_field(name="Channel",    value=channel.mention,                              inline=True)
        done.add_field(name="Role",       value=role.mention,                                 inline=True)
        done.add_field(name="Method",     value=method.title(),                               inline=True)
        done.add_field(name="Log",        value=log_channel.mention if log_channel else "—",  inline=True)
        done.description = (
            f"Verification panel posted in {channel.mention}.\n\n"
            + ("⚠️ Update the question with `/verify method` since you chose a custom method." if method in ("custom","review") else "")
        )
        await interaction.followup.send(embed=done, ephemeral=True)

    # ── /verify panel ─────────────────────────────────────────────────────────
    @app_commands.command(name="panel", description="Re-post the verification panel anywhere.")
    @app_commands.describe(channel="Channel to post in (defaults to current channel)")
    @app_commands.checks.has_permissions(administrator=True)
    async def panel(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        target = channel or interaction.channel
        config = await _load_config(self.bot.db.db_path, interaction.guild.id)
        if not config:
            return await interaction.response.send_message(
                embed=error_embed("Not Set Up", "Run `/verify setup` first."),
                ephemeral=True
            )
        embed, view = _build_panel(interaction.guild, config)
        await target.send(embed=embed, view=view)
        await interaction.response.send_message(
            embed=success_embed("Panel Posted", f"Verification panel posted in {target.mention}."),
            ephemeral=True
        )

    # ── /verify method ────────────────────────────────────────────────────────
    @app_commands.command(name="method", description="Change verification method and update the question if needed.")
    @app_commands.describe(
        method="New verification method",
        question="Custom question to ask users (only needed for custom/review methods)"
    )
    @app_commands.choices(method=[
        app_commands.Choice(name="✅ One-click button",          value="button"),
        app_commands.Choice(name="🎮 Roblox username",            value="roblox"),
        app_commands.Choice(name="🎵 Spotify username",           value="spotify"),
        app_commands.Choice(name="📝 Custom question (auto)",     value="custom"),
        app_commands.Choice(name="👤 Custom question + review",   value="review"),
    ])
    @app_commands.checks.has_permissions(administrator=True)
    async def method_cmd(self, interaction: discord.Interaction, method: str, question: str = None):
        config = await _load_config(self.bot.db.db_path, interaction.guild.id)
        if not config:
            return await interaction.response.send_message(
                embed=error_embed("Not Set Up", "Run `/verify setup` first."),
                ephemeral=True
            )

        updates = {"method": method}
        if question:
            updates["custom_label"] = question
        await _save_config(self.bot.db.db_path, interaction.guild.id, **updates)

        await interaction.response.send_message(
            embed=success_embed(
                "Method Updated",
                f"Verification method changed to **{method}**.\n"
                + (f"Question: **{question}**\n" if question else "")
                + "Run `/verify panel` to repost the panel with the new settings."
            ),
            ephemeral=True
        )

    # ── /verify config ────────────────────────────────────────────────────────
    @app_commands.command(name="config", description="View current verification configuration.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def config_cmd(self, interaction: discord.Interaction):
        config = await _load_config(self.bot.db.db_path, interaction.guild.id)
        if not config:
            return await interaction.response.send_message(
                embed=error_embed("Not Set Up", "Run `/verify setup` first."),
                ephemeral=True
            )

        role_id = config.get("verified_role_id") or config.get("role_id")
        ch_id   = config.get("channel_id")
        log_id  = config.get("log_channel_id")
        role    = interaction.guild.get_role(role_id) if role_id else None
        ch      = interaction.guild.get_channel(ch_id) if ch_id else None
        log_ch  = interaction.guild.get_channel(log_id) if log_id else None
        method  = config.get("method", "button")
        count   = await _count_verified(self.bot.db.db_path, interaction.guild.id)
        pct     = f"{count / max(interaction.guild.member_count, 1) * 100:.1f}%"

        embed = discord.Embed(
            title="🔐  Verification Config",
            color=discord.Color(0x00D4FF),
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="Method",   value=method.title(),                       inline=True)
        embed.add_field(name="Channel",  value=ch.mention if ch else "Not set",      inline=True)
        embed.add_field(name="Role",     value=role.mention if role else "Not set",  inline=True)
        embed.add_field(name="Log",      value=log_ch.mention if log_ch else "None", inline=True)
        embed.add_field(name="Verified", value=f"{count:,} ({pct})",                 inline=True)
        if config.get("custom_label"):
            embed.add_field(name="Question", value=config["custom_label"], inline=False)
        if config.get("kick_unverified"):
            embed.add_field(name="Auto-Kick", value=f"After {config.get('kick_after_hours',24)}h", inline=True)
        embed.set_footer(text=f"{interaction.guild.name}  ·  /verify setup to change settings")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /verify stats ─────────────────────────────────────────────────────────
    @app_commands.command(name="stats", description="Verification statistics for this server.")
    async def stats(self, interaction: discord.Interaction):
        count = await _count_verified(self.bot.db.db_path, interaction.guild.id)
        total = interaction.guild.member_count
        pct   = count / max(total, 1)
        bar   = "█" * int(pct * 20) + "░" * (20 - int(pct * 20))

        embed = discord.Embed(
            title="📊  Verification Stats",
            description=f"`{bar}` **{pct*100:.1f}%** verified",
            color=discord.Color(0x00D4FF),
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="✅ Verified",   value=f"{count:,}",          inline=True)
        embed.add_field(name="❓ Unverified", value=f"{total-count:,}",    inline=True)
        embed.add_field(name="👥 Total",      value=f"{total:,}",          inline=True)
        embed.set_footer(text=f"{interaction.guild.name}  ·  XERO Verification")
        await interaction.response.send_message(embed=embed)

    # ── /verify reset ─────────────────────────────────────────────────────────
    @app_commands.command(name="reset", description="Remove a user's verification (they'll need to re-verify).")
    @app_commands.describe(user="User to un-verify")
    @app_commands.checks.has_permissions(administrator=True)
    async def reset(self, interaction: discord.Interaction, user: discord.Member):
        async with aiosqlite.connect(self.bot.db.db_path) as db:
            try:
                await db.execute(
                    "DELETE FROM verified_members WHERE user_id=? AND guild_id=?",
                    (user.id, interaction.guild.id)
                )
            except Exception:
                pass
            try:
                await db.execute(
                    "DELETE FROM user_verifications WHERE user_id=? AND guild_id=?",
                    (user.id, interaction.guild.id)
                )
            except Exception:
                pass
            await db.commit()

        config = await _load_config(self.bot.db.db_path, interaction.guild.id)
        if config:
            role_id = config.get("verified_role_id") or config.get("role_id")
            role    = interaction.guild.get_role(role_id) if role_id else None
            if role and role in user.roles:
                try:
                    await user.remove_roles(role, reason=f"Verification reset by {interaction.user}")
                except Exception:
                    pass

        await interaction.response.send_message(
            embed=success_embed("Verification Reset", f"{user.mention} has been un-verified and will need to re-verify.")
        )

    # ── /verify kick-config ───────────────────────────────────────────────────
    @app_commands.command(name="kick-config", description="Auto-kick members who don't verify within a time limit.")
    @app_commands.describe(enabled="Enable or disable auto-kick", hours="Hours to wait before kicking (default 24)")
    @app_commands.checks.has_permissions(administrator=True)
    async def kick_config(self, interaction: discord.Interaction, enabled: bool, hours: int = 24):
        await _save_config(
            self.bot.db.db_path, interaction.guild.id,
            kick_unverified=1 if enabled else 0,
            kick_after_hours=hours
        )
        if enabled:
            await interaction.response.send_message(embed=success_embed(
                "Auto-Kick Enabled",
                f"Members who don't verify within **{hours} hours** will be automatically kicked.\n"
                "Bot checks every 30 minutes."
            ))
        else:
            await interaction.response.send_message(embed=info_embed(
                "Auto-Kick Disabled",
                "Members will not be auto-kicked for being unverified."
            ))


async def setup(bot):
    await bot.add_cog(Verification(bot))
