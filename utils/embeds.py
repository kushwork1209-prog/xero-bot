"""
XERO Bot — Cyber-Luxury Embed Engine
The most advanced, structured, and premium embed system on Discord.
Beyond ZNYT — Engineered for elite servers.
"""
import discord
from typing import Optional, List, Tuple, Union
import datetime
import base64
import io

# ── XERO Elite Palette ───────────────────────────────────────────────────────
class XeroColors:
    # We use high-contrast, deep saturation for a premium "OLED" look
    PRIMARY    = discord.Color(0x00D4FF)   # Electric Cyan
    SECONDARY  = discord.Color(0x7B2FFF)   # Deep Ultraviolet
    DARK       = discord.Color(0x0D1117)   # Obsidian
    SUCCESS    = discord.Color(0x00FF94)   # Neon Mint
    ERROR      = discord.Color(0xFF3B5C)   # Crimson Rose
    WARNING    = discord.Color(0xFFB800)   # Amber Gold
    INFO       = discord.Color(0x00D4FF)   
    GOLD       = discord.Color(0xFFD700)   
    ECONOMY    = discord.Color(0x00E676)   
    LEVEL      = discord.Color(0xAA00FF)   
    MOD        = discord.Color(0xFF6B35)   
    MUSIC      = discord.Color(0x1DB954)   
    FUN        = discord.Color(0xFF4FA3)   
    AI         = discord.Color(0x00BCD4)   
    DANGER     = discord.Color(0xFF1744)   

XERO = XeroColors()

# ── Structured Typography & Symbols ──────────────────────────────────────────
# We use specific spacing and dividers to create a "ZNYT-plus" layout
DIVIDER = "──────────────────────────"
FOOTER_MAIN  = "XERO ELITE  •  xero.gg"
FOOTER_AI    = "XERO NEURAL  •  Powered by NVIDIA Llama 4 Maverick"
FOOTER_ECO   = "XERO ECONOMY  •  xero.gg"
FOOTER_MOD   = "XERO MODERATION  •  xero.gg"
FOOTER_LEVEL = "XERO LEVELS  •  xero.gg"

def _base(
    title: str = "",
    description: str = "",
    color: discord.Color = None,
    footer: str = FOOTER_MAIN,
    thumbnail: str = None,
    image: str = None,
    author_name: str = None,
    author_icon: str = None,
    timestamp: bool = True,
    fields: List[Tuple[str, str, bool]] = None,
) -> discord.Embed:
    """The Core Architect: Generates structured, high-end layouts."""
    
    # Clean and structure the description for a "Boxed" look
    final_desc = ""
    if description:
        # We wrap descriptions in code blocks or specific formatting if they are short
        # to give that "UI terminal" feel that is more premium than plain text.
        if len(description) < 100 and "\n" not in description:
            final_desc = f"```\n{description}\n```"
        else:
            final_desc = description

    embed = discord.Embed(
        title=title.upper() if title else None, # All-caps titles for authority
        description=final_desc,
        color=color or XERO.PRIMARY,
        timestamp=discord.utils.utcnow() if timestamp else None,
    )

    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    if image:
        embed.set_image(url=image)
    if author_name:
        embed.set_author(name=author_name.upper(), icon_url=author_icon)
    
    if fields:
        # Field separators for that structured "ZNYT" but better look
        for name, value, inline in fields:
            # We use small caps or bold for field names
            embed.add_field(name=f"▹ {name.upper()}", value=value, inline=inline)

    if footer:
        embed.set_footer(text=footer)
        
    return embed

