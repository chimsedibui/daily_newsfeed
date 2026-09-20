"""Test viec chia nhom: xep hang rieng tung nhom, tran tong so tin, parser HF."""
import json
from datetime import UTC, datetime, timedelta

from news_bot.config import Source
from news_bot.graph.nodes.rank import rank_clusters, score_article
from news_bot.graph.nodes.render import _apply_total_cap
from news_bot.models import Article, Digest, DigestItem
from news_bot.sources.feeds import parse_hf_papers
from news_bot.utils import content_hash, simhash

NOW = datetime.now(UTC)


def make(title: str, group: str = "serious", hours_ago: float = 1.0,
         weight: float = 1.0, aid: int = 1) -> Article:
    return Article(
        id=aid,
        source_id="s",
        publisher="P",
        group=group,
        title=title,
        url_canonical=f"https://x.vn/{aid}",
        url_original=f"https://x.vn/{aid}",
        weight=weight,
        published_at=NOW - timedelta(hours=hours_ago),
        content_hash=content_hash(title),
        simhash=simhash(title),
    )


def test_keyword_boost_is_scoped_to_its_group():
    """'bóng chuyền' chi duoc cong diem trong nhom life, khong phai serious."""
    life = make("Tuyển bóng chuyền nữ thắng trận ra quân", group="life", aid=1)
    same_in_serious = make("Tuyển bóng chuyền nữ thắng trận ra quân",
                           group="serious", aid=2)
    assert score_article(life, 1, NOW) > score_article(same_in_serious, 1, NOW)


def test_fed_keyword_only_counts_for_serious():
    fed = make("FED giữ nguyên lãi suất điều hành", group="serious", aid=1)
    fed_life = make("FED giữ nguyên lãi suất điều hành", group="life", aid=2)
    assert score_article(fed, 1, NOW) > score_article(fed_life, 1, NOW)


def test_penalty_pushes_gossip_down_in_life():
    normal = make("Honda ra mắt mẫu xe máy điện mới", group="life", aid=1)
    gossip = make("Sao Việt lộ ảnh gây sốt", group="life", aid=2)
    assert score_article(normal, 1, NOW) > score_article(gossip, 1, NOW)


def test_shortlist_is_cut_per_group_not_globally():
    """Tin serious luon ghi diem cao hon, cat chung mot lan thi life mat sach cho."""
    clusters = [[make(f"FED lãi suất thông báo số {i}", group="serious",
                      hours_ago=0.5, weight=2.0, aid=i)] for i in range(40)]
    clusters += [[make(f"Ra mắt mẫu xe máy số {i}", group="life",
                       hours_ago=20, aid=100 + i)] for i in range(10)]

    out = rank_clusters({"clusters": clusters}, {})
    per_group = out["metrics"]["shortlist_per_group"]
    assert per_group["life"] > 0, "nhom life bi nhom serious at het cho"
    assert per_group["serious"] == 24      # 12 tin x 2 ung vien
    assert per_group["life"] == 10         # chi co 10 bai, lay het


def _digest(group: str, n: int) -> Digest:
    return Digest(
        group=group,
        digest_date="2026-09-20",
        headline="h",
        overview="o",
        items=[
            DigestItem(rank=i, headline=f"t{i}", summary="s",
                       url=f"https://x/{group}/{i}", publisher="P")
            for i in range(1, n + 1)
        ],
    )


def test_total_cap_trims_life_before_serious():
    digests = _apply_total_cap([_digest("serious", 12), _digest("life", 6)], cap=15)
    by_group = {d.group: len(d.items) for d in digests}
    assert by_group == {"serious": 12, "life": 3}


def test_total_cap_keeps_ranks_contiguous_after_trimming():
    digests = _apply_total_cap([_digest("serious", 12), _digest("life", 6)], cap=15)
    life = next(d for d in digests if d.group == "life")
    assert [i.rank for i in life.items] == [1, 2, 3]


