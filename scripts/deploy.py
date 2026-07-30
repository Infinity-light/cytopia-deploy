from __future__ import annotations

import argparse
import json
import mimetypes
import os
import shlex
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import webbrowser
import zipfile
from dataclasses import replace
from pathlib import Path

from preflight import (
    DeployPlan,
    collect_files,
    collect_static_files,
    detect_plan,
    normalize_healthcheck,
    scan_for_local_secret_files,
)

DEFAULT_API_BASE = os.getenv("CYTOPIA_DEPLOY_API", "https://summercamp.godpenai.com").rstrip("/")
TERMINAL_STATUSES = {"published", "failed"}


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
    plan = detect_plan(project_dir)
    if args.preset:
        plan = DeployPlan(
            kind="static" if args.preset == "static" else "fullstack",
            preset=args.preset,
            database=plan.database,
            entrypoint=args.entrypoint or plan.entrypoint,
            healthcheck=args.healthcheck or plan.healthcheck,
            build_command=plan.build_command if args.preset == "static" else None,
            output_dir=plan.output_dir if args.preset == "static" else None,
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
        output_dir = args.dist if args.dist.is_absolute() else project_dir / args.dist
        build_command = args.build_command
        plan = replace(plan, kind="static", preset="static", database="none", output_dir=output_dir)
    else:
        output_dir = plan.output_dir or project_dir
        build_command = args.build_command if args.build_command is not None else plan.build_command
    run_build(build_command, project_dir)
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
    result = {
        "status": "published",
        "url": job["url"],
        "fallback_url": job["fallback_url"],
        "hostname": job["hostname"],
        "deployment_id": job["deployment_id"],
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
