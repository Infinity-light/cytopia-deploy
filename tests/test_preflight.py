from pathlib import Path

import pytest

from scripts.preflight import collect_static_files, scan_for_local_secret_files


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
