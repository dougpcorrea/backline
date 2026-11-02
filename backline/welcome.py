"""Texto do canal #start-here.

O texto é Markdown do Discord e pode ser editado pelo próprio dono com /admin welcome.
Atalhos que viram links dos canais: {post}, {discover}, {mutual}.
"""

DEFAULT_TEXT = """# 🎸 Welcome to Backline

**Great artists deserve to be heard.**
Every day, independent musicians create incredible work that goes unnoticed. Backline exists to change that — bringing artists together to discover great music, support each other, and grow their presence on social media.

*Real artists. Real appreciation. Collective growth.*

### 🎵 01 — How It Works
Share your music in {post} and discover what other independent artists are creating in {discover}.

Take the time to genuinely appreciate their work: watch their videos, listen until the end, like, leave meaningful comments, save, repost, or share when you enjoy something. And when an artist truly resonates with you, give them a follow.

There's no fixed queue or assigned groups. Explore freely, discover new music, and build genuine connections.

### 🏆 02 — Your Reputation
Backline uses an automated system to track verifiable interactions between registered artists across Instagram, TikTok, and YouTube.

Your participation contributes to a 10-level reputation system, making it easier for other members to recognize artists who actively support the community. The more consistently you participate, the stronger your reputation becomes.

-# Currently, verified comments are the primary ranking metric. Other interactions are encouraged but not universally tracked.

### 🤝 03 — Mutual Follow
Discover artists with similar musical interests through our optional Mutual Follow system in {mutual}.

The goal is to build meaningful networks, not collect empty follows.

### ⭐ 04 — Artist Ratings
Our internal artist evaluation system helps identify musical styles, experience levels, and artistic quality. Members can evaluate each other's work anonymously, helping Backline provide better music discovery and more relevant artist recommendations as the community grows.

-# Artist ratings and recommendation features will be introduced as they become available.

### 🚀 05 — Get Started
🎸 **Join** — Register your Instagram, TikTok, and/or YouTube accounts and verify ownership using a temporary code in your bio.
👤 **My Profiles** — View your verification status, update your social accounts, or manage your musical genres.

Once verified, introduce yourself, share your music, and start discovering other artists.

Backline is built on a simple belief: supporting independent music should mean actually caring about the music. ❤️"""


def render(text: str, mentions: dict[str, str]) -> str:
    out = text
    for key, value in mentions.items():
        out = out.replace("{" + key + "}", value)
    return out[:4096]


def build(mentions: dict[str, str], text: str | None = None):
    import discord
    return discord.Embed(description=render(text or DEFAULT_TEXT, mentions), colour=0x30A46C)
