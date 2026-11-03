"""Entrada na comunidade: perfis, gêneros e verificação (código na bio em stand-by: /admin bio-check)."""
from __future__ import annotations

import secrets

import discord
from discord.ext import commands

from ..collectors import CollectorError
from ..db import now
from ..i18n import t
from ..links import PLATFORM_NAME, PLATFORMS, normalize_handle

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def new_code() -> str:
    return "BL-" + "".join(secrets.choice(CODE_ALPHABET) for _ in range(5))


class JoinView(discord.ui.View):
    """Painel fixo do canal de entrada: Join (novos) e My profiles (status)."""

    def __init__(self, bot, links: list[tuple[str, str, str]] | None = None):
        super().__init__(timeout=None)
        self.bot = bot
        join = discord.ui.Button(label="Join", style=discord.ButtonStyle.success, emoji="🎸", custom_id="bl:join")
        join.callback = self.on_join
        mine = discord.ui.Button(label="My profiles", style=discord.ButtonStyle.secondary, emoji="👤",
                                 custom_id="bl:profiles")
        mine.callback = self.on_profiles
        self.add_item(join)
        self.add_item(mine)
        # atalhos para os canais (botões de link não precisam de custom_id)
        for label, emoji, url in links or []:
            self.add_item(discord.ui.Button(label=label, emoji=emoji, url=url, row=1))

    async def on_join(self, interaction: discord.Interaction) -> None:
        member = await self.bot.db.get_member(interaction.user.id)
        if member and member["verified_at"]:
            # Já é membro: em vez do formulário, mostra o status com o botão de atualizar
            embed, view = await status_card(self.bot, interaction.user.id,
                                            "You're already a member. Use **Update profiles** to change them.")
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            return
        await open_profiles_modal(self.bot, interaction)

    async def on_profiles(self, interaction: discord.Interaction) -> None:
        if not await self.bot.db.get_member(interaction.user.id) or \
                not await self.bot.db.handles_of(interaction.user.id):
            await interaction.response.send_message(t("start_first", "en"), ephemeral=True)
            return
        embed, view = await status_card(self.bot, interaction.user.id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


async def open_profiles_modal(bot, interaction: discord.Interaction) -> None:
    await bot.db.upsert_member(interaction.user.id, "en")
    current = {h["platform"]: h["handle"] for h in await bot.db.handles_of(interaction.user.id)}
    await interaction.response.send_modal(ProfilesModal(bot, "en", current))


STATUS_ICON = {True: "✅", False: "⏳"}


async def status_card(bot, user_id: int, header: str | None = None) -> tuple[discord.Embed, discord.ui.View]:
    """Cartão com cada perfil, situação da verificação e os botões de ação."""
    db = bot.db
    member = await db.get_member(user_id)
    handles = await db.handles_of(user_id)
    genres = [r["genre"] for r in await db.fetchall("SELECT genre FROM member_genres WHERE user_id=?", (user_id,))]
    pending = [h for h in handles if not h["verified"]]
    verified_any = any(h["verified"] for h in handles)
    tried = bool(member and member["verify_tried"])
    verb = "Verify again" if tried else "Verify"

    lines = []
    for h in handles:
        state = "verified" if h["verified"] else "not verified yet"
        lines.append(f"{STATUS_ICON[bool(h['verified'])]} **{PLATFORM_NAME[h['platform']]}** · @{h['handle']} — {state}")
    desc = (header + "\n\n" if header else "") + ("\n".join(lines) or "No profiles yet.")
    bio = await bio_check_on(bot)
    if pending and member and member["verify_code"] and bio:
        desc += (f"\n\n**To verify the ⏳ profiles:** add this code anywhere in their bio, save, "
                 f"then click **{verb}**:\n# `{member['verify_code']}`")
    elif pending and not bio:
        desc += f"\n\nTap **{verb}** to confirm the ⏳ profiles."
    elif verified_any and bio:
        desc += "\n\nYou can remove the code from your bios."
    colour = 0x30A46C if not pending else (0xC9A227 if verified_any else 0xE5484D)
    embed = discord.Embed(title="👤 Your Backline profiles", description=desc, colour=colour)
    embed.add_field(name="Genres", value=" · ".join(genres) or "—", inline=False)
    embed.set_footer(text="Member ✅" if member and member["verified_at"] else "Not a member yet — verify at least one profile")
    return embed, StatusView(bot, show_verify=bool(pending), verify_label=verb)


class StatusView(discord.ui.View):
    def __init__(self, bot, show_verify: bool = True, verify_label: str = "Verify"):
        super().__init__(timeout=None)
        self.bot = bot
        if show_verify:
            v = discord.ui.Button(label=verify_label, style=discord.ButtonStyle.primary,
                                  emoji="🔄" if verify_label != "Verify" else "✅", custom_id="bl:verify")
            v.callback = self.on_verify
            self.add_item(v)
        u = discord.ui.Button(label="Update profiles", style=discord.ButtonStyle.secondary, emoji="✏️",
                              custom_id="bl:update")
        u.callback = self.on_update
        g = discord.ui.Button(label="Genres", style=discord.ButtonStyle.secondary, emoji="🎵", custom_id="bl:genres")
        g.callback = self.on_genres
        self.add_item(u)
        self.add_item(g)

    async def on_verify(self, interaction: discord.Interaction) -> None:
        await run_verification(self.bot, interaction)

    async def on_update(self, interaction: discord.Interaction) -> None:
        await open_profiles_modal(self.bot, interaction)

    async def on_genres(self, interaction: discord.Interaction) -> None:
        text, view = await genre_picker(self.bot, interaction.user.id)
        await interaction.response.send_message(text, view=view, ephemeral=True)


class ProfilesModal(discord.ui.Modal):
    def __init__(self, bot, lang: str, current: dict[str, str]):
        super().__init__(title=t("modal_profiles", lang), timeout=600)
        self.bot, self.lang = bot, lang
        self.inputs: dict[str, discord.ui.TextInput] = {}
        for platform in PLATFORMS:
            field = discord.ui.TextInput(label=t(f"field_{platform}", lang), required=False, max_length=100,
                                         default=current.get(platform), placeholder="@yourname")
            self.inputs[platform] = field
            self.add_item(field)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        db, lang, uid = self.bot.db, self.lang, interaction.user.id
        wanted: dict[str, str | None] = {}
        for platform, field in self.inputs.items():
            raw = field.value.strip()
            handle = normalize_handle(raw)
            if raw and not handle:
                await interaction.response.send_message(
                    t("err_invalid_handle", lang, platform=PLATFORM_NAME[platform], value=raw), ephemeral=True)
                return
            if handle:
                owner = await db.handle_owner(platform, handle)
                if owner and owner != uid:
                    await interaction.response.send_message(
                        t("err_handle_taken", lang, handle=handle, platform=PLATFORM_NAME[platform]),
                        ephemeral=True)
                    return
            wanted[platform] = handle
        if not any(wanted.values()):
            await interaction.response.send_message(t("err_no_handle", lang), ephemeral=True)
            return

        existing = {h["platform"]: h for h in await db.handles_of(uid)}
        for platform, handle in wanted.items():
            old = existing.get(platform)
            if handle is None:
                if old:
                    await db.execute("DELETE FROM handles WHERE user_id=? AND platform=?", (uid, platform))
            elif not old or old["handle"] != handle:
                # @ novo ou trocado: precisa verificar de novo
                await db.execute(
                    "INSERT INTO handles(user_id, platform, handle, external_id, verified) VALUES(?,?,?,NULL,0) "
                    "ON CONFLICT(user_id, platform) DO UPDATE SET handle=excluded.handle, "
                    "external_id=NULL, verified=0",
                    (uid, platform, handle))

        member = await db.get_member(uid)
        if not member["verify_code"]:
            await db.execute("UPDATE members SET verify_code=?, verify_tried=0 WHERE user_id=?", (new_code(), uid))

        text, view = await genre_picker(self.bot, uid, lang, step=True)
        await interaction.response.send_message(text, view=view, ephemeral=True)


MAX_GENRES = 3


class GenreView(discord.ui.View):
    """Um botão por gênero. Cada clique liga/desliga e já salva no banco (funciona mesmo se o bot reiniciar:
    os cliques são tratados em Onboarding.on_interaction pelo custom_id). Verde = escolhido."""

    def __init__(self, bot, lang: str, user_id: int, chosen: list[str] | None = None, step: bool = False):
        super().__init__(timeout=None)
        self.bot, self.lang, self.user_id = bot, lang, user_id
        self.chosen: list[str] = list(chosen or [])
        self.step = step
        for i, g in enumerate(self.bot.cfg.genres[:20]):
            on = g in self.chosen
            self.add_item(discord.ui.Button(
                label=g, style=discord.ButtonStyle.success if on else discord.ButtonStyle.secondary,
                emoji="✅" if on else None, row=i // 5, custom_id=f"bl:genre:{i}"))
        self.add_item(discord.ui.Button(label=f"Done ({len(self.chosen)}/{MAX_GENRES})",
                                        style=discord.ButtonStyle.primary, emoji="💾", disabled=not self.chosen,
                                        row=4, custom_id="bl:genre_done"))

    def _text(self) -> str:
        prefix = "**Step 2 of 3**\n" if self.step else ""
        if not self.chosen:
            return prefix + f"🎵 **Pick up to {MAX_GENRES} genres** that describe your music, then press **Done**."
        return (prefix + f"🎵 **Your genres:** {' · '.join(self.chosen)}\n"
                f"Tap a genre to add or remove it (up to {MAX_GENRES}), then press **Done**.")


async def _member_genres(bot, uid: int) -> list[str]:
    return [r["genre"] for r in await bot.db.fetchall("SELECT genre FROM member_genres WHERE user_id=?", (uid,))
            if r["genre"] in bot.cfg.genres]


async def handle_genre_click(bot, interaction: discord.Interaction, custom_id: str) -> None:
    db, uid = bot.db, interaction.user.id
    chosen = await _member_genres(bot, uid)
    step = "Step 2 of 3" in (interaction.message.content or "") if interaction.message else False
    if custom_id == "bl:genre_done":
        await sync_genre_roles(bot, interaction.user, chosen)
        pending = [h for h in await db.handles_of(uid) if not h["verified"]]
        if pending and not await bio_check_on(bot):
            await interaction.response.edit_message(content="🔎 Confirming your profiles… one moment.",
                                                    embed=None, view=None)
            notes = await verify_pending(bot, interaction.user)
            embed, view = await status_card(bot, uid, "✅ Genres saved.\n" + "\n".join(notes))
            await interaction.edit_original_response(content=None, embed=embed, view=view)
            return
        header = ("✅ Genres saved." if not pending else "**Last step:** prove these profiles are yours.")
        embed, view = await status_card(bot, uid, header)
        await interaction.response.edit_message(content=None, embed=embed, view=view)
        return
    idx = int(custom_id.rsplit(":", 1)[1])
    if idx >= len(bot.cfg.genres):
        return
    genre = bot.cfg.genres[idx]
    if genre in chosen:
        await db.execute("DELETE FROM member_genres WHERE user_id=? AND genre=?", (uid, genre))
        chosen.remove(genre)
    else:
        if len(chosen) >= MAX_GENRES:   # já tem 3: tira o primeiro para entrar o novo
            await db.execute("DELETE FROM member_genres WHERE user_id=? AND genre=?", (uid, chosen[0]))
            chosen = chosen[1:]
        await db.execute("INSERT INTO member_genres(user_id, genre) VALUES(?,?) ON CONFLICT DO NOTHING", (uid, genre))
        chosen.append(genre)
    view = GenreView(bot, "en", uid, chosen, step=step)
    await interaction.response.edit_message(content=view._text(), embed=None, view=view)


async def genre_picker(bot, user_id: int, lang: str = "en", step: bool = False) -> tuple[str, "GenreView"]:
    view = GenreView(bot, lang, user_id, await _member_genres(bot, user_id), step=step)
    return view._text(), view


async def sync_genre_roles(bot, user: discord.abc.User, chosen: list[str]) -> None:
    guild = bot.guild
    member = guild.get_member(user.id) if guild else None
    if not member:
        return
    add, remove = [], []
    for g in bot.cfg.genres:
        role = await bot.role(f"genre_{g}")
        if not role:
            continue
        if g in chosen and role not in member.roles:
            add.append(role)
        elif g not in chosen and role in member.roles:
            remove.append(role)
    try:
        if add:
            await member.add_roles(*add, reason="Backline genres")
        if remove:
            await member.remove_roles(*remove, reason="Backline genres")
    except discord.HTTPException:
        pass


async def bio_check_on(bot) -> bool:
    """Verificação por código na bio. Em stand-by (desligada) enquanto a comunidade é pequena: /admin bio-check."""
    return (await bot.db.get_setting("bio_check", "0")) == "1"


async def verify_pending(bot, user: discord.abc.User) -> list[str]:
    """Verifica os perfis pendentes. Com o código na bio desligado, basta o perfil existir e não ser de outro membro."""
    db, uid = bot.db, user.id
    member = await db.get_member(uid)
    bio = await bio_check_on(bot)
    code = (member["verify_code"] or "").upper()
    await db.execute("UPDATE members SET verify_tried=1 WHERE user_id=?", (uid,))
    ok, missing, failed, taken = [], [], [], []
    # contas TESTER (is_test=1, sem demo_name): perfis fictícios valem sem conferir nada
    tester = bool(member["is_test"]) and not member["demo_name"]
    for h in await db.handles_of(uid):
        if h["verified"]:
            continue
        platform, handle = h["platform"], h["handle"]
        if tester:
            await db.execute("UPDATE handles SET verified=1 WHERE user_id=? AND platform=?", (uid, platform))
            ok.append(PLATFORM_NAME[platform])
            continue
        owner = await db.handle_owner(platform, handle)
        if owner and owner != uid:
            taken.append(f"{PLATFORM_NAME[platform]} @{handle}")
            continue
        try:
            profile = await bot.collectors.profile(platform, handle)
        except CollectorError as e:
            await bot.log_admin(f"⚠️ Verificação {platform} @{handle} falhou: {e}")
            if bio:
                failed.append(PLATFORM_NAME[platform])
                continue
            profile = None   # sem bio: não trava a entrada por causa de instabilidade da API
            await db.execute("UPDATE handles SET verified=1 WHERE user_id=? AND platform=?", (uid, platform))
            ok.append(PLATFORM_NAME[platform])
            continue
        if profile and (not bio or code in (profile.bio or "").upper()):
            await db.execute("UPDATE handles SET verified=1, external_id=? WHERE user_id=? AND platform=?",
                             (profile.external_id, uid, platform))
            ok.append(PLATFORM_NAME[platform])
        else:
            missing.append(PLATFORM_NAME[platform])

    notes = []
    if ok:
        first_time = member["verified_at"] is None
        if first_time:
            await db.execute("UPDATE members SET verified_at=? WHERE user_id=?", (now(), uid))
            await bot.log_admin(f"🆕 Novo membro verificado: <@{uid}> ({', '.join(ok)})")
            notes.append("🎉 **Welcome to Backline!** You now have access to the community.")
        await grant_member_roles(bot, user)
        notes.append(f"✅ Confirmed: **{', '.join(ok)}**")
    if taken:
        notes.append(f"❌ Already registered by another member: **{', '.join(taken)}**. If it's yours, "
                     "open a ticket in suggestions.")
    if missing and bio:
        notes.append(f"⏳ Code not found yet: **{', '.join(missing)}** — check that it's saved in the bio "
                     f"and the profile is public, wait a minute and click **Verify again**.")
    elif missing:
        notes.append(f"⏳ Couldn't find **{', '.join(missing)}** — check the spelling of your @ and that the "
                     "profile is public, then tap **Update profiles**.")
    if failed:
        notes.append(f"⚠️ Couldn't reach **{', '.join(failed)}** right now — try again in a few minutes.")
    if not notes:
        notes.append("Everything is already verified. ✅")
    return notes


async def run_verification(bot, interaction: discord.Interaction) -> None:
    """Botão Verify: confere os perfis pendentes e responde com o cartão de status."""
    member = await bot.db.get_member(interaction.user.id)
    if not member:
        await interaction.response.send_message(t("start_first", "en"), ephemeral=True)
        return
    await interaction.response.send_message("🔎 Checking your profiles… this can take up to a minute.",
                                            ephemeral=True)
    notes = await verify_pending(bot, interaction.user)
    embed, view = await status_card(bot, interaction.user.id, "\n".join(notes))
    await interaction.edit_original_response(content=None, embed=embed, view=view)


async def founder_eligible(bot, uid: int, founder: discord.Role) -> bool:
    """Founder Member: os primeiros membros reais verificados (contas de teste e demo ficam de fora)."""
    from .server_setup import FOUNDER_LIMIT
    row = await bot.db.fetchone("SELECT COALESCE(is_test,0) AS t, demo_name FROM members WHERE user_id=?", (uid,))
    if row and (row["t"] or row["demo_name"]):
        return False
    return len(founder.members) < FOUNDER_LIMIT


async def grant_member_roles(bot, user: discord.abc.User) -> None:
    guild = bot.guild
    member = guild.get_member(user.id) if guild else None
    if not member:
        return
    roles = [r for r in [await bot.role("member")] if r and r not in member.roles]
    founder = await bot.role("founder")
    if founder and founder not in member.roles and await founder_eligible(bot, user.id, founder):
        roles.append(founder)
    if roles:
        try:
            await member.add_roles(*roles, reason="Backline verified")
        except discord.HTTPException as e:
            await bot.log_admin(f"⚠️ Não consegui dar cargos para {member.mention}: {e}")


class Onboarding(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        bot.add_view(JoinView(bot))
        bot.add_view(StatusView(bot, show_verify=True))

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.type != discord.InteractionType.component:
            return
        cid = (interaction.data or {}).get("custom_id", "")
        if cid.startswith("bl:genre:") or cid == "bl:genre_done":
            if interaction.response.is_done():
                return
            try:
                await handle_genre_click(self.bot, interaction, cid)
            except Exception as e:  # noqa: BLE001
                await self.bot.log_admin(f"⚠️ Escolha de gênero falhou: `{str(e)[:200]}`")


async def setup(bot):
    await bot.add_cog(Onboarding(bot))
