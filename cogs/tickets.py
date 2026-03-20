"""
XERO Bot — Ticket System (Complete Rewrite)
Every action permanently logged. Full case log on close. AI summary. Paginated history.
"""
import discord, aiosqlite, asyncio, io, logging, datetime
from discord.ext import commands
from discord import app_commands
from utils.embeds import XERO, success_embed, error_embed, info_embed

logger = logging.getLogger("XERO.Tickets")

TC_OPEN    = 0x2B2D31
TC_CLAIMED = 0x5865F2
TC_CLOSED  = 0x1A1A1A
TC_HISTORY = 0x23272A


async def _log_event(db_path, ticket_id, guild_id, user_id, event_type, detail=None):
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO ticket_events (ticket_id, guild_id, user_id, event_type, detail) VALUES (?,?,?,?,?)",
            (ticket_id, guild_id, user_id, event_type, detail)
        )
        await db.commit()


async def _get_ticket(db_path, channel_id):
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM tickets WHERE channel_id=?", (channel_id,)) as c:
            row = await c.fetchone()
    return dict(row) if row else None


async def _get_events(db_path, ticket_id):
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM ticket_events WHERE ticket_id=? ORDER BY created_at ASC", (ticket_id,)
        ) as c:
            return [dict(r) for r in await c.fetchall()]


def _fmt_event(ev, guild):
    user   = guild.get_member(ev["user_id"])
    name   = user.display_name if user else f"User {ev['user_id']}"
    ts     = ev["created_at"][:16].replace("T"," ")
    icons  = {"opened":"📂","claimed":"🙋","unclaimed":"🔓","user_added":"➕","user_removed":"➖","closed":"🔒","rating":"⭐"}
    icon   = icons.get(ev["event_type"], "•")
    detail = f" — {ev['detail']}" if ev.get("detail") else ""
    return f"`{ts}` {icon} **{name}** {ev['event_type'].replace('_',' ')}{detail}"


async def _close_flow(interaction, bot, reason="Resolved"):
    channel   = interaction.channel
    guild     = interaction.guild
    db_path   = bot.db.db_path
    ticket    = await _get_ticket(db_path, channel.id)
    if not ticket:
        return await interaction.response.send_message(embed=error_embed("Not a Ticket","This isn't a ticket channel."), ephemeral=True)

    tid    = ticket["ticket_id"]
    opener = guild.get_member(ticket["user_id"])
    closer = interaction.user

    await interaction.response.send_message(embed=discord.Embed(description="🔒  Closing — generating case log...", color=TC_CLOSED))

    await _log_event(db_path, tid, guild.id, closer.id, "closed", f"Closed by {closer} — {reason}")

    async with aiosqlite.connect(db_path) as db:
        await db.execute("UPDATE tickets SET status='closed', closed_at=datetime('now'), closed_by=? WHERE ticket_id=?", (closer.id, tid))
        await db.commit()

    # Collect messages
    messages = []
    async for msg in channel.history(limit=500, oldest_first=True):
        messages.append({"ts": msg.created_at.strftime("%H:%M"), "author": msg.author.display_name, "content": msg.content[:200], "is_bot": msg.author.bot})

    # Refresh ticket for rating
    ticket = await _get_ticket(db_path, channel.id)
    events = await _get_events(db_path, tid)

    # Duration
    duration = "—"
    try:
        od     = datetime.datetime.fromisoformat(ticket["created_at"].replace("Z",""))
        cd     = datetime.datetime.utcnow()
        delta  = cd - od
        h, rem = divmod(int(delta.total_seconds()), 3600)
        duration = f"{h}h {rem//60}m" if h else f"{rem//60}m"
    except Exception: pass

    # AI summary
    ai_summary = None
    try:
        human = [m for m in messages if not m["is_bot"] and m["content"].strip()]
        if human:
            convo = "\n".join(f"{m['author']}: {m['content']}" for m in human[:40])
            ai_summary = await bot.nvidia.ask(
                f"Summarize this Discord support ticket in 2-3 sentences. Cover: what the issue was, what was done, outcome.\n\n{convo}"
            )
    except Exception as e:
        logger.debug(f"AI summary: {e}")

    if ai_summary:
        async with aiosqlite.connect(db_path) as db:
            await db.execute("UPDATE tickets SET ai_summary=?, message_count=? WHERE ticket_id=?", (ai_summary, len(messages), tid))
            await db.commit()

    # Event timeline
    staff_evs = [ev for ev in events if ev["event_type"] not in ("opened","message")]
    timeline  = "\n".join(_fmt_event(ev, guild) for ev in staff_evs) if staff_evs else "*No staff events logged.*"

    # Case log embed
    e = discord.Embed(title=f"📁  Case #{tid}  —  Closed", color=TC_CLOSED, timestamp=discord.utils.utcnow())
    o_str = f"{opener.mention} `{opener}` (`{ticket['user_id']}`)" if opener else f"`<@{ticket['user_id']}>`"
    c_str = f"{closer.mention} (`{closer}`)"
    e.add_field(name="👤  Opened By", value=o_str,                                inline=False)
    e.add_field(name="🔒  Closed By", value=c_str,                                inline=True)
    e.add_field(name="⏱️  Duration",  value=duration,                             inline=True)
    e.add_field(name="💬  Messages",  value=str(len(messages)),                   inline=True)
    e.add_field(name="📂  Topic",     value=ticket.get("topic","General Support"), inline=True)
    if ticket.get("rating"):
        fb = f"\n*{ticket['rating_feedback']}*" if ticket.get("rating_feedback") else ""
        e.add_field(name="⭐  Rating", value=f"{'⭐'*ticket['rating']} ({ticket['rating']}/5){fb}", inline=True)
    e.add_field(name="📋  Event Timeline",  value=timeline[:900],                  inline=False)
    if ai_summary:
        e.add_field(name="🤖  AI Case Summary", value=ai_summary[:600],            inline=False)
    if opener:
        e.set_thumbnail(url=opener.display_avatar.url)
    e.set_footer(text=f"Case #{tid}  •  {guild.name}  •  XERO Tickets")

    # Transcript file
    lines = [f"CASE #{tid} TRANSCRIPT — {guild.name}", f"Opener: {opener} ({ticket['user_id']})", f"Topic: {ticket.get('topic','General')}", f"Duration: {duration}", "="*60, ""]
    for m in messages:
        lines.append(f"[{m['ts']}] {m['author']}: {m['content']}")
    txt_file = discord.File(io.StringIO("\n".join(lines)), filename=f"case-{tid}.txt")

    # Send to log channel
    settings = await bot.db.get_guild_settings(guild.id)
    log_ch   = guild.get_channel(settings.get("ticket_log_channel_id") or settings.get("log_channel_id") or 0)
    if log_ch:
        try: await log_ch.send(embed=e, file=txt_file)
        except Exception: await channel.send(embed=e)
    else:
        await channel.send(embed=e)

    await asyncio.sleep(8)
    try: await channel.delete(reason=f"Ticket #{tid} closed by {closer}")
    except Exception as ex: logger.error(f"Delete: {ex}")



# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STAFF INTELLIGENCE BRIEF
# Generated fresh when a ticket opens. Posted to channel, visible staff only.
# Never stored in ticket history or /ticket history.
# Pulls: account age, cross-server bans, all mod history, economy, XP, tickets
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _build_staff_brief(bot, guild, member: discord.Member, ticket_id: int) -> discord.Embed:
    """
    Pulls every piece of data we have on this user across the entire XERO network
    and generates a staff-only intelligence brief.
    Nemotron reads it all and writes a natural-language overview.
    """
    uid    = member.id
    gid    = guild.id
    db_path = bot.db.db_path
    age_days = (discord.utils.utcnow() - member.created_at).days

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row

        # Mod cases in THIS server
        async with db.execute(
            "SELECT action, reason, timestamp FROM mod_cases WHERE guild_id=? AND user_id=? ORDER BY case_id DESC LIMIT 10",
            (gid, uid)
        ) as c: local_cases = [dict(r) for r in await c.fetchall()]

        # ★ Cross-server mod cases (ALL guilds in XERO network)
        async with db.execute(
            "SELECT guild_id, action, reason, timestamp FROM mod_cases WHERE user_id=? AND guild_id!=? ORDER BY case_id DESC LIMIT 20",
            (uid, gid)
        ) as c: global_cases = [dict(r) for r in await c.fetchall()]

        # Cross-server bans specifically
        cross_bans = [c for c in global_cases if c["action"].lower() in ("ban","tempban")]

        # Warnings in this server
        async with db.execute(
            "SELECT reason, timestamp FROM warnings WHERE guild_id=? AND user_id=? ORDER BY id DESC LIMIT 5",
            (gid, uid)
        ) as c: warns = [dict(r) for r in await c.fetchall()]

        # Global blacklist
        try:
            async with db.execute("SELECT reason FROM blacklisted_users WHERE user_id=?", (uid,)) as c:
                blacklisted = await c.fetchone()
        except Exception: blacklisted = None

        # Tickets in this server
        async with db.execute(
            "SELECT COUNT(*) as total, SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) as open_count FROM tickets WHERE guild_id=? AND user_id=?",
            (gid, uid)
        ) as c:
            tkt = dict(await c.fetchone())

        # Economy
        async with db.execute("SELECT wallet, bank, total_earned FROM economy WHERE guild_id=? AND user_id=?", (gid, uid)) as c:
            eco = dict(await c.fetchone()) if (row := await c.fetchone()) else None
        if eco is None:
            try:
                async with db.execute("SELECT wallet, bank, total_earned FROM economy WHERE guild_id=? AND user_id=?", (gid, uid)) as c:
                    r = await c.fetchone()
                    eco = dict(r) if r else {"wallet":0,"bank":0,"total_earned":0}
            except Exception: eco = {"wallet":0,"bank":0,"total_earned":0}

        # XP / Level
        async with db.execute("SELECT level, total_xp FROM levels WHERE guild_id=? AND user_id=?", (gid, uid)) as c:
            lvl_row = await c.fetchone()
        lvl = dict(lvl_row) if lvl_row else {"level":0,"total_xp":0}

        # Previous closed tickets (summary only)
        async with db.execute(
            "SELECT ticket_id, topic, ai_summary, closed_at FROM tickets WHERE guild_id=? AND user_id=? AND status=\'closed\' ORDER BY ticket_id DESC LIMIT 3",
            (gid, uid)
        ) as c: prev_tickets = [dict(r) for r in await c.fetchall()]

        # User activity stats
        async with db.execute("SELECT commands_used, messages_sent FROM user_stats WHERE guild_id=? AND user_id=?", (gid, uid)) as c:
            stats = dict(await c.fetchone()) if (r2 := await c.fetchone()) else {"commands_used":0,"messages_sent":0}

    # How many XERO servers this user appears in
    xero_server_count = len(set(c["guild_id"] for c in global_cases))

    # Build the raw data for AI
    lines = [
        f"User: {member.display_name} ({member}), ID: {uid}",
        f"Account age: {age_days} days old",
        f"Joined this server: {(discord.utils.utcnow() - member.joined_at).days if member.joined_at else '?'} days ago",
        f"Server level: {lvl['level']}, XP: {lvl['total_xp']:,}",
        f"Economy: ${eco['wallet']:,} wallet, ${eco['bank']:,} bank, ${eco['total_earned']:,} total earned",
        f"Activity: {stats.get('commands_used',0):,} commands used, {stats.get('messages_sent',0):,} messages",
        f"Tickets in this server: {tkt['total']} total, {tkt['open_count']} open",
        f"Warnings in this server: {len(warns)}",
    ]
    if local_cases:
        case_summary = ", ".join(f"{c['action']} ({c['timestamp'][:10]})" for c in local_cases[:5])
        lines.append(f"Local mod actions: {case_summary}")
    if cross_bans:
        ban_details = []
        for b in cross_bans[:5]:
            s_name = bot.get_guild(b["guild_id"])
            sname  = s_name.name if s_name else f"Server {b['guild_id']}"
            ban_details.append(f"banned from {sname} on {b['timestamp'][:10]}")
        lines.append(f"CROSS-SERVER BANS: {', '.join(ban_details)}")
    if global_cases and not cross_bans:
        lines.append(f"Cross-server mod history: {len(global_cases)} actions across {xero_server_count} other XERO servers")
    if blacklisted:
        lines.append(f"GLOBAL XERO BLACKLIST: YES — {blacklisted['reason']}")
    if prev_tickets:
        for pt in prev_tickets[:2]:
            if pt.get("ai_summary"):
                lines.append(f"Previous ticket #{pt['ticket_id']} ({pt.get('topic','?')}): {pt['ai_summary'][:100]}")

    raw_data = "\n".join(lines)

    # AI writes the brief
    ai_brief = None
    try:
        prompt = (
            f"You are writing a staff intelligence brief for a Discord support ticket. "
            f"Write a concise 3-4 sentence overview of this user for the staff team to read before responding. "
            f"Be direct and professional. Flag anything concerning clearly (bans, warnings, blacklist). "
            f"If there are cross-server bans, that\'s the most important thing to lead with.\n\n"
            f"Data:\n{raw_data}"
        )
        ai_brief = await bot.nvidia.ask(prompt)
    except Exception as e:
        logger.debug(f"Staff brief AI: {e}")

    # Build the embed
    risk = "🔴 HIGH" if (blacklisted or len(cross_bans) >= 2) else ("🟡 MEDIUM" if (cross_bans or len(warns) >= 3 or len(local_cases) >= 3) else "🟢 LOW")

    e = discord.Embed(
        title=f"👁  Staff Brief — {member.display_name}",
        description=ai_brief or raw_data[:800],
        color=0xFF1744 if "HIGH" in risk else (0xFFB800 if "MEDIUM" in risk else 0x2B2D31),
        timestamp=discord.utils.utcnow()
    )
    e.set_thumbnail(url=member.display_avatar.url)
    e.add_field(name="⚠️ Risk Level",       value=risk,                                          inline=True)
    e.add_field(name="📅 Account Age",      value=f"{age_days} days",                            inline=True)
    e.add_field(name="📊 Server Level",     value=str(lvl["level"]),                             inline=True)
    e.add_field(name="💬 Messages",         value=f"{stats.get('messages_sent',0):,}",           inline=True)
    e.add_field(name="⚠️ Local Warns",      value=str(len(warns)),                               inline=True)
    e.add_field(name="🛡️ Local Cases",      value=str(len(local_cases)),                         inline=True)

    if cross_bans:
        ban_lines = []
        for b in cross_bans[:4]:
            s_obj  = bot.get_guild(b["guild_id"])
            sname  = s_obj.name if s_obj else f"Server {b['guild_id']}"
            ban_lines.append(f"• **{sname}** — {b['timestamp'][:10]} — *{(b.get('reason') or 'No reason')[:40]}*")
        e.add_field(name=f"🚨 BANNED IN {len(cross_bans)} OTHER SERVER(S)", value="\n".join(ban_lines), inline=False)

    if blacklisted:
        e.add_field(name="🚫 XERO GLOBAL BLACKLIST", value=f"Reason: {blacklisted['reason']}", inline=False)

    if prev_tickets:
        pt_lines = [f"• **#{pt['ticket_id']}** {pt.get('topic','?')} — {pt.get('ai_summary','')[:60]}..." for pt in prev_tickets if pt.get("ai_summary")]
        if pt_lines:
            e.add_field(name="📁 Previous Tickets", value="\n".join(pt_lines[:3]), inline=False)

    e.set_footer(text=f"Case #{ticket_id}  •  Staff Only — not visible to {member.display_name}  •  XERO Intelligence")
    return e