def test_total_cap_is_a_noop_when_under_budget():
    digests = _apply_total_cap([_digest("serious", 10), _digest("life", 5)], cap=20)
    assert sum(len(d.items) for d in digests) == 15


HF_PAYLOAD = json.dumps([
    {
        "publishedAt": "2026-09-16T20:00:00.000Z",
        "paper": {
            "id": "2609.19656",
            "title": "Self-Evolving Search Index",
            "summary": "We introduce a method for ...",
            "upvotes": 40,
        },
    },
    {"paper": {"id": "2609.19499", "title": "Sample Count Is Not Enough",
               "summary": "abc", "upvotes": 0}},
    {"paper": {"title": "Thieu id nen bi bo qua"}},
]).encode()


def test_hf_papers_parser_maps_upvotes_into_weight():
    src = Source(id="hf", publisher="HF", url="https://x", strategy="hf_papers",
                 group="serious", weight=1.6)
    items = parse_hf_papers(HF_PAYLOAD, src)
    assert len(items) == 2, "muc thieu arxiv id phai bi bo"
    top, low = items
    assert top.weight > low.weight          # 40 upvote duoc cong diem
    assert low.weight == 1.6                # 0 upvote giu nguyen weight nguon
    assert top.url == "https://huggingface.co/papers/2609.19656"
    assert top.raw["arxiv_url"] == "https://arxiv.org/abs/2609.19656"
    assert top.group == "serious"


def test_hf_papers_parser_survives_garbage():
    src = Source(id="hf", publisher="HF", url="https://x", strategy="hf_papers")
    assert parse_hf_papers(b"{not json", src) == []
    assert parse_hf_papers(b'{"error": "nope"}', src) == []


def test_stale_threshold_scales_with_source_cadence():
    """Newsletter tuan im 12 ngay la binh thuong; feed hang ngay thi khong."""
    from news_bot.sources.collector import CollectResult

    weekly = CollectResult(stale_after_h=int(336 * 1.5))
    weekly.newest_item_age_h = 309
    assert not weekly.stale

    daily = CollectResult(stale_after_h=168)
    daily.newest_item_age_h = 309
    assert daily.stale


def test_stale_is_false_when_no_dated_items():
    from news_bot.sources.collector import CollectResult

    res = CollectResult()
    assert res.newest_item_age_h is None
    assert not res.stale


def test_purge_days_zero_is_not_swallowed(monkeypatch):
    """days=0 la gia tri hop le (xoa tat ca) nhung falsy - de bi `or` nuot."""
    from news_bot import retention

    captured = {}

    class FakeResult:
        rowcount = 0

        @staticmethod
        def scalar():
            return 0

    class FakeSession:
        def execute(self, stmt, params=None):
            captured.update(params or {})
            return FakeResult()

    class FakeScope:
        def __enter__(self):
            return FakeSession()

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(retention, "session_scope", lambda: FakeScope())
    monkeypatch.setattr(retention, "db_size", lambda: {"total": "0 kB", "tables": []})

    out = retention.purge(days=0, dry_run=True)
    assert out["days"] == 0
    assert captured["days"] == 0, "days=0 bi thay bang mac dinh"


def test_purge_days_none_uses_configured_default(monkeypatch):
    from news_bot import retention

    captured = {}

    class FakeResult:
        rowcount = 0

        @staticmethod
        def scalar():
            return 0

    class FakeSession:
        def execute(self, stmt, params=None):
            captured.update(params or {})
            return FakeResult()

    class FakeScope:
        def __enter__(self):
            return FakeSession()

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(retention, "session_scope", lambda: FakeScope())
    monkeypatch.setattr(retention, "db_size", lambda: {"total": "0 kB", "tables": []})

    out = retention.purge(days=None, dry_run=True)
    assert out["days"] == retention.get_settings().retention_days
    assert captured["days"] == out["days"]
