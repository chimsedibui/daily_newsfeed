"""DAG ban tin hang ngay.

Phan vai:
  - Airflow  = dieu phoi: lich chay, retry tho, fan-out theo nguon, canh bao.
  - LangGraph = noi dung: dedupe -> rank -> tom tat -> bien tap (1 task duy nhat).

    preflight -> create_run -> ingest[source] (mapped) -> check_sources -> build_digest -,
                            |                                                             |-> deliver -> purge_old_data
                            `-> build_weather ----------------------------------------------'                    -> finalize

build_weather chay song song va doc lap voi nhanh tin tuc: khong co bai tin nao
thi van phai co du bao, va Open-Meteo hong thi khong duoc keo do ca bo tin tuc.

HAI MOI TRUONG PYTHON, KHONG DUNG CHUNG
---------------------------------------
Airflow 2.10.5 ghim `sqlalchemy>=1.4.36,<2.0`, trong khi app dung SQLAlchemy 2.x
voi driver psycopg3 (`postgresql+psycopg://`) - dialect nay chi ton tai tu
SQLAlchemy 2.0. Cai chung mot venv thi Airflow keo SQLAlchemy ve 1.4 va app chet
ngay o buoc ket noi DB (NoSuchModuleError: sqlalchemy.dialects:postgresql.psycopg).

Nen moi task cham vao app deu chay bang `@task.external_python` tro toi
interpreter cua venv app. Airflow khong can biet gi ve langgraph/psycopg, app
khong bi ghim boi lich su phu thuoc cua orchestrator.

He qua: task external_python KHONG nhan duoc `context` (moi truong ben kia khong
co Airflow). Nhung gi can tu context phai truyen qua op_kwargs dang template
Jinja - xem `digest_day=DS_LOCAL` ben duoi.

Luu y: tham so KHONG duoc dat ten trung context key cua Airflow (logical_date,
ds, run_id, params, ti...) - decorator se chen default vao va lam vo chu ky ham,
hoac te hon la lang le truyen gia tri cua Airflow thay vi gia tri ta muon. Vi vay
run_id cua pipeline duoc goi la `pipeline_run_id`.

Vi sao khong tach nho LangGraph thanh nhieu task? Vi state giua cac buoc la
object Python lon, day qua XCom la phan tac dung.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

import pendulum
from airflow.decorators import dag, task
from airflow.models import Variable
from airflow.utils.trigger_rule import TriggerRule

REPO_ROOT = Path(__file__).resolve().parents[2]

# Interpreter cua venv app. Doi duong dan qua bien moi truong khi deploy.
APP_PYTHON = os.environ.get("NEWS_APP_PYTHON", str(REPO_ROOT / ".venv" / "bin" / "python"))

LOCAL_TZ = pendulum.timezone("Asia/Ho_Chi_Minh")
# Ngay nghiep vu theo gio VN, dang YYYY-MM-DD.
DS_LOCAL = '{{ logical_date.in_timezone("Asia/Ho_Chi_Minh") | ds }}'

default_args = {
    "owner": "data-platform",
    "retries": 2,
    "retry_delay": timedelta(minutes=3),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=15),
    "execution_timeout": timedelta(minutes=30),
}


@dag(
    dag_id="daily_news_digest",
    description="Tong hop tin tuc VN hang ngay va ban len Google Chat",
    schedule="0 8 * * 1-5",          # 08:00 gio VN, thu 2 - thu 6
    start_date=datetime(2026, 9, 1, tzinfo=LOCAL_TZ),
    catchup=False,
    max_active_runs=1,
    default_args=default_args,
    tags=["news", "langgraph", "google-chat"],
)
def daily_news_digest():
    @task.external_python(python=APP_PYTHON)
    def preflight() -> list[str]:
        from news_bot import pipeline

        result = pipeline.preflight()
        if result["problems"]:
            raise RuntimeError(f"Cau hinh chua du: {result['problems']}")
        return result["sources"]

    @task.external_python(python=APP_PYTHON)
    def create_run(digest_day: str, dag_run_id: str) -> str:
        from datetime import date

        from news_bot import pipeline

        return pipeline.start_run(
            logical_date=date.fromisoformat(digest_day),
            trigger="airflow",
            dag_run_id=dag_run_id,
        )

    @task.external_python(python=APP_PYTHON, retries=1,
                          execution_timeout=timedelta(minutes=5))
    def ingest(source_id: str, pipeline_run_id: str) -> dict:
        """Mot nguon chet khong lam do DAG - trang thai nam trong source_health."""
        from news_bot import pipeline

        return pipeline.ingest_one(pipeline_run_id, source_id)

    @task
    def check_sources(results: list[dict]) -> dict:
        """Chan truong hop ca loat feed doi URL ma khong ai biet.

        Task nay thuan dict nen chay ngay trong moi truong Airflow, khong can
        nhay sang venv app.
        """
        failed = [r["source_id"] for r in results if not r["ok"]]
        stale = [r["source_id"] for r in results if r.get("stale")]
        total_new = sum(r.get("new", 0) for r in results)
        threshold = float(Variable.get("news_min_source_success_ratio", default_var=0.5))
        ratio = 1 - len(failed) / max(len(results), 1)
        if ratio < threshold:
            raise RuntimeError(
                f"Chi {ratio:.0%} nguon OK (nguong {threshold:.0%}). Hong: {failed}"
            )
        return {"failed_sources": failed, "stale_sources": stale,
                "new_articles": total_new, "total_sources": len(results)}

    @task.external_python(python=APP_PYTHON, retries=1,
                          execution_timeout=timedelta(minutes=20))
    def build_digest(pipeline_run_id: str, digest_day: str) -> dict:
        from datetime import date

        from news_bot import pipeline

        return pipeline.build_digest(pipeline_run_id, date.fromisoformat(digest_day))

    @task.external_python(python=APP_PYTHON, retries=2,
                          execution_timeout=timedelta(minutes=5))
    def build_weather(pipeline_run_id: str, digest_day: str) -> dict:
        from datetime import date

        from news_bot import pipeline

        return pipeline.build_weather(pipeline_run_id, date.fromisoformat(digest_day))

    @task.external_python(python=APP_PYTHON, retries=3,
                          retry_delay=timedelta(minutes=2),
                          trigger_rule=TriggerRule.ALL_DONE)
    def deliver(pipeline_run_id: str, digest_day: str) -> dict:
        """Gui moi ban tin `pending` cua run.

        ALL_DONE: thoi tiet hong thi van gui tin tuc, va nguoc lai. Danh sach
        lay tu DB nen retry khong gui lai cai da gui.
        """
        from datetime import date

        from news_bot import pipeline

        return pipeline.deliver_all(pipeline_run_id, date.fromisoformat(digest_day))

    @task.external_python(python=APP_PYTHON, trigger_rule=TriggerRule.ALL_DONE,
                          retries=1)
    def purge_old_data() -> dict:
        """Don du lieu qua han. ALL_DONE: run hong thi van phai don.

        Chay sau `deliver` chu khong truoc: neu don truoc ma pipeline dang doc
        du lieu thi VACUUM se tranh chap khoa mot cach vo ich.
        """
        from news_bot import pipeline

        return pipeline.purge_old_data()

    @task.external_python(python=APP_PYTHON, trigger_rule=TriggerRule.ALL_DONE)
    def finalize(pipeline_run_id: str, digest: dict, health: dict, delivery: dict,
                 weather: dict) -> None:
        """Chay ca khi nhanh tren that bai -> pipeline_run khong ket o 'running'."""
        from news_bot import pipeline

        digest = digest or {}
        health = health or {}
        delivery = delivery or {}
        weather = weather or {}

        from news_bot.pipeline import run_status

        if delivery.get("status") not in ("sent", "skipped", "already_sent"):
            status = "failed"
        elif weather.get("skipped"):
            status = "partial"
        else:
            status = run_status(
                health.get("failed_sources", []),
                health.get("stale_sources", []),
                health.get("total_sources", 0),
            )

        pipeline.finalize(
            pipeline_run_id,
            status=status,
            metrics={
                **(digest.get("metrics") or {}),
                "new_articles": health.get("new_articles", 0),
                "failed_sources": health.get("failed_sources", []),
                "stale_sources": health.get("stale_sources", []),
                "delivery_status": delivery.get("status"),
                "delivered": delivery.get("sent", []),
                "weather_ok": not weather.get("skipped", True),
            },
        )

    sources = preflight()
    pipeline_run_id = create_run(digest_day=DS_LOCAL, dag_run_id="{{ run_id }}")
    ingested = ingest.partial(pipeline_run_id=pipeline_run_id).expand(source_id=sources)
    health = check_sources(ingested)
    digest = build_digest(pipeline_run_id=pipeline_run_id, digest_day=DS_LOCAL)
    weather = build_weather(pipeline_run_id=pipeline_run_id, digest_day=DS_LOCAL)
    sent = deliver(pipeline_run_id=pipeline_run_id, digest_day=DS_LOCAL)

    health >> digest
    [digest, weather] >> sent
    sent >> purge_old_data()
    finalize(pipeline_run_id, digest, health, sent, weather)


daily_news_digest()
