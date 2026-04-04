"""
XERO Bot — Giveaway System
Creative, button-based entry. Image support. Live entry counts. Professional embeds.
Nothing like a basic reaction bot.
"""
import discord
from discord.ext import commands, tasks, app_commands
from utils.guard import command_guard
import logging
import datetime
import random
import asyncio
import aiosqlite

logger = logging.getLogger("XERO.Giveaway")

# ── Color palette ──────────────────────────────────────────────────────────────
GW_GOLD   = 0xF0B429   # active giveaway — warm gold
GW_ENDED  = 0x4B4F58   # ended giveaway — cool grey
GW_WIN    = 0x43B581   # winner announcement — success green


# ── Embed builders (no _base — avoids the ### double-prefix bug) ───────────────

def _gw_active_embed(
    prize:        str,
    end_ts:       int,
    winners_count: int,
    host:         discord.Member,
    entry_count:  int  = 0,
    gw_id:        int  = None,
    required_role: discord.Role | None = None,
    bonus_role:   discord.Role | None = None,
    image_url:    str  | None = None,
    description:  str  | None = None,
) -> discord.Embed:
    """Rich active-giveaway embed — clean, distinct, no double markdown."""
    embed = discord.Embed(color=GW_GOLD, timestamp=discord.utils.utcnow())

    # Hero prize — h2 only once, clean
    embed.description = f"## {prize}"
    if description:
        embed.description += f"\n\n{description}"

    embed.add_field(name="🏆  Winners",   value=f"`{winners_count}`",  inline=True)
    embed.add_field(name="⏱️  Ends",       value=f"<t:{end_ts}:R>",    inline=True)
    embed.add_field(name="🎫  Hosted by", value=host.mention,           inline=True)

    reqs = []
    if required_role: reqs.append(f"Must have {required_role.mention}")
    if bonus_role:    reqs.append(f"{bonus_role.mention} → **2× entries**")
    if reqs:
        embed.add_field(name="📋  Requirements", value="\n".join(reqs), inline=False)

    embed.add_field(
        name="🎟️  Entries",
        value=f"**{entry_count}** {'person has' if entry_count == 1 else 'people have'} entered",
        inline=False,
    )

    if image_url:
        embed.set_image(url=image_url)

    embed.set_footer(text=f"XERO ELITE  •  Giveaway #{gw_id}" if gw_id else "XERO ELITE  •  xero.gg")
    return embed


def _gw_ended_embed(
    prize:       str,
    winners:     list[int],
    entry_count: int,
    gw_id:       int | None = None,
    image_url:   str | None = None,
) -> discord.Embed:
    embed = discord.Embed(color=GW_WIN if winners else GW_ENDED, timestamp=discord.utils.utcnow())
    embed.title       = "🎉  GIVEAWAY ENDED"
    embed.description = f"**{prize}**"

    if winners:
        embed.add_field(
            name="🏆  Winner(s)",
            value=" ".join(f"<@{w}>" for w in winners),
            inline=False,
        )
    else:
        embed.add_field(name="😔  No one entered the giveaway", value="Better luck next time!", inline=False)

    embed.add_field(name="📊  Total Entries", value=f"**{entry_count}**", inline=True)
    if image_url:
        embed.set_thumbnail(url=image_url)
    embed.set_footer(text=f"XERO ELITE  •  Giveaway #{gw_id}" if gw_id else "XERO ELITE  •  xero.gg")
    return embed


def _gw_winner_dm_embed(prize: str, channel: discord.TextChannel) -> discord.Embed:
    embed = discord.Embed(
        color=GW_WIN,
        title="🎉  You Won a Giveaway!",
        description=f"You have been selected as a winner for **{prize}**!",
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="📍  Server", value=channel.guild.name, inline=True)
    embed.add_field(name="📢  Channel", value=channel.mention, inline=True)
    embed.set_footer(text="XERO ELITE  •  Congratulations!")
    return embed


# ── Persistent Entry View ──────────────────────────────────────────────────────