class TicketOpenView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)

    @discord.ui.button(label="Open a Ticket", style=discord.ButtonStyle.secondary, custom_id="xero_t_open_v2", emoji="🎫")
    async def open_ticket(self, interaction, button):
        bot      = interaction.client
        guild    = interaction.guild
        settings = await bot.db.get_guild_settings(guild.id)
        async with aiosqlite.connect(bot.db.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT channel_id FROM tickets WHERE guild_id=? AND user_id=? AND status='open'", (guild.id, interaction.user.id)) as c:
                existing = await c.fetchone()
        if existing:
            ch = guild.get_channel(existing["channel_id"])
            if ch: return await interaction.response.send_message(f"You already have an open ticket: {ch.mention}", ephemeral=True)

        cat  = guild.get_channel(settings.get("ticket_category_id") or 0)
        role = guild.get_role(settings.get("ticket_support_role_id") or 0)
        ow   = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user:   discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True, read_message_history=True),
            guild.me:           discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, manage_messages=True),
        }
        if role: ow[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        try:
            ch = await guild.create_text_channel(
                f"ticket-{interaction.user.display_name[:18].lower().replace(' ','-')}",
                category=cat, overwrites=ow,
                topic=f"Support ticket — {interaction.user} — {discord.utils.utcnow().strftime('%Y-%m-%d')}"
            )
        except Exception as ex:
            return await interaction.response.send_message(f"❌ {ex}", ephemeral=True)

        async with aiosqlite.connect(bot.db.db_path) as db:
            cur = await db.execute("INSERT INTO tickets (guild_id, channel_id, user_id) VALUES (?,?,?)", (guild.id, ch.id, interaction.user.id))
            tid = cur.lastrowid
            await db.commit()

        await _log_event(bot.db.db_path, tid, guild.id, interaction.user.id, "opened", f"Opened by {interaction.user}")

        emb = discord.Embed(title=f"Ticket #{tid}", description=f"Hello {interaction.user.mention}!\n\nDescribe your issue clearly and a staff member will assist you shortly.", color=TC_OPEN, timestamp=discord.utils.utcnow())
        emb.set_thumbnail(url=interaction.user.display_avatar.url)
        emb.set_footer(text=f"Case #{tid}  •  XERO Tickets")
        ping = interaction.user.mention + (f" | {role.mention}" if role else "")
        await ch.send(content=ping, embed=emb, view=TicketActionView(bot))

        # ── Staff Intelligence Brief ──────────────────────────────────────
        # Runs async after channel is created — staff see this at the top, opener cannot
        try:
            brief = await _build_staff_brief(bot, guild, interaction.user, tid)
            # Send to staff — if there's a support role, mention them so they see it
            # This is sent WITHOUT @mention so it appears centered/pinned at top
            brief_msg = await ch.send(embed=brief)
            try: await brief_msg.pin()
            except Exception: pass  # no pin perms? just leave it at top
        except Exception as ex:
            logger.error(f"Staff brief: {ex}")

        await interaction.response.send_message(f"✅ Ticket opened: {ch.mention}", ephemeral=True)


class TicketActionView(discord.ui.View):
    def __init__(self, bot=None): super().__init__(timeout=None); self.bot = bot

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.secondary, custom_id="xero_t_claim_v2", emoji="🙋")
    async def claim(self, interaction, button):
        if not interaction.user.guild_permissions.manage_channels:
            return await interaction.response.send_message("Staff only.", ephemeral=True)
        bot    = interaction.client
        ticket = await _get_ticket(bot.db.db_path, interaction.channel.id)
        if not ticket: return await interaction.response.send_message("Not a ticket.", ephemeral=True)
        if ticket.get("claimed_by"):
            m = interaction.guild.get_member(ticket["claimed_by"])
            return await interaction.response.send_message(f"Already claimed by {m.mention if m else 'someone'}.", ephemeral=True)
        async with aiosqlite.connect(bot.db.db_path) as db:
            await db.execute("UPDATE tickets SET claimed_by=? WHERE ticket_id=?", (interaction.user.id, ticket["ticket_id"]))
            await db.commit()
        await _log_event(bot.db.db_path, ticket["ticket_id"], interaction.guild.id, interaction.user.id, "claimed", f"Claimed by {interaction.user.display_name}")
        await interaction.response.send_message(embed=discord.Embed(description=f"🙋 {interaction.user.mention} claimed this ticket.", color=TC_CLAIMED))

    @discord.ui.button(label="Unclaim", style=discord.ButtonStyle.secondary, custom_id="xero_t_unclaim_v2", emoji="🔓")
    async def unclaim(self, interaction, button):
        if not interaction.user.guild_permissions.manage_channels:
            return await interaction.response.send_message("Staff only.", ephemeral=True)
        bot    = interaction.client
        ticket = await _get_ticket(bot.db.db_path, interaction.channel.id)
        if not ticket: return await interaction.response.send_message("Not a ticket.", ephemeral=True)
        async with aiosqlite.connect(bot.db.db_path) as db:
            await db.execute("UPDATE tickets SET claimed_by=NULL WHERE ticket_id=?", (ticket["ticket_id"],))
            await db.commit()
        await _log_event(bot.db.db_path, ticket["ticket_id"], interaction.guild.id, interaction.user.id, "unclaimed", f"Released by {interaction.user.display_name}")
        await interaction.response.send_message(embed=discord.Embed(description=f"🔓 {interaction.user.mention} released this ticket.", color=TC_OPEN))

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, custom_id="xero_t_close_v2", emoji="🔒")
    async def close(self, interaction, button):
        if not interaction.user.guild_permissions.manage_channels:
            return await interaction.response.send_message("Staff only.", ephemeral=True)
        await _close_flow(interaction, interaction.client)


