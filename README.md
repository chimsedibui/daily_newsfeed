# updating-news

Bot tổng hợp tin tức Việt Nam hằng ngày, bắn bản tin lên **Google Chat** qua webhook.

- **Nguồn tin**: RSS + SSR (JSON-LD `NewsArticle`) của VnExpress, Tuổi Trẻ, Thanh Niên, Dân Trí, VietnamNet, CafeF — 14 feed, đã kiểm chứng chạy thật.
- **Xử lý**: LangGraph (gom trùng → xếp hạng → tóm tắt → biên tập → render card).
- **Trace & logging**: Postgres (`pipeline_run`, `node_span`, `llm_call`, `app_log`, `source_health`).
- **Lịch chạy**: Airflow DAG `daily_news_digest`, 08:00 giờ VN, T2–T6.

Báo cáo khảo sát nguồn tin và giải thích kiến trúc: [docs/REPORT.md](docs/REPORT.md).

---

## Kiến trúc

```
Airflow DAG  daily_news_digest
  preflight ─→ create_run ─→ ingest[14 feed] ─→ check_sources ─┐
                                                               ↓
                                     build_digest  (LangGraph) ─→ deliver ─→ finalize
                                                                             (ALL_DONE)

LangGraph  build_digest
  load ─(có bài?)─→ cluster ─→ rank ─→ enrich ─→ summarize ─(có tóm tắt?)─→ compose ─→ render ─→ END
     └─ không ──→ END                                        └─ không ──→ END
```

Phân vai: **Airflow lo điều phối** (lịch, retry, fan-out theo nguồn, cảnh báo), **LangGraph lo nội dung**. Gọi LLM nằm trọn trong một task Airflow vì state giữa các node là object Python lớn — đẩy qua XCom là phản tác dụng.

---

## Cài đặt

```bash
python -m venv .venv && .venv/Scripts/activate
pip install -e ".[dev]"
cp .env.example .env    # điền GOOGLE_CHAT_WEBHOOK_URL + GEMINI_API_KEY
```

Dựng Postgres + Airflow:

```bash
docker compose up -d
```

Postgres ở `localhost:5432` (news/news/news), Airflow UI ở `http://localhost:8080`.

**Không có Docker?** Xem [docs/SETUP-WSL.md](docs/SETUP-WSL.md) — Postgres chạy rootless + Airflow trong venv riêng. Đó là cách stack này đang được kiểm chứng.

> **Hai môi trường Python, cố ý tách rời.** Airflow 2.10 ghim `sqlalchemy<2.0`, còn app dùng psycopg3 (`postgresql+psycopg://`) vốn cần SQLAlchemy 2.x. Gộp một venv là app chết ngay ở bước kết nối DB. DAG chạy mỗi task chạm app bằng `@task.external_python` trỏ tới interpreter của venv app; container làm y hệt — xem `Dockerfile.airflow`.

### Lấy webhook URL của Google Chat

Mở Space → **Apps & integrations** → **Webhooks** → **Add webhooks** → copy nguyên URL (bao gồm cả `key=` và `token=`) vào `GOOGLE_CHAT_WEBHOOK_URL`.

---

## Dùng

```bash
news-bot check-sources        # thử từng feed, không ghi DB — chạy trước khi deploy
news-bot init-db              # tạo schema (idempotent)
news-bot run                  # ingest + LangGraph, KHÔNG gửi
news-bot run --send           # chạy đầy đủ và bắn lên Google Chat
news-bot preview 12           # in payload cardsV2 của digest #12
news-bot send 12 --run-id ... # gửi lại digest đã render (khi webhook hỏng)
news-bot config               # xem cấu hình hiện tại (đã che secret)
```

Đặt `DRY_RUN=true` để chạy toàn bộ pipeline mà không gọi LLM và không bắn webhook — dùng khi kiểm thử hạ tầng.

---

## Cấu hình nguồn tin

`config/sources.yaml`. Mỗi entry một feed:

```yaml
- id: vnexpress_kinh_doanh
  publisher: VnExpress
  category: kinh-doanh
  url: https://vnexpress.net/rss/kinh-doanh.rss
  weight: 1.3          # nhân vào điểm xếp hạng
  strategy: rss        # rss | hf_papers | github_trending
  max_items: 25
  enabled: true
```

`weight` và `BOOST_KEYWORDS` trong [rank.py](src/news_bot/graph/nodes/rank.py) là hai chỗ chỉnh khẩu vị bản tin cho team.

---

## Chọn LLM

`LLM_PROVIDERS` là một chuỗi ưu tiên, mặc định `gemini,vertex,openai`. Provider đầu
chuỗi hỏng kiểu hệ thống (sai key, hết quota, không kết nối được) thì lần gọi đó
tự rơi xuống cái tiếp theo, và provider hỏng bị **bỏ hẳn cho phần còn lại của tiến
trình** — nếu không, một sự cố của Gemini sẽ làm cả 36 bài đều thử Gemini trước.

| Provider | Xác thực | Tiền đi đâu |
|---|---|---|
| `gemini` | `GEMINI_API_KEY` | tài khoản gắn với key. Key không hết hạn |
| `vertex` | ADC (`gcloud auth application-default login`) | project GCP. **ADC của tài khoản người dùng sẽ hết hạn** |
| `openai` | `OPENAI_API_KEY` | tài khoản OpenAI |

Mỗi provider có bảng model riêng trong `llm.PROVIDER_MODELS` — đổi provider là đổi
model, vì `gemini-3.1-flash-lite` không tồn tại trên OpenAI. `SUMMARIZER_MODEL` /
`EDITOR_MODEL` chỉ ghi đè cho provider **đầu** chuỗi.

Bước tóm tắt luôn chạy với `thinking_budget=0`: rút gọn một bài báo không cần suy
luận, bật lên chỉ tăng token và độ trễ.

---

## Quan sát vận hành

```sql
-- Tổng quan các lần chạy: thời lượng, số tin, chi phí LLM, nguồn hỏng
SELECT * FROM news.v_run_overview ORDER BY started_at DESC LIMIT 10;

-- Node nào chậm trong một run
SELECT name, kind, status, duration_ms FROM news.node_span
WHERE run_id = :run_id ORDER BY started_at;

-- Chi phí LLM theo ngày
SELECT date_trunc('day', created_at) d, model, purpose,
       count(*) calls, sum(input_tokens+output_tokens) tokens, sum(cost_usd) usd
FROM news.llm_call GROUP BY 1,2,3 ORDER BY 1 DESC;

-- Feed "chết lâm sàng": HTTP 200 nhưng bài mới nhất đã quá 7 ngày
SELECT source_id, max(newest_item_age_h) age_h FROM news.source_health
WHERE created_at > now() - interval '3 days' GROUP BY 1 HAVING max(newest_item_age_h) > 168;
```

---

## Kiểm thử

```bash
pytest -q
ruff check src tests airflow
```

22 test, không cần Postgres và không gọi LLM — các node nhận `config` rỗng là tự tắt trace.

Kiểm chứng ở tầng tích hợp:

```bash
./scripts/pg.sh start
news-bot init-db && news-bot run      # LangGraph end-to-end
./scripts/airflow.sh check            # lỗi import DAG
./scripts/airflow.sh test             # chạy thật cả DAG
```
