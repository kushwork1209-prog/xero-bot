"""
XERO Bot — Discord Channel Database Backup System

Uses BACKUP_CHANNEL_ID env var (set in Railway variables).
Fetches ALL data: guild settings, user levels, economy, mod cases,
warnings, tickets, profiles, images — everything.

On startup:
  1. Scans channel history for JSON attachments
  2. Picks the LARGEST valid file (most complete backup)
  3. Applies it to the DB
  4. Then backs up every 10 minutes automatically
"""
import discord, aiosqlite, asyncio, json, logging, io, datetime, os, base64

logger = logging.getLogger("XERO.Backup")
MIN_SIZE = 5 * 1024  # 5KB minimum to be a real backup


# ── Tables to back up ────────────────────────────────────────────────────────

FULL_TABLES = [
    # Config
    "guild_settings",
    "verification_config",
    "verify_config_v2",
    "automod_config",
    "level_rewards",
    "autoresponder_rules",
    "sticky_messages",
    "highlights",
    "custom_commands",
    "tag_storage",
    "reaction_role_panels",
    "reaction_role_entries",
    "starboard_config",
    "counting_config",
    "confession_config",
    "temp_voice_config",
    "birthday_config",
    "suggestion_config",
    "ticket_config",
    "announcement_channels",
    "security_config",
    "bot_staff",
    "bot_incidents",
    "blacklisted_users",
    "blacklisted_guilds",
    # User data
    "levels",
    "economy",
    "economy_shop",
    "economy_inventory",
    "warnings",
    "mod_cases",
    "user_verifications",
    "verified_members",
    "user_stats",
    "member_profiles",
    "skill_observations",
    "birthdays",
    "marriages",
    "reputation",
    "streaks",
    "giveaways",
    "giveaway_participants",
    "tickets",
    "ticket_events",
    "reminders",
    "afk_users",
    "server_backups",
    "counting_scores",
]


async def _dump_all(db_path: str) -> dict:
    """Dump every table into a dict. Skips missing tables gracefully."""
    out = {}
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        for table in FULL_TABLES:
            try:
                async with db.execute(f"SELECT * FROM {table}") as c:
                    rows = await c.fetchall()
                    out[table] = [dict(r) for r in rows]
            except Exception:
                out[table] = []

        # Also grab any tables we might have missed
        try:
            async with db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ) as c:
                all_tables = [r[0] for r in await c.fetchall()]
            for t in all_tables:
                if t not in out:
                    try:
                        async with db.execute(f"SELECT * FROM {t} LIMIT 500") as c:
                            rows = await c.fetchall()
                            out[t] = [dict(r) for r in rows]
                    except Exception:
                        out[t] = []
        except Exception:
            pass

    return out


async def _apply_all(db_path: str, data: dict):
    """Restore all tables from backup dict."""
    restored = 0
    async with aiosqlite.connect(db_path) as db:
        for table, rows in data.items():
            if not rows:
                continue
            for row in rows:
                try:
                    cols    = list(row.keys())
                    vals    = list(row.values())
                    ph      = ", ".join("?" * len(cols))
                    col_str = ", ".join(f'"{c}"' for c in cols)
                    await db.execute(
                        f'INSERT OR REPLACE INTO "{table}" ({col_str}) VALUES ({ph})',
                        vals
                    )
                    restored += 1
                except Exception:
                    pass
        await db.commit()
    logger.info(f"✅ Restored {restored:,} rows across {len(data)} tables")


def _get_backup_channel_id() -> int | None:
    """Read BACKUP_CHANNEL_ID from env."""
    val = os.getenv("BACKUP_CHANNEL_ID", "").strip()
    if val:
        try:
            return int(val)
        except ValueError:
            logger.warning(f"BACKUP_CHANNEL_ID is not a valid integer: {val!r}")
    return None


