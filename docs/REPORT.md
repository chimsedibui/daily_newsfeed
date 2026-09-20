# Báo cáo: nguồn dữ liệu báo chí VN & kiến trúc bot bản tin

Ngày khảo sát: **20/09/2026**. Mọi endpoint trong báo cáo này đều được gọi thật, không suy đoán.

---

## 1. Kết luận ngắn

| Câu hỏi | Trả lời |
|---|---|
| VnExpress có API công khai không? | **Không.** Không có API documented. `gw.vnexpress.net` tồn tại nhưng mọi path thử đều trả `404 page not found` — đây là gateway nội bộ, không phải hợp đồng công khai. |
| Vậy lấy tin bằng gì? | **RSS** cho danh sách + **SSR HTML (JSON-LD `NewsArticle`)** cho nội dung. Đây là hai bề mặt duy nhất các báo chủ động công bố. |
| Có chung một cách trích xuất cho mọi báo không? | **Có.** Cả 6 báo khảo sát đều nhúng `schema.org/NewsArticle` trong `<script type="application/ld+json">`. Không cần viết selector riêng cho từng báo. |
| Rủi ro lớn nhất? | Feed **"chết lâm sàng"**: trả HTTP 200 nhưng nội dung đóng băng nhiều tháng. Khảo sát này bắt được 2 trường hợp như vậy. |

---

## 2. Khảo sát nguồn tin

### 2.1 VnExpress

**Không có API công khai.** Đã thử `gw.vnexpress.net/ar/get_rectangle_home` và `gw.vnexpress.net/ar/get_setting` — host phản hồi nhưng cả hai đều `404 page not found`. Kể cả nếu dò ra path đúng, đây là API nội bộ: không có cam kết tương thích, không có rate limit công bố, và dùng nó là rủi ro cả về kỹ thuật lẫn điều khoản sử dụng. **Không khuyến nghị.**

**RSS** (`application/xml; charset=utf-8`) là bề mặt chính thức, ổn định. Trang `vnexpress.net/rss` liệt kê **23 chuyên mục**:

```
tin-moi-nhat  tin-noi-bat  tin-xem-nhieu  thoi-su  the-gioi  kinh-doanh
startup  khoa-hoc-cong-nghe  giai-tri  the-thao  phap-luat  giao-duc
suc-khoe  du-lich  gia-dinh  bat-dong-san  oto-xe-may  goc-nhin
y-kien  tam-su  thu-gian  spotlight  vne-go
```

Lưu ý: cấu trúc chuyên mục đã đổi — `so-hoa.rss` nay **301** sang `khoa-hoc-cong-nghe.rss`. Bản tiếng Anh: `e.vnexpress.net/rss/news.rss`. Sitemap index: `vnexpress.net/sitemap.xml`.

**Trang bài viết** là SSR thuần (273 KB HTML), chứa 2 khối JSON-LD. Khối `NewsArticle` đầy đủ:

```json
{"@type":"NewsArticle",
 "headline":"...", "description":"...",
 "datePublished":"2026-09-20T15:10:00+07:00",
 "dateModified":"...",
 "author":{"@type":"Organization","name":"VnExpress"},
 "image":{...}, "thumbnailUrl":"...", "publisher":{...}}
```

Ngoài ra có `<meta name="tt_site_id">`, `tt_category_id` để biết chuyên mục nội bộ, và thân bài nằm trong `<p class="Normal">`.

### 2.2 Toàn cảnh các báo

Trạng thái đo ngày 20/09/2026:

