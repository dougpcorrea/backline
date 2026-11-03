"""Artist Ratings — avaliação anônima de qualidade musical, separada do ranking de participação.

Fluxo:
1. No canal #artist-ratings o membro cadastra 3 publicações dele (Instagram/YouTube, conta verificada).
2. Isso libera o app de avaliação (a mesma Activity do Discover, aberta neste canal).
3. No app ele ouve as 3 faixas de outro artista e dá notas de 1 a 5 em 4 critérios + feedback escrito opcional.
   As notas vão direto para o Supabase (função backline_rate, que confere quem está avaliando).
4. A cada poucos minutos o bot puxa as avaliações, guarda, avisa o artista por DM e recalcula:
   - a nota de qualidade (média ajustada) usada no Discover;
   - o bônus de participação de quem avalia (1x a 1,5x).
5. O artista vê as notas (a partir de 5 avaliadores) e os feedbacks, sempre anônimos, em "My results".
"""
from __future__ import annotations

import asyncio
import io
import json
import random

import discord
from discord.ext import commands, tasks

from ..db import now
from ..i18n import lang_of
from ..links import PLATFORM_NAME
from ..scoring import RATING_MIN_RATERS, RATING_WEIGHTS, adjusted, weighted
from ..services import rating_pool

CRITERIA = [
    ("execution", "Musical execution", "Pitch, timing, vocal or instrumental command."),
    ("interpretation", "Interpretation", "Expressiveness, musicality and the ability to convey emotion."),
    ("arrangement", "Arrangement & creativity", "Musical choices, personality and identity."),
    ("technical", "Technical quality", "Audio clarity, balance and presentation — no expensive gear required."),
]

PANEL_TEXT = """## ⭐ Artist Ratings
An honest, anonymous look at the **quality of your music** — separate from your participation level. A very active member can still be developing their craft, and a great musician can be quiet on the server. Ratings measure the music, not the activity.

### 🎚️ How it works
**1.** Tap **Submit my 3 tracks** and share three of your own Instagram or YouTube posts that best represent you.
**2.** That unlocks the **Ratings app**. Open it, pick an artist (similar genres and active members come first) and listen to all three of their tracks.
**3.** Rate four criteria from 1 to 5 and, if you want, leave written feedback:
> **Musical execution · 35%** — pitch, timing, vocal or instrumental command
> **Interpretation · 30%** — expressiveness, musicality, emotion
> **Arrangement & creativity · 20%** — musical choices, personality, identity
> **Technical quality · 15%** — audio clarity, balance, presentation (no expensive gear required)

### 🔒 Anonymous and fair
• Nobody ever sees who rated them. Written feedback reaches the artist anonymously.
• One rating per member per artist — and you can't rate yourself.
• Scores stay **provisional until 5 different members** have rated you, and use an adjusted average so a single 5/5 can't outweigh dozens of consistent ratings.
• Your detailed scores are **private**. Backline uses them only to recommend music and connect artists at a similar stage — never to label anyone publicly.

### 🚀 Rating helps you too
Every artist you rate gives your **participation level a small boost — up to 1.5×** when you've rated everyone available. Ratings also help Discover surface great music.

### 🤝 Our commitment to genuineness
Listen to every track before rating. Rate the music, not the person, the follower count or the friendship. Be honest and kind: feedback should help an artist grow — specific, respectful and constructive. Fake, retaliatory or careless ratings go against everything Backline stands for and can cost you your access.
"""


def _stars(v: float) -> str:
    full = int(round(v))
    return "★" * full + "☆" * (5 - full)


class TracksModal(discord.ui.Modal):
    def __init__(self, bot, lang: str, current: list[str]):
        super().__init__(title="Your 3 tracks for Artist Ratings", timeout=900)
        self.bot, self.lang = bot, lang
        self.inputs = []
        for i in range(3):
            ti = discord.ui.TextInput(max_length=300, placeholder="https://www.instagram.com/reel/...",
                                      default=current[i] if i < len(current) else None)
            self.inputs.append(ti)
            self.add_item(discord.ui.Label(text=f"Track {i + 1}", description="Your own Instagram or YouTube post",
                                           component=ti))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        msg = await self.bot.get_cog("Ratings").save_tracks(interaction.user, [i.value for i in self.inputs],
                                                             self.lang)
        await interaction.followup.send(msg, ephemeral=True)


