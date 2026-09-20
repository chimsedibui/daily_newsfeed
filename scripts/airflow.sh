#!/usr/bin/env bash
# Airflow cho may dev, chay trong venv RIENG (.venv-airflow).
#
#   ./scripts/airflow.sh init          # tao DB metadata + migrate
#   ./scripts/airflow.sh check         # liet ke DAG + loi import
#   ./scripts/airflow.sh test [YYYY-MM-DD]   # chay that ca DAG, khong can scheduler
#   ./scripts/airflow.sh standalone    # webserver + scheduler o :8080
#   ./scripts/airflow.sh <lenh airflow khac>
#
# Vi sao venv rieng: Airflow 2.10 ghim sqlalchemy<2.0 con app can sqlalchemy>=2.0
# (driver psycopg3). Xem doc string dau file airflow/dags/daily_news_dag.py.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=ports.sh
. "$REPO/scripts/ports.sh"
AF_PY="$REPO/.venv-airflow/bin/python"
AF="$REPO/.venv-airflow/bin/airflow"

export AIRFLOW_HOME="${AIRFLOW_HOME:-$REPO/.airflow}"
export AIRFLOW__CORE__DAGS_FOLDER="$REPO/airflow/dags"
export AIRFLOW__CORE__LOAD_EXAMPLES=False
export AIRFLOW__CORE__EXECUTOR="${AIRFLOW__CORE__EXECUTOR:-LocalExecutor}"
export AIRFLOW__CORE__DEFAULT_TIMEZONE=Asia/Ho_Chi_Minh
export AIRFLOW__DATABASE__SQL_ALCHEMY_CONN="${AIRFLOW__DATABASE__SQL_ALCHEMY_CONN:-postgresql+psycopg2://news@$NEWS_BIND_HOST:$NEWS_PG_PORT/airflow}"
export AIRFLOW__WEBSERVER__WEB_SERVER_PORT="$NEWS_AIRFLOW_PORT"
export AIRFLOW__WEBSERVER__WEB_SERVER_HOST="$NEWS_BIND_HOST"
# Interpreter ma cac task external_python se goi sang.
export NEWS_APP_PYTHON="${NEWS_APP_PYTHON:-$REPO/.venv/bin/python}"

if [ ! -x "$AF" ]; then
    echo "Chua co venv Airflow o $REPO/.venv-airflow. Xem docs/SETUP-WSL.md." >&2
    exit 1
fi

case "${1:-check}" in
    init)
        # CREATE DATABASE khong chay trong transaction -> autocommit.
        NEWS_PG_PORT="$NEWS_PG_PORT" "$REPO/.venv/bin/python" - <<'PYEOF'
import os

import psycopg

port = os.environ.get("NEWS_PG_PORT", "18432")
with psycopg.connect(f"host=127.0.0.1 port={port} user=news dbname=postgres",
                     autocommit=True) as conn, conn.cursor() as cur:
    cur.execute("SELECT 1 FROM pg_database WHERE datname = 'airflow'")
    if cur.fetchone() is None:
        cur.execute("CREATE DATABASE airflow")
        print("da tao database airflow")
    else:
        print("database airflow da co")
PYEOF
        "$AF" db migrate
        ;;
    check)
        echo "=== loi import DAG ==="
        "$AF" dags list-import-errors
        echo "=== danh sach DAG ==="
        "$AF" dags list
        echo "=== task trong daily_news_digest ==="
        "$AF" tasks list daily_news_digest
        ;;
    test)
        shift
        DAY="${1:-$(date +%F)}"
        "$AF" dags test daily_news_digest "$DAY"
        ;;
    standalone)
        "$AF" standalone
        ;;
    *)
        exec "$AF" "$@"
        ;;
esac