async def brand_embed(embed: discord.Embed, guild: discord.Guild, bot) -> Tuple[discord.Embed, Optional[discord.File]]:
    """Applies XERO Unified Branding with elite overrides."""
    if not guild: return embed, None
        
    settings = await bot.db.get_guild_settings(guild.id)
    
    # 1. Premium Color Sync
    if settings.get("embed_color"):
        try:
            hex_color = settings["embed_color"].lstrip("#")
            embed.color = discord.Color(int(hex_color, 16))
        except: pass
    
    # 2. Elite Footer Structure
    current_footer = embed.footer.text or FOOTER_MAIN
    if "|" not in current_footer and guild.name.upper() not in current_footer.upper():
        embed.set_footer(text=f"{current_footer}  |  {guild.name.upper()}")
    
    # 3. Unified Image Integration
    file = None
    if settings.get("unified_image_data"):
        try:
            image_data = base64.b64decode(settings["unified_image_data"])
            file = discord.File(io.BytesIO(image_data), filename="xero_brand.png")
            embed.set_image(url="attachment://xero_brand.png")
        except: pass
        
    return embed, file

# ── Elite Command Helpers ─────────────────────────────────────────────────────

def comprehensive_embed(**kwargs) -> discord.Embed:
    return _base(**kwargs)

def success_embed(title: str, description: str = "", **kwargs) -> discord.Embed:
    return _base(title=f"✓  {title}", description=description, color=XERO.SUCCESS, **kwargs)

def error_embed(title: str, description: str = "", **kwargs) -> discord.Embed:
    return _base(title=f"✕  {title}", description=description, color=XERO.ERROR, **kwargs)

def info_embed(title: str, description: str = "", **kwargs) -> discord.Embed:
    return _base(title=f"ℹ  {title}", description=description, color=XERO.INFO, **kwargs)

def warning_embed(title: str, description: str = "", **kwargs) -> discord.Embed:
    return _base(title=f"⚠  {title}", description=description, color=XERO.WARNING, **kwargs)

def ai_embed(title: str, description: str = "", **kwargs) -> discord.Embed:
    return _base(title=f"◈  {title}", description=description, color=XERO.AI, footer=FOOTER_AI, **kwargs)

def mod_embed(action: str, user: discord.Member, moderator: discord.Member, reason: str, case_id: int, duration: str = None) -> discord.Embed:
    colors = {"ban": XERO.DANGER, "kick": XERO.MOD, "warn": XERO.WARNING, "timeout": XERO.WARNING}
    fields = [
        ("Target", f"{user.mention}\n`{user.id}`", True),
        ("Moderator", moderator.mention, True),
        ("Reason", f"```\n{reason}\n```", False)
    ]
    if duration: fields.insert(2, ("Duration", duration, True))
    
    embed = _base(title=f"CASE #{case_id}  •  {action}", color=colors.get(action.lower(), XERO.MOD), fields=fields)
    embed.set_thumbnail(url=user.display_avatar.url)
    return embed

def economy_embed(user: discord.Member, wallet: int, bank: int, bank_limit: int, streak: int = 0, net_rank: int = None) -> discord.Embed:
    total = wallet + bank
    pct = min(bank / max(bank_limit, 1) * 100, 100)
    bar = "▰" * int(pct / 10) + "▱" * (10 - int(pct / 10))
    
    fields = [
        ("Liquid Assets", f"`${wallet:,}`", True),
        ("Secure Vault", f"`${bank:,}`", True),
        ("Net Worth", f"**`${total:,}`**", True),
        ("Vault Capacity", f"`{bar}`  **{pct:.0f}%**", False)
    ]
    if streak > 0: fields.append(("Daily Streak", f"**{streak} Days**", True))
    if net_rank: fields.append(("Global Rank", f"**#{net_rank}**", True))
    
    embed = _base(title=f"FINANCIAL PROFILE", color=XERO.ECONOMY, fields=fields)
    embed.set_author(name=user.display_name, icon_url=user.display_avatar.url)
    return embed

