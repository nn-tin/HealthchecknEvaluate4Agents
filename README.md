# TicketAgents — Grafana monitoring

Healthcheck và evaluation có lịch độc lập, cùng dùng múi giờ `Asia/Ho_Chi_Minh`. Evaluation vẫn có thể chạy thủ công bằng CLI. Kết quả lưu JSON và PostgreSQL; Grafana hiển thị lịch sử, tiến độ batch và cảnh báo. Streamlit đã được gỡ; generate nằm ngoài phạm vi đợt này.

## Khởi động

Tại thư mục `TicketAgents`, giữ nguyên `.env` hiện có chứa Dify/Qwen credentials. Nếu cài mới, sao chép `.env.example` và điền credentials.

```powershell
Copy-Item observability/.env.example observability/.env
# Đổi bốn password trong observability/.env. Không ghi đè file đã cấu hình.
docker compose --env-file observability/.env -f observability/docker-compose.yml up -d --build
```

Trong phiên triển khai bằng Codex, `observability/.env` đã được tạo với bốn mật khẩu ngẫu nhiên riêng.

- Grafana: http://localhost:3000, user `admin`, password `GRAFANA_ADMIN_PASSWORD` trong `observability/.env`.
- Vào folder **TicketAgents** để xem Health Overview, Evaluation Quality, Latest Agent Runs và Run Detail.
- PostgreSQL: `localhost:5433`, database/user `ticketagents`. Grafana dùng `grafanareader`, chỉ có quyền SELECT.
- JSON download: `localhost:8081`, user `reports`, password `REPORT_PASSWORD`. Run Detail có link tải bản gốc chứa dữ liệu nghiệp vụ, dành cho người được phép debug.

Các port chỉ bind localhost. Truy cập từ máy khác cần reverse proxy/TLS và sửa URL download trong dashboard.

## CLI

```powershell
..\.venv\Scripts\python.exe -m pip install -r requirements.txt
..\.venv\Scripts\python.exe run.py db-init
..\.venv\Scripts\python.exe run.py healthcheck --agent all
..\.venv\Scripts\python.exe run.py eval --agent leave --cases data/seeds/leave.yaml
..\.venv\Scripts\python.exe run.py backfill --runs-dir data/runs
..\.venv\Scripts\python.exe run.py scheduler
```

Host CLI cần `DATABASE_URL=postgresql://ticketagents:<POSTGRES_PASSWORD>@localhost:5433/ticketagents` trong `.env`. Compose cấp cấu hình DB cho container. Dùng `RESULT_SINK=json` để chạy offline.

Chạy CLI trong container:

```powershell
docker compose --env-file observability/.env -f observability/docker-compose.yml exec scheduler python run.py healthcheck --agent all
docker compose --env-file observability/.env -f observability/docker-compose.yml exec scheduler python run.py backfill
```

## Lịch chạy và khôi phục

Trong file `.env` ở thư mục gốc `TicketAgents` (file này được nạp vào container qua `env_file: ../.env`):

```dotenv
HEALTHCHECK_SCHEDULES=08:00,12:30
HEALTHCHECK_TIMEZONE=Asia/Ho_Chi_Minh
HEALTHCHECK_AGENTS=leave,late,helpdesk,bookroom
HEALTHCHECK_WEEKDAYS=mon-sun
HEALTHCHECK_MISFIRE_GRACE_MINUTES=15
EVALUATION_SCHEDULES=17:30
EVALUATION_TIMEZONE=Asia/Ho_Chi_Minh
EVALUATION_AGENTS=leave,late,helpdesk,bookroom
EVALUATION_WEEKDAYS=mon-sun
EVALUATION_MISFIRE_GRACE_MINUTES=15
```

Mỗi evaluation batch tự nạp toàn bộ case trong `data/seeds/<agent>.yaml` của các agent thuộc `EVALUATION_AGENTS`. Đặt `*_WEEKDAYS=mon-fri` nếu chỉ chạy ngày làm việc. Sau khi đổi lịch, recreate service tương ứng:

