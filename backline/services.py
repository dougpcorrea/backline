"""Regras de negócio que usam o banco (participação e recrutamento).

Ficam separadas dos cogs do Discord para poderem ser testadas sem bot.
"""
from __future__ import annotations

from dataclasses import dataclass

from .collectors import Comment
from .db import Database, now
from .scoring import compute_level, rating_boost, ratio_of, recruiter_score

DAY = 86400

# Comentários ligados a membros verificados e ativos (um por membro por publicação).
MATCHED_SQL = """
SELECT DISTINCT c.post_id AS post_id, h.user_id AS commenter_id
FROM comments c
JOIN handles h
  ON h.platform = c.platform AND h.verified = 1
 AND (h.handle = c.author_handle
      OR (COALESCE(h.external_id, '') != '' AND h.external_id = c.author_external_id))
JOIN members m ON m.user_id = h.user_id AND m.left_at IS NULL
"""


@dataclass
class Score:
    user_id: int
    given: int
    received: int
    ratio: float
    level: int


async def record_comments(db: Database, post_id: int, platform: str, comments: list[Comment]) -> list[int]:
    """Salva comentários novos. Devolve os membros que comentaram pela primeira vez nessa publicação."""
    before = {r["commenter_id"] for r in await db.fetchall(
        f"SELECT y.commenter_id FROM ({MATCHED_SQL}) y WHERE y.post_id=?", (post_id,))}
    ts = now()
    await db.executemany(
        "INSERT INTO comments(platform, comment_id, post_id, author_handle, "
        "author_external_id, created_at, collected_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
        [(platform, c.comment_id, post_id, c.author_handle, c.author_external_id, c.created_at, ts)
         for c in comments],
    )
    after = await post_member_commenters(db, post_id)
    return [uid for uid in after if uid not in before]


async def post_member_commenters(db: Database, post_id: int) -> list[int]:
    """Membros (exceto o autor) que comentaram numa publicação."""
    rows = await db.fetchall(
        f"SELECT x.commenter_id FROM ({MATCHED_SQL}) x JOIN posts p ON p.id = x.post_id "
        "WHERE x.post_id=? AND x.commenter_id != p.user_id",
        (post_id,),
    )
    return [r["commenter_id"] for r in rows]


async def rating_pool(db: Database) -> list[int]:
    """Artistas avaliáveis: membros ativos e verificados com as 3 publicações cadastradas no Artist Ratings."""
    rows = await db.fetchall(
        "SELECT t.user_id FROM rating_tracks t JOIN members m ON m.user_id = t.user_id "
        "WHERE m.verified_at IS NOT NULL AND m.left_at IS NULL AND m.demo_name IS NULL "
        "GROUP BY t.user_id HAVING COUNT(*) >= 3")
    return [int(r["user_id"]) for r in rows]


async def rating_boosts(db: Database) -> dict[int, float]:
    """Bônus de participação por avaliar artistas: proporcional a quantos dos artistas disponíveis a pessoa avaliou."""
    pool = set(await rating_pool(db))
    if not pool:
        return {}
    out = {}
    rated: dict[int, int] = {}
    for r in await db.fetchall("SELECT rater_id, artist_id FROM artist_ratings"):
        if int(r["artist_id"]) in pool:
            rated[int(r["rater_id"])] = rated.get(int(r["rater_id"]), 0) + 1
    for uid, n in rated.items():
        out[uid] = rating_boost(n, len(pool) - (1 if uid in pool else 0))
    return out


async def compute_scores(db: Database, window_days: int, min_activity: int,
                         target_ratio: float, save: bool = True) -> dict[int, Score]:
    cutoff = now() - window_days * DAY
    base = (
        f"SELECT x.commenter_id, p.user_id AS author_id FROM ({MATCHED_SQL}) x "
        "JOIN posts p ON p.id = x.post_id "
        "WHERE p.removed = 0 AND p.created_at >= ? AND x.commenter_id != p.user_id"
    )
    given_rows = await db.fetchall(
        f"SELECT commenter_id AS uid, COUNT(*) AS n FROM ({base}) b GROUP BY commenter_id", (cutoff,))
    recv_rows = await db.fetchall(
        f"SELECT author_id AS uid, COUNT(*) AS n FROM ({base}) b GROUP BY author_id", (cutoff,))
    given = {r["uid"]: r["n"] for r in given_rows}
    received = {r["uid"]: r["n"] for r in recv_rows}

    boosts = await rating_boosts(db)
    scores: dict[int, Score] = {}
    for uid in await db.verified_member_ids():
        g, r = given.get(uid, 0), received.get(uid, 0)
        scores[uid] = Score(uid, g, r, ratio_of(g, r),
                            compute_level(g, r, min_activity, target_ratio, boosts.get(uid, 1.0)))

    if save:
        ts = now()
        await db.executemany(
            "INSERT INTO scores(user_id, given, received, ratio, level, updated_at) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET given=excluded.given, received=excluded.received, "
            "ratio=excluded.ratio, level=excluded.level, updated_at=excluded.updated_at",
            [(s.user_id, s.given, s.received, s.ratio, s.level, ts) for s in scores.values()],
        )
    return scores