async def backup_now(bot) -> bool:
    """Create a backup and post it to the backup channel."""
    ch_id = _get_backup_channel_id()
    if not ch_id:
        return False

    channel = bot.get_channel(ch_id)
    if not channel:
        try:
            channel = await bot.fetch_channel(ch_id)
        except Exception as e:
            logger.warning(f"Cannot access backup channel {ch_id}: {e}")
            return False

    try:
        data    = await _dump_all(bot.db.db_path)
        total_rows = sum(len(v) for v in data.values())

        payload = {
            "xero_backup": True,
            "version": 3,
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "guilds": len(bot.guilds),
            "total_rows": total_rows,
            "tables": len(data),
            "data": data,
        }

        raw     = json.dumps(payload, default=str)
        content = raw.encode("utf-8")

        if len(content) < MIN_SIZE:
            logger.warning(f"Backup too small ({len(content)}B) — skipping")
            return False

        ts    = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        fname = f"xero_backup_{ts}.json"
        fobj  = discord.File(io.BytesIO(content), filename=fname)

        size_kb = len(content) / 1024
        embed = discord.Embed(
            title="💾  XERO Auto-Backup",
            color=0x00D4FF,
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="Size",       value=f"`{size_kb:.1f} KB`",        inline=True)
        embed.add_field(name="Tables",     value=f"`{payload['tables']}`",      inline=True)
        embed.add_field(name="Rows",       value=f"`{total_rows:,}`",           inline=True)
        embed.add_field(name="Servers",    value=f"`{payload['guilds']}`",      inline=True)
        embed.add_field(name="Timestamp",  value=f"<t:{int(datetime.datetime.utcnow().timestamp())}:F>", inline=True)
        embed.set_footer(text="XERO DB Backup  ·  Do not delete files in this channel")

        await channel.send(embed=embed, file=fobj)
        logger.info(f"✅ Backup posted to #{channel.name} — {size_kb:.1f} KB, {total_rows:,} rows")
        return True

    except Exception as e:
        logger.error(f"Backup failed: {e}")
        return False


async def restore_latest(bot) -> bool:
    """
    Scan backup channel for JSON files.
    Picks the LARGEST valid one (most complete backup).
    Restores it.
    """
    ch_id = _get_backup_channel_id()
    if not ch_id:
        logger.info("No BACKUP_CHANNEL_ID set — skipping restore")
        return False

    channel = bot.get_channel(ch_id)
    if not channel:
        try:
            channel = await bot.fetch_channel(ch_id)
        except Exception as e:
            logger.warning(f"Cannot access backup channel {ch_id}: {e}")
            return False

    logger.info(f"Scanning #{channel.name} for backups...")

    best_data    = None
    best_size    = 0
    best_fname   = None
    best_rows    = 0

    try:
        import aiohttp
        async for message in channel.history(limit=500):
            for att in message.attachments:
                if not att.filename.endswith(".json"):
                    continue
                if att.size < MIN_SIZE:
                    continue
                if att.size < best_size:
                    continue  # Already found something bigger
                try:
                    async with aiohttp.ClientSession() as s:
                        async with s.get(att.url, timeout=aiohttp.ClientTimeout(total=20)) as r:
                            if r.status != 200:
                                continue
                            raw    = await r.read()
                            parsed = json.loads(raw)
                            if not (parsed.get("xero_backup") and parsed.get("data")):
                                continue
                            rows = parsed.get("total_rows", sum(len(v) for v in parsed["data"].values()))
                            if att.size > best_size or rows > best_rows:
                                best_data  = parsed["data"]
                                best_size  = att.size
                                best_fname = att.filename
                                best_rows  = rows
                except Exception as e:
                    logger.debug(f"Could not read {att.filename}: {e}")

        if best_data:
            logger.info(f"Restoring from {best_fname} ({best_size/1024:.1f} KB, {best_rows:,} rows)")
            await _apply_all(bot.db.db_path, best_data)
            return True
        else:
            logger.info("No valid backups found — starting fresh")
            return False

    except Exception as e:
        logger.error(f"Restore scan failed: {e}")
        return False
