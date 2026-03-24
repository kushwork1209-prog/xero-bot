"""XERO Bot — Unified Branding & Customization"""
import discord
from discord.ext import commands
from discord import app_commands
import aiosqlite
import io
import base64
import logging
from utils.embeds import success_embed, error_embed, info_embed

logger = logging.getLogger("XERO.Branding")

class Branding(commands.GroupCog, name="branding"):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="unified-image", description="Set a single image to be used across all server modules (Tickets, Verification, Welcome, etc.).")
    @app_commands.describe(image="The image to use (upload a file)")
    @app_commands.checks.has_permissions(administrator=True)
    async def unified_image(self, interaction: discord.Interaction, image: discord.Attachment):
        if not image.content_type or not image.content_type.startswith("image/"):
            return await interaction.response.send_message(embed=error_embed("Invalid File", "Please upload a valid image file (PNG, JPG, etc.)."), ephemeral=True)
        
        await interaction.response.defer()
        
        try:
            image_bytes = await image.read()
            # Basic validation
            from PIL import Image
            img = Image.open(io.BytesIO(image_bytes))
            img.verify()
            
            # Convert to base64 for DB storage
            b64_data = base64.b64encode(image_bytes).decode("utf-8")
            
            await self.bot.db.update_guild_setting(interaction.guild.id, "unified_image_data", b64_data)
            
            modules = [
                "Tickets", "Verification", "Giveaways", "Suggestions", 
                "Reaction Roles", "Announcements", "Welcome", "Farewell", "Level-Up"
            ]
            module_list = "\n".join([f"• {m}" for m in modules])
            
            embed = success_embed("Unified Branding Active!", f"This image will now be used across all **{len(modules)}** core modules:\n{module_list}")
            embed.set_image(url=image.url)
            embed.set_footer(text=f"{interaction.guild.name}  •  Unified Branding")
            await interaction.followup.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Unified image upload error: {e}")
            await interaction.followup.send(embed=error_embed("Upload Failed", f"An error occurred while processing the image: {str(e)}"))

    @app_commands.command(name="color", description="Set a custom embed color for the bot in this server.")
    @app_commands.describe(hex_code="Hex color code (e.g. #FF5733)")
    @app_commands.checks.has_permissions(administrator=True)
    async def set_color(self, interaction: discord.Interaction, hex_code: str):
        hex_code = hex_code.lstrip("#")
        if len(hex_code) != 6:
            return await interaction.response.send_message(embed=error_embed("Invalid Color", "Please provide a valid 6-character hex code (e.g. #FF5733)."), ephemeral=True)
        
        try:
            int(hex_code, 16)
        except ValueError:
            return await interaction.response.send_message(embed=error_embed("Invalid Color", "Invalid hex characters provided."), ephemeral=True)
        
        await self.bot.db.update_guild_setting(interaction.guild.id, "embed_color", f"#{hex_code}")
        
        color = discord.Color(int(hex_code, 16))
        embed = discord.Embed(title="Custom Color Set!", description=f"The bot will now use `#{hex_code}` for its embeds in this server.", color=color, timestamp=discord.utils.utcnow())
        embed.set_footer(text=f"{interaction.guild.name}  •  XERO Branding")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="nickname", description="Set a custom nickname for the bot in this server.")
    @app_commands.describe(name="The new nickname (leave blank to reset)")
    @app_commands.checks.has_permissions(administrator=True)
    async def set_nickname(self, interaction: discord.Interaction, name: str = None):
        try:
            await interaction.guild.me.edit(nick=name)
            await self.bot.db.update_guild_setting(interaction.guild.id, "bot_nickname", name)
            
            if name:
                await interaction.response.send_message(embed=success_embed("Nickname Updated", f"I am now known as **{name}** in this server!"))
            else:
                await interaction.response.send_message(embed=success_embed("Nickname Reset", "My nickname has been reset to default."))
        except discord.Forbidden:
            await interaction.response.send_message(embed=error_embed("Permission Denied", "I don't have permission to change my own nickname. Please check my roles."), ephemeral=True)

    @app_commands.command(name="view", description="View your server's branding configuration.")
    async def view_branding(self, interaction: discord.Interaction):
        settings = await self.bot.db.get_guild_settings(interaction.guild.id)
        
        embed = info_embed("Server Branding", f"Customization settings for **{interaction.guild.name}**")
        embed.add_field(name="🎨 Embed Color", value=settings.get("embed_color", "#5865F2"), inline=True)
        embed.add_field(name="🏷️ Bot Nickname", value=settings.get("bot_nickname") or "Default (XERO Bot)", inline=True)
        
        has_image = "Yes" if settings.get("unified_image_data") else "No"
        embed.add_field(name="🖼️ Unified Image", value=has_image, inline=True)
        
        if settings.get("embed_color"):
            try:
                embed.color = discord.Color(int(settings["embed_color"].lstrip("#"), 16))
            except: pass
            
        embed.set_footer(text=f"{interaction.guild.name}  •  XERO Branding")
        embed.timestamp = discord.utils.utcnow()
        await interaction.response.send_message(embed=embed)

async def setup(bot):
    await bot.add_cog(Branding(bot))
