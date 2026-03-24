"""XERO Bot — Verification System (6 commands)"""
import discord
from discord.ext import commands
from discord import app_commands
import logging
import aiosqlite
from utils.embeds import success_embed, error_embed, info_embed, comprehensive_embed, comprehensive_embed

logger = logging.getLogger("XERO.Verification")


class VerifyButton(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="✅ Verify", style=discord.ButtonStyle.green, custom_id="nexus_verify_btn")
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with aiosqlite.connect(interaction.client.db.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM verification_config WHERE guild_id=?", (interaction.guild.id,)) as c:
                config = await c.fetchone()
        if not config:
            return await interaction.response.send_message("⚠️ Verification not configured. Ask an admin to run `/verify setup`.", ephemeral=True)
        role = interaction.guild.get_role(config["role_id"])
        if not role:
            return await interaction.response.send_message("⚠️ Verification role not found. Please contact an admin.", ephemeral=True)
        if role in interaction.user.roles:
            return await interaction.response.send_message("You are already verified! ✅", ephemeral=True)
        try:
            await interaction.user.add_roles(role, reason="XERO Verification")
            async with aiosqlite.connect(interaction.client.db.db_path) as db:
                await db.execute(
                    "INSERT OR IGNORE INTO user_verifications (user_id, guild_id) VALUES (?,?)",
                    (interaction.user.id, interaction.guild.id)
                )
                await db.commit()
            embed = success_embed("Verification Complete!", f"Welcome to **{interaction.guild.name}**!\nYou've been given the {role.mention} role.")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            logger.info(f"Verified: {interaction.user} in {interaction.guild.name}")
        except discord.Forbidden:
            await interaction.response.send_message("❌ I don't have permission to assign that role. Please contact an admin.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Verification failed: {str(e)}", ephemeral=True)


class Verification(commands.GroupCog, name="verify"):
    def __init__(self, bot):
        self.bot = bot
        bot.add_view(VerifyButton())  # Persistent view

    @app_commands.command(name="setup", description="Set up the verification system for your server.")
    @app_commands.describe(channel="Channel to send the verification message", role="Role to grant upon verification", message="Custom verification message")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup(self, interaction: discord.Interaction, channel: discord.TextChannel, role: discord.Role, message: str = None):
        msg = message or f"Welcome to **{interaction.guild.name}**! Click the button below to verify and gain access."
        async with aiosqlite.connect(self.bot.db.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO verification_config (guild_id, channel_id, role_id, message) VALUES (?,?,?,?)",
                (interaction.guild.id, channel.id, role.id, msg)
            )
            await db.commit()
        await self.bot.db.update_guild_setting(interaction.guild.id, "verify_role_id", role.id)
        await self.bot.db.update_guild_setting(interaction.guild.id, "verify_channel_id", channel.id)
        from utils.embeds import brand_embed, comprehensive_embed
        embed = comprehensive_embed(title="✅ Verification Required", description=msg, color=discord.Color.green())
        embed.add_field(name="Instructions", value="1. Click **Verify** below\n2. You'll receive access instantly!", inline=False)
        embed.set_footer(text="Verification")
        if interaction.guild.icon:
            embed.set_thumbnail(url=interaction.guild.icon.url)
        
        # Unified Branding
        embed, file = await brand_embed(embed, interaction.guild, self.bot)
        if file:
            await channel.send(embed=embed, file=file, view=VerifyButton())
        else:
            await channel.send(embed=embed, view=VerifyButton())
        await interaction.response.send_message(embed=success_embed("Verification Setup Complete!", f"Verification panel posted in {channel.mention}.\n**Role:** {role.mention}"))

    @app_commands.command(name="config", description="View current verification configuration.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def config(self, interaction: discord.Interaction):
        async with aiosqlite.connect(self.bot.db.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM verification_config WHERE guild_id=?", (interaction.guild.id,)) as c:
                conf = await c.fetchone()
        if not conf:
            return await interaction.response.send_message(embed=error_embed("Not Configured", "Verification not set up. Use `/verify setup` first."), ephemeral=True)
        conf = dict(conf)
        ch = interaction.guild.get_channel(conf["channel_id"])
        role = interaction.guild.get_role(conf["role_id"])
        embed = info_embed("Verification Configuration", "Current setup:")
        embed.add_field(name="Channel", value=ch.mention if ch else "Not found", inline=True)
        embed.add_field(name="Role", value=role.mention if role else "Not found", inline=True)
        embed.add_field(name="Message", value=conf["message"][:500], inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="update-message", description="Update the verification panel message.")
    @app_commands.describe(message="New verification message")
    @app_commands.checks.has_permissions(administrator=True)
    async def update_message(self, interaction: discord.Interaction, message: str):
        async with aiosqlite.connect(self.bot.db.db_path) as db:
            await db.execute("UPDATE verification_config SET message=? WHERE guild_id=?", (message, interaction.guild.id))
            await db.commit()
        await interaction.response.send_message(embed=success_embed("Message Updated", f"New message: **{message}**\nUse `/verify setup` to repost the panel."))

    @app_commands.command(name="update-role", description="Change the role granted upon verification.")
    @app_commands.describe(role="New verification role")
    @app_commands.checks.has_permissions(administrator=True)
    async def update_role(self, interaction: discord.Interaction, role: discord.Role):
        async with aiosqlite.connect(self.bot.db.db_path) as db:
            await db.execute("UPDATE verification_config SET role_id=? WHERE guild_id=?", (role.id, interaction.guild.id))
            await db.commit()
        await self.bot.db.update_guild_setting(interaction.guild.id, "verify_role_id", role.id)
        await interaction.response.send_message(embed=success_embed("Role Updated", f"Verification role changed to {role.mention}."))

    @app_commands.command(name="stats", description="View verification statistics for this server.")
    async def stats(self, interaction: discord.Interaction):
        async with aiosqlite.connect(self.bot.db.db_path) as db:
            async with db.execute("SELECT COUNT(*) FROM user_verifications WHERE guild_id=?", (interaction.guild.id,)) as c:
                count = (await c.fetchone())[0]
        pct = f"{count / max(interaction.guild.member_count, 1) * 100:.1f}%"
        embed = info_embed("Verification Statistics", f"**Total Verified Members:** {count:,}\n**Verification Rate:** {pct}\n**Total Members:** {interaction.guild.member_count:,}")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="reset", description="Remove a user's verified status (they'll need to re-verify).")
    @app_commands.describe(user="User to un-verify")
    @app_commands.checks.has_permissions(administrator=True)
    async def reset(self, interaction: discord.Interaction, user: discord.Member):
        async with aiosqlite.connect(self.bot.db.db_path) as db:
            await db.execute("DELETE FROM user_verifications WHERE user_id=? AND guild_id=?", (user.id, interaction.guild.id))
            await db.commit()
        settings = await self.bot.db.get_guild_settings(interaction.guild.id)
        if settings.get("verify_role_id"):
            role = interaction.guild.get_role(settings["verify_role_id"])
            if role and role in user.roles:
                try:
                    await user.remove_roles(role, reason="Verification reset by admin")
                except Exception:
                    pass
        await interaction.response.send_message(embed=success_embed("Verification Reset", f"{user.mention} has been un-verified."))


async def setup(bot):
    await bot.add_cog(Verification(bot))
