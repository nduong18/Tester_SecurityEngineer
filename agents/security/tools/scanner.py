"""Bộ quét bảo mật tĩnh: rule regex tích hợp (luôn chạy) + bandit / pip-audit nếu đã cài và được cho phép."""
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from schemas import Finding
from tools.workspace import iter_source_files

SCAN_SUFFIXES = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".php", ".go", ".rb", ".cs",
    ".yml", ".yaml", ".json", ".ini", ".cfg", ".toml", ".sh", ".env", ".properties", ".sql",
}
MAX_FILE_BYTES = 1024 * 1024
PLACEHOLDER = re.compile(r"(your|example|changeme|xxx|<|\$\{|os\.environ|getenv|dummy|test)", re.I)

# (id, severity, title, regex, recommendation)
RULES: list[tuple[str, str, str, re.Pattern, str]] = [
    ("SEC-PRIVKEY", "CRITICAL", "Private key nằm trong mã nguồn",
     re.compile(r"-----BEGIN (RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"), "Xoá key khỏi repo, thu hồi và tạo key mới."),
    ("SEC-AWSKEY", "CRITICAL", "AWS Access Key bị hardcode",
     re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "Thu hồi key, dùng biến môi trường / secret manager."),
    ("SEC-SECRET", "HIGH", "Secret / mật khẩu bị hardcode",
     re.compile(r"""(?i)\b(password|passwd|pwd|secret|secret_key|api_key|apikey|access_token|auth_token|token)\b\s*[:=]\s*["']([^"'\s]{6,})["']"""),
     "Đọc secret từ biến môi trường hoặc secret manager."),
    ("SEC-EVAL", "HIGH", "Dùng eval/exec với dữ liệu động",
     re.compile(r"(?<![\w.])(eval|exec)\s*\("), "Không dùng eval/exec; parse dữ liệu bằng json/ast.literal_eval."),
    ("SEC-SHELL", "HIGH", "subprocess với shell=True (command injection)",
     re.compile(r"subprocess\.\w+\(.*shell\s*=\s*True"), "Truyền list tham số, bỏ shell=True."),
    ("SEC-OSSYSTEM", "MEDIUM", "os.system / os.popen (command injection)",
     re.compile(r"\bos\.(system|popen)\s*\("), "Dùng subprocess.run với list tham số."),
    ("SEC-PICKLE", "HIGH", "Deserialize không an toàn (pickle/marshal)",
     re.compile(r"\b(pickle|cPickle|marshal|dill)\.loads?\s*\("), "Dùng JSON; không unpickle dữ liệu không tin cậy."),
    ("SEC-YAML", "MEDIUM", "yaml.load không dùng SafeLoader",
     re.compile(r"\byaml\.load\((?![^)]*SafeLoader)"), "Dùng yaml.safe_load."),
    ("SEC-SQLI", "HIGH", "SQL ghép chuỗi (SQL injection)",
     re.compile(r"""(?i)\.(execute|executemany|raw)\s*\(\s*(f["']|["'][^"']*["']\s*(%|\+|\.format))"""),
     "Dùng parameterized query (placeholder ? / %s kèm tham số)."),
    ("SEC-SQLI-STR", "HIGH", "Câu SQL tạo bằng f-string",
     re.compile(r"""(?i)f["']\s*(SELECT|INSERT|UPDATE|DELETE)\b[^"']*\{"""), "Dùng parameterized query."),
    ("SEC-TLS", "MEDIUM", "Tắt kiểm tra chứng chỉ TLS",
     re.compile(r"verify\s*=\s*False"), "Bật verify=True."),
    ("SEC-DEBUG", "MEDIUM", "Bật chế độ debug",
     re.compile(r"(?i)\b(debug\s*=\s*True|DEBUG\s*=\s*True)"), "Tắt debug khi deploy."),
    ("SEC-CORS", "MEDIUM", "CORS cho phép mọi origin",
     re.compile(r"""allow_origins\s*=\s*\[\s*["']\*["']|Access-Control-Allow-Origin["']?\s*[:,]\s*["']\*"""),
     "Giới hạn danh sách origin cụ thể."),
    ("SEC-WEAKHASH", "LOW", "Hàm băm yếu (MD5/SHA1)",
     re.compile(r"\bhashlib\.(md5|sha1)\s*\(|createHash\(\s*['\"](md5|sha1)"), "Dùng SHA-256; mật khẩu dùng bcrypt/argon2."),
    ("SEC-BIND", "LOW", "Lắng nghe trên mọi interface",
     re.compile(r"""host\s*=\s*["']0\.0\.0\.0["']"""), "Đảm bảo có firewall / auth khi bind 0.0.0.0."),
    ("SEC-XSS", "MEDIUM", "Gán innerHTML / dangerouslySetInnerHTML (XSS)",
     re.compile(r"\.innerHTML\s*=|dangerouslySetInnerHTML"), "Escape dữ liệu, dùng textContent."),
    ("SEC-JWT-NONE", "HIGH", "JWT chấp nhận thuật toán 'none' / tắt verify",
     re.compile(r"""(?i)algorithms\s*=\s*\[\s*["']none["']|verify_signature["']?\s*:\s*False"""), "Bắt buộc ký và verify JWT."),
]


