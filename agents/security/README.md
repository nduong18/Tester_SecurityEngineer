# Security Agent (`security-01`, port 8010)

Agent bị động: chỉ nhận `POST /tasks` từ PM Conductor, không tự gọi agent khác.

| Action | Input artifact | Output artifact |
|---|---|---|
| `security_review` | `SOURCE_CODE` (bắt buộc), `ARCHITECTURE` (tuỳ chọn) | `security-report.md` (`SECURITY_REPORT`) |

## Cách rà soát

1. Giải nén `SOURCE_CODE` an toàn vào `workspace/<task_id>/` (chặn Zip-Slip, zip bomb).
2. **Rule tích hợp** (`tools/scanner.py`, luôn chạy, không cần cài thêm): secret/private key/AWS key hardcode, file `.env` / `.pem` bị đóng gói, `eval/exec`, `shell=True`, `os.system`, `pickle.loads`, `yaml.load`, SQL ghép chuỗi, `verify=False`, `debug=True`, CORS `*`, MD5/SHA1, `innerHTML`, JWT `none`.
3. **bandit** và **pip-audit**: chạy nếu đã `pip install bandit pip-audit` và có tên trong `ALLOWED_COMMANDS` (pip-audit cần Internet).
4. **LLM** (nếu có `LLM_API_KEY` và có input `ARCHITECTURE`): threat model ngắn theo OWASP/STRIDE, chỉ để tham khảo, **không** ảnh hưởng verdict (tránh LLM báo nhầm làm kẹt Fix Loop).

`verdict = FAIL` khi có ít nhất 1 finding `HIGH`/`CRITICAL`, khớp với `policy.should_trigger_fix_loop` của PM.

```json
{"verdict": "FAIL", "summary": "FAIL: 0 CRITICAL, 3 HIGH, ...",
 "findings": [{"id": "SEC-SQLI", "severity": "HIGH", "file": "app/db.py", "line": 8,
               "title": "SQL ghép chuỗi (SQL injection)", "recommendation": "Dùng parameterized query"}],
 "stats": {"CRITICAL": 0, "HIGH": 3, "MEDIUM": 1, "LOW": 0, "INFO": 0, "files_scanned": 12}}
```

## Chạy

```bash
cp .env.example .env        # điền AGENT_API_KEY
python -m uvicorn api:app --host 0.0.0.0 --port 8010
pytest tests -q
```