class GiveawayEntryView(discord.ui.View):
    """Persistent button view — survives bot restarts."""

    def __init__(self, gw_id: int, bot: commands.Bot, entry_count: int = 0):
        super().__init__(timeout=None)
        self.gw_id       = gw_id
        self.bot         = bot
        self._lock       = asyncio.Lock()
        self._pending_update: asyncio.Task | None = None

        btn = discord.ui.Button(
            label      = self._label(entry_count),
            style      = discord.ButtonStyle.success,
            custom_id  = f"gw_enter:{gw_id}",
            emoji      = "🎟️",
            row        = 0,
        )
        btn.callback = self._enter_callback
        self.add_item(btn)

    @staticmethod
    def _label(count: int) -> str:
        return f"Enter  ·  {count} Entered" if count else "Enter Giveaway"

    async def _enter_callback(self, interaction: discord.Interaction):
        """Toggle entry — enter if not in, leave if already in."""
        gw_id = self.gw_id

        async with self.bot.db._db_context() as db:
            db.row_factory = aiosqlite.Row
            # Check giveaway still active
            async with db.execute(
                "SELECT prize, required_role_id, bonus_role_id, ended, paused FROM giveaways WHERE giveaway_id=?",
                (gw_id,),
            ) as c:
                gw = await c.fetchone()
            if not gw:
                return await interaction.response.send_message("This giveaway no longer exists.", ephemeral=True)
            gw = dict(gw)
            if gw["ended"] or gw["paused"]:
                return await interaction.response.send_message(
                    "This giveaway has ended or is paused." if gw["ended"] else "This giveaway is currently paused.",
                    ephemeral=True,
                )

            # Required role check
            req_role_id = gw.get("required_role_id")
            if req_role_id:
                member = interaction.guild.get_member(interaction.user.id)
                if not member or not any(r.id == req_role_id for r in member.roles):
                    role = interaction.guild.get_role(req_role_id)
                    return await interaction.response.send_message(
                        f"You need the **{role.name if role else 'required'}** role to enter this giveaway.",
                        ephemeral=True,
                    )

            # Toggle entry
            async with db.execute(
                "SELECT rowid FROM giveaway_participants WHERE giveaway_id=? AND user_id=?",
                (gw_id, interaction.user.id),
            ) as c:
                existing = await c.fetchone()

            if existing:
                # Leave
                await db.execute(
                    "DELETE FROM giveaway_participants WHERE giveaway_id=? AND user_id=?",
                    (gw_id, interaction.user.id),
                )
                await db.commit()
                action = "left"
            else:
                # Enter (bonus role = 2 entries)
                bonus_role_id = gw.get("bonus_role_id")
                entries = 1
                if bonus_role_id:
                    member = interaction.guild.get_member(interaction.user.id)
                    if member and any(r.id == bonus_role_id for r in member.roles):
                        entries = 2
                for _ in range(entries):
                    try:
                        await db.execute(
                            "INSERT INTO giveaway_participants (giveaway_id, user_id) VALUES (?,?)",
                            (gw_id, interaction.user.id),
                        )
                    except Exception:
                        pass
                await db.commit()
                action = "entered"

            # Get new count
            async with db.execute(
                "SELECT COUNT(DISTINCT user_id) FROM giveaway_participants WHERE giveaway_id=?",
                (gw_id,),
            ) as c:
                row = await c.fetchone()
            new_count = row[0] if row else 0

        # Update button label + respond
        for item in self.children:
            if isinstance(item, discord.ui.Button) and item.custom_id == f"gw_enter:{gw_id}":
                item.label = self._label(new_count)
                break

        if action == "entered":
            msg = f"✅ You've entered the giveaway for **{gw['prize']}**!\n\nClick the button again to withdraw your entry."
        else:
            msg = f"↩️ You've withdrawn from the **{gw['prize']}** giveaway."

        await interaction.response.edit_message(view=self)
        await interaction.followup.send(msg, ephemeral=True)


# ── Cog ────────────────────────────────────────────────────────────────────────

