import os
import time

import pytest

from backline.collectors import Comment
from backline.db import Database, now
from backline.links import normalize_handle, parse_post_url
from backline.scoring import compute_level, level_color
from backline.services import compute_scores, post_member_commenters, record_comments, recruiter_stats


# ---------------- níveis ----------------
def test_levels():
    assert compute_level(0, 0, 5) == 1
    assert compute_level(0, 20, 5) == 1
    assert compute_level(10, 10, 5) == 10          # dá tanto quanto recebe
    assert compute_level(5, 10, 5) == 5            # dá metade
    assert compute_level(20, 10, 5) == 10          # dá mais: teto 10
    assert compute_level(3, 0, 5) == 3             # pouca atividade: máximo 3
    assert compute_level(1, 10, 5) == 1
    assert compute_level(8, 10, 5, target_ratio=0.8) == 10


def test_colors_go_red_to_green():
    r1 = (level_color(1) >> 16) & 255
    g10 = (level_color(10) >> 8) & 255
    assert r1 > 150 and g10 > 150


# ---------------- links ----------------
@pytest.mark.parametrize("url,platform,ext", [
    ("https://www.instagram.com/reel/C9abcDEF12x/?igsh=xyz", "instagram", "C9abcDEF12x"),
    ("instagram.com/p/ABC_def-1/", "instagram", "ABC_def-1"),
    ("https://www.tiktok.com/@Maroto.music/video/7412345678901234567?lang=pt", "tiktok", "7412345678901234567"),
    ("https://youtu.be/dQw4w9WgXcQ?si=abc", "youtube", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "youtube", "dQw4w9WgXcQ"),
    ("https://m.youtube.com/watch?v=dQw4w9WgXcQ&t=10", "youtube", "dQw4w9WgXcQ"),
])
def test_parse_post_url(url, platform, ext):
    link = parse_post_url(url)
    assert link and link.platform == platform and link.external_id == ext


def test_parse_rejects_profiles():
    assert parse_post_url("https://instagram.com/fulano") is None
    assert parse_post_url("https://google.com") is None


def test_tiktok_owner_hint():
    assert parse_post_url("https://www.tiktok.com/@Maroto.music/video/1").owner_hint == "maroto.music"


def test_normalize_handle():
    assert normalize_handle("@Maroto") == "maroto"
    assert normalize_handle("https://www.instagram.com/maroto.music/") == "maroto.music"
    assert normalize_handle("https://www.youtube.com/@MarotoMusic") == "marotomusic"
    assert normalize_handle("nome com espaço") is None
    assert normalize_handle("") is None


# ---------------- banco + índice ----------------
PG_URL = os.getenv("TEST_DATABASE_URL")
BACKENDS = ["sqlite"] + (["postgres"] if PG_URL else [])


@pytest.fixture(params=BACKENDS)
async def db(request, tmp_path):
    if request.param == "postgres":
        import asyncpg
        c = await asyncpg.connect(PG_URL)
        await c.execute("DROP SCHEMA IF EXISTS backline CASCADE")
        await c.close()
        d = Database(PG_URL)
    else:
        d = Database(str(tmp_path / "t.db"))
    await d.connect()
    yield d
    await d.close()


async def add_member(db, uid, ig):
    await db.upsert_member(uid, "pt")
    await db.execute("UPDATE members SET verified_at=? WHERE user_id=?", (int(time.time()), uid))
    await db.execute("INSERT INTO handles(user_id, platform, handle, verified) VALUES(?, 'instagram', ?, 1)",
                     (uid, ig))


async def add_post(db, uid, ext):
    return await db.insert_returning_id(
        "INSERT INTO posts(user_id, platform, url, external_id, created_at) VALUES(?, 'instagram', ?, ?, ?)",
        (uid, f"https://www.instagram.com/p/{ext}/", ext, int(time.time())))


