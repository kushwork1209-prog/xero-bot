"""
XERO Bot — Premium Embed System
Consistent XERO brand identity across every single command.
Brand: Electric Blue #00D4FF | Deep Navy #0A0E1A | Accent Purple #7B2FFF
"""
import discord
from typing import Optional, List, Tuple, Union
import datetime
import base64
import io

# ── XERO Brand Colors ─────────────────────────────────────────────────────────
class XeroColors:
    PRIMARY    = discord.Color(0x00D4FF)   # Electric blue  — main brand
    SECONDARY  = discord.Color(0x7B2FFF)   # Accent purple  — special
    DARK       = discord.Color(0x0D1117)   # Deep dark      — neutral
    SUCCESS    = discord.Color(0x00FF94)   # Neon green     — success
    ERROR      = discord.Color(0xFF3B5C)   # Neon red       — error
    WARNING    = discord.Color(0xFFB800)   # Amber          — warning
    INFO       = discord.Color(0x00D4FF)   # Same as primary
    GOLD       = discord.Color(0xFFD700)   # Gold           — economy/wins
    ECONOMY    = discord.Color(0x00E676)   # Money green    — economy
    LEVEL      = discord.Color(0xAA00FF)   # Deep purple    — levels/XP
    MOD        = discord.Color(0xFF6B35)   # Orange         — moderation
    MUSIC      = discord.Color(0x1DB954)   # Spotify green  — music
    FUN        = discord.Color(0xFF4FA3)   # Hot pink       — fun commands
    AI         = discord.Color(0x00BCD4)   # Cyan           — AI commands
    DANGER     = discord.Color(0xFF1744)   # Bright red     — dangerous actions


XERO = XeroColors()

FOOTER_MAIN  = "XERO Bot  •  xero.gg"
FOOTER_AI    = "XERO AI  •  Powered by NVIDIA Llama 4 Maverick"
FOOTER_ECO   = "XERO Economy  •  xero.gg"
FOOTER_MOD   = "XERO Moderation  •  xero.gg"
FOOTER_LEVEL = "XERO Levels  •  xero.gg"


def _base(
    title: str = "",
    description: str = "",
    color: discord.Color = None,
    footer: str = FOOTER_MAIN,
    thumbnail: str = None,
    image: str = None,
    author_name: str = None,
    author_icon: str = None,
    timestamp: bool = True,  # Default to True as per user request
    fields: List[Tuple[str, str, bool]] = None,
    guild_id: int = None,
    bot = None
) -> discord.Embed:
    """Base embed factory — all XERO embeds flow through here."""
    
    # ── Guild Branding ──
    final_color = color or XERO.PRIMARY
    final_footer = footer or FOOTER_MAIN
    
    if guild_id and bot and hasattr(bot, 'db'):
        # This is a bit heavy for a base helper, but necessary for "everywhere" rule
        # In a real bot, we'd cache these settings.
        try:
            # We use a sync-lookalike or just accept we might not have it yet
            # For Manus implementation, we'll assume we can't easily do async here 
            # without changing all calls. So we'll provide a way to 'brand' an embed later.
            pass 
        except: pass

    embed = discord.Embed(
        title=title,
        description=description,
        color=final_color,
        timestamp=discord.utils.utcnow() if timestamp else None,
    )
    if final_footer:
        embed.set_footer(text=final_footer)
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    if image:
        embed.set_image(url=image)
    if author_name:
        embed.set_author(name=author_name, icon_url=author_icon or None)
    if fields:
        for name, value, inline in fields:
            embed.add_field(name=name, value=value, inline=inline)
    return embed

async def brand_embed(embed: discord.Embed, guild: discord.Guild, bot) -> Tuple[discord.Embed, Optional[discord.File]]:
    """
    Applies guild-specific branding to an embed:
    1. Custom Color
    2. Unified Image (if available)
    3. Standard Footer + Timestamp
    """
    if not guild:
        return embed, None
        
    settings = await bot.db.get_guild_settings(guild.id)
    
    # 1. Custom Color
    if settings.get("embed_color"):
        try:
            hex_color = settings["embed_color"].lstrip("#")
            embed.color = discord.Color(int(hex_color, 16))
        except: pass
    
    # 2. Standard Footer + Timestamp
    current_footer = embed.footer.text or FOOTER_MAIN
    if "|" not in current_footer and guild.name not in current_footer:
        embed.set_footer(text=f"{current_footer}  •  {guild.name}")
    embed.timestamp = discord.utils.utcnow()
    
    # 3. Unified Image
    file = None
    if settings.get("unified_image_data"):
        try:
            image_data = base64.b64decode(settings["unified_image_data"])
            file = discord.File(io.BytesIO(image_data), filename="unified_brand.png")
            embed.set_image(url="attachment://unified_brand.png")
        except Exception: pass
        
    return embed, file

