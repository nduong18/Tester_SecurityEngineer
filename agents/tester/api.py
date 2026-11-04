import asyncio
import hashlib
import hmac
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Đảm bảo import đúng trong thư mục agent-template
current_dir = Path(__file__).resolve().parent
if str(current_dir) not in sys.path:
    sys.path.insert(0, str(current_dir))

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, Response, UploadFile, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from agent_contract.enums import ArtifactType, ErrorCode, Priority, TaskStatus
from agent_contract.models import (
    CONTRACT_VERSION,
    AgentIdentity,
    AgentState,
    ArtifactRef,
    TaskRequest,
    TaskResult,
    failed_result,
    make_error,
)
from agent_contract.registry import CAPABILITY_REGISTRY
from agent import TesterAgent
from config import AgentConfig
from llm import LLMClient
from tools.artifact import ArtifactError, list_artifacts

logger = logging.getLogger("agent.api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Khởi tạo cấu hình và Agent
config = AgentConfig()
config.verify_startup(check_port=False)

mock_mode = not bool(config.llm_api_key.strip())
llm_client = LLMClient(config=config, mock=mock_mode)
agent_instance = TesterAgent(config=config, llm=llm_client)

app = FastAPI(
    title=f"{agent_instance.identity.name} API",
    version=CONTRACT_VERSION,
    description="FastAPI service cho AI Agent tuân thủ Agent Contract v1.0 và Agent API v1",
)
app.state.config = config

# Concurrency lock: Chỉ chạy 1 task tại một thời điểm
task_lock = asyncio.Lock()
security_scheme = HTTPBearer(auto_error=False)


# -----------------------------------------------------------------------------
# 1. Middleware: Log truy cập an toàn & Che giấu Secrets
# -----------------------------------------------------------------------------
@app.middleware("http")
async def access_log_middleware(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = int((time.perf_counter() - start) * 1000)

    client_ip = request.client.host if request.client else "unknown"
    task_id = request.headers.get("X-Task-ID") or ""
    path = request.url.path
    if "/tasks/" in path:
        parts = path.strip("/").split("/")
        if len(parts) >= 2 and parts[0] == "tasks":
            task_id = parts[1]

    # Ghi log an toàn không chứa token hay secret
    logger.info(
        f"{request.method} {path} status={response.status_code} "
        f"duration={duration_ms}ms ip={client_ip} task_id={task_id}"
    )
    return response


# -----------------------------------------------------------------------------
# 2. Xử lý ngoại lệ Validation đầu vào
# -----------------------------------------------------------------------------
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    error_msg = f"Dữ liệu đầu vào TaskRequest không hợp lệ: {exc}"
    fake_req = TaskRequest(
        task_id="TASK-INVALID-INPUT",
        project_id="PROJ-UNKNOWN",
        agent_id=config.agent_id,
        role=config.agent_role,
        action="unknown",
        instruction="Invalid input payload",
    )
    result = failed_result(
        fake_req,
        make_error(code=ErrorCode.INVALID_INPUT, message=error_msg, retryable=False),
        summary="Dữ liệu đầu vào TaskRequest không hợp lệ.",
    )
    return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content=result.model_dump(mode="json"))


# -----------------------------------------------------------------------------
# 3. Dependency: Xác thực Bearer Token
# -----------------------------------------------------------------------------
def verify_bearer_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(security_scheme),
) -> str:
    """Xác thực Bearer token an toàn bằng hmac.compare_digest. Bắt buộc cho mọi endpoint trừ /health."""
    expected_token = (config.agent_api_key or "").strip()
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Thiếu header Authorization: Bearer <AGENT_API_KEY>",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = credentials.credentials.strip()
    if not hmac.compare_digest(token, expected_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer token không hợp lệ.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token


# -----------------------------------------------------------------------------
# 4. Các Endpoints chuẩn Agent API v1
# -----------------------------------------------------------------------------
@app.get("/", include_in_schema=False)
def root_redirect() -> RedirectResponse:
    return RedirectResponse(url="/docs")


@app.get("/health", summary="Kiểm tra trạng thái sẵn sàng của dịch vụ (Không xác thực)")
def health() -> dict[str, str]:
    role_val = (
        agent_instance.identity.role.value
        if hasattr(agent_instance.identity.role, "value")
        else str(agent_instance.identity.role)
    )
    return {
        "status": "healthy",
        "agent_id": agent_instance.identity.agent_id,
        "role": role_val,
        "contract_version": CONTRACT_VERSION,
        "agent_version": agent_instance.identity.version,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


@app.get("/agent", response_model=AgentIdentity, summary="Lấy thông tin định danh và năng lực của Agent")
def get_agent(token: str = Depends(verify_bearer_token)) -> AgentIdentity:
    return agent_instance.get_identity()


@app.get("/state", response_model=AgentState, summary="Lấy trạng thái vận hành hiện thời của Agent")
def get_state(token: str = Depends(verify_bearer_token)) -> AgentState:
    return agent_instance.get_state()


@app.post("/tasks", response_model=TaskResult, summary="Gửi TaskRequest để Agent thực thi (Idempotent & Concurrency Lock)")
async def execute_task(request: TaskRequest, token: str = Depends(verify_bearer_token)) -> Response:
    # 1. Kiểm tra Idempotency: nếu task_id đã xử lý thì trả lại kết quả cũ
    results_dir = Path(config.data_dir) / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    result_file = results_dir / f"{request.task_id}.json"

    if result_file.exists():
        try:
            with open(result_file, "r", encoding="utf-8") as f:
                cached_data = json.load(f)
            return JSONResponse(status_code=200, content=cached_data)
        except Exception as e:
            logger.warning(f"Không thể đọc kết quả cache từ {result_file}: {e}")

    # 2. Concurrency Lock: Kiểm tra agent có đang bận xử lý task khác
    if task_lock.locked():
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "detail": "Agent đang bận xử lý tác vụ khác. Chỉ xử lý một tác vụ tại một thời điểm.",
                "status": "RUNNING",
            },
        )

    # 3. Thực thi task an toàn trong lock và áp dụng timeout
    timeout_sec = (
        request.constraints.timeout_seconds
        if request.constraints and request.constraints.timeout_seconds > 0
        else config.llm_timeout_seconds
    )

    async with task_lock:
        # Kiểm tra lại cache lần nữa phòng trường hợp race condition
        if result_file.exists():
            with open(result_file, "r", encoding="utf-8") as f:
                return JSONResponse(status_code=200, content=json.load(f))

        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(agent_instance.handle, request),
                timeout=float(timeout_sec),
            )
        except asyncio.TimeoutError:
            result = TaskResult(
                contract_version=request.contract_version,
                task_id=request.task_id,
                agent_id=request.agent_id,
                status=TaskStatus.FAILED,
                summary=f"Tác vụ thất bại do quá thời gian chờ (TIMEOUT sau {timeout_sec}s).",
                error=make_error(
                    code=ErrorCode.TIMEOUT,
                    message=f"Tác vụ vượt quá thời hạn thực thi ({timeout_sec}s).",
                    retryable=True,
                ),
            )

        # Lưu TaskResult vào data/results/<task_id>.json
        try:
            dump_data = result.model_dump(mode="json")
            with open(result_file, "w", encoding="utf-8") as f:
                json.dump(dump_data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Lỗi lưu kết quả task vào {result_file}: {e}")

        return JSONResponse(status_code=200, content=result.model_dump(mode="json"))


@app.get("/tasks/{task_id}", response_model=TaskResult, summary="Lấy kết quả TaskResult đã lưu")
def get_task_result(task_id: str, token: str = Depends(verify_bearer_token)) -> Response:
    results_dir = Path(config.data_dir) / "results"
    result_file = results_dir / f"{task_id}.json"
    if not result_file.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy kết quả cho task_id: '{task_id}'",
        )
    with open(result_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    return JSONResponse(status_code=200, content=data)


@app.get("/artifacts", response_model=list[ArtifactRef], summary="Lấy danh sách tất cả ArtifactRef của Agent")
def get_artifacts(token: str = Depends(verify_bearer_token)) -> list[ArtifactRef]:
    return list_artifacts(config.artifact_dir)


@app.get("/artifacts/{artifact_id}/content", summary="Tải nội dung file artifact kèm header X-Checksum-SHA256")
def get_artifact_content(artifact_id: str, token: str = Depends(verify_bearer_token)) -> Response:
    artifacts = list_artifacts(config.artifact_dir)
    target_art = next((a for a in artifacts if a.artifact_id == artifact_id), None)
    if not target_art:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy artifact có id '{artifact_id}' trong index.",
        )

    base_dir = Path(config.artifact_dir).resolve()
    clean_uri = target_art.uri.replace("\\", "/").strip().lstrip("/")
    if ".." in clean_uri.split("/"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Phát hiện path traversal trong URI.")

    # Tìm file theo uri
    cand1 = (base_dir.parent / clean_uri).resolve()
    cand2 = (base_dir / Path(clean_uri).name).resolve()
    cand3 = (base_dir / clean_uri).resolve()

    target_file = None
    for cand in [cand1, cand2, cand3]:
        try:
            cand.relative_to(base_dir)
            if cand.exists() and cand.is_file():
                target_file = cand
                break
        except ValueError:
            # Nếu cand1 trỏ vào base_dir
            if base_dir in cand.parents and cand.exists() and cand.is_file():
                target_file = cand
                break

    if not target_file or not target_file.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File cho artifact '{artifact_id}' không tồn tại trên đĩa.",
        )

    with open(target_file, "rb") as f:
        content_bytes = f.read()

    checksum = target_art.checksum or hashlib.sha256(content_bytes).hexdigest()
    return Response(
        content=content_bytes,
        media_type="application/octet-stream",
        headers={
            "X-Checksum-SHA256": checksum,
            "Content-Disposition": f'attachment; filename="{target_file.name}"',
        },
    )