class RatingsPanel(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Submit my 3 tracks", emoji="🎵", style=discord.ButtonStyle.secondary,
                       custom_id="bl:rate_tracks")
    async def tracks(self, interaction: discord.Interaction, _b) -> None:
        rows = await self.bot.db.fetchall("SELECT url FROM rating_tracks WHERE user_id=? ORDER BY slot",
                                          (interaction.user.id,))
        await interaction.response.send_modal(TracksModal(self.bot, lang_of(interaction.locale),
                                                          [r["url"] for r in rows]))

    @discord.ui.button(label="Open Ratings", emoji="⭐", style=discord.ButtonStyle.primary, custom_id="bl:rate_open")
    async def open(self, interaction: discord.Interaction, _b) -> None:
        if interaction.user.id not in set(await rating_pool(self.bot.db)):
            await interaction.response.send_message(
                "🔒 Submit **your 3 tracks** first — that unlocks the Ratings app.", ephemeral=True)
            return
        from .discover import launch_or_explain
        await launch_or_explain(self.bot, interaction, "Ratings")

    @discord.ui.button(label="My results", emoji="📊", style=discord.ButtonStyle.secondary,
                       custom_id="bl:rate_results")
    async def results(self, interaction: discord.Interaction, _b) -> None:
        await interaction.response.send_message(embed=await self.bot.get_cog("Ratings").results_embed(
            interaction.user.id), ephemeral=True)


