"""Painel administrativo: comandos /admin (só para administradores)."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ..db import now
from ..links import PLATFORM_NAME

Platform = Literal["instagram", "tiktok", "youtube"]


class WelcomeModal(discord.ui.Modal, title="Texto do #start-here"):
    """O envio é tratado em Admin.on_interaction (pelo custom_id), não aqui: assim funciona
    mesmo se o bot reiniciar (atualização) enquanto a janela está aberta."""

    def __init__(self, current: str):
        super().__init__(timeout=None, custom_id="bl:welcome_modal")
        self.text = discord.ui.TextInput(
            label="Texto (Markdown do Discord)", custom_id="bl:welcome_text",
            placeholder="{post} {discover} {mutual} viram links dos canais",
            style=discord.TextStyle.paragraph, default=current[:4000], max_length=4000, required=False)
        self.add_item(self.text)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        return  # tratado em Admin.on_interaction


class Admin(commands.Cog):
    admin = app_commands.Group(name="admin", description="Backline admin",
                               default_permissions=discord.Permissions(administrator=True), guild_only=True)

    def __init__(self, bot):
        self.bot = bot
        self.auto_update.start()

    def cog_unload(self) -> None:
        self.auto_update.cancel()

    @property
    def part(self):
        return self.bot.get_cog("Participation")

    @admin.command(name="status", description="Resumo do sistema")
    async def status(self, interaction: discord.Interaction) -> None:
        db, cfg = self.bot.db, self.bot.cfg
        one = lambda sql, p=(): db.fetchone(sql, p)  # noqa: E731
        members = (await one("SELECT COUNT(*) n FROM members WHERE verified_at IS NOT NULL AND left_at IS NULL"))["n"]
        pending = (await one("SELECT COUNT(*) n FROM members WHERE verified_at IS NULL AND left_at IS NULL"))["n"]
        posts = (await one("SELECT COUNT(*) n FROM posts WHERE removed=0 AND created_at>=?",
                           (now() - cfg.window_days * 86400,)))["n"]
        comments = (await one("SELECT COUNT(*) n FROM comments"))["n"]
        recruits = (await one("SELECT COUNT(*) n FROM recruits WHERE left_at IS NULL"))["n"]
        day_ago = now() - 86400
        ok = (await one("SELECT COUNT(*) n FROM collection_log WHERE at>=? AND ok=1", (day_ago,)))["n"]
        fail = (await one("SELECT COUNT(*) n FROM collection_log WHERE at>=? AND ok=0", (day_ago,)))["n"]
        last_err = await one("SELECT * FROM collection_log WHERE ok=0 ORDER BY at DESC LIMIT 1")

        lines = [
            f"**Estado:** {'⏸️ pausado' if await self.bot.is_paused() else '▶️ rodando'}",
            f"**Membros verificados:** {members} · aguardando verificação: {pending}",
            f"**Publicações na janela ({cfg.window_days}d):** {posts}",
            f"**Comentários coletados (total):** {comments}",
            f"**Recrutados ativos no servidor:** {recruits}",
            f"**Coletas nas últimas 24h:** {ok} ok · {fail} com erro",
            f"**YouTube API:** {'✅' if cfg.youtube_api_key else '❌ sem chave'} · "
            f"**Apify:** {'✅' if cfg.apify_token else '❌ sem token'}",
        ]
        if last_err:
            lines.append(f"**Último erro:** <t:{last_err['at']}:R> publicação #{last_err['post_id']}: "
                         f"`{(last_err['error'] or '')[:300]}`")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @admin.command(name="pause", description="Pausar coleta e atualização de cargos")
    async def pause(self, interaction: discord.Interaction) -> None:
        await self.bot.db.set_setting("paused", "1")
        await interaction.response.send_message("⏸️ Pausado. Os cargos atuais ficam congelados.", ephemeral=True)

    @admin.command(name="resume", description="Retomar coleta e atualização de cargos")
    async def resume(self, interaction: discord.Interaction) -> None:
        await self.bot.db.set_setting("paused", "0")
        await interaction.response.send_message("▶️ Retomado.", ephemeral=True)

    @admin.command(name="recalc", description="Recalcular índices, cargos e ranking agora")
    async def recalc(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        changed = await self.part.recalc()
        await interaction.followup.send(f"✅ Recalculado. {changed} membro(s) mudaram de nível.", ephemeral=True)

    @admin.command(name="collect", description="Coletar comentários agora (uma publicação ou todas da semana)")
    @app_commands.describe(post_id="Número da publicação (aparece no rodapé do cartão). Vazio = últimos 7 dias")
    async def collect(self, interaction: discord.Interaction, post_id: int | None = None) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        db = self.bot.db
        if post_id:
            posts = await db.fetchall("SELECT * FROM posts WHERE id=? AND removed=0", (post_id,))
        else:
            posts = await db.fetchall("SELECT * FROM posts WHERE removed=0 AND created_at>=?",
                                      (now() - 7 * 86400,))
        if not posts:
            await interaction.followup.send("Nenhuma publicação encontrada.", ephemeral=True)
            return
        found = 0
        for p in posts:
            found += await self.part.collect_post(p, advance_checkpoint=False)
        await self.part.recalc()
        await interaction.followup.send(
            f"✅ {len(posts)} publicação(ões) coletada(s). {found} novo(s) comentário(s) de membros.", ephemeral=True)

    @admin.command(name="remove-post", description="Remover uma publicação do feed e do cálculo")
    async def remove_post(self, interaction: discord.Interaction, post_id: int) -> None:
        db = self.bot.db
        if not await db.fetchone("SELECT id FROM posts WHERE id=?", (post_id,)):
            await interaction.response.send_message("Publicação não encontrada.", ephemeral=True)
            return
        await db.execute("UPDATE posts SET removed=1 WHERE id=?", (post_id,))
        feed = self.bot.get_cog("Feed")
        if feed:
            await feed.refresh_card(post_id)
        await interaction.response.send_message(f"🗑️ Publicação #{post_id} removida.", ephemeral=True)

    @admin.command(name="member", description="Ver dados de um membro")
    async def member_info(self, interaction: discord.Interaction, member: discord.Member) -> None:
        db = self.bot.db
        m = await db.get_member(member.id)
        if not m:
            await interaction.response.send_message("Esse membro ainda não começou a entrada.", ephemeral=True)
            return
        handles = await db.handles_of(member.id)
        s = await db.fetchone("SELECT * FROM scores WHERE user_id=?", (member.id,))
        rec = await db.fetchone("SELECT * FROM recruits WHERE user_id=?", (member.id,))
        recruited = await db.fetchone("SELECT COUNT(*) n FROM recruits WHERE inviter_id=?", (member.id,))
        verified = f"<t:{m['verified_at']}:R>" if m["verified_at"] else "não"
        lines = [
            f"**{member.mention}** · idioma `{m['locale']}` · código `{m['verify_code']}`",
            f"Verificado: {verified}",
            *[f"{'✅' if h['verified'] else '⏳'} {PLATFORM_NAME[h['platform']]}: @{h['handle']}" for h in handles],
            f"Índice: Lv.{s['level']} · dados {s['given']} · recebidos {s['received']}" if s else "Índice: —",
            f"Recrutado por: <@{rec['inviter_id']}>" if rec else "Recrutado por: —",
            f"Recrutou: {recruited['n']}",
        ]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @admin.command(name="verify", description="Marcar um perfil como verificado manualmente")
    async def force_verify(self, interaction: discord.Interaction, member: discord.Member, platform: Platform) -> None:
        db = self.bot.db
        h = await db.fetchone("SELECT * FROM handles WHERE user_id=? AND platform=?", (member.id, platform))
        if not h:
            await interaction.response.send_message("Esse membro não informou esse perfil.", ephemeral=True)
            return
        owner = await db.handle_owner(platform, h["handle"])
        if owner and owner != member.id:
            await interaction.response.send_message(f"⚠️ @{h['handle']} já é de <@{owner}>.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        external_id = None
        if platform == "youtube":
            try:
                prof = await self.bot.collectors.youtube_profile(h["handle"])
                external_id = prof.external_id if prof else None
            except Exception:  # noqa: BLE001
                pass
        await db.execute("UPDATE handles SET verified=1, external_id=COALESCE(?, external_id) "
                         "WHERE user_id=? AND platform=?", (external_id, member.id, platform))
        await db.execute("UPDATE members SET verified_at=COALESCE(verified_at, ?) WHERE user_id=?",
                         (now(), member.id))
        from .onboarding import grant_member_roles
        await grant_member_roles(self.bot, member)
        await interaction.followup.send(f"✅ {PLATFORM_NAME[platform]} de {member.mention} verificado.",
                                        ephemeral=True)

    @admin.command(name="bypass", description="Liberar um membro sem verificar perfis (ex.: conta de teste)")
    @app_commands.describe(level="Nível fixo de 1 a 10 (vazio = calculado normalmente)",
                           genres="Gêneros separados por vírgula, ex.: Latin, Pop",
                           recruiter="Dar também o cargo de Recruiter",
                           mutual="Colocar no Mutual Follow")
    async def bypass(self, interaction: discord.Interaction, member: discord.Member,
                     level: app_commands.Range[int, 1, 10] | None = None, genres: str | None = None,
                     recruiter: bool = False, mutual: bool = False) -> None:
        await interaction.response.defer(ephemeral=True)
        db, bot = self.bot.db, self.bot
        await db.upsert_member(member.id, "en")
        await db.execute("UPDATE members SET verified_at=COALESCE(verified_at, ?), is_test=1, manual_level=? "
                         "WHERE user_id=?", (now(), level, member.id))
        from .onboarding import grant_member_roles, sync_genre_roles
        await grant_member_roles(bot, member)
        notes = []
        if genres:
            wanted = [g.strip() for g in genres.split(",") if g.strip()]
            valid = [g for g in bot.cfg.genres if g.lower() in {w.lower() for w in wanted}]
            await db.execute("DELETE FROM member_genres WHERE user_id=?", (member.id,))
            for g in valid:
                await db.execute("INSERT INTO member_genres(user_id, genre) VALUES(?,?) ON CONFLICT DO NOTHING",
                                 (member.id, g))
            await sync_genre_roles(bot, member, valid)
            notes.append("gêneros: " + (", ".join(valid) or "nenhum válido"))
        if level:
            part = bot.get_cog("Participation")
            await part.sync_level_roles({member.id: level})
            notes.append(f"nível fixo Lv.{level}")
        if recruiter:
            role = await bot.role("recruiter")
            if role:
                await member.add_roles(role, reason="Backline bypass")
                notes.append("Recruiter")
        if mutual:
            await db.execute("INSERT INTO mutual_members(user_id, active, joined_at) VALUES(?,1,?) "
                             "ON CONFLICT(user_id) DO UPDATE SET active=1", (member.id, now()))
            role = await bot.role("mutual")
            if role:
                await member.add_roles(role, reason="Backline bypass")
            notes.append("Mutual Follow")
        await interaction.followup.send(f"✅ {member.mention} liberado sem verificação ({'; '.join(notes) or 'só membro'}).\n"
                                        f"Gêneros válidos: {', '.join(bot.cfg.genres)}", ephemeral=True)

    @admin.command(name="update", description="Instalar agora a versão mais recente e reiniciar o bot")
    @app_commands.describe(source="github (padrão) ou supabase (pacote antigo)")
    async def update(self, interaction: discord.Interaction,
                     source: Literal["github", "supabase"] = "github") -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            if source == "github":
                msg = await self._github_update(force=True)
                if msg is None:
                    await interaction.followup.send("✅ O bot já está na versão mais recente do GitHub.", ephemeral=True)
                    return
            else:
                msg = await self._install_package()
        except Exception as e:  # noqa: BLE001
            await interaction.followup.send(f"❌ Atualização falhou: `{str(e)[:400]}`", ephemeral=True)
            return
        await interaction.followup.send(msg + "\n🔄 Reiniciando o bot (uns 15 segundos)…", ephemeral=True)
        await self.bot.log_admin(f"🔄 Bot atualizado por {interaction.user.mention}. {msg}")
        asyncio.get_running_loop().call_later(2, lambda: os.execv(sys.executable,
                                                                  [sys.executable, "-m", "backline.bot"]))

    async def _install_package(self) -> str:
        cfg = json.load(open(self.bot.cfg.supabase_config))
        url, key = cfg["supabaseUrl"].rstrip("/"), cfg["supabaseChave"]
        headers = {"apikey": key, "Authorization": f"Bearer {key}"}
        async with self.bot.session.get(f"{url}/rest/v1/backline_arquivos?id=eq.pacote&select=dados,atualizado_em",
                                        headers=headers) as r:
            rows = await r.json()
        if not rows:
            raise RuntimeError("nenhum pacote publicado no Supabase")
        await self._install_bytes(base64.b64decode(rows[0]["dados"]))
        return f"✅ Código atualizado (pacote de {rows[0]['atualizado_em'][:16].replace('T', ' ')} UTC, md5 {hashlib.md5(rows[0]['dados'].encode()).hexdigest()[:8]})."

    async def _install_bytes(self, raw: bytes) -> None:
        """Instala um tar (gz/xz) cujo conteúdo é a pasta do projeto (backline/, deploy/, requirements.txt)."""
        app_dir = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as tmp:
            with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
                tar.extractall(tmp, filter="data")
            src = Path(tmp)
            # desce até a pasta que contém requirements.txt (pacote do Supabase ou tarball do GitHub)
            while not (src / "requirements.txt").exists():
                subdirs = [d for d in src.iterdir() if d.is_dir()]
                if len(subdirs) != 1:
                    raise RuntimeError("pacote sem requirements.txt")
                src = subdirs[0]
            for item in src.iterdir():
                if item.name in (".env", "log", ".git", "tests") or item.name.endswith((".db", ".db-wal", ".db-shm")):
                    continue
                dest = app_dir / item.name
                if item.is_dir():
                    shutil.rmtree(dest, ignore_errors=True)
                    shutil.copytree(item, dest)
                else:
                    shutil.copy2(item, dest)
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "pip", "install", "--user", "--break-system-packages", "-q",
            "-r", str(app_dir / "requirements.txt"),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=600)
        if proc.returncode:
            raise RuntimeError("pip: " + out.decode(errors="ignore")[-300:])

    # ---------- atualização automática pelo GitHub ----------
    async def _github_head(self, repo: str, branch: str) -> dict | None:
        """Último commit do repositório. Usa ETag: resposta 304 (nada novo) não gasta o limite do GitHub,
        então dá pra conferir a cada 30 segundos."""
        headers = {"Accept": "application/vnd.github+json"}
        etag = getattr(self, "_etag", None)
        if etag:
            headers["If-None-Match"] = etag
        async with self.bot.session.get(f"https://api.github.com/repos/{repo}/commits/{branch}",
                                        headers=headers) as r:
            if r.status == 304:
                return self._head_cache
            if r.status != 200:
                return None
            self._etag = r.headers.get("ETag")
            self._head_cache = await r.json()
            return self._head_cache

    async def _github_update(self, force: bool = False) -> str | None:
        """Instala a versão mais nova do GitHub. Retorna a mensagem, ou None se não havia nada novo."""
        repo = await self.bot.db.get_setting("update_repo") or self.bot.cfg.update_repo
        if not repo or repo == "off":
            raise RuntimeError("atualização pelo GitHub desligada")
        head = await self._github_head(repo, self.bot.cfg.update_branch)
        if not head:
            raise RuntimeError(f"não consegui ler o repositório {repo}")
        sha = head["sha"]
        installed = await self.bot.db.get_setting("installed_sha")
        if installed is None and not force:
            # primeira vez: só registra a versão atual (o código já veio pelo Supabase)
            await self.bot.db.set_setting("installed_sha", sha)
            return None
        if sha == installed:
            return None
        async with self.bot.session.get(f"https://codeload.github.com/{repo}/tar.gz/{sha}") as r:
            r.raise_for_status()
            raw = await r.read()
        await self._install_bytes(raw)
        await self.bot.db.set_setting("installed_sha", sha)
        msg = head.get("commit", {}).get("message", "").splitlines()[0][:200]
        return f"✅ Versão `{sha[:7]}` instalada: {msg}"

    @tasks.loop(seconds=30)
    async def auto_update(self) -> None:
        try:
            msg = await self._github_update()
        except Exception as e:  # noqa: BLE001
            if "não consegui ler" not in str(e) and "desligada" not in str(e):
                await self.bot.log_admin(f"⚠️ Atualização automática falhou: `{str(e)[:300]}`")
            return
        if not msg:
            return
        await self.bot.log_admin(f"🔄 Atualização automática. {msg}")
        asyncio.get_running_loop().call_later(2, lambda: os.execv(sys.executable,
                                                                  [sys.executable, "-m", "backline.bot"]))

    @auto_update.before_loop
    async def _wait_ready(self) -> None:
        await self.bot.wait_until_ready()

    @admin.command(name="tester-reset", description="Zerar o cadastro de uma conta TESTER (padrão: Astolfo)")
    @app_commands.describe(member="Conta a zerar e marcar como TESTER (vazio = todas as contas TESTER)")
    async def tester_reset(self, interaction: discord.Interaction, member: discord.Member | None = None) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        oneoff = self.bot.get_cog("OneOff")
        if member:
            ids = [member.id]
        else:
            ids = [r["user_id"] for r in await self.bot.db.fetchall(
                "SELECT user_id FROM members WHERE is_test=1 AND demo_name IS NULL")]
        for uid in ids:
            await oneoff.reset_tester(uid)
        names = ", ".join(f"<@{i}>" for i in ids) or "nenhuma conta TESTER"
        await interaction.followup.send(f"🧪 Cadastro zerado: {names}. Pode testar o **Join** de novo "
                                        "(perfis fictícios verificam na hora).", ephemeral=True)

    @admin.command(name="welcome", description="Editar o texto do #start-here")
    async def welcome(self, interaction: discord.Interaction) -> None:
        from ..welcome import DEFAULT_TEXT
        current = await self.bot.db.get_setting("welcome_text") or DEFAULT_TEXT
        await interaction.response.send_modal(WelcomeModal(current))

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.type != discord.InteractionType.modal_submit:
            return
        if (interaction.data or {}).get("custom_id") != "bl:welcome_modal":
            return
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Só administradores.", ephemeral=True)
            return
        value = ""
        for row in interaction.data.get("components", []):
            for comp in row.get("components", []) or ([row.get("component")] if row.get("component") else []):
                if comp and comp.get("custom_id") == "bl:welcome_text":
                    value = (comp.get("value") or "").strip()
        await interaction.response.defer(ephemeral=True, thinking=True)
        if value:
            await self.bot.db.set_setting("welcome_text", value)
            note = "✅ Texto do #start-here atualizado."
        else:
            await self.bot.db.execute("DELETE FROM settings WHERE key='welcome_text'")
            note = "↩️ Campo vazio: voltei para o texto padrão."
        setup = self.bot.get_cog("ServerSetup")
        try:
            if setup and interaction.guild:
                await setup.refresh_welcome(interaction.guild)
        except Exception as e:  # noqa: BLE001
            note += f"\n⚠️ Não consegui redesenhar o painel: `{str(e)[:200]}`"
        await interaction.followup.send(note, ephemeral=True)

    @admin.command(name="unverify", description="Tirar a verificação de um perfil")
    async def unverify(self, interaction: discord.Interaction, member: discord.Member, platform: Platform) -> None:
        await self.bot.db.execute("UPDATE handles SET verified=0 WHERE user_id=? AND platform=?",
                                  (member.id, platform))
        await interaction.response.send_message(
            f"↩️ {PLATFORM_NAME[platform]} de {member.mention} não está mais verificado.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(Admin(bot))
