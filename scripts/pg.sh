#!/usr/bin/env bash
# Postgres chay rootless: binaries doc lap, khong can sudo, khong can Docker.
# Dung cho may dev khong cai duoc Docker. Production van dung docker-compose.yml.
#
#   ./scripts/pg.sh start | stop | status | sql "<cau lenh>" | reset
#
# Lan dau chay se tu initdb vao $REPO/.pgdata va tao database.
#
# Luu y: goi binaries tu io.zonky.test chi co server (initdb/pg_ctl/postgres),
# khong kem psql/createdb. Nen cac thao tac SQL di qua psycopg trong .venv.
set -euo pipefail

PGHOME="${PGHOME:-$HOME/.local/pgsql/17}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PGDATA="${PGDATA:-$REPO/.pgdata}"
PGPORT="${PGPORT:-5432}"
PGAPPUSER="${PGAPPUSER:-news}"
PGDB="${PGDB:-news}"
PY="${PY:-$REPO/.venv/bin/python}"
LOG="$PGDATA/server.log"

export PATH="$PGHOME/bin:$PATH"

if [ ! -x "$PGHOME/bin/initdb" ]; then
    echo "Khong thay Postgres o $PGHOME. Xem docs/SETUP-WSL.md." >&2
    exit 1
fi

# $1 = database, $2 = SQL, $3 = autocommit (1/0)
run_sql() {
    "$PY" - "$1" "$2" "${3:-0}" <<'PYEOF'
import sys
import psycopg

db, sql, autocommit = sys.argv[1], sys.argv[2], sys.argv[3] == "1"
dsn = f"host=127.0.0.1 port={__import__('os').environ.get('PGPORT', '5432')} user={__import__('os').environ.get('PGAPPUSER', 'news')} dbname={db}"
with psycopg.connect(dsn, autocommit=autocommit) as conn, conn.cursor() as cur:
    cur.execute(sql)
    if cur.description:
        widths = [len(d.name) for d in cur.description]
        rows = [[("" if v is None else str(v)) for v in r] for r in cur.fetchall()]
        for row in rows:
            widths = [max(w, len(v)) for w, v in zip(widths, row)]
        header = " | ".join(d.name.ljust(w) for d, w in zip(cur.description, widths))
        print(header)
        print("-+-".join("-" * w for w in widths))
        for row in rows:
            print(" | ".join(v.ljust(w) for v, w in zip(row, widths)))
        print(f"({len(rows)} dong)")
PYEOF
}

init_cluster() {
    [ -f "$PGDATA/PG_VERSION" ] && return 0
    echo "initdb -> $PGDATA"
    initdb -D "$PGDATA" -U "$PGAPPUSER" -E UTF8 --locale=C \
           --auth-local=trust --auth-host=trust >/dev/null
    # DB dev: chi nghe loopback, khong mo ra mang.
    {
        echo "listen_addresses = '127.0.0.1'"
        echo "port = $PGPORT"
    } >> "$PGDATA/postgresql.conf"
}

case "${1:-status}" in
    start)
        init_cluster
        if pg_ctl -D "$PGDATA" status >/dev/null 2>&1; then
            echo "postgres da chay san"
        else
            pg_ctl -D "$PGDATA" -l "$LOG" start -w
        fi
        # CREATE DATABASE khong chay trong transaction -> autocommit.
        if run_sql postgres "CREATE DATABASE $PGDB" 1 2>/dev/null; then
            echo "da tao database $PGDB"
        fi
        echo "POSTGRES_DSN=postgresql+psycopg://$PGAPPUSER@127.0.0.1:$PGPORT/$PGDB"
        ;;
    stop)
        pg_ctl -D "$PGDATA" stop -m fast
        ;;
    status)
        pg_ctl -D "$PGDATA" status
        ;;
    sql)
        shift
        run_sql "$PGDB" "$*" 1
        ;;
    reset)
        pg_ctl -D "$PGDATA" stop -m immediate 2>/dev/null || true
        rm -rf "$PGDATA"
        echo "da xoa $PGDATA"
        ;;
    *)
        echo "dung: $0 start|stop|status|sql \"<cau lenh>\"|reset" >&2
        exit 2
        ;;
esac
