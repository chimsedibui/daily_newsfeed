"""DAG ban tin hang ngay.

Phan vai:
  - Airflow  = dieu phoi: lich chay, retry tho, fan-out theo nguon, canh bao.
  - LangGraph = noi dung: dedupe -> rank -> tom tat -> bien tap (1 task duy nhat).

Vi sao khong nhet ca pipeline vao 1 task? Vi thu thap nguon la I/O doc lap,
fan-out bang dynamic task mapping cho ta retry rieng tung nguon va nhin thay
nguon nao chet ngay tren UI. Con vi sao khong tach nho LangGraph thanh nhieu
task? Vi state giua cac buoc la object Python lon, day qua XCom la phan tac dung.

    preflight -> create_run -> ingest[source] (mapped) -> build_digest
                                                       -> deliver -> finalize
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pendulum
from airflow.decorators import dag, task
from airflow.models import Variable
from airflow.utils.trigger_rule import TriggerRule

# Repo duoc mount vao container Airflow tai /opt/news; them src/ vao path.
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

LOCAL_TZ = pendulum.timezone("Asia/Ho_Chi_Minh")

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
    description="Tong hop tin tuc VN hang ngay va bap len Google Chat",
    schedule="0 8 * * 1-5",          # 08:00 gio VN, thu 2 - thu 6
    start_date=datetime(2026, 9, 1, tzinfo=LOCAL_TZ),
    catchup=False,
    max_active_runs=1,
    default_args=default_args,
    tags=["news", "langgraph", "google-chat"],
)
def daily_news_digest():
    @task
    def preflight() -> list[str]:
        from news_bot import pipeline

        result = pipeline.preflight()
        if result["problems"]:
            raise RuntimeError(f"Cau hinh chua du: {result['problems']}")
        return result["sources"]

    @task
    def create_run(**context) -> str:
        from news_bot import pipeline

        logical_date = context["logical_date"].in_timezone(LOCAL_TZ).date()
        return pipeline.start_run(
            logical_date=logical_date,
            trigger="airflow",
            dag_run_id=context["dag_run"].run_id,
        )

    @task(retries=1, execution_timeout=timedelta(minutes=5))
    def ingest(source_id: str, run_id: str) -> dict:
        """Mot nguon chet khong lam do DAG - trang thai nam trong source_health."""
        from news_bot import pipeline

        return pipeline.ingest_one(run_id, source_id)

    @task
    def check_sources(results: list[dict]) -> dict:
        """Chan truong hop ca loat feed doi URL ma khong ai biet."""
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
                "new_articles": total_new}

    @task(execution_timeout=timedelta(minutes=20), retries=1)
    def build_digest(run_id: str, **context) -> dict:
        from news_bot import pipeline

        logical_date = context["logical_date"].in_timezone(LOCAL_TZ).date()
        return pipeline.build_digest(run_id, logical_date)

    @task(retries=3, retry_delay=timedelta(minutes=2))
    def deliver(run_id: str, digest: dict, **context) -> dict:
        from news_bot import pipeline

        logical_date = context["logical_date"].in_timezone(LOCAL_TZ).date()
        return pipeline.deliver(run_id, digest.get("digest_id"), logical_date)

    @task(trigger_rule=TriggerRule.ALL_DONE)
    def finalize(run_id: str, digest: dict, health: dict, delivery: dict) -> None:
        """Chay ca khi nhanh tren that bai -> pipeline_run khong bao gio ket o 'running'."""
        from news_bot import pipeline

        delivery = delivery or {}
        status = "success"
        if (delivery.get("status") not in ("sent", "skipped", "already_sent")):
            status = "failed"
        elif health.get("failed_sources") or health.get("stale_sources"):
            status = "partial"

        pipeline.finalize(
            run_id,
            status=status,
            metrics={
                **(digest.get("metrics") or {}),
                "new_articles": health.get("new_articles", 0),
                "failed_sources": health.get("failed_sources", []),
                "stale_sources": health.get("stale_sources", []),
                "delivery_status": delivery.get("status"),
            },
        )

    sources = preflight()
    run_id = create_run()
    ingested = ingest.partial(run_id=run_id).expand(source_id=sources)
    health = check_sources(ingested)
    digest = build_digest(run_id)
    sent = deliver(run_id, digest)

    health >> digest
    finalize(run_id, digest, health, sent)


daily_news_digest()