@app.post("/artifacts/inbox", response_model=ArtifactRef, summary="Nhận file artifact từ agent khác vào inbox/")
async def receive_artifact_inbox(
    metadata: str = Form(..., description="JSON chuỗi ArtifactRef"),
    file: UploadFile = File(..., description="File binary đính kèm"),
    token: str = Depends(verify_bearer_token),
) -> ArtifactRef:
    try:
        meta_dict = json.loads(metadata)
        art_ref = ArtifactRef.model_validate(meta_dict)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Metadata không đúng định dạng ArtifactRef JSON: {e}",
        )

    # Đọc nội dung file
    content_bytes = await file.read()
    max_bytes = config.max_upload_mb * 1024 * 1024
    if len(content_bytes) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Dung lượng file ({len(content_bytes)} bytes) vượt quá giới hạn cho phép ({config.max_upload_mb} MB).",
        )

    # Kiểm tra sha256 checksum
    actual_sha = hashlib.sha256(content_bytes).hexdigest()
    if art_ref.checksum and actual_sha.lower() != art_ref.checksum.lower():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"ARTIFACT_ERROR: Checksum sha256 không khớp! Nhận: {actual_sha}, Mong đợi: {art_ref.checksum}",
        )

    # Làm sạch tên file (chặn path traversal)
    raw_name = file.filename or art_ref.name or "artifact.bin"
    clean_filename = Path(raw_name).name.replace("..", "").strip()
    if not clean_filename:
        clean_filename = "artifact.bin"

    origin_agent_id = (art_ref.created_by or "unknown").strip()
    inbox_dir = (Path(config.artifact_dir) / "inbox" / origin_agent_id).resolve()
    inbox_dir.mkdir(parents=True, exist_ok=True)

    dest_file = inbox_dir / clean_filename
    with open(dest_file, "wb") as f:
        f.write(content_bytes)

    # Cập nhật uri và checksum cho ArtifactRef
    rel_uri = f"artifacts/inbox/{origin_agent_id}/{clean_filename}"
    saved_ref = ArtifactRef(
        artifact_id=art_ref.artifact_id,
        name=clean_filename,
        type=art_ref.type,
        version=art_ref.version,
        uri=rel_uri,
        created_by=art_ref.created_by,
        created_at=art_ref.created_at,
        checksum=actual_sha,
    )

    # Ghi nhận vào index.json của agent
    existing = list_artifacts(config.artifact_dir)
    # Cập nhật nếu trùng artifact_id, hoặc thêm mới
    existing = [a for a in existing if a.artifact_id != saved_ref.artifact_id]
    existing.append(saved_ref)

    index_file = Path(config.artifact_dir) / "index.json"
    with open(index_file, "w", encoding="utf-8") as f:
        json_data = []
        for a in existing:
            dump = a.model_dump(mode="json")
            if isinstance(dump.get("created_at"), str) and dump["created_at"].endswith("+00:00"):
                dump["created_at"] = dump["created_at"][:-6] + "Z"
            json_data.append(dump)
        json.dump(json_data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    return saved_ref
