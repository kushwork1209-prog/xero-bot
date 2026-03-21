"""XERO Bot — Music Player (12 commands)
Fully self-contained: yt-dlp + FFmpeg + PyNaCl only — no external music API keys needed.
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
    logger.warning("yt-dlp not installed — music commands will be disabled. Run: pip install yt-dlp")

# ── yt-dlp options ────────────────────────────────────────────────────────────
YTDL_OPTS = {
    "format": "bestaudio[ext=webm]/bestaudio[ext=m4a]/bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
    "extract_flat": False,
    "socket_timeout": 15,
    "retries": 3,
    # Prefer opus codec for lower CPU usage in Discord
    "postprocessors": [],
}

# ── FFmpeg options ─────────────────────────────────────────────────────────────
FFMPEG_OPTS = {
    "before_options": (
        "-reconnect 1 "
        "-reconnect_streamed 1 "
        "-reconnect_delay_max 5 "
        "-nostdin"
    ),
    "options": "-vn -bufsize 64k",
}


# ── Queue state ───────────────────────────────────────────────────────────────
class MusicQueue:
    def __init__(self):
        self.songs: list[dict] = []
        self.volume: float = 0.5
        self.loop: bool = False
        self.shuffle: bool = False
        self.now_playing: dict | None = None


# ── Helpers ───────────────────────────────────────────────────────────────────
def _fmt_duration(seconds: int) -> str:
    if not seconds:
        return "Live"
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _search_sync(query: str) -> dict:
    """Blocking yt-dlp search — run in executor."""
    with yt_dlp.YoutubeDL(YTDL_OPTS) as ydl:
        is_url = query.startswith(("http://", "https://", "www."))
        search_query = query if is_url else f"ytsearch:{query}"
        info = ydl.extract_info(search_query, download=False)
        if "entries" in info:
            info = info["entries"][0]
        if not info:
            raise ValueError("No results found.")
        return {
            "url": info["url"],
            "title": info.get("title", "Unknown"),
            "duration": info.get("duration", 0),
            "thumbnail": info.get("thumbnail"),
            "webpage_url": info.get("webpage_url", ""),
            "uploader": info.get("uploader", "Unknown"),
        }


# ── Cog ───────────────────────────────────────────────────────────────────────
class Music(commands.GroupCog, name="music"):
    """🎵 Full music system — play, queue, skip, loop, volume and more."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.queues: dict[int, MusicQueue] = {}

    # ── Internal helpers ──────────────────────────────────────────────────────

    def get_queue(self, guild_id: int) -> MusicQueue:
        if guild_id not in self.queues:
            self.queues[guild_id] = MusicQueue()
        return self.queues[guild_id]

    def _play_next(self, guild_id: int) -> None:
        """Start the next song in the queue (called from after-callback)."""
        queue = self.get_queue(guild_id)
        guild = self.bot.get_guild(guild_id)
        if not guild or not guild.voice_client:
            return

        vc = guild.voice_client
        if vc.is_playing() or vc.is_paused():
            return  # Something else already started

        # Loop: re-add the current song to the front
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
            vc.play(
                source,
                after=lambda e: asyncio.run_coroutine_threadsafe(
                    self._after_song(guild_id, e), self.bot.loop
                ),
            )
            logger.info(f"[Music] Playing '{song['title']}' in guild {guild_id}")
        except Exception as e:
            logger.error(f"[Music] Failed to start playback in guild {guild_id}: {e}")
            # Try next song
            asyncio.run_coroutine_threadsafe(self._after_song(guild_id, e), self.bot.loop)

    async def _after_song(self, guild_id: int, error=None) -> None:
        if error:
            logger.error(f"[Music] Playback error in guild {guild_id}: {error}")
        self._play_next(guild_id)

    async def _ensure_voice(self, interaction: discord.Interaction) -> bool:
        """Check user is in a voice channel. Sends error if not."""
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message(
                embed=error_embed("No Voice Channel", "You must be in a voice channel to use music commands."),
                ephemeral=True,
            )
            return False
        return True

    async def _get_voice_client(self, interaction: discord.Interaction) -> discord.VoiceClient | None:
        """Connect to or return the existing voice client for the guild."""
        vc = interaction.guild.voice_client
        channel = interaction.user.voice.channel
        if vc:
            if vc.channel != channel:
                await vc.move_to(channel)
            return vc
        try:
            return await channel.connect(timeout=10.0, reconnect=True)
        except asyncio.TimeoutError:
            await interaction.followup.send(
                embed=error_embed("Connection Timeout", "Could not connect to your voice channel. Try again."),
                ephemeral=True,
            )
            return None
        except discord.ClientException as e:
            await interaction.followup.send(
                embed=error_embed("Voice Error", f"Failed to connect: {e}"),
                ephemeral=True,
            )
            return None

    # ── Commands ──────────────────────────────────────────────────────────────

    @app_commands.command(name="play", description="Play a song from YouTube by title or URL.")
    @app_commands.describe(query="Song title or YouTube URL")
    @command_guard
    async def play(self, interaction: discord.Interaction, query: str):
        if not await self._ensure_voice(interaction):
            return
        if not YTDLP_AVAILABLE:
            return await interaction.response.send_message(
                embed=error_embed("Missing Dependency", "yt-dlp is not installed on this host. Contact the bot owner."),
                ephemeral=True,
            )

        await interaction.response.defer()

        try:
            loop = asyncio.get_event_loop()
            song = await asyncio.wait_for(
                loop.run_in_executor(None, _search_sync, query),
                timeout=30.0,
            )
        except asyncio.TimeoutError:
            return await interaction.followup.send(
                embed=error_embed("Timed Out", "YouTube search took too long. Please try again.")
            )
        except Exception as e:
            logger.error(f"[Music] Search error for '{query}': {e}")
            return await interaction.followup.send(
                embed=error_embed("Not Found", f"Could not find: **{query}**\n`{str(e)[:120]}`")
            )

        vc = await self._get_voice_client(interaction)
        if not vc:
            return

        queue = self.get_queue(interaction.guild.id)

        if vc.is_playing() or vc.is_paused():
            queue.songs.append(song)
            embed = info_embed(
                "Added to Queue",
                f"**[{song['title']}]({song['webpage_url']})**\n"
                f"Position: **#{len(queue.songs)}**  ·  Duration: **{_fmt_duration(song['duration'])}**",
            )
            if song["thumbnail"]:
                embed.set_thumbnail(url=song["thumbnail"])
        else:
            queue.now_playing = song
            try:
                source = discord.FFmpegPCMAudio(song["url"], **FFMPEG_OPTS)
                source = discord.PCMVolumeTransformer(source, volume=queue.volume)
                vc.play(
                    source,
                    after=lambda e: asyncio.run_coroutine_threadsafe(
                        self._after_song(interaction.guild.id, e), self.bot.loop
                    ),
                )
            except Exception as e:
                logger.error(f"[Music] Playback start error: {e}")
                return await interaction.followup.send(
                    embed=error_embed("Playback Error", f"Could not start playback: `{e}`")
                )

            embed = comprehensive_embed(
                title="🎵 Now Playing",
                description=f"**[{song['title']}]({song['webpage_url']})**",
                color=discord.Color.purple(),
            )
            embed.add_field(name="Duration", value=_fmt_duration(song["duration"]), inline=True)
            embed.add_field(name="Uploader", value=song["uploader"], inline=True)
            embed.add_field(name="Volume", value=f"{int(queue.volume * 100)}%", inline=True)
            if song["thumbnail"]:
                embed.set_thumbnail(url=song["thumbnail"])

        await interaction.followup.send(embed=embed)

    @app_commands.command(name="pause", description="Pause the current song.")
    async def pause(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            vc.pause()
            await interaction.response.send_message(
                embed=success_embed("Paused ⏸", "Music paused. Use `/music resume` to continue.")
            )
        else:
            await interaction.response.send_message(
                embed=error_embed("Nothing Playing", "No audio is currently playing."), ephemeral=True
            )

    @app_commands.command(name="resume", description="Resume a paused song.")
    async def resume(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc and vc.is_paused():
            vc.resume()
            await interaction.response.send_message(embed=success_embed("Resumed ▶️", "Music resumed!"))
        else:
            await interaction.response.send_message(
                embed=error_embed("Not Paused", "Music is not paused."), ephemeral=True
            )

    @app_commands.command(name="skip", description="Skip the current song.")
    async def skip(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc and (vc.is_playing() or vc.is_paused()):
            queue = self.get_queue(interaction.guild.id)
            current = queue.now_playing
            # Disable loop temporarily so skip actually skips
            was_loop = queue.loop
            queue.loop = False
            vc.stop()
            queue.loop = was_loop
            title = current["title"] if current else "current song"
            await interaction.response.send_message(
                embed=success_embed("Skipped ⏭", f"Skipped **{title}**.")
            )
        else:
            await interaction.response.send_message(
                embed=error_embed("Nothing Playing", "Nothing to skip."), ephemeral=True
            )

    @app_commands.command(name="stop", description="Stop music and disconnect the bot from voice.")
    async def stop(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc:
            self.queues.pop(interaction.guild.id, None)
            await vc.disconnect()
            await interaction.response.send_message(
                embed=success_embed("Stopped ⏹", "Music stopped and disconnected.")
            )
        else:
            await interaction.response.send_message(
                embed=error_embed("Not Connected", "I'm not in a voice channel."), ephemeral=True
            )

    @app_commands.command(name="queue", description="View the current music queue.")
    async def queue_cmd(self, interaction: discord.Interaction):
        queue = self.get_queue(interaction.guild.id)
        vc = interaction.guild.voice_client
        embed = comprehensive_embed(title="🎵 Music Queue", color=discord.Color.purple())

        if queue.now_playing and vc and (vc.is_playing() or vc.is_paused()):
            np = queue.now_playing
            embed.add_field(
                name="▶️ Now Playing",
                value=f"**[{np['title']}]({np['webpage_url']})**  ·  {_fmt_duration(np['duration'])}",
                inline=False,
            )
        else:
            embed.add_field(name="▶️ Now Playing", value="Nothing", inline=False)

        if queue.songs:
            queue_text = "\n".join(
                f"`{i+1}.` [{s['title']}]({s['webpage_url']})  ·  {_fmt_duration(s['duration'])}"
                for i, s in enumerate(queue.songs[:10])
            )
            if len(queue.songs) > 10:
                queue_text += f"\n*...and {len(queue.songs) - 10} more*"
            embed.add_field(name=f"📋 Up Next ({len(queue.songs)} songs)", value=queue_text, inline=False)
        else:
            embed.add_field(name="📋 Up Next", value="Queue is empty", inline=False)

        embed.add_field(name="🔊 Volume", value=f"{int(queue.volume * 100)}%", inline=True)
        embed.add_field(name="🔁 Loop", value="On" if queue.loop else "Off", inline=True)
        embed.add_field(name="🔀 Shuffle", value="On" if queue.shuffle else "Off", inline=True)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="nowplaying", description="Show detailed info about the currently playing song.")
    async def nowplaying(self, interaction: discord.Interaction):
        queue = self.get_queue(interaction.guild.id)
        vc = interaction.guild.voice_client
        if not vc or (not vc.is_playing() and not vc.is_paused()) or not queue.now_playing:
            return await interaction.response.send_message(
                embed=error_embed("Nothing Playing", "No song is currently playing."), ephemeral=True
            )
        song = queue.now_playing
        embed = comprehensive_embed(
            title="🎶 Now Playing",
            description=f"**[{song['title']}]({song['webpage_url']})**",
            color=discord.Color.purple(),
        )
        embed.add_field(name="Duration", value=_fmt_duration(song.get("duration", 0)), inline=True)
        embed.add_field(name="Volume", value=f"{int(queue.volume * 100)}%", inline=True)
        embed.add_field(name="Queue", value=f"{len(queue.songs)} songs", inline=True)
        embed.add_field(name="Loop", value="🔁 On" if queue.loop else "Off", inline=True)
        embed.add_field(name="Uploader", value=song.get("uploader", "Unknown"), inline=True)
        if song.get("thumbnail"):
            embed.set_thumbnail(url=song["thumbnail"])
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="volume", description="Set the music volume (1–100).")
    @app_commands.describe(level="Volume level 1–100")
    async def volume(self, interaction: discord.Interaction, level: int):
        level = max(1, min(100, level))
        queue = self.get_queue(interaction.guild.id)
        queue.volume = level / 100
        vc = interaction.guild.voice_client
        if vc and vc.source and isinstance(vc.source, discord.PCMVolumeTransformer):
            vc.source.volume = queue.volume
        await interaction.response.send_message(
            embed=success_embed("Volume Set 🔊", f"Volume set to **{level}%**.")
        )

    @app_commands.command(name="loop", description="Toggle loop mode — replays the current song/queue continuously.")
    async def loop_cmd(self, interaction: discord.Interaction):
        queue = self.get_queue(interaction.guild.id)
        queue.loop = not queue.loop
        status = "enabled 🔁" if queue.loop else "disabled"
        await interaction.response.send_message(
            embed=success_embed("Loop", f"Loop is now **{status}**.")
        )

    @app_commands.command(name="shuffle", description="Shuffle the queue randomly.")
    async def shuffle(self, interaction: discord.Interaction):
        queue = self.get_queue(interaction.guild.id)
        if not queue.songs:
            return await interaction.response.send_message(
                embed=error_embed("Empty Queue", "Nothing to shuffle."), ephemeral=True
            )
        _random.shuffle(queue.songs)
        queue.shuffle = not queue.shuffle
        embed = success_embed(
            "🔀 Shuffled!",
            f"Queue shuffled! **{len(queue.songs)}** songs reordered.\nShuffle mode: {'🔀 On' if queue.shuffle else 'Off'}",
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="remove", description="Remove a song from the queue by its position.")
    @app_commands.describe(position="Queue position to remove (1 = next song)")
    async def remove(self, interaction: discord.Interaction, position: int):
        queue = self.get_queue(interaction.guild.id)
        if not queue.songs:
            return await interaction.response.send_message(
                embed=error_embed("Empty Queue", "The queue is empty."), ephemeral=True
            )
        if position < 1 or position > len(queue.songs):
            return await interaction.response.send_message(
                embed=error_embed("Invalid Position", f"Position must be between 1 and {len(queue.songs)}."),
                ephemeral=True,
            )
        removed = queue.songs.pop(position - 1)
        await interaction.response.send_message(
            embed=success_embed("Removed", f"Removed **{removed['title']}** from the queue.")
        )

    @app_commands.command(name="lyrics", description="Fetch lyrics for the current song or any song you name.")
    @app_commands.describe(song="Song to get lyrics for (default: currently playing)")
    @command_guard
    async def lyrics(self, interaction: discord.Interaction, song: str = None):
        import aiohttp
        import urllib.parse
        import re

        await interaction.response.defer()
        queue = self.get_queue(interaction.guild.id)
        title = song or (queue.now_playing["title"] if queue.now_playing else None)
        if not title:
            return await interaction.followup.send(
                embed=error_embed("No Song", "Play something or provide a song name.")
            )

        # Clean up title for search
        clean = re.sub(
            r"\[.*?\]|\(.*?\)|official|video|lyrics|audio|hd|4k|mv|ft\.?.*",
            "",
            title,
            flags=re.IGNORECASE,
        ).strip()

        # Try lyrist.vercel.app (no API key needed)
        try:
            async with aiohttp.ClientSession() as session:
                url = f"https://lyrist.vercel.app/api/{urllib.parse.quote(clean)}"
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    if r.status == 200:
                        data = await r.json()
                        lyr = data.get("lyrics", "")
                        if lyr:
                            embed = discord.Embed(
                                title=f"🎵 {data.get('title', clean)}",
                                description=lyr[:2000] + ("..." if len(lyr) > 2000 else ""),
                                color=discord.Color.purple(),
                            )
                            if data.get("artist"):
                                embed.set_footer(text=f"Artist: {data['artist']}  •  XERO Music")
                            return await interaction.followup.send(embed=embed)
        except Exception as e:
            logger.warning(f"[Music] Lyrics fetch failed for '{clean}': {e}")

        # Fallback: AI-generated description
        try:
            resp = await self.bot.nvidia.ask(
                f'Provide the lyrics or a brief description of the song "{clean}". '
                "If you don't know it, say so honestly."
            )
        except Exception:
            resp = None

        embed = discord.Embed(
            title=f"🎵 {clean}",
            description=resp or "Lyrics not found for this song.",
            color=discord.Color.purple(),
        )
        embed.set_footer(text="XERO Music  •  Powered by lyrist.vercel.app")
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Music(bot))
    logger.info("✓ Music cog loaded (yt-dlp + FFmpeg, no external API keys required)")
