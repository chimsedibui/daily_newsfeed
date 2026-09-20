#!/usr/bin/env bash
# Cai systemd user units cho news-bot. Khong can sudo.
#
#   ./systemd/install.sh            # cai + enable + start
#   ./systemd/install.sh uninstall  # go
#
# Luu y ve WSL: unit nguoi dung chi song khi co phien cua user. De chung chay
# ma khong can mo terminal, can bat linger MOT LAN (buoc duy nhat can sudo):
#     sudo loginctl enable-linger $USER
# Va WSL khong tu khoi dong cung Windows - xem docs/OPERATIONS.md.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$HOME/.config/systemd/user"
UNITS=(news-postgres.service news-airflow.service news-status.service
       news-backup.service news-backup.timer
       news-node-exporter.service news-pg-exporter.service
       news-prometheus.service news-grafana.service)

if [ "${1:-install}" = "uninstall" ]; then
    systemctl --user disable --now news-airflow.service news-status.service \
        news-grafana.service news-prometheus.service news-pg-exporter.service \
        news-node-exporter.service news-postgres.service news-backup.timer 2>/dev/null || true
    for u in "${UNITS[@]}"; do rm -f "$DEST/$u"; done
    systemctl --user daemon-reload
    echo "da go cac unit"
    exit 0
fi

mkdir -p "$DEST"
for u in "${UNITS[@]}"; do
    sed "s|__REPO__|$REPO|g" "$REPO/systemd/$u" > "$DEST/$u"
    echo "da cai $u"
done

systemctl --user daemon-reload
# reset-failed: lan cai truoc co the de lai trang thai failed lam `start` bi tu choi.
systemctl --user reset-failed news-postgres.service news-status.service 2>/dev/null || true

systemctl --user enable --now news-postgres.service

# Doi server nhan ket noi roi tao database. Lam o day chu khong phai trong unit:
# ExecStartPost chay khi server chua san sang va lam lenh stop bi treo.
for _ in $(seq 1 30); do
    if bash "$REPO/scripts/pg.sh" sql "SELECT 1" >/dev/null 2>&1; then break; fi
    sleep 1
done
bash "$REPO/scripts/pg.sh" ensure-db || true

systemctl --user enable --now news-status.service
systemctl --user enable --now news-backup.timer
echo
echo "Airflow chua duoc bat tu dong. Bat khi muon lich chay that:"
echo "  systemctl --user enable --now news-airflow.service"
echo
# Cung ly do nhu Airflow: khong bat san. Ngoai ra bon unit nay can binaries
# duoc tai ve truoc, neu bat ma chua cai thi chung se restart-loop.
echo "Quan sat tai nguyen may (Grafana) cung chua bat. Cai binaries roi bat:"
echo "  ./scripts/obs.sh install"
echo "  systemctl --user enable --now news-node-exporter.service news-pg-exporter.service"
echo "  systemctl --user enable --now news-prometheus.service news-grafana.service"
echo
if [ "$(loginctl show-user "$USER" --property=Linger --value 2>/dev/null)" != "yes" ]; then
    echo "CANH BAO: linger dang TAT - cac unit se dung khi ban dong phien WSL."
    echo "  sudo loginctl enable-linger $USER"
fi
