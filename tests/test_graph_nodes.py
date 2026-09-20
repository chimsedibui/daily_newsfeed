"""Test cac node thuan logic - khong can Postgres, khong goi LLM.

Day la ly do moi node nhan `config` va lay trace_store tu do: truyen `{}` la
node chay o che do khong trace.
"""
from datetime import UTC, datetime, timedelta

from news_bot.graph.nodes.dedupe import cluster_articles
from news_bot.graph.nodes.rank import rank_clusters, score_article
from news_bot.models import Article
from news_bot.utils import content_hash, simhash

NOW = datetime.now(UTC)


def make(title: str, publisher: str = "VnExpress", hours_ago: float = 1.0,
         weight: float = 1.0, aid: int = 1) -> Article:
    return Article(
        id=aid,
        source_id=publisher.lower(),
        publisher=publisher,
        title=title,
        url_canonical=f"https://x.vn/{aid}",
        url_original=f"https://x.vn/{aid}",
        weight=weight,
        published_at=NOW - timedelta(hours=hours_ago),
        content_hash=content_hash(title),
        simhash=simhash(title),
    )


def test_cluster_merges_same_event_across_publishers():
    a = make("Ngan hang Nha nuoc giam lai suat dieu hanh them 0,5 diem phan tram",
             "VnExpress", aid=1)
    b = make("Ngan hang Nha nuoc giam lai suat dieu hanh them 0,5 diem phan tram hom nay",
             "Tuoi Tre", aid=2)
    c = make("Doi tuyen Viet Nam thang 3-0 trong tran giao huu", "Thanh Nien", aid=3)

    out = cluster_articles({"articles": [a, b, c]}, {})
    assert out["metrics"]["clusters"] == 2
    assert out["metrics"]["duplicates_merged"] == 1


def test_cluster_keeps_unrelated_news_separate():
    titles = [
        "Gia xang dau tang manh tu chieu nay",
        "Dai hoc Bach khoa cong bo diem chuan",
        "Bao so 5 do bo vao khu vuc Trung Bo",
        "Cong ty cong nghe Nhat mo trung tam nghien cuu tai Ha Noi",
        "Doi tuyen bong chuyen nu gianh huy chuong bac",
    ]
    arts = [make(t, aid=i) for i, t in enumerate(titles)]
    assert cluster_articles({"articles": arts}, {})["metrics"]["clusters"] == 5


def test_rank_prefers_recent_corroborated_and_keyword_hits():
    fresh_ai = make("Viet Nam cong bo chien luoc trí tuệ nhân tạo quoc gia",
                    hours_ago=0.5, weight=1.4, aid=1)
    old_misc = make("Thoi tiet cuoi tuan co mua rai rac", hours_ago=30, aid=2)

    s_fresh = score_article(fresh_ai, cluster_size=4, now=NOW)
    s_old = score_article(old_misc, cluster_size=1, now=NOW)
    assert s_fresh > s_old


def test_rank_records_cluster_metadata_on_representative():
    a = make("Tin quan trong ve lãi suất", "VnExpress", aid=1)
    b = make("Tin quan trong ve lãi suất", "Tuoi Tre", aid=2)
    out = rank_clusters({"clusters": [[a, b]]}, {})
    head = out["shortlist"][0]
    assert head.raw["cluster_size"] == 2
    assert head.raw["also_at"] == ["Tuoi Tre"]
    assert head.raw["heuristic_rank"] == 1
