# agent-contract (v1.0.0)

Gói Contract chuẩn hóa giao thức dữ liệu (Pydantic v2) dùng chung cho 10 AI Agent:
PM, SA, BA, PO, DEV, TESTER, DEVOPS, DBA, SCRUM_MASTER, SECURITY.

## Cài đặt chế độ editable
```bash
pip install -e ./contract
```

## Sử dụng
```python
from agent_contract import (
    CONTRACT_VERSION,
    AgentRole,
    TaskRequest,
    TaskResult,
    TaskStatus,
    make_error,
    failed_result,
    blocked_result,
)
```
