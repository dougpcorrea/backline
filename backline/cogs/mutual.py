"""Mutual Follow (opcional): membros que querem se seguir nas plataformas.

Fluxo:
1. O membro entra no Mutual Follow pelo painel do canal #mutual-follow.
2. "Quem seguir" mostra até 10 perfis (primeiro quem já segue a pessoa).
3. O membro segue nas plataformas e marca no menu quem seguiu.
4. O bot confere:
   - YouTube: pela API oficial (se as inscrições do canal forem públicas);
   - Instagram e TikTok: lendo os "seguindo" mais recentes do perfil (Apify).
5. Quando confirma, avisa a outra pessoa pra seguir de volta. Quando os dois
   se seguem, avisa que virou mútuo.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ..collectors import CollectorError
from ..db import now
from ..i18n import STRINGS, bi, bi_short, lang_of, t
from ..links import PLATFORM_EMOJI, PLATFORM_NAME
from ..services import (add_follow_claims, is_followed_back, mark_follow, mutual_active_ids,
                        mutual_candidates, mutual_stats, pending_follow_claims, profile_url)

STRINGS.update({
    "mf_title": {"en": "🤝 Mutual Follow", "pt": "🤝 Mutual Follow"},
    "mf_desc": {
        "en": "Optional. Join to follow other artists and get followed back.\n"
              "• **Who to follow** shows profiles (people who already follow you come first).\n"
              "• After following, mark them in the menu — the bot checks it "
              "(YouTube: needs public subscriptions; Instagram: public profile).\n"
              "• When someone follows you, you get a DM to follow back.",
        "pt": "Opcional. Participe pra seguir outros artistas e ser seguido de volta.\n"
              "• **Quem seguir** mostra perfis (quem já te segue aparece primeiro).\n"
              "• Depois de seguir, marque no menu — o bot confere "
              "(YouTube: inscrições públicas; Instagram: perfil público).\n"
              "• Quando alguém te segue, você recebe uma mensagem pra seguir de volta.",
    },
    "mf_btn_toggle": {"en": "Join / Leave", "pt": "Entrar / Sair"},
    "mf_btn_who": {"en": "Who to follow", "pt": "Quem seguir"},
    "mf_btn_mine": {"en": "My follows", "pt": "Meus follows"},
    "mf_joined": {"en": "🤝 You joined Mutual Follow! Click **Who to follow** to start.",
                  "pt": "🤝 Você entrou no Mutual Follow! Clique em **Quem seguir** pra começar."},
    "mf_left": {"en": "You left Mutual Follow. Your verified follows stay recorded.",
                "pt": "Você saiu do Mutual Follow. Os follows já confirmados continuam registrados."},
    "mf_not_in": {"en": "Join Mutual Follow first (**Join / Leave** button).",
                  "pt": "Entre no Mutual Follow primeiro (botão **Entrar / Sair**)."},
    "mf_none": {"en": "Nobody new to follow right now. Come back later!",
                "pt": "Ninguém novo pra seguir agora. Volte mais tarde!"},
    "mf_list_head": {"en": "Follow these profiles, then mark below who you followed:",
                     "pt": "Siga estes perfis e depois marque abaixo quem você seguiu:"},
    "mf_follows_you": {"en": "already follows you", "pt": "já te segue"},
    "mf_select": {"en": "I followed…", "pt": "Eu segui…"},
    "mf_claimed": {"en": "✅ Got it! I'll check {n} follow(s) within ~30 min and notify the other side.",
                   "pt": "✅ Anotado! Vou conferir {n} follow(s) em até ~30 min e avisar a outra pessoa."},
    "mf_stats": {
        "en": "**You follow:** {following} · **Follow you:** {followers} · **Mutual:** {mutual} · **Being checked:** {pending}",
        "pt": "**Você segue:** {following} · **Te seguem:** {followers} · **Mútuos:** {mutual} · **Em verificação:** {pending}",
    },
    "mf_to_return": {"en": "**Follow back:**", "pt": "**Siga de volta:**"},
    "dm_mf_followed": {
        "en": "🤝 <@{who}> followed you on {platform}! Follow back: {url}\nThen mark it in **Who to follow** in #mutual-follow.",
        "pt": "🤝 <@{who}> te seguiu no {platform}! Siga de volta: {url}\nDepois marque em **Quem seguir** no #mutual-follow.",
    },
    "dm_mf_mutual": {"en": "🎉 You and <@{who}> now follow each other on {platform}!",
                     "pt": "🎉 Você e <@{who}> agora se seguem no {platform}!"},
    "dm_mf_failed": {
        "en": "⚠️ I couldn't confirm that you follow <@{who}> on {platform}. "
              "Check that you followed {url} and that your profile is public{yt}, then mark it again.",
        "pt": "⚠️ Não consegui confirmar que você segue <@{who}> no {platform}. "
              "Confira se seguiu {url} e se seu perfil é público{yt}, e marque de novo.",
    },
    "yt_public_en": {"en": " (on YouTube, your subscriptions must be public)", "pt": ""},
    "yt_public_pt": {"en": "", "pt": " (no YouTube, suas inscrições precisam estar públicas)"},
})


class MutualPanelView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot
        for key, style, emoji, cid, cb in [
            ("mf_btn_toggle", discord.ButtonStyle.secondary, "🤝", "bl:mf:toggle", self.on_toggle),
            ("mf_btn_who", discord.ButtonStyle.primary, "🔎", "bl:mf:who", self.on_who),
            ("mf_btn_mine", discord.ButtonStyle.secondary, "📋", "bl:mf:mine", self.on_mine),
        ]:
            b = discord.ui.Button(label=bi_short(key), style=style, emoji=emoji, custom_id=cid)
            b.callback = cb
            self.add_item(b)

    @property
    def cog(self) -> "Mutual":
        return self.bot.get_cog("Mutual")

    async def on_toggle(self, interaction: discord.Interaction) -> None:
        await self.cog.toggle(interaction)

    async def on_who(self, interaction: discord.Interaction) -> None:
        await self.cog.who(interaction)

    async def on_mine(self, interaction: discord.Interaction) -> None:
        await self.cog.mine(interaction)


class FollowSelect(discord.ui.Select):
    def __init__(self, cog: "Mutual", lang: str, cands):
        options = []
        for c in cands:
            label = f"{PLATFORM_NAME[c.platform]} · @{c.handle}"[:100]
            options.append(discord.SelectOption(label=label, value=f"{c.user_id}:{c.platform}",
                                                emoji=PLATFORM_EMOJI[c.platform]))
        super().__init__(placeholder=t("mf_select", lang), min_values=1, max_values=len(options), options=options)
        self.cog, self.lang = cog, lang

    async def callback(self, interaction: discord.Interaction) -> None:
        items = []
        for v in self.values:
            uid, platform = v.split(":", 1)
            items.append((int(uid), platform))
        n = await add_follow_claims(self.cog.bot.db, interaction.user.id, items)
        await interaction.response.edit_message(content=t("mf_claimed", self.lang, n=n), embed=None, view=None)


class Mutual(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        bot.add_view(MutualPanelView(bot))
        self._lock = asyncio.Lock()
        self.verify_loop.start()

    def cog_unload(self) -> None:
        self.verify_loop.cancel()

    # ---------- ações do painel ----------
    async def _require_member(self, interaction) -> str | None:
        lang = lang_of(interaction.locale)
        m = await self.bot.db.get_member(interaction.user.id)
        if not m or not m["verified_at"]:
            await interaction.response.send_message(
                t("err_not_verified", lang, channel=await self.bot.channel_mention("start")), ephemeral=True)
            return None
        return lang

    async def toggle(self, interaction: discord.Interaction) -> None:
        lang = await self._require_member(interaction)
        if not lang:
            return
        db, uid = self.bot.db, interaction.user.id
        active = uid in await mutual_active_ids(db)
        role = await self.bot.role("mutual")
        member = interaction.guild.get_member(uid) if interaction.guild else None
        if active:
            await db.execute("UPDATE mutual_members SET active=0 WHERE user_id=?", (uid,))
            if role and member and role in member.roles:
                await member.remove_roles(role, reason="Mutual Follow off")
            await interaction.response.send_message(t("mf_left", lang), ephemeral=True)
        else:
            await db.execute("INSERT INTO mutual_members(user_id, active, joined_at) VALUES(?,1,?) "
                             "ON CONFLICT(user_id) DO UPDATE SET active=1", (uid, now()))
            if role and member and role not in member.roles:
                await member.add_roles(role, reason="Mutual Follow on")
            await interaction.response.send_message(t("mf_joined", lang), ephemeral=True)

    async def who(self, interaction: discord.Interaction) -> None:
        lang = await self._require_member(interaction)
        if not lang:
            return
        if interaction.user.id not in await mutual_active_ids(self.bot.db):
            await interaction.response.send_message(t("mf_not_in", lang), ephemeral=True)
            return
        cands = await mutual_candidates(self.bot.db, interaction.user.id, limit=10)
        if not cands:
            await interaction.response.send_message(t("mf_none", lang), ephemeral=True)
            return
        lines = []
        for c in cands:
            tag = f" — ⭐ {t('mf_follows_you', lang)}" if c.follows_me else ""
            lines.append(f"{PLATFORM_EMOJI[c.platform]} <@{c.user_id}> · [@{c.handle}]"
                         f"({profile_url(c.platform, c.handle, c.external_id)}){tag}")
        embed = discord.Embed(description="\n".join(lines), colour=0x5865F2)
        view = discord.ui.View(timeout=900)
        view.add_item(FollowSelect(self, lang, cands))
        await interaction.response.send_message(t("mf_list_head", lang), embed=embed, view=view, ephemeral=True)

    async def mine(self, interaction: discord.Interaction) -> None:
        lang = await self._require_member(interaction)
        if not lang:
            return
        st = await mutual_stats(self.bot.db, interaction.user.id)
        text = t("mf_stats", lang, following=st.following, followers=st.followers, mutual=st.mutual,
                 pending=st.pending)
        if st.to_return:
            text += "\n\n" + t("mf_to_return", lang) + "\n" + "\n".join(
                f"{PLATFORM_EMOJI[r['platform']]} <@{r['follower_id']}> · "
                f"[@{r['handle']}]({profile_url(r['platform'], r['handle'], r['external_id'])})"
                for r in st.to_return[:15])
        await interaction.response.send_message(text, ephemeral=True)

    @app_commands.command(name="mutual", description="Mutual Follow: who to follow")
    @app_commands.guild_only()
    async def mutual_cmd(self, interaction: discord.Interaction) -> None:
        await self.who(interaction)

    # ---------- verificação ----------
    @tasks.loop(minutes=15)
    async def verify_loop(self) -> None:
        if await self.bot.is_paused():
            return
        async with self._lock:
            await self.verify_pending()

    @verify_loop.before_loop
    async def _wait(self) -> None:
        await self.bot.wait_until_ready()

    async def verify_pending(self) -> int:
        db, cfg, col = self.bot.db, self.bot.cfg, self.bot.collectors
        claims = await pending_follow_claims(db)
        if not claims:
            return 0
        done = 0
        # Instagram/TikTok: uma leitura do "seguindo" por pessoa e plataforma serve pra vários follows
        groups: dict[tuple[int, str], list] = defaultdict(list)
        for c in claims:
            groups[(c["follower_id"], c["platform"])].append(c)
        for (follower_id, platform), items in groups.items():
            try:
                if platform == "youtube":
                    results = {}
                    for c in items:
                        if not c["follower_ext"] or not c["followee_ext"]:
                            results[c["followee_id"]] = None
                            continue
                        results[c["followee_id"]] = await col.youtube_is_subscribed(c["follower_ext"],
                                                                                    c["followee_ext"])
                else:
                    seen = await col.following(platform, items[0]["follower_handle"], cfg.mutual_scan_limit)
                    results = {c["followee_id"]: c["followee_handle"] in seen for c in items}
            except CollectorError as e:
                await self.bot.log_admin(f"⚠️ Mutual Follow: falha ao conferir {platform} de <@{follower_id}>: {e}")
                continue
            for c in items:
                ok = results.get(c["followee_id"])
                if ok:
                    await mark_follow(db, follower_id, c["followee_id"], platform, "verified")
                    await self.on_verified(c)
                    done += 1
                else:
                    failed = c["attempts"] + 1 >= cfg.mutual_max_attempts
                    await mark_follow(db, follower_id, c["followee_id"], platform, "failed" if failed else "pending")
                    if failed:
                        await self.dm(follower_id, "dm_mf_failed", who=c["followee_id"],
                                      platform=PLATFORM_NAME[platform],
                                      url=profile_url(platform, c["followee_handle"], c["followee_ext"]),
                                      yt_platform=platform)
        return done

    async def on_verified(self, c) -> None:
        platform = c["platform"]
        if await is_followed_back(self.bot.db, c["follower_id"], c["followee_id"], platform):
            for a, b in ((c["follower_id"], c["followee_id"]), (c["followee_id"], c["follower_id"])):
                await self.dm(a, "dm_mf_mutual", who=b, platform=PLATFORM_NAME[platform])
        else:
            await self.dm(c["followee_id"], "dm_mf_followed", who=c["follower_id"],
                          platform=PLATFORM_NAME[platform],
                          url=profile_url(platform, c["follower_handle"], c["follower_ext"]))

    async def dm(self, user_id: int, key: str, yt_platform: str | None = None, **kw) -> None:
        guild = self.bot.guild
        member = guild.get_member(user_id) if guild else None
        if not member:
            return
        m = await self.bot.db.get_member(user_id)
        lang = m["locale"] if m else "en"
        if key == "dm_mf_failed":
            kw["yt"] = t(f"yt_public_{lang}", lang) if yt_platform == "youtube" else ""
        try:
            await member.send(t(key, lang, **kw))
        except discord.HTTPException:
            pass

    @staticmethod
    def panel_embed() -> discord.Embed:
        return discord.Embed(title=t("mf_title", "en"), description=bi("mf_desc"), colour=0x30A46C)


async def setup(bot):
    await bot.add_cog(Mutual(bot))
