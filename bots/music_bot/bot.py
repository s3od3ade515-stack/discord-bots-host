import asyncio
import os
import random
import time

import discord
import yt_dlp
from discord import app_commands

TOKEN = os.getenv("MUSIC_BOT_TOKEN") or os.getenv("DISCORD_TOKEN") or ""
if not TOKEN:
    raise SystemExit("❌ ما فيه توكن. حط MUSIC_BOT_TOKEN في Environment Variables.")

# اختياري: ملف كوكيز يوتيوب (يفيد لو يوتيوب حظر IP الاستضافة)
COOKIES = os.getenv("YTDLP_COOKIES")

LEAVE_WHEN_OWNER_LEAVES = True   # يطلع البوت إذا صاحب الأمر طلع من الروم
LEAVE_WHEN_EMPTY = True          # يطلع البوت إذا الروم فضى من البشر
IDLE_TIMEOUT = 300               # يطلع البوت بعد 5 دقايق بدون تشغيل
VOLUME_STEP = 0.10
MAX_VOLUME = 1.5
PAGE_SIZE = 5
PANEL_REFRESH = 10               # تحديث شريط التقدم كل كم ثانية
COLOR = 0x8A2BE2

FFMPEG_OPTS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

LOOP_LABELS = {0: ("تكرار: لا", "🔁"), 1: ("تكرار مقطع", "🔂"), 2: ("تكرار الطابور", "🔁")}


# ───────────────────────── YouTube ─────────────────────────
async def search_youtube(query: str, n: int = 15):
    def run():
        opts = {"quiet": True, "extract_flat": True, "skip_download": True}
        if COOKIES:
            opts["cookiefile"] = COOKIES
        with yt_dlp.YoutubeDL(opts) as y:
            info = y.extract_info(f"ytsearch{n}:{query}", download=False)
        out = []
        for e in info.get("entries", []) or []:
            vid = e.get("id")
            if not vid:
                continue
            out.append({
                "title": e.get("title") or "بدون عنوان",
                "url": f"https://www.youtube.com/watch?v={vid}",
                "thumb": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                "duration": e.get("duration"),
                "channel": e.get("channel") or e.get("uploader") or "",
            })
        return out

    return await asyncio.to_thread(run)


async def get_stream_url(url: str) -> str:
    def run():
        opts = {"format": "bestaudio/best", "quiet": True, "noplaylist": True}
        if COOKIES:
            opts["cookiefile"] = COOKIES
        with yt_dlp.YoutubeDL(opts) as y:
            return y.extract_info(url, download=False)["url"]

    return await asyncio.to_thread(run)


def fmt_duration(sec):
    if not sec:
        return "—"
    sec = int(sec)
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# ───────────────────────── الحالة ─────────────────────────
class Player:
    def __init__(self, guild_id, owner_id, vc):
        self.guild_id = guild_id
        self.owner_id = owner_id
        self.vc: discord.VoiceClient = vc
        self.track = None
        self.queue: list[dict] = []
        self.history: list[dict] = []
        self.loop = 0                 # 0 بدون، 1 مقطع، 2 الطابور
        self.volume = 0.5
        self.message: discord.Message | None = None
        self.mode = "player"          # player / search (عشان التحديث التلقائي ما يمسح نتائج البحث)
        self.gen = 0                  # يمنع callback المقطع القديم من التأثير
        self.acc = 0.0                # ثواني التشغيل المتراكمة
        self.resume_ts = None         # وقت آخر استئناف
        self.idle_since = time.monotonic()
        self.task: asyncio.Task | None = None

    def elapsed(self):
        if not self.track:
            return 0
        extra = (time.monotonic() - self.resume_ts) if self.resume_ts else 0
        return self.acc + extra

    def pause(self):
        self.acc = self.elapsed()
        self.resume_ts = None
        self.vc.pause()

    def resume(self):
        self.resume_ts = time.monotonic()
        self.vc.resume()


players: dict[int, Player] = {}


