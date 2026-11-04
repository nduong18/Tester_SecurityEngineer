# CẨM NANG THIẾT LẬP KẾT NỐI VÀ CẤU HÌNH 10 AGENT TRÊN 5 MÁY
### Multi-Machine Connectivity & Network Deployment Guide
*Dự án: **ai-team** | Phiên bản chuẩn hóa: **Agent Contract v1.1.0** & **Agent API v1***

---

## 1. TỔNG QUAN KIẾN TRÚC MẠNG VÀ NGUYÊN TẮC KẾT NỐI

Dự án **ai-team** bao gồm 10 AI Agent độc lập được phát triển và vận hành trên 5 máy tính vật lý khác nhau trong mạng nội bộ (LAN) hoặc qua mạng ảo Tailscale VPN.

```
                           +-----------------------------------------------+
                           |                 MÁY 1 (Leader)                |
                           |  [pm-01 : 8001]   <--->  [devops-01 : 8002]    |
                           +-----------------------------------------------+
                                      |                      |
                    (HTTP Agent API v1)                      (HTTP Agent API v1)
                                      v                      v
        +--------------------------------+        +--------------------------------+
        |             MÁY 2              |        |             MÁY 3              |
        | [ba-01: 8003]  [sa-01: 8004]   |        | [po-01: 8005]  [dev-01: 8006]  |
        +--------------------------------+        +--------------------------------+
                                      |                      |
                                      v                      v
        +--------------------------------+        +--------------------------------+
        |             MÁY 4              |        |             MÁY 5              |
        | [tester-01: 8007] [dba-01: 8008]       | [scrum_master-01: 8009]        |
        |                                |        | [security-01: 8010]            |
        +--------------------------------+        +--------------------------------+
```

### Các nguyên tắc cốt lõi:
1. **Kiến trúc Không chia sẻ ổ đĩa (Shared-Nothing Architecture)**: Không có thư mục dùng chung giữa các máy. Mọi sự trao đổi thông tin, tài liệu và mã nguồn đều đi qua giao thức HTTP REST API chuẩn (**Agent API v1**).
2. **Quy ước chuyển giao file (Artifact Relaying)**:
   - Mã nguồn (`SOURCE_CODE`) được đóng gói thành file `.zip`.
   - File được trung chuyển qua API: Tải về qua `GET /artifacts/{id}/content` và đẩy vào `POST /artifacts/inbox`.
   - Dung lượng upload tối đa cho phép là `MAX_UPLOAD_MB=50`.
   - Tính toàn vẹn được chứng thực bằng mã băm SHA-256 (`checksum` trong metadata và header `X-Checksum-SHA256`).
3. **Cơ chế điều phối (Orchestration)**:
   - **DUY NHẤT PM Agent (`pm-01`)** thông qua module Conductor được quyền chủ động gọi HTTP tới các Agent khác để giao việc và trung chuyển artifact.
   - **9 Agent còn lại** hoạt động hoàn toàn bị động (Passive): chỉ lắng nghe request trên `0.0.0.0:<PORT>`, thực thi tác vụ và trả về kết quả; tuyệt đối không tự ý gọi agent khác.
4. **Bảo mật và Xác thực (Authentication)**:
   - Mọi request (trừ `GET /health`) bắt buộc phải có header `Authorization: Bearer <AGENT_API_KEY>`.
   - Xác thực an toàn bằng hàm so sánh thời gian thực `hmac.compare_digest` để chống tấn công Timing Attack.

---

## 2. MA TRẬN PHÂN BỔ 10 AGENT TRÊN 5 MÁY

