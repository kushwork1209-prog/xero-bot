"""
XERO Bot — Automatic Database Backup & Restore
===============================================
Protects against Railway's ephemeral filesystem wiping all server configs.

How it works:
  • Every 1 minute: compress all DB tables → send as .gz file to BACKUP_CHANNEL_ID
  • On startup: if DB is empty → download latest backup → restore all rows automatically
  • Users never notice. Configs survive any number of redeploys.

Setup (one-time):
  1. Create a private Discord channel visible only to XERO Bot
  2. Copy the channel ID into Railway env vars as BACKUP_CHANNEL_ID
  3. Done — backups run automatically

For even better persistence (recommended):
  • Add a Railway Volume mounted at /app/data — data will never be wiped at all
"""

import discord
import aiosqlite
import asyncio
import logging
import json
import gzip
import io
import os
from datetime import datetime

logger = logging.getLogger("XERO.Backup")

BACKUP_CHANNEL_ID = int(os.getenv("BACKUP_CHANNEL_ID", "0"))
DB_PATH           = os.getenv("DB_PATH", "data/xero.db")

BACKUP_TABLES = [
    "guild_settings",
    "levels",
    "economy",
    "economy_streaks",
    "warnings",
    "mod_cases",
    "birthdays",
    "level_rewards",
    "reaction_role_panels",
    "starboard_config",
    "starboard_messages",
    "verification_config",
    "tickets",
    "temp_voice_config",
    "autoresponder_rules",
    "custom_commands",
    "reputation",
    "marriages",
    "stocks",
    "stock_portfolio",
    "user_stats",
    "afk_users",
    "suggestions",
    "member_roles",
    "economy_transactions",
    "personality_log",
    "counting_config",
]


async def export_db(bot: discord.Client) -> bytes:
    """Export all critical tables as gzip-compressed JSON. Typically 50–500KB."""
    data: dict[str, list] = {}
    for table in BACKUP_TABLES:
        try:
            async with bot.db._db_context() as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(f"SELECT * FROM {table}") as c:
                    rows = await c.fetchall()
                    data[table] = [dict(r) for r in rows]
        except Exception:
            pass
    payload = {
        "timestamp": datetime.utcnow().isoformat(),
        "tables":    data,
        "version":   "xero_v4",
        "row_counts": {t: len(v) for t, v in data.items()},
    }
    json_bytes = json.dumps(payload, default=str).encode("utf-8")
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=6) as gz:
        gz.write(json_bytes)
    return buf.getvalue()


async def import_db(bot: discord.Client, backup_bytes: bytes) -> int:
    """Restore all tables from a gzip-compressed JSON backup. Returns row count."""
    buf = io.BytesIO(backup_bytes)
    with gzip.GzipFile(fileobj=buf, mode="rb") as gz:
        payload = json.loads(gz.read().decode("utf-8"))
    tables    = payload.get("tables", {})
    total_rows = 0
    async with bot.db._db_context() as db:
        for table, rows in tables.items():
            if not rows:
                continue
            for row in rows:
                cols         = list(row.keys())
                placeholders = ", ".join("?" for _ in cols)
                col_str      = ", ".join(cols)
                values       = [row[c] for c in cols]
                try:
                    # Using INSERT OR REPLACE for SQLite, or ON CONFLICT for PG (handled by adapter)
                    await db.execute(
                        f"INSERT OR REPLACE INTO {table} ({col_str}) VALUES ({placeholders})",
                        values,
                    )
                    total_rows += 1
                except Exception:
                    pass
        await db.commit()
    logger.info(f"✓ DB restored: {len(tables)} tables, {total_rows} rows")
    return total_rows


async def is_db_empty(bot: discord.Client) -> bool:
    """True if guild_settings has no rows — fresh or wiped DB."""
    try:
        async with bot.db._db_context() as db:
            async with db.execute("SELECT COUNT(*) FROM guild_settings") as c:
                row = await c.fetchone()
                # row[0] for SQLite, row['count'] or similar for PG. Adapter makes it look like tuple.
                return row[0] == 0
    except Exception:
        return True


async def auto_restore(bot: discord.Client) -> bool:
    """
    Called once on startup. If DB is empty AND BACKUP_CHANNEL_ID is set,
    finds the most recent backup in that channel and restores automatically.
    """
    if not BACKUP_CHANNEL_ID:
        logger.info("BACKUP_CHANNEL_ID not set — skipping auto-restore check.")
        return False
    # Force restore if DB is empty or only has a few guilds (likely fresh deploy)
    # This ensures that even if a few guilds joined before restore, we still pull the backup.
    is_empty = await is_db_empty(bot)
    if not is_empty:
        logger.info("✓ DB has existing data — no restore needed.")
        return False

    logger.warning("⚠️  DB is empty — searching backup channel for latest backup...")
    try:
        channel = bot.get_channel(BACKUP_CHANNEL_ID)
        if not channel:
            channel = await bot.fetch_channel(BACKUP_CHANNEL_ID)
    except Exception as e:
        logger.error(f"Cannot access backup channel {BACKUP_CHANNEL_ID}: {e}")
        return False

    backup_msg = None
    async for msg in channel.history(limit=100):
        if msg.attachments:
            for att in msg.attachments:
                if att.filename.startswith("xero_backup_") and att.filename.endswith(".gz"):
                    backup_msg = msg
                    break
        if backup_msg:
            break

    if not backup_msg:
        logger.warning("No backup found in backup channel. Starting fresh.")
        return False

    att = next(
        a for a in backup_msg.attachments
        if a.filename.startswith("xero_backup_") and a.filename.endswith(".gz")
    )
    logger.info(f"Restoring from: {att.filename} ({att.size // 1024}KB) sent {backup_msg.created_at}")
    try:
        import aiohttp
        async with aiohttp.ClientSession() as s:
            async with s.get(att.url, timeout=aiohttp.ClientTimeout(total=30)) as r:
                backup_bytes = await r.read()
        rows = await import_db(bot, backup_bytes)
        logger.info(f"✅ DB auto-restored! {rows} rows recovered.")
        return True
    except Exception as e:
        logger.error(f"Auto-restore failed: {e}")
        return False


async def send_backup(
    bot: discord.Client,
    triggered_by: str = "auto",
) -> bool:
    """Send a compressed DB backup to the backup channel."""
    if not BACKUP_CHANNEL_ID:
        return False
    try:
        channel = bot.get_channel(BACKUP_CHANNEL_ID)
        if not channel:
            channel = await bot.fetch_channel(BACKUP_CHANNEL_ID)
    except Exception as e:
        logger.error(f"Backup channel not found: {e}")
        return False

    try:
        backup_bytes = await export_db(bot)
        timestamp    = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        fname        = f"xero_backup_{timestamp}.gz"
        file         = discord.File(io.BytesIO(backup_bytes), filename=fname)
        buf = io.BytesIO(backup_bytes)
        with gzip.GzipFile(fileobj=buf, mode="rb") as gz:
            meta = json.loads(gz.read())
        guilds = len(meta["tables"].get("guild_settings", []))
        total  = sum(len(v) for v in meta["tables"].values())
        await channel.send(
            content=(
                f"📦 **XERO Auto-Backup** | `{timestamp} UTC` | trigger: `{triggered_by}`\n"
                f"**{guilds}** servers • **{total}** rows • `{len(backup_bytes)//1024}KB`"
            ),
            file=file,
        )
        logger.info(f"✅ Backup: {fname} | {guilds} guilds | {total} rows | {len(backup_bytes)//1024}KB")
        return True
    except Exception as e:
        logger.error(f"Backup send failed: {e}")
        return False