@dataclass
class RecruiterStats:
    user_id: int
    total: int
    active: int
    verified_inactive: int

    @property
    def score(self) -> float:
        return recruiter_score(self.active, self.verified_inactive)


async def recruiter_stats(db: Database, min_activity: int) -> dict[int, RecruiterStats]:
    rows = await db.fetchall(
        "SELECT r.inviter_id, r.user_id, m.verified_at, COALESCE(s.given, 0) AS given "
        "FROM recruits r "
        "LEFT JOIN members m ON m.user_id = r.user_id "
        "LEFT JOIN scores s ON s.user_id = r.user_id "
        "WHERE r.left_at IS NULL"
    )
    stats: dict[int, RecruiterStats] = {}
    for row in rows:
        st = stats.setdefault(row["inviter_id"], RecruiterStats(row["inviter_id"], 0, 0, 0))
        st.total += 1
        if row["verified_at"] is not None:
            if row["given"] >= min_activity:
                st.active += 1
            else:
                st.verified_inactive += 1
    return stats


COLLECT_DAYS = 7          # cada publicação é acompanhada por 7 dias


def collect_interval(platform: str, age_seconds: int) -> int:
    """De quanto em quanto tempo reler os comentários de uma publicação.
    YouTube (API oficial, grátis): a cada 30 min. Instagram (pago por leitura): 1h no 1º dia, depois 6h."""
    if platform == "youtube":
        return 30 * 60
    return 3600 if age_seconds < DAY else 6 * 3600


async def posts_due_for_collection(db: Database, checkpoints_hours: list[int] | None = None) -> list:
    """Publicações (não demo) dos últimos 7 dias cuja próxima leitura já venceu."""
    ts = now()
    rows = await db.fetchall("SELECT * FROM posts WHERE removed = 0 AND created_at >= ? "
                             "AND external_id NOT LIKE 'demo-%' AND user_id NOT IN "
                             "(SELECT user_id FROM members WHERE demo_name IS NOT NULL)",
                             (ts - COLLECT_DAYS * DAY - 6 * 3600,))
    due = []
    for row in rows:
        age = ts - row["created_at"]
        last = row["last_collected"] or 0
        if age > COLLECT_DAYS * DAY and last >= row["created_at"] + COLLECT_DAYS * DAY:
            continue   # leitura final já feita
        if ts - last >= collect_interval(row["platform"], age):
            due.append(row)
    return due


async def posts_in_last_week(db: Database, user_id: int) -> list:
    return await db.fetchall(
        "SELECT * FROM posts WHERE user_id=? AND removed=0 AND created_at >= ? ORDER BY created_at",
        (user_id, now() - 7 * DAY),
    )


# ======================= Mutual Follow =======================
MUTUAL_PLATFORMS = ("instagram", "youtube")


@dataclass
class Candidate:
    user_id: int
    platform: str
    handle: str
    external_id: str | None
    follows_me: bool


async def mutual_active_ids(db: Database) -> set[int]:
    rows = await db.fetchall(
        "SELECT mm.user_id FROM mutual_members mm JOIN members m ON m.user_id = mm.user_id "
        "WHERE mm.active = 1 AND m.left_at IS NULL AND m.verified_at IS NOT NULL")
    return {r["user_id"] for r in rows}


async def retribution_rates(db: Database) -> dict[int, float]:
    """Para cada membro: dos que o seguiram (verificado), quantos ele seguiu de volta."""
    rows = await db.fetchall(
        "SELECT f.followee_id AS uid, COUNT(*) AS recebidos, "
        "SUM(CASE WHEN EXISTS (SELECT 1 FROM follows b WHERE b.follower_id = f.followee_id "
        "  AND b.followee_id = f.follower_id AND b.platform = f.platform AND b.status = 'verified') "
        "  THEN 1 ELSE 0 END) AS devolvidos "
        "FROM follows f WHERE f.status = 'verified' GROUP BY f.followee_id")
    return {r["uid"]: (r["devolvidos"] or 0) / r["recebidos"] for r in rows if r["recebidos"]}