| Máy | Agent ID | Vai trò (Role) | Cổng (Port) | Host Lắng nghe | Nhiệm vụ chính trong Pipeline |
|:---:|:---:|:---:|:---:|:---:|:---|
| **Máy 1** | `pm-01` | `PM` | `8001` | `0.0.0.0` | Điều phối tiến độ, quản lý vòng đời dự án, trung chuyển artifact, phê duyệt Gate |
| **Máy 1** | `devops-01` | `DEVOPS` | `8002` | `0.0.0.0` | Tạo CI/CD config, build Docker image, deploy staging/production, kiểm tra sức khỏe hệ thống |
| **Máy 2** | `ba-01` | `BA` | `8003` | `0.0.0.0` | Viết đặc tả yêu cầu phần mềm (SRS), phân rã danh sách User Stories kèm Acceptance Criteria |
| **Máy 2** | `sa-01` | `SA` | `8004` | `0.0.0.0` | Thiết kế kiến trúc tổng thể hệ thống, component diagrams, công nghệ tích hợp |
| **Máy 3** | `po-01` | `PO` | `8005` | `0.0.0.0` | Định nghĩa Product Backlog ban đầu, nghiệm thu sản phẩm cuối cùng (Accept Release) |
| **Máy 3** | `dev-01` | `DEV` | `8006` | `0.0.0.0` | Lập trình tính năng, đóng gói file `.zip` mã nguồn (`SOURCE_CODE`), sửa lỗi theo Fix Loop |
| **Máy 4** | `tester-01` | `TESTER` | `8007` | `0.0.0.0` | Viết kịch bản kiểm thử tự động (`TEST_CASE`), chạy test suite và xuất báo cáo verdict PASS/FAIL |
| **Máy 4** | `dba-01` | `DBA` | `8008` | `0.0.0.0` | Thiết kế mô hình dữ liệu quan hệ, viết câu lệnh DDL `schema.sql`, kiểm soát toàn vẹn dữ liệu |
| **Máy 5** | `scrum_master-01` | `SCRUM_MASTER` | `8009` | `0.0.0.0` | Theo dõi blocker, tổ chức Sprint Retrospective và tổng kết kinh nghiệm dự án |
| **Máy 5** | `security-01` | `SECURITY` | `8010` | `0.0.0.0` | Rà soát lỗ hổng an toàn thông tin, bảo mật mã nguồn, phát hiện rủi ro HIGH/CRITICAL |

---

## 3. THIẾT LẬP ĐỊA CHỈ IP VÀ MẠNG NỘI BỘ

Chọn 1 trong 2 phương án tùy theo điều kiện làm việc của nhóm:

### Phương án A: Cùng phòng / Cùng mạng Wi-Fi (LAN)
1. **Kiểm tra địa chỉ IP của máy:**
   - Trên Windows: Chạy lệnh `ipconfig` (tìm dòng `IPv4 Address`, ví dụ: `192.168.1.101`).
   - Trên Linux/macOS: Chạy lệnh `ip a` hoặc `ifconfig`.
2. **Cố định IP:** Thiết lập DHCP Reservation trên Router Wi-Fi hoặc gán Static IP cho card mạng để IP không bị thay đổi sau khi khởi động lại:
   - Máy 1: `192.168.1.101`
   - Máy 2: `192.168.1.102`
   - Máy 3: `192.168.1.103`
   - Máy 4: `192.168.1.104`
   - Máy 5: `192.168.1.105`

