"""Textos em inglês e português.

- `t(key, lang)`: mensagem privada, no idioma do Discord do membro.
- `bi(key)`: mensagem pública, nos dois idiomas (inglês em cima, português embaixo).
"""
from __future__ import annotations

import discord

STRINGS: dict[str, dict[str, str]] = {
    # ---------- entrada ----------
    "panel_title": {
        "en": "🎸 Welcome to Backline",
        "pt": "🎸 Bem-vindo ao Backline",
    },
    "panel_desc": {
        "en": "A community of independent artists who discover, support and grow together.\n\n"
              "🎸 **Join** — tell us your Instagram, TikTok and/or YouTube and verify them with a code in your bio.\n"
              "👤 **My profiles** — see your verification status, update your profiles or genres.",
        "pt": "Uma comunidade de artistas independentes que se descobrem, se apoiam e crescem juntos.\n"
              "Clique no botão abaixo, informe seus perfis e confirme com um código na bio.",
    },
    "btn_join": {"en": "Join / Update profiles", "pt": "Entrar / Atualizar perfis"},
    "modal_profiles": {"en": "Your profiles", "pt": "Seus perfis"},
    "field_instagram": {"en": "Instagram @ (optional)", "pt": "@ do Instagram (opcional)"},
    "field_tiktok": {"en": "TikTok @ (optional)", "pt": "@ do TikTok (opcional)"},
    "field_youtube": {"en": "YouTube @ (optional)", "pt": "@ do YouTube (opcional)"},
    "err_no_handle": {
        "en": "Please fill in at least one profile.",
        "pt": "Preencha pelo menos um perfil.",
    },
    "err_invalid_handle": {
        "en": "That {platform} @ doesn't look valid: `{value}`",
        "pt": "Esse @ do {platform} não parece válido: `{value}`",
    },
    "err_handle_taken": {
        "en": "@{handle} on {platform} is already verified by another member. If it's yours, talk to an admin.",
        "pt": "O @{handle} no {platform} já foi verificado por outro membro. Se ele é seu, fale com um admin.",
    },
    "genre_prompt": {
        "en": "**Step 2 of 3** — Pick up to 3 genres that describe your music.",
        "pt": "**Passo 2 de 3** — Escolha até 3 gêneros que descrevem sua música.",
    },
    "genre_placeholder": {"en": "Your genres", "pt": "Seus gêneros"},
    "verify_instructions": {
        "en": "**Step 3 of 3** — Add this code anywhere in the bio of each profile you entered:\n"
              "# `{code}`\n"
              "Save it, then click **Verify**. You can remove the code after you're verified.",
        "pt": "**Passo 3 de 3** — Coloque este código em qualquer lugar da bio de cada perfil que você informou:\n"
              "# `{code}`\n"
              "Salve e clique em **Verificar**. Depois de verificado, pode tirar o código da bio.",
    },
    "btn_verify": {"en": "Verify", "pt": "Verificar"},
    "verifying": {
        "en": "Checking your bios… this can take up to a minute.",
        "pt": "Conferindo suas bios… pode levar até um minuto.",
    },
    "verify_ok": {
        "en": "✅ Verified: {platforms}. Welcome to Backline! You can remove the code from your bio now.",
        "pt": "✅ Verificado: {platforms}. Bem-vindo ao Backline! Já pode tirar o código da bio.",
    },
    "verify_missing": {
        "en": "Not found yet: {platforms}. Make sure the code is saved in the bio and the profile is public, then try again.",
        "pt": "Ainda não encontrado: {platforms}. Confira se o código está salvo na bio e se o perfil é público, e tente de novo.",
    },
    "verify_fail": {
        "en": "❌ Code not found in any bio. Make sure it's saved and the profile is public, wait a minute and click Verify again.",
        "pt": "❌ Código não encontrado em nenhuma bio. Confira se está salvo e se o perfil é público, espere um minuto e clique em Verificar de novo.",
    },
    "verify_error": {
        "en": "⚠️ Couldn't check {platforms} right now. Please try again in a few minutes.",
        "pt": "⚠️ Não consegui conferir {platforms} agora. Tente de novo em alguns minutos.",
    },
    "start_first": {
        "en": "Start with the **Join** button first.",
        "pt": "Comece pelo botão **Entrar** primeiro.",
    },

    # ---------- feed ----------
    "post_panel_title": {"en": "📤 Share your work", "pt": "📤 Publique seu trabalho"},
    "post_panel_desc": {
        "en": "Paste the link of your new Instagram, TikTok or YouTube post. It enters Discover, "
              "where members find it, and the comments they leave on it count for everyone's participation.\n\n"
              "Limit: {n} post(s) every 7 days.",
        "pt": "Cole o link da sua nova publicação no Instagram, TikTok ou YouTube. Ela entra no Discover, "
              "e os comentários que os membros deixarem lá contam pra participação de todos.\n"
              "Limite: {n} publicação(ões) a cada 7 dias.",
    },
    "btn_new_post": {"en": "New post", "pt": "Nova publicação"},
    "modal_post": {"en": "New post", "pt": "Nova publicação"},
    "field_link": {"en": "Post link", "pt": "Link da publicação"},
    "err_not_verified": {
        "en": "Verify your profiles first in {channel}.",
        "pt": "Verifique seus perfis primeiro em {channel}.",
    },
    "err_bad_link": {
        "en": "I couldn't recognize that link. Use the link of a post on Instagram, TikTok or YouTube.",
        "pt": "Não reconheci esse link. Use o link de uma publicação do Instagram, TikTok ou YouTube.",
    },
    "err_platform_not_verified": {
        "en": "Your {platform} profile isn't verified yet. Update your profiles in {channel}.",
        "pt": "Seu perfil do {platform} ainda não está verificado. Atualize seus perfis em {channel}.",
    },
    "err_not_owner": {
        "en": "This post doesn't seem to be from your verified {platform} profile (@{handle}).",
        "pt": "Essa publicação não parece ser do seu perfil verificado no {platform} (@{handle}).",
    },
    "err_duplicate": {"en": "This post was already shared.", "pt": "Essa publicação já está no feed."},
    "err_limit": {
        "en": "You've reached the limit of {n} post(s) every 7 days. Your next post is available {when}.",
        "pt": "Você atingiu o limite de {n} publicação(ões) a cada 7 dias. A próxima fica liberada {when}.",
    },
    "post_ok": {"en": "✅ Shared! Your post now shows up for members in {channel}.",
                "pt": "✅ Publicado! Sua publicação já aparece pros membros em {channel}."},
    "card_new": {"en": "New post", "pt": "Nova publicação"},
    "card_comments": {
        "en": "💬 {n} member(s) commented",
        "pt": "💬 {n} membro(s) comentaram",
    },
    "btn_open": {"en": "Open", "pt": "Abrir"},
    "dm_new_comments": {
        "en": "💬 {names} commented on your post {url}\nReturning the favor keeps your level up. 😉",
        "pt": "💬 {names} comentaram na sua publicação {url}\nRetribuir mantém seu nível lá em cima. 😉",
    },

    # ---------- perfil ----------
    "profile_not_member": {
        "en": "This person isn't a verified member yet.",
        "pt": "Essa pessoa ainda não é um membro verificado.",
    },
    "profile_level": {"en": "Participation", "pt": "Participação"},
    "profile_given": {"en": "Comments given", "pt": "Comentários dados"},
    "profile_received": {"en": "Comments received", "pt": "Comentários recebidos"},
    "profile_ratio": {"en": "Reciprocity", "pt": "Reciprocidade"},
    "profile_genres": {"en": "Genres", "pt": "Gêneros"},
    "profile_profiles": {"en": "Profiles", "pt": "Perfis"},
    "profile_recruits": {"en": "Recruits (active / total)", "pt": "Recrutados (ativos / total)"},
    "profile_footer": {
        "en": "Last {days} days · below {min} comments given, the level stays at 3 max",
        "pt": "Últimos {days} dias · abaixo de {min} comentários dados, o nível fica no máximo 3",
    },

    # ---------- ranking ----------
    "ranking_title": {"en": "🏆 Backline ranking", "pt": "🏆 Ranking do Backline"},
    "ranking_participation": {"en": "Top participation", "pt": "Maior participação"},
    "ranking_recruiters": {"en": "Top recruiters", "pt": "Maiores divulgadores"},
    "ranking_empty": {"en": "Nobody here yet.", "pt": "Ninguém aqui ainda."},
    "ranking_footer": {"en": "Updated automatically", "pt": "Atualizado automaticamente"},

    # ---------- recrutamento ----------
    "invite_ok": {
        "en": "🛡️ Your personal invite link:\n{url}\nEveryone who joins through it counts as your recruit. "
              "Recruits who stay active count much more than inactive ones.",
        "pt": "🛡️ Seu link de convite pessoal:\n{url}\nQuem entrar por ele conta como seu recrutado. "
              "Recrutados que ficam ativos valem bem mais que os inativos.",
    },
    "invite_error": {
        "en": "I couldn't create an invite. Ask an admin to check my permissions.",
        "pt": "Não consegui criar o convite. Peça pra um admin conferir minhas permissões.",
    },
    "paused": {
        "en": "⏸️ The ranking is paused for maintenance. Your participation is still being saved.",
        "pt": "⏸️ O ranking está pausado para manutenção. Sua participação continua sendo registrada.",
    },
}


# A comunidade é internacional: tudo que os membros veem fica em inglês.
# (Os textos em português continuam no arquivo caso um dia vire opção.)
LANGUAGES = ("en",)


def lang_of(locale: discord.Locale | str | None) -> str:
    return "en"


def t(key: str, lang: str = "en", **kwargs) -> str:
    entry = STRINGS.get(key, {})
    text = entry.get(lang) or entry.get("en") or key
    return text.format(**kwargs) if kwargs else text


def bi(key: str, **kwargs) -> str:
    """Texto de mensagem pública (só inglês)."""
    return t(key, "en", **kwargs)


def bi_short(key: str, **kwargs) -> str:
    """Rótulo curto (ex.: botões), só inglês."""
    return t(key, "en", **kwargs)
