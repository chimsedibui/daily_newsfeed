# Chạy stack trên WSL, không cần Docker và không cần quyền root

Đây là cách môi trường dev hiện tại đang chạy và đã được kiểm chứng end-to-end.
Nếu máy có Docker thì dùng `docker compose up -d` cho nhanh; tài liệu này dành cho
máy không cài được Docker (không có quyền admin).

Môi trường đã kiểm chứng: Ubuntu 26.04 trên WSL2, PostgreSQL 17.11, Airflow 2.10.5,
Python 3.12 (qua `uv`).

---

## 1. Hai môi trường Python, cố ý tách rời

| venv | Nội dung | Vì sao |
|---|---|---|
| `.venv` | app: LangGraph, SQLAlchemy 2.x, psycopg3 | code nghiệp vụ |
| `.venv-airflow` | chỉ Airflow 2.10.5 | orchestrator |

**Không được gộp.** Airflow 2.10.5 ghim `sqlalchemy>=1.4.36,<2.0`, còn app dùng
driver psycopg3 qua DSN `postgresql+psycopg://` — dialect này chỉ có từ
SQLAlchemy 2.0. Cài chung một venv thì Airflow kéo SQLAlchemy về 1.4 và app chết
ngay khi kết nối DB:

```
NoSuchModuleError: Can't load plugin: sqlalchemy.dialects:postgresql.psycopg
```

DAG giải quyết bằng `@task.external_python(python=$NEWS_APP_PYTHON)`: mỗi task chạm
vào app được chạy bằng interpreter của `.venv`. Container Docker cũng làm đúng như
vậy — xem `Dockerfile.airflow`.

---

## 2. Cài đặt

### 2.1 Hai venv

```bash
uv python install 3.12

uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"

uv venv --python 3.12 .venv-airflow
uv pip install --python .venv-airflow/bin/python \
  "apache-airflow==2.10.5" psycopg2-binary \
  --constraint https://raw.githubusercontent.com/apache/airflow/constraints-2.10.5/constraints-3.12.txt
```

### 2.2 PostgreSQL không cần root

Gói binaries của `io.zonky.test` là PostgreSQL build sẵn, chạy dưới quyền user
thường. File `.txz` nằm **bên trong** file `.jar` trên Maven Central:

```bash
PGV=17.11.0
BASE=https://repo1.maven.org/maven2/io/zonky/test/postgres/embedded-postgres-binaries-linux-amd64
mkdir -p ~/.local/pgsql/17 /tmp/pgjar
curl -sSL -o /tmp/pgjar/pg.jar "$BASE/$PGV/embedded-postgres-binaries-linux-amd64-$PGV.jar"
cd /tmp/pgjar && python3 -c "import zipfile; zipfile.ZipFile('pg.jar').extractall('.')"
tar -xJf /tmp/pgjar/postgres-linux-x86_64.txz -C ~/.local/pgsql/17
```

Gói này chỉ có `initdb`, `pg_ctl`, `postgres` — **không kèm `psql`/`createdb`**.
Vì thế `scripts/pg.sh` đi qua psycopg trong `.venv` cho mọi thao tác SQL.

### 2.3 Khởi động

```bash
./scripts/pg.sh start            # initdb lần đầu + tạo database news
cp .env.example .env             # sửa POSTGRES_DSN theo dòng pg.sh in ra
.venv/bin/news-bot init-db       # áp dụng sql/001_init.sql
./scripts/airflow.sh init        # tạo database airflow + db migrate
```

`.env` tối thiểu để chạy thử khi chưa có API key:

```
POSTGRES_DSN=postgresql+psycopg://news@127.0.0.1:5432/news
DRY_RUN=true
LOG_JSON=false
```

---

## 3. Dùng hằng ngày

```bash
./scripts/pg.sh start | stop | status
./scripts/pg.sh sql "SELECT * FROM news.v_run_overview ORDER BY started_at DESC LIMIT 5"

.venv/bin/news-bot check-sources     # thử 14 feed, không ghi DB
.venv/bin/news-bot run               # ingest + LangGraph (theo DRY_RUN trong .env)

./scripts/airflow.sh check           # lỗi import DAG + danh sách task
./scripts/airflow.sh test            # chạy thật cả DAG, không cần scheduler
./scripts/airflow.sh standalone      # webserver + scheduler ở :8080
```

---

## 4. Những thứ trông như lỗi nhưng không phải

**`Traceback ... ModuleNotFoundError: No module named 'pendulum'`** trong log task
`external_python`. Đây là Airflow **thăm dò** venv app xem có `pendulum`/`dill`/
`cloudpickle` không, để quyết định cách truyền context và serializer. Venv app cố
tình không có chúng, nên Airflow ghi `Use 'pickle' as serializer` rồi chạy tiếp.
Hệ quả duy nhất: task `external_python` **không nhận được `context`** — nên ngày
nghiệp vụ được truyền dưới dạng chuỗi qua template Jinja (`DS_LOCAL` trong DAG).

**`digest.status = 'skipped'`** sau khi chạy. Đúng: ở `DRY_RUN=true` không có gì
được gửi đi thật, nên không được đánh dấu `sent` — nếu đánh dấu `sent` thì bộ lọc
`already_sent_urls()` sẽ loại vĩnh viễn các bài đó khỏi bản tin ngày mai.

**Tham số task trùng tên context key của Airflow** (`logical_date`, `ds`, `run_id`,
`params`, `ti`, ...) sẽ bị decorator chèn giá trị mặc định vào, làm vỡ chữ ký hàm
hoặc lặng lẽ truyền giá trị của Airflow thay vì giá trị ta muốn. Vì vậy DAG dùng
`digest_day` và `pipeline_run_id`.

---

## 5. Giới hạn của môi trường này

- `LocalExecutor` với Postgres metadata chạy được, nhưng `scripts/airflow.sh test`
  luôn chạy tuần tự trong một tiến trình — không kiểm chứng được tính song song
  thật của các task `ingest` đã map.
- `.pgdata/`, `.airflow/`, `.venv*/` đều nằm trong `.gitignore`; không có gì trong
  số đó được commit.
- Postgres chỉ nghe `127.0.0.1`. Đây là DB dev, không mở ra mạng.
