"""Backline Discover: escolhe quais publicações mostrar para cada membro.

Pontuação orgânica (0–1), com os pesos definidos pelo Douglas:
    participação do artista 35% · afinidade de gênero 25% · recente e não visto 20%
    · afinidade de qualidade 15% · descoberta aleatória 5%
O impulso pago (boost) multiplica a chance de ser sorteado, sem marcar o cartão.
A seleção é um sorteio ponderado (não um top-N fixo), então "Refresh" sempre traz variedade
e artistas de nível baixo continuam aparecendo de vez em quando.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

DAY = 86400
WEIGHTS = {"participation": 0.35, "genre": 0.25, "fresh": 0.20, "quality": 0.15, "random": 0.05}
FRESH_DAYS = 30        # depois disso a publicação conta como "antiga"
SEEN_COOLDOWN = 7 * DAY  # o que o membro já viu volta só depois disso (se faltar conteúdo)
NEW_ARTIST_DAYS = 14


@dataclass
class Candidate:
    post_id: int
    user_id: int
    created_at: int
    level: int = 1
    genres: set[str] = field(default_factory=set)
    avg_rating: float | None = None      # média de estrelas que os trabalhos do artista recebem
    seen_at: int | None = None           # quando este membro viu este post no Discover
    boost: float = 1.0                   # 1 = sem impulso


@dataclass
class Viewer:
    user_id: int
    genres: set[str]
    avg_rating: float | None = None      # média de estrelas que o próprio membro recebe


def components(c: Candidate, v: Viewer, now: int, rng: random.Random) -> dict[str, float]:
    participation = max(0.0, min(1.0, (c.level - 1) / 9))
    if v.genres:
        genre = len(c.genres & v.genres) / len(v.genres)
    else:
        genre = 0.5
    age_days = max(0, now - c.created_at) / DAY
    fresh = max(0.0, 1 - age_days / FRESH_DAYS)
    if c.seen_at:
        fresh *= 0.2
    if c.avg_rating is not None and v.avg_rating is not None:
        quality = 1 - abs(c.avg_rating - v.avg_rating) / 4
    elif c.avg_rating is not None:
        quality = c.avg_rating / 5 * 0.8   # membro ainda sem avaliações: favorece levemente quem é bem avaliado
    else:
        quality = 0.5                      # sem dados: neutro
    return {"participation": participation, "genre": genre, "fresh": fresh,
            "quality": quality, "random": rng.random()}


def score(c: Candidate, v: Viewer, now: int, rng: random.Random) -> float:
    comp = components(c, v, now, rng)
    organic = sum(WEIGHTS[k] * comp[k] for k in WEIGHTS)
    return organic * max(c.boost, 0.0)


def pick(candidates: list[Candidate], viewer: Viewer, now: int, n: int = 4,
         rng: random.Random | None = None) -> list[Candidate]:
    """Sorteio ponderado sem repetição: no máximo uma publicação por artista."""
    rng = rng or random.Random()
    pool = [c for c in candidates if c.user_id != viewer.user_id]
    recent_unseen = [c for c in pool if not c.seen_at or now - c.seen_at > SEEN_COOLDOWN]
    if len(recent_unseen) >= n:
        pool = recent_unseen
    weighted = [(c, max(score(c, viewer, now, rng), 1e-4) ** 2) for c in pool]
    chosen: list[Candidate] = []
    artists: set[int] = set()
    while weighted and len(chosen) < n:
        total = sum(w for _, w in weighted)
        r = rng.random() * total
        acc = 0.0
        for i, (c, w) in enumerate(weighted):
            acc += w
            if acc >= r:
                break
        c = weighted.pop(i)[0]
        if c.user_id in artists:
            continue
        artists.add(c.user_id)
        chosen.append(c)
    return chosen
