# Vận hành

Cách stack này chạy thường trú trên WSL: cổng, systemd, sao lưu, dọn dữ liệu và
trang trạng thái.

---

## 1. Cổng — một chỗ duy nhất

`config/ports.env`. Ba script (`pg.sh`, `airflow.sh`, `status.py`) đều đọc từ đây.

| Dịch vụ | Cổng | Vì sao không dùng mặc định |
|---|---|---|
| Postgres | **18432** | 5432 là cổng bị giành nhiều nhất trên máy dev |
| Airflow | **18080** | 8080 gần như chắc chắn đụng |
| Trang trạng thái | **18081** | |

Tất cả chỉ nghe `127.0.0.1`. Đổi cổng thì sửa `config/ports.env` rồi:

```bash
./scripts/ports.sh check    # xem cổng nào đang bị chiếm
systemctl --user restart news-postgres news-status
```

`pg.sh` tự viết lại `port` trong `postgresql.conf` khi phát hiện lệch, nên đổi
cổng không cần tạo lại cluster.

---

## 2. Tự khởi động bằng systemd

```bash
./systemd/install.sh            # cài + enable + start
./systemd/install.sh uninstall  # gỡ
```

| Unit | Việc |
|---|---|
| `news-postgres.service` | Postgres rootless, chạy foreground |
| `news-status.service` | trang trạng thái ở :18081 |
| `news-airflow.service` | scheduler + webserver (**không tự bật**) |
| `news-backup.timer` | sao lưu 02:00 hằng ngày |

Airflow cố tình không bật sẵn — bật khi muốn lịch chạy thật:

```bash
systemctl --user enable --now news-airflow.service
```

### Hai giới hạn thật của WSL

**Linger.** Unit người dùng dừng khi phiên WSL cuối cùng đóng. Bật một lần
(đây là bước duy nhất cần `sudo`):

```bash
sudo loginctl enable-linger $USER
```

**WSL không tự chạy khi Windows khởi động.** Kể cả có systemd và linger, không
gì chạy cho đến khi WSL được đánh thức. Tạo một Task trong Windows Task
Scheduler, trigger *At log on*, chạy:

```
wsl.exe -d Ubuntu-26.04 -u khanhxoe -e /bin/true
```

Lệnh đó chỉ khởi động WSL rồi thoát; systemd bên trong lo phần còn lại. Không
làm bước này thì "tự spinup" chỉ đúng khi m đã mở WSL.

### Vì sao unit Postgres viết như vậy

`Type=exec` chứ không phải `Type=forking`: `pg_ctl` fork rồi thoát ngay, nên khi
server đã chạy sẵn systemd sẽ chờ một cú fork không bao giờ đến và timeout sau
120 giây (đã gặp thật). Với `Type=exec`, systemd sở hữu chính tiến trình
`postgres`.

Không có `ExecStartPost`: tạo database ở đó chạy khi server chưa nhận kết nối,
và tiến trình con của nó kẹt trong cgroup làm lệnh `stop` treo tới lúc bị
SIGKILL (cũng đã gặp thật). Tạo database là việc cài đặt một lần, `install.sh` lo.

---

## 3. Sao lưu

```bash
./scripts/backup.sh          # tạo bản mới, giữ 7 bản gần nhất
./scripts/backup.sh list
./scripts/backup.sh restore ~/.local/share/news-bot/backups/news-YYYYmmdd-HHMMSS.sql.gz
```

Bản sao nằm ở `~/.local/share/news-bot/backups/`, gzip, ~456 KB mỗi bản.

### Vì sao không phải pgBackRest

pgBackRest làm WAL archiving và PITR cho cluster do root quản lý. Ở đây:

- Cluster chạy **rootless** từ gói binaries của `io.zonky.test`; cài pgBackRest
  cần `sudo apt`.
- Gói đó **không kèm `pg_dump`** — nên `scripts/pg_dump_lite.py` dump bằng
  `COPY ... TO STDOUT` qua psycopg, không cần binary ngoài.
- Dữ liệu nghiệp vụ **dựng lại được từ feed**. Thứ thực sự không tái tạo được
  chỉ là lịch sử trace và bản tin đã gửi — một bản dump logic mỗi ngày là đủ.

Nếu sau này cần PITR thật (mất dữ liệu tính bằng phút là không chấp nhận được),
lúc đó pgBackRest mới đáng: cần `sudo apt install pgbackrest`, bật
`archive_mode=on` + `archive_command` trong `postgresql.conf`, và một repo path
riêng. Đó là thay đổi về mô hình vận hành, không phải thêm một script.

---

## 4. Dọn dữ liệu

Giữ **14 ngày** (`RETENTION_DAYS`). Chạy tự động như một task Airflow sau khi gửi.

```bash
news-bot db-size              # kích thước từng bảng
news-bot purge --dry-run      # đếm, không xoá
news-bot purge                # xoá + VACUUM
```

`pipeline_run` là gốc của cây trace — xoá một run sẽ CASCADE sang `node_span`,
`llm_call`, `app_log`, `source_health`, `article_summary` và `digest`. Bảng
`article` độc lập nên xoá riêng, và **chỉ xoá bài không còn bản tin nào trỏ tới**,
để bản tin đã gửi không bị rỗng ruột.

---

## 5. Trang trạng thái

<http://localhost:18081> — xem được từ cả Windows lẫn WSL, tự làm mới mỗi 60 giây.

Hiển thị: trạng thái lần chạy gần nhất, chi phí LLM 7 ngày, nội dung từng bản
tin (bấm mở để xem danh sách tin và link), sức khoẻ 42 nguồn, dung lượng từng bảng.

Ngoài ra: `/health` trả JSON cho monitoring, `/api/runs` trả JSON các lần chạy.

Dùng `http.server` của thư viện chuẩn — không thêm FastAPI/Flask cho một trang
chỉ đọc. Chỉ nghe loopback.

---

## 6. Kiểm tra nhanh khi có sự cố

```bash
systemctl --user status news-postgres news-status news-airflow
journalctl --user -u news-postgres -n 50 --no-pager
./scripts/ports.sh                    # cổng có bị chiếm không
news-bot check-sources                # feed nào chết/đóng băng
curl -s localhost:18081/health
```
