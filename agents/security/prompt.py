import json
from typing import Any

from agent_contract.models import TaskRequest

SECURITY_SYSTEM_PROMPT = """Bạn là SECURITY AI Agent (ID: security-01) trong hệ thống 'ai-team'.
Chuyên môn: security_analysis, vulnerability_scan, dependency_scan, security_review.

NHIỆM VỤ: Đọc tài liệu kiến trúc và kết quả quét tĩnh, lập threat model ngắn gọn theo OWASP Top 10 / STRIDE:
xác thực & phân quyền, quản lý secret, validate input, mã hoá dữ liệu truyền/lưu, logging, rate limit.
Đưa ra tối đa 10 khuyến nghị cụ thể, ưu tiên rủi ro cao nhất trước.

QUY TẮC:
1. Chỉ làm việc thuộc vai trò SECURITY.
2. Trả về DUY NHẤT một chuỗi JSON hợp lệ theo Schema, không kèm văn bản ngoài JSON.
"""


def build_messages(
    system_prompt: str,
    request: TaskRequest,
    output_schema: dict[str, Any] | None = None,
    inputs_text: str = "",
) -> list[dict[str, str]]:
    """Ghép system prompt, instruction, context và nội dung input thành messages OpenAI-compatible."""
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
        user_parts.append(inputs_text)
    return [{"role": "system", "content": system}, {"role": "user", "content": "\n\n".join(user_parts)}]
