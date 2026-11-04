"""Tìm input artifact (đã được PM relay vào inbox/) và giải nén SOURCE_CODE vào workspace cô lập."""
import shutil
import zipfile
from pathlib import Path

from agent_contract.enums import ArtifactType
from agent_contract.models import ArtifactRef, TaskRequest

from tools.artifact import ArtifactError, list_artifacts

MAX_UNZIPPED_BYTES = 200 * 1024 * 1024  # chặn zip bomb
SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__", ".pytest_cache", "dist", "build"}


def find_input_files(request: TaskRequest, artifact_dir: str, types: set[ArtifactType]) -> list[Path]:
    """Trả về đường dẫn local của các input_artifacts thuộc `types`.

    PM relay giữ nguyên ArtifactRef gốc (uri của agent nguồn), còn file thật nằm ở
    artifacts/inbox/<created_by>/<name> — ưu tiên đường dẫn đó, sau đó tra index.json theo artifact_id.
    """
    base = Path(artifact_dir).resolve()
    index = {a.artifact_id: a for a in list_artifacts(base)}
    found: list[Path] = []
    for ref in request.input_artifacts:
        if ref.type not in types:
            continue
        candidates = [base / "inbox" / Path(ref.created_by).name / Path(ref.name).name]
        local: ArtifactRef | None = index.get(ref.artifact_id)
        if local and local.uri.startswith("artifacts/"):
            candidates.append(base / local.uri[len("artifacts/"):])
        for cand in candidates:
            cand = cand.resolve()
            if base in cand.parents and cand.is_file():
                found.append(cand)
                break
        else:
            raise ArtifactError(f"Không tìm thấy file của input artifact '{ref.artifact_id}' ({ref.name}) trong inbox/.")
    return found


def safe_extract(zip_path: Path, dest: Path) -> None:
    """Giải nén zip, chặn Zip-Slip (path traversal / đường dẫn tuyệt đối) và zip bomb."""
    dest = dest.resolve()
    with zipfile.ZipFile(zip_path) as zf:
        total = 0
        for info in zf.infolist():
            target = (dest / info.filename).resolve()
            if target != dest and dest not in target.parents:
                raise ArtifactError(f"Zip-Slip: entry '{info.filename}' trỏ ra ngoài workspace.")
            total += info.file_size
            if total > MAX_UNZIPPED_BYTES:
                raise ArtifactError("File zip giải nén vượt quá giới hạn 200MB.")
        zf.extractall(dest)


def prepare_workspace(sources: list[Path], workspace_dir: str, task_id: str) -> Path:
    """Tạo workspace/<task_id>/ sạch và đưa mã nguồn vào (giải nén .zip, copy file thường)."""
    root = (Path(workspace_dir) / Path(task_id).name).resolve()
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    for src in sources:
        if zipfile.is_zipfile(src):
            safe_extract(src, root)
        else:
            shutil.copy2(src, root / src.name)
    return root


def iter_source_files(root: Path, suffixes: set[str] | None = None):
    """Duyệt file mã nguồn, bỏ qua thư mục phụ thuộc/build."""
    for p in sorted(root.rglob("*")):
        if not p.is_file() or any(part in SKIP_DIRS for part in p.relative_to(root).parts):
            continue
        if suffixes is None or p.suffix.lower() in suffixes or p.name.lower() in suffixes:
            yield p