# ── Public helpers ────────────────────────────────────────────────────────────

def comprehensive_embed(
    title: str = "",
    description: str = "",
    fields: List[Tuple[str, str, bool]] = None,
    footer_text: str = FOOTER_MAIN,
    thumbnail_url: str = None,
    image_url: str = None,
    color: discord.Color = None,
    author_name: str = None,
    author_icon: str = None,
) -> discord.Embed:
    return _base(title=title, description=description, color=color or XERO.PRIMARY,
                 footer=footer_text, thumbnail=thumbnail_url, image=image_url,
                 author_name=author_name, author_icon=author_icon, fields=fields)


def success_embed(title: str, description: str = "", **kwargs) -> discord.Embed:
    clean_title = title.lstrip("✅ ").lstrip("✓ ")
    return _base(title=f"✅  {clean_title}", description=description,
                 color=XERO.SUCCESS, footer=kwargs.get("footer_text", FOOTER_MAIN))


def error_embed(title: str, description: str = "", **kwargs) -> discord.Embed:
    clean_title = title.lstrip("❌ ")
    return _base(title=f"❌  {clean_title}", description=description,
                 color=XERO.ERROR, footer=kwargs.get("footer_text", FOOTER_MAIN))


def info_embed(title: str, description: str = "", **kwargs) -> discord.Embed:
    clean_title = title.lstrip("ℹ️ ")
    return _base(title=f"ℹ️  {clean_title}", description=description,
                 color=XERO.INFO, footer=kwargs.get("footer_text", FOOTER_MAIN))


def warning_embed(title: str, description: str = "", **kwargs) -> discord.Embed:
    clean_title = title.lstrip("⚠️ ")
    return _base(title=f"⚠️  {clean_title}", description=description,
                 color=XERO.WARNING, footer=kwargs.get("footer_text", FOOTER_MAIN))


def ai_embed(title: str, description: str = "", **kwargs) -> discord.Embed:
    return _base(title=f"🤖  {title}", description=description,
                 color=XERO.AI, footer=FOOTER_AI)


def mod_embed(
    action: str, user: discord.Member, moderator: discord.Member,
    reason: str, case_id: int, duration: str = None
) -> discord.Embed:
    ACTION_COLORS = {
        "ban": XERO.DANGER, "kick": XERO.MOD, "warn": XERO.WARNING,
        "timeout": XERO.WARNING, "unban": XERO.SUCCESS,
        "softban": XERO.ERROR, "mute": XERO.MOD,
    }
    ACTION_EMOJIS = {
        "ban": "🔨", "kick": "👢", "warn": "⚠️", "timeout": "⏱️",
        "unban": "🔓", "softban": "🪃", "mute": "🔇",
    }
    emoji = ACTION_EMOJIS.get(action.lower(), "⚖️")
    color = ACTION_COLORS.get(action.lower(), XERO.MOD)
    embed = discord.Embed(
        title=f"{emoji}  {action.upper()}  •  Case #{case_id}",
        color=color,
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="👤 User",        value=f"{user.mention}\n`{user.id}`",  inline=True)
    embed.add_field(name="🛡️ Moderator",   value=moderator.mention,               inline=True)
    embed.add_field(name="📋 Reason",      value=reason,                           inline=False)
    if duration:
        embed.add_field(name="⏱️ Duration", value=duration, inline=True)
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.set_footer(text=FOOTER_MOD)
    return embed


def economy_embed(
    user: discord.Member, wallet: int, bank: int,
    bank_limit: int, streak: int = 0, net_rank: int = None
) -> discord.Embed:
    total     = wallet + bank
    pct_full  = min(bank / max(bank_limit, 1) * 100, 100)
    bank_bar  = "█" * int(pct_full / 5) + "░" * (20 - int(pct_full / 5))
    embed = discord.Embed(
        title=f"💳  {user.display_name}'s Wallet",
        color=XERO.ECONOMY,
        timestamp=discord.utils.utcnow()
    )
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.add_field(name="👛  Wallet",    value=f"```${wallet:,}```",               inline=True)
    embed.add_field(name="🏦  Bank",      value=f"```${bank:,} / ${bank_limit:,}```", inline=True)
    embed.add_field(name="💎  Net Worth", value=f"```${total:,}```",               inline=True)
    embed.add_field(
        name=f"🏦  Bank Capacity  ({pct_full:.0f}%)",
        value=f"`{bank_bar}`",
        inline=False
    )
    if streak > 0:
        streak_emoji = "🔥" * min(streak, 5)
        embed.add_field(name="🔥  Daily Streak", value=f"**{streak}** days {streak_emoji}", inline=True)
    if net_rank:
        embed.add_field(name="🏆  Server Rank", value=f"**#{net_rank}**", inline=True)
    embed.set_footer(text=FOOTER_ECO)
    return embed