| Báo | Endpoint dùng được | Ghi chú |
|---|---|---|
| **VnExpress** | `vnexpress.net/rss/<mục>.rss` | 23 chuyên mục, nhanh nhất (~50 ms) |
| **Tuổi Trẻ** | `tuoitre.vn/home.rss`, `tuoitre.vn/rss/<mục>.rss` | `/rss/tin-moi-nhat.rss` **301** về `/home.rss`; có namespace `media:` cho ảnh |
| **Thanh Niên** | `thanhnien.vn/rss/home.rss` | có `content:encoded` |
| **Dân Trí** | `dantri.com.vn/rss/home.rss`, `/rss/cong-nghe.rss` | feed lớn (113 KB) |
| **VietnamNet** | `vietnamnet.vn/<mục>.rss` | **không có** tiền tố `/rss/` (chỉ 301 về gốc); không có feed "tin mới nhất", phải đi theo chuyên mục; feed nặng (~1.1 MB) |
| **CafeF** | `cafef.vn/<mục>.rss` | `trang-chu.rss` → 404, phải chỉ đích danh chuyên mục |
| **ZNews** | ✗ | `/rss` trả **403**, sitemap trả 404 + body nén sai. Phải dùng HTML nếu cần. |
| **VnEconomy** | ✗ | `/rss/tin-moi.rss` trả 200 nhưng `<title>No Content</title>`, channel rỗng |
| **Vietstock** | (thủ công) | `/rss` là trang HTML liệt kê feed, không phải feed |

### 2.3 Hai feed đã chết — phát hiện trong lúc khảo sát

| Feed | HTTP | Bài mới nhất | Thực tế |
|---|---|---|---|
| `dantri.com.vn/rss/suc-manh-so.rss` | 200 | 16/02/2025 | Bài đầu tiên trong feed ghi rõ *"Chuyên mục Sức mạnh số đổi tên thành Công nghệ"* → phải dùng `/rss/cong-nghe.rss` |
| `vietnamnet.vn/cong-nghe.rss` | 200 | 10/03/2025 | Bỏ hoang. Thay bằng `kinh-doanh.rss` (đang cập nhật theo phút) |

Đây là lý do hệ thống ghi cột `source_health.newest_item_age_h`: một feed chết vẫn trả 200, health check theo HTTP status sẽ **không** bắt được. Bài mới nhất quá 7 ngày → đánh dấu `stale`, DAG hạ trạng thái run xuống `partial`, `news-bot check-sources` thoát với mã lỗi.

### 2.4 Vì sao không crawl, không dùng API nội bộ

RSS + SSR đủ dùng và là bề mặt công khai. Cụ thể: 14 feed đang cấu hình cho ra **~290 bài/24h**, mỗi feed mất 7–550 ms. Pipeline chỉ fetch fulltext cho ~24 bài lọt shortlist, tức khoảng 24 request/ngày tới trang bài viết — tải không đáng kể với bất kỳ báo nào. Giữ `User-Agent` khai báo rõ ràng và có địa chỉ liên hệ.

---

## 3. Kiến trúc & lý do chọn

### 3.1 Trích xuất nội dung: 3 tầng, không selector riêng từng báo

[`sources/extract.py`](../src/news_bot/sources/extract.py):

1. **JSON-LD `NewsArticle`** — chính xác nhất, có `datePublished`, `author`, `image`.
2. **`<meta>` og:/article:** — khi thiếu JSON-LD.
3. **Heuristic DOM** — chọn khối có tổng độ dài `<p>` lớn nhất.

Selector riêng cho từng báo là thứ vỡ đầu tiên khi báo đổi giao diện; `schema.org` thì các báo tự giữ vì nó phục vụ Google News. Đo thực tế: **6/6 bài** lấy được body, kiểu trích xuất `json-ld+dom`, thân bài 2.8–7.3 nghìn ký tự.

Hai lỗi thật đã xử lý trong lúc chạy thử:

- **Entity escape hai lần**: CafeF trả `Mở &amp;quot;cánh cửa&amp;quot;` trong JSON-LD → `_clean()` unescape lặp cho tới khi ổn định.
- **Datetime naive**: CafeF/VietnamNet ghi `datePublished` không kèm offset. Để nguyên thì `astimezone()` suy diễn theo giờ của server; chạy trên container UTC sẽ lệch 7 tiếng và làm sai điểm độ-mới khi xếp hạng. `ensure_aware()` gán `Asia/Ho_Chi_Minh` tại tầng collector.

