"""XERO Bot — Music Player (11 commands)"""
import discord
from discord.ext import commands
from discord import app_commands
import logging
import asyncio
from utils.embeds import success_embed, error_embed, info_embed, comprehensive_embed

logger = logging.getLogger("XERO.Music")

try:
    import yt_dlp
    YTDLP_AVAILABLE = True
except ImportError:
    YTDLP_AVAILABLE = False

YTDL_OPTS = {
    "format": "bestaudio/best", "noplaylist": True, "quiet": True,
    "no_warnings": True, "default_search": "auto", "source_address": "0.0.0.0",
}
FFMPEG_OPTS = {"before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5", "options": "-vn"}


class MusicQueue:
    def __init__(self):
        self.songs = []
        self.volume = 0.5
        self.loop = False
        self.shuffle = False
        self.now_playing = None


class Music(commands.GroupCog, name="music"):
    def __init__(self, bot):
        self.bot = bot
        self.queues: dict[int, MusicQueue] = {}

    def get_queue(self, guild_id: int) -> MusicQueue:
        if guild_id not in self.queues:
            self.queues[guild_id] = MusicQueue()
        return self.queues[guild_id]

    def play_next(self, guild_id: int):
        queue = self.get_queue(guild_id)
        guild = self.bot.get_guild(guild_id)
        if not guild or not guild.voice_client:
            return
        if queue.songs:
            song = queue.songs.pop(0)
            queue.now_playing = song
            source = discord.FFmpegPCMAudio(song["url"], **FFMPEG_OPTS)
            source = discord.PCMVolumeTransformer(source, volume=queue.volume)
            guild.voice_client.play(source, after=lambda e: asyncio.run_coroutine_threadsafe(
                self._after_song(guild_id, e), self.bot.loop
            ))

    async def _after_song(self, guild_id: int, error=None):
        if error:
            logger.error(f"Music player error: {error}")
        self.play_next(guild_id)

    async def ensure_voice(self, interaction: discord.Interaction) -> bool:
        if not interaction.user.voice:
            await interaction.response.send_message(embed=error_embed("No Voice Channel", "You must be in a voice channel to use music commands."), ephemeral=True)
            return False
        return True

    @app_commands.command(name="play", description="Play a song from YouTube by title or URL.")
    @app_commands.describe(query="Song title or YouTube URL")
    async def play(self, interaction: discord.Interaction, query: str):
        if not await self.ensure_voice(interaction):
            return
        if not YTDLP_AVAILABLE:
            return await interaction.response.send_message(embed=error_embed("Missing Dependency", "yt-dlp is not installed. Run `pip install yt-dlp`."), ephemeral=True)
        await interaction.response.defer()
        try:
            with yt_dlp.YoutubeDL(YTDL_OPTS) as ydl:
                info = ydl.extract_info(f"ytsearch:{query}" if not query.startswith("http") else query, download=False)
                if "entries" in info:
                    info = info["entries"][0]
                song = {"url": info["url"], "title": info["title"], "duration": info.get("duration", 0), "thumbnail": info.get("thumbnail"), "webpage_url": info.get("webpage_url", "")}
        except Exception as e:
            return await interaction.followup.send(embed=error_embed("Not Found", f"Couldn't find: **{query}**\n`{str(e)[:100]}`"))

        vc = interaction.guild.voice_client
        if not vc:
            vc = await interaction.user.voice.channel.connect()

        queue = self.get_queue(interaction.guild.id)
        if vc.is_playing() or vc.is_paused():
            queue.songs.append(song)
            embed = info_embed("Added to Queue", f"**{song['title']}**\nPosition: **#{len(queue.songs)}**")
        else:
            queue.now_playing = song
            source = discord.FFmpegPCMAudio(song["url"], **FFMPEG_OPTS)
            source = discord.PCMVolumeTransformer(source, volume=queue.volume)
            vc.play(source, after=lambda e: asyncio.run_coroutine_threadsafe(self._after_song(interaction.guild.id, e), self.bot.loop))
            mins, secs = divmod(song["duration"], 60)
            embed = comprehensive_embed(title="🎵 Now Playing", description=f"**[{song['title']}]({song['webpage_url']})**", color=discord.Color.purple())
            embed.add_field(name="Duration", value=f"{mins}:{secs:02d}", inline=True)
            embed.add_field(name="Volume", value=f"{int(queue.volume * 100)}%", inline=True)
            if song["thumbnail"]:
                embed.set_thumbnail(url=song["thumbnail"])
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="pause", description="Pause the current song.")
    async def pause(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            vc.pause()
            await interaction.response.send_message(embed=success_embed("Paused", "Music paused. Use `/music resume` to continue."))
        else:
            await interaction.response.send_message(embed=error_embed("Nothing Playing", "No audio is currently playing."), ephemeral=True)

    @app_commands.command(name="resume", description="Resume a paused song.")
    async def resume(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc and vc.is_paused():
            vc.resume()
            await interaction.response.send_message(embed=success_embed("Resumed", "Music resumed!"))
        else:
            await interaction.response.send_message(embed=error_embed("Not Paused", "Music is not paused."), ephemeral=True)

    @app_commands.command(name="skip", description="Skip the current song.")
    async def skip(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc and (vc.is_playing() or vc.is_paused()):
            queue = self.get_queue(interaction.guild.id)
            current = queue.now_playing
            vc.stop()
            await interaction.response.send_message(embed=success_embed("Skipped", f"Skipped **{current['title'] if current else 'current song'}**."))
        else:
            await interaction.response.send_message(embed=error_embed("Nothing Playing", "Nothing to skip."), ephemeral=True)

    @app_commands.command(name="stop", description="Stop music and disconnect the bot from voice.")
    async def stop(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc:
            self.queues.pop(interaction.guild.id, None)
            await vc.disconnect()
            await interaction.response.send_message(embed=success_embed("Stopped", "Music stopped and disconnected."))
        else:
            await interaction.response.send_message(embed=error_embed("Not Connected", "I'm not in a voice channel."), ephemeral=True)

    @app_commands.command(name="queue", description="View the current music queue.")
    async def queue(self, interaction: discord.Interaction):
        queue = self.get_queue(interaction.guild.id)
        embed = comprehensive_embed(title="🎵 Music Queue", color=discord.Color.purple())
        vc = interaction.guild.voice_client
        if queue.now_playing and vc and (vc.is_playing() or vc.is_paused()):
            embed.add_field(name="▶️ Now Playing", value=f"**{queue.now_playing['title']}**", inline=False)
        if queue.songs:
            queue_text = "\n".join(f"`{i+1}.` {s['title']}" for i, s in enumerate(queue.songs[:10]))
            if len(queue.songs) > 10:
                queue_text += f"\n*...and {len(queue.songs)-10} more*"
            embed.add_field(name=f"📋 Queue ({len(queue.songs)} songs)", value=queue_text, inline=False)
        else:
            embed.add_field(name="Queue", value="Empty", inline=False)
        embed.add_field(name="🔊 Volume", value=f"{int(queue.volume*100)}%", inline=True)
        embed.add_field(name="🔁 Loop", value="On" if queue.loop else "Off", inline=True)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="nowplaying", description="Show detailed info about the currently playing song.")
    async def nowplaying(self, interaction: discord.Interaction):
        queue = self.get_queue(interaction.guild.id)
        vc = interaction.guild.voice_client
        if not vc or (not vc.is_playing() and not vc.is_paused()) or not queue.now_playing:
            return await interaction.response.send_message(embed=error_embed("Nothing Playing", "No song is currently playing."), ephemeral=True)
        song = queue.now_playing
        mins, secs = divmod(song.get("duration", 0), 60)
        embed = comprehensive_embed(title="🎶 Now Playing", description=f"**{song['title']}**", color=discord.Color.purple())
        embed.add_field(name="Duration", value=f"{mins}:{secs:02d}", inline=True)
        embed.add_field(name="Volume", value=f"{int(queue.volume*100)}%", inline=True)
        embed.add_field(name="Queue", value=f"{len(queue.songs)} songs", inline=True)
        if song.get("thumbnail"):
            embed.set_thumbnail(url=song["thumbnail"])
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="volume", description="Set the music volume (1-100).")
    @app_commands.describe(level="Volume level 1-100")
    async def volume(self, interaction: discord.Interaction, level: int):
        level = max(1, min(100, level))
        queue = self.get_queue(interaction.guild.id)
        queue.volume = level / 100
        vc = interaction.guild.voice_client
        if vc and vc.source and isinstance(vc.source, discord.PCMVolumeTransformer):
            vc.source.volume = queue.volume
        await interaction.response.send_message(embed=success_embed("Volume Set", f"Volume set to **{level}%**."))

    @app_commands.command(name="loop", description="Toggle loop for the current queue.")
    async def loop(self, interaction: discord.Interaction):
        queue = self.get_queue(interaction.guild.id)
        queue.loop = not queue.loop
        status = "enabled 🔁" if queue.loop else "disabled"
        await interaction.response.send_message(embed=success_embed("Loop", f"Loop is now **{status}**."))

    @app_commands.command(name="remove", description="Remove a song from the queue by its position.")
    @app_commands.describe(position="Queue position to remove (1 = next song)")
    async def remove(self, interaction: discord.Interaction, position: int):
        queue = self.get_queue(interaction.guild.id)
        if not queue.songs:
            return await interaction.response.send_message(embed=error_embed("Empty Queue", "The queue is empty."), ephemeral=True)
        if position < 1 or position > len(queue.songs):
            return await interaction.response.send_message(embed=error_embed("Invalid Position", f"Position must be 1-{len(queue.songs)}."), ephemeral=True)
        removed = queue.songs.pop(position - 1)
        await interaction.response.send_message(embed=success_embed("Removed", f"Removed **{removed['title']}** from the queue."))


    @app_commands.command(name="shuffle", description="Shuffle the queue randomly and toggle shuffle mode.")
    async def shuffle(self, interaction: discord.Interaction):
        import random as _random
        queue = self.get_queue(interaction.guild.id)
        if not queue.songs:
            return await interaction.response.send_message(embed=error_embed("Empty Queue","Nothing to shuffle."), ephemeral=True)
        _random.shuffle(queue.songs)
        queue.shuffle = not queue.shuffle
        embed = success_embed("🔀 Shuffled!", f"Queue shuffled! **{len(queue.songs)}** songs reordered.\nShuffle: {'🔀 On' if queue.shuffle else 'Off'}")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="lyrics", description="Fetch lyrics for the current song or any song you name.")
    @app_commands.describe(song="Song to get lyrics for (default: currently playing)")
    async def lyrics(self, interaction: discord.Interaction, song: str = None):
        import aiohttp, urllib.parse, re
        await interaction.response.defer()
        queue = self.get_queue(interaction.guild.id)
        title = song or (queue.now_playing["title"] if queue.now_playing else None)
        if not title:
            return await interaction.followup.send(embed=error_embed("No Song","Play something or provide a song name."))
        clean = re.sub(r"\[.*?\]|\(.*?\)|official|video|lyrics|audio|hd|4k", "", title, flags=re.IGNORECASE).strip()
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"https://lyrist.vercel.app/api/{urllib.parse.quote(clean)}", timeout=aiohttp.ClientTimeout(total=10)) as r:
                    if r.status == 200:
                        data = await r.json()
                        lyr  = data.get("lyrics","")
                        if lyr:
                            embed = discord.Embed(title=f"🎵 {data.get('title',clean)}", description=lyr[:2000]+("..." if len(lyr)>2000 else ""), color=discord.Color.purple())
                            if data.get("artist"): embed.set_footer(text=f"Artist: {data['artist']}  •  XERO Music")
                            return await interaction.followup.send(embed=embed)
        except Exception: pass
        # AI fallback
        resp = await self.bot.nvidia.ask(f"Provide lyrics or a brief description of the song \"{clean}\". If you don't know it, say so.")
        embed = discord.Embed(title=f"🎵 {clean}", description=resp or "Lyrics not found.", color=discord.Color.purple())
        embed.set_footer(text="XERO Music  •  lyrist.vercel.app")
        await interaction.followup.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Music(bot))