def level_embed(
    user: discord.Member, level: int, xp: int,
    next_xp: int, total_xp: int, rank: int
) -> discord.Embed:
    pct       = min(xp / max(next_xp, 1), 1.0)
    filled    = int(pct * 20)
    bar       = "█" * filled + "░" * (20 - filled)
    pct_label = f"{pct*100:.1f}%"
    # Level color gradient
    if level >= 50:   color = XERO.GOLD
    elif level >= 25: color = XERO.SECONDARY
    elif level >= 10: color = XERO.PRIMARY
    else:             color = XERO.LEVEL

    embed = discord.Embed(
        title=f"📊  {user.display_name}  •  Level {level}",
        color=color,
        timestamp=discord.utils.utcnow()
    )
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.add_field(name="🏆  Rank",      value=f"**#{rank}**",        inline=True)
    embed.add_field(name="⭐  Level",     value=f"**{level}**",         inline=True)
    embed.add_field(name="✨  Total XP",  value=f"**{total_xp:,}**",   inline=True)
    embed.add_field(
        name=f"📈  Progress to Level {level+1}  ({pct_label})",
        value=f"`{bar}`\n**{xp:,}** / {next_xp:,} XP",
        inline=False
    )
    embed.set_footer(text=FOOTER_LEVEL)
    return embed


def giveaway_embed(
    prize: str, end_ts: int, winners: int,
    host: discord.Member, participants: int = 0,
    requirements: str = None
) -> discord.Embed:
    embed = discord.Embed(
        title="🎉  GIVEAWAY",
        description=f"## {prize}",
        color=XERO.GOLD,
        timestamp=discord.utils.utcnow()
    )
    embed.add_field(name="🏆  Winners",   value=str(winners),                 inline=True)
    embed.add_field(name="⏰  Ends",       value=f"<t:{end_ts}:R>",           inline=True)
    embed.add_field(name="👥  Entries",   value=str(participants),             inline=True)
    embed.add_field(name="📢  Hosted by", value=host.mention,                  inline=True)
    if requirements:
        embed.add_field(name="📋  Requirements", value=requirements, inline=False)
    embed.set_footer(text="React with 🎉 to enter  •  XERO Bot")
    return embed


def raid_alert_embed(guild: discord.Guild, join_count: int, window_secs: int) -> discord.Embed:
    embed = discord.Embed(
        title="🚨  RAID DETECTED  —  AUTO-LOCKDOWN ACTIVE",
        description=(
            f"**{join_count}** accounts joined **{guild.name}** in the last **{window_secs} seconds**.\n\n"
            f"The server has been **automatically locked down**.\n"
            f"All channels are locked. Verify new members before unlocking.\n\n"
            f"Use `/server unlockdown` to restore access."
        ),
        color=XERO.DANGER,
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="🛡️  Action Taken", value="All channels locked + DM alert sent to admins", inline=False)
    embed.set_footer(text="XERO Smart Moderation  •  Auto-Raid Protection")
    return embed


def escalation_embed(
    user: discord.Member, warn_count: int,
    action_taken: str, reason: str
) -> discord.Embed:
    embed = discord.Embed(
        title=f"⚡  AUTO-ESCALATION  •  {action_taken.upper()}",
        description=(
            f"{user.mention} has reached **{warn_count} warnings**.\n"
            f"XERO's auto-escalation system has taken action."
        ),
        color=XERO.MOD,
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="👤  User",         value=f"{user} (`{user.id}`)", inline=True)
    embed.add_field(name="⚠️  Warnings",     value=str(warn_count),         inline=True)
    embed.add_field(name="🔨  Auto-Action",  value=action_taken,             inline=True)
    embed.add_field(name="📋  Last Reason",  value=reason,                   inline=False)
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.set_footer(text="XERO Smart Moderation  •  Auto-Escalation")
    return embed
