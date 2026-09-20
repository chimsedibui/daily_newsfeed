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
# Cong lay tu config/ports.env - mot cho duy nhat de doi.
# shellcheck source=ports.sh
. "$REPO/scripts/ports.sh"
PGDATA="${PGDATA:-$REPO/.pgdata}"
PGPORT="${PGPORT:-$NEWS_PG_PORT}"
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
        echo "listen_addresses = '$NEWS_BIND_HOST'"
        echo "port = $PGPORT"
    } >> "$PGDATA/postgresql.conf"
}

# Cluster da ton tai nhung config/ports.env doi cong -> viet lai postgresql.conf.
sync_port() {
    [ -f "$PGDATA/postgresql.conf" ] || return 0
    grep -qE "^port = $PGPORT\$" "$PGDATA/postgresql.conf" && return 0
    sed -i -E "s/^port = .*/port = $PGPORT/" "$PGDATA/postgresql.conf"
    grep -qE "^port = " "$PGDATA/postgresql.conf" || echo "port = $PGPORT" >> "$PGDATA/postgresql.conf"
    echo "da doi cong Postgres sang $PGPORT (can khoi dong lai neu dang chay)"
}

case "${1:-status}" in
    start)
        init_cluster
        sync_port
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
    init)
        # Chi chuan bi cluster, KHONG khoi dong server. Danh cho ExecStartPre.
        init_cluster
        sync_port
        ;;
    serve)
        # Chay postgres o foreground de systemd so huu tien trinh. Dung cach nay
        # thay vi pg_ctl: pg_ctl fork ra roi thoat, nen Type=forking se treo khi
        # server da chay san, va systemd khong bao gio biet dung PID chinh.
        init_cluster
        sync_port
        exec postgres -D "$PGDATA"
        ;;
    ensure-db)
        # Cho server san sang roi moi tao database. Chay tu ExecStartPost nen
        # PHAI co gioi han thoi gian va PHAI luon thoat 0: treo o day se lam
        # systemd coi ca service la khoi dong that bai.
        for _ in $(seq 1 30); do
            if run_sql postgres "SELECT 1" 1 >/dev/null 2>&1; then
                if run_sql postgres "CREATE DATABASE $PGDB" 1 >/dev/null 2>&1; then
                    echo "da tao database $PGDB"
                fi
                exit 0
            fi
            sleep 1
        done
        echo "canh bao: server chua san sang sau 30s, bo qua ensure-db" >&2
        exit 0
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
        echo "dung: $0 start|serve|init|ensure-db|stop|status|sql \"<cau lenh>\"|reset" >&2
        exit 2
        ;;
esac
