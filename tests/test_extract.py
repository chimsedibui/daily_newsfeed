from news_bot.config import Source
from news_bot.sources.extract import extract_article
from news_bot.sources.feeds import parse_rss

# Rut gon tu HTML that cua vnexpress.net (JSON-LD NewsArticle + body p.Normal).
ARTICLE_HTML = """
<html lang="vi"><head>
<meta property="og:title" content="Tieu de tu og">
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"NewsArticle",
 "headline":"Hang chuc nghin tu lanh hong chat dong tai Anh",
 "description":"Cong ty tai che Unimetals de don giai the.",
 "datePublished":"2026-09-20T15:10:00+07:00",
 "author":{"@type":"Organization","name":"VnExpress"},
 "image":{"url":"https://i1-vne.vnecdn.net/anh.jpg"}}
</script>
<script type="application/ld+json">{"@type":"BreadcrumbList","itemListElement":[]}</script>
</head><body><article>
<p class="Normal">Unimetals tung la don vi tai che tu lanh lon nhat nuoc Anh, xu ly
khoang mot phan ba luong thiet bi thai bo moi nam trong suot mot thap ky qua.</p>
<p class="Normal">Sau khi cong ty de don giai the vao cuoi nam ngoai, cac hoi dong
dia phuong khong tim duoc don vi thay the va phai chat dong thiet bi tai kho bai.</p>
<p class="Normal">Co quan moi truong cho biet dang lam viec voi cac ben lien quan de
tim phuong an xu ly trong quy toi, tranh nguy co ro ri khi gas ra moi truong.</p>
</article></body></html>
"""


def test_extract_prefers_jsonld_over_meta():
    data = extract_article(ARTICLE_HTML, "https://vnexpress.net/x-123.html")
    assert data["title"].startswith("Hang chuc nghin")
    assert data["author"] == "VnExpress"
    assert data["published_at"].year == 2026
    assert data["image_url"].endswith("anh.jpg")
    assert data["lang"] == "vi"


def test_extract_falls_back_to_dom_for_body():
    data = extract_article(ARTICLE_HTML, "https://vnexpress.net/x-123.html")
    assert "Unimetals" in data["body"]
    assert data["body"].count("\n\n") >= 1          # gom duoc nhieu doan
    assert "dom" in data["source_kind"]


def test_extract_survives_garbage_jsonld():
    html = '<html><head><script type="application/ld+json">{oops</script>' \
           '<meta property="og:title" content="Van doc duoc"></head><body></body></html>'
    assert extract_article(html, "https://x.vn/a")["title"] == "Van doc duoc"


RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<item><title>Tin A</title><link>https://vnexpress.net/tin-a-1.html</link>
<description><![CDATA[<a href="x"><img src="https://cdn/x.jpg"></a>Mo ta A]]></description>
<pubDate>Sun, 20 Sep 2026 15:10:24 +0700</pubDate></item>
<item><title>Tin B</title><link>https://vnexpress.net/tin-b-2.html</link>
<description>Mo ta B</description></item>
<item><title>Khong co link</title><link></link></item>
</channel></rss>"""


def test_parse_rss_extracts_items_and_skips_broken():
    src = Source(id="s", publisher="VnExpress", url="https://x", category="tong-hop")
    items = parse_rss(RSS, src)
    assert [i.title for i in items] == ["Tin A", "Tin B"]
    assert items[0].image_url == "https://cdn/x.jpg"
    assert items[0].lead == "Mo ta A"           # HTML trong description bi lot bo
    assert items[0].published_at.year == 2026
    assert items[1].published_at is None


DOUBLE_ESCAPED = """
<html><head><script type="application/ld+json">
{"@type":"NewsArticle","headline":"Nang hang: Mo &amp;quot;canh cua&amp;quot; von ngoai",
 "description":"Von ngoai &amp;amp; co hoi","datePublished":"2026-09-20T10:23:00"}
</script></head><body></body></html>
"""


def test_extract_unescapes_double_encoded_entities():
    data = extract_article(DOUBLE_ESCAPED, "https://cafef.vn/a.html")
    assert data["title"] == 'Nang hang: Mo "canh cua" von ngoai'
    assert data["lead"] == "Von ngoai & co hoi"


def test_extract_keeps_naive_datetime_for_caller_to_localize():
    # CafeF khong kem offset; collector moi la noi gan mui gio nghiep vu.
    data = extract_article(DOUBLE_ESCAPED, "https://cafef.vn/a.html")
    assert data["published_at"].tzinfo is None
