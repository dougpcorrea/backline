"""Texto do canal #start-here. {post}, {discover}, {mutual} viram menções aos canais."""

TITLE = "🎸 Welcome to Backline"

INTRO = (
    "**Great artists deserve to be heard.**\n"
    "Every day, independent musicians create incredible work that goes unnoticed. Backline exists to change "
    "that — bringing artists together to discover great music, support each other, and grow their presence "
    "on social media.\n\n"
    "*Real artists. Real appreciation. Collective growth.*"
)

SECTIONS = [
    ("🎵 01 — How It Works",
     "Share your music in {post} and discover what other independent artists are creating in {discover}.\n\n"
     "Take the time to genuinely appreciate their work: watch their videos, listen until the end, like, leave "
     "meaningful comments, save, repost, or share when you enjoy something. And when an artist truly resonates "
     "with you, give them a follow.\n"
     "There's no fixed queue or assigned groups. Explore freely, discover new music, and build genuine connections."),
    ("🏆 02 — Your Reputation",
     "Backline uses an automated system to track verifiable interactions between registered artists across "
     "Instagram, TikTok, and YouTube.\n"
     "Your participation contributes to a 10-level reputation system, making it easier for other members to "
     "recognize artists who actively support the community.\n"
     "The more consistently you participate, the stronger your reputation becomes.\n"
     "Currently, verified comments are the primary ranking metric. Other interactions are encouraged but not "
     "universally tracked."),
    ("🤝 03 — Mutual Follow",
     "Discover artists with similar musical interests through our optional Mutual Follow system in {mutual}.\n"
     "You can accept or decline invitations freely. When both artists agree, the bot tracks the connection "
     "where technically possible.\n"
     "The goal is to build meaningful networks, not collect empty follows."),
    ("⭐ 04 — Artist Ratings",
     "Our internal artist evaluation system helps identify musical styles, experience levels, and artistic quality.\n"
     "Members can evaluate each other's work anonymously, helping Backline provide better music discovery and "
     "more relevant artist recommendations as the community grows.\n"
     "Artist ratings and recommendation features will be introduced as they become available."),
    ("🚀 05 — Get Started",
     "🎸 **Join** — Register your Instagram, TikTok, and/or YouTube accounts and verify ownership using a "
     "temporary code in your bio.\n"
     "👤 **My Profiles** — View your verification status, update your social accounts, or manage your musical genres.\n\n"
     "Once verified, introduce yourself, share your music, and start discovering other artists."),
]

OUTRO = ("Backline is built on a simple belief: supporting independent music should mean actually caring "
         "about the music. ❤️")


def _space(text: str) -> str:
    """Uma linha em branco entre cada parágrafo (o Discord cola tudo se não tiver)."""
    parts = [p.strip() for p in text.split("\n") if p.strip()]
    return "\n\n".join(parts)


def build(mentions: dict[str, str]):
    import discord
    embed = discord.Embed(title=TITLE, description=_space(INTRO) + "\n\u200b", colour=0x30A46C)
    for name, value in SECTIONS:
        # o "\u200b" no fim cria um respiro entre uma seção e a próxima
        embed.add_field(name=name, value=_space(value.format(**mentions)) + "\n\u200b", inline=False)
    embed.add_field(name="\u200b", value=OUTRO, inline=False)
    return embed