def progress_bar(player: Player, length=14):
    total = player.track.get("duration")
    if not total:
        return "🔴 بث مباشر / مدة غير معروفة"
    pos = min(player.elapsed(), total)
    idx = int(pos / total * (length - 1))
    bar = "".join("🔘" if i == idx else "▬" for i in range(length))
    return f"`{fmt_duration(pos)}` {bar} `{fmt_duration(total)}`"


def volume_bar(player: Player):
    filled = round(player.volume / MAX_VOLUME * 10)
    return "▰" * filled + "▱" * (10 - filled) + f" {int(player.volume * 100)}%"


def idle_embed(player: Player):
    e = discord.Embed(
        title="🎵 مشغل الموسيقى",
        description="اضغط **🔍 بحث** وابحث عن أي مقطع من يوتيوب.\n"
                    "تقدر تختار أكثر من مقطع مرة وحدة وتنضاف للطابور 📜",
        color=COLOR,
    )
    if player.queue:
        e.add_field(name="📜 بالطابور", value=f"{len(player.queue)} مقطع", inline=True)
    e.add_field(name="🔊 الصوت", value=volume_bar(player), inline=True)
    return e


def track_embed(player: Player):
    t = player.track
    paused = player.vc.is_paused()
    e = discord.Embed(
        title=t["title"],
        url=t["url"],
        description=f"{'⏸ متوقف مؤقتًا' if paused else '▶️ يتم التشغيل الآن'}\n{progress_bar(player)}",
        color=COLOR,
    )
    e.set_image(url=t["thumb"])
    if t.get("channel"):
        e.add_field(name="📺 القناة", value=t["channel"], inline=True)
    e.add_field(name="🔁 التكرار", value=LOOP_LABELS[player.loop][0].replace("تكرار: ", "").replace("تكرار ", ""), inline=True)
    e.add_field(name="📜 بالطابور", value=str(len(player.queue)), inline=True)
    e.add_field(name="🔊 الصوت", value=volume_bar(player), inline=False)
    if player.queue:
        e.add_field(name="⏭ التالي", value=player.queue[0]["title"][:100], inline=False)
    if t.get("requester"):
        e.set_footer(text=f"طلب: {t['requester']}")
    return e


def current_embed(player: Player):
    return track_embed(player) if player.track else idle_embed(player)


async def play_track(player: Player, track: dict):
    """يشغّل مقطع. يرمي استثناء إذا فشل."""
    player.gen += 1
    my_gen = player.gen
    if player.vc.is_playing() or player.vc.is_paused():
        player.vc.stop()
    stream = await get_stream_url(track["url"])
    if my_gen != player.gen:
        return  # صار تشغيل ثاني بالأثناء
    src = discord.PCMVolumeTransformer(
        discord.FFmpegPCMAudio(stream, **FFMPEG_OPTS), volume=player.volume
    )
    loop = asyncio.get_running_loop()

    def after(err):
        if err:
            print("Player error:", err)
        asyncio.run_coroutine_threadsafe(advance(player, my_gen), loop)

    player.track = track
    player.acc = 0.0
    player.resume_ts = time.monotonic()
    player.idle_since = None
    player.vc.play(src, after=after)


async def advance(player: Player, gen=None, manual=False):
    """ينتقل للمقطع التالي حسب وضع التكرار والطابور."""
    if gen is not None and gen != player.gen:
        return
    finished = player.track
    if finished and player.loop == 1 and not manual:
        nxt = finished
    else:
        if finished:
            player.history.append(finished)
            del player.history[:-50]
            if player.loop == 2:
                player.queue.append(finished)
        nxt = player.queue.pop(0) if player.queue else None

    while nxt:
        try:
            await play_track(player, nxt)
            break
        except Exception as ex:
            print("Play error:", ex)
            nxt = player.queue.pop(0) if player.queue else None

    if not nxt:
        player.gen += 1
        player.track = None
        player.idle_since = time.monotonic()
    await refresh_panel(player)