async def mutual_candidates(db: Database, user_id: int, limit: int = 10) -> list[Candidate]:
    """Perfis que o membro ainda não segue, priorizando quem já o segue e quem costuma retribuir."""
    import random
    active = await mutual_active_ids(db)
    if user_id not in active:
        return []
    mine = {h["platform"] for h in await db.handles_of(user_id) if h["verified"]}
    rows = await db.fetchall(
        "SELECT h.user_id, h.platform, h.handle, h.external_id FROM handles h WHERE h.verified = 1 AND h.user_id != ?",
        (user_id,))
    done = {(r["followee_id"], r["platform"]) for r in await db.fetchall(
        "SELECT followee_id, platform FROM follows WHERE follower_id = ? AND status IN ('pending','verified')",
        (user_id,))}
    follows_me = {(r["follower_id"], r["platform"]) for r in await db.fetchall(
        "SELECT follower_id, platform FROM follows WHERE followee_id = ? AND status = 'verified'", (user_id,))}
    rates = await retribution_rates(db)
    out = []
    for r in rows:
        key = (r["user_id"], r["platform"])
        if r["user_id"] not in active or r["platform"] not in mine or key in done:
            continue
        out.append(Candidate(r["user_id"], r["platform"], r["handle"], r["external_id"], key in follows_me))
    random.shuffle(out)
    out.sort(key=lambda c: (not c.follows_me, -rates.get(c.user_id, 0.5)))
    return out[:limit]


async def add_follow_claims(db: Database, follower_id: int, items: list[tuple[int, str]]) -> int:
    ts = now()
    await db.executemany(
        "INSERT INTO follows(follower_id, followee_id, platform, status, claimed_at, attempts) "
        "VALUES(?,?,?,'pending',?,0) ON CONFLICT(follower_id, followee_id, platform) DO UPDATE SET "
        "status='pending', claimed_at=excluded.claimed_at, attempts=0, last_check=NULL "
        "WHERE follows.status IN ('failed','unfollowed')",
        [(follower_id, followee, platform, ts) for followee, platform in items])
    return len(items)


async def pending_follow_claims(db: Database, min_age_s: int = 120, recheck_s: int = 900) -> list:
    ts = now()
    return await db.fetchall(
        "SELECT f.*, a.handle AS follower_handle, a.external_id AS follower_ext, "
        "b.handle AS followee_handle, b.external_id AS followee_ext "
        "FROM follows f "
        "JOIN handles a ON a.user_id = f.follower_id AND a.platform = f.platform AND a.verified = 1 "
        "JOIN handles b ON b.user_id = f.followee_id AND b.platform = f.platform AND b.verified = 1 "
        "WHERE f.status = 'pending' AND f.claimed_at <= ? AND (f.last_check IS NULL OR f.last_check <= ?) "
        "ORDER BY f.claimed_at LIMIT 200",
        (ts - min_age_s, ts - recheck_s))


async def mark_follow(db: Database, follower_id: int, followee_id: int, platform: str, status: str) -> None:
    ts = now()
    if status == "verified":
        await db.execute("UPDATE follows SET status='verified', verified_at=?, last_check=? "
                         "WHERE follower_id=? AND followee_id=? AND platform=?",
                         (ts, ts, follower_id, followee_id, platform))
    else:
        await db.execute("UPDATE follows SET status=?, last_check=?, attempts=attempts+1 "
                         "WHERE follower_id=? AND followee_id=? AND platform=?",
                         (status, ts, follower_id, followee_id, platform))


async def is_followed_back(db: Database, follower_id: int, followee_id: int, platform: str) -> bool:
    return bool(await db.fetchone(
        "SELECT 1 FROM follows WHERE follower_id=? AND followee_id=? AND platform=? AND status='verified'",
        (followee_id, follower_id, platform)))


@dataclass
class MutualStats:
    following: int
    followers: int
    mutual: int
    pending: int
    to_return: list  # rows (follower_id, platform, handle) que seguiram e ainda não foram seguidos de volta


async def mutual_stats(db: Database, user_id: int) -> MutualStats:
    following = await db.fetchall("SELECT followee_id, platform FROM follows WHERE follower_id=? AND status='verified'",
                                  (user_id,))
    followers = await db.fetchall(
        "SELECT f.follower_id, f.platform, h.handle, h.external_id FROM follows f "
        "JOIN handles h ON h.user_id = f.follower_id AND h.platform = f.platform "
        "WHERE f.followee_id=? AND f.status='verified'", (user_id,))
    pending = await db.fetchone("SELECT COUNT(*) AS n FROM follows WHERE follower_id=? AND status='pending'",
                                (user_id,))
    mine = {(r["followee_id"], r["platform"]) for r in following}
    mutual = [r for r in followers if (r["follower_id"], r["platform"]) in mine]
    to_return = [r for r in followers if (r["follower_id"], r["platform"]) not in mine]
    return MutualStats(len(following), len(followers), len(mutual), pending["n"] if pending else 0, to_return)


def profile_url(platform: str, handle: str, external_id: str | None = None) -> str:
    if platform == "instagram":
        return f"https://www.instagram.com/{handle}/"
    if platform == "tiktok":
        return f"https://www.tiktok.com/@{handle}"
    if external_id:
        return f"https://www.youtube.com/channel/{external_id}"
    return f"https://www.youtube.com/@{handle}"
