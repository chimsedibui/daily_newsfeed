#!/usr/bin/env bash
# Quan sat TAI NGUYEN MAY: Prometheus + Grafana + hai exporter, chay rootless.
#
#   ./scripts/obs.sh install    # tai binaries ve ~/.local/obs (mot lan)
#   ./scripts/obs.sh start | stop | restart | status
#   ./scripts/obs.sh render     # chi render config, khong chay gi
#
# Ranh gioi voi trang trang thai o :18081 - CO Y khong chong lan:
#
#   Trang HTML (news_bot.status)  ->  nghiep vu: run, chi phi LLM, nguon tin,
#                                     noi dung ban tin, dung luong bang.
#   Grafana (o day)               ->  tai nguyen: CPU, RAM, dia, mang, va so
#                                     lieu runtime cua tien trinh Postgres.
#
# Vi sao khong day metric cua pipeline vao Prometheus: pipeline chay 1 lan/ngay
# trong vai phut, con Prometheus la mo hinh PULL scrape moi 15 giay. Phan lon
# luot scrape se truot vi tien trinh khong con song. Du lieu do da nam trong
# Postgres roi va trang HTML doc thang tu do - chinh xac hon, khong mat mau.
#
# Binaries la file tinh, khong can sudo, khong can Docker - cung tinh than voi
# scripts/pg.sh. May co Docker thi dung docker-compose.yml (4 service tuong ung).
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=ports.sh
. "$REPO/scripts/ports.sh"

OBSHOME="${OBSHOME:-$HOME/.local/obs}"
OBSDATA="${OBSDATA:-$REPO/.obs}"
PIDDIR="$OBSDATA/pids"

# Doi version: sua o day roi chay lai `install`. Da kiem chung 2026-09.
PROM_VER="${PROM_VER:-3.1.0}"
NODE_VER="${NODE_VER:-1.8.2}"
GRAFANA_VER="${GRAFANA_VER:-11.5.1}"
PGEXP_VER="${PGEXP_VER:-0.16.0}"

# postgres_exporter noi toi cung cluster ma pg.sh dung (auth trust, khong mat khau).
PGAPPUSER="${PGAPPUSER:-news}"
PGDB="${PGDB:-news}"
PG_DSN="postgresql://$PGAPPUSER@127.0.0.1:$NEWS_PG_PORT/$PGDB?sslmode=disable"