class TicketHistoryView(discord.ui.View):
    def __init__(self, bot, guild, tickets, idx=0):
        super().__init__(timeout=120)
        self.bot     = bot
        self.guild   = guild
        self.tickets = tickets
        self.idx     = idx
        self._upd()

    def _upd(self):
        self.prev_btn.disabled = self.idx <= 0
        self.next_btn.disabled = self.idx >= len(self.tickets) - 1
        self.counter.label     = f"{self.idx+1} / {len(self.tickets)}"

    async def _embed(self):
        t      = self.tickets[self.idx]
        events = await _get_events(self.bot.db.db_path, t["ticket_id"])
        opener = self.guild.get_member(t["user_id"])
        closer = self.guild.get_member(t.get("closed_by") or 0)
        o_str  = f"{opener.mention} `{opener}` (`{t['user_id']}`)" if opener else f"`<@{t['user_id']}>`"
        c_str  = f"{closer.mention} (`{closer}`)" if closer else (f"`<@{t['closed_by']}>`" if t.get("closed_by") else "Unknown")
        duration = "—"
        try:
            od     = datetime.datetime.fromisoformat(t["created_at"].replace("Z",""))
            cd     = datetime.datetime.fromisoformat(t["closed_at"].replace("Z",""))
            delta  = cd - od
            h, rem = divmod(int(delta.total_seconds()), 3600)
            duration = f"{h}h {rem//60}m" if h else f"{rem//60}m"
        except Exception: pass

        staff_evs = [ev for ev in events if ev["event_type"] not in ("opened","message")]
        timeline  = "\n".join(_fmt_event(ev, self.guild) for ev in staff_evs) if staff_evs else "*No staff events on record.*"

        e = discord.Embed(title=f"📁  Case #{t['ticket_id']}", color=TC_HISTORY, timestamp=discord.utils.utcnow())
        e.add_field(name="👤  Opened By",  value=o_str,                               inline=False)
        e.add_field(name="🔒  Closed By",  value=c_str,                               inline=True)
        e.add_field(name="⏱️  Duration",   value=duration,                            inline=True)
        e.add_field(name="💬  Messages",   value=str(t.get("message_count") or "—"), inline=True)
        e.add_field(name="📂  Topic",      value=t.get("topic","General Support"),     inline=True)
        ts_open = f"<t:{int(datetime.datetime.fromisoformat(t['created_at'].replace('Z','')).timestamp())}:f>" if t.get("created_at") else "—"
        e.add_field(name="📅  Opened",     value=ts_open,                             inline=True)
        if t.get("rating"):
            fb = f"\n*{t['rating_feedback']}*" if t.get("rating_feedback") else ""
            e.add_field(name="⭐  Rating", value=f"{'⭐'*t['rating']} ({t['rating']}/5){fb}", inline=True)
        e.add_field(name="📋  Event Log",  value=timeline[:800],                      inline=False)
        if t.get("ai_summary"):
            e.add_field(name="🤖  AI Case Summary", value=t["ai_summary"][:500],     inline=False)
        else:
            e.add_field(name="🤖  AI Summary", value="*Not available for this case.*", inline=False)
        if opener: e.set_thumbnail(url=opener.display_avatar.url)
        e.set_footer(text=f"Case {self.idx+1} of {len(self.tickets)}  •  #{t['ticket_id']}  •  XERO Tickets")
        return e

    @discord.ui.button(emoji="◀", style=discord.ButtonStyle.secondary, custom_id="th_prev_v2")
    async def prev_btn(self, interaction, button):
        self.idx -= 1; self._upd()
        await interaction.response.edit_message(embed=await self._embed(), view=self)

    @discord.ui.button(label="1 / 1", style=discord.ButtonStyle.secondary, disabled=True, custom_id="th_ctr_v2")
    async def counter(self, interaction, button): pass

    @discord.ui.button(emoji="▶", style=discord.ButtonStyle.secondary, custom_id="th_next_v2")
    async def next_btn(self, interaction, button):
        self.idx += 1; self._upd()
        await interaction.response.edit_message(embed=await self._embed(), view=self)


