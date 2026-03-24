"""
XERO Bot — Aegis Protocol (Verification & Security)
The most advanced 4-tier verification system on Discord.
Silent Risk Scoring | Custom Questions | Math CAPTCHA | Quarantine & Appeal
"""
import discord
from discord.ext import commands
from discord import app_commands
import logging
import random
import datetime
import asyncio
import aiosqlite
import json
from utils.embeds import (
    success_embed, error_embed, info_embed, comprehensive_embed, 
    brand_embed, XERO
)

logger = logging.getLogger("XERO.Aegis")

# ── Risk Scoring Logic ───────────────────────────────────────────────────────

async def calculate_risk_score(member: discord.Member, bot) -> tuple[int, list]:
    """Calculates a 0-100 risk score based on multiple identity factors."""
    score = 0
    factors = []
    
    # 1. Account Age (Critical)
    age_days = (discord.utils.utcnow() - member.created_at).days
    if age_days < 1:
        score += 50
        factors.append("NEW_ACCOUNT_CRITICAL")
    elif age_days < 7:
        score += 30
        factors.append("NEW_ACCOUNT_HIGH")
    elif age_days < 30:
        score += 15
        factors.append("NEW_ACCOUNT_MED")
        
    # 2. Default Avatar
    if member.avatar is None:
        score += 15
        factors.append("DEFAULT_AVATAR")
        
    # 3. Username Patterns (Basic)
    name = member.name.lower()
    if any(x in name for x in ["bot", "spam", "verify", "click"]):
        score += 10
        factors.append("SUSPICIOUS_USERNAME")
        
    # 4. Cross-Server Ban History (XERO Network)
    async with aiosqlite.connect(bot.db.db_path) as db:
        async with db.execute("SELECT COUNT(*) FROM mod_cases WHERE user_id=? AND action='ban'", (member.id,)) as c:
            row = await c.fetchone()
            ban_count = row[0] if row else 0
            if ban_count > 0:
                score += min(ban_count * 20, 40)
                factors.append(f"NETWORK_BANS_{ban_count}")

    # 5. Discord Flags (if available)
    if member.public_flags.spammer:
        score += 40
        factors.append("DISCORD_SPAMMER_FLAG")

    return min(score, 100), factors

# ── UI Components ────────────────────────────────────────────────────────────

class AegisAppealModal(discord.ui.Modal, title="AEGIS PROTOCOL — APPEAL"):
    appeal = discord.ui.TextInput(
        label="Why should you be granted access?",
        style=discord.ui.TextStyle.paragraph,
        placeholder="Provide details about your identity or why you were flagged.",
        required=True,
        min_length=20,
        max_length=500
    )

    def __init__(self, bot, guild_id):
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message("Your appeal has been submitted to the server staff. Please wait.", ephemeral=True)
        
        settings = await self.bot.db.get_guild_settings(self.guild_id)
        log_ch_id = settings.get("verify_log_channel_id") or settings.get("log_channel_id")
        guild = self.bot.get_guild(self.guild_id)
        
        if log_ch_id and guild:
            log_ch = guild.get_channel(log_ch_id)
            if log_ch:
                embed = comprehensive_embed(
                    title="QUARANTINE APPEAL SUBMITTED",
                    description=f"**User:** {interaction.user.mention}\n**ID:** `{interaction.user.id}`\n\n**Appeal:**\n```\n{self.appeal.value}\n```",
                    color=XERO.WARNING
                )
                view = AegisStaffReviewView(self.bot, interaction.user.id, self.guild_id)
                await log_ch.send(embed=embed, view=view)