```powershell
docker compose --env-file observability/.env -f observability/docker-compose.yml up -d --no-deps --force-recreate scheduler
docker compose --env-file observability/.env -f observability/docker-compose.yml up -d --no-deps --force-recreate evaluation-scheduler
```

Hai scheduler kiểm tra mỗi 15 giây và dùng PostgreSQL để chống chạy trùng. Máy và Docker Desktop phải hoạt động vào giờ chạy. Nếu khởi động muộn trong grace period, scheduler chạy slot gần nhất; quá thời hạn thì ghi nhận slot bị lỡ trên dashboard/cảnh báo.

Unique claim trong PostgreSQL chống trùng slot qua restart hoặc nhiều scheduler. Advisory lock ngăn batch chạy chồng. Bốn agent chạy tuần tự, một agent lỗi vẫn tiếp tục các agent còn lại. Healthy/Slow phản ánh phản hồi và kết nối; evaluation mới chấm chất lượng nội dung.

JSON được ghi nguyên tử trước DB. DB tạm hỏng vẫn giữ JSON; scheduler nhập lại run còn thiếu mỗi 5 phút. Backfill dùng upsert và transaction, chạy lại không tạo record trùng. PostgreSQL phải sẵn sàng để claim batch mới. Batch bị crash giữ trạng thái running và cảnh báo thiếu kết quả; không tự chạy lại slot đã claim.

## Dashboard và alert

- Health Overview: trạng thái mới nhất, thời điểm kiểm tra, TTFT, latency/P50/P95, state timeline, batch 08:00/12:30 và tool calls.
- Evaluation Quality: trạng thái batch theo lịch, PASS/FAIL/ERROR, pass rate, score, skipped/error metrics, rule failures và runtime.
- Latest Agent Runs: 8 thẻ trực quan cho lần healthcheck và evaluation case mới nhất của từng agent (trên toàn bộ lịch sử đã lưu), kèm thời gian, trạng thái, chỉ số chính và liên kết tới run/log.
- Run Detail: metric/reason, rule, transcript, tool trace và JSON gốc. Duration thiếu dữ liệu giữ NULL.

Rule đánh giá mỗi phút: batch quá hạn/thiếu agent, healthcheck Error, hai lần Slow liên tiếp, evaluation ERROR trong 24 giờ, pass rate <80% trong 7 ngày (tối thiểu 5 mẫu). Missing batch bắt đầu sau grace 15 phút và pending thêm 1 phút. Ngày đầu cài sau giờ chạy cũng có thể báo slot đã lỡ.

Alert hiển thị trong **Alerting → Alert rules**. Muốn nhận email/webhook, cấu hình contact point và notification policy trong Grafana. Chưa cấu hình người nhận nên chưa gửi thông báo ra ngoài. Dashboard/datasource/alert được provision từ file; chỉnh file nguồn để giữ thay đổi qua restart.

## Dữ liệu và vận hành

DB không lưu raw SSE hay tool input/output. Transcript mặc định có placeholder; đặt `DATABASE_STORE_TRANSCRIPT=true` để lưu nội dung. Mặc định nội dung được redaction; đặt thêm `DATABASE_REDACT_TRANSCRIPT=false` để lưu nguyên văn. Credential, email, UUID, internal identifier và tên/ID từ configured inputs được mask khi redaction được bật. JSON gốc giữ trong `data/runs`, không đưa lên Git.

API key trong `.env` không copy vào Docker image. Volumes giữ PostgreSQL/Grafana qua restart. `docker compose ... down` giữ dữ liệu; không dùng `down -v` khi cần giữ lịch sử. Mật khẩu reader được tạo lần đầu khởi tạo volume; đổi env sau đó cần đổi password role tương ứng.

## Kiểm thử

```powershell
..\.venv\Scripts\python.exe -m pytest tests -q
```

Đặt `TEST_DATABASE_URL` để bật integration tests. Mỗi test tạo và xóa schema riêng `monitoring_test_<uuid>`. Schema production ở `migrations/001_initial.sql`; cấu hình stack ở `observability/`.
