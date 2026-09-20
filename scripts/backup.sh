#!/usr/bin/env bash
# Sao luu logic Postgres bang pg_dump, giu N ban gan nhat.
#
#   ./scripts/backup.sh           # tao ban sao luu moi + don ban cu
#   ./scripts/backup.sh list      # liet ke cac ban dang co
#   ./scripts/backup.sh restore <file>
#
# Vi sao khong phai pgBackRest: pgBackRest lam WAL archiving + PITR cho cluster
# duoc quan ly boi root. Cluster o day chay rootless tu goi binaries cua
# io.zonky.test, vao apt can sudo, va du lieu nghiep vu deu dung lai duoc tu
# feed. Ban dump logic hang ngay dung dung nhu cau va khong can quyen gi.
# Neu sau nay can PITR that: xem docs/OPERATIONS.md muc pgBackRest.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=ports.sh
. "$REPO/scripts/ports.sh"

PGHOME="${PGHOME:-$HOME/.local/pgsql/17}"
BACKUP_DIR="${NEWS_BACKUP_DIR:-$HOME/.local/share/news-bot/backups}"
KEEP="${NEWS_BACKUP_KEEP:-7}"
PGDB="${PGDB:-news}"
PGAPPUSER="${PGAPPUSER:-news}"
PY="$REPO/.venv/bin/python"

mkdir -p "$BACKUP_DIR"

have_pg_dump() { [ -x "$PGHOME/bin/pg_dump" ] || command -v pg_dump >/dev/null 2>&1; }
pg_dump_bin() {
    if [ -x "$PGHOME/bin/pg_dump" ]; then echo "$PGHOME/bin/pg_dump"; else command -v pg_dump; fi
}

case "${1:-backup}" in
    backup)
        STAMP="$(date +%Y%m%d-%H%M%S)"
        OUT="$BACKUP_DIR/news-$STAMP.sql.gz"
        if have_pg_dump; then
            "$(pg_dump_bin)" -h "$NEWS_BIND_HOST" -p "$NEWS_PG_PORT" -U "$PGAPPUSER" \
                --schema=news --no-owner --no-privileges "$PGDB" | gzip > "$OUT"
        else
            # Goi Postgres rootless khong kem pg_dump -> dump bang psycopg.
            NEWS_BACKUP_OUT="$OUT" "$PY" "$REPO/scripts/pg_dump_lite.py"
        fi
        echo "da tao $(basename "$OUT") ($(du -h "$OUT" | cut -f1))"

        # Giu KEEP ban moi nhat. Chu y `set -e`: khi khong co gi de xoa thi
        # nhanh [ -n ... ] && ... tra ve 1 va lam ca script thoat loi - du ban
        # sao luu vua tao thanh cong. Dung `if` thay vi chuoi &&.
        mapfile -t old < <(ls -1t "$BACKUP_DIR"/news-*.sql.gz 2>/dev/null | tail -n +$((KEEP + 1)) || true)
        for f in "${old[@]:-}"; do
            if [ -n "$f" ] && [ -e "$f" ]; then
                rm -f "$f"
                echo "da xoa ban cu $(basename "$f")"
            fi
        done
        ;;
    list)
        ls -lht "$BACKUP_DIR"/news-*.sql.gz 2>/dev/null || echo "chua co ban sao luu nao"
        ;;
    restore)
        SRC="${2:?dung: $0 restore <duong-dan.sql.gz>}"
        echo "Se GHI DE schema news tu $SRC. Ctrl-C trong 5 giay de huy."
        sleep 5
        NEWS_RESTORE_SRC="$SRC" "$PY" "$REPO/scripts/pg_restore_lite.py"
        ;;
    *)
        echo "dung: $0 backup|list|restore <file>" >&2
        exit 2
        ;;
esac