class AegisStaffReviewView(discord.ui.View):
    def __init__(self, bot, target_id, guild_id):
        super().__init__(timeout=None)
        self.bot = bot
        self.target_id = target_id
        self.guild_id = guild_id

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.green)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = self.bot.get_guild(self.guild_id)
        member = guild.get_member(self.target_id)
        settings = await self.bot.db.get_guild_settings(self.guild_id)
        
        if member and settings.get("verify_role_id"):
            role = guild.get_role(settings["verify_role_id"])
            q_role = guild.get_role(settings.get("quarantine_role_id"))
            if role:
                await member.add_roles(role, reason=f"Aegis Appeal Approved by {interaction.user}")
                if q_role and q_role in member.roles:
                    await member.remove_roles(q_role)
                
                try: await member.send(embed=success_embed("Access Granted", f"Your appeal in **{guild.name}** was approved."))
                except: pass
                
                await interaction.response.edit_message(content=f"✅ Approved by {interaction.user.mention}", view=None)
            else:
                await interaction.response.send_message("Verify role not found.", ephemeral=True)
        else:
            await interaction.response.send_message("Member not found or role not configured.", ephemeral=True)

    @discord.ui.button(label="Deny & Kick", style=discord.ButtonStyle.grey)
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = self.bot.get_guild(self.guild_id)
        member = guild.get_member(self.target_id)
        if member:
            await member.kick(reason=f"Aegis Appeal Denied by {interaction.user}")
            await interaction.response.edit_message(content=f"❌ Denied & Kicked by {interaction.user.mention}", view=None)

    @discord.ui.button(label="Hard Ban", style=discord.ButtonStyle.red)
    async def ban(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = self.bot.get_guild(self.guild_id)
        member = guild.get_member(self.target_id)
        if member:
            await member.ban(reason=f"Aegis Appeal Denied (Hard Ban) by {interaction.user}")
            await interaction.response.edit_message(content=f"🔨 Banned by {interaction.user.mention}", view=None)

class AegisQuestionModal(discord.ui.Modal, title="AEGIS PROTOCOL — VERIFICATION"):
    answer = discord.ui.TextInput(label="Security Question", placeholder="Enter the answer here...")

    def __init__(self, bot, correct_answer, role_id):
        super().__init__()
        self.bot = bot
        self.correct_answer = correct_answer.lower().strip()
        self.role_id = role_id

    async def on_submit(self, interaction: discord.Interaction):
        if self.answer.value.lower().strip() == self.correct_answer:
            role = interaction.guild.get_role(self.role_id)
            if role:
                await interaction.user.add_roles(role, reason="Aegis Tier 2 Success")
                await interaction.response.send_message(embed=success_embed("Verified", "Access granted. Welcome!"), ephemeral=True)
            else:
                await interaction.response.send_message("Error: Role not found.", ephemeral=True)
        else:
            await interaction.response.send_message(embed=error_embed("Access Denied", "Incorrect answer. Try again."), ephemeral=True)

class AegisMathView(discord.ui.View):
    def __init__(self, bot, role_id):
        super().__init__(timeout=60)
        self.bot = bot
        self.role_id = role_id
        self.a = random.randint(10, 50)
        self.b = random.randint(10, 50)
        self.correct = self.a + self.b
        
        # Create 4 buttons, one correct
        options = [self.correct]
        while len(options) < 4:
            wrong = self.correct + random.randint(-10, 10)
            if wrong != self.correct and wrong not in options:
                options.append(wrong)
        random.shuffle(options)
        
        for opt in options:
            btn = discord.ui.Button(label=str(opt), style=discord.ButtonStyle.secondary, custom_id=f"math_{opt}")
            btn.callback = self.make_callback(opt)
            self.add_item(btn)

    def make_callback(self, val):
        async def callback(interaction: discord.Interaction):
            if val == self.correct:
                role = interaction.guild.get_role(self.role_id)
                if role:
                    await interaction.user.add_roles(role, reason="Aegis Tier 3 Success")
                    await interaction.response.edit_message(embed=success_embed("Verified", "Access granted. Welcome!"), view=None)
                else:
                    await interaction.response.send_message("Error: Role not found.", ephemeral=True)
            else:
                await interaction.response.send_message(embed=error_embed("Access Denied", "Incorrect answer."), ephemeral=True)
        return callback

class AegisVerifyView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="VERIFY IDENTITY", style=discord.ButtonStyle.green, custom_id="aegis_verify_btn")
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        settings = await self.bot.db.get_guild_settings(interaction.guild.id)
        tier = settings.get("verify_tier", 1)
        role_id = settings.get("verify_role_id")
        
        if not role_id:
            return await interaction.response.send_message("Verification role not configured.", ephemeral=True)
            
        # Tier 1: Simple Click (Silent risk check already happened on join)
        if tier == 1:
            role = interaction.guild.get_role(role_id)
            if role:
                await interaction.user.add_roles(role, reason="Aegis Tier 1 Success")
                await interaction.response.send_message(embed=success_embed("Verified", "Identity confirmed. Access granted."), ephemeral=True)
            else:
                await interaction.response.send_message("Error: Role not found.", ephemeral=True)
                
        # Tier 2: Custom Question
        elif tier == 2:
            question = settings.get("verify_question", "What is the server code?")
            answer = settings.get("verify_answer")
            if not answer:
                return await interaction.response.send_message("Security question answer not set by admin.", ephemeral=True)
            modal = AegisQuestionModal(self.bot, answer, role_id)
            modal.answer.label = question[:45]
            await interaction.response.send_modal(modal)
            
        # Tier 3: Math CAPTCHA
        elif tier == 3:
            view = AegisMathView(self.bot, role_id)
            embed = info_embed("SECURITY CHALLENGE", f"Please solve the following to verify:\n## {view.a} + {view.b} = ?")
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            
        # Tier 4: Full Gate (Usually quarantined already, but handle button click)
        elif tier == 4:
            # Check if user is in quarantine
            q_role_id = settings.get("quarantine_role_id")
            if q_role_id:
                q_role = interaction.guild.get_role(q_role_id)
                if q_role and q_role in interaction.user.roles:
                    await interaction.response.send_modal(AegisAppealModal(self.bot, interaction.guild.id))
                else:
                    # Not quarantined, just verify
                    role = interaction.guild.get_role(role_id)
                    if role:
                        await interaction.user.add_roles(role, reason="Aegis Tier 4 Manual Success")
                        await interaction.response.send_message(embed=success_embed("Verified", "Access granted."), ephemeral=True)
                    else:
                        await interaction.response.send_message("Error: Role not found.", ephemeral=True)

# ── Main Cog ─────────────────────────────────────────────────────────────────