def scan_rules(root: Path) -> tuple[list[Finding], int]:
    """Quét regex trên toàn bộ file văn bản. Trả về (findings, số file đã quét)."""
    findings: list[Finding] = []
    count = 0
    for path in iter_source_files(root):
        rel = path.relative_to(root).as_posix()
        name = path.name.lower()
        if name == ".env" or (name.startswith(".env.") and not name.endswith(("example", "sample", "template"))):
            findings.append(Finding(id="SEC-ENVFILE", severity="HIGH", title="File .env bị đóng gói vào mã nguồn",
                                    file=rel, recommendation="Loại .env khỏi artifact, chỉ giữ .env.example.", source="rules"))
        if name.endswith((".pem", ".key", ".p12", ".pfx", "id_rsa")):
            findings.append(Finding(id="SEC-KEYFILE", severity="CRITICAL", title="File khoá bí mật bị đóng gói",
                                    file=rel, recommendation="Xoá file khoá khỏi artifact và thu hồi khoá.", source="rules"))
        if path.suffix.lower() not in SCAN_SUFFIXES and name not in SCAN_SUFFIXES:
            continue
        if path.stat().st_size > MAX_FILE_BYTES:
            continue
        count += 1
        is_test = "test" in rel.lower()
        for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith(("#", "//", "*", "--")):
                continue
            for rule_id, sev, title, rx, rec in RULES:
                m = rx.search(line)
                if not m:
                    continue
                if rule_id == "SEC-SECRET" and (PLACEHOLDER.search(m.group(2)) or is_test):
                    continue
                findings.append(Finding(id=rule_id, severity=sev, title=title, file=rel, line=lineno,
                                        description=stripped[:200], recommendation=rec, source="rules"))
    return findings, count


def _clean_env() -> dict[str, str]:
    keep = {"PATH", "SYSTEMROOT", "TEMP", "TMP", "HOME", "USERPROFILE", "WINDIR", "APPDATA", "LOCALAPPDATA"}
    return {k: v for k, v in os.environ.items() if k.upper() in keep}


def has_tool(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def run_bandit(root: Path, timeout: int) -> list[Finding]:
    """Chạy bandit (nếu đã cài) và map issue_severity sang Finding."""
    proc = subprocess.run(
        [sys.executable, "-m", "bandit", "-r", str(root), "-f", "json", "-q", "-x", ",".join(f"*/{d}/*" for d in ("tests", "test", "venv", ".venv", "node_modules"))],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, env=_clean_env(),
    )
    data = json.loads(proc.stdout or "{}")
    out = []
    for r in data.get("results", []):
        fname = Path(r.get("filename", ""))
        try:
            rel = fname.resolve().relative_to(root).as_posix()
        except ValueError:
            rel = fname.name
        sev = str(r.get("issue_severity", "LOW")).upper()
        out.append(Finding(
            id=f"BANDIT-{r.get('test_id', '')}", severity=sev if sev in ("HIGH", "MEDIUM", "LOW") else "LOW",
            title=r.get("issue_text", "")[:150], file=rel, line=int(r.get("line_number") or 0),
            description=(r.get("code") or "").strip()[:300], recommendation=r.get("more_info", ""), source="bandit",
        ))
    return out


def run_pip_audit(root: Path, timeout: int) -> list[Finding]:
    """Chạy pip-audit trên các requirements*.txt (cần mạng). Mỗi CVE là finding HIGH."""
    out = []
    for req in iter_source_files(root):
        if not (req.name.startswith("requirements") and req.suffix == ".txt"):
            continue
        proc = subprocess.run(
            [sys.executable, "-m", "pip_audit", "-r", str(req), "-f", "json", "--progress-spinner", "off"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, env=_clean_env(),
        )
        try:
            data = json.loads(proc.stdout or "{}")
        except json.JSONDecodeError:
            continue
        for dep in data.get("dependencies", []):
            for v in dep.get("vulns", []):
                fix = ", ".join(v.get("fix_versions", [])) or "chưa có bản vá"
                out.append(Finding(
                    id=v.get("id", "CVE"), severity="HIGH", title=f"Thư viện có lỗ hổng: {dep.get('name')}=={dep.get('version')}",
                    file=req.relative_to(root).as_posix(), description=(v.get("description") or "")[:300],
                    recommendation=f"Nâng cấp lên: {fix}", source="pip-audit",
                ))
    return out