async def go_previous(player: Player):
    if player.track and (player.elapsed() > 5 or not player.history):
        await play_track(player, player.track)  # إعادة المقطع من البداية
    elif player.history:
        prev = player.history.pop()
        if player.track:
            player.queue.insert(0, player.track)
        await play_track(player, prev)


async def refresh_panel(player: Player):
    if player.message and player.mode == "player":
        try:
            await player.message.edit(embed=current_embed(player), view=PlayerView(player))
        except discord.HTTPException:
            pass


async def updater(player: Player):
    """يحدّث شريط التقدم، ويخرج البوت إذا طوّل بدون تشغيل."""
    try:
        while players.get(player.guild_id) is player:
            await asyncio.sleep(PANEL_REFRESH)
            if player.track and not player.vc.is_paused():
                await refresh_panel(player)
            elif not player.track and player.idle_since and time.monotonic() - player.idle_since > IDLE_TIMEOUT:
                await shutdown_player(player, "ما فيه تشغيل من فترة فطلع البوت.")
                return
    except asyncio.CancelledError:
        pass


async def shutdown_player(player: Player, reason: str):
    players.pop(player.guild_id, None)
    player.gen += 1
    if player.task and player.task is not asyncio.current_task():
        player.task.cancel()
    try:
        if player.vc.is_connected():
            player.vc.stop()
            await player.vc.disconnect()
    except Exception:
        pass
    if player.message:
        try:
            await player.message.edit(
                embed=discord.Embed(title="👋 تم الخروج", description=reason, color=COLOR),
                view=None,
            )
        except discord.HTTPException:
            pass


async def guard(interaction: discord.Interaction, player: Player) -> bool:
    if not player.vc.is_connected():
        await interaction.response.send_message("البوت مو متصل بروم صوتي، استخدم /play من جديد.", ephemeral=True)
        return False
    vs = interaction.user.voice
    if not (vs and vs.channel and vs.channel.id == player.vc.channel.id):
        await interaction.response.send_message("لازم تكون في نفس الروم الصوتي مع البوت.", ephemeral=True)
        return False
    return True


# ───────────────────────── الواجهات ─────────────────────────
class SearchModal(discord.ui.Modal, title="🔍 بحث في يوتيوب"):
    query = discord.ui.TextInput(label="اسم المقطع", placeholder="اكتب اسم المقطع...", max_length=100)

    def __init__(self, player: Player):
        super().__init__()
        self.player = player

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            results = await search_youtube(str(self.query))
        except Exception as ex:
            print("Search error:", ex)
            results = []
        if not results:
            await interaction.edit_original_response(
                embed=discord.Embed(title="❌ ما لقيت نتائج", description="جرّب كلمات ثانية.", color=0xE74C3C),
                view=PlayerView(self.player),
            )
            return
        view = SearchView(self.player, results, str(self.query))
        await interaction.edit_original_response(embed=view.make_embed(), view=view)


class QueueView(discord.ui.View):
    def __init__(self, player: Player):
        super().__init__(timeout=120)
        self.player = player
        self.clear_btn.disabled = not player.queue

    @staticmethod
    def build_embed(player: Player):
        e = discord.Embed(title="📜 الطابور", color=COLOR)
        if player.track:
            e.add_field(name="▶️ الآن", value=player.track["title"][:200], inline=False)
        if player.queue:
            lines = [f"**{i}.** {t['title'][:70]} `{fmt_duration(t['duration'])}`"
                     for i, t in enumerate(player.queue[:10], 1)]
            if len(player.queue) > 10:
                lines.append(f"... و {len(player.queue) - 10} مقطع ثاني")
            total = sum(t.get("duration") or 0 for t in player.queue)
            e.add_field(name=f"⏭ التالي ({len(player.queue)} • {fmt_duration(total)})", value="\n".join(lines), inline=False)
        else:
            e.description = "الطابور فاضي."
        return e

    @discord.ui.button(label="مسح الطابور", emoji="🗑", style=discord.ButtonStyle.danger)
    async def clear_btn(self, interaction: discord.Interaction, _):
        if not await guard(interaction, self.player):
            return
        self.player.queue.clear()
        await interaction.response.edit_message(embed=self.build_embed(self.player), view=QueueView(self.player))
        await refresh_panel(self.player)


