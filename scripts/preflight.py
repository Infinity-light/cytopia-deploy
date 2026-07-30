from __future__ import annotations

import argparse
import json
import re
import stat
from dataclasses import dataclass
from pathlib import Path

STATIC_SUFFIXES = {
    ".avif", ".css", ".csv", ".eot", ".gif", ".htm", ".html", ".ico",
    ".jpeg", ".jpg", ".js", ".json", ".map", ".mjs", ".mp3", ".mp4",
    ".ogg", ".otf", ".pdf", ".png", ".svg", ".txt", ".wasm", ".webm",
    ".webmanifest", ".webp", ".woff", ".woff2", ".xml",
}
SOURCE_SUFFIXES = STATIC_SUFFIXES | {
    ".cfg", ".cjs", ".cts", ".ini", ".jsx", ".lock", ".mts", ".prisma",
    ".py", ".sql", ".toml", ".ts", ".tsx", ".vue", ".yaml", ".yml",
}
FORBIDDEN_NAMES = {
    ".env", ".env.local", ".env.production", "id_rsa", "id_ed25519",
    "credentials.json", "service-account.json", "dockerfile",
    "docker-compose.yml", "docker-compose.yaml",
}
INFRASTRUCTURE_FILES = {"dockerfile", "docker-compose.yml", "docker-compose.yaml"}
IGNORED_DIRS = {
    ".git", ".idea", ".vscode", "__pycache__", "node_modules", ".next",
    ".pytest_cache", ".ruff_cache", ".venv", "venv",
}
SECRET_PATTERNS = (
    ("private key", re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("AWS access key", re.compile(rb"\bAKIA[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(rb"\bghp_[A-Za-z0-9]{30,}\b")),
    ("model API key", re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("Aliyun access key", re.compile(rb"\bLTAI[A-Za-z0-9]{12,}\b")),
    (
        "assigned credential",
        re.compile(
            rb"""(?ix)
            \b(?:api[_-]?key|access[_-]?token|client[_-]?secret|secret[_-]?key)
            \b\s*[:=]\s*["']?[A-Za-z0-9_./+=-]{16,}
            """
        ),
    ),
    ("bearer token", re.compile(rb"(?i)\bBearer\s+[A-Za-z0-9_./+=-]{20,}")),
)


@dataclass(frozen=True)
class DeployFile:
    path: Path
    relative: Path
    size: int


@dataclass(frozen=True)
class DeployPlan:
    kind: str
    preset: str
    database: str
    entrypoint: str
    healthcheck: str
    build_command: str | None = None
    output_dir: Path | None = None
    classification_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class SemanticFinding:
    code: str
    path: str
    message: str


StaticFile = DeployFile


def _is_symlink(path: Path) -> bool:
    try:
        return stat.S_ISLNK(path.lstat().st_mode)
    except OSError:
        return True


def scan_for_local_secret_files(project_dir: Path) -> list[str]:
    findings = []
    for path in project_dir.rglob("*"):
        relative = path.relative_to(project_dir)
        if any(part in IGNORED_DIRS for part in relative.parts):
            continue
        lowered = path.name.lower()
        if path.is_file() and (
            (lowered in FORBIDDEN_NAMES and lowered not in INFRASTRUCTURE_FILES)
            or lowered.startswith(".env")
        ):
            findings.append(str(relative))
    return findings


def _scan_content(item: DeployFile) -> None:
    if item.path.suffix.lower() not in SOURCE_SUFFIXES or item.size > 2 * 1024 * 1024:
        return
    content = item.path.read_bytes()
    for label, pattern in SECRET_PATTERNS:
        if pattern.search(content):
            raise ValueError(f"检测到疑似 {label}：{item.relative}")


def collect_files(
    root: Path,
    *,
    kind: str,
    max_files: int = 2000,
    max_bytes: int = 80 * 1024 * 1024,
) -> list[DeployFile]:
    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f"部署目录不存在：{root}")
    allowed = STATIC_SUFFIXES if kind == "static" else SOURCE_SUFFIXES
    files = []
    total = 0
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in IGNORED_DIRS for part in relative.parts):
            continue
        if _is_symlink(path):
            raise ValueError(f"部署包不能包含符号链接：{relative}")
        if not path.is_file():
            continue
        if any(part.startswith(".") for part in relative.parts):
            continue
        if path.name.lower() in FORBIDDEN_NAMES:
            continue
        if path.suffix.lower() not in allowed:
            if kind == "fullstack":
                continue
            raise ValueError(f"不支持的静态文件类型：{relative}")
        item = DeployFile(path, relative, path.stat().st_size)
        total += item.size
        files.append(item)
        if len(files) > max_files:
            raise ValueError(f"部署文件超过 {max_files} 个")
        if total > max_bytes:
            raise ValueError(f"部署内容超过 {max_bytes // 1024 // 1024} MB")
        _scan_content(item)
    return files


def collect_static_files(
    output_dir: Path,
    *,
    max_files: int = 2000,
    max_bytes: int = 80 * 1024 * 1024,
) -> list[DeployFile]:
    if not (output_dir / "index.html").is_file():
        raise ValueError(f"静态产物根目录缺少 index.html：{output_dir}")
    return collect_files(output_dir, kind="static", max_files=max_files, max_bytes=max_bytes)


def package_manager(project_dir: Path) -> str:
    if (project_dir / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (project_dir / "yarn.lock").exists():
        return "yarn"
    return "npm"


def detect_database(text: str) -> str:
    lowered = text.lower()
    if any(name in lowered for name in ("mysql", "pymysql", "mysqlclient")):
        return "mysql"
    if any(name in lowered for name in ("postgres", "psycopg", "asyncpg", "pg ")):
        return "postgresql"
    if any(name in lowered for name in ("sqlite", "better-sqlite3")):
        return "sqlite"
    return "none"


def normalize_healthcheck(value: str) -> str:
    normalized = "/" + value.strip().lstrip("/")
    if "\\" in normalized or ".." in normalized or "?" in normalized or "#" in normalized:
        raise ValueError(f"无效的健康检查路径：{value}")
    return normalized


def detect_healthcheck(project_dir: Path) -> str:
    for path in project_dir.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".py", ".js", ".jsx", ".ts", ".tsx"}:
            text = path.read_text(encoding="utf-8", errors="ignore")
            if re.search(r"""["']/health(?:z)?["']""", text):
                return normalize_healthcheck("/healthz" if "/healthz" in text else "/health")
    return "/"


def _next_static_export(project_dir: Path) -> bool:
    for name in (
        "next.config.js",
        "next.config.mjs",
        "next.config.cjs",
        "next.config.ts",
    ):
        path = project_dir / name
        if path.is_file() and re.search(
            r"""\boutput\s*:\s*["']export["']""",
            path.read_text(encoding="utf-8", errors="ignore"),
        ):
            return True
    return False


def _iter_text_files(root: Path):
    if not root.is_dir():
        return
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in IGNORED_DIRS for part in relative.parts):
            continue
        if (
            path.is_file()
            and path.suffix.lower() in SOURCE_SUFFIXES
            and path.stat().st_size <= 2 * 1024 * 1024
        ):
            yield path, relative


def scan_semantic_risks(
    project_dir: Path,
    plan: DeployPlan,
    *,
    output_dir: Path | None = None,
) -> list[SemanticFinding]:
    """Find deploy-time mistakes that syntax and secret scans cannot detect."""
    findings: list[SemanticFinding] = []
    seen: set[tuple[str, str]] = set()

    def add(code: str, path: Path | str, message: str) -> None:
        key = (code, str(path))
        if key not in seen:
            findings.append(SemanticFinding(code, str(path), message))
            seen.add(key)

    public_secret = re.compile(
        r"\b(?:NEXT_PUBLIC|VITE|PUBLIC)_[A-Z0-9_]*(?:PASSWORD|PASSWD|SECRET|TOKEN|API_KEY|PRIVATE_KEY)\b",
        re.IGNORECASE,
    )
    client_password_gate = re.compile(
        r"""(?is)
        (?:password|passwd|密码).{0,160}(?:===|==).{0,80}["'][^"'{}\r\n]{4,64}["']
        |
        ["'][^"'{}\r\n]{4,64}["'].{0,80}(?:===|==).{0,160}(?:password|passwd|密码)
        """,
        re.VERBOSE,
    )
    for path, relative in _iter_text_files(project_dir):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if public_secret.search(text):
            add(
                "public_secret",
                relative,
                "公开客户端环境变量中包含密码、令牌或密钥；它会被打进浏览器代码。",
            )
        if (
            path.suffix.lower() in {".js", ".jsx", ".ts", ".tsx", ".vue"}
            and client_password_gate.search(text)
        ):
            add(
                "client_password_gate",
                relative,
                "检测到浏览器端硬编码密码比较；请改用服务端鉴权。",
            )

    if plan.kind != "static":
        return findings

    for api_dir in ("api", "app/api", "pages/api", "src/app/api", "src/pages/api"):
        if (project_dir / api_dir).is_dir():
            add(
                "static_api_mismatch",
                api_dir,
                "静态部署不能运行 API 路由；项目应按全栈方式部署。",
            )

    package_json = project_dir / "package.json"
    if package_json.is_file():
        package = json.loads(package_json.read_text(encoding="utf-8"))
        dependencies = {
            **package.get("dependencies", {}),
            **package.get("devDependencies", {}),
        }
        server_dependencies = {
            "express", "fastify", "koa", "@nestjs/core", "hono",
            "prisma", "@prisma/client", "sequelize", "typeorm",
            "mysql", "mysql2", "pg", "better-sqlite3",
        }
        detected = sorted(server_dependencies.intersection(dependencies))
        if detected:
            add(
                "static_runtime_mismatch",
                "package.json",
                "静态部署检测到服务端或数据库依赖：" + ", ".join(detected),
            )

    built_root = (output_dir or plan.output_dir or project_dir).resolve()
    unresolved_env = re.compile(
        r"\b(?:process\.env|import\.meta\.env)\.[A-Za-z_][A-Za-z0-9_]*"
    )
    loopback = re.compile(
        r"(?i)(?:https?://)?(?:localhost|127\.0\.0\.1)(?::\d+)?"
    )
    for path, relative in _iter_text_files(built_root):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if unresolved_env.search(text):
            add(
                "unresolved_client_env",
                relative,
                "静态产物仍包含未解析的运行时环境变量。",
            )
        if loopback.search(text):
            add(
                "loopback_url",
                relative,
                "静态产物引用 localhost/127.0.0.1，线上浏览器无法访问本机服务。",
            )
    return findings


def require_semantic_preflight(
    project_dir: Path,
    plan: DeployPlan,
    *,
    output_dir: Path | None = None,
) -> None:
    findings = scan_semantic_risks(project_dir, plan, output_dir=output_dir)
    if findings:
        detail = "; ".join(
            f"{finding.code} ({finding.path}): {finding.message}"
            for finding in findings
        )
        raise ValueError(f"语义预检失败：{detail}")


def _python_entrypoint(project_dir: Path, framework: str) -> str:
    marker = "FastAPI(" if framework == "fastapi" else "Flask("
    for path in sorted(project_dir.glob("*.py")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if marker in text:
            variable = re.search(
                r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*" + re.escape(marker),
                text,
                re.MULTILINE,
            )
            return f"{path.stem}:{variable.group(1) if variable else 'app'}"
    return "app:app" if (project_dir / "app.py").exists() else ""


def detect_plan(project_dir: Path) -> DeployPlan:
    requirements = project_dir / "requirements.txt"
    if requirements.is_file():
        packages = requirements.read_text(encoding="utf-8", errors="ignore").lower()
        if "fastapi" in packages:
            return DeployPlan(
                "fullstack",
                "fastapi",
                detect_database(packages),
                _python_entrypoint(project_dir, "fastapi"),
                detect_healthcheck(project_dir),
                classification_reasons=("requirements.txt contains FastAPI",),
            )
        if "flask" in packages:
            return DeployPlan(
                "fullstack",
                "flask",
                detect_database(packages),
                _python_entrypoint(project_dir, "flask"),
                detect_healthcheck(project_dir),
                classification_reasons=("requirements.txt contains Flask",),
            )
    package_json = project_dir / "package.json"
    if package_json.is_file():
        package = json.loads(package_json.read_text(encoding="utf-8"))
        dependencies = {**package.get("dependencies", {}), **package.get("devDependencies", {})}
        dynamic = {"express", "fastify", "koa", "next", "@nestjs/core", "hono"}
        dynamic_found = sorted(dynamic.intersection(dependencies))
        if "next" in dependencies and _next_static_export(project_dir):
            return DeployPlan(
                "static",
                "static",
                "none",
                "",
                "/",
                f"{package_manager(project_dir)} run build",
                project_dir / "out",
                ("next.config explicitly sets output: export",),
            )
        if dynamic_found and not package.get("scripts", {}).get("start"):
            raise ValueError(
                "检测到动态服务依赖但 package.json 缺少 start 脚本；"
                "拒绝静默降级为静态部署：" + ", ".join(dynamic_found)
            )
        if dynamic_found:
            if "next" in dependencies and not any(
                (project_dir / path).is_dir()
                for path in ("app", "pages", "src/app", "src/pages")
            ):
                raise ValueError("Next.js 项目缺少 app or pages 页面目录")
            return DeployPlan(
                "fullstack",
                "node",
                detect_database(" ".join(dependencies)),
                "package.json",
                detect_healthcheck(project_dir),
                classification_reasons=(
                    "package.json contains server runtime: " + ", ".join(dynamic_found),
                    "package.json contains a start script",
                ),
            )
        if "build" not in package.get("scripts", {}):
            raise ValueError("package.json 没有 build 或可运行的 start 脚本")
        return DeployPlan(
            "static",
            "static",
            "none",
            "",
            "/",
            f"{package_manager(project_dir)} run build",
            project_dir / "dist",
            ("package.json has a build script and no server runtime",),
        )
    if (project_dir / "index.html").is_file():
        return DeployPlan(
            "static",
            "static",
            "none",
            "",
            "/",
            None,
            project_dir,
            ("project root contains index.html and no server manifest",),
        )
    raise ValueError("无法识别项目；请用 --preset 指定 static、fastapi、flask 或 node")


def main() -> int:
    parser = argparse.ArgumentParser(description="检查训练营静态或全栈部署包")
    parser.add_argument("project_dir", type=Path)
    args = parser.parse_args()
    project_dir = args.project_dir.resolve()
    secrets_found = scan_for_local_secret_files(project_dir)
    if secrets_found:
        raise SystemExit("项目包含禁止上传的密钥文件：" + ", ".join(secrets_found))
    plan = detect_plan(project_dir)
    root = plan.output_dir or project_dir
    require_semantic_preflight(project_dir, plan, output_dir=root)
    files = collect_static_files(root) if plan.kind == "static" else collect_files(root, kind="fullstack")
    print(
        f"PASS kind={plan.kind} preset={plan.preset} database={plan.database} "
        f"files={len(files)} bytes={sum(item.size for item in files)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
