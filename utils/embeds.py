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
    footer: str = None,
    thumbnail: str = None,
    image: str = None,
    author_name: str = None,
    author_icon: str = None,
    timestamp: bool = True,
    fields: List[Tuple[str, str, bool]] = None,
) -> discord.Embed:
    """Base embed factory — all XERO embeds flow through here."""
    
    # Standardize color to brand primary if not provided
    final_color = color if color is not None else XERO.PRIMARY
    
    embed = discord.Embed(
        title=title,
        description=description,
        color=final_color,
        timestamp=discord.utils.utcnow() if timestamp else None,
    )
    
    # Standard footer if none provided
    if footer:
        embed.set_footer(text=footer)
    else:
        embed.set_footer(text=FOOTER_MAIN)
        
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

async def brand_embed(embed: discord.Embed, guild: discord.Guild, bot, force_color: bool = False) -> Tuple[discord.Embed, Optional[discord.File]]:
    """
    Applies guild-specific branding to an embed:
    1. Custom Color (if embed color is default primary or force_color is True)
    2. Unified Image (if available)
    3. Standard Footer + Timestamp
    """
    if not guild:
        return embed, None
        
    settings = await bot.db.get_guild_settings(guild.id)
    
    # 1. Custom Color logic
    # We only override if it's the default primary or if specifically forced
    if force_color or embed.color == XERO.PRIMARY:
        if settings.get("embed_color"):
            try:
                hex_color = settings["embed_color"].lstrip("#")
                embed.color = discord.Color(int(hex_color, 16))
            except: pass
    
    # 2. Standard Footer + Timestamp
    current_footer = embed.footer.text or FOOTER_MAIN
    # Clean up the footer to avoid double branding
    clean_footer = current_footer.split(" • ")[0].split(" | ")[0]
    embed.set_footer(text=f"{clean_footer}  •  {guild.name}")
    
    if not embed.timestamp:
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


def stock_embed(stocks: list) -> discord.Embed:
    """Displays the XERO Stock Exchange — all tickers, prices, and 1-period change."""
    embed = discord.Embed(
        title="📈  XERO Stock Exchange",
        description="Live prices updated every hour. Buy low, sell high.",
        color=XERO.ECONOMY,
        timestamp=discord.utils.utcnow(),
    )
    if not stocks:
        embed.description = "No stocks available right now. Check back soon!"
        embed.set_footer(text=FOOTER_ECO)
        return embed

    lines = []
    for s in stocks:
        symbol    = s["symbol"]
        name      = s["name"]
        price     = s["price"]
        prev      = s.get("prev_price", price)
        diff      = price - prev
        pct       = (diff / max(prev, 1)) * 100
        arrow     = "🟢 ▲" if diff >= 0 else "🔴 ▼"
        sign      = "+" if diff >= 0 else ""
        lines.append(
            f"**{symbol}** — {name}\n"
            f"  `${price:,}`  {arrow} {sign}{diff:,} ({sign}{pct:.1f}%)"
        )

    # Split into two columns of fields for readability
    mid = (len(lines) + 1) // 2
    embed.add_field(name="🏦  Tickers (1/2)", value="\n\n".join(lines[:mid]) or "—", inline=True)
    embed.add_field(name="🏦  Tickers (2/2)", value="\n\n".join(lines[mid:]) or "—", inline=True)
    embed.set_footer(text=FOOTER_ECO)
    return embed