class Tickets(commands.GroupCog, name="ticket"):
    def __init__(self, bot):
        self.bot = bot
        bot.add_view(TicketOpenView())
        bot.add_view(TicketActionView(bot))

    @app_commands.command(name="setup", description="Set up the ticket panel. Posts the Open a Ticket button.")
    @app_commands.describe(channel="Where to post the panel", support_role="Role to ping", category="Category for ticket channels", log_channel="Where case logs are posted on close", message="Custom panel message")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup(self, interaction: discord.Interaction, channel: discord.TextChannel,
                    support_role: discord.Role=None, category: discord.CategoryChannel=None,
                    log_channel: discord.TextChannel=None, message: str=None):
        if support_role: await self.bot.db.update_guild_setting(interaction.guild.id, "ticket_support_role_id", support_role.id)
        if category:     await self.bot.db.update_guild_setting(interaction.guild.id, "ticket_category_id", category.id)
        if log_channel:  await self.bot.db.update_guild_setting(interaction.guild.id, "ticket_log_channel_id", log_channel.id)
        txt  = message or "Need help? Click the button below to open a support ticket."
        emb  = discord.Embed(title="Support Tickets", description=txt, color=TC_OPEN)
        if interaction.guild.icon: emb.set_thumbnail(url=interaction.guild.icon.url)
        emb.set_footer(text=f"{interaction.guild.name}  •  XERO Tickets")
        await channel.send(embed=emb, view=TicketOpenView())
        desc = f"Panel posted in {channel.mention}."
        if support_role: desc += f"\nSupport role: {support_role.mention}"
        if category:     desc += f"\nCategory: {category.mention}"
        if log_channel:  desc += f"\nCase logs → {log_channel.mention}"
        await interaction.response.send_message(embed=success_embed("Ticket System Ready", desc), ephemeral=True)

    @app_commands.command(name="close", description="Close this ticket and generate a full case log.")
    @app_commands.describe(reason="Reason for closing")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def close(self, interaction: discord.Interaction, reason: str="Resolved"):
        await _close_flow(interaction, self.bot, reason)

    @app_commands.command(name="claim", description="Claim this ticket as your responsibility.")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def claim(self, interaction: discord.Interaction):
        ticket = await _get_ticket(self.bot.db.db_path, interaction.channel.id)
        if not ticket: return await interaction.response.send_message(embed=error_embed("Not a Ticket",""), ephemeral=True)
        if ticket.get("claimed_by"):
            m = interaction.guild.get_member(ticket["claimed_by"])
            return await interaction.response.send_message(embed=error_embed("Already Claimed", f"Claimed by {m.mention if m else 'someone'}."), ephemeral=True)
        async with aiosqlite.connect(self.bot.db.db_path) as db:
            await db.execute("UPDATE tickets SET claimed_by=? WHERE ticket_id=?", (interaction.user.id, ticket["ticket_id"]))
            await db.commit()
        await _log_event(self.bot.db.db_path, ticket["ticket_id"], interaction.guild.id, interaction.user.id, "claimed", f"Claimed by {interaction.user.display_name}")
        await interaction.response.send_message(embed=discord.Embed(description=f"🙋 {interaction.user.mention} claimed this ticket.", color=TC_CLAIMED))

    @app_commands.command(name="unclaim", description="Release this ticket so another staff member can take it.")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def unclaim(self, interaction: discord.Interaction):
        ticket = await _get_ticket(self.bot.db.db_path, interaction.channel.id)
        if not ticket: return await interaction.response.send_message(embed=error_embed("Not a Ticket",""), ephemeral=True)
        async with aiosqlite.connect(self.bot.db.db_path) as db:
            await db.execute("UPDATE tickets SET claimed_by=NULL WHERE ticket_id=?", (ticket["ticket_id"],))
            await db.commit()
        await _log_event(self.bot.db.db_path, ticket["ticket_id"], interaction.guild.id, interaction.user.id, "unclaimed", f"Released by {interaction.user.display_name}")
        await interaction.response.send_message(embed=discord.Embed(description=f"🔓 {interaction.user.mention} released this ticket.", color=TC_OPEN))

    @app_commands.command(name="add", description="Add a user to this ticket.")
    @app_commands.describe(user="User to add")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def add(self, interaction: discord.Interaction, user: discord.Member):
        await interaction.channel.set_permissions(user, view_channel=True, send_messages=True, read_message_history=True)
        ticket = await _get_ticket(self.bot.db.db_path, interaction.channel.id)
        if ticket: await _log_event(self.bot.db.db_path, ticket["ticket_id"], interaction.guild.id, interaction.user.id, "user_added", f"{user.display_name} added by {interaction.user.display_name}")
        await interaction.response.send_message(embed=discord.Embed(description=f"➕ {user.mention} added.", color=TC_CLAIMED))

    @app_commands.command(name="remove", description="Remove a user from this ticket.")
    @app_commands.describe(user="User to remove")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def remove(self, interaction: discord.Interaction, user: discord.Member):
        await interaction.channel.set_permissions(user, overwrite=None)
        ticket = await _get_ticket(self.bot.db.db_path, interaction.channel.id)
        if ticket: await _log_event(self.bot.db.db_path, ticket["ticket_id"], interaction.guild.id, interaction.user.id, "user_removed", f"{user.display_name} removed by {interaction.user.display_name}")
        await interaction.response.send_message(embed=discord.Embed(description=f"➖ {user.mention} removed.", color=TC_OPEN))

    @app_commands.command(name="list", description="View all open tickets.")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def list_tickets(self, interaction: discord.Interaction):
        async with aiosqlite.connect(self.bot.db.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM tickets WHERE guild_id=? AND status='open' ORDER BY ticket_id DESC", (interaction.guild.id,)) as c:
                tickets = [dict(r) for r in await c.fetchall()]
        if not tickets:
            return await interaction.response.send_message(embed=info_embed("No Open Tickets","All quiet."), ephemeral=True)
        e = discord.Embed(title=f"Open Tickets — {len(tickets)} active", color=TC_OPEN, timestamp=discord.utils.utcnow())
        for t in tickets[:10]:
            ch = interaction.guild.get_channel(t["channel_id"])
            opener  = interaction.guild.get_member(t["user_id"])
            claimer = interaction.guild.get_member(t.get("claimed_by") or 0)
            try: ts = f"<t:{int(datetime.datetime.fromisoformat(t['created_at'].replace('Z','')).timestamp())}:R>"
            except Exception: ts = "—"
            opener_str = opener.mention if opener else f"<@{t['user_id']}>"
            e.add_field(name=f"#{t['ticket_id']} — {t.get('topic','General')}", value=f"**Opener:** {opener_str}\n**Channel:** {ch.mention if ch else '(deleted)'}\n**Claimed:** {claimer.mention if claimer else '—'}\n**Opened:** {ts}", inline=True)
        e.set_footer(text="XERO Tickets")
        await interaction.response.send_message(embed=e, ephemeral=True)

    @app_commands.command(name="history", description="Browse closed tickets latest to earliest. ◀ ▶ to navigate. Filter by user optionally.")
    @app_commands.describe(user="Filter to a specific user's tickets (optional)")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def history(self, interaction: discord.Interaction, user: discord.Member=None):
        await interaction.response.defer(ephemeral=True)
        q = "SELECT * FROM tickets WHERE guild_id=? AND status='closed'"
        p = [interaction.guild.id]
        if user: q += " AND user_id=?"; p.append(user.id)
        q += " ORDER BY ticket_id DESC"
        async with aiosqlite.connect(self.bot.db.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(q, p) as c:
                tickets = [dict(r) for r in await c.fetchall()]
        if not tickets:
            msg = f"No closed tickets" + (f" for {user.mention}" if user else "") + "."
            return await interaction.followup.send(embed=info_embed("No History", msg), ephemeral=True)
        view  = TicketHistoryView(self.bot, interaction.guild, tickets)
        embed = await view._embed()
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="rate", description="Rate your support experience 1-5 stars.")
    @app_commands.describe(stars="Your rating", feedback="Optional feedback")
    @app_commands.choices(stars=[
        app_commands.Choice(name="⭐ 1 — Poor",value=1), app_commands.Choice(name="⭐⭐ 2 — Fair",value=2),
        app_commands.Choice(name="⭐⭐⭐ 3 — Good",value=3), app_commands.Choice(name="⭐⭐⭐⭐ 4 — Great",value=4),
        app_commands.Choice(name="⭐⭐⭐⭐⭐ 5 — Excellent",value=5),
    ])
    async def rate(self, interaction: discord.Interaction, stars: int, feedback: str=""):
        async with aiosqlite.connect(self.bot.db.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT ticket_id FROM tickets WHERE guild_id=? AND user_id=? ORDER BY ticket_id DESC LIMIT 1", (interaction.guild.id, interaction.user.id)) as c:
                row = await c.fetchone()
        if not row:
            return await interaction.response.send_message(embed=error_embed("No Ticket","No ticket found to rate."), ephemeral=True)
        async with aiosqlite.connect(self.bot.db.db_path) as db:
            await db.execute("UPDATE tickets SET rating=?, rating_feedback=? WHERE ticket_id=?", (stars, feedback, row["ticket_id"]))
            await db.commit()
        await _log_event(self.bot.db.db_path, row["ticket_id"], interaction.guild.id, interaction.user.id, "rating", f"{stars}/5 — {feedback[:80] if feedback else 'no comment'}")
        e = discord.Embed(description=f"{'⭐'*stars} — Thank you{(f': *{feedback}*') if feedback else '!'}", color=TC_CLAIMED)
        e.set_footer(text="XERO Tickets  •  Your feedback helps the team")
        await interaction.response.send_message(embed=e)

    @app_commands.command(name="transcript", description="Export a text transcript of this ticket.")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def transcript(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        lines = []
        async for msg in interaction.channel.history(limit=500, oldest_first=True):
            lines.append(f"[{msg.created_at.strftime('%H:%M:%S')}] {msg.author.display_name}: {msg.content}")
        if not lines:
            return await interaction.followup.send(embed=error_embed("Empty","No messages."), ephemeral=True)
        f = discord.File(io.StringIO("\n".join(lines)), filename=f"{interaction.channel.name}-transcript.txt")
        await interaction.followup.send(embed=discord.Embed(description=f"📄 {len(lines)} messages exported.", color=TC_OPEN), file=f, ephemeral=True)


async def setup(bot):
    try:
        async with aiosqlite.connect(bot.db.db_path) as db:
            for col in ["claimed_by INTEGER","rating INTEGER","rating_feedback TEXT","message_count INTEGER DEFAULT 0","ai_summary TEXT","log_message_id INTEGER"]:
                try: await db.execute(f"ALTER TABLE tickets ADD COLUMN {col}")
                except Exception: pass
            try:
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS ticket_events (
                        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ticket_id INTEGER NOT NULL,
                        guild_id INTEGER NOT NULL,
                        user_id INTEGER NOT NULL,
                        event_type TEXT NOT NULL,
                        detail TEXT,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """)
            except Exception: pass
            await db.commit()
    except Exception as e:
        logger.error(f"Ticket migration: {e}")
    await bot.add_cog(Tickets(bot))