class Ratings(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        bot.add_view(RatingsPanel(bot))
        self._lock = asyncio.Lock()
        self.sync_loop.start()

    def cog_unload(self) -> None:
        self.sync_loop.cancel()

    # ---------- cadastro das 3 faixas ----------
    async def save_tracks(self, user: discord.abc.User, urls: list[str], lang: str) -> str:
        db = self.bot.db
        member = await db.get_member(user.id)
        if not member or not member["verified_at"]:
            return "❌ Verify your profile first (Join in start-here), then come back."
        urls = [u.strip() for u in urls]
        if any(not u for u in urls):
            return "❌ Please share **three** tracks."
        feed = self.bot.get_cog("Feed")
        tracks, seen = [], set()
        for i, u in enumerate(urls, 1):
            err, link, details = await feed.validate_link(user, member, u, lang, check_duplicate=False)
            if err:
                return f"**Track {i}:** {err}"
            if link.external_id in seen:
                return f"❌ Track {i} is the same post as another one — share three different tracks."
            seen.add(link.external_id)
            tracks.append((link, details))
        await db.execute("DELETE FROM rating_tracks WHERE user_id=?", (user.id,))
        for slot, (link, d) in enumerate(tracks, 1):
            await db.execute(
                "INSERT INTO rating_tracks(user_id, slot, platform, url, external_id, title, thumb_url, created_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (user.id, slot, link.platform, link.url, link.external_id, d.get("title"), d.get("thumb"), now()))
        asyncio.create_task(self.sync())
        return ("✅ Your 3 tracks are saved — the **Ratings app is unlocked**! Tap **Open Ratings** to start "
                "listening to other artists. Your own results show up in **My results** as ratings come in.")

    # ---------- resultados ----------
    async def global_mean(self) -> float:
        rows = await self.bot.db.fetchall("SELECT * FROM artist_ratings")
        return sum(weighted(r) for r in rows) / len(rows) if rows else 3.5

    async def quality(self) -> dict[int, float]:
        """Nota ajustada (1 a 5) de cada artista avaliado — usada no critério 'qualidade' do Discover."""
        rows = await self.bot.db.fetchall("SELECT * FROM artist_ratings")
        if not rows:
            return {}
        m = sum(weighted(r) for r in rows) / len(rows)
        by: dict[int, list[float]] = {}
        for r in rows:
            by.setdefault(int(r["artist_id"]), []).append(weighted(r))
        return {uid: adjusted(v, m) for uid, v in by.items()}

    async def results_embed(self, uid: int) -> discord.Embed:
        db = self.bot.db
        rows = await db.fetchall("SELECT * FROM artist_ratings WHERE artist_id=?", (uid,))
        given = await db.fetchone("SELECT COUNT(*) AS n FROM artist_ratings WHERE rater_id=?", (uid,))
        e = discord.Embed(title="📊 Your Artist Ratings", colour=0xF0B232)
        has_tracks = uid in set(await rating_pool(db))
        if not has_tracks:
            e.description = "Submit **your 3 tracks** to start receiving ratings."
            return e
        n = len(rows)
        if n < RATING_MIN_RATERS:
            e.description = (f"**Provisional** — {n}/{RATING_MIN_RATERS} ratings so far. Your scores appear once "
                             f"{RATING_MIN_RATERS} different members have rated you (that keeps raters anonymous).")
        else:
            m = await self.global_mean()
            overall = adjusted([weighted(r) for r in rows], m)
            lines = [f"**Overall: {overall:.2f} / 5.00** {_stars(overall)}", f"-# Adjusted average · {n} ratings", ""]
            for key, name, _d in CRITERIA:
                avg = sum(r[key] for r in rows) / n
                lines.append(f"**{name}** · {int(RATING_WEIGHTS[key] * 100)}%  —  {avg:.1f} {_stars(avg)}")
            e.description = "\n".join(lines)
        fb = [r["feedback"].strip() for r in rows if (r["feedback"] or "").strip()]
        random.shuffle(fb)   # sem ordem de chegada: ajuda a manter o anonimato
        if fb:
            text = "\n\n".join(f"💬 *{f[:400]}*" for f in fb[:8])
            e.add_field(name=f"Anonymous feedback ({len(fb)})", value=text[:1024], inline=False)
        e.set_footer(text=f"You've rated {given['n'] if given else 0} artist(s). Every rating boosts your level.")
        return e

    # ---------- sincronização com o Supabase ----------
    def _rest(self) -> tuple[str, dict]:
        base, headers = self.bot.get_cog("Discover")._supabase()
        return f"{base}/rest/v1", dict(headers, **{"Content-Type": "application/json"})

    async def pull(self) -> list[dict]:
        """Traz as avaliações novas feitas no app."""
        rest, h = self._rest()
        async with self.bot.session.get(f"{rest}/backline_ratings?select=*", headers=h) as r:
            if r.status >= 300:
                raise RuntimeError(f"ratings: {r.status} {(await r.text())[:150]}")
            rows = await r.json()
        db, fresh = self.bot.db, []
        for x in rows:
            try:
                rater, artist = int(x["rater"]), int(x["artist"])
            except (TypeError, ValueError):
                continue
            if await db.fetchone("SELECT 1 FROM artist_ratings WHERE rater_id=? AND artist_id=?", (rater, artist)):
                continue
            await db.execute(
                "INSERT INTO artist_ratings(rater_id, artist_id, execution, interpretation, arrangement, technical, "
                "feedback, rated_at) VALUES(?,?,?,?,?,?,?,?)",
                (rater, artist, x["execution"], x["interpretation"], x["arrangement"], x["technical"],
                 (x.get("feedback") or "")[:1000] or None, now()))
            fresh.append({"rater": rater, "artist": artist})
        return fresh

    async def push_pool(self, pool: list[int]) -> None:
        rest, h = self._rest()
        async with self.bot.session.delete(f"{rest}/backline_rating_pool?user_id=neq.0", headers=h) as r:
            if r.status >= 300:
                raise RuntimeError(f"pool: {r.status} {(await r.text())[:150]}")
        if pool:
            async with self.bot.session.post(f"{rest}/backline_rating_pool", headers=h,
                                             json=[{"user_id": str(u)} for u in pool]) as r:
                if r.status >= 300:
                    raise RuntimeError(f"pool: {r.status} {(await r.text())[:150]}")

    async def publish(self, pool: list[int]) -> None:
        """ratings.json: artistas avaliáveis com as 3 faixas, gêneros e nível (o app ordena por afinidade)."""
        from PIL import Image
        disc = self.bot.get_cog("Discover")
        base, headers = disc._supabase()
        db, guild = self.bot.db, self.bot.guild
        artists = []
        for uid in pool:
            member = guild.get_member(uid) if guild else None
            m = await db.fetchone("SELECT s.level, mb.manual_level FROM members mb LEFT JOIN scores s "
                                  "ON s.user_id = mb.user_id WHERE mb.user_id=?", (uid,))
            genres = [r["genre"] for r in await db.fetchall(
                "SELECT genre FROM member_genres WHERE user_id=?", (uid,))]
            tracks = []
            for t in await db.fetchall("SELECT * FROM rating_tracks WHERE user_id=? ORDER BY slot", (uid,)):
                cover = t["cover_path"]
                if not cover:
                    thumb = t["thumb_url"]
                    if not thumb:
                        title, thumb = await self.bot.collectors.post_meta(t["platform"], t["url"], t["external_id"])
                        await db.execute("UPDATE rating_tracks SET title=COALESCE(title, ?), thumb_url=? "
                                         "WHERE user_id=? AND slot=?", (title, thumb, uid, t["slot"]))
                    if thumb:
                        try:
                            async with self.bot.session.get(thumb) as r:
                                raw = await r.read() if r.status == 200 else None
                            if raw:
                                img = Image.open(io.BytesIO(raw)).convert("RGB")
                                img.thumbnail((640, 640))
                                buf = io.BytesIO()
                                img.save(buf, "JPEG", quality=85)
                                cover = f"covers/r{uid}_{t['slot']}_{t['external_id'][:20]}.jpg"
                                await disc._upload(base, headers, cover, buf.getvalue(), "image/jpeg")
                                await db.execute("UPDATE rating_tracks SET cover_path=? WHERE user_id=? AND slot=?",
                                                 (cover, uid, t["slot"]))
                        except Exception:  # noqa: BLE001
                            cover = None
                tracks.append({"platform": t["platform"], "platform_name": PLATFORM_NAME[t["platform"]],
                               "url": t["url"], "title": t["title"] or f"Track {t['slot']}", "cover": cover})
            an = await db.fetchone("SELECT artist_name FROM members WHERE user_id=?", (uid,))
            name = (an and an["artist_name"]) or (member.display_name if member else "Artist")
            artists.append({"uid": str(uid), "name": name,
                            "genres": genres, "level": (m and (m["manual_level"] or m["level"])) or 1,
                            "tracks": tracks})
        ch = await self.bot.channel("ratings")
        payload = {"updated_at": now(), "channel_id": str(ch.id) if ch else None, "artists": artists,
                   "criteria": [{"key": k, "name": n, "desc": d, "weight": RATING_WEIGHTS[k]} for k, n, d in CRITERIA]}
        await disc._upload(base, headers, "ratings.json", json.dumps(payload).encode(), "application/json")

    async def notify(self, fresh: list[dict]) -> None:
        for artist in {f["artist"] for f in fresh}:
            user = self.bot.get_user(artist)
            if not user:
                continue
            try:
                await user.send("⭐ You received a new **anonymous rating** on Backline! "
                                f"Check **My results** in {await self.bot.channel_mention('ratings')}.")
            except discord.HTTPException:
                pass

    async def sync(self) -> None:
        if not self.bot.get_cog("Discover"):
            return
        async with self._lock:
            fresh = await self.pull()
            pool = await rating_pool(self.bot.db)
            await self.push_pool(pool)
            await self.publish(pool)
            if fresh:
                await self.notify(fresh)
                part = self.bot.get_cog("Participation")
                if part:
                    await part._recalc()   # o bônus de quem avaliou entra no nível

    @tasks.loop(minutes=5)
    async def sync_loop(self) -> None:
        try:
            await self.sync()
        except Exception as e:  # noqa: BLE001
            if "backline_r" in str(e) or "404" in str(e):
                return   # tabelas ainda não criadas no Supabase
            await self.bot.log_admin(f"⚠️ Ratings sync: `{str(e)[:300]}`")

    @sync_loop.before_loop
    async def _wait(self) -> None:
        await self.bot.wait_until_ready()
        await asyncio.sleep(20)


async def setup(bot):
    await bot.add_cog(Ratings(bot))
