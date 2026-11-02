"""Configuração lida do arquivo .env."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    return int(value) if value else default


def _float(name: str, default: float) -> float:
    value = os.getenv(name, "").strip()
    return float(value) if value else default


# Gêneros amplos, no padrão das plataformas e premiações internacionais.
# Estilos brasileiros (MPB, Bossa Nova, Samba, Pagode, Sertanejo, Funk BR) entram em "Latin",
# como no Latin Grammy. Subgêneros podem vir depois.
GENRES = [
    "Pop", "Rock", "Indie / Alternative", "Hip-Hop / Rap", "R&B / Soul",
    "Electronic", "Latin", "Jazz / Blues", "Country", "Folk / Acoustic",
    "Metal", "Classical", "Gospel / Christian", "Reggae", "Other",
]

# Para quem escolheu um gênero da lista antiga
LEGACY_GENRES = {
    "Indie": "Indie / Alternative", "MPB": "Latin", "Samba / Pagode": "Latin", "Sertanejo": "Latin",
    "Funk": "Latin", "Jazz": "Jazz / Blues", "Blues": "Jazz / Blues", "Singer-Songwriter": "Folk / Acoustic",
    "Gospel": "Gospel / Christian", "Instrumental": "Other", "Lo-fi": "Electronic",
}


@dataclass
class Config:
    token: str = os.getenv("DISCORD_TOKEN", "")
    guild_id: int = _int("GUILD_ID", 0)
    youtube_api_key: str = os.getenv("YOUTUBE_API_KEY", "")
    apify_token: str = os.getenv("APIFY_TOKEN", "")
    apify_ig_comments: str = os.getenv("APIFY_IG_COMMENTS_ACTOR", "apify~instagram-comment-scraper")
    apify_ig_profile: str = os.getenv("APIFY_IG_PROFILE_ACTOR", "apify~instagram-profile-scraper")
    apify_ig_post: str = os.getenv("APIFY_IG_POST_ACTOR", "apify~instagram-scraper")
    apify_tt_comments: str = os.getenv("APIFY_TT_COMMENTS_ACTOR", "clockworks~tiktok-comments-scraper")
    apify_tt_profile: str = os.getenv("APIFY_TT_PROFILE_ACTOR", "clockworks~tiktok-profile-scraper")
    apify_ig_following: str = os.getenv("APIFY_IG_FOLLOWING_ACTOR", "coderx~instagram-followers-following-scraper-no-cookies-login")
    apify_tt_following: str = os.getenv("APIFY_TT_FOLLOWING_ACTOR", "coregent~tiktok-followers-following-scraper")
    # Mutual Follow: quantos "seguindo" recentes ler por verificação e quantas tentativas
    mutual_scan_limit: int = _int("MUTUAL_SCAN_LIMIT", 100)
    mutual_max_attempts: int = _int("MUTUAL_MAX_ATTEMPTS", 3)
    # Onde o servidor guarda a URL/chave do Supabase (usado pelo /admin update)
    supabase_config: str = os.getenv("SUPABASE_CONFIG", "/etc/painel-nuvem/config.json")
    update_repo: str = os.getenv("UPDATE_REPO", "dougpcorrea/backline")  # repositório público do GitHub
    update_branch: str = os.getenv("UPDATE_BRANCH", "main")
    window_days: int = _int("WINDOW_DAYS", 30)
    min_activity: int = _int("MIN_ACTIVITY", 5)
    target_ratio: float = _float("TARGET_RATIO", 1.0)
    posts_per_week: int = _int("POSTS_PER_WEEK", 1)
    checkpoints_hours: list[int] = field(default_factory=lambda: [
        int(x) for x in os.getenv("COLLECT_CHECKPOINTS_HOURS", "24,72,168").split(",") if x.strip()
    ])
    recruiter_min_active: int = _int("RECRUITER_MIN_ACTIVE", 3)
    # Supabase/Postgres (postgresql://...) ou caminho de um arquivo SQLite
    database_url: str = os.getenv("DATABASE_URL", "") or os.getenv("DATABASE_PATH", "backline.db")
    genres: list[str] = field(default_factory=lambda: list(GENRES))


config = Config()