async def test_reciprocity_flow(db):
    # A, B e C são membros. D comenta mas não é membro.
    for uid, ig in [(1, "ana"), (2, "bia"), (3, "caio")]:
        await add_member(db, uid, ig)
    p_ana = await add_post(db, 1, "AAA")
    p_bia = await add_post(db, 2, "BBB")

    new = await record_comments(db, p_ana, "instagram", [
        Comment("c1", "bia", None, None),
        Comment("c2", "bia", None, None),     # segundo comentário da Bia no mesmo post não conta 2x
        Comment("c3", "caio", None, None),
        Comment("c4", "estranho", None, None),
        Comment("c5", "ana", None, None),     # autora respondendo não conta
    ])
    assert sorted(new) == [2, 3]
    # coletar de novo não gera "novos" repetidos
    assert await record_comments(db, p_ana, "instagram", [Comment("c1", "bia", None, None)]) == []

    await record_comments(db, p_bia, "instagram", [Comment("c6", "ana", None, None)])
    assert await post_member_commenters(db, p_bia) == [1]

    scores = await compute_scores(db, window_days=30, min_activity=1, target_ratio=1.0)
    ana, bia, caio = scores[1], scores[2], scores[3]
    assert (ana.given, ana.received) == (1, 2)   # deu 1, recebeu 2 -> 0,5 -> Lv.5
    assert ana.level == 5
    assert (bia.given, bia.received) == (1, 1) and bia.level == 10
    assert (caio.given, caio.received) == (1, 0) and caio.level == 10


async def test_match_by_external_id(db):
    await add_member(db, 1, "ana")
    await add_member(db, 2, "bia")
    await db.execute("UPDATE handles SET external_id='999' WHERE user_id=2")
    p = await add_post(db, 1, "AAA")
    # Bia trocou o @ na plataforma, mas o ID interno continua o mesmo
    assert await record_comments(db, p, "instagram", [Comment("c1", "bia_nova", "999", None)]) == [2]


async def test_old_posts_leave_window(db):
    await add_member(db, 1, "ana")
    await add_member(db, 2, "bia")
    p = await add_post(db, 1, "OLD")
    await db.execute("UPDATE posts SET created_at=? WHERE id=?", (int(time.time()) - 40 * 86400, p))
    await record_comments(db, p, "instagram", [Comment("c1", "bia", None, None)])
    scores = await compute_scores(db, 30, 1, 1.0)
    assert scores[2].given == 0


async def test_recruiter_quality(db):
    for uid, ig in [(1, "rec"), (2, "a"), (3, "b"), (4, "c")]:
        await add_member(db, uid, ig)
    await db.upsert_member(5, "en")  # entrou mas nunca verificou
    for uid in (2, 3, 4, 5):
        await db.execute("INSERT INTO recruits(user_id, inviter_id, joined_at) VALUES(?,1,0)", (uid,))
    await db.execute("INSERT INTO scores(user_id, given) VALUES(2, 10), (3, 6), (4, 1)")
    st = (await recruiter_stats(db, min_activity=5))[1]
    assert (st.total, st.active, st.verified_inactive) == (4, 2, 1)
    assert st.score == 2.25


# ---------------- Mutual Follow ----------------
from backline.services import (add_follow_claims, mark_follow, mutual_candidates, mutual_stats,  # noqa: E402
                               pending_follow_claims, is_followed_back)


async def join_mutual(db, uid):
    await db.execute("INSERT INTO mutual_members(user_id, active, joined_at) VALUES(?,1,0)", (uid,))


async def test_mutual_flow(db):
    for uid, ig in [(1, "ana"), (2, "bia"), (3, "caio")]:
        await add_member(db, uid, ig)
    for uid in (1, 2):
        await join_mutual(db, uid)
    # Caio não entrou no Mutual Follow: não aparece pra ninguém
    cands = await mutual_candidates(db, 1)
    assert [(c.user_id, c.platform) for c in cands] == [(2, "instagram")]
    assert await mutual_candidates(db, 3) == []

    await add_follow_claims(db, 1, [(2, "instagram")])
    assert await mutual_candidates(db, 1) == []          # já marcado, sai da lista
    await db.execute("UPDATE follows SET claimed_at = 0")
    pend = await pending_follow_claims(db)
    assert len(pend) == 1 and pend[0]["follower_handle"] == "ana" and pend[0]["followee_handle"] == "bia"

    await mark_follow(db, 1, 2, "instagram", "verified")
    assert not await is_followed_back(db, 1, 2, "instagram")
    # Bia vê a Ana no topo, marcada como "já te segue"
    cands = await mutual_candidates(db, 2)
    assert cands[0].user_id == 1 and cands[0].follows_me
    st = await mutual_stats(db, 2)
    assert (st.followers, st.following, st.mutual, len(st.to_return)) == (1, 0, 0, 1)

    await add_follow_claims(db, 2, [(1, "instagram")])
    await mark_follow(db, 2, 1, "instagram", "verified")
    assert await is_followed_back(db, 2, 1, "instagram")
    st = await mutual_stats(db, 1)
    assert (st.followers, st.following, st.mutual, len(st.to_return)) == (1, 1, 1, 0)


