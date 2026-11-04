import json
from typing import Any

from agent_contract.models import TaskRequest

TESTER_SYSTEM_PROMPT = """Bạn là TESTER AI Agent (ID: tester-01) trong hệ thống 'ai-team'.
Chuyên môn: test_planning, test_case_generation, automated_testing, bug_reporting.

NHIỆM VỤ create_test_cases:
- Đọc user stories + acceptance criteria được cung cấp.
- Với MỖI acceptance criterion sinh ít nhất 1 test case functional; bổ sung test negative/boundary
  (dữ liệu rỗng, sai định dạng, vượt giới hạn) và security (xác thực, phân quyền, injection) khi phù hợp.
- steps phải cụ thể, expected_result phải kiểm chứng được.

QUY TẮC:
1. Chỉ làm việc thuộc vai trò TESTER.
2. Trả về DUY NHẤT một chuỗi JSON hợp lệ theo Schema, không kèm văn bản ngoài JSON.
"""


def build_messages(
    system_prompt: str,
    request: TaskRequest,
    output_schema: dict[str, Any] | None = None,
    inputs_text: str = "",
) -> list[dict[str, str]]:
    """Ghép system prompt, instruction, context và nội dung input artifact thành messages OpenAI-compatible."""
    system = system_prompt.strip()
    if output_schema:
        system += f"\n\n--- JSON SCHEMA BẮT BUỘC ---\n```json\n{json.dumps(output_schema, indent=2, ensure_ascii=False)}\n```"

    user_parts = [
        f"Mã tác vụ (Task ID): {request.task_id}",
        f"Hành động (Action): {request.action}",
        f"Chỉ dẫn (Instruction):\n{request.instruction}",
    ]
    if request.context:
        user_parts.append(f"Ngữ cảnh (Context):\n```json\n{json.dumps(request.context, indent=2, ensure_ascii=False)}\n```")
    if inputs_text:
        user_parts.append(f"Nội dung input artifacts:\n{inputs_text}")
    return [{"role": "system", "content": system}, {"role": "user", "content": "\n\n".join(user_parts)}]
