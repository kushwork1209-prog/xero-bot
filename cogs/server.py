from utils.embeds import brand_embed
"""XERO Bot — Server Management & Logging (14 commands)"""
import discord
from utils.guard import command_guard
from discord.ext import commands
from discord import app_commands
import logging
import datetime
from utils.embeds import success_embed, error_embed, info_embed, comprehensive_embed, warning_embed

logger = logging.getLogger("XERO.Server")


class Server(commands.GroupCog, name="server"):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="icon", description="View or get a download link for the server icon.")
    async def icon(self, interaction: discord.Interaction):
        if not interaction.guild.icon:
            return await interaction.response.send_message(embed=error_embed("No Icon", "This server has no icon set."))
        embed = comprehensive_embed(title=f"🖼️ {interaction.guild.name} — Icon", color=XERO.PRIMARY,)
        embed.set_image(url=interaction.guild.icon.url)
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Download PNG", url=f"{interaction.guild.icon.url}?size=4096&format=png", style=discord.ButtonStyle.link))
        await interaction.response.send_message(embed=embed, view=view)

    @app_commands.command(name="banner", description="View the server banner image.")
    async def banner(self, interaction: discord.Interaction):
        if not interaction.guild.banner:
            return await interaction.response.send_message(embed=error_embed("No Banner", "This server has no banner. Requires Boost Level 2."))
        embed = comprehensive_embed(title=f"🎨 {interaction.guild.name} — Banner", color=XERO.PRIMARY,)
        embed.set_image(url=interaction.guild.banner.url)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="bans", description="List the most recent bans in this server.")
    @app_commands.checks.has_permissions(ban_members=True)
    @command_guard
    async def bans(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        bans = [entry async for entry in interaction.guild.bans(limit=20)]
        if not bans:
            return await interaction.followup.send(embed=info_embed("No Bans", "No users are currently banned."))
        embed = comprehensive_embed(title=f"🚫 Banned Users ({len(bans)} shown)", color=XERO.PRIMARY,)
        for b in bans[:15]:
            embed.add_field(name=str(b.user), value=f"ID: `{b.user.id}`\nReason: {b.reason or 'No reason'}", inline=True)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="invites", description="List all active invites for this server.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def invites(self, interaction: discord.Interaction):
        invites = await interaction.guild.invites()
        if not invites:
            return await interaction.response.send_message(embed=info_embed("No Invites", "No active invites found."))
        invites.sort(key=lambda i: i.uses, reverse=True)
        embed = comprehensive_embed(title=f"🔗 Server Invites ({len(invites)} total)", color=XERO.PRIMARY,)
        for inv in invites[:10]:
            embed.add_field(
                name=f"/{inv.code}",
                value=f"**Uses:** {inv.uses} | **Creator:** {inv.inviter.mention if inv.inviter else 'Unknown'}\n**Channel:** {inv.channel.mention}\n**Expires:** {'Never' if not inv.max_age else f'<t:{int((inv.created_at + datetime.timedelta(seconds=inv.max_age)).timestamp())}:R>'}",
                inline=False
            )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="create-invite", description="Create a custom server invite link.")
    @app_commands.describe(channel="Channel for the invite", max_uses="Max uses (0=unlimited)", expires_hours="Expires in hours (0=never)")
    @app_commands.checks.has_permissions(create_instant_invite=True)
    async def create_invite(self, interaction: discord.Interaction, channel: discord.TextChannel = None, max_uses: int = 0, expires_hours: int = 0):
        ch = channel or interaction.channel
        max_age = expires_hours * 3600 if expires_hours > 0 else 0
        invite = await ch.create_invite(max_uses=max_uses, max_age=max_age, reason=f"Created by {interaction.user}")
        embed = success_embed("Invite Created!", f"**Link:** {invite.url}\n**Channel:** {ch.mention}\n**Max Uses:** {'Unlimited' if max_uses == 0 else max_uses}\n**Expires:** {'Never' if max_age == 0 else f'{expires_hours}h'}")
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Open Invite", url=invite.url, style=discord.ButtonStyle.link))
        await interaction.response.send_message(embed=embed, view=view)

    @app_commands.command(name="channels", description="View a full breakdown of all server channels.")
    async def channels(self, interaction: discord.Interaction):
        text = [c for c in interaction.guild.channels if isinstance(c, discord.TextChannel)]
        voice = [c for c in interaction.guild.channels if isinstance(c, discord.VoiceChannel)]
        stage = [c for c in interaction.guild.channels if isinstance(c, discord.StageChannel)]
        forum = [c for c in interaction.guild.channels if isinstance(c, discord.ForumChannel)]
        cats = interaction.guild.categories
        embed = comprehensive_embed(title=f"📡 {interaction.guild.name} — Channels", color=XERO.PRIMARY,)
        embed.add_field(name="💬 Text", value=str(len(text)), inline=True)
        embed.add_field(name="🔊 Voice", value=str(len(voice)), inline=True)
        embed.add_field(name="📁 Categories", value=str(len(cats)), inline=True)
        embed.add_field(name="🎭 Stage", value=str(len(stage)), inline=True)
        embed.add_field(name="📋 Forum", value=str(len(forum)), inline=True)
        embed.add_field(name="📊 Total", value=str(len(interaction.guild.channels)), inline=True)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="members", description="View detailed member breakdown for the server.")
    async def members(self, interaction: discord.Interaction):
        guild = interaction.guild
        online = sum(1 for m in guild.members if m.status == discord.Status.online)
        idle = sum(1 for m in guild.members if m.status == discord.Status.idle)
        dnd = sum(1 for m in guild.members if m.status == discord.Status.dnd)
        offline = sum(1 for m in guild.members if m.status == discord.Status.offline)
        bots = sum(1 for m in guild.members if m.bot)
        humans = guild.member_count - bots
        embed = comprehensive_embed(title=f"👥 {guild.name} — Members", color=XERO.PRIMARY,)
        embed.add_field(name="Total", value=f"**{guild.member_count:,}**", inline=True)
        embed.add_field(name="Humans", value=f"**{humans:,}**", inline=True)
        embed.add_field(name="Bots", value=f"**{bots:,}**", inline=True)
        embed.add_field(name="🟢 Online", value=f"**{online:,}**", inline=True)
        embed.add_field(name="🟡 Idle", value=f"**{idle:,}**", inline=True)
        embed.add_field(name="🔴 DND", value=f"**{dnd:,}**", inline=True)
        embed.add_field(name="⚫ Offline", value=f"**{offline:,}**", inline=True)
        embed.add_field(name="💎 Boosters", value=f"**{guild.premium_subscription_count:,}**", inline=True)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="boosts", description="View server boost status and list of boosters.")
    async def boosts(self, interaction: discord.Interaction):
        guild = interaction.guild
        boosters = guild.premium_subscribers
        embed = comprehensive_embed(title=f"💎 {guild.name} — Boost Status", color=XERO.PRIMARY,)
        embed.add_field(name="Boost Level", value=f"**Level {guild.premium_tier}**", inline=True)
        embed.add_field(name="Total Boosts", value=f"**{guild.premium_subscription_count}**", inline=True)
        embed.add_field(name="Boosters", value=f"**{len(boosters)}**", inline=True)
        perks = {
            0: "No perks", 1: "50 emoji slots, 128kbps audio, server icon",
            2: "150 emoji slots, 256kbps audio, server banner",
            3: "250 emoji slots, 384kbps audio, vanity URL"
        }
        embed.add_field(name="Current Perks", value=perks.get(guild.premium_tier, "Unknown"), inline=False)
        if boosters:
            mentions = ", ".join(b.mention for b in boosters[:10])
            if len(boosters) > 10:
                mentions += f" *+{len(boosters)-10} more*"
            embed.add_field(name="Boosters", value=mentions, inline=False)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="audit-log", description="View the last 10 audit log entries for this server.")
    @app_commands.checks.has_permissions(view_audit_log=True)
    @command_guard
    async def audit_log(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        entries = []
        async for entry in interaction.guild.audit_logs(limit=10):
            entries.append(entry)
        if not entries:
            return await interaction.followup.send(embed=info_embed("Empty", "No audit log entries found."))
        embed = comprehensive_embed(title="📋 Recent Audit Log", color=XERO.PRIMARY,)
        for e in entries:
            action_name = str(e.action).replace("AuditLogAction.", "").replace("_", " ").title()
            user_name = str(e.user) if e.user else "Unknown"
            target_name = str(e.target) if e.target else "Unknown"
            embed.add_field(
                name=f"{action_name}",
                value=f"**By:** {user_name}\n**Target:** {target_name}\n**When:** <t:{int(e.created_at.timestamp())}:R>",
                inline=True
            )
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="slowmode-all", description="Set slowmode on all text channels at once.")
    @app_commands.describe(seconds="Slowmode delay in seconds (0 to remove)")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def slowmode_all(self, interaction: discord.Interaction, seconds: int):
        await interaction.response.defer()
        seconds = max(0, min(21600, seconds))
        count = 0
        for ch in interaction.guild.text_channels:
            try:
                await ch.edit(slowmode_delay=seconds)
                count += 1
            except Exception:
                pass
        msg = f"Slowmode **{'removed' if seconds == 0 else f'set to {seconds}s'}** on **{count}** channels."
        await interaction.followup.send(embed=success_embed("Slowmode Applied", msg))

    @app_commands.command(name="lockdown", description="Lock all channels — emergency server lockdown.")
    @app_commands.describe(reason="Reason for lockdown")
    @app_commands.checks.has_permissions(administrator=True)
    async def lockdown(self, interaction: discord.Interaction, reason: str = "Emergency lockdown"):
        await interaction.response.defer()
        count = 0
        for ch in interaction.guild.text_channels:
            ow = ch.overwrites_for(interaction.guild.default_role)
            ow.send_messages = False
            try:
                await ch.set_permissions(interaction.guild.default_role, overwrite=ow)
                count += 1
            except Exception:
                pass
        await interaction.followup.send(embed=warning_embed("🔴 SERVER LOCKDOWN ACTIVE", f"**{count}** channels locked.\n**Reason:** {reason}\n\nUse `/server unlockdown` to restore access."))

    @app_commands.command(name="unlockdown", description="Unlock all channels — lift the server lockdown.")
    @app_commands.checks.has_permissions(administrator=True)
    async def unlockdown(self, interaction: discord.Interaction):
        await interaction.response.defer()
        count = 0
        for ch in interaction.guild.text_channels:
            ow = ch.overwrites_for(interaction.guild.default_role)
            ow.send_messages = None
            try:
                await ch.set_permissions(interaction.guild.default_role, overwrite=ow)
                count += 1
            except Exception:
                pass
        await interaction.followup.send(embed=success_embed("🟢 Lockdown Lifted", f"**{count}** channels have been unlocked. Server is back to normal."))

    @app_commands.command(name="nuke", description="Clear a channel by cloning it and deleting the original.")
    @app_commands.describe(channel="Channel to nuke (default: current)")
    @app_commands.checks.has_permissions(administrator=True)
    async def nuke(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        ch = channel or interaction.channel
        position = ch.position
        new_ch = await ch.clone(reason=f"Channel nuked by {interaction.user}")
        await new_ch.edit(position=position)
        await ch.delete(reason=f"Nuked by {interaction.user}")
        embed = success_embed("💥 Channel Nuked", "This channel has been wiped clean. All messages deleted.")
        await new_ch.send(embed=embed)
        if ch.id != interaction.channel.id:
            await interaction.response.send_message(embed=success_embed("Nuked", f"#{ch.name} has been nuked."))


async def setup(bot):
    await bot.add_cog(Server(bot))
