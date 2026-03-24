"""
XERO Bot — Core Administration
Management-only commands for bot control, syncing, and system health.
"""
import discord
from discord.ext import commands
from discord import app_commands
import logging
import time
import os
import aiosqlite
import psutil
from utils.embeds import success_embed, error_embed, info_embed, comprehensive_embed, XERO

logger = logging.getLogger("XERO.Core")

def is_management():
    def predicate(interaction: discord.Interaction):
        return interaction.user.id in [1124403755459346514, 1104381387601199144] # Add IDs as needed
    return app_commands.check(predicate)

async def _ensure_mgmt_tables(db_path):
    async with aiosqlite.connect(db_path) as db:
        await db.execute("CREATE TABLE IF NOT EXISTS mgmt_logs (id INTEGER PRIMARY KEY, action TEXT, user_id INTEGER, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)")
        await db.commit()

class CoreAdmin(commands.GroupCog, name="core"):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="sync", description="FORCE SYNC: Manually push the entire command tree to Discord API.")
    @is_management()
    async def sync(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            # Sync global commands
            synced = await self.bot.tree.sync()
            # Sync management guild commands
            mguild = discord.Object(id=self.bot.MANAGEMENT_GUILD_ID)
            g_synced = await self.bot.tree.sync(guild=mguild)
            
            msg = (
                f"✅ **Force Sync Complete**\n"
                f"▹ Global Commands: `{len(synced)}` synced\n"
                f"▹ Management Guild: `{len(g_synced)}` synced\n\n"
                f"*Note: Discord can take up to 1 hour to update the client cache.*"
            )
            await interaction.followup.send(embed=success_embed("System Sync", msg), ephemeral=True)
        except Exception as e:
            await interaction.followup.send(embed=error_embed("Sync Failed", f"```py\n{e}\n```"), ephemeral=True)

    @app_commands.command(name="stats", description="View bot system performance and health.")
    @is_management()
    async def stats(self, interaction: discord.Interaction):
        process = psutil.Process(os.getpid())
        mem = process.memory_info().rss / 1024 / 1024
        cpu = psutil.cpu_percent()
        uptime = time.time() - self.bot.launch_time
        
        embed = comprehensive_embed(
            title="XERO SYSTEM HEALTH",
            color=XERO.PRIMARY,
            fields=[
                ("Memory", f"{mem:.2f} MB", True),
                ("CPU", f"{cpu}%", True),
                ("Latency", f"{round(self.bot.latency * 1000)}ms", True),
                ("Uptime", f"{int(uptime // 3600)}h {int((uptime % 3600) // 60)}m", True),
                ("Guilds", str(len(self.bot.guilds)), True),
                ("Users", f"{sum(g.member_count for g in self.bot.guilds):,}", True)
            ]
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

async def setup(bot):
    await bot.add_cog(CoreAdmin(bot))
