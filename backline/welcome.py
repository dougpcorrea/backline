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
Every **30 minutes**, the Backline bot checks the posts shared in {post} through the Instagram and YouTube APIs and finds the comments left by other registered members. No login or account connection needed — it only reads public comments and matches them to the profiles you verified.

These verified interactions feed a **10-level reputation system**: the more you support other artists compared to the support you receive, the higher your level. Levels are recalculated automatically, so keep showing up.

**Discover is for active supporters: only artists at Lv.3 or higher appear in {discover}.** Getting there is simple — comment on other members' posts.

**The higher your level, the more often your music shows up in Discover — and in the top spots.** That's why using Discover a lot matters: every track you open and comment on raises your level and puts your own music in front of more artists.

-# Comments are the only interaction tracked automatically right now. Likes, saves and shares are encouraged but can't be verified.

### 🤝 03 — Mutual Follow
Discover artists with similar musical interests through our optional Mutual Follow system in {mutual}.

The goal is to build meaningful networks, not collect empty follows.

### ⭐ 04 — Artist Ratings
Our internal artist evaluation system helps identify musical styles, experience levels, and artistic quality. Members can evaluate each other's work anonymously, helping Backline provide better music discovery and more relevant artist recommendations as the community grows.

-# Artist ratings and recommendation features will be introduced as they become available.

### 🚀 05 — Get Started
🎸 **Join** — Register your Instagram and/or YouTube accounts and verify ownership using a temporary code in your bio.
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