class Verification(commands.GroupCog, name="verify"):
    def __init__(self, bot):
        self.bot = bot
        bot.add_view(AegisVerifyView(bot))

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot: return
        
        bot = self.bot
        guild = member.guild
        settings = await bot.db.get_guild_settings(guild.id)
        
        # 1. Silent Risk Score
        score, factors = await calculate_risk_score(member, bot)
        
        # Log join & risk
        log_ch_id = settings.get("verify_log_channel_id") or settings.get("log_channel_id")
        if log_ch_id:
            log_ch = guild.get_channel(log_ch_id)
            if log_ch:
                color = XERO.SUCCESS if score < 40 else (XERO.WARNING if score < 70 else XERO.ERROR)
                embed = comprehensive_embed(
                    title="AEGIS: INBOUND IDENTITY DETECTED",
                    description=f"**User:** {member.mention}\n**ID:** `{member.id}`\n**Risk Score:** `{score}/100`",
                    color=color,
                    fields=[("Risk Factors", f"```\n{', '.join(factors) if factors else 'NONE'}\n```", False)],
                    thumbnail=member.display_avatar.url
                )
                await log_ch.send(embed=embed)

        # 2. Tier Logic
        tier = settings.get("verify_tier", 1)
        
        # Auto-Quarantine if score is high or Tier 4
        if score >= 70 or tier == 4:
            q_role_id = settings.get("quarantine_role_id")
            if q_role_id:
                q_role = guild.get_role(q_role_id)
                if q_role:
                    await member.add_roles(q_role, reason=f"Aegis Quarantine (Risk: {score})")
                    try:
                        embed = error_embed(
                            "ACCESS RESTRICTED", 
                            f"You have been placed in quarantine in **{guild.name}** due to security flags.\n\n"
                            "Please click the **Verify** button in the server to submit an appeal."
                        )
                        await member.send(embed=embed)
                    except: pass

    @app_commands.command(name="setup", description="Initialize the Aegis Protocol verification suite.")
    @app_commands.describe(
        tier="Security level (1-4)", 
        role="Role granted on success", 
        log_channel="Where to send security alerts",
        quarantine_role="Role for restricted users (Tier 4)",
        question="Custom question (Tier 2)",
        answer="Custom answer (Tier 2)",
        use_brand_image="Whether to use the Unified Brand Image (True) or a custom image (False)",
        custom_image="Custom image URL to use for this panel (if use_brand_image is False)"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def setup(self, interaction: discord.Interaction, 
                    tier: int, 
                    role: discord.Role, 
                    log_channel: discord.TextChannel = None,
                    quarantine_role: discord.Role = None,
                    question: str = None,
                    answer: str = None,
                    use_brand_image: bool = True,
                    custom_image: str = None):
        
        if tier not in [1, 2, 3, 4]:
            return await interaction.response.send_message("Invalid tier. Choose 1-4.", ephemeral=True)
            
        await self.bot.db.update_guild_setting(interaction.guild.id, "verify_tier", tier)
        await self.bot.db.update_guild_setting(interaction.guild.id, "verify_role_id", role.id)
        if log_channel: await self.bot.db.update_guild_setting(interaction.guild.id, "verify_log_channel_id", log_channel.id)
        if quarantine_role: await self.bot.db.update_guild_setting(interaction.guild.id, "quarantine_role_id", quarantine_role.id)
        if question: await self.bot.db.update_guild_setting(interaction.guild.id, "verify_question", question)
        if answer: await self.bot.db.update_guild_setting(interaction.guild.id, "verify_answer", answer)

        tier_descs = {
            1: "Click to Verify + Silent Risk Scoring",
            2: "Custom Security Question",
            3: "Math CAPTCHA Challenge",
            4: "Full Gate (Quarantine + Staff Review)"
        }

        embed = comprehensive_embed(
            title="AEGIS PROTOCOL: DEPLOYED",
            description=f"Verification suite has been initialized at **Tier {tier}**.",
            color=XERO.SUCCESS,
            fields=[
                ("Security Mode", tier_descs[tier], True),
                ("Verified Role", role.mention, True),
                ("Alert Channel", log_channel.mention if log_channel else "DEFAULT LOG", True)
            ]
        )
        
        # Post the panel
        panel_embed = comprehensive_embed(
            title="IDENTITY VERIFICATION REQUIRED",
            description=(
                "This server is protected by the **XERO Aegis Protocol**.\n\n"
                "Please click the button below to begin the verification process. "
                "Failure to verify may result in removal."
            ),
            color=XERO.PRIMARY,
            author_name=f"{interaction.guild.name.upper()} — SECURITY GATEWAY",
            author_icon=interaction.guild.icon.url if interaction.guild.icon else None
        )
        
        file = None
        if use_brand_image:
            panel_embed, file = await brand_embed(panel_embed, interaction.guild, self.bot)
        elif custom_image:
            panel_embed.set_image(url=custom_image)
            
        await interaction.channel.send(embed=panel_embed, file=file, view=AegisVerifyView(self.bot))
        await interaction.response.send_message(embed=embed)

async def setup(bot):
    await bot.add_cog(Verification(bot))
