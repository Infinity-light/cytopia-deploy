from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from preflight import (
    collect_files,
    collect_static_files,
    detect_plan,
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
