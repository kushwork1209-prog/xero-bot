from utils.embeds import brand_embed
"""XERO Bot — AutoMod System (8 commands)"""
import discord
from discord.ext import commands
from discord import app_commands
import logging
import aiosqlite
from utils.embeds import success_embed, error_embed, info_embed, comprehensive_embed

logger = logging.getLogger("XERO.AutoMod")


class AutoMod(commands.GroupCog, name="automod"):
    def __init__(self, bot):
        self.bot = bot

    async def get_config(self, guild_id: int) -> dict:
        async with aiosqlite.connect(self.bot.db.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM automod_config WHERE guild_id=?", (guild_id,)) as c:
                row = await c.fetchone()
        if row:
            return dict(row)
        return {"guild_id": guild_id, "enabled": 0, "anti_spam": 0, "anti_links": 0,
                "anti_caps": 0, "anti_profanity": 0, "max_mentions": 5, "spam_threshold": 5,
                "log_channel_id": None, "action": "delete"}

    async def save_config(self, guild_id: int, **kwargs):
        async with aiosqlite.connect(self.bot.db.db_path) as db:
            existing = await self.get_config(guild_id)
            if existing.get("guild_id"):
                sets = ", ".join(f"{k}=?" for k in kwargs)
                await db.execute(f"UPDATE automod_config SET {sets} WHERE guild_id=?", (*kwargs.values(), guild_id))
            else:
                await db.execute("INSERT INTO automod_config (guild_id) VALUES (?)", (guild_id,))
                sets = ", ".join(f"{k}=?" for k in kwargs)
                await db.execute(f"UPDATE automod_config SET {sets} WHERE guild_id=?", (*kwargs.values(), guild_id))
            await db.commit()

    @app_commands.command(name="setup", description="Initial AutoMod configuration with recommended settings.")
    @app_commands.describe(log_channel="Channel to log automod actions")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup(self, interaction: discord.Interaction, log_channel: discord.TextChannel = None):
        await self.save_config(
            interaction.guild.id,
            enabled=1, anti_spam=1, anti_links=0, anti_caps=1,
            anti_profanity=1, max_mentions=5, spam_threshold=5, action="delete",
            log_channel_id=log_channel.id if log_channel else None
        )
        # Seed default profanity
        default_words = ["fuck", "niga", "niger", "nigger", "bitch", "asshole", "shit"]
        async with aiosqlite.connect(self.bot.db.db_path) as db:
            for word in default_words:
                await db.execute("INSERT OR IGNORE INTO automod_filters (guild_id, filter_type, value) VALUES (?,?,?)",
                                 (interaction.guild.id, "word", word))
            await db.commit()

        embed = success_embed("AutoMod Configured!", "Recommended settings applied:")
        embed.add_field(name="✅ Enabled", value="Anti-Spam, Anti-Caps, Anti-Profanity (AI-Powered)", inline=False)
        embed.add_field(name="📋 Settings", value=f"Max Mentions: 5 | Spam Threshold: 5 messages", inline=False)
        embed.add_field(name="🛡️ AI Guard", value="AI is now monitoring chat for harsh language and context.", inline=False)
        if log_channel:
            embed.add_field(name="📢 Log Channel", value=log_channel.mention, inline=False)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="toggle", description="Enable or disable AutoMod entirely.")
    @app_commands.describe(enabled="Enable or disable AutoMod")
    @app_commands.checks.has_permissions(administrator=True)
    async def toggle(self, interaction: discord.Interaction, enabled: bool):
        await self.save_config(interaction.guild.id, enabled=1 if enabled else 0)
        await self.bot.db.update_guild_setting(interaction.guild.id, "automod_enabled", 1 if enabled else 0)
        status = "enabled" if enabled else "disabled"
        await interaction.response.send_message(embed=success_embed(f"AutoMod {status.capitalize()}", f"AutoMod is now **{status}**."))

    @app_commands.command(name="anti-spam", description="Configure anti-spam detection settings.")
    @app_commands.describe(enabled="Enable or disable", threshold="Number of messages in 5 seconds to trigger (3-20)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def anti_spam(self, interaction: discord.Interaction, enabled: bool, threshold: int = 5):
        threshold = max(3, min(20, threshold))
        await self.save_config(interaction.guild.id, anti_spam=1 if enabled else 0, spam_threshold=threshold)
        await interaction.response.send_message(embed=success_embed("Anti-Spam Updated", f"Anti-Spam: **{'enabled' if enabled else 'disabled'}** | Threshold: **{threshold} msgs/5s**"))

    @app_commands.command(name="anti-links", description="Block external links in messages.")
    @app_commands.describe(enabled="Enable or disable link filtering")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def anti_links(self, interaction: discord.Interaction, enabled: bool):
        await self.save_config(interaction.guild.id, anti_links=1 if enabled else 0)
        await interaction.response.send_message(embed=success_embed("Anti-Links Updated", f"Link filtering: **{'enabled' if enabled else 'disabled'}**"))

    @app_commands.command(name="anti-caps", description="Flag messages that are mostly uppercase.")
    @app_commands.describe(enabled="Enable or disable caps filtering")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def anti_caps(self, interaction: discord.Interaction, enabled: bool):
        await self.save_config(interaction.guild.id, anti_caps=1 if enabled else 0)
        await interaction.response.send_message(embed=success_embed("Anti-Caps Updated", f"Caps filtering: **{'enabled' if enabled else 'disabled'}**"))

    @app_commands.command(name="add-filter", description="Add a word or phrase to the AutoMod filter.")
    @app_commands.describe(word="Word or phrase to filter")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def add_filter(self, interaction: discord.Interaction, word: str):
        async with aiosqlite.connect(self.bot.db.db_path) as db:
            await db.execute("INSERT INTO automod_filters (guild_id, filter_type, value) VALUES (?,?,?)",
                             (interaction.guild.id, "word", word.lower()))
            await db.commit()
        await interaction.response.send_message(embed=success_embed("Filter Added", f"Added `{word}` to the word filter."))

    @app_commands.command(name="remove-filter", description="Remove a word from the AutoMod filter.")
    @app_commands.describe(word="Word to remove from filters")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def remove_filter(self, interaction: discord.Interaction, word: str):
        async with aiosqlite.connect(self.bot.db.db_path) as db:
            await db.execute("DELETE FROM automod_filters WHERE guild_id=? AND value=?", (interaction.guild.id, word.lower()))
            await db.commit()
        await interaction.response.send_message(embed=success_embed("Filter Removed", f"Removed `{word}` from the word filter."))

    @app_commands.command(name="list-filters", description="View all active AutoMod filters for this server.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def list_filters(self, interaction: discord.Interaction):
        config = await self.get_config(interaction.guild.id)
        async with aiosqlite.connect(self.bot.db.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM automod_filters WHERE guild_id=?", (interaction.guild.id,)) as c:
                filters = [dict(r) for r in await c.fetchall()]

        def toggle(val): return "✅" if val else "❌"
        embed = comprehensive_embed(title="🛡️ AutoMod Configuration", color=XERO.PRIMARY,)
        embed.add_field(name="Status", value=(
            f"**AutoMod:** {toggle(config.get('enabled'))} {'Enabled' if config.get('enabled') else 'Disabled'}\n"
            f"**Anti-Spam:** {toggle(config.get('anti_spam'))} (threshold: {config.get('spam_threshold', 5)})\n"
            f"**Anti-Links:** {toggle(config.get('anti_links'))}\n"
            f"**Anti-Caps:** {toggle(config.get('anti_caps'))}\n"
            f"**Anti-Profanity:** {toggle(config.get('anti_profanity'))}"
        ), inline=False)
        if filters:
            words = [f"`{f['value']}`" for f in filters[:20]]
            embed.add_field(name=f"🚫 Filtered Words ({len(filters)})", value=", ".join(words), inline=False)
        else:
            embed.add_field(name="🚫 Filtered Words", value="None added yet. Use `/automod add-filter` to add.", inline=False)
        await interaction.response.send_message(embed=embed)


    async def process_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        
        config = await self.get_config(message.guild.id)
        if not config.get("enabled"):
            return
        
        # ── Anti-Profanity & AI Guard ──────────────────────────────────────
        if config.get("anti_profanity"):
            content = message.content.lower()
            triggered_word = None
            
            # 1. Check local filters
            async with aiosqlite.connect(self.bot.db.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute("SELECT value FROM automod_filters WHERE guild_id=? AND filter_type='word'", (message.guild.id,)) as c:
                    filters = [r["value"] for r in await c.fetchall()]
            
            for word in filters:
                if word in content:
                    triggered_word = word
                    break
            
            # 2. AI Context Check (if no word triggered, or to confirm)
            is_harsh = False
            if not triggered_word:
                # Only run AI if message is long enough or contains suspicious patterns
                if len(message.content) > 10:
                    try:
                        prompt = f"Analyze if this message is toxic, contains harsh profanity, or racial slurs. Reply only with 'YES' or 'NO'.\nMessage: {message.content}"
                        response = await self.bot.nvidia.ask(prompt)
                        if "YES" in response.upper():
                            is_harsh = True
                            triggered_word = "AI Detection"
                    except Exception:
                        pass
            else:
                is_harsh = True

            if is_harsh:
                try:
                    await message.delete()
                    await message.channel.send(f"{message.author.mention}, you cannot say that! Please follow the guidelines.", delete_after=5)
                    
                    # Add Soft Warning
                    reason = f"AutoMod: Harsh language detected ({triggered_word})"
                    await self.bot.db.add_warning(message.guild.id, message.author.id, self.bot.user.id, reason, warn_type="soft")
                    
                    # DM User
                    try:
                        dm_embed = discord.Embed(title="⚠️ Software Warning", color=XERO.PRIMARY,)
                        dm_embed, file = await brand_embed(dm_embed, guild, bot)
                        dm_embed, file = await brand_embed(dm_embed, guild, bot)
                        dm_embed.description = f"Yo, I detected you saying something harsh in **{message.guild.name}**.\n\n**Message:** {message.content}\n**Reason:** {reason}\n\nPlease follow the community guidelines to avoid further action."
                        await message.author.send(embed=dm_embed)
                    except Exception: pass
                    
                    # Log Action
                    if config.get("log_channel_id"):
                        log_ch = message.guild.get_channel(config["log_channel_id"])
                        if log_ch:
                            log_embed = discord.Embed(title="🛡️ AutoMod Action", color=XERO.PRIMARY, timestamp=discord.utils.utcnow())
                            log_embed, file = await brand_embed(log_embed, guild, bot)
                            log_embed, file = await brand_embed(log_embed, guild, bot)
                            log_embed.add_field(name="User", value=f"{message.author.mention} ({message.author.id})")
                            log_embed.add_field(name="Action", value="Message Deleted + Soft Warning")
                            log_embed.add_field(name="Reason", value=reason)
                            log_embed.add_field(name="Original Message", value=message.content[:1024], inline=False)
                            await log_ch.send(embed=log_embed)
                    return True
                except Exception as e:
                    logger.error(f"AutoMod error: {e}")
        return False

async def setup(bot):
    await bot.add_cog(AutoMod(bot))