def heist_embed(
    leader: discord.Member,
    bank: str,
    participants: list,
    potential: int,
    success: bool = None,
    actual_reward: int = None,
) -> discord.Embed:
    """
    Heist embed — three states:
      • Planning  (success=None)  — recruiting phase
      • Success   (success=True)  — payout
      • Failure   (success=False) — fine
    """
    crew_list = ", ".join(m.mention for m in participants) if participants else leader.mention

    if success is None:
        # ── Planning phase ──────────────────────────────────────────────────
        embed = discord.Embed(
            title=f"🏦  HEIST PLANNED  •  {bank}",
            description=(
                f"{leader.mention} is planning a heist on **{bank}**!\n\n"
                f"Click **Join Heist** to join the crew.\n"
                f"More crew = higher success chance.\n"
                f"*Heist executes in **60 seconds**.*"
            ),
            color=XERO.WARNING,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="💰  Potential Loot",  value=f"**${potential:,}**",          inline=True)
        embed.add_field(name="👥  Crew",             value=crew_list,                       inline=True)
        embed.add_field(name="📊  Base Success",     value="**30%** (+8% per extra member)", inline=True)
        embed.set_thumbnail(url=leader.display_avatar.url)

    elif success:
        # ── Success ─────────────────────────────────────────────────────────
        per_person = actual_reward // max(len(participants), 1)
        embed = discord.Embed(
            title=f"💰  HEIST SUCCESSFUL  •  {bank}",
            description=(
                f"The crew cracked **{bank}** and walked away clean!\n\n"
                f"**Total stolen:** ${actual_reward:,}\n"
                f"**Each member gets:** ${per_person:,}"
            ),
            color=XERO.SUCCESS,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="👥  Crew",   value=crew_list,                  inline=False)
        embed.add_field(name="🎯  Target", value=f"**{bank}**",              inline=True)
        embed.add_field(name="💵  Payout", value=f"**${per_person:,}** each", inline=True)
        embed.set_thumbnail(url=leader.display_avatar.url)

    else:
        # ── Failure ──────────────────────────────────────────────────────────
        embed = discord.Embed(
            title=f"🚨  HEIST FAILED  •  {bank}",
            description=(
                f"The crew got caught trying to rob **{bank}**!\n\n"
                f"Security was too tight. Everyone pays a fine of **${actual_reward:,}**."
            ),
            color=XERO.ERROR,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="👥  Caught",  value=crew_list,                   inline=False)
        embed.add_field(name="🏦  Target",  value=f"**{bank}**",               inline=True)
        embed.add_field(name="💸  Fine",    value=f"**${actual_reward:,}** each", inline=True)
        embed.set_thumbnail(url=leader.display_avatar.url)

    embed.set_footer(text=FOOTER_ECO)
    return embed


def milestone_embed(guild: discord.Guild, member_count: int) -> discord.Embed:
    """Celebration embed for server member milestones (100, 500, 1000, etc.)."""
    # Pick a colour that escalates with size
    if member_count >= 10_000:
        color = XERO.GOLD
        tier  = "🏆 LEGENDARY"
    elif member_count >= 1_000:
        color = XERO.SECONDARY
        tier  = "💎 EPIC"
    elif member_count >= 500:
        color = XERO.PRIMARY
        tier  = "🌟 AMAZING"
    else:
        color = XERO.SUCCESS
        tier  = "🎉 MILESTONE"

    embed = discord.Embed(
        title=f"{tier}  •  {guild.name} reached {member_count:,} members!",
        description=(
            f"**{guild.name}** just hit **{member_count:,} members**! 🎊\n\n"
            f"Thank you to every single person who joined and made this community what it is.\n"
            f"Here's to the next milestone! 🚀"
        ),
        color=color,
        timestamp=discord.utils.utcnow(),
    )
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.add_field(name="👥  Members",  value=f"**{member_count:,}**", inline=True)
    embed.add_field(name="🏠  Server",   value=guild.name,              inline=True)
    embed.set_footer(text=FOOTER_MAIN)
    return embed


def health_embed(
    guild: discord.Guild,
    score: int,
    grade: str,
    analysis: str,
    recommendations: list,
) -> discord.Embed:
    """
    Server health report embed.
    score         — 0-100 integer health score
    grade         — letter grade string, e.g. "A", "B+", "C"
    analysis      — AI-generated or rule-based analysis paragraph
    recommendations — list of recommendation strings
    """
    # Color based on score
    if score >= 80:
        color = XERO.SUCCESS
        status = "🟢 Excellent"
    elif score >= 60:
        color = XERO.PRIMARY
        status = "🔵 Good"
    elif score >= 40:
        color = XERO.WARNING
        status = "🟡 Needs Attention"
    else:
        color = XERO.ERROR
        status = "🔴 Critical"

    # Progress bar
    filled = int(score / 5)
    bar    = "█" * filled + "░" * (20 - filled)

    embed = discord.Embed(
        title=f"🏥  Server Health Report  •  {guild.name}",
        description=analysis or "No analysis available.",
        color=color,
        timestamp=discord.utils.utcnow(),
    )
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)

    embed.add_field(name="📊  Health Score",  value=f"**{score}/100**  ({status})",  inline=True)
    embed.add_field(name="🎓  Grade",          value=f"**{grade}**",                  inline=True)
    embed.add_field(
        name="📈  Score Bar",
        value=f"`{bar}` {score}%",
        inline=False,
    )
    if recommendations:
        rec_text = "\n".join(f"• {r}" for r in recommendations[:8])  # cap at 8 to stay within field limit
        embed.add_field(name="💡  Recommendations", value=rec_text, inline=False)
    else:
        embed.add_field(name="💡  Recommendations", value="No recommendations — server is in great shape!", inline=False)

    embed.set_footer(text="XERO Smart Moderation  •  Server Health")
    return embed