### 3.2 Gom trùng: hai tầng

- **Tầng DB**: `canonical_url()` bỏ `utm_*`/`fbclid`, hạ `www.`/`amp.`, sắp xếp query → `UNIQUE(url_canonical)` lo trùng tuyệt đối.
- **Tầng nội dung**: **SimHash 64-bit** trên 2-gram, Hamming ≤ 12, cộng Jaccard tiêu đề ≥ 0.55 → bắt trường hợp 5 báo cùng đưa một tin. Tự cài, không cần thư viện ngoài.

Đo thực tế: 287 bài → **271 cụm**, gộp 16 bài trùng. Bài đại diện là bài có body đầy đủ nhất ở nguồn `weight` cao; các báo còn lại hiện thành "cùng đưa tin".

### 3.3 Xếp hạng bằng heuristic, không bằng LLM

```
điểm = độ_mới × trọng_số_nguồn + độ_phủ_nhiều_báo + từ_khoá_ưu_tiên + độ_sâu
```

Cho LLM xếp hạng 290 bài mỗi ngày vừa đắt vừa chậm. Heuristic cắt còn `2 × digest_size` (24 bài) rồi mới để LLM làm việc nó giỏi hơn: **tóm tắt** và **biên tập**. Độ phủ nhiều báo là tín hiệu mạnh — một sự kiện được 5 báo cùng đưa gần như chắc chắn quan trọng.

### 3.4 LangGraph: node thuần, trace qua `config`

Mỗi node là `(state, config) -> partial state`. `TraceStore` được truyền qua `config["configurable"]["trace_store"]`, nên **truyền `config={}` là tắt trace** — đó là cách test node mà không cần Postgres. State dùng reducer (`Annotated`) cho `metrics`/`errors` để node chạy song song không ghi đè nhau.

Tách `compose` khỏi `render`, và `render` khỏi `deliver`: webhook hỏng thì retry được task `deliver` mà **không phải gọi lại LLM**. Đây là ranh giới đắt nhất trong pipeline.

`compose` có đường lùi: nếu LLM lỗi hoặc trả JSON hỏng, bản tin vẫn ra theo `importance` (`mode=fallback`). Id do model trả về được kiểm tra nằm trong khoảng hợp lệ và khử trùng lặp trước khi dùng — model không bịa được bài.

### 3.5 Trace & logging trong Postgres

Không phụ thuộc LangSmith/Langfuse. Bảy bảng, cây span dựng bằng `parent_span_id`, span id giữ trong `contextvar` nên log và `llm_call` tự gắn đúng parent mà không phải truyền tay xuống từng hàm.

| Bảng | Dùng để |
|---|---|
| `pipeline_run` | 1 dòng/lần chạy: trạng thái, thời lượng, snapshot cấu hình, metrics |
| `node_span` | 1 dòng/node: thời lượng, input/output preview (cắt 2 KB), lỗi |
| `llm_call` | model, purpose, token vào/ra/cache, `cost_usd`, độ trễ |
| `app_log` | structlog ghi song song ra stdout (JSON) và vào DB |
| `source_health` | theo từng nguồn từng run: HTTP, số bài, độ trễ, `newest_item_age_h` |
| `article`, `article_summary`, `digest` | dữ liệu nghiệp vụ |

View `v_run_overview` gộp sẵn: số tin, số span, chi phí LLM, số nguồn hỏng, số nguồn đóng băng.

**Chi phí** quy đổi trong [`llm.py`](../src/news_bot/llm.py) theo bảng giá OpenAI API (đọc 20/09/2026, bậc short context, tier Standard): `gpt-6-astra` $10/$50 per MTok, `gpt-5.6-sol` $4/$20, `gpt-5.6-terra` $2/$12, `gpt-5.6-luna` $0.20/$1.20. Bậc long context đắt gấp đôi. Model không có trong bảng trả `0` thay vì đoán bừa.