log() { printf '%s\n' "$*"; }
die() { printf '%s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- install

# $1 = url, $2 = thu muc dich, $3 = so thanh phan duong dan cat bo
fetch_tgz() {
    local url="$1" dest="$2" strip="${3:-1}"
    mkdir -p "$dest"
    log "  tai $(basename "$url")"
    curl -sSLf --retry 3 "$url" -o "$OBSDATA/.dl.tgz" \
        || die "tai that bai: $url (kiem tra mang, hoac doi version o dau obs.sh)"
    tar -xzf "$OBSDATA/.dl.tgz" -C "$dest" --strip-components="$strip"
    rm -f "$OBSDATA/.dl.tgz"
}

cmd_install() {
    mkdir -p "$OBSHOME" "$OBSDATA"
    local gh="https://github.com"

    if [ ! -x "$OBSHOME/prometheus/prometheus" ]; then
        fetch_tgz "$gh/prometheus/prometheus/releases/download/v$PROM_VER/prometheus-$PROM_VER.linux-amd64.tar.gz" \
                  "$OBSHOME/prometheus"
    fi
    if [ ! -x "$OBSHOME/node_exporter/node_exporter" ]; then
        fetch_tgz "$gh/prometheus/node_exporter/releases/download/v$NODE_VER/node_exporter-$NODE_VER.linux-amd64.tar.gz" \
                  "$OBSHOME/node_exporter"
    fi
    if [ ! -x "$OBSHOME/postgres_exporter/postgres_exporter" ]; then
        fetch_tgz "$gh/prometheus-community/postgres_exporter/releases/download/v$PGEXP_VER/postgres_exporter-$PGEXP_VER.linux-amd64.tar.gz" \
                  "$OBSHOME/postgres_exporter"
    fi
    if [ ! -x "$OBSHOME/grafana/bin/grafana" ]; then
        fetch_tgz "https://dl.grafana.com/oss/release/grafana-$GRAFANA_VER.linux-amd64.tar.gz" \
                  "$OBSHOME/grafana"
    fi

    cmd_render
    log "da cai vao $OBSHOME"
}

# ---------------------------------------------------------------- render

# Config cua Prometheus/Grafana khong tu doc bien moi truong cho cong, nen
# render tu template trong config/ ra $OBSDATA - cung kieu __REPO__ cua systemd.
cmd_render() {
    mkdir -p "$OBSDATA/prometheus-data" "$PIDDIR" \
             "$OBSDATA/grafana/data" "$OBSDATA/grafana/logs" "$OBSDATA/grafana/plugins" \
             "$OBSDATA/provisioning/datasources" "$OBSDATA/provisioning/dashboards"

    sed -e "s|__BIND__|$NEWS_BIND_HOST|g" \
        -e "s|__PROM_PORT__|$NEWS_PROM_PORT|g" \
        -e "s|__NODE_PORT__|$NEWS_NODE_EXPORTER_PORT|g" \
        -e "s|__PGEXP_PORT__|$NEWS_PG_EXPORTER_PORT|g" \
        "$REPO/config/observability/prometheus.yml" > "$OBSDATA/prometheus.yml"

    sed -e "s|__BIND__|$NEWS_BIND_HOST|g" \
        -e "s|__PROM_PORT__|$NEWS_PROM_PORT|g" \
        "$REPO/config/observability/datasource.yml" \
        > "$OBSDATA/provisioning/datasources/prometheus.yml"

    sed -e "s|__REPO__|$REPO|g" \
        "$REPO/config/observability/dashboards.yml" \
        > "$OBSDATA/provisioning/dashboards/news.yml"

    log "da render config vao $OBSDATA"
}

# ---------------------------------------------------------------- serve

# Moi lenh serve-* chay foreground de systemd so huu dung tien trinh
# (cung ly do nhu `pg.sh serve` - xem systemd/news-postgres.service).
serve_node() {
    exec "$OBSHOME/node_exporter/node_exporter" \
        --web.listen-address="$NEWS_BIND_HOST:$NEWS_NODE_EXPORTER_PORT" \
        --collector.filesystem.mount-points-exclude='^/(dev|proc|sys|run|var/lib/docker/.+|mnt/[a-z])($|/)' \
        --no-collector.wifi --no-collector.infiniband \
        --no-collector.nfs --no-collector.nfsd
}

serve_pgexp() {
    export DATA_SOURCE_NAME="$PG_DSN"
    exec "$OBSHOME/postgres_exporter/postgres_exporter" \
        --web.listen-address="$NEWS_BIND_HOST:$NEWS_PG_EXPORTER_PORT"
}

serve_prom() {
    cmd_render >/dev/null
    exec "$OBSHOME/prometheus/prometheus" \
        --config.file="$OBSDATA/prometheus.yml" \
        --storage.tsdb.path="$OBSDATA/prometheus-data" \
        --storage.tsdb.retention.time=15d \
        --web.listen-address="$NEWS_BIND_HOST:$NEWS_PROM_PORT" \
        --web.enable-lifecycle
}

serve_grafana() {
    cmd_render >/dev/null
    export GF_PATHS_DATA="$OBSDATA/grafana/data"
    export GF_PATHS_LOGS="$OBSDATA/grafana/logs"
    export GF_PATHS_PLUGINS="$OBSDATA/grafana/plugins"
    export GF_PATHS_PROVISIONING="$OBSDATA/provisioning"
    export GF_SERVER_HTTP_ADDR="$NEWS_BIND_HOST"
    export GF_SERVER_HTTP_PORT="$NEWS_GRAFANA_PORT"
    export GF_ANALYTICS_REPORTING_ENABLED=false
    export GF_ANALYTICS_CHECK_FOR_UPDATES=false
    export GF_NEWS_NEWS_FEED_ENABLED=false
    # Stack dev chi nghe loopback, va trang trang thai o :18081 cung khong co
    # dang nhap. Mo xem tu do; sua dashboard thi van dang nhap admin/admin.
    export GF_AUTH_ANONYMOUS_ENABLED=true
    export GF_AUTH_ANONYMOUS_ORG_ROLE=Viewer
    exec "$OBSHOME/grafana/bin/grafana" server --homepath "$OBSHOME/grafana"
}

# ---------------------------------------------------------------- start/stop

# Chay nen + ghi PID. Danh cho lam tay; chay thuong tru thi dung systemd
# (systemd/install.sh) de duoc restart tu dong.
spawn() {
    local name="$1"
    local pidfile="$PIDDIR/$name.pid"
    if running "$name"; then
        log "  $name da chay (pid $(cat "$pidfile"))"
        return 0
    fi
    mkdir -p "$PIDDIR" "$OBSDATA/logs"
    # nohup KHONG phai trang tri: khi terminal dong, nhan SIGHUP thi Prometheus
    # va Grafana song sot (chung dung tin hieu do de reload config / xoay log)
    # con node_exporter va postgres_exporter chet ngay - im lang, khong log loi.
    # Da gap that: sau khi thoat shell chi con 2/4 tien trinh.
    nohup "$0" "serve-$name" >"$OBSDATA/logs/$name.log" 2>&1 </dev/null &
    echo $! > "$pidfile"
    log "  $name -> pid $!"
}

running() {
    local pidfile="$PIDDIR/$1.pid"
    [ -f "$pidfile" ] || return 1
    kill -0 "$(cat "$pidfile")" 2>/dev/null
}

cmd_start() {
    [ -x "$OBSHOME/prometheus/prometheus" ] \
        || die "chua cai. Chay: ./scripts/obs.sh install"
    cmd_render >/dev/null
    log "khoi dong:"
    spawn node
    spawn pgexp
    spawn prom
    spawn grafana

    # Kiem tra lai sau vai giay. Tien trinh chet yeu (cong bi chiem la pho bien
    # nhat) van kip de lai mot PID trong file, nen khong hoi lai thi `start` se
    # bao thanh cong cho mot thu da chet.
    sleep 4
    local chet=0
    for name in node pgexp prom grafana; do
        if ! running "$name"; then
            chet=1
            log "CANH BAO: $name khong tru duoc - xem $OBSDATA/logs/$name.log"
        fi
    done

    log ""
    log "Grafana:    http://$NEWS_BIND_HOST:$NEWS_GRAFANA_PORT"
    log "Prometheus: http://$NEWS_BIND_HOST:$NEWS_PROM_PORT"
    log "Nghiep vu (run/chi phi/nguon tin) van o http://$NEWS_BIND_HOST:$NEWS_STATUS_PORT"
    return "$chet"
}

cmd_stop() {
    for name in grafana prom pgexp node; do
        local pidfile="$PIDDIR/$name.pid"
        if running "$name"; then
            local pid
            pid="$(cat "$pidfile")"
            kill "$pid" 2>/dev/null || true
            # PHAI cho chet han truoc khi bao da dung. Grafana can vai giay moi
            # nha cong; khong cho thi `stop; start` dua voi chinh no - ban
            # grafana moi khong bind duoc :18091 roi thoat gan nhu khong log gi.
            local i
            for i in $(seq 1 40); do
                kill -0 "$pid" 2>/dev/null || break
                sleep 0.25
            done
            if kill -0 "$pid" 2>/dev/null; then
                kill -9 "$pid" 2>/dev/null || true
                log "da buoc dung $name (khong tu thoat sau 10s)"
            else
                log "da dung $name"
            fi
        fi
        rm -f "$pidfile"
    done
}

cmd_status() {
    printf "%-14s %-8s %s\n" "DICH VU" "CONG" "TRANG THAI"
    for entry in "node:$NEWS_NODE_EXPORTER_PORT" "pgexp:$NEWS_PG_EXPORTER_PORT" \
                 "prom:$NEWS_PROM_PORT" "grafana:$NEWS_GRAFANA_PORT"; do
        local name="${entry%%:*}" port="${entry##*:}"
        if running "$name"; then
            printf "%-14s %-8s dang chay (pid %s)\n" "$name" "$port" "$(cat "$PIDDIR/$name.pid")"
        else
            printf "%-14s %-8s dung\n" "$name" "$port"
        fi
    done
}

case "${1:-status}" in
    install)       cmd_install ;;
    render)        cmd_render ;;
    start)         cmd_start ;;
    stop)          cmd_stop ;;
    restart)       cmd_stop; cmd_start ;;
    status)        cmd_status ;;
    serve-node)    serve_node ;;
    serve-pgexp)   serve_pgexp ;;
    serve-prom)    serve_prom ;;
    serve-grafana) serve_grafana ;;
    *)
        echo "dung: $0 install|render|start|stop|restart|status|serve-{node,pgexp,prom,grafana}" >&2
        exit 2
        ;;
esac
