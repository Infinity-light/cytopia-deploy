from __future__ import annotations

import argparse
import re
import stat
from dataclasses import dataclass
from pathlib import Path

ALLOWED_SUFFIXES = {
    ".avif", ".css", ".csv", ".eot", ".gif", ".htm", ".html", ".ico",
    ".jpeg", ".jpg", ".js", ".json", ".map", ".mjs", ".mp3", ".mp4",
    ".ogg", ".otf", ".pdf", ".png", ".svg", ".txt", ".wasm", ".webm",
    ".webmanifest", ".webp", ".woff", ".woff2", ".xml",
}
TEXT_SUFFIXES = {
    ".css", ".csv", ".htm", ".html", ".js", ".json", ".map", ".mjs",
    ".svg", ".txt", ".webmanifest", ".xml",
}
FORBIDDEN_NAMES = {
    ".env", ".env.local", ".env.production", "id_rsa", "id_ed25519",
    "credentials.json", "service-account.json",
}
IGNORED_DIRS = {
    ".git", ".idea", ".vscode", "__pycache__", "node_modules",
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
class StaticFile:
    path: Path
    relative: Path
    size: int


def _is_symlink(path: Path) -> bool:
    try:
        return stat.S_ISLNK(path.lstat().st_mode)
    except OSError:
        return True


def scan_for_local_secret_files(project_dir: Path) -> list[str]:
    findings: list[str] = []
    for path in project_dir.rglob("*"):
        if any(part in IGNORED_DIRS for part in path.parts):
            continue
        if path.is_file() and (
            path.name.lower() in FORBIDDEN_NAMES
            or path.name.lower().startswith(".env")
        ):
            findings.append(str(path.relative_to(project_dir)))
    return findings


def collect_static_files(
    output_dir: Path,
    *,
    max_files: int = 2000,
    max_bytes: int = 80 * 1024 * 1024,
) -> list[StaticFile]:
    output_dir = output_dir.resolve()
    if not output_dir.is_dir():
        raise ValueError(f"静态产物目录不存在：{output_dir}")
    index = output_dir / "index.html"
    if not index.is_file():
        raise ValueError(f"静态产物根目录缺少 index.html：{output_dir}")
    files: list[StaticFile] = []
    total = 0
    for path in sorted(output_dir.rglob("*")):
        relative = path.relative_to(output_dir)
        if any(part in IGNORED_DIRS for part in relative.parts):
            continue
        if _is_symlink(path):
            raise ValueError(f"静态产物不能包含符号链接：{relative}")
        if not path.is_file():
            continue
        if any(part.startswith(".") and part != ".well-known" for part in relative.parts):
            raise ValueError(f"静态产物不能包含隐藏文件：{relative}")
        if path.name.lower() in FORBIDDEN_NAMES:
            raise ValueError(f"静态产物包含敏感文件：{relative}")
        if path.suffix.lower() not in ALLOWED_SUFFIXES:
            raise ValueError(f"不支持的静态文件类型：{relative}")
        size = path.stat().st_size
        total += size
        files.append(StaticFile(path=path, relative=relative, size=size))
        if len(files) > max_files:
            raise ValueError(f"静态文件超过 {max_files} 个")
        if total > max_bytes:
            raise ValueError(f"静态产物解压后超过 {max_bytes // 1024 // 1024} MB")
        if path.suffix.lower() in TEXT_SUFFIXES:
            content = path.read_bytes()
            for label, pattern in SECRET_PATTERNS:
                if pattern.search(content):
                    raise ValueError(f"检测到疑似 {label}：{relative}")
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description="检查训练营静态部署产物")
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--project-dir", type=Path)
    args = parser.parse_args()
    project_dir = (args.project_dir or args.output_dir).resolve()
    secret_files = scan_for_local_secret_files(project_dir)
    if secret_files:
        raise SystemExit("项目包含禁止上传的密钥文件：" + ", ".join(secret_files))
    files = collect_static_files(args.output_dir)
    total = sum(item.size for item in files)
    print(f"PASS files={len(files)} bytes={total} root={args.output_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
