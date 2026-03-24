"""
XERO Bot — Events
Handles: welcome/farewell, XP (exponential + multipliers), reminders (channel + DM),
AI mentions (reads real channel history), starboard, temp voice, birthdays,
AFK, raid detection, AI automod, milestones.
"""
import discord
from discord.ext import commands, tasks
import logging, random, re, datetime, aiosqlite, urllib.parse

logger = logging.getLogger("XERO.Events")

# Per-guild AI conversation memory  guild_id -> [{role,content}]
AI_MEMORY: dict = {}


class Events(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.xp_cooldowns: dict = {}       # (user_id, guild_id) -> datetime
        self.process_reminders.start()
        self.process_temp_bans.start()
        self.check_birthdays.start()
        self.process_scheduled_messages.start()

    def cog_unload(self):
        self.process_reminders.cancel()
        self.process_temp_bans.cancel()
        self.check_birthdays.cancel()
        self.process_scheduled_messages.cancel()

    # ── Background: Reminders ─────────────────────────────────────────────
    @tasks.loop(seconds=30)
    async def process_reminders(self):
        try:
            reminders = await self.bot.db.get_due_reminders()
            for r in reminders:
                try:
                    from utils.embeds import XERO, comprehensive_embed
                    embed = discord.Embed(
                        title="⏰  Reminder!",
                        description=r["message"],
                        color=XERO.PRIMARY,
                        timestamp=discord.utils.utcnow()
                    )
                    embed.set_footer(text=f"Reminder #{r['id']}  •  XERO Bot")

                    # ── Send to channel ──
                    channel = self.bot.get_channel(r["channel_id"])
                    user    = self.bot.get_user(r["user_id"])
                    if not user:
                        try: user = await self.bot.fetch_user(r["user_id"])
                        except Exception: pass

                    if channel and user:
                        try:
                            await channel.send(content=user.mention, embed=embed)
                        except Exception as e:
                            logger.warning(f"Reminder channel send failed: {e}")

                    # ── Also DM the user ──
                    if user:
                        try:
                            dm_embed = discord.Embed(
                                title="⏰  Your Reminder",
                                description=r["message"],
                                color=XERO.PRIMARY,
                                timestamp=discord.utils.utcnow()
                            )
                            dm_embed.set_footer(text="You set this reminder with XERO Bot")
                            await user.send(embed=dm_embed)
                        except discord.Forbidden:
                            pass  # DMs disabled — channel already sent
                        except Exception as e:
                            logger.warning(f"Reminder DM failed: {e}")

                    await self.bot.db.mark_reminder_sent(r["id"])
                except Exception as e:
                    logger.error(f"Reminder error: {e}")
        except Exception as e:
            logger.error(f"Reminder loop: {e}")

    @process_reminders.before_loop
    async def before_reminders(self): await self.bot.wait_until_ready()

    # ── Background: Auto-unban temp bans ─────────────────────────────────
    @tasks.loop(minutes=5)
    async def process_temp_bans(self):
        try:
            import aiosqlite
            async with aiosqlite.connect(self.bot.db.db_path) as db:
                try:
                    async with db.execute(
                        "SELECT case_id, guild_id, user_id, reason FROM temp_bans WHERE expires_at <= datetime('now')"
                    ) as c:
                        due = await c.fetchall()
                    for case_id, guild_id, user_id, reason in due:
                        guild = self.bot.get_guild(guild_id)
                        if guild:
                            try:
                                await guild.unban(discord.Object(user_id), reason=f"XERO TempBan expired: {reason}")
                                logger.info(f"Auto-unbanned {user_id} in {guild.name}")
                            except Exception as e:
                                logger.debug(f"Auto-unban: {e}")
                        await db.execute("DELETE FROM temp_bans WHERE case_id=?", (case_id,))
                    await db.commit()
                except Exception:
                    pass  # Table may not exist yet
        except Exception as e:
            logger.debug(f"Temp-ban loop: {e}")

    @process_temp_bans.before_loop
    async def before_temp_bans(self): await self.bot.wait_until_ready()

    # ── Background: Birthdays ─────────────────────────────────────────────
    @tasks.loop(hours=1)
    async def check_birthdays(self):
        today = datetime.date.today()
        for guild in self.bot.guilds:
            try:
                settings = await self.bot.db.get_guild_settings(guild.id)
                ch_id = settings.get("birthday_channel_id") or settings.get("welcome_channel_id")
                if not ch_id: continue
                ch = guild.get_channel(ch_id)
                if not ch: continue
                async with aiosqlite.connect(self.bot.db.db_path) as db:
                    db.row_factory = aiosqlite.Row
                    async with db.execute(
                        "SELECT * FROM birthdays WHERE guild_id=? AND month=? AND day=? AND announced_year!=?",
                        (guild.id, today.month, today.day, today.year)
                    ) as c:
                        bdays = [dict(r) for r in await c.fetchall()]
                for b in bdays:
                    m = guild.get_member(b["user_id"])
                    if not m: continue
                    age = f" They're turning **{today.year - b['year']}**!" if b.get("year") else ""
                    embed = discord.Embed(
                        title="🎂  Happy Birthday!",
                        description=f"Everyone wish {m.mention} a happy birthday! 🎉{age}",
                        color=discord.Color.pink()
                    )
                    embed.set_thumbnail(url=m.display_avatar.url)
                    await ch.send(content=m.mention, embed=embed)
                    async with aiosqlite.connect(self.bot.db.db_path) as db:
                        await db.execute("UPDATE birthdays SET announced_year=? WHERE user_id=? AND guild_id=?",
                                         (today.year, b["user_id"], guild.id))
                        await db.commit()
            except Exception as e:
                logger.error(f"Birthday {guild.name}: {e}")

    @check_birthdays.before_loop
    async def before_birthdays(self): await self.bot.wait_until_ready()

    # ── Background: Scheduled messages ────────────────────────────────────
    @tasks.loop(minutes=1)
    async def process_scheduled_messages(self):
        try:
            async with aiosqlite.connect(self.bot.db.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute("SELECT * FROM scheduled_messages WHERE sent=0 AND send_at<=datetime('now')") as c:
                    due = [dict(r) for r in await c.fetchall()]
            for msg in due:
                try:
                    ch = self.bot.get_channel(msg["channel_id"])
                    if ch:
                        if msg.get("embed_title"):
                            embed = comprehensive_embed(title=msg["embed_title"], description=msg["message"], color=discord.Color.blurple())
                            await ch.send(embed=embed)
                        else:
                            await ch.send(msg["message"])
                    async with aiosqlite.connect(self.bot.db.db_path) as db:
                        if msg.get("repeat_hours", 0) > 0:
                            nxt = (datetime.datetime.now() + datetime.timedelta(hours=msg["repeat_hours"])).isoformat()
                            await db.execute("UPDATE scheduled_messages SET send_at=? WHERE id=?", (nxt, msg["id"]))
                        else:
                            await db.execute("UPDATE scheduled_messages SET sent=1 WHERE id=?", (msg["id"],))
                        await db.commit()
                except Exception as e:
                    logger.error(f"Scheduled msg: {e}")
        except Exception as e:
            logger.error(f"Scheduled loop: {e}")

    @process_scheduled_messages.before_loop
    async def before_scheduled(self): await self.bot.wait_until_ready()

    # ── Guild join ─────────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        await self.bot.db.create_guild_settings(guild.id)
        logger.info(f"Joined: {guild.name} ({guild.id})")
        if guild.system_channel and guild.system_channel.permissions_for(guild.me).send_messages:
            from utils.embeds import XERO, comprehensive_embed
            embed = discord.Embed(
                title="👋  Hey, I'm XERO!",
                description=(
                    "Thanks for adding me! Get started with `/admin` for the full control panel.\n\n"
                    "**300+ commands** — AI, Moderation, Economy, Levels, Giveaways, Music, Tickets + more.\n"
                    "All premium features. Completely free."
                ),
                color=XERO.PRIMARY
            )
            embed.set_thumbnail(url=self.bot.user.display_avatar.url)
            try: await guild.system_channel.send(embed=embed)
            except Exception: pass

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        logger.info(f"Left: {guild.name} ({guild.id})")

    # ── Member join ────────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot: return
        settings = await self.bot.db.get_guild_settings(member.guild.id)
        if not settings: return

        # ── Account age filter ─────────────────────────────────────────────
        min_age = settings.get("min_account_age_days", 0)
        if min_age > 0:
            age_days = (discord.utils.utcnow() - member.created_at).days
            if age_days < min_age:
                action = settings.get("account_age_action", "kick_dm")
                try:
                    if "dm" in action:
                        await member.send(embed=discord.Embed(
                            title=f"❌ Account Too New — {member.guild.name}",
                            description=f"Your account must be at least **{min_age} days old** to join this server.\n"
                                        f"Your account is **{age_days} days old**.\nPlease try again later.",
                            color=discord.Color.red()
                        ))
                    if "ban" in action:
                        await member.ban(reason=f"XERO Account Age Filter: {age_days} days < {min_age} minimum")
                    else:
                        await member.kick(reason=f"XERO Account Age Filter: {age_days} days < {min_age} minimum")
                    logger.info(f"Account age filter: {action} {member} ({age_days}d < {min_age}d)")
                    return
                except Exception as e:
                    logger.error(f"Account age filter action: {e}")

        # ── Role restore ───────────────────────────────────────────────────
        if settings.get("role_restore_enabled", 0):
            try:
                import aiosqlite
                async with aiosqlite.connect(self.bot.db.db_path) as db:
                    async with db.execute(
                        "SELECT role_ids FROM member_roles WHERE user_id=? AND guild_id=?",
                        (member.id, member.guild.id)
                    ) as c:
                        row = await c.fetchone()
                if row and row[0]:
                    role_ids = [int(r) for r in row[0].split(",") if r.strip()]
                    roles = [member.guild.get_role(rid) for rid in role_ids]
                    roles = [r for r in roles if r and not r.managed and r != member.guild.default_role]
                    if roles:
                        await member.add_roles(*roles, reason="XERO Role Restore")
                        logger.info(f"Restored {len(roles)} roles for {member}")
            except Exception as e:
                logger.error(f"Role restore on join: {e}")

        # Raid detection
        smart = self.bot.cogs.get("SmartMod")
        if smart: await smart.handle_member_join_check(member)

        # Auto-role
        if settings.get("autorole_id"):
            role = member.guild.get_role(settings["autorole_id"])
            if role:
                try: await member.add_roles(role, reason="XERO Auto-Role")
                except Exception as e: logger.error(f"Auto-role: {e}")


        # ── Welcome message ────────────────────────────────────────────────
        if settings.get("welcome_channel_id"):
            ch = self.bot.get_channel(settings["welcome_channel_id"])
            if not ch:
                try: ch = await self.bot.fetch_channel(settings["welcome_channel_id"])
                except Exception: ch = None
            
            if ch:
                try:
                    from utils.embeds import XERO, comprehensive_embed
                    raw = settings.get("welcome_message") or "Welcome {user} to **{server}**! You are member #{count}. 🎉"
                    msg = raw \
                        .replace("{user}",   member.mention) \
                        .replace("{name}",   member.display_name) \
                        .replace("{server}", member.guild.name) \
                        .replace("{count}",  str(member.guild.member_count))

                    from utils.embeds import brand_embed, comprehensive_embed
                    embed = discord.Embed(
                        title=f"👋  Welcome to {member.guild.name}!",
                        description=msg,
                        color=XERO.PRIMARY
                    )
                    embed.set_thumbnail(url=member.display_avatar.url)
                    embed.add_field(name="👥  Member #", value=f"**{member.guild.member_count:,}**",  inline=True)
                    embed.add_field(name="📅  Account",  value=f"<t:{int(member.created_at.timestamp())}:R>", inline=True)
                    embed.set_footer(text="Welcome!")
                    
                    # Unified Branding
                    embed, file = await brand_embed(embed, member.guild, self.bot)
                    if file:
                        await ch.send(embed=embed, file=file)
                    else:
                        await ch.send(embed=embed)

                    # Welcome card — personalized image with name overlay
                    # Priority: uploaded file (with name) → server banner → AI URL → nothing
                    from utils.welcome_card import generate_welcome_card, fetch_avatar, get_base_image_async
                    _base_img = await get_base_image_async(member.guild.id)
                    if _base_img and not settings.get("unified_image_data"):
                        # Admin uploaded an image — generate personalized card
                        avatar_bytes = await fetch_avatar(str(member.display_avatar.url)) if settings.get("welcome_card_show_avatar", 1) else None
                        card_bytes   = generate_welcome_card(
                            guild_id          = member.guild.id,
                            base_bytes        = _base_img,
                            member_name       = member.display_name if settings.get("welcome_card_show_name", 1) else "",
                            member_avatar_bytes = avatar_bytes,
                            text_color        = settings.get("welcome_card_text_color", "#FFFFFF"),
                            text_position     = settings.get("welcome_card_text_pos", "bottom_left"),
                            show_name         = bool(settings.get("welcome_card_show_name", 1)),
                            show_avatar       = bool(settings.get("welcome_card_show_avatar", 1)),
                            show_member_count = bool(settings.get("welcome_card_show_count", 1)),
                            member_count      = member.guild.member_count,
                            server_name       = member.guild.name,
                            overlay_style     = settings.get("welcome_card_overlay", "gradient"),
                            font_size         = settings.get("welcome_card_font_size", 52),
                        )
                        if card_bytes:
                            import io as _io
                            card_file = discord.File(_io.BytesIO(card_bytes), filename="welcome.png")
                            card_embed = discord.Embed(color=XERO.PRIMARY)
                            card_embed.set_image(url="attachment://welcome.png")
                            await ch.send(embed=card_embed, file=card_file)
                    elif settings.get("welcome_use_banner") and member.guild.banner:
                        img_embed = discord.Embed(color=XERO.PRIMARY)
                        img_embed.set_image(url=member.guild.banner.url)
                        await ch.send(embed=img_embed)
                    elif settings.get("welcome_image_url"):
                        img_embed = discord.Embed(color=XERO.PRIMARY)
                        img_embed.set_image(url=settings["welcome_image_url"])
                        await ch.send(embed=img_embed)
                    elif settings.get("welcome_image_enabled"):
                        prompt  = urllib.parse.quote(
                            f"vibrant welcome banner for Discord member {member.display_name}, "
                            f"colorful celebration modern digital art"
                        )
                        img_url = f"https://image.pollinations.ai/prompt/{prompt}?width=900&height=220&model=flux&nologo=true&seed={member.id%99999}"
                        img_embed = discord.Embed(color=XERO.PRIMARY)
                        img_embed.set_image(url=img_url)
                        await ch.send(embed=img_embed)

                    # Personality welcome line
                    personality = self.bot.cogs.get("Personality")
                    if personality and settings.get("personality_enabled", 1):
                        await personality.on_member_welcome(member, ch)

                except Exception as e:
                    logger.error(f"Welcome: {e}")

        # ── Welcome DM ────────────────────────────────────────────────────
        if settings.get("welcome_dm_enabled", 0):
            try:
                from utils.embeds import XERO, comprehensive_embed
                dm_raw = settings.get("welcome_dm_message") or (
                    "Hey {name}! 👋\n\nWelcome to **{server}**! "
                    "We're glad to have you.\n\nCheck out the channels and enjoy your stay!"
                )
                dm_msg = dm_raw \
                    .replace("{user}",   member.mention) \
                    .replace("{name}",   member.display_name) \
                    .replace("{server}", member.guild.name) \
                    .replace("{count}",  str(member.guild.member_count))
                dm_embed = discord.Embed(
                    title=f"👋  Welcome to {member.guild.name}!",
                    description=dm_msg,
                    color=XERO.PRIMARY
                )
                dm_embed.set_thumbnail(url=member.guild.icon.url if member.guild.icon else member.display_avatar.url)
                dm_embed.set_footer(text=f"{member.guild.name}  •  Sent by XERO Bot")
                await member.send(embed=dm_embed)

                # ── DM Image logic (Matches channel welcome logic) ──
                from utils.welcome_card import generate_welcome_card, fetch_avatar, get_base_image_async
                _base_img = await get_base_image_async(member.guild.id)
                
                if _base_img:
                    # Personalized card
                    avatar_bytes = await fetch_avatar(str(member.display_avatar.url)) if settings.get("welcome_card_show_avatar", 1) else None
                    card_bytes   = generate_welcome_card(
                        guild_id            = member.guild.id,
                        base_bytes          = _base_img,
                        member_name         = member.display_name if settings.get("welcome_card_show_name", 1) else "",
                        member_avatar_bytes = avatar_bytes,
                        text_color          = settings.get("welcome_card_text_color", "#FFFFFF"),
                        text_position       = settings.get("welcome_card_text_pos", "bottom_left"),
                        show_name           = bool(settings.get("welcome_card_show_name", 1)),
                        show_avatar         = bool(settings.get("welcome_card_show_avatar", 1)),
                        show_member_count   = bool(settings.get("welcome_card_show_count", 1)),
                        member_count        = member.guild.member_count,
                        server_name         = member.guild.name,
                        overlay_style       = settings.get("welcome_card_overlay", "gradient"),
                        font_size           = settings.get("welcome_card_font_size", 52),
                    )
                    if card_bytes:
                        import io as _io
                        card_file = discord.File(_io.BytesIO(card_bytes), filename="welcome_dm.png")
                        card_embed = discord.Embed(color=XERO.PRIMARY)
                        card_embed.set_image(url="attachment://welcome_dm.png")
                        await member.send(embed=card_embed, file=card_file)
                else:
                    # Fallback to URLs/Banner
                    dm_img_url = settings.get("welcome_dm_image_url") or settings.get("welcome_image_url")
                    if not dm_img_url and settings.get("welcome_use_banner") and member.guild.banner:
                        dm_img_url = member.guild.banner.url
                    
                    if dm_img_url:
                        dm_img_embed = discord.Embed(color=XERO.PRIMARY)
                        dm_img_embed.set_image(url=dm_img_url)
                        await member.send(embed=dm_img_embed)
                    elif settings.get("welcome_image_enabled"):
                        # AI fallback
                        prompt  = urllib.parse.quote(f"vibrant welcome banner for Discord member {member.display_name}")
                        img_url = f"https://image.pollinations.ai/prompt/{prompt}?width=900&height=220&model=flux&nologo=true"
                        img_embed = discord.Embed(color=XERO.PRIMARY)
                        img_embed.set_image(url=img_url)
                        await member.send(embed=img_embed)
            except discord.Forbidden:
                pass  # User has DMs disabled — silent fail
            except Exception as e:
                logger.error(f"Welcome DM: {e}")

        # Milestone check
        personality = self.bot.cogs.get("Personality")
        if personality: await personality.check_milestone(member.guild)

    # ── Member leave ───────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if member.bot: return
        settings = await self.bot.db.get_guild_settings(member.guild.id)
        if not settings: return

        # ── Save roles for restore on rejoin ──────────────────────────────
        if settings.get("role_restore_enabled", 0):
            try:
                saveable = [r.id for r in member.roles if not r.managed and r != member.guild.default_role]
                if saveable:
                    async with aiosqlite.connect(self.bot.db.db_path) as db:
                        await db.execute(
                            "INSERT OR REPLACE INTO member_roles (user_id, guild_id, role_ids) VALUES (?,?,?)",
                            (member.id, member.guild.id, ",".join(str(r) for r in saveable))
                        )
                        await db.commit()
            except Exception as e:
                logger.error(f"Role save on leave: {e}")

        # ── Farewell message ───────────────────────────────────────────────
        if not settings.get("farewell_channel_id"): return
        ch = member.guild.get_channel(settings["farewell_channel_id"])
        if not ch: return
        try:
            from utils.embeds import XERO, brand_embed, comprehensive_embed
            msg = (settings.get("farewell_message") or "Goodbye **{name}**, we'll miss you!") \
                .replace("{user}", member.display_name) \
                .replace("{name}", member.display_name) \
                .replace("{server}", member.guild.name)
            embed = comprehensive_embed(description=msg, color=XERO.ERROR)
            embed.set_author(name=f"{member.display_name} left the server", icon_url=member.display_avatar.url)
            embed.set_footer(text=f"Members: {member.guild.member_count:,}")
            
            # Unified Branding
            embed, file = await brand_embed(embed, member.guild, self.bot)
            if file:
                await ch.send(embed=embed, file=file)
            else:
                await ch.send(embed=embed)
        except Exception as e:
            logger.error(f"Farewell: {e}")

    # ── Anti-nuke: channel delete ──────────────────────────────────────────
    @commands.Cog.listener()
    async def on_message_delete_snipe(self, message: discord.Message):
        if message.author.bot or not message.guild: return
        if not hasattr(self.bot, '_snipe_cache'):
            self.bot._snipe_cache = {}
        self.bot._snipe_cache[message.channel.id] = {
            'content':    message.content,
            'author':     str(message.author),
            'avatar':     message.author.display_avatar.url,
            'deleted_at': discord.utils.utcnow(),
        }

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        try:
            guild = channel.guild
            # Find who did it from audit log
            async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.channel_delete):
                if entry.user and not entry.user.bot:
                    security = self.bot.cogs.get("Security")
                    if security:
                        await security.check_nuke_action(guild, entry.user, "channel_delete")
        except Exception as e:
            logger.debug(f"Anti-nuke channel delete: {e}")

    # ── Anti-nuke: role delete ─────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        try:
            guild = role.guild
            async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.role_delete):
                if entry.user and not entry.user.bot:
                    security = self.bot.cogs.get("Security")
                    if security:
                        await security.check_nuke_action(guild, entry.user, "role_delete")
        except Exception as e:
            logger.debug(f"Anti-nuke role delete: {e}")

    # ── Anti-nuke: mass ban ────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        try:
            async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.ban):
                if entry.user and not entry.user.bot and entry.user.id != self.bot.user.id:
                    security = self.bot.cogs.get("Security")
                    if security:
                        await security.check_nuke_action(guild, entry.user, "mass_ban")
        except Exception as e:
            logger.debug(f"Anti-nuke ban: {e}")

    # ── Voice state (temp channels) ────────────────────────────────────────
    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before, after):
        guild = member.guild
        # ── Voice XP tracking ─────────────────────────────────────────────
        if after.channel and not before.channel:
            # Store join time in memory for XP calculation
            if not hasattr(self, '_voice_join_times'):
                self._voice_join_times = {}
            self._voice_join_times[(member.id, member.guild.id)] = discord.utils.utcnow()
        elif before.channel and not after.channel:
            # Member left — award voice XP for time spent
            if not hasattr(self, '_voice_join_times'):
                self._voice_join_times = {}
            join_time = self._voice_join_times.pop((member.id, member.guild.id), None)
            if join_time:
                try:
                    settings = await self.bot.db.get_guild_settings(guild.id)
                    if settings and settings.get("voice_xp_enabled", 0):
                        minutes = max(0, int((discord.utils.utcnow() - join_time).total_seconds() / 60))
                        if minutes > 0:
                            rate    = settings.get("voice_xp_rate", 5)
                            xp_gain = min(minutes * rate, rate * 60)  # Cap at 1hr worth
                            # Only award if multiple people in channel
                            if len(before.channel.members) >= 1:  # They just left so check before
                                leveled_up, new_level = await self.bot.db.update_xp(
                                    member.id, guild.id, xp_gain, is_bot_command=False
                                )
                                if leveled_up:
                                    ch = before.channel  # Use voice channel's text companion
                                    await self._announce_level_up(member, new_level, None, settings)
                except Exception as e:
                    logger.debug(f"Voice XP: {e}")

        # User joins a trigger channel → create temp VC

        if after.channel and after.channel != before.channel:
            try:
                async with aiosqlite.connect(self.bot.db.db_path) as db:
                    db.row_factory = aiosqlite.Row
                    async with db.execute(
                        "SELECT * FROM temp_voice_config WHERE guild_id=? AND trigger_channel_id=?",
                        (guild.id, after.channel.id)
                    ) as c:
                        config = await c.fetchone()
                if config:
                    config = dict(config)
                    name     = config["default_name"].replace("{user}", member.display_name)
                    category = guild.get_channel(config["category_id"]) if config.get("category_id") else after.channel.category
                    new_vc   = await guild.create_voice_channel(
                        name=name, category=category,
                        user_limit=config["default_limit"] or 0,
                        reason=f"Temp VC for {member}"
                    )
                    await member.move_to(new_vc)
                    async with aiosqlite.connect(self.bot.db.db_path) as db:
                        await db.execute(
                            "INSERT INTO temp_voice_channels (channel_id,guild_id,owner_id) VALUES (?,?,?)",
                            (new_vc.id, guild.id, member.id)
                        )
                        await db.commit()
            except Exception as e:
                logger.error(f"Temp VC create: {e}")

        # User leaves a temp VC → delete if empty
        if before.channel and before.channel != after.channel:
            try:
                async with aiosqlite.connect(self.bot.db.db_path) as db:
                    async with db.execute(
                        "SELECT channel_id FROM temp_voice_channels WHERE channel_id=? AND guild_id=?",
                        (before.channel.id, guild.id)
                    ) as c:
                        row = await c.fetchone()
                if row:
                    vc = guild.get_channel(before.channel.id)
                    if vc and len(vc.members) == 0:
                        await vc.delete(reason="Temp VC: empty")
                        async with aiosqlite.connect(self.bot.db.db_path) as db:
                            await db.execute("DELETE FROM temp_voice_channels WHERE channel_id=?", (before.channel.id,))
                            await db.commit()
            except Exception as e:
                logger.error(f"Temp VC delete: {e}")

    # ── Starboard ──────────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.emoji.name != "⭐" or not payload.guild_id: return
        guild = self.bot.get_guild(payload.guild_id)
        if not guild: return
        try:
            async with aiosqlite.connect(self.bot.db.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT * FROM starboard_config WHERE guild_id=? AND enabled=1", (guild.id,)
                ) as c:
                    config = await c.fetchone()
            if not config: return
            config = dict(config)
            ch  = guild.get_channel(payload.channel_id)
            if not ch: return
            msg = await ch.fetch_message(payload.message_id)
            if msg.author.bot: return
            star_r   = discord.utils.get(msg.reactions, emoji="⭐")
            stars    = star_r.count if star_r else 0
            if stars < config["threshold"]: return
            sb_ch    = guild.get_channel(config["channel_id"])
            if not sb_ch: return
            async with aiosqlite.connect(self.bot.db.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT * FROM starboard_messages WHERE original_id=? AND guild_id=?",
                    (msg.id, guild.id)
                ) as c:
                    existing = await c.fetchone()
            if existing:
                existing = dict(existing)
                if existing.get("starboard_id"):
                    try:
                        sb_msg = await sb_ch.fetch_message(existing["starboard_id"])
                        e = sb_msg.embeds[0]
                        e.set_footer(text=f"⭐ {stars}  •  #{ch.name}")
                        await sb_msg.edit(embed=e)
                    except Exception: pass
                return
            embed = discord.Embed(
                description=msg.content or "*[No text]*",
                color=discord.Color.gold(),
                timestamp=msg.created_at
            )
            embed.set_author(name=msg.author.display_name, icon_url=msg.author.display_avatar.url)
            embed.add_field(name="Source", value=f"[Jump]({msg.jump_url})", inline=True)
            embed.set_footer(text=f"⭐ {stars}  •  #{ch.name}")
            if msg.attachments:
                att = msg.attachments[0]
                if att.content_type and "image" in att.content_type:
                    embed.set_image(url=att.url)
            sb_msg = await sb_ch.send(embed=embed)
            async with aiosqlite.connect(self.bot.db.db_path) as db:
                await db.execute(
                    "INSERT INTO starboard_messages (original_id,guild_id,starboard_id,channel_id) VALUES (?,?,?,?)",
                    (msg.id, guild.id, sb_msg.id, ch.id)
                )
                await db.commit()
        except Exception as e:
            logger.error(f"Starboard: {e}")

    # ── Main message handler ───────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot: return

        # ── Handle DM mentions (bot responds in DMs when pinged) ──────────
        if not message.guild:
            if self.bot.user.mentioned_in(message) or message.channel.type == discord.ChannelType.private:
                dm_content = re.sub(r"<@!?\d+>", "", message.content).strip()
                if not dm_content and not message.content.strip():
                    try:
                        await message.reply(
                            "Hey! 👋 You can DM me and I'll reply — just send your message or question directly!"
                        )
                    except Exception: pass
                    return
                query = dm_content or message.content
                async with message.channel.typing():
                    try:
                        response = await self.bot.nvidia.ask(query)
                        if response:
                            if len(response) > 2000:
                                for i in range(0, len(response), 1990):
                                    await message.reply(response[i:i+1990])
                            else:
                                await message.reply(response)
                    except Exception as e:
                        logger.error(f"DM AI: {e}")
            return  # All DM handling done above

        settings = await self.bot.db.get_guild_settings(message.guild.id)
        if not settings: return

        # ── AFK check ─────────────────────────────────────────────────────
        afk = await self.bot.db.get_afk(message.author.id, message.guild.id)
        if afk:
            await self.bot.db.remove_afk(message.author.id, message.guild.id)
            try:
                await message.reply(
                    f"Welcome back {message.author.mention}! Your AFK has been removed.",
                    delete_after=8
                )
            except Exception: pass

        # ── Notify AFK users who were mentioned ───────────────────────────
        for mentioned in message.mentions:
            if mentioned.id == message.author.id: continue
            afk_data = await self.bot.db.get_afk(mentioned.id, message.guild.id)
            if afk_data:
                ts = datetime.datetime.fromisoformat(afk_data["set_at"])
                try:
                    await message.reply(
                        f"💤 **{mentioned.display_name}** is AFK: {afk_data['reason']} (set <t:{int(ts.timestamp())}:R>)",
                        delete_after=12
                    )
                except Exception: pass

        # ── Counting game ──────────────────────────────────────────────────
        try:
            async with aiosqlite.connect(self.bot.db.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT * FROM counting_config WHERE guild_id=? AND channel_id=? AND enabled=1",
                    (message.guild.id, message.channel.id)
                ) as c:
                    cc = await c.fetchone()
            if cc:
                cc = dict(cc)
                try:
                    num  = int(message.content.strip())
                    exp  = cc["current"] + 1
                    if num == exp and message.author.id != cc.get("last_user_id"):
                        new_hs = max(num, cc["high_score"])
                        async with aiosqlite.connect(self.bot.db.db_path) as db:
                            await db.execute(
                                "UPDATE counting_config SET current=?,last_user_id=?,high_score=? WHERE guild_id=? AND channel_id=?",
                                (num, message.author.id, new_hs, message.guild.id, message.channel.id)
                            )
                            await db.commit()
                        await message.add_reaction("✅")
                        if new_hs == num and num > cc["high_score"] and num > 1:
                            try:
                                await message.channel.send(f"🏆 New High Score! **{num:,}** by {message.author.mention}!", delete_after=10)
                            except Exception: pass
                    else:
                        async with aiosqlite.connect(self.bot.db.db_path) as db:
                            await db.execute(
                                "UPDATE counting_config SET current=0,last_user_id=NULL WHERE guild_id=? AND channel_id=?",
                                (message.guild.id, message.channel.id)
                            )
                            await db.commit()
                        await message.add_reaction("❌")
                        reason = "counted twice in a row!" if message.author.id == cc.get("last_user_id") else f"expected **{exp}**"
                        try:
                            await message.channel.send(
                                f"💥 {message.author.mention} ruined it! ({reason}) Start from **1**!", delete_after=8
                            )
                        except Exception: pass
                except ValueError:
                    pass
        except Exception as e:
            logger.error(f"Counting: {e}")

        # ── AI AutoMod ─────────────────────────────────────────────────────
        if settings.get("aimod_enabled") and message.content and len(message.content) > 10:
            try: await self._run_aimod(message, settings)
            except Exception as e: logger.error(f"AI AutoMod: {e}")

        # ── Link filter ────────────────────────────────────────────────────
        if settings.get("link_filter_enabled", 0) and message.content:
            import re as _re
            urls = _re.findall(r'https?://([^\s/]+)', message.content)
            if urls:
                try:
                    async with aiosqlite.connect(self.bot.db.db_path) as db:
                        async with db.execute(
                            "SELECT domain FROM allowed_domains WHERE guild_id=?",
                            (message.guild.id,)
                        ) as c:
                            allowed = {r[0] for r in await c.fetchall()}
                    # Always allow Discord and common CDNs
                    always_allowed = {"discord.com","discord.gg","tenor.com","giphy.com","cdn.discordapp.com","media.discordapp.net","i.imgur.com"}
                    allowed |= always_allowed
                    if not any(any(domain in url for domain in allowed) for url in urls):
                        try:
                            await message.delete()
                            await message.channel.send(
                                f"🔗 {message.author.mention} — external links are not allowed here.",
                                delete_after=6
                            )
                        except Exception: pass
                except Exception as e:
                    logger.debug(f"Link filter: {e}")

        # ── XP System ──────────────────────────────────────────────────────
        # XP comes from MESSAGES only (not bot commands — those go through award_command_xp)
        # 60-second cooldown per user per guild
        if settings.get("leveling_enabled", 1):
            # XP blacklist check
            xp_blocked = False
            try:
                async with aiosqlite.connect(self.bot.db.db_path) as db:
                    async with db.execute(
                        "SELECT 1 FROM xp_blacklist WHERE guild_id=? AND channel_id=?",
                        (message.guild.id, message.channel.id)
                    ) as c:
                        xp_blocked = await c.fetchone() is not None
            except Exception: pass

            if not xp_blocked:
                now = discord.utils.utcnow()
                key = (message.author.id, message.guild.id)
                last = self.xp_cooldowns.get(key)
                if not last or (now - last).total_seconds() >= 60:
                    base_gain = random.randint(15, 25)
                    # Double XP event check
                    sf_cog = self.bot.cogs.get("ServerFeatures")
                    if sf_cog and sf_cog.is_double_xp(message.guild.id):
                        base_gain *= 2
                    # Role multiplier check
                    try:
                        async with aiosqlite.connect(self.bot.db.db_path) as _db:
                            for role in message.author.roles:
                                async with _db.execute(
                                    "SELECT multiplier FROM xp_role_multipliers WHERE guild_id=? AND role_id=?",
                                    (message.guild.id, role.id)
                                ) as _c:
                                    row = await _c.fetchone()
                                    if row and row[0] > 1.0:
                                        base_gain = int(base_gain * row[0])
                                        break
                    except Exception:
                        pass
                    leveled_up, new_level = await self.bot.db.update_xp(
                        message.author.id, message.guild.id, base_gain, is_bot_command=False
                    )
                    self.xp_cooldowns[key] = now
                    await self.bot.db.increment_stat(message.author.id, message.guild.id, "messages_sent")
                    if leveled_up:
                        await self._announce_level_up(message.author, new_level, message.channel, settings)

        # ── AutoMod ───────────────────────────────────────────────────────────
        am_cog = self.bot.cogs.get("AutoMod")
        if am_cog:
            try:
                # If AutoMod deletes the message, it returns True or similar
                # We should check if the message still exists before continuing
                if await am_cog.process_message(message):
                    return
            except Exception as e: logger.debug(f"AutoMod: {e}")

        # ── Auto-responder ────────────────────────────────────────────────────
        # Skip autoresponder if the bot is mentioned (AI mention handler takes over)
        if not self.bot.user.mentioned_in(message):
            ar_cog = self.bot.cogs.get("AutoResponder")
            if ar_cog:
                try: await ar_cog.process_message(message)
                except Exception as e: logger.debug(f"AutoResponder: {e}")

        # ── @XERO mention → AI response with REAL channel context ──────────
        if self.bot.user.mentioned_in(message) and settings.get("ai_enabled", 1):
            content = re.sub(r"<@!?\d+>", "", message.content).strip()
            # If pinged with no message, send a friendly prompt instead of silently ignoring
            if not content:
                try:
                    from utils.embeds import XERO as _XERO, comprehensive_embed
                    greet_embed = discord.Embed(
                        description=(
                            f"Hey {message.author.mention}! 👋\n\n"
                            f"You can ask me anything — just ping me with your question.\n"
                            f"Try: **@XERO what happened in chat?** or **@XERO explain quantum computing**"
                        ),
                        color=_XERO.PRIMARY
                    )
                    greet_embed.set_footer(text="XERO AI  •  Powered by NVIDIA Nemotron")
                    await message.reply(embed=greet_embed)
                except Exception: pass
                return

            async with message.channel.typing():
                try:
                    # ── Read last 15 real messages from the channel ──
                    history_msgs = []
                    async for hist in message.channel.history(limit=15, before=message):
                        if not hist.content: continue
                        role = "assistant" if hist.author.id == self.bot.user.id else "user"
                        label = f"[{hist.author.display_name}]"
                        history_msgs.append({
                            "role":    role,
                            "content": f"{label}: {hist.content[:300]}"
                        })
                    history_msgs.reverse()  # oldest first

                    # ── Merge with stored AI memory for this guild ──
                    gid = message.guild.id
                    if gid not in AI_MEMORY:
                        AI_MEMORY[gid] = []

                    # Channel history first (real context), then stored memory
                    combined = history_msgs + AI_MEMORY[gid][-4:]

                    # Include channel + user context in system prompt
                    persona  = settings.get("persona", "neutral")
                    response = await self.bot.nvidia.chat_with_context(
                        combined, content, persona,
                        user_name    = message.author.display_name,
                        channel_name = message.channel.name,
                        server_name  = message.guild.name,
                    )

                    if response:
                        # Store in memory
                        AI_MEMORY[gid].append({"role": "user",      "content": f"[{message.author.display_name}]: {content}"})
                        AI_MEMORY[gid].append({"role": "assistant", "content": response})
                        if len(AI_MEMORY[gid]) > 20:
                            AI_MEMORY[gid] = AI_MEMORY[gid][-20:]

                        # Send (split if needed)
                        if len(response) > 2000:
                            for i in range(0, len(response), 1990):
                                await message.reply(response[i:i+1990])
                        else:
                            await message.reply(response)

                except Exception as e:
                    logger.error(f"AI mention: {e}")

    # ── Helper: award XP for using a bot command ───────────────────────────
    async def award_command_xp(self, user_id: int, guild_id: int):
        """
        Call this from any command to award the 2x bot command XP bonus.
        XP gain: base 15-25 * (passive_multiplier + 1.0)
        No cooldown for command XP — every command use rewards XP.
        """
        settings = await self.bot.db.get_guild_settings(guild_id)
        if not settings or not settings.get("leveling_enabled", 1):
            return
        base_gain  = random.randint(15, 25)
        leveled_up, new_level = await self.bot.db.update_xp(
            user_id, guild_id, base_gain, is_bot_command=True
        )
        if leveled_up:
            guild = self.bot.get_guild(guild_id)
            if guild:
                member = guild.get_member(user_id)
                if member:
                    await self._announce_level_up(member, new_level, None, settings)

    # ── Helper: level up announcement ─────────────────────────────────────
    async def _announce_level_up(self, member: discord.Member, level: int, fallback_ch, settings: dict):
        # DM the user if configured
        if settings.get("levelup_dm_enabled", 0):
            try:
                dm_msg = (settings.get("levelup_dm_message") or "🎉 Congrats {user}! You reached **Level {level}** in **{server}**!") \
                    .replace("{user}", member.display_name) \
                    .replace("{level}", str(level)) \
                    .replace("{server}", member.guild.name)
                await member.send(embed=comprehensive_embed(description=dm_msg, color=0x00FF94))
            except Exception:
                pass  # DMs disabled
        # Channel announcement below
        from utils.embeds import XERO, comprehensive_embed
        level_ch_id = settings.get("level_up_channel_id")
        ch = member.guild.get_channel(level_ch_id) if level_ch_id else fallback_ch
        if not ch: return

        personality = self.bot.cogs.get("Personality")
        line = ""
        if personality and settings.get("personality_enabled", 1):
            line = await personality.on_level_up(member, level, ch)

        # Color scales with level
        if level >= 50:   color = XERO.GOLD
        elif level >= 25: color = XERO.SECONDARY
        elif level >= 10: color = XERO.PRIMARY
        else:             color = discord.Color.purple()

        embed = discord.Embed(
            title="⬆️  Level Up!",
            description=f"{member.mention} reached **Level {level}**!" + (f"\n\n{line}" if line else ""),
            color=color
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        
        # Show next multiplier milestone
        mult = self.bot.db.xp_multiplier(level)
        bot_mult = self.bot.db.xp_multiplier(level, is_bot_command=True)
        embed.add_field(
            name="⚡ XP Multipliers",
            value=f"💬 Messages: **{mult:.2f}×** | 🤖 Commands: **{bot_mult:.2f}×**",
            inline=False
        )
        embed.set_footer(text="Keep chatting to level up!")

        # Unified Branding
        from utils.embeds import brand_embed, comprehensive_embed
        embed, file = await brand_embed(embed, member.guild, self.bot)
        
        try:
            if file:
                await ch.send(embed=embed, file=file)
            else:
                await ch.send(embed=embed)
        except Exception as e:
            logger.error(f"Level-up announce: {e}")

        # Check role rewards
        rewards = await self.bot.db.get_level_rewards(member.guild.id)
        for rw in rewards:
            if rw["level"] == level:
                role = member.guild.get_role(rw["role_id"])
                if role:
                    try: await member.add_roles(role, reason=f"Level {level} reward")
                    except Exception: pass

    # ── AI AutoMod logic ───────────────────────────────────────────────────
    async def _run_aimod(self, message: discord.Message, settings: dict):
        threshold = float(settings.get("aimod_threshold") or 0.7)
        action    = settings.get("aimod_action") or "delete"
        prompt = (
            f"Is this Discord message toxic, harmful, or violating community standards?\n"
            f"Message: \"{message.content}\"\n"
            f"Reply with JSON only: {{\"toxic\": true/false, \"score\": 0.0-1.0, \"reason\": \"brief reason\"}}"
        )
        response = await self.bot.nvidia.ask(prompt,
            "You are a content moderation AI. Reply ONLY with valid JSON, nothing else.")
        if not response: return
        import json, re as _re
        try:
            m = _re.search(r'\{.*\}', response, _re.DOTALL)
            if not m: return
            result = json.loads(m.group())
            if result.get("toxic") and float(result.get("score", 0)) >= threshold:
                if action in ("delete", "warn_and_delete"):
                    try: await message.delete()
                    except Exception: pass
                if action in ("warn", "warn_and_delete"):
                    try:
                        await message.channel.send(
                            f"⚠️ {message.author.mention} — message flagged by AI AutoMod.",
                            delete_after=8
                        )
                    except Exception: pass
                # Log
                log_ch_id = settings.get("aimod_log_channel_id") or settings.get("log_channel_id")
                if log_ch_id:
                    log_ch = message.guild.get_channel(log_ch_id)
                    if log_ch:
                        from utils.embeds import XERO, comprehensive_embed
                        embed = discord.Embed(
                            title="🤖 AI AutoMod Action",
                            color=XERO.WARNING,
                            timestamp=discord.utils.utcnow()
                        )
                        embed.add_field(name="User",    value=message.author.mention, inline=True)
                        embed.add_field(name="Score",   value=f"{result.get('score',0):.2f}", inline=True)
                        embed.add_field(name="Action",  value=action, inline=True)
                        embed.add_field(name="Reason",  value=result.get("reason","N/A"), inline=False)
                        embed.add_field(name="Content", value=message.content[:500], inline=False)
                        try: await log_ch.send(embed=embed)
                        except Exception: pass
        except Exception as e:
            logger.error(f"AI AutoMod parse: {e}")


    # ── on_app_command_completion — fires after EVERY slash command ────────
    # This single listener fixes ALL leaderboard/profile/stats data by
    # incrementing commands_used and awarding 2x command XP automatically.
    @commands.Cog.listener()
    async def on_app_command_completion(
        self,
        interaction: discord.Interaction,
        command: discord.app_commands.Command
    ):
        """Called after every successful slash command. Tracks stats + awards XP."""
        if not interaction.guild or not interaction.user:
            return
        try:
            # 1. Increment commands_used for leaderboard/profile
            await self.bot.db.increment_stat(
                interaction.user.id,
                interaction.guild.id,
                "commands_used"
            )
            # 2. Award 2x bot command XP bonus
            settings = await self.bot.db.get_guild_settings(interaction.guild.id)
            if settings and settings.get("leveling_enabled", 1):
                await self.award_command_xp(interaction.user.id, interaction.guild.id)
        except Exception as e:
            logger.error(f"on_app_command_completion error: {e}")


async def setup(bot):
    await bot.add_cog(Events(bot))
