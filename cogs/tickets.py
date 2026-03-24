from utils.guard import command_guard
"""
XERO Bot — Ticket System (Complete Rewrite)
ZNYT-style category dropdowns, emoji-named channels, and staff intelligence briefs.
"""
import discord, aiosqlite, asyncio, io, logging, datetime
from discord.ext import commands
from discord import app_commands
from utils.embeds import XERO, success_embed, error_embed, info_embed, brand_embed

logger = logging.getLogger("XERO.Tickets")

TC_OPEN    = 0x2B2D31
TC_CLAIMED = 0x5865F2
TC_CLOSED  = 0x1A1A1A
TC_HISTORY = 0x23272A

TICKET_CATEGORIES = {
    "general": {"label": "General Support", "emoji": "🔧", "description": "Basic questions and help"},
    "senior": {"label": "Senior Support", "emoji": "📋", "description": "Prize claims and partnership requests"},
    "executive": {"label": "Executive Support", "emoji": "💼", "description": "Career opportunities and reports"},
}

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


async def _build_staff_brief(bot, guild, member: discord.Member, ticket_id: int) -> discord.Embed:
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

        # Cross-server mod cases
        async with db.execute(
            "SELECT guild_id, action, reason, timestamp FROM mod_cases WHERE user_id=? AND guild_id!=? ORDER BY case_id DESC LIMIT 20",
            (uid, gid)
        ) as c: global_cases = [dict(r) for r in await c.fetchall()]

        cross_bans = [c for c in global_cases if c["action"].lower() in ("ban","tempban")]

        # Warnings
        async with db.execute(
            "SELECT reason, timestamp FROM warnings WHERE guild_id=? AND user_id=? ORDER BY id DESC LIMIT 5",
            (gid, uid)
        ) as c: warns = [dict(r) for r in await c.fetchall()]

        # Global blacklist
        try:
            async with db.execute("SELECT reason FROM blacklisted_users WHERE user_id=?", (uid,)) as c:
                blacklisted = await c.fetchone()
        except Exception: blacklisted = None

        # Tickets
        async with db.execute(
            "SELECT COUNT(*) as total, SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) as open_count FROM tickets WHERE guild_id=? AND user_id=?",
            (gid, uid)
        ) as c:
            tkt = dict(await c.fetchone())

        # XP / Level
        async with db.execute("SELECT level, total_xp FROM levels WHERE guild_id=? AND user_id=?", (gid, uid)) as c:
            lvl_row = await c.fetchone()
        lvl = dict(lvl_row) if lvl_row else {"level":0,"total_xp":0}

        # Previous closed tickets
        async with db.execute(
            "SELECT ticket_id, topic, ai_summary, closed_at FROM tickets WHERE guild_id=? AND user_id=? AND status='closed' ORDER BY ticket_id DESC LIMIT 3",
            (gid, uid)
        ) as c: prev_tickets = [dict(r) for r in await c.fetchall()]

    e = discord.Embed(title="🛡️  STAFF INTELLIGENCE BRIEF", color=0x2B2D31, timestamp=discord.utils.utcnow())
    e.set_author(name=f"{member} — {uid}", icon_url=member.display_avatar.url)
    
    e.add_field(name="⏳ Account Age", value=f"{age_days} days", inline=True)
    e.add_field(name="📊 Server Activity", value=f"Level {lvl['level']} ({lvl['total_xp']:,} XP)", inline=True)
    e.add_field(name="📂 Ticket History", value=f"{tkt['total']} total ({tkt['open_count']} open)", inline=True)

    if local_cases:
        case_list = "\n".join([f"• **{c['action'].upper()}** — {c['timestamp'][:10]} — *{c['reason'][:40]}*" for c in local_cases[:3]])
        e.add_field(name="⚖️ Recent Mod Cases", value=case_list, inline=False)
    
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

class TicketCategorySelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label=v["label"], value=k, emoji=v["emoji"], description=v["description"])
            for k, v in TICKET_CATEGORIES.items()
        ]
        super().__init__(placeholder="Choose a support category...", min_values=1, max_values=1, options=options, custom_id="xero_t_select")

    async def callback(self, interaction: discord.Interaction):
        category_key = self.values[0]
        category_info = TICKET_CATEGORIES[category_key]
        
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
            # ZNYT Style: emoji-name (🔧-john_doe)
            clean_name = interaction.user.display_name[:18].lower().replace(' ','-')
            channel_name = f"{category_info['emoji']}-{clean_name}"
            
            ch = await guild.create_text_channel(
                channel_name,
                category=cat, overwrites=ow,
                topic=f"{category_info['label']} — {interaction.user} — {discord.utils.utcnow().strftime('%Y-%m-%d')}"
            )
        except Exception as ex:
            return await interaction.response.send_message(f"❌ {ex}", ephemeral=True)

        async with aiosqlite.connect(bot.db.db_path) as db:
            cur = await db.execute("INSERT INTO tickets (guild_id, channel_id, user_id, topic) VALUES (?,?,?,?)", (guild.id, ch.id, interaction.user.id, category_info['label']))
            tid = cur.lastrowid
            await db.commit()

        await _log_event(bot.db.db_path, tid, guild.id, interaction.user.id, "opened", f"Opened {category_info['label']} ticket")

        emb = discord.Embed(title=f"Ticket #{tid} — {category_info['label']}", description=f"Hello {interaction.user.mention}!\n\nYou have opened a **{category_info['label']}** ticket. Describe your issue clearly and staff will assist you shortly.", color=TC_OPEN, timestamp=discord.utils.utcnow())
        emb.set_thumbnail(url=interaction.user.display_avatar.url)
        emb.set_footer(text=f"Case #{tid}  •  XERO Tickets")
        
        # Apply branding
        emb, file = await brand_embed(emb, guild, bot)
        
        ping = interaction.user.mention + (f" | {role.mention}" if role else "")
        if file:
            await ch.send(content=ping, embed=emb, view=TicketActionView(bot), file=file)
        else:
            await ch.send(content=ping, embed=emb, view=TicketActionView(bot))

        # Staff Intelligence Brief
        try:
            brief = await _build_staff_brief(bot, guild, interaction.user, tid)
            brief_msg = await ch.send(embed=brief)
            try: await brief_msg.pin()
            except Exception: pass
        except Exception as ex:
            logger.error(f"Staff brief: {ex}")

        await interaction.response.send_message(f"✅ Ticket opened: {ch.mention}", ephemeral=True)

class TicketOpenView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketCategorySelect())

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

    @app_commands.command(name="setup", description="Set up the ZNYT-style ticket panel with category dropdown.")
    @app_commands.describe(channel="Where to post the panel", support_role="Role to ping", category="Category for ticket channels", log_channel="Where case logs are posted on close", message="Custom panel message")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup(self, interaction: discord.Interaction, channel: discord.TextChannel,
                    support_role: discord.Role=None, category: discord.CategoryChannel=None,
                    log_channel: discord.TextChannel=None, message: str=None):
        if support_role: await self.bot.db.update_guild_setting(interaction.guild.id, "ticket_support_role_id", support_role.id)
        if category:     await self.bot.db.update_guild_setting(interaction.guild.id, "ticket_category_id", category.id)
        if log_channel:  await self.bot.db.update_guild_setting(interaction.guild.id, "ticket_log_channel_id", log_channel.id)

        txt = message or "Welcome to our assistance centre. If you need assistance within the server, please follow the guidelines below to choose the correct support category."
        emb = discord.Embed(title="Assistance", description=txt, color=TC_OPEN)
        
        # ZNYT Style Layout
        emb.add_field(name="General Support", value="Basic Questions", inline=False)
        emb.add_field(name="Senior Support", value="Prize Claims\nPartnership Requests", inline=False)
        emb.add_field(name="Executive Support", value="Career Opportunities\nReports & Appeals", inline=False)
        
        if interaction.guild.icon: emb.set_thumbnail(url=interaction.guild.icon.url)
        
        # Apply Branding
        emb, file = await brand_embed(emb, interaction.guild, self.bot)
        
        if file:
            await channel.send(embed=emb, view=TicketOpenView(), file=file)
        else:
            await channel.send(embed=emb, view=TicketOpenView())
            
        desc = f"ZNYT-style ticket panel posted in {channel.mention}."
        if support_role: desc += f"\nSupport role: {support_role.mention}"
        if category:     desc += f"\nCategory: {category.mention}"
        if log_channel:  desc += f"\nCase logs → {log_channel.mention}"
        await interaction.response.send_message(embed=success_embed("Ticket System Ready", desc))

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
            return await interaction.response.send_message(embed=info_embed("No Open Tickets","All quiet."))
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
        await interaction.response.send_message(embed=e)

    @app_commands.command(name="history", description="Browse closed tickets latest to earliest. ◀ ▶ to navigate. Filter by user optionally.")
    @app_commands.describe(user="Filter to a specific user's tickets (optional)")
    @app_commands.checks.has_permissions(manage_channels=True)
    @command_guard
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
            return await interaction.followup.send(embed=info_embed("No History", msg))
        view  = TicketHistoryView(self.bot, interaction.guild, tickets)
        embed = await view._embed()
        await interaction.followup.send(embed=embed, view=view)

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
    @command_guard
    async def transcript(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        lines = []
        async for msg in interaction.channel.history(limit=500, oldest_first=True):
            lines.append(f"[{msg.created_at.strftime('%H:%M:%S')}] {msg.author.display_name}: {msg.content}")
        if not lines:
            return await interaction.followup.send(embed=error_embed("Empty","No messages."), ephemeral=True)
        f = discord.File(io.StringIO("\n".join(lines)), filename=f"{interaction.channel.name}-transcript.txt")
        await interaction.followup.send(embed=discord.Embed(description=f"📄 {len(lines)} messages exported.", color=TC_OPEN), file=f)


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