### Phương án B: Làm việc từ xa / Khác mạng Wi-Fi (Khuyên dùng Tailscale VPN)
- Cài đặt [Tailscale](https://tailscale.com/) trên cả 5 máy và đăng nhập cùng một tài khoản (chung 1 Tailnet).
- Mỗi máy sẽ tự động nhận một IP cố định dạng `100.x.y.z`.
- **Ưu điểm vượt trội**: Không cần cấu hình Router, không cần mở port modem, đường truyền được mã hóa đầu-cuối an toàn, tự động xuyên qua mọi lớp tường lửa của nhà mạng.

---

## 4. MỞ CỔNG TƯỜNG LỬA (FIREWALL) TRÊN TỪNG MÁY

Agent chạy dịch vụ Uvicorn lắng nghe trên `HOST=0.0.0.0`. Tường lửa của hệ điều hành cần cho phép các máy khác kết nối vào đúng cổng được phân bổ.

### Trên Windows (Mở PowerShell với quyền **Administrator**):

```powershell
# Máy 1 (Mở cổng 8001 và 8002):
New-NetFirewallRule -DisplayName "AI-Team (Machine 1: PM, DevOps)" -Direction Inbound -LocalPort 8001,8002 -Protocol TCP -Action Allow

# Máy 2 (Mở cổng 8003 và 8004):
New-NetFirewallRule -DisplayName "AI-Team (Machine 2: BA, SA)" -Direction Inbound -LocalPort 8003,8004 -Protocol TCP -Action Allow

# Máy 3 (Mở cổng 8005 và 8006):
New-NetFirewallRule -DisplayName "AI-Team (Machine 3: PO, DEV)" -Direction Inbound -LocalPort 8005,8006 -Protocol TCP -Action Allow

# Máy 4 (Mở cổng 8007 và 8008):
New-NetFirewallRule -DisplayName "AI-Team (Machine 4: TESTER, DBA)" -Direction Inbound -LocalPort 8007,8008 -Protocol TCP -Action Allow

# Máy 5 (Mở cổng 8009 và 8010):
New-NetFirewallRule -DisplayName "AI-Team (Machine 5: SCRUM_MASTER, SECURITY)" -Direction Inbound -LocalPort 8009,8010 -Protocol TCP -Action Allow
```

### Trên Linux (UFW):

```bash
# Máy 1:
sudo ufw allow 8001/tcp && sudo ufw allow 8002/tcp && sudo ufw reload

# Máy 2:
sudo ufw allow 8003/tcp && sudo ufw allow 8004/tcp && sudo ufw reload

# Máy 3:
sudo ufw allow 8005/tcp && sudo ufw allow 8006/tcp && sudo ufw reload

# Máy 4:
sudo ufw allow 8007/tcp && sudo ufw allow 8008/tcp && sudo ufw reload

# Máy 5:
sudo ufw allow 8009/tcp && sudo ufw allow 8010/tcp && sudo ufw reload
```

---

## 5. HƯỚNG DẪN THIẾT LẬP TRÊN TỪNG MÁY (1-MINUTE SETUP)

### Bước 1: Chuẩn bị môi trường chung (Áp dụng cho cả 5 máy)

```powershell
# 1. Clone repository về máy
git clone <URL_REPO_CUA_NHOM> ai-team
cd ai-team

# 2. Khởi tạo môi trường ảo Python 3.11+
python -m venv venv
.\venv\Scripts\Activate.ps1    # Trên Windows
# source venv/bin/activate     # Trên Linux/macOS

# 3. Cài đặt gói lõi contract và các thư viện phụ thuộc đã ghim cứng
pip install -e ./contract
pip install -r agent-template/requirements.txt
```

---

### Bước 2: Thiết lập cấu hình tự động bằng tiện ích `configure_lan.py`

Thay vì tự copy và chỉnh sửa thủ công các file `.env` dễ bị nhầm lẫn cổng hay thiếu biến, hãy chạy lệnh thiết lập tự động theo số máy của bạn:

#### Nếu bạn ngồi ở **Máy 1** (PM & DevOps):
```powershell
python scripts/configure_lan.py setup-machine 1
```

#### Nếu bạn ngồi ở **Máy 2** (BA & SA):
```powershell
python scripts/configure_lan.py setup-machine 2
```

#### Nếu bạn ngồi ở **Máy 3** (PO & DEV):
```powershell
python scripts/configure_lan.py setup-machine 3
```

#### Nếu bạn ngồi ở **Máy 4** (TESTER & DBA):
```powershell
python scripts/configure_lan.py setup-machine 4
```

#### Nếu bạn ngồi ở **Máy 5** (SCRUM_MASTER & SECURITY):
```powershell
python scripts/configure_lan.py setup-machine 5
```

> **Tiện ích sẽ tự động thực hiện:**
> 1. Tạo file `.env` cho cả 2 Agent của máy đó với đúng `PORT`, `AGENT_ID`, `AGENT_ROLE`, `HOST=0.0.0.0`, `MAX_UPLOAD_MB=50`.
> 2. Sinh ngẫu nhiên mã khóa bảo mật Bearer Token an toàn (32 ký tự ngẫu nhiên).
> 3. In ra màn hình dòng biến `AGENT_API_KEY_<ID>=...` để bạn gửi cho người quản lý Máy 1 (PM).

---

### Bước 3: Cấu hình LLM API Key trên từng Agent

Mở file `.env` của từng agent trên máy bạn (ví dụ: `agents/ba/.env` và `agents/sa/.env`), điền API Key thực tế của bạn:

```properties
# Ví dụ dùng Groq (khuyến nghị cho tốc độ phản hồi nhanh):
LLM_PROVIDER=groq
LLM_MODEL=llama-3.3-70b-versatile
LLM_API_KEY=gsk_your_groq_api_key_here

# Hoặc dùng Google Gemini Flash:
# LLM_PROVIDER=gemini
# LLM_MODEL=gemini-2.5-flash
# LLM_API_KEY=AIzaSy_your_gemini_key_here
```

---

### Bước 4: Khởi động 2 Agent trên máy của bạn

Mỗi agent cần chạy trên một cửa sổ Terminal riêng (hoặc chạy nền):

#### Trên Máy 1:
```powershell
# Terminal 1: PM Agent
cd agents/pm
python -m uvicorn api:app --host 0.0.0.0 --port 8001

# Terminal 2: DevOps Agent
cd agents/devops
python -m uvicorn api:app --host 0.0.0.0 --port 8002
```

#### Trên Máy 2:
```powershell
# Terminal 1: BA Agent
cd agents/ba
python -m uvicorn api:app --host 0.0.0.0 --port 8003

# Terminal 2: SA Agent
cd agents/sa
python -m uvicorn api:app --host 0.0.0.0 --port 8004
```

#### Trên Máy 3:
```powershell
# Terminal 1: PO Agent
cd agents/po
python -m uvicorn api:app --host 0.0.0.0 --port 8005

# Terminal 2: DEV Agent
cd agents/dev
python -m uvicorn api:app --host 0.0.0.0 --port 8006
```

#### Trên Máy 4:
```powershell
# Terminal 1: TESTER Agent
cd agents/tester
python -m uvicorn api:app --host 0.0.0.0 --port 8007

# Terminal 2: DBA Agent
cd agents/dba
python -m uvicorn api:app --host 0.0.0.0 --port 8008
```

#### Trên Máy 5:
```powershell
# Terminal 1: SCRUM_MASTER Agent
cd agents/scrum_master
python -m uvicorn api:app --host 0.0.0.0 --port 8009

# Terminal 2: SECURITY Agent
cd agents/security
python -m uvicorn api:app --host 0.0.0.0 --port 8010
```

---

## 6. THIẾT LẬP KẾT NỐI TOÀN MẠNG (DÀNH CHO MÁY 1 / PM)

Để PM Conductor hoặc công cụ kiểm thử `lan_client.py` có thể kết nối tới các máy khác, Máy 1 cần 2 file cấu hình tại thư mục gốc:

### 1. File `scripts/endpoints.yaml` (Bảng địa chỉ toàn mạng)
Tạo file `scripts/endpoints.yaml` (copy từ `scripts/endpoints.example.yaml` hoặc dùng lệnh sinh tự động):

```powershell
# Lệnh sinh tự động với danh sách IP thực tế của 5 máy:
python scripts/configure_lan.py gen-endpoints --ip1 192.168.1.101 --ip2 192.168.1.102 --ip3 192.168.1.103 --ip4 192.168.1.104 --ip5 192.168.1.105
```

Nội dung file `scripts/endpoints.yaml`:
```yaml
pm-01: "http://192.168.1.101:8001"
devops-01: "http://192.168.1.101:8002"
ba-01: "http://192.168.1.102:8003"
sa-01: "http://192.168.1.102:8004"
po-01: "http://192.168.1.103:8005"
dev-01: "http://192.168.1.103:8006"
tester-01: "http://192.168.1.104:8007"
dba-01: "http://192.168.1.104:8008"
scrum_master-01: "http://192.168.1.105:8009"
security-01: "http://192.168.1.105:8010"
```

### 2. File `.env.lan` (Bảng Bearer Token xác thực)
Tạo file `.env.lan` tại gốc repo (file này đã nằm trong `.gitignore` để tránh rò rỉ lên Git). Điền các token do các thành viên gửi về:

```properties
# .env.lan - Chứa Token bí mật để gọi chéo các Agent qua mạng LAN
AGENT_API_KEY_PM_01=token_cua_pm_tren_may_1
AGENT_API_KEY_DEVOPS_01=token_cua_devops_tren_may_1
AGENT_API_KEY_BA_01=token_cua_ba_tren_may_2
AGENT_API_KEY_SA_01=token_cua_sa_tren_may_2
AGENT_API_KEY_PO_01=token_cua_po_tren_may_3
AGENT_API_KEY_DEV_01=token_cua_dev_tren_may_3
AGENT_API_KEY_TESTER_01=token_cua_tester_tren_may_4
AGENT_API_KEY_DBA_01=token_cua_dba_tren_may_4
AGENT_API_KEY_SCRUM_MASTER_01=token_cua_sm_tren_may_5
AGENT_API_KEY_SECURITY_01=token_cua_security_tren_may_5
```

> [!TIP]
> Mỗi thành viên chỉ cần chạy lệnh `python scripts/configure_lan.py export-tokens` trên máy của họ, sau đó copy kết quả gửi vào nhóm chat chung để người quản lý PM dán vào file `.env.lan`.

---

## 7. KIỂM TRA BẮT TAY TOÀN BỘ MẠNG LAN (VERIFICATION & HANDSHAKE)

Sau khi cả 5 máy đã bật đủ 10 agent, thực hiện kiểm tra kết nối từ Máy 1:

### 1. Kiểm tra tự động toàn bộ 10 Agent bằng `lan_check.py`:
```powershell
python scripts/lan_check.py
```
Script sẽ tự động thực hiện 4 bước kiểm tra với từng agent:
- [x] **TCP Connect**: Kiểm tra thông mạng và mở cổng Firewall.
- [x] **HTTP /health**: Kiểm tra agent có phản hồi 200 OK công khai.
- [x] **Bearer Auth**: Kiểm tra token bí mật (`/agent` với 401 khi không có token và 200 khi có token).
- [x] **Artifact Inbox Upload**: Đẩy thử một file test và kiểm tra SHA-256.

Báo cáo kết quả sẽ hiển thị bảng trạng thái và lưu chi tiết tại `docs/lan-check-report.md`.

---

### 2. Thử nghiệm gọi thủ công bằng `lan_client.py`:

```powershell
# 1. Kiểm tra sức khỏe của tất cả agent:
python scripts/lan_client.py health all

# 2. Đẩy thử 1 artifact vào inbox của DEV Agent trên Máy 3:
python scripts/lan_client.py push-artifact dev-01 README.md --type DOCUMENTATION --created-by pm-01

# 3. Kéo thử artifact từ DEV Agent về máy:
python scripts/lan_client.py pull-artifact dev-01 <artifact_id> --out ./temp_downloads/
```

---

### 3. Chạy thử nghiệm quy trình phân phối phần mềm thực tế bằng PM Conductor:

Từ Máy 1, ra lệnh cho PM điều phối tự động toàn bộ chuỗi 16 bước:

```powershell
cd agents/pm

# Khởi động dự án tự động:
python -m cli project start --brief-file ../../examples/brief.md --auto-approve

# Theo dõi tiến độ thời gian thực:
python -m cli project status <project_id> --watch
```

---

## 8. CẨM NANG KHẮC PHỤC SỰ CỐ THƯỜNG GẶP (TROUBLESHOOTING)

| Hiện tượng / Mã lỗi | Nguyên nhân | Cách khắc phục ngay lập tức |
|---|---|---|
| **`Connection refused`**<br>`[WinError 10061]` | Agent đích chưa khởi động, hoặc trong file `.env` đang để `HOST=127.0.0.1` thay vì `0.0.0.0`. | 1. Đảm bảo Terminal chạy Uvicorn vẫn đang mở.<br>2. Sửa `.env` thành `HOST=0.0.0.0`.<br>3. Khởi động lại Uvicorn với cờ `--host 0.0.0.0`. |
| **`Connection timed out`**<br>`[WinError 10060]` | Tường lửa (Firewall) trên máy đích đang chặn kết nối đến, hoặc sai địa chỉ IP. | 1. Mở PowerShell Administrator trên máy đích và chạy lệnh mở cổng ở **Mục 4**.<br>2. Ping thử IP máy đích: `Test-NetConnection -ComputerName <IP> -Port <PORT>`. |
| **Agent từ chối khởi động:**<br>`Khi HOST khác 127.0.0.1 bắt buộc phải có AGENT_API_KEY` | Tính năng bảo vệ tự động của Agent Contract v1.1.0 phát hiện `HOST=0.0.0.0` nhưng chưa có khóa bảo mật. | Mở file `.env` của agent đó và thêm `AGENT_API_KEY=<chuỗi_token_bảo_mật>` (tối thiểu 16 ký tự). |
| **`HTTP 401 Unauthorized`** | Bearer Token gửi đi không khớp với `AGENT_API_KEY` trong file `.env` của agent đích. | 1. Kiểm tra lại giá trị token trong file `.env.lan` của máy gửi.<br>2. So sánh với `AGENT_API_KEY` trong file `.env` của agent đích.<br>3. Chạy `python scripts/configure_lan.py export-tokens` để đồng bộ lại. |
| **`HTTP 409 Conflict`** | Agent đang bận xử lý một tác vụ khác (Concurrency Lock). | Chờ tác vụ trước đó hoàn thành hoặc kiểm tra trạng thái qua `GET /state`. Mỗi Agent chỉ xử lý một task tại một thời điểm. |
| **`HTTP 422 Checksum mismatch`** | Checksum SHA-256 tính từ file tải lên không khớp với trường `checksum` trong metadata JSON. | Đảm bảo file không bị hỏng trong quá trình truyền tải mạng; không can thiệp sửa đổi nội dung file sau khi đã tính hash. |
| **`HTTP 413 Payload Too Large`** | Dung lượng file upload vượt quá giới hạn cho phép. | Kiểm tra biến `MAX_UPLOAD_MB=50` trong file `.env` của agent đích. |

---

## 9. TỔNG KẾT DANH MỤC LỆNH NHANH

```powershell
# 1. Sinh cấu hình máy cá nhân (chọn số máy 1-5):
python scripts/configure_lan.py setup-machine <1..5>

# 2. Xuất token của máy để gửi cho PM:
python scripts/configure_lan.py export-tokens

# 3. Sinh bảng endpoints toàn mạng (chỉ chạy trên Máy PM):
python scripts/configure_lan.py gen-endpoints --ip1 <IP1> --ip2 <IP2> --ip3 <IP3> --ip4 <IP4> --ip5 <IP5>

# 4. Kiểm tra sức khỏe toàn bộ 10 agent qua mạng:
python scripts/lan_check.py

# 5. Khởi động dự án tự động bằng PM Conductor:
cd agents/pm && python -m cli project start --brief-file ../../examples/brief.md --auto-approve
```
