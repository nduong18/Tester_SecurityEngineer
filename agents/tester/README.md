# Tester Agent (`tester-01`, port 8007)

Agent bị động: chỉ nhận `POST /tasks` từ PM Conductor, không tự gọi agent khác.

| Action | Input artifact | Output artifact | Việc làm |
|---|---|---|---|
| `create_test_cases` | `USER_STORY` / `REQUIREMENT` (tuỳ chọn) | `test-cases.json` (`TEST_CASE`) | Sinh test case functional / negative / boundary / security. Có `LLM_API_KEY` thì dùng LLM, không thì sinh offline từ các dòng gạch đầu dòng / Given-When-Then. |
| `run_test_suite` | `SOURCE_CODE` (.zip hoặc file), `TEST_CASE` | `test-report.md` (`TEST_REPORT`) | Giải nén an toàn vào `workspace/<task_id>/`, kiểm tra cú pháp mọi file `.py`, chạy `pytest` (nếu có test), xuất verdict. |

`outputs[0]` của `run_test_suite` có dạng PM Conductor đọc được:

```json
{"verdict": "FAIL", "summary": "...", "stats": {"tests": 3, "passed": 2, "failed": 1},
 "findings": [{"id": "TEST-001", "severity": "HIGH", "title": "Test FAILED: ...", "file": "...", "line": 12}]}
```

- Lỗi cú pháp, test fail, pytest crash/timeout → `HIGH` → `verdict=FAIL` → PM mở Fix Loop (`dev.fix_bugs`).
- Không có test nào → chỉ `MEDIUM` (verdict vẫn PASS, nhưng ghi rõ trong report).

## Chạy

```bash
cp .env.example .env        # điền AGENT_API_KEY (và LLM_API_KEY nếu muốn dùng LLM)
python -m uvicorn api:app --host 0.0.0.0 --port 8007
python cli.py run examples/task.json --mock
pytest tests -q
```

## Lưu ý an toàn

pytest chạy code do DEV agent sinh ra trên máy này. Agent đã: chặn Zip-Slip và zip bomb, chạy trong thư mục workspace riêng, không truyền biến môi trường (API key) sang tiến trình test, có timeout. Đó **không** phải sandbox thật; muốn an toàn hơn hãy chạy agent trong Docker/VM. Bỏ `pytest` khỏi `ALLOWED_COMMANDS` để tắt hẳn việc chạy test.
