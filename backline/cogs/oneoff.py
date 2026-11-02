"""Tarefas que rodam uma única vez depois de uma atualização (cada uma marcada em settings)."""
from __future__ import annotations

import asyncio

from discord.ext import commands

from ..db import now
from ..links import parse_post_url

# publicação do Douglas cadastrada como teste no Discover
SEED_POSTS = ["https://www.instagram.com/p/DfK4XMTvbYl/"]


class OneOff(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._done = False

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._done:
            return
        self._done = True
        await asyncio.sleep(3)
        try:
            await self.reset_testers()
            await self.seed_posts()
            await self.refetch_instagram_meta()
            await self.drop_tiktok()
            await self.update_reputation_text()
        except Exception as e:  # noqa: BLE001
            await self.bot.log_admin(f"⚠️ Tarefa única falhou: `{str(e)[:300]}`")

    async def reset_testers(self) -> None:
        """Contas TESTER (ex.: @astolfo0336) voltam a ser 'novo membro' para testar o cadastro."""
        db = self.bot.db
        if await db.get_setting("oneoff_tester_reset_v1"):
            return
        await db.set_setting("oneoff_tester_reset_v1", "1")
        rows = await db.fetchall("SELECT user_id FROM members WHERE is_test=1 AND demo_name IS NULL")
        for r in rows:
            await self.reset_tester(r["user_id"])
            await self.bot.log_admin(f"🧪 <@{r['user_id']}> virou TESTER: cadastro zerado; no Join, perfis fictícios "
                                     "são verificados sem código na bio.")

    async def reset_tester(self, uid: int) -> None:
        """Zera o cadastro de uma conta e marca como TESTER (perfis fictícios verificam sem código)."""
        db, guild = self.bot.db, self.bot.guild
        for sql in ("DELETE FROM handles WHERE user_id=?", "DELETE FROM member_genres WHERE user_id=?",
                    "DELETE FROM scores WHERE user_id=?", "DELETE FROM mutual_members WHERE user_id=?",
                    "DELETE FROM discover_batch WHERE user_id=?"):
            await db.execute(sql, (uid,))
        if not await db.get_member(uid):
            await db.upsert_member(uid, "en")
        await db.execute("UPDATE members SET verified_at=NULL, verify_code=NULL, manual_level=NULL, verify_tried=0, is_test=1 "
                         "WHERE user_id=?", (uid,))
        member = guild.get_member(uid) if guild else None
        if member:
            backline_ids = {int(x["value"]) for x in await db.fetchall(
                "SELECT value FROM settings WHERE key LIKE 'role_%'") if str(x["value"]).isdigit()}
            remove = [ro for ro in member.roles if ro.id in backline_ids]
            if remove:
                try:
                    await member.remove_roles(*remove, reason="Backline tester reset")
                except Exception:  # noqa: BLE001
                    pass

    async def update_reputation_text(self) -> None:
        """Seção 02 do #start-here nova (verificação a cada 30 min + Discover só Lv.3+), também no texto editado."""
        import re
        from ..welcome import DEFAULT_TEXT
        db = self.bot.db
        if await db.get_setting("oneoff_reputation_text_v1"):
            return
        await db.set_setting("oneoff_reputation_text_v1", "1")
        text = await db.get_setting("welcome_text")
        if text:
            new02 = re.search(r"### 🏆 02 — Your Reputation\n.*?(?=### 🤝 03)", DEFAULT_TEXT, flags=re.S).group(0)
            text2 = re.sub(r"###[^\n]*02[^\n]*Reputation[^\n]*\n.*?(?=###[^\n]*03)", lambda m: new02, text, flags=re.S)
            if text2 != text:
                await db.set_setting("welcome_text", text2)
        setup = self.bot.get_cog("ServerSetup")
        if setup and self.bot.guild:
            await setup.refresh_welcome(self.bot.guild)

    async def drop_tiktok(self) -> None:
        """TikTok saiu da plataforma (não dá pra conferir comentários lá)."""
        db = self.bot.db
        if await db.get_setting("oneoff_drop_tiktok_v1"):
            return
        await db.set_setting("oneoff_drop_tiktok_v1", "1")
        await db.execute("DELETE FROM handles WHERE platform='tiktok'")
        await db.execute("DELETE FROM follows WHERE platform='tiktok'")
        await db.execute("UPDATE posts SET removed=1 WHERE platform='tiktok' AND external_id NOT LIKE 'demo-%'")
        await db.execute("UPDATE posts SET platform='youtube' WHERE platform='tiktok' AND external_id LIKE 'demo-%'")
        text = await db.get_setting("welcome_text")
        if text:
            for a, b in (("Instagram, TikTok, and/or YouTube", "Instagram and/or YouTube"),
                         ("Instagram, TikTok and/or YouTube", "Instagram and/or YouTube"),
                         ("across Instagram, TikTok, and YouTube", "on Instagram and YouTube"),
                         ("Instagram, TikTok, and YouTube", "Instagram and YouTube"),
                         ("Instagram, TikTok and YouTube", "Instagram and YouTube")):
                text = text.replace(a, b)
            await db.set_setting("welcome_text", text)
        setup = self.bot.get_cog("ServerSetup")
        if setup and self.bot.guild:
            await setup.refresh_welcome(self.bot.guild)
        part = self.bot.get_cog("Participation")
        if part:
            await part._recalc()
        disc = self.bot.get_cog("Discover")
        if disc:
            await disc.publish_data()
        await self.bot.log_admin("🧹 TikTok removido: perfis e publicações do TikTok saíram; textos atualizados.")

    async def refetch_instagram_meta(self) -> None:
        """Posts do Instagram cadastrados antes da busca de capa/título: buscar agora."""
        db = self.bot.db
        if await db.get_setting("oneoff_ig_meta_v1"):
            return
        await db.set_setting("oneoff_ig_meta_v1", "1")
        await db.execute("UPDATE posts SET meta_at=NULL WHERE platform='instagram' AND thumb_url IS NULL "
                         "AND external_id NOT LIKE 'demo-%'")
        disc = self.bot.get_cog("Discover")
        if disc:
            await disc.publish_data()

    async def seed_posts(self) -> None:
        db = self.bot.db
        owner = await db.fetchone(
            "SELECT h.user_id FROM handles h JOIN members m ON m.user_id=h.user_id "
            "WHERE h.platform='instagram' AND h.verified=1 AND m.is_test=0 ORDER BY m.verified_at LIMIT 1")
        if not owner:
            return
        added = 0
        for url in SEED_POSTS:
            link = parse_post_url(url)
            if not link or await db.fetchone("SELECT id FROM posts WHERE platform=? AND external_id=?",
                                             (link.platform, link.external_id)):
                continue
            await db.execute("INSERT INTO posts(user_id, platform, url, external_id, created_at) VALUES(?,?,?,?,?)",
                             (owner["user_id"], link.platform, link.url, link.external_id, now()))
            added += 1
        if added:
            disc = self.bot.get_cog("Discover")
            if disc:
                await disc.publish_data()
            await self.bot.log_admin(f"📤 {added} publicação de teste cadastrada para <@{owner['user_id']}> "
                                     "(já está no Discover).")


async def setup(bot):
    await bot.add_cog(OneOff(bot))