class PlayerView(discord.ui.View):
    def __init__(self, player: Player):
        super().__init__(timeout=None)
        self.player = player
        player.mode = "player"
        playing = bool(player.track)
        paused = player.vc.is_paused() if player.vc else False

        self.toggle.label = "تشغيل" if paused else "إيقاف مؤقت"
        self.toggle.emoji = "▶️" if paused else "⏸"
        self.toggle.disabled = not playing
        self.prev_btn.disabled = not (playing or player.history)
        self.skip_btn.disabled = not playing
        self.stop_btn.disabled = not (playing or player.queue)
        self.shuffle_btn.disabled = len(player.queue) < 2

        label, emoji = LOOP_LABELS[player.loop]
        self.loop_btn.label, self.loop_btn.emoji = label, emoji
        self.loop_btn.style = discord.ButtonStyle.success if player.loop else discord.ButtonStyle.secondary

    # ── الصف الأول ──
    @discord.ui.button(label="بحث", emoji="🔍", style=discord.ButtonStyle.primary, row=0)
    async def search(self, interaction: discord.Interaction, _):
        if await guard(interaction, self.player):
            await interaction.response.send_modal(SearchModal(self.player))

    @discord.ui.button(label="السابق", emoji="⏮", style=discord.ButtonStyle.secondary, row=0)
    async def prev_btn(self, interaction: discord.Interaction, _):
        if not await guard(interaction, self.player):
            return
        await interaction.response.defer()
        try:
            await go_previous(self.player)
        except Exception as ex:
            print("Prev error:", ex)
        await interaction.edit_original_response(embed=current_embed(self.player), view=PlayerView(self.player))

    @discord.ui.button(label="إيقاف مؤقت", emoji="⏸", style=discord.ButtonStyle.secondary, row=0)
    async def toggle(self, interaction: discord.Interaction, _):
        p = self.player
        if not await guard(interaction, p):
            return
        if p.vc.is_paused():
            p.resume()
        elif p.vc.is_playing():
            p.pause()
        await interaction.response.edit_message(embed=current_embed(p), view=PlayerView(p))

    @discord.ui.button(label="تخطي", emoji="⏭", style=discord.ButtonStyle.secondary, row=0)
    async def skip_btn(self, interaction: discord.Interaction, _):
        if not await guard(interaction, self.player):
            return
        await interaction.response.defer()
        await advance(self.player, manual=True)
        await interaction.edit_original_response(embed=current_embed(self.player), view=PlayerView(self.player))

    @discord.ui.button(label="إيقاف", emoji="⏹", style=discord.ButtonStyle.danger, row=0)
    async def stop_btn(self, interaction: discord.Interaction, _):
        p = self.player
        if not await guard(interaction, p):
            return
        p.gen += 1
        p.vc.stop()
        p.track = None
        p.queue.clear()
        p.idle_since = time.monotonic()
        await interaction.response.edit_message(embed=idle_embed(p), view=PlayerView(p))

    # ── الصف الثاني ──
    @discord.ui.button(label="خفض", emoji="🔉", style=discord.ButtonStyle.secondary, row=1)
    async def vol_down(self, interaction: discord.Interaction, _):
        await self._change_volume(interaction, -VOLUME_STEP)

    @discord.ui.button(label="رفع", emoji="🔊", style=discord.ButtonStyle.secondary, row=1)
    async def vol_up(self, interaction: discord.Interaction, _):
        await self._change_volume(interaction, VOLUME_STEP)

    @discord.ui.button(label="تكرار", emoji="🔁", style=discord.ButtonStyle.secondary, row=1)
    async def loop_btn(self, interaction: discord.Interaction, _):
        p = self.player
        if not await guard(interaction, p):
            return
        p.loop = (p.loop + 1) % 3
        await interaction.response.edit_message(embed=current_embed(p), view=PlayerView(p))

    @discord.ui.button(label="خلط", emoji="🔀", style=discord.ButtonStyle.secondary, row=1)
    async def shuffle_btn(self, interaction: discord.Interaction, _):
        p = self.player
        if not await guard(interaction, p):
            return
        random.shuffle(p.queue)
        await interaction.response.edit_message(embed=current_embed(p), view=PlayerView(p))

    @discord.ui.button(label="الطابور", emoji="📜", style=discord.ButtonStyle.secondary, row=1)
    async def queue_btn(self, interaction: discord.Interaction, _):
        await interaction.response.send_message(
            embed=QueueView.build_embed(self.player), view=QueueView(self.player), ephemeral=True
        )

    async def _change_volume(self, interaction, delta):
        p = self.player
        if not await guard(interaction, p):
            return
        p.volume = round(min(MAX_VOLUME, max(0.0, p.volume + delta)), 2)
        if isinstance(p.vc.source, discord.PCMVolumeTransformer):
            p.vc.source.volume = p.volume
        await interaction.response.edit_message(embed=current_embed(p), view=PlayerView(p))


