"""Entrada na comunidade: perfis, gêneros e verificação por código na bio."""
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
    if pending and member and member["verify_code"]:
        desc += (f"\n\n**To verify the ⏳ profiles:** add this code anywhere in their bio, save, "
                 f"then click **{verb}**:\n# `{member['verify_code']}`")
    elif verified_any:
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

        text, view = await genre_picker(self.bot, uid, lang)
        await interaction.response.send_message("**Step 2 of 3**\n" + text, view=view, ephemeral=True)


MAX_GENRES = 3


class GenreView(discord.ui.View):
    """Um botão por gênero: cada clique liga/desliga na hora (verde = escolhido). "Done" salva."""

    def __init__(self, bot, lang: str, user_id: int, chosen: list[str] | None = None):
        super().__init__(timeout=900)
        self.bot, self.lang, self.user_id = bot, lang, user_id
        self.chosen: list[str] = list(chosen or [])
        self._build()

    def _build(self) -> None:
        self.clear_items()
        for i, g in enumerate(self.bot.cfg.genres[:20]):
            on = g in self.chosen
            b = discord.ui.Button(label=g, style=discord.ButtonStyle.success if on else discord.ButtonStyle.secondary,
                                  emoji="✓" if on else None, row=i // 5)
            b.callback = self._toggle(g)
            self.add_item(b)
        done = discord.ui.Button(label=f"Done ({len(self.chosen)}/{MAX_GENRES})", style=discord.ButtonStyle.primary,
                                 emoji="💾", disabled=not self.chosen, row=4)
        done.callback = self.on_done
        self.add_item(done)

    def _text(self) -> str:
        if not self.chosen:
            return f"🎵 **Pick up to {MAX_GENRES} genres** that describe your music, then press **Done**."
        return (f"🎵 **Your genres:** {' · '.join(self.chosen)}\n"
                f"Tap a genre to add or remove it (up to {MAX_GENRES}), then press **Done**.")

    def _toggle(self, genre: str):
        async def cb(interaction: discord.Interaction) -> None:
            if genre in self.chosen:
                self.chosen.remove(genre)
            elif len(self.chosen) < MAX_GENRES:
                self.chosen.append(genre)
            else:
                self.chosen = self.chosen[1:] + [genre]   # já tinha 3: troca o mais antigo
            self._build()
            await interaction.response.edit_message(content=self._text(), embed=None, view=self)
        return cb

    async def on_done(self, interaction: discord.Interaction) -> None:
        db, uid = self.bot.db, self.user_id
        chosen = list(self.chosen)
        await db.execute("DELETE FROM member_genres WHERE user_id=?", (uid,))
        for g in chosen:
            await db.execute("INSERT INTO member_genres(user_id, genre) VALUES(?,?) ON CONFLICT DO NOTHING", (uid, g))
        await sync_genre_roles(self.bot, interaction.user, chosen)

        pending = [h for h in await db.handles_of(uid) if not h["verified"]]
        header = ("✅ Genres saved." if not pending else
                  "**Last step:** prove these profiles are yours.")
        embed, view = await status_card(self.bot, uid, header)
        await interaction.response.edit_message(content=None, embed=embed, view=view)


async def genre_picker(bot, user_id: int, lang: str = "en") -> tuple[str, "GenreView"]:
    current = [r["genre"] for r in await bot.db.fetchall("SELECT genre FROM member_genres WHERE user_id=?", (user_id,))]
    view = GenreView(bot, lang, user_id, [g for g in current if g in bot.cfg.genres])
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


async def run_verification(bot, interaction: discord.Interaction) -> None:
    """Confere o código na bio de cada perfil pendente e responde com o cartão de status."""
    db, uid = bot.db, interaction.user.id
    member = await db.get_member(uid)
    if not member or not member["verify_code"]:
        await interaction.response.send_message(t("start_first", "en"), ephemeral=True)
        return
    await interaction.response.send_message("🔎 Checking your bios… this can take up to a minute.", ephemeral=True)

    code = member["verify_code"].upper()
    await db.execute("UPDATE members SET verify_tried=1 WHERE user_id=?", (uid,))
    ok, missing, failed = [], [], []
    # contas TESTER (is_test=1, sem demo_name): perfis fictícios valem sem código na bio
    tester = bool(member["is_test"]) and not member["demo_name"]
    for h in await db.handles_of(uid):
        if h["verified"]:
            continue
        platform, handle = h["platform"], h["handle"]
        if tester:
            await db.execute("UPDATE handles SET verified=1 WHERE user_id=? AND platform=?", (uid, platform))
            ok.append(PLATFORM_NAME[platform])
            continue
        try:
            profile = await bot.collectors.profile(platform, handle)
        except CollectorError as e:
            failed.append(PLATFORM_NAME[platform])
            await bot.log_admin(f"⚠️ Verificação {platform} @{handle} falhou: {e}")
            continue
        if profile and code in (profile.bio or "").upper():
            owner = await db.handle_owner(platform, handle)
            if owner and owner != uid:
                missing.append(PLATFORM_NAME[platform])
                continue
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
        await grant_member_roles(bot, interaction.user)
        notes.append(f"✅ Just verified: **{', '.join(ok)}**")
    if missing:
        notes.append(f"⏳ Code not found yet: **{', '.join(missing)}** — check that it's saved in the bio "
                     f"and the profile is public, wait a minute and click **Verify again**.")
    if failed:
        notes.append(f"⚠️ Couldn't reach **{', '.join(failed)}** right now — try again in a few minutes.")
    if not notes:
        notes.append("Everything is already verified. ✅")
    embed, view = await status_card(bot, uid, "\n".join(notes))
    await interaction.edit_original_response(content=None, embed=embed, view=view)


async def grant_member_roles(bot, user: discord.abc.User) -> None:
    guild = bot.guild
    member = guild.get_member(user.id) if guild else None
    if not member:
        return
    roles = [r for r in [await bot.role("member")] if r and r not in member.roles]
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


async def setup(bot):
    await bot.add_cog(Onboarding(bot))