### 3.6 Airflow

`schedule="0 8 * * 1-5"`, timezone `Asia/Ho_Chi_Minh`, `catchup=False`, `max_active_runs=1`.

- **Fan-out theo nguồn** bằng `.expand()` → retry riêng từng feed, nhìn thấy ngay trên UI feed nào chết.
- **`check_sources`** chặn trường hợp cả loạt feed đổi URL: dưới ngưỡng `news_min_source_success_ratio` (Airflow Variable, mặc định 0.5) thì fail cả DAG.
- **`deliver`** retry 3 lần, cách nhau 2 phút — riêng cho lỗi mạng của webhook.
- **`finalize`** dùng `TriggerRule.ALL_DONE` → `pipeline_run` không bao giờ kẹt ở trạng thái `running`.

### 3.7 Google Chat

`cardsV2`: header + 1 section/tin (nhãn báo, "cùng đưa tin", tóm tắt, nút "Đọc bài") + footer thống kê. Giới hạn ~32 KB/message nên `split_message()` cắt theo section và đánh số `(1/2)`. Nội dung được escape HTML — card dùng subset HTML nên tiêu đề chứa `<` sẽ phá layout. `threadKey` theo ngày gom bản tin vào một thread.

Đo thực tế: bản tin 6 tin = **4.9 KB**, 1 message.

---

## 4. Chi phí — số đo thật

Một lần chạy đầy đủ ngày 20/09/2026, lấy thẳng từ bảng `llm_call`:

| Model | Việc | Lần gọi | Token vào | Token ra | USD | Độ trễ TB |
|---|---|---|---|---|---|---|
| `gpt-5.6-terra` | tóm tắt | 24 | 37.199 | 5.427 | $0,1395 | 3,4 s |
| `gpt-6-astra` | biên tập | 1 | 3.500 | 797 | $0,0749 | 15,8 s |
| | **tổng** | **25** | **40.699** | **6.224** | **$0,2144** | |

**$0,21/ngày ≈ $4,3/tháng** (20 ngày làm việc). Cần gạt chi phí rõ nhất: đổi
`SUMMARIZER_MODEL=gpt-5.6-luna` ($0,20/$1,20 thay vì $2/$12) — bước tóm tắt rơi
xuống khoảng $0,014, tổng còn **~$0,09/ngày ≈ $1,8/tháng**. Đo lại bằng
`llm_call.cost_usd` sau khi đổi rồi hãy chốt.

Ban đầu stack viết cho Claude; chuyển sang OpenAI theo yêu cầu vì key sẵn có là
key OpenAI. Chỉ `llm.py` phải sửa — LangGraph, prompt và structured output đi qua
trừu tượng của LangChain nên giữ nguyên.

## 5. Hiện trạng & việc còn lại

**Đã chạy thật, đã kiểm chứng**

- 14/14 feed sống, thu 287–300 bài/24h.
- Gom trùng, xếp hạng, trích xuất fulltext, render card: chạy trên tin thật.
- 22 unit test xanh, `ruff` sạch. Test không cần Postgres, không gọi LLM.

**Đã chạy end-to-end trên WSL** (20/09/2026)

Không cài được Docker (không có quyền admin), nên stack được dựng bằng PostgreSQL 17.11
chạy rootless và Airflow trong venv riêng — chi tiết ở [SETUP-WSL.md](SETUP-WSL.md).
Phần trước đây chưa kiểm chứng nay đã chạy thật:

| Hạng mục | Kết quả |
|---|---|
| `sql/001_init.sql` | 8 bảng + view, áp dụng lại nhiều lần không lỗi |
| `repository.py` | UPSERT nhận đúng bài mới: run 1 = 274 bài, run 2 = 1 bài |
| LangGraph qua CLI | 274 bài → 269 cụm → 24 shortlist → digest 12 tin, 6,7 s |
| DAG trên Airflow | 7 task, 14 ingest map song song, toàn bộ SUCCESS, ~29 s |
| Trace | 7 span/run, `source_health` đủ 14 dòng, `dag_run_id` ghi đúng |