class SearchSelect(discord.ui.Select):
    def __init__(self, sv: "SearchView"):
        self.sv = sv
        start = sv.page * PAGE_SIZE
        opts = [
            discord.SelectOption(
                label=r["title"][:100],
                description=f"{fmt_duration(r['duration'])} • {r['channel']}"[:100],
                value=str(i),
            )
            for i, r in enumerate(sv.results[start:start + PAGE_SIZE], start=start)
        ]
        super().__init__(placeholder="اختر مقطع أو أكثر...", options=opts,
                         min_values=1, max_values=len(opts), row=0)

    async def callback(self, interaction: discord.Interaction):
        p = self.sv.player
        if not await guard(interaction, p):
            return
        await interaction.response.defer()
        tracks = [dict(self.sv.results[int(v)], requester=interaction.user.display_name) for v in self.values]

        queued = 0
        if p.track is None:
            first = tracks.pop(0)
            try:
                await play_track(p, first)
            except Exception as ex:
                print("Play error:", ex)
                await interaction.followup.send("ما قدرت أشغّل هذا المقطع، جرّب غيره.", ephemeral=True)
                tracks = []
        p.queue.extend(tracks)
        queued = len(tracks)

        await interaction.edit_original_response(embed=current_embed(p), view=PlayerView(p))
        if queued:
            await interaction.followup.send(f"✅ أضفت {queued} مقطع للطابور.", ephemeral=True)