async def test_failed_claim_can_be_retried(db):
    for uid, ig in [(1, "ana"), (2, "bia")]:
        await add_member(db, uid, ig)
        await join_mutual(db, uid)
    await add_follow_claims(db, 1, [(2, "instagram")])
    await mark_follow(db, 1, 2, "instagram", "failed")
    assert [c.user_id for c in await mutual_candidates(db, 1)] == [2]   # volta pra lista
    await add_follow_claims(db, 1, [(2, "instagram")])
    row = await db.fetchone("SELECT status, attempts FROM follows")
    assert (row["status"], row["attempts"]) == ("pending", 0)


# ---------------- Discover ----------------
def test_discover_pick_rules():
    import random
    from backline.discover import Candidate, Viewer, pick, DAY
    now_ = 1_000_000_000
    cands = [Candidate(post_id=i, user_id=100 + i % 6, created_at=now_ - i * DAY, level=1 + i % 10,
                       genres={"Pop"} if i % 2 else {"Rock"}) for i in range(30)]
    cands.append(Candidate(post_id=99, user_id=1, created_at=now_))  # do próprio membro
    v = Viewer(user_id=1, genres={"Pop"})
    for seed in range(50):
        got = pick(cands, v, now_, n=4, rng=random.Random(seed))
        assert len(got) == 4
        assert all(c.user_id != 1 for c in got)
        assert len({c.user_id for c in got}) == 4  # um post por artista


def test_discover_weights_favor_participation_and_boost():
    import random
    from collections import Counter
    from backline.discover import Candidate, Viewer, pick
    now_ = 1_000_000_000
    hi = Candidate(post_id=1, user_id=10, created_at=now_, level=10, genres={"Pop"})
    lo = Candidate(post_id=2, user_id=11, created_at=now_, level=1, genres={"Pop"})
    boosted = Candidate(post_id=3, user_id=12, created_at=now_, level=1, genres={"Pop"}, boost=3.0)
    v = Viewer(user_id=1, genres={"Pop"})
    first = Counter(pick([hi, lo, boosted], v, now_, n=1, rng=random.Random(s))[0].post_id for s in range(2000))
    assert first[1] > first[2]          # participação pesa
    assert first[3] > first[2]          # impulso pesa
    assert first[2] > 0                 # nível baixo ainda aparece


@pytest.mark.asyncio
async def test_discover_candidates_query(db):
    from backline.cogs.discover import Discover
    t = now()
    for uid in (1, 2, 3):
        await db.upsert_member(uid, "en")
        await db.execute("UPDATE members SET verified_at=? WHERE user_id=?", (t, uid))
    await db.execute("INSERT INTO posts(user_id, platform, url, external_id, created_at) VALUES(2,'youtube','u','a',?)", (t,))
    await db.execute("INSERT INTO posts(user_id, platform, url, external_id, created_at) VALUES(3,'tiktok','u2','b',?)", (t,))
    await db.execute("INSERT INTO member_genres(user_id, genre) VALUES(2,'Pop')")
    for uid in (2, 3):   # Discover só mostra Lv.3+
        await db.execute("INSERT INTO scores(user_id, level, updated_at) VALUES(?,5,?)", (uid, t))
    await db.execute("INSERT INTO ratings(rater_id, post_id, stars, rated_at) VALUES(1,1,4,?)", (t,))
    await db.execute("INSERT INTO ratings(rater_id, post_id, stars, rated_at) VALUES(1,1,5,?) "
                     "ON CONFLICT (rater_id, post_id) DO UPDATE SET stars=excluded.stars", (t,))
    await db.execute("INSERT INTO boosts(user_id, multiplier, until) VALUES(3, 2.0, ?)", (t + 100,))
    await db.execute("INSERT INTO discover_seen(user_id, post_id, seen_at) VALUES(1,2,?)", (t,))

    class B:
        pass
    b = B(); b.db = db; b.add_view = lambda v: None
    cog = Discover.__new__(Discover); cog.bot = b
    cands = {c.post_id: c for c in await cog.candidates(1)}
    assert cands[1].genres == {"Pop"} and cands[1].avg_rating == 5.0
    assert cands[2].boost == 2.0 and cands[2].seen_at == t


def test_collect_interval_schedule():
    from backline.services import collect_interval, DAY
    assert collect_interval("youtube", 3 * DAY) == 1800
    assert collect_interval("instagram", 3600) == 3600
    assert collect_interval("instagram", 2 * DAY) == 6 * 3600
