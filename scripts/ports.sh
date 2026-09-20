#!/usr/bin/env bash
# Nap config/ports.env va kiem tra cac cong con trong.
#
#   ./scripts/ports.sh          # in cac cong dang cau hinh + trang thai
#   ./scripts/ports.sh check    # thoat khac 0 neu co cong bi chiem boi service la
#   source ./scripts/ports.sh   # chi nap bien, khong in gi
set -euo pipefail

PORTS_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORTS_FILE="${PORTS_FILE:-$PORTS_REPO/config/ports.env}"

if [ -f "$PORTS_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    . "$PORTS_FILE"
    set +a
fi

: "${NEWS_PG_PORT:=18432}"
: "${NEWS_AIRFLOW_PORT:=18080}"
: "${NEWS_STATUS_PORT:=18081}"
: "${NEWS_BIND_HOST:=127.0.0.1}"

# Ai dang giu cong nay? In "" neu trong.
port_owner() {
    local port="$1"
    if command -v ss >/dev/null 2>&1; then
        ss -lntp "sport = :$port" 2>/dev/null | awk 'NR>1 {print $NF; exit}'
    else
        # Khong co ss -> thu mo socket bang python.
        "$PORTS_REPO/.venv/bin/python" - "$port" <<'PYEOF'
import socket
import sys

s = socket.socket()
try:
    s.bind(("127.0.0.1", int(sys.argv[1])))
except OSError:
    print("dang-bi-chiem")
finally:
    s.close()
PYEOF
    fi
}

# Khi duoc `source`, chi nap bien roi thoi.
(return 0 2>/dev/null) && return 0

status=0
printf "%-22s %-7s %s\n" "DICH VU" "CONG" "TRANG THAI"
for entry in "Postgres:$NEWS_PG_PORT" "Airflow:$NEWS_AIRFLOW_PORT" \
             "Trang trang thai:$NEWS_STATUS_PORT"; do
    name="${entry%%:*}"
    port="${entry##*:}"
    owner="$(port_owner "$port")"
    if [ -z "$owner" ]; then
        printf "%-22s %-7s trong\n" "$name" "$port"
    else
        printf "%-22s %-7s DANG BI CHIEM  %s\n" "$name" "$port" "$owner"
        # Postgres cua chinh repo nay giu cong la binh thuong.
        case "$owner" in
            *postgres*) ;;
            *) status=1 ;;
        esac
    fi
done

if [ "${1:-}" = "check" ]; then
    [ "$status" -eq 0 ] && echo "OK: khong co xung dot cong" \
        || echo "CANH BAO: co cong bi service la chiem, sua config/ports.env" >&2
    exit "$status"
fi