class SearchView(discord.ui.View):
    def __init__(self, player: Player, results: list, query: str, page: int = 0):
        super().__init__(timeout=300)
        self.player, self.results, self.query, self.page = player, results, query, page
        player.mode = "search"
        self.pages = max(1, (len(results) + PAGE_SIZE - 1) // PAGE_SIZE)
        self.add_item(SearchSelect(self))
        self.prev.disabled = page == 0
        self.next.disabled = page >= self.pages - 1

    def make_embed(self):
        start = self.page * PAGE_SIZE
        lines = [
            f"**{i + 1}.** {r['title']}  `{fmt_duration(r['duration'])}`"
            for i, r in enumerate(self.results[start:start + PAGE_SIZE], start=start)
        ]
        e = discord.Embed(title=f"🔍 نتائج: {self.query}", description="\n".join(lines), color=COLOR)
        e.set_footer(text=f"صفحة {self.page + 1}/{self.pages}")
        return e

    async def on_timeout(self):
        if self.player.mode == "search":
            self.player.mode = "player"
            await refresh_panel(self.player)

    async def _go(self, interaction, page):
        v = SearchView(self.player, self.results, self.query, page)
        await interaction.response.edit_message(embed=v.make_embed(), view=v)

    @discord.ui.button(label="السابق", emoji="◀️", style=discord.ButtonStyle.secondary, row=1)
    async def prev(self, interaction: discord.Interaction, _):
        await self._go(interaction, self.page - 1)

    @discord.ui.button(label="التالي", emoji="▶️", style=discord.ButtonStyle.secondary, row=1)
    async def next(self, interaction: discord.Interaction, _):
        await self._go(interaction, self.page + 1)

    @discord.ui.button(label="بحث جديد", emoji="🔍", style=discord.ButtonStyle.primary, row=1)
    async def new_search(self, interaction: discord.Interaction, _):
        if await guard(interaction, self.player):
            await interaction.response.send_modal(SearchModal(self.player))

    @discord.ui.button(label="رجوع", emoji="↩️", style=discord.ButtonStyle.danger, row=1)
    async def back(self, interaction: discord.Interaction, _):
        await interaction.response.edit_message(embed=current_embed(self.player), view=PlayerView(self.player))


# ───────────────────────── البوت ─────────────────────────
intents = discord.Intents.default()
intents.voice_states = True


class MusicBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        await self.tree.sync()


bot = MusicBot()


@bot.tree.command(name="play", description="افتح مشغّل الموسيقى (واكتب اسم مقطع إذا تبي تشغله مباشرة)")
@app_commands.describe(query="اسم المقطع (اختياري) — يشغّل أول نتيجة مباشرة")
async def play(interaction: discord.Interaction, query: str | None = None):
    if not interaction.guild:
        return await interaction.response.send_message("الأمر يشتغل داخل السيرفر فقط.", ephemeral=True)

    vs = interaction.user.voice
    if not vs or not vs.channel:
        return await interaction.response.send_message("ادخل روم صوتي أول، وبعدين اكتب /play 🎧", ephemeral=True)

    channel = vs.channel
    perms = channel.permissions_for(interaction.guild.me)
    if not (perms.connect and perms.speak):
        return await interaction.response.send_message("ما عندي صلاحية الدخول أو التكلم في هذا الروم.", ephemeral=True)

    await interaction.response.defer()

    player = players.get(interaction.guild.id)
    if player and player.vc.is_connected():
        if player.vc.channel.id != channel.id:
            await player.vc.move_to(channel)
        player.owner_id = interaction.user.id
    else:
        vc = await channel.connect(self_deaf=True)
        player = Player(interaction.guild.id, interaction.user.id, vc)
        players[interaction.guild.id] = player
        player.task = asyncio.create_task(updater(player))

    if player.message:
        try:
            await player.message.delete()
        except discord.HTTPException:
            pass

    # تشغيل مباشر إذا كتب اسم مقطع
    if query:
        try:
            results = await search_youtube(query, n=1)
            if results:
                track = dict(results[0], requester=interaction.user.display_name)
                if player.track is None:
                    await play_track(player, track)
                else:
                    player.queue.append(track)
        except Exception as ex:
            print("Quick play error:", ex)

    player.mode = "player"
    player.message = await interaction.followup.send(
        embed=current_embed(player), view=PlayerView(player), wait=True
    )


@bot.event
async def on_voice_state_update(member, before, after):
    if member.id == bot.user.id:
        if after.channel is None:
            p = players.pop(member.guild.id, None)
            if p:
                p.gen += 1
                if p.task:
                    p.task.cancel()
        return

    player = players.get(member.guild.id)
    if not player or not player.vc.is_connected():
        return
    bot_channel = player.vc.channel
    if not (before.channel == bot_channel and after.channel != bot_channel):
        return

    if LEAVE_WHEN_OWNER_LEAVES and member.id == player.owner_id:
        return await shutdown_player(player, "طلعت من الروم الصوتي فطلع البوت.")
    if LEAVE_WHEN_EMPTY and not [m for m in bot_channel.members if not m.bot]:
        await shutdown_player(player, "الروم فضى فطلع البوت.")


@bot.event
async def on_ready():
    print(f"✅ شغّال باسم {bot.user}")


bot.run(TOKEN)
