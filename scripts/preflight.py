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


def detect_healthcheck(project_dir: Path) -> str:
    for path in project_dir.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".py", ".js", ".jsx", ".ts", ".tsx"}:
            text = path.read_text(encoding="utf-8", errors="ignore")
            if re.search(r"""["']/health(?:z)?["']""", text):
                return "/healthz" if "/healthz" in text else "/health"
    return "/"


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
            return DeployPlan("fullstack", "fastapi", detect_database(packages), _python_entrypoint(project_dir, "fastapi"), detect_healthcheck(project_dir))
        if "flask" in packages:
            return DeployPlan("fullstack", "flask", detect_database(packages), _python_entrypoint(project_dir, "flask"), detect_healthcheck(project_dir))
    package_json = project_dir / "package.json"
    if package_json.is_file():
        package = json.loads(package_json.read_text(encoding="utf-8"))
        dependencies = {**package.get("dependencies", {}), **package.get("devDependencies", {})}
        dynamic = {"express", "fastify", "koa", "next", "@nestjs/core", "hono"}
        if dynamic.intersection(dependencies) and package.get("scripts", {}).get("start"):
            return DeployPlan("fullstack", "node", detect_database(" ".join(dependencies)), "package.json", detect_healthcheck(project_dir))
        if "build" not in package.get("scripts", {}):
            raise ValueError("package.json 没有 build 或可运行的 start 脚本")
        return DeployPlan("static", "static", "none", "", "/", f"{package_manager(project_dir)} run build", project_dir / "dist")
    if (project_dir / "index.html").is_file():
        return DeployPlan("static", "static", "none", "", "/", None, project_dir)
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
    files = collect_static_files(root) if plan.kind == "static" else collect_files(root, kind="fullstack")
    print(
        f"PASS kind={plan.kind} preset={plan.preset} database={plan.database} "
        f"files={len(files)} bytes={sum(item.size for item in files)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