def level_embed(user: discord.Member, level: int, xp: int, next_xp: int, total_xp: int, rank: int) -> discord.Embed:
    pct = min(xp / max(next_xp, 1), 1.0)
    bar = "█" * int(pct * 15) + "░" * (15 - int(pct * 15))
    
    fields = [
        ("Current Level", f"**{level}**", True),
        ("Global Rank", f"**#{rank}**", True),
        ("Total Experience", f"`{total_xp:,} XP`", True),
        ("Progression", f"`{bar}`  **{pct*100:.1f}%**", False)
    ]
    
    embed = _base(title="EXPERIENCE OVERVIEW", color=XERO.LEVEL, fields=fields)
    embed.set_author(name=user.display_name, icon_url=user.display_avatar.url)
    return embed

def giveaway_embed(prize: str, end_ts: int, winners: int, host: discord.Member, participants: int = 0, requirements: str = None) -> discord.Embed:
    fields = [
        ("Winners", str(winners), True),
        ("Entries", str(participants), True),
        ("Hosted By", host.mention, True),
        ("Time Remaining", f"<t:{end_ts}:R>", True)
    ]
    if requirements: fields.append(("Requirements", f"```\n{requirements}\n```", False))
    
    return _base(title="ACTIVE GIVEAWAY", description=f"## {prize}", color=XERO.GOLD, fields=fields)

def raid_alert_embed(guild: discord.Guild, join_count: int, window_secs: int) -> discord.Embed:
    desc = f"**{join_count}** identities detected within **{window_secs}s**.\nProtocol: **LOCKDOWN ACTIVE**."
    return _base(title="SECURITY BREACH DETECTED", description=desc, color=XERO.DANGER)

def escalation_embed(user: discord.Member, warn_count: int, action_taken: str, reason: str) -> discord.Embed:
    fields = [
        ("Identity", f"{user.mention}", True),
        ("Violations", f"**{warn_count} Warnings**", True),
        ("Auto-Action", f"**{action_taken.upper()}**", True),
        ("Final Incident", f"```\n{reason}\n```", False)
    ]
    return _base(title="AUTOMATED ESCALATION", color=XERO.DANGER, fields=fields)

def heist_embed(leader: discord.Member, target_bank: int, participants: list, potential: int, success: bool = None, actual_reward: int = None) -> discord.Embed:
    crew = ", ".join(m.mention for m in participants) if participants else "NONE"
    if success is None:
        fields = [("Target Value", f"`${target_bank:,}`", True), ("Potential Loot", f"`${potential:,}`", True), ("Current Crew", crew, False)]
        return _base(title="HEIST IN PROGRESS", description="Recruiting elite crew members...", color=XERO.GOLD, fields=fields)
    elif success:
        return _base(title="HEIST SUCCESS", description=f"Vault breached. Secured **`${actual_reward:,}`**.", color=XERO.SUCCESS)
    else:
        return _base(title="HEIST FAILED", description=f"Mission compromised. Fine: **`${actual_reward:,}`**.", color=XERO.ERROR)

def stock_embed(stocks: list) -> discord.Embed:
    embed = _base(title="MARKET EXCHANGE", color=XERO.ECONOMY)
    if not stocks: embed.description = "```\nMARKET CLOSED\n```"
    else:
        for s in stocks:
            change = s.get("change_pct", 0.0)
            sign = "+" if change >= 0 else ""
            embed.add_field(name=f"▹ {s['symbol']}", value=f"`${s['price']:,}` ({sign}{change:.1f}%)", inline=True)
    return embed

def milestone_embed(guild: discord.Guild, count: int) -> discord.Embed:
    return _base(title="SERVER MILESTONE", description=f"**{guild.name}** has reached **{count:,}** members.", color=XERO.GOLD)

def health_embed(guild: discord.Guild, score: float, grade: str, analysis: str, recs: list) -> discord.Embed:
    fields = [("Grade", f"**{grade}**", True), ("Vitality Score", f"**{score:.1f}/100**", True)]
    if recs: fields.append(("Recommendations", "\n".join(f"▹ {r}" for r in recs[:3]), False))
    return _base(title="SERVER VITALITY REPORT", description=analysis, color=XERO.SUCCESS if score >= 80 else XERO.WARNING, fields=fields)
