# Tester & Security Engineer Agents

Phần việc **TESTER** + **SECURITY** trong hệ thống `ai-team` (10 agent, 5 máy, PM điều phối qua LAN).
Theo cùng chuẩn **Agent Contract v1.1.0 / Agent API v1** với các repo
[vietanh910/AGENT](https://github.com/vietanh910/AGENT) (PM, DevOps) và [QuyFox/SA-PO](https://github.com/QuyFox/SA-PO) (SA, PO).

| Agent | ID | Port | Action PM gọi (workflow `software_delivery`) | Artifact trả về |
|---|---|---|---|---|
| Tester | `tester-01` | 8007 | `create_test_cases`, `run_test_suite` | `TEST_CASE`, `TEST_REPORT` |
| Security | `security-01` | 8010 | `security_review` | `SECURITY_REPORT` |

## Vị trí trong luồng làm việc

```
BA (user stories) ──► tester.create_test_cases ──► DEV implement_project (.zip)
                                                        │
                       tester.run_test_suite ◄──────────┘
                              │ verdict FAIL ──► PM Fix Loop: dev.fix_bugs ──► chạy lại test
                              ▼ PASS
                       security.security_review
                              │ có HIGH/CRITICAL ──► PM Fix Loop: dev.fix_bugs ──► test + review lại
                              ▼ PASS
                       devops.generate_ci_config ...
```

Hai agent đều **bị động**: chỉ nhận task từ PM, không gọi agent khác. File đầu vào (ví dụ `source.zip` của DEV)
được PM đẩy vào `artifacts/inbox/<agent_nguồn>/` qua `POST /artifacts/inbox`. Kết quả trả về luôn có
`outputs[0].verdict` (`PASS`/`FAIL`) và `outputs[0].findings[]` (có `severity`), khớp với `policy.parse_verdict` của PM.

Chi tiết từng agent: [agents/tester/README.md](agents/tester/README.md), [agents/security/README.md](agents/security/README.md).

## Cấu trúc

```
contract/            Gói Agent Contract dùng chung (copy từ repo AGENT, không sửa)
config/              agents_registry.json của cả team
agents/tester/       Tester Agent  (agent.py: logic, tools/workspace.py: giải nén an toàn)
agents/security/     Security Agent (tools/scanner.py: rule quét + bandit/pip-audit)
scripts/             run_local.py (up/status/down), local_agents.yaml, configure_lan.py
tools/validate_agent.py  Bộ chấm 14 tiêu chí Contract của team
CONNECTIVITY_GUIDE.md    Hướng dẫn kết nối LAN 5 máy
```

## Chạy nhanh

```bash
python -m venv .venv
.venv/Scripts/activate        # Linux/macOS: source .venv/bin/activate
pip install -e ./contract -r agents/tester/requirements.txt
pip install bandit pip-audit  # tuỳ chọn, giúp Security quét sâu hơn
```

Tạo `.env` cho mỗi agent từ `.env.example` và điền `AGENT_API_KEY` (gửi token này cho người chạy PM để điền vào `.env.lan`):

```bash
cp agents/tester/.env.example agents/tester/.env
cp agents/security/.env.example agents/security/.env
```

```bash
pytest agents/tester/tests agents/security/tests -q
python tools/validate_agent.py
python scripts/run_local.py up
```

Mở firewall cho máy khác trong LAN gọi tới (PowerShell Admin):

```powershell
New-NetFirewallRule -DisplayName "AI-Team (Tester & Security)" -Direction Inbound -LocalPort 8007,8010 -Protocol TCP -Action Allow
```

PM cần thêm vào `endpoints.yaml`: `tester-01: "http://<IP>:8007"` và `security-01: "http://<IP>:8010"`.
