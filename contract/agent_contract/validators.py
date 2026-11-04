import re
from pathlib import Path
from typing import Any

from agent_contract.enums import AgentRole, TaskStatus
from agent_contract.registry import CAPABILITY_REGISTRY

AGENT_ID_REGEX = r"^[a-z_]+-\d{2}$"
_AGENT_ID_PATTERN = re.compile(AGENT_ID_REGEX)


def validate_agent_id(agent_id: str) -> str:
    """Xác thực agent_id theo định dạng ^[a-z_]+-\\d{2}$ (ví dụ: pm-01, devops-02)."""
    if not isinstance(agent_id, str) or not _AGENT_ID_PATTERN.match(agent_id):
        raise ValueError(
            f"agent_id '{agent_id}' không hợp lệ. Phải khớp định dạng '{AGENT_ID_REGEX}' (ví dụ: pm-01, devops-01)."
        )
    return agent_id


def validate_artifact_uri(uri: str) -> str:
    """Xác thực uri của artifact là đường dẫn tương đối và không chứa '..'."""
    if not isinstance(uri, str) or not uri.strip():
        raise ValueError("uri của artifact không được để trống.")

    normalized_uri = uri.replace("\\", "/")

    # Chặn đường dẫn tuyệt đối (Unix '/' hoặc Windows 'C:')
    if normalized_uri.startswith("/") or re.match(r"^[a-zA-Z]:", uri) or Path(uri).is_absolute():
        raise ValueError(f"uri '{uri}' phải là đường dẫn tương đối, không được là đường dẫn tuyệt đối.")

    # Chặn đường dẫn chứa '..' (path traversal)
    parts = normalized_uri.split("/")
    if ".." in parts:
        raise ValueError(f"uri '{uri}' không được chứa '..' để ngăn ngừa path traversal.")

    return uri


def validate_capabilities(role: AgentRole, capabilities: list[str]) -> list[str]:
    """Xác thực danh sách capabilities phải là tập con của CAPABILITY_REGISTRY[role]."""
    allowed_caps = set(CAPABILITY_REGISTRY.get(role, []))
    invalid_caps = [cap for cap in capabilities if cap not in allowed_caps]
    if invalid_caps:
        raise ValueError(
            f"Role '{role.value}' chứa capability không hợp lệ: {invalid_caps}. "
            f"Chỉ được phép trong: {sorted(allowed_caps)}"
        )
    return capabilities


def validate_task_result_status_and_error(status: TaskStatus, error: Any | None) -> None:
    """Xác thực ràng buộc giữa status và error trong TaskResult:

    - status == COMPLETED -> error phải là None.
    - status in (FAILED, BLOCKED) -> error bắt buộc phải có.
    - status == CANCELLED -> error là tùy chọn (None hoặc AgentError).
    """
    if status == TaskStatus.COMPLETED and error is not None:
        raise ValueError("Khi status là COMPLETED, trường 'error' bắt buộc phải là None.")

    if status in (TaskStatus.FAILED, TaskStatus.BLOCKED) and error is None:
        raise ValueError(f"Khi status là {status.value}, trường 'error' bắt buộc phải có thông tin AgentError.")
