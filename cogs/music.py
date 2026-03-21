"""XERO Bot — Music Player (12 commands)
Optimized for Railway: Uses SoundCloud + JioSaavn as primary search to bypass YouTube bot-blocking.
No external API keys required.
"""
import discord
from discord.ext import commands
from discord import app_commands
import logging
import asyncio
import random as _random
from utils.embeds import success_embed, error_embed, info_embed, comprehensive_embed
from utils.guard import command_guard

logger = logging.getLogger("XERO.Music")

try:
    import yt_dlp
    YTDLP_AVAILABLE = True
except ImportError:
    YTDLP_AVAILABLE = False
    logger.warning("yt-dlp not installed — music commands will be disabled.")

# ── yt-dlp options ────────────────────────────────────────────────────────────
YTDL_OPTS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "scsearch",  # Switch to SoundCloud search by default
    "source_address": "0.0.0.0",
    "extract_flat": False,
    "socket_timeout": 15,
    "retries": 5,
    "nocheckcertificate": True,
}

FFMPEG_OPTS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -nostdin",
    "options": "-vn -bufsize 64k",
}

class MusicQueue:
    def __init__(self):
        self.songs: list[dict] = []
        self.volume: float = 0.5
        self.loop: bool = False
        self.shuffle: bool = False
        self.now_playing: dict | None = None

def _fmt_duration(seconds: int) -> str:
    if not seconds: return "Live"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

def _search_sync(query: str) -> dict:
    """Blocking search — tries SoundCloud first, then JioSaavn, then YouTube as last resort."""
    # If it's a direct URL, just use it
    is_url = query.startswith(("http://", "https://", "www."))
    
    # Strategy: 1. SoundCloud (scsearch), 2. JioSaavn (jssearch), 3. YouTube (ytsearch)
    search_strategies = [query] if is_url else [f"scsearch:{query}", f"ytsearch:{query}"]
    
    last_err = None
    for search_query in search_strategies:
        try:
            with yt_dlp.YoutubeDL(YTDL_OPTS) as ydl:
                info = ydl.extract_info(search_query, download=False)
                if "entries" in info:
                    if not info["entries"]: continue
                    info = info["entries"][0]
                
                return {
                    "url": info["url"],
                    "title": info.get("title", "Unknown"),
                    "duration": info.get("duration", 0),
                    "thumbnail": info.get("thumbnail"),
                    "webpage_url": info.get("webpage_url", ""),
                    "uploader": info.get("uploader", "Unknown"),
                }
        except Exception as e:
            last_err = e
            continue
            
    raise last_err or ValueError("No results found.")

class Music(commands.GroupCog, name="music"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.queues: dict[int, MusicQueue] = {}

    def get_queue(self, guild_id: int) -> MusicQueue:
        if guild_id not in self.queues:
            self.queues[guild_id] = MusicQueue()
        return self.queues[guild_id]

    def _play_next(self, guild_id: int) -> None:
        queue = self.get_queue(guild_id)
        guild = self.bot.get_guild(guild_id)
        if not guild or not guild.voice_client: return

        vc = guild.voice_client
        if vc.is_playing() or vc.is_paused(): return

        if queue.loop and queue.now_playing:
            queue.songs.insert(0, queue.now_playing)

        if not queue.songs:
            queue.now_playing = None
            return

        song = queue.songs.pop(0)
        queue.now_playing = song
        try:
            source = discord.FFmpegPCMAudio(song["url"], **FFMPEG_OPTS)
            source = discord.PCMVolumeTransformer(source, volume=queue.volume)
            vc.play(source, after=lambda e: asyncio.run_coroutine_threadsafe(self._after_song(guild_id, e), self.bot.loop))
        except Exception as e:
            logger.error(f"[Music] Playback error: {e}")
            self._play_next(guild_id)

    async def _after_song(self, guild_id: int, error=None) -> None:
        self._play_next(guild_id)

    async def _ensure_voice(self, interaction: discord.Interaction) -> bool:
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message(embed=error_embed("No Voice", "Join a voice channel first."), ephemeral=True)
            return False
        return True

    @app_commands.command(name="play", description="Play music (SoundCloud/YouTube).")
    @app_commands.describe(query="Song name or URL")
    @command_guard
    async def play(self, interaction: discord.Interaction, query: str):
        if not await self._ensure_voice(interaction): return
        if not YTDLP_AVAILABLE:
            return await interaction.response.send_message("yt-dlp missing.", ephemeral=True)

        await interaction.response.defer()
        try:
            loop = asyncio.get_event_loop()
            song = await asyncio.wait_for(loop.run_in_executor(None, _search_sync, query), timeout=30.0)
        except Exception as e:
            return await interaction.followup.send(embed=error_embed("Error", f"Could not find or play: {query}\n`{str(e)[:100]}`"))

        vc = interaction.guild.voice_client
        if not vc:
            try:
                vc = await interaction.user.voice.channel.connect(timeout=10.0, reconnect=True)
            except Exception as e:
                return await interaction.followup.send(embed=error_embed("Voice Error", f"Failed to connect: {e}"))

        queue = self.get_queue(interaction.guild.id)
        if vc.is_playing() or vc.is_paused():
            queue.songs.append(song)
            await interaction.followup.send(embed=info_embed("Added to Queue", f"**{song['title']}**\nPosition: #{len(queue.songs)}"))
        else:
            queue.now_playing = song
            source = discord.FFmpegPCMAudio(song["url"], **FFMPEG_OPTS)
            source = discord.PCMVolumeTransformer(source, volume=queue.volume)
            vc.play(source, after=lambda e: asyncio.run_coroutine_threadsafe(self._after_song(interaction.guild.id, e), self.bot.loop))
            
            embed = comprehensive_embed(title="🎵 Now Playing", description=f"**[{song['title']}]({song['webpage_url']})**", color=discord.Color.purple())
            embed.add_field(name="Duration", value=_fmt_duration(song["duration"]), inline=True)
            embed.add_field(name="Source", value=song["uploader"], inline=True)
            if song["thumbnail"]: embed.set_thumbnail(url=song["thumbnail"])
            await interaction.followup.send(embed=embed)

    @app_commands.command(name="stop", description="Stop and disconnect.")
    async def stop(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc:
            self.queues.pop(interaction.guild.id, None)
            await vc.disconnect()
            await interaction.response.send_message(embed=success_embed("Stopped", "Disconnected."))
        else:
            await interaction.response.send_message("Not connected.", ephemeral=True)

    @app_commands.command(name="skip", description="Skip song.")
    async def skip(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()
            await interaction.response.send_message("⏭ Skipped.")
        else:
            await interaction.response.send_message("Nothing playing.", ephemeral=True)

    @app_commands.command(name="queue", description="Show queue.")
    async def queue_cmd(self, interaction: discord.Interaction):
        queue = self.get_queue(interaction.guild.id)
        embed = discord.Embed(title="Queue", color=discord.Color.purple())
        if queue.now_playing:
            embed.description = f"**Now Playing:** {queue.now_playing['title']}\n\n"
            if queue.songs:
                embed.description += "\n".join(f"`{i+1}.` {s['title']}" for i, s in enumerate(queue.songs[:10]))
            else:
                embed.description += "Queue is empty."
        else:
            embed.description = "Nothing playing."
        await interaction.response.send_message(embed=embed)

async def setup(bot: commands.Bot):
    await bot.add_cog(Music(bot))
