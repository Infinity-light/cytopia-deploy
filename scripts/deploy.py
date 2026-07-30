from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import mimetypes
import os
import shlex
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
import zipfile
import zlib
from dataclasses import replace
from html.parser import HTMLParser
from pathlib import Path

from preflight import (
    DeployPlan,
    collect_files,
    collect_static_files,
    detect_plan,
    normalize_healthcheck,
    require_semantic_preflight,
    scan_for_local_secret_files,
)

DEFAULT_API_BASE = os.getenv("CYTOPIA_DEPLOY_API", "https://summercamp.godpenai.com").rstrip("/")
TERMINAL_STATUSES = {"published", "failed"}
CLIENT_VERSION = "2.0.0"


def request_json(
    method: str,
    url: str,
    *,
    payload: dict | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 60,
) -> tuple[int, dict]:
    data = None
    request_headers = {"Accept": "application/json", **(headers or {})}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = {"ok": False, "error": {"message": body or str(exc)}}
        return exc.code, parsed


def api_data(status: int, payload: dict) -> dict:
    if status >= 400 or not payload.get("ok"):
        error = payload.get("error") or {}
        raise RuntimeError(error.get("message") or error.get("code") or f"HTTP {status}")
    return payload.get("data", payload)


def guard_static_downgrade(
    detected: DeployPlan,
    *,
    requested_static: bool,
    allow_static_export: bool,
    reason: str | None,
) -> str:
    if detected.kind != "fullstack" or not requested_static:
        return ""
    cleaned = " ".join((reason or "").split())
    if not allow_static_export:
        raise ValueError(
            "项目已识别为全栈应用，拒绝通过 --dist/--preset static 静默降级。"
            "如项目确实支持纯静态导出，请显式添加 --allow-static-export "
            "并提供 --static-export-reason。"
        )
    if len(cleaned) < 8:
        raise ValueError("--static-export-reason 至少需要 8 个字符，说明为何不需要后端和数据库。")
    return cleaned


