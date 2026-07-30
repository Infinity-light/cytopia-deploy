from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from preflight import (
    collect_files,
    collect_static_files,
    detect_plan,
    normalize_healthcheck,
    require_semantic_preflight,
    scan_for_local_secret_files,
)


def test_static_output_passes_without_credentials(tmp_path: Path):
    (tmp_path / "index.html").write_text("<h1>hello</h1>", encoding="utf-8")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("console.log('ok')", encoding="utf-8")

    files = collect_static_files(tmp_path)

    assert [item.relative.as_posix() for item in files] == [
        "assets/app.js",
        "index.html",
    ]


def test_project_scan_blocks_every_dotenv_variant(tmp_path: Path):
    (tmp_path / ".env.development").write_text("KEY=secret", encoding="utf-8")

    assert scan_for_local_secret_files(tmp_path) == [".env.development"]


def test_static_output_blocks_generic_assigned_credentials(tmp_path: Path):
    (tmp_path / "index.html").write_text("<h1>bad</h1>", encoding="utf-8")
    (tmp_path / "config.js").write_text(
        "window.API_KEY = 'provider-credential-1234567890';",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="assigned credential"):
        collect_static_files(tmp_path)


@pytest.mark.parametrize(
    ("requirement", "source", "preset", "database"),
    [
        ("Flask==3.1\npymysql==1.1\n", "from flask import Flask\napp = Flask(__name__)\n@app.get('/health')\ndef health(): return 'ok'\n", "flask", "mysql"),
        ("fastapi==0.139\naiosqlite==0.21\n", "from fastapi import FastAPI\napp = FastAPI()\n", "fastapi", "sqlite"),
    ],
)
def test_python_fullstack_detection(tmp_path: Path, requirement: str, source: str, preset: str, database: str):
    (tmp_path / "requirements.txt").write_text(requirement, encoding="utf-8")
    (tmp_path / "app.py").write_text(source, encoding="utf-8")

    plan = detect_plan(tmp_path)
    files = collect_files(tmp_path, kind="fullstack")

    assert plan.kind == "fullstack"
    assert plan.preset == preset
    assert plan.database == database
    assert plan.entrypoint == "app:app"
    assert {item.relative.as_posix() for item in files} == {"requirements.txt", "app.py"}


def test_python_stdlib_sqlite_is_detected_from_source(tmp_path: Path):
    (tmp_path / "requirements.txt").write_text("Flask==3.1.2\n", encoding="utf-8")
    (tmp_path / "app.py").write_text(
        "import sqlite3\nfrom flask import Flask\napp = Flask(__name__)\n",
        encoding="utf-8",
    )

    plan = detect_plan(tmp_path)

    assert plan.kind == "fullstack"
    assert plan.preset == "flask"
    assert plan.database == "sqlite"


def test_node_mysql_detection_and_infrastructure_files_are_excluded(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        '{"scripts":{"start":"node server.js"},"dependencies":{"express":"5","mysql2":"3"}}',
        encoding="utf-8",
    )
    (tmp_path / "server.js").write_text("require('express')()", encoding="utf-8")
    (tmp_path / "Dockerfile").write_text("FROM node:20", encoding="utf-8")

    plan = detect_plan(tmp_path)
    files = collect_files(tmp_path, kind="fullstack")

    assert plan.preset == "node"
    assert plan.database == "mysql"
    assert "Dockerfile" not in {item.relative.as_posix() for item in files}


def test_next_detection_rejects_incomplete_source_tree(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        '{"scripts":{"build":"next build","start":"next start"},"dependencies":{"next":"16"}}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="app or pages"):
        detect_plan(tmp_path)


def test_dynamic_node_project_cannot_fall_back_to_static_without_start(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        '{"scripts":{"build":"next build"},"dependencies":{"next":"16"}}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="拒绝静默降级"):
        detect_plan(tmp_path)


def test_explicit_next_export_is_classified_as_static(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        '{"scripts":{"build":"next build"},"dependencies":{"next":"16"}}',
        encoding="utf-8",
    )
    (tmp_path / "next.config.mjs").write_text(
        "export default { output: 'export' };",
        encoding="utf-8",
    )

    plan = detect_plan(tmp_path)

    assert plan.kind == "static"
    assert plan.output_dir == tmp_path / "out"
    assert "output: export" in plan.classification_reasons[0]


def test_semantic_preflight_blocks_public_password_and_loopback(tmp_path: Path):
    (tmp_path / "index.html").write_text("<h1>hello</h1>", encoding="utf-8")
    (tmp_path / "app.js").write_text(
        "const key = process.env.NEXT_PUBLIC_APP_PASSWORD;"
        "fetch('http://localhost:3000/api');",
        encoding="utf-8",
    )
    plan = detect_plan(tmp_path)

    with pytest.raises(ValueError, match="public_secret") as exc:
        require_semantic_preflight(tmp_path, plan, output_dir=tmp_path)

    assert "loopback_url" in str(exc.value)
    assert "unresolved_client_env" in str(exc.value)


def test_semantic_preflight_blocks_static_api_routes(tmp_path: Path):
    (tmp_path / "index.html").write_text("<h1>hello</h1>", encoding="utf-8")
    api = tmp_path / "api"
    api.mkdir()
    (api / "hello.js").write_text("export default () => 'ok'", encoding="utf-8")
    plan = detect_plan(tmp_path)

    with pytest.raises(ValueError, match="static_api_mismatch"):
        require_semantic_preflight(tmp_path, plan, output_dir=tmp_path)


def test_healthcheck_normalizes_duplicate_leading_slashes():
    assert normalize_healthcheck("//api/auth/me") == "/api/auth/me"


def test_device_flow_recovers_once_from_missing_code(monkeypatch):
    import deploy

    responses = iter(
        [
            (201, {"ok": True, "data": {"device_code": "first", "user_code": "AAAA-BBBB", "verification_uri": "https://example/first", "expires_in": 60, "interval": 2}}),
            (404, {"ok": False, "error": {"message": "设备码不存在"}}),
            (201, {"ok": True, "data": {"device_code": "second", "user_code": "CCCC-DDDD", "verification_uri": "https://example/second", "expires_in": 60, "interval": 2}}),
            (200, {"ok": True, "data": {"authorized": True}}),
        ]
    )
    monkeypatch.setattr(deploy, "request_json", lambda *_args, **_kwargs: next(responses))
    monkeypatch.setattr(deploy.time, "sleep", lambda _seconds: None)

    assert deploy.authorize("https://example", open_browser=False) == "second"


def test_fullstack_static_downgrade_requires_explicit_reason():
    import deploy

    detected = deploy.DeployPlan(
        "fullstack",
        "flask",
        "sqlite",
        "app:app",
        "/health",
    )

    with pytest.raises(ValueError, match="拒绝"):
        deploy.guard_static_downgrade(
            detected,
            requested_static=True,
            allow_static_export=False,
            reason=None,
        )

    reason = deploy.guard_static_downgrade(
        detected,
        requested_static=True,
        allow_static_export=True,
        reason="后端功能已移除，数据为构建时快照",
    )
    assert reason == "后端功能已移除，数据为构建时快照"


def test_http_decoder_rejects_mismatched_gzip_header():
    import deploy

    with pytest.raises(OSError):
        deploy._decode_http_body(b"already decoded", "gzip")