class Giveaway(commands.GroupCog, name="giveaway"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.process_giveaways.start()

    async def cog_load(self):
        """Re-register persistent views for all active giveaways on startup."""
        await self.bot.wait_until_ready()
        try:
            async with self.bot.db._db_context() as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT giveaway_id, channel_id, message_id FROM giveaways WHERE ended=0 AND message_id IS NOT NULL"
                ) as c:
                    rows = [dict(r) for r in await c.fetchall()]
            for row in rows:
                gw_id = row["giveaway_id"]
                # Get current entry count for button label
                async with self.bot.db._db_context() as db:
                    async with db.execute(
                        "SELECT COUNT(DISTINCT user_id) FROM giveaway_participants WHERE giveaway_id=?",
                        (gw_id,),
                    ) as c:
                        cnt_row = await c.fetchone()
                cnt = cnt_row[0] if cnt_row else 0
                view = GiveawayEntryView(gw_id=gw_id, bot=self.bot, entry_count=cnt)
                self.bot.add_view(view)
            logger.info(f"[Giveaway] Re-registered {len(rows)} persistent views.")
        except Exception as e:
            logger.error(f"[Giveaway] cog_load view restore error: {e}")

    def cog_unload(self):
        self.process_giveaways.cancel()

    @tasks.loop(minutes=1)
    async def process_giveaways(self):
        try:
            async with self.bot.db._db_context() as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT * FROM giveaways WHERE ended=0 AND paused=0 AND end_time <= datetime('now')"
                ) as c:
                    rows = [dict(r) for r in await c.fetchall()]
            for gw in rows:
                await self._end_giveaway(gw["giveaway_id"])
        except Exception as e:
            logger.error(f"[Giveaway] loop error: {e}")

    @process_giveaways.before_loop
    async def before_loop(self):
        await self.bot.wait_until_ready()

    # ── Core end logic ────────────────────────────────────────────────────────

    async def _end_giveaway(self, giveaway_id: int):
        async with self.bot.db._db_context() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM giveaways WHERE giveaway_id=?", (giveaway_id,)) as c:
                gw = await c.fetchone()
            if not gw:
                return None
            gw = dict(gw)
            async with db.execute(
                "SELECT DISTINCT user_id FROM giveaway_participants WHERE giveaway_id=?", (giveaway_id,)
            ) as c:
                participant_ids = [r["user_id"] for r in await c.fetchall()]
            # Weighted entries (bonus role = 2 rows)
            async with db.execute(
                "SELECT user_id FROM giveaway_participants WHERE giveaway_id=?", (giveaway_id,)
            ) as c:
                weighted = [r["user_id"] for r in await c.fetchall()]
            await db.execute("UPDATE giveaways SET ended=1 WHERE giveaway_id=?", (giveaway_id,))
            await db.commit()

        winners = []
        if weighted:
            seen = set()
            pool = list(weighted)
            while len(winners) < gw["winners_count"] and pool:
                pick = random.choice(pool)
                if pick not in seen:
                    seen.add(pick)
                    winners.append(pick)
                pool.remove(pick)

        channel = self.bot.get_channel(gw["channel_id"])
        if not channel:
            return winners

        image_url = gw.get("image_url")

        # Edit original message — swap live embed for ended embed, disable button
        if gw.get("message_id"):
            try:
                msg = await channel.fetch_message(gw["message_id"])
                ended_embed = _gw_ended_embed(
                    prize       = gw["prize"],
                    winners     = winners,
                    entry_count = len(participant_ids),
                    gw_id       = giveaway_id,
                    image_url   = image_url,
                )
                disabled_view = discord.ui.View()
                done_btn = discord.ui.Button(label="Giveaway Ended", style=discord.ButtonStyle.secondary, disabled=True, emoji="🔒")
                disabled_view.add_item(done_btn)
                await msg.edit(embed=ended_embed, view=disabled_view)
            except Exception as e:
                logger.error(f"[Giveaway] edit original msg error: {e}")

        # Announcement in channel
        if winners:
            winner_mentions = " ".join(f"<@{w}>" for w in winners)
            announce_embed = discord.Embed(
                color       = GW_WIN,
                title       = "🎉  We Have Winners!",
                description = f"**{gw['prize']}** — Congratulations!\n\n{winner_mentions}",
                timestamp   = discord.utils.utcnow(),
            )
            announce_embed.add_field(name="Total Entries", value=str(len(participant_ids)), inline=True)
            announce_embed.set_footer(text=f"XERO ELITE  •  Giveaway #{giveaway_id}")
            await channel.send(content=winner_mentions, embed=announce_embed)

            # DM each winner
            for uid in winners:
                try:
                    member = channel.guild.get_member(uid)
                    if member:
                        await member.send(embed=_gw_winner_dm_embed(gw["prize"], channel))
                except Exception:
                    pass
        else:
            no_winner_embed = discord.Embed(
                color       = GW_ENDED,
                title       = "Giveaway Ended",
                description = f"No one entered **{gw['prize']}**. Better luck next time!",
                timestamp   = discord.utils.utcnow(),
            )
            no_winner_embed.set_footer(text=f"XERO ELITE  •  Giveaway #{giveaway_id}")
            await channel.send(embed=no_winner_embed)

        return winners

    # ── Slash Commands ────────────────────────────────────────────────────────

    @app_commands.command(name="start", description="Launch a creative giveaway with image, role requirements, and bonus entries.")
    @app_commands.describe(
        prize         = "What are you giving away?",
        duration      = "Duration in minutes (default: 60)",
        winners       = "Number of winners (default: 1)",
        channel       = "Channel to post in (default: current)",
        required_role = "Role required to enter",
        bonus_role    = "Role that gets 2× entries",
        ping_role     = "Role to ping when giveaway starts",
        image         = "Upload an image for the giveaway (e.g. prize screenshot)",
        image_url     = "Or paste an image URL instead of uploading",
        note          = "Optional extra note shown in the giveaway",
    )
    @app_commands.checks.has_permissions(manage_messages=True)
    @command_guard
    async def start(
        self,
        interaction: discord.Interaction,
        prize:         str,
        duration:      int                  = 60,
        winners:       int                  = 1,
        channel:       discord.TextChannel  = None,
        required_role: discord.Role         = None,
        bonus_role:    discord.Role         = None,
        ping_role:     discord.Role         = None,
        image:         discord.Attachment   = None,
        image_url:     str                  = None,
        note:          str                  = None,
    ):
        await interaction.response.defer(ephemeral=True)

        ch       = channel or interaction.channel
        end_ts   = int((discord.utils.utcnow() + datetime.timedelta(minutes=max(1, duration))).timestamp())
        w_count  = max(1, winners)
        final_img = None

        # Resolve image — uploaded attachment takes priority over URL
        if image:
            # Validate it's an image
            if not image.content_type or not image.content_type.startswith("image/"):
                return await interaction.followup.send("The attached file must be an image (PNG, JPG, GIF, WebP).", ephemeral=True)
            final_img = image.url
        elif image_url:
            final_img = image_url

        # Ensure columns exist (idempotent)
        async with self.bot.db._db_context() as db:
            for col_def in (
                "required_role_id INTEGER",
                "bonus_role_id    INTEGER",
                "image_url        TEXT",
            ):
                try:
                    await db.execute(f"ALTER TABLE giveaways ADD COLUMN {col_def}")
                except Exception:
                    pass

            sql = (
                "INSERT INTO giveaways "
                "(guild_id, channel_id, prize, winners_count, end_time, created_by, "
                " required_role_id, bonus_role_id, image_url) "
                "VALUES (?,?,?,?, datetime(?,'unixepoch'), ?,?,?,?)"
            )
            async with db.execute(sql, (
                interaction.guild.id, ch.id, prize, w_count, end_ts,
                interaction.user.id,
                required_role.id if required_role else None,
                bonus_role.id    if bonus_role    else None,
                final_img,
            )) as c:
                gw_id = c.lastrowid
            await db.commit()

        embed = _gw_active_embed(
            prize         = prize,
            end_ts        = end_ts,
            winners_count = w_count,
            host          = interaction.user,
            entry_count   = 0,
            gw_id         = gw_id,
            required_role = required_role,
            bonus_role    = bonus_role,
            image_url     = final_img,
            description   = note,
        )

        view = GiveawayEntryView(gw_id=gw_id, bot=self.bot, entry_count=0)
        self.bot.add_view(view)  # register persistent view

        ping_content = ping_role.mention if ping_role else None
        msg = await ch.send(content=ping_content, embed=embed, view=view)

        async with self.bot.db._db_context() as db:
            await db.execute("UPDATE giveaways SET message_id=? WHERE giveaway_id=?", (msg.id, gw_id))
            await db.commit()

        confirm = discord.Embed(
            color       = GW_GOLD,
            title       = "Giveaway Started",
            description = f"**{prize}** is live in {ch.mention}",
            timestamp   = discord.utils.utcnow(),
        )
        confirm.add_field(name="Ends",     value=f"<t:{end_ts}:F>",  inline=True)
        confirm.add_field(name="Winners",  value=str(w_count),       inline=True)
        confirm.add_field(name="ID",       value=f"`{gw_id}`",       inline=True)
        confirm.set_footer(text="XERO ELITE  •  xero.gg")
        await interaction.followup.send(embed=confirm, ephemeral=True)

    @app_commands.command(name="end", description="End a giveaway immediately and draw winners.")
    @app_commands.describe(giveaway_id="Giveaway ID to end")
    @app_commands.checks.has_permissions(manage_messages=True)
    @command_guard
    async def end(self, interaction: discord.Interaction, giveaway_id: int):
        await interaction.response.defer(ephemeral=True)
        winners = await self._end_giveaway(giveaway_id)
        if winners is None:
            return await interaction.followup.send("Giveaway not found.", ephemeral=True)
        msg = f"Giveaway #{giveaway_id} ended. **{len(winners)} winner(s)** drawn." if winners else f"Giveaway #{giveaway_id} ended — no entries."
        await interaction.followup.send(embed=discord.Embed(color=GW_GOLD, description=f"✅ {msg}"), ephemeral=True)

    @app_commands.command(name="reroll", description="Reroll winners for a giveaway that has already ended.")
    @app_commands.describe(giveaway_id="Giveaway ID", winners="How many to reroll (default: 1)")
    @app_commands.checks.has_permissions(manage_messages=True)
    @command_guard
    async def reroll(self, interaction: discord.Interaction, giveaway_id: int, winners: int = 1):
        await interaction.response.defer(ephemeral=True)
        async with self.bot.db._db_context() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM giveaways WHERE giveaway_id=?", (giveaway_id,)) as c:
                gw = await c.fetchone()
            if not gw:
                return await interaction.followup.send("Giveaway not found.", ephemeral=True)
            gw = dict(gw)
            async with db.execute(
                "SELECT user_id FROM giveaway_participants WHERE giveaway_id=?", (giveaway_id,)
            ) as c:
                pool = [r["user_id"] for r in await c.fetchall()]

        if not pool:
            return await interaction.followup.send("No participants to reroll from.", ephemeral=True)

        new_winners = random.sample(pool, min(winners, len(pool)))
        channel = self.bot.get_channel(gw["channel_id"])
        if channel:
            mentions = " ".join(f"<@{w}>" for w in new_winners)
            embed = discord.Embed(
                color       = GW_WIN,
                title       = "🎲  Reroll — New Winners!",
                description = f"**{gw['prize']}**\n\n{mentions}",
                timestamp   = discord.utils.utcnow(),
            )
            embed.set_footer(text=f"XERO ELITE  •  Rerolled by {interaction.user.display_name}")
            await channel.send(content=mentions, embed=embed)
            for uid in new_winners:
                try:
                    m = channel.guild.get_member(uid)
                    if m:
                        await m.send(embed=_gw_winner_dm_embed(gw["prize"], channel))
                except Exception:
                    pass
        await interaction.followup.send(embed=discord.Embed(color=GW_WIN, description=f"✅ Rerolled **{len(new_winners)}** winner(s)."), ephemeral=True)

    @app_commands.command(name="pause", description="Pause a live giveaway (no new entries).")
    @app_commands.describe(giveaway_id="Giveaway ID")
    @app_commands.checks.has_permissions(manage_messages=True)
    @command_guard
    async def pause(self, interaction: discord.Interaction, giveaway_id: int):
        async with self.bot.db._db_context() as db:
            await db.execute("UPDATE giveaways SET paused=1 WHERE giveaway_id=? AND guild_id=?", (giveaway_id, interaction.guild.id))
            await db.commit()
        await interaction.response.send_message(embed=discord.Embed(color=GW_GOLD, description=f"⏸  Giveaway #{giveaway_id} paused."), ephemeral=True)

    @app_commands.command(name="resume", description="Resume a paused giveaway.")
    @app_commands.describe(giveaway_id="Giveaway ID")
    @app_commands.checks.has_permissions(manage_messages=True)
    @command_guard
    async def resume(self, interaction: discord.Interaction, giveaway_id: int):
        async with self.bot.db._db_context() as db:
            await db.execute("UPDATE giveaways SET paused=0 WHERE giveaway_id=? AND guild_id=?", (giveaway_id, interaction.guild.id))
            await db.commit()
        await interaction.response.send_message(embed=discord.Embed(color=GW_WIN, description=f"▶️  Giveaway #{giveaway_id} resumed."), ephemeral=True)

    @app_commands.command(name="list", description="List all active giveaways in this server.")
    @command_guard
    async def list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        async with self.bot.db._db_context() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM giveaways WHERE guild_id=? AND ended=0 ORDER BY end_time ASC",
                (interaction.guild.id,),
            ) as c:
                rows = [dict(r) for r in await c.fetchall()]

        if not rows:
            return await interaction.followup.send(embed=discord.Embed(color=GW_ENDED, description="No active giveaways right now."), ephemeral=True)

        embed = discord.Embed(color=GW_GOLD, title=f"Active Giveaways  ({len(rows)})", timestamp=discord.utils.utcnow())
        for gw in rows[:10]:
            end_ts = int(datetime.datetime.fromisoformat(gw["end_time"]).replace(tzinfo=datetime.timezone.utc).timestamp())
            status = "⏸ Paused" if gw.get("paused") else f"Ends <t:{end_ts}:R>"
            embed.add_field(
                name  = f"#{gw['giveaway_id']}  —  {gw['prize']}",
                value = f"{status}  ·  {gw['winners_count']} winner(s)  ·  <#{gw['channel_id']}>",
                inline= False,
            )
        embed.set_footer(text="XERO ELITE  •  xero.gg")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="info", description="Show details about a specific giveaway.")
    @app_commands.describe(giveaway_id="Giveaway ID")
    @command_guard
    async def info(self, interaction: discord.Interaction, giveaway_id: int):
        await interaction.response.defer(ephemeral=True)
        async with self.bot.db._db_context() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM giveaways WHERE giveaway_id=?", (giveaway_id,)) as c:
                gw = await c.fetchone()
            if not gw:
                return await interaction.followup.send("Giveaway not found.", ephemeral=True)
            gw = dict(gw)
            async with db.execute(
                "SELECT COUNT(DISTINCT user_id) FROM giveaway_participants WHERE giveaway_id=?", (giveaway_id,)
            ) as c:
                cnt = (await c.fetchone())[0]

        end_ts = int(datetime.datetime.fromisoformat(gw["end_time"]).replace(tzinfo=datetime.timezone.utc).timestamp())
        status = "Ended" if gw["ended"] else ("Paused" if gw.get("paused") else "Active")
        embed = discord.Embed(
            color       = GW_ENDED if gw["ended"] else GW_GOLD,
            title       = f"Giveaway #{giveaway_id}",
            description = f"**{gw['prize']}**",
            timestamp   = discord.utils.utcnow(),
        )
        embed.add_field(name="Status",   value=status,          inline=True)
        embed.add_field(name="Winners",  value=str(gw["winners_count"]), inline=True)
        embed.add_field(name="Entries",  value=str(cnt),        inline=True)
        embed.add_field(name="Ends",     value=f"<t:{end_ts}:F>", inline=True)
        embed.add_field(name="Host",     value=f"<@{gw['created_by']}>", inline=True)
        embed.add_field(name="Channel",  value=f"<#{gw['channel_id']}>", inline=True)
        if gw.get("image_url"):
            embed.set_thumbnail(url=gw["image_url"])
        embed.set_footer(text="XERO ELITE  •  xero.gg")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="delete", description="Permanently delete a giveaway record.")
    @app_commands.describe(giveaway_id="Giveaway ID to delete")
    @app_commands.checks.has_permissions(administrator=True)
    @command_guard
    async def delete(self, interaction: discord.Interaction, giveaway_id: int):
        async with self.bot.db._db_context() as db:
            await db.execute("DELETE FROM giveaway_participants WHERE giveaway_id=?", (giveaway_id,))
            await db.execute("DELETE FROM giveaways WHERE giveaway_id=? AND guild_id=?", (giveaway_id, interaction.guild.id))
            await db.commit()
        await interaction.response.send_message(
            embed=discord.Embed(color=GW_GOLD, description=f"✅ Giveaway #{giveaway_id} deleted permanently."),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Giveaway(bot))