def skill_fingerprint() -> str:
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for relative in ("SKILL.md", "scripts/preflight.py", "scripts/deploy.py"):
        path = root / relative
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def source_git_state(project_dir: Path) -> tuple[str, bool]:
    try:
        commit = subprocess.run(
            ["git", "-C", str(project_dir), "rev-parse", "HEAD"],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )
        if commit.returncode:
            return "", False
        dirty = subprocess.run(
            ["git", "-C", str(project_dir), "status", "--porcelain"],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )
        return commit.stdout.strip()[:64], bool(dirty.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return "", False


class _AssetParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.assets: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        values = dict(attrs)
        if tag == "script" and values.get("src"):
            self.assets.append(values["src"])
        if (
            tag == "link"
            and values.get("href")
            and "stylesheet" in values.get("rel", "").lower()
        ):
            self.assets.append(values["href"])


def _decode_http_body(body: bytes, encoding: str) -> bytes:
    normalized = encoding.lower().strip()
    if normalized == "gzip":
        return gzip.decompress(body)
    if normalized == "deflate":
        return zlib.decompress(body)
    if normalized and normalized != "identity":
        raise RuntimeError(f"线上验证不支持响应压缩格式：{encoding}")
    return body


def _fetch_public(url: str) -> tuple[int, dict[str, str], bytes, str]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Encoding": "gzip",
            "User-Agent": f"cytopia-deploy/{CLIENT_VERSION}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024:
                raise RuntimeError("线上响应超过 8 MB，无法完成安全验证")
            headers = {key.lower(): value for key, value in response.headers.items()}
            body = _decode_http_body(raw, headers.get("content-encoding", ""))
            return response.status, headers, body, response.geturl()
    except urllib.error.HTTPError as exc:
        return exc.code, {}, exc.read(), exc.geturl()


def verify_public_site(url: str, *, kind: str, healthcheck: str) -> dict:
    status, headers, body, final_url = _fetch_public(url)
    if not 200 <= status < 400:
        raise RuntimeError(f"线上首页验证失败：HTTP {status}")
    if len(body.strip()) < 16:
        raise RuntimeError("线上首页验证失败：响应为空或过短")

    checked_assets: list[str] = []
    content_type = headers.get("content-type", "")
    if "html" in content_type.lower() or b"<html" in body[:2048].lower():
        parser = _AssetParser()
        parser.feed(body.decode("utf-8", errors="replace"))
        page_origin = urllib.parse.urlsplit(final_url)
        for asset in parser.assets:
            asset_url = urllib.parse.urljoin(final_url, asset)
            parsed = urllib.parse.urlsplit(asset_url)
            if parsed.netloc != page_origin.netloc:
                continue
            asset_status, _, asset_body, _ = _fetch_public(asset_url)
            if not 200 <= asset_status < 400 or not asset_body:
                raise RuntimeError(
                    f"线上静态资源验证失败：{asset_url} (HTTP {asset_status})"
                )
            checked_assets.append(asset_url)
            if len(checked_assets) >= 6:
                break

    probe_url = (
        urllib.parse.urljoin(final_url, "__cytopia_verify__/")
        if kind == "static"
        else urllib.parse.urljoin(final_url, healthcheck.lstrip("/"))
    )
    probe_status, _, probe_body, _ = _fetch_public(probe_url)
    if not 200 <= probe_status < 400 or not probe_body:
        label = "SPA 深链接" if kind == "static" else "健康端点"
        raise RuntimeError(f"线上{label}验证失败：HTTP {probe_status}")
    return {
        "home_status": status,
        "probe_url": probe_url,
        "probe_status": probe_status,
        "assets_checked": len(checked_assets),
    }


def package_manager(project_dir: Path) -> str:
    if (project_dir / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (project_dir / "yarn.lock").exists():
        return "yarn"
    return "npm"


def detect_build(project_dir: Path) -> tuple[str | None, Path]:
    package_json = project_dir / "package.json"
    if package_json.is_file():
        package = json.loads(package_json.read_text(encoding="utf-8"))
        if "build" not in package.get("scripts", {}):
            raise ValueError("package.json 没有 build 脚本；请补齐或用 --dist 指定已有静态产物")
        manager = package_manager(project_dir)
        command = f"{manager} run build"
        for name in ("dist", "build", "out"):
            candidate = project_dir / name
            if candidate.exists():
                return command, candidate
        return command, project_dir / "dist"
    if (project_dir / "index.html").is_file():
        return None, project_dir
    raise ValueError("无法识别静态项目；请用 --dist 指定包含 index.html 的产物目录")


def run_build(command: str | None, project_dir: Path) -> None:
    if not command:
        return
    print(f"[build] {command}", flush=True)
    result = subprocess.run(command, cwd=project_dir, shell=True, check=False)
    if result.returncode:
        raise RuntimeError(f"本地构建失败，退出码 {result.returncode}")


def make_zip(files, output_dir: Path, archive: Path) -> None:
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for item in files:
            bundle.write(item.path, item.relative.as_posix())


def multipart_body(fields: dict[str, str], file_field: str, file_path: Path) -> tuple[bytes, str]:
    boundary = f"----cytopia-{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                value.encode("utf-8"),
                b"\r\n",
            ]
        )
    content_type = mimetypes.guess_type(file_path.name)[0] or "application/zip"
    chunks.extend(
        [
            f"--{boundary}\r\n".encode(),
            (
                f'Content-Disposition: form-data; name="{file_field}"; '
                f'filename="{file_path.name}"\r\n'
            ).encode(),
            f"Content-Type: {content_type}\r\n\r\n".encode(),
            file_path.read_bytes(),
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    return b"".join(chunks), boundary


def upload_archive(api_base: str, device_code: str, manifest: dict, archive: Path) -> dict:
    body, boundary = multipart_body(
        {"manifest": json.dumps(manifest, ensure_ascii=False)},
        "artifact",
        archive,
    )
    request = urllib.request.Request(
        f"{api_base}/api/deploy/artifacts",
        data=body,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {device_code}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return api_data(response.status, json.loads(response.read().decode("utf-8")))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {"ok": False, "error": {"message": body or str(exc)}}
        return api_data(exc.code, payload)


def authorize(api_base: str, *, open_browser: bool, recovery_attempts: int = 1) -> str:
    status, response = request_json("POST", f"{api_base}/api/deploy/device/start")
    device = api_data(status, response)
    print(f"\n设备码：{device['user_code']}", flush=True)
    print(f"授权地址：{device['verification_uri']}", flush=True)
    print("请在浏览器登录训练营账号并确认本次部署。", flush=True)
    if open_browser:
        webbrowser.open(device["verification_uri"])
    deadline = time.monotonic() + int(device["expires_in"])
    interval = max(2, int(device.get("interval", 2)))
    while time.monotonic() < deadline:
        time.sleep(interval)
        poll_status, poll_response = request_json(
            "POST",
            f"{api_base}/api/deploy/device/token",
            payload={"device_code": device["device_code"]},
        )
        if poll_status in {404, 410}:
            if recovery_attempts > 0:
                print("[auth] 设备码已失效，正在自动申请新的授权码。", flush=True)
                return authorize(
                    api_base,
                    open_browser=open_browser,
                    recovery_attempts=recovery_attempts - 1,
                )
            raise RuntimeError("设备码不存在或已过期，请重新部署")
        polled = api_data(poll_status, poll_response)
        if polled["authorized"]:
            print("[auth] 已授权，开始上传。", flush=True)
            return device["device_code"]
    raise RuntimeError("等待浏览器授权超时")


def wait_for_deployment(api_base: str, device_code: str, deployment_id: str) -> dict:
    seen = 0
    deadline = time.monotonic() + 10 * 60
    while time.monotonic() < deadline:
        status, response = request_json(
            "GET",
            f"{api_base}/api/deploy/jobs/{deployment_id}",
            headers={"Authorization": f"Bearer {device_code}"},
        )
        job = api_data(status, response)
        for event in job.get("events", [])[seen:]:
            print(f"[{event['stage']}] {event['message']}", flush=True)
        seen = len(job.get("events", []))
        if job["status"] in TERMINAL_STATUSES:
            return job
        time.sleep(2)
    raise RuntimeError("部署等待超过十分钟")


def main() -> int:
    parser = argparse.ArgumentParser(description="无密钥部署项目到菁英 AI 创孵营")
    parser.add_argument("--project-dir", type=Path, default=Path.cwd())
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--dist", type=Path)
    parser.add_argument("--build-command")
    parser.add_argument("--preset", choices=("static", "fastapi", "flask", "node"))
    parser.add_argument("--database", choices=("auto", "none", "sqlite", "postgresql", "mysql"), default="auto")
    parser.add_argument("--entrypoint")
    parser.add_argument("--healthcheck")
    parser.add_argument(
        "--allow-static-export",
        action="store_true",
        help="显式允许将检测为全栈的项目部署为静态导出",
    )
    parser.add_argument(
        "--static-export-reason",
        help="说明静态导出为何不依赖后端和数据库",
    )
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    project_dir = args.project_dir.resolve()
    secret_files = scan_for_local_secret_files(project_dir)
    if secret_files:
        raise RuntimeError(
            "项目包含禁止上传的密钥文件：" + ", ".join(secret_files)
            + "。请移除密钥并改用 /__camp/ai/chat。"
        )
    detected_plan = detect_plan(project_dir)
    source_commit, source_dirty = source_git_state(project_dir)
    static_export_reason = guard_static_downgrade(
        detected_plan,
        requested_static=args.preset == "static" or args.dist is not None,
        allow_static_export=args.allow_static_export,
        reason=args.static_export_reason,
    )
    override_items = []
    if args.preset:
        override_items.append(f"preset={args.preset}")
    if args.dist:
        override_items.append("dist=explicit")
    if args.build_command is not None:
        override_items.append("build_command=custom")
    if args.database != "auto":
        override_items.append(f"database={args.database}")
    if args.entrypoint:
        override_items.append(f"entrypoint={args.entrypoint}")
    if args.healthcheck:
        override_items.append(f"healthcheck={normalize_healthcheck(args.healthcheck)}")
    if static_export_reason:
        override_items.append("static_export=approved")
    override_summary = "; ".join(override_items)
    plan = detected_plan
    classification_overridden = False
    if args.preset:
        classification_overridden = args.preset != detected_plan.preset
        plan = DeployPlan(
            kind="static" if args.preset == "static" else "fullstack",
            preset=args.preset,
            database=plan.database,
            entrypoint=args.entrypoint or plan.entrypoint,
            healthcheck=args.healthcheck or plan.healthcheck,
            build_command=plan.build_command if args.preset == "static" else None,
            output_dir=plan.output_dir if args.preset == "static" else None,
            classification_reasons=detected_plan.classification_reasons,
        )
    if args.database != "auto":
        plan = replace(plan, database=args.database)
    if args.entrypoint:
        plan = replace(plan, entrypoint=args.entrypoint)
    if args.healthcheck:
        plan = replace(plan, healthcheck=normalize_healthcheck(args.healthcheck))
    else:
        plan = replace(plan, healthcheck=normalize_healthcheck(plan.healthcheck))
    if args.dist:
        classification_overridden = classification_overridden or detected_plan.kind != "static"
        output_dir = args.dist if args.dist.is_absolute() else project_dir / args.dist
        build_command = args.build_command
        plan = replace(plan, kind="static", preset="static", database="none", output_dir=output_dir)
    else:
        output_dir = plan.output_dir or project_dir
        build_command = args.build_command if args.build_command is not None else plan.build_command
    run_build(build_command, project_dir)
    require_semantic_preflight(project_dir, plan, output_dir=output_dir)
    files = collect_static_files(output_dir) if plan.kind == "static" else collect_files(project_dir, kind="fullstack")
    total_bytes = sum(item.size for item in files)
    print(
        f"[preflight] PASS kind={plan.kind} preset={plan.preset} database={plan.database} "
        f"files={len(files)} bytes={total_bytes} source={output_dir}",
        flush=True,
    )

    with tempfile.TemporaryDirectory(prefix="cytopia-deploy-") as temporary:
        archive = Path(temporary) / "dist.zip"
        make_zip(files, output_dir, archive)
        print(f"[package] {archive.stat().st_size} bytes", flush=True)
        if args.dry_run:
            result = {
                "status": "dry-run",
                "files": len(files),
                "bytes": total_bytes,
                "archive_bytes": archive.stat().st_size,
                "kind": plan.kind,
                "preset": plan.preset,
                "database": plan.database,
                "entrypoint": plan.entrypoint,
                "client_version": CLIENT_VERSION,
                "skill_fingerprint": skill_fingerprint(),
                "classification_reason": "; ".join(detected_plan.classification_reasons),
                "classification_overridden": classification_overridden,
                "static_export_reason": static_export_reason,
                "override_summary": override_summary,
                "source_commit": source_commit,
                "source_dirty": source_dirty,
            }
            print(json.dumps(result, ensure_ascii=False) if args.json else "本地预检完成，未上传。")
            return 0
        device_code = authorize(args.api_base.rstrip("/"), open_browser=not args.no_open)
        manifest = {
            "version": 2 if plan.kind == "fullstack" else 1,
            "project_name": args.project_name,
            "entry": "index.html",
            "kind": plan.kind,
            "preset": plan.preset,
            "database": plan.database,
            "entrypoint": plan.entrypoint,
            "healthcheck": plan.healthcheck,
            "spa": True,
            "needs_ai_gateway": True,
            "source": "cytopia-deploy-skill",
            "client_version": CLIENT_VERSION,
            "skill_fingerprint": skill_fingerprint(),
            "classification_reason": "; ".join(detected_plan.classification_reasons),
            "classification_overridden": classification_overridden,
            "static_export_reason": static_export_reason,
            "override_summary": override_summary,
            "source_commit": source_commit,
            "source_dirty": source_dirty,
        }
        queued = upload_archive(args.api_base.rstrip("/"), device_code, manifest, archive)
        print(f"[queued] deployment_id={queued['deployment_id']}", flush=True)
        job = wait_for_deployment(
            args.api_base.rstrip("/"),
            device_code,
            queued["deployment_id"],
        )
    if job["status"] != "published":
        raise RuntimeError(job.get("error") or "部署失败")
    http_verification = verify_public_site(
        job["url"],
        kind=plan.kind,
        healthcheck=plan.healthcheck,
    )
    print(
        "[verify] PASS "
        f"home={http_verification['home_status']} "
        f"probe={http_verification['probe_status']} "
        f"assets={http_verification['assets_checked']}",
        flush=True,
    )
    result = {
        "status": "published",
        "url": job["url"],
        "fallback_url": job["fallback_url"],
        "hostname": job["hostname"],
        "deployment_id": job["deployment_id"],
        "http_verification": http_verification,
        "browser_verification_required": True,
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"\n部署完成：{job['url']}")
        if job["fallback_url"] != job["url"]:
            print(f"备用入口：{job['fallback_url']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