**Bốn khiếm khuyết lộ ra khi triển khai và đã sửa**

1. **Airflow ghim `sqlalchemy<2.0`.** `_PIP_ADDITIONAL_REQUIREMENTS` trong
   docker-compose bản đầu cài app thẳng vào môi trường Airflow — làm vậy thì
   SQLAlchemy bị hạ xuống 1.4 và app chết với `NoSuchModuleError:
   sqlalchemy.dialects:postgresql.psycopg`. Đã tách hai venv, DAG dùng
   `@task.external_python`, và thêm `Dockerfile.airflow` làm đúng như vậy trong container.
2. **`pipeline_run.duration_ms` luôn bằng 0.** Nó tính bằng đồng hồ trong tiến
   trình, mà Airflow gọi `finalize` ở một task/tiến trình khác với task tạo run.
   Nay tính bằng SQL từ `started_at`.
3. **View `v_run_overview` đọc khoá metrics không tồn tại** (`articles_collected`).
   Nay lấy số bài mới thẳng từ `source_health` — dùng được cả khi run chết giữa chừng.
   Kèm theo: `CREATE OR REPLACE VIEW` không đổi được danh sách cột, phải `DROP` trước.
4. **`DRY_RUN` đánh dấu bản tin là `sent`.** Không gửi gì mà đánh dấu đã gửi thì
   bộ lọc `already_sent_urls()` loại vĩnh viễn các bài đó khỏi bản tin hôm sau.
   Nay là `skipped`.

Ngoài ra, tham số task **không được trùng tên context key của Airflow**
(`logical_date`, `run_id`, ...) — decorator sẽ chèn default và làm vỡ chữ ký hàm
hoặc lặng lẽ truyền giá trị của Airflow. DAG dùng `digest_day`, `pipeline_run_id`.

**Đã bắn thật lên Google Chat**

Chạy đầy đủ với LLM ngày 20/09/2026: 304 bài nạp → 292 bài chưa từng lên bản tin
→ 285 cụm → 24 bài tóm tắt (0 lỗi) → biên tập chọn 10 tin, `mode=llm`, 44 giây,
$0,2144. Card 10 tin gửi tới space `AAQAvr7hjRY`, HTTP 200.

Bộ lọc `already_sent_urls()` hoạt động đúng: lần gửi trước đã loại 12 bài khỏi
danh sách ứng viên của lần này.

Một lỗi hiển thị lộ ra khi nhìn card thật trên Google Chat: header ở chế độ đường
lùi in `Ban tin ngay 2026-09-20` không dấu, cạnh subtitle có dấu. Quy ước viết
không dấu chỉ dành cho comment trong code, không dành cho chuỗi người dùng đọc.
Đã sửa, và ngày hiển thị đổi sang `dd/mm/yyyy`.

**Còn lại chưa kiểm chứng**

- `docker-compose.yml` và `Dockerfile.airflow` chưa build lần nào (máy không có Docker).
- Tính song song thật của các task `ingest` đã map: `airflow dags test` chạy tuần tự.

**Nên làm tiếp**

1. Đo token thật bằng `messages.count_tokens` rồi chốt model cho bước tóm tắt.
2. Bật `langgraph-checkpoint-postgres` (`pip install -e ".[checkpoint]"`) để resume được sau bước `summarize` — bước đắt nhất.
3. Prompt caching cho system prompt của bước tóm tắt (24 lần gọi/ngày dùng chung một system prompt).
4. Chỉnh `BOOST_KEYWORDS` và `weight` theo khẩu vị thật của team sau tuần đầu.
5. Cảnh báo: đẩy `source_health` có `stale=true` vào chính Google Chat space đó.
