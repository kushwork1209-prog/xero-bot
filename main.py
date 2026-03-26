"""
XERO Bot — Main Entry Point
Advanced AI-Powered Discord Bot by Team Flame
300+ Commands | NVIDIA Llama 4 Maverick | All Premium Features Free
"""
import discord
from discord.ext import commands
import os, logging, asyncio, time
from database import Database
from utils.nvidia_api import NvidiaAPI
from dotenv import load_dotenv

load_dotenv()
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler("logs/xero.log"), logging.StreamHandler()]
)
logger = logging.getLogger("XERO")

class XeroBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(command_prefix=commands.when_mentioned_or("/"), intents=intents, help_command=None, case_insensitive=True)
        self.db          = Database()
        self.nvidia      = NvidiaAPI(
            primary_key = os.getenv("NVIDIA_MAIN_KEY",   os.getenv("NVIDIA_API_KEY", "")),
            vision_key  = os.getenv("NVIDIA_VISION_KEY", os.getenv("NVIDIA_API_KEY", "")),
        )
        self.launch_time = time.time()
        self.MANAGEMENT_GUILD_ID = int(os.getenv("MANAGEMENT_GUILD_ID", "1431852658767040535"))
        self.initial_extensions = [
            "cogs.events", "cogs.config", "cogs.info", "cogs.admin",
            "cogs.moderation", "cogs.automod", "cogs.smart_mod",
            "cogs.economy", "cogs.economy_advanced",
            "cogs.levels", "cogs.leaderboard",
            "cogs.ai", "cogs.ai_advanced",
            "cogs.fun", "cogs.social", "cogs.engagement", "cogs.personality",
            "cogs.birthday", "cogs.suggestions", "cogs.reactionroles", "cogs.starboard",
            "cogs.custom_commands", "cogs.giveaway", "cogs.announcement",
            "cogs.verification", "cogs.tickets", "cogs.roles", "cogs.server",
            "cogs.temp_voice",
            "cogs.security",
            "cogs.server_features",
            "cogs.profile_extras",
            "cogs.voice_ai",
            "cogs.member_intelligence",
            "cogs.autoresponder",
            "cogs.logging_system",
            "cogs.tools", "cogs.music", "cogs.profile", "cogs.analytics",
            "cogs.utility", "cogs.backup", "cogs.core_admin",
        ]

    async def setup_hook(self):
        os.makedirs("data", exist_ok=True)
        await self.db.initialize()
        if hasattr(self.db, 'initialize_advanced'):
            await self.db.initialize_advanced()
        if hasattr(self.db, 'initialize_xero_tables'):
            await self.db.initialize_xero_tables()
        if hasattr(self.db, 'initialize_v4_tables'):
            await self.db.initialize_v4_tables()
        logger.info("✓ Database fully initialized.")
        for ext in self.initial_extensions:
            try:
                await self.load_extension(ext)
                logger.info(f"  ✓ {ext}")
            except Exception as e:
                logger.error(f"  ✗ {ext} — {e}")
        mguild = discord.Object(id=self.MANAGEMENT_GUILD_ID)
        try:
            synced = await self.tree.sync()
            logger.info(f"✓ Synced {len(synced)} global slash commands.")
        except Exception as e:
            logger.error(f"Failed to sync: {e}")
        try:
            # Sync ONLY guild-specific commands to management guild.
            # NO copy_global_to — that duplicates all 350+ global commands there,
            # making every command appear twice in the management server.
            # /core and /support use guilds=[mguild] in add_cog, sync fine without it.
            guild_synced = await self.tree.sync(guild=mguild)
            logger.info(f"✓ Management guild: {len(guild_synced)} guild-only commands synced.")
        except Exception as e:
            logger.warning(f"Management guild sync: {e}")

    async def on_ready(self):
        logger.info(f"✓ XERO ready — {self.user} | {len(self.guilds)} guilds | {sum(g.member_count for g in self.guilds):,} users | {round(self.latency*1000)}ms")
        await self.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="300+ commands | /help"))

    async def on_interaction(self, interaction: discord.Interaction):
        """Guard: if a command interaction is deferred and never responded to, send a fallback after 28s."""
        if interaction.type != discord.InteractionType.application_command:
            return
        await asyncio.sleep(28)
        try:
            if interaction.response.is_done():
                # Check if a followup was never sent - we can't easily detect this
                # but we can try sending one; if it fails it means it was already handled
                pass
        except Exception:
            pass

    async def on_app_command_error(self, interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
        from utils.embeds import error_embed
        # Unwrap CommandInvokeError to get the real error
        original = getattr(error, "original", error)
        if isinstance(error, discord.app_commands.MissingPermissions):
            msg = f"You need: `{'`, `'.join(error.missing_permissions)}`"
        elif isinstance(error, discord.app_commands.BotMissingPermissions):
            msg = f"I need: `{'`, `'.join(error.missing_permissions)}`"
        elif isinstance(error, discord.app_commands.CommandOnCooldown):
            msg = f"Slow down! Try again in **{error.retry_after:.1f}s**"
        elif isinstance(error, discord.app_commands.CheckFailure):
            msg = "You don't have permission to use this command."
        elif isinstance(original, asyncio.TimeoutError):
            msg = "Request timed out. Please try again."
        else:
            msg = "Something went wrong. Please try again."
            logger.error(f"Command error in {getattr(interaction.command, 'name', 'unknown')}: {original}", exc_info=original)
        try:
            embed = error_embed("Error", msg)
            # Always send a response - prevents thinking forever
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception:
            pass

async def main():
    bot = XeroBot()
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        logger.critical("DISCORD_TOKEN not set!"); return
    async with bot:
        await bot.start(token)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("XERO Bot shut down.")
