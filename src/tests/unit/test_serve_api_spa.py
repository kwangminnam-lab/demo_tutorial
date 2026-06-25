"""정적 프론트(SPA) 서빙 마운트 단위 테스트 — 단일 이미지 토폴로지(ADR-019).

`_mount_spa`(composition root)가 API 라우터를 가리지 않으면서(우선 매칭) 미매칭
GET 경로를 index.html로 폴백하고, 실파일·해시 자산을 그대로 서빙하며, index.html이
없으면 조용히 넘기지 않고 명확히 실패(SystemExit)하는지 잠근다.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# serve_api는 진입점(scripts/)이라 src 패키지 경로에 없다 — repo 루트를 얹어 import한다
# (scripts/test_execute.py와 동일한 진입점 테스트 관례).
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.serve_api import _mount_spa  # noqa: E402


def _spa_dir(root: Path) -> Path:
    (root / "index.html").write_text("<html>SPA</html>", encoding="utf-8")
    (root / "assets").mkdir()
    (root / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (root / "favicon.ico").write_text("ico", encoding="utf-8")
    return root


def _app_with_spa(static_dir: Path) -> TestClient:
    app = FastAPI()

    @app.get("/v1/ping")
    def _ping() -> dict[str, bool]:
        return {"ok": True}

    _mount_spa(app, str(static_dir))
    return TestClient(app)


def test_api_route_takes_precedence_over_spa(tmp_path: Path) -> None:
    client = _app_with_spa(_spa_dir(tmp_path))
    assert client.get("/v1/ping").json() == {"ok": True}


def test_root_and_deep_routes_fall_back_to_index(tmp_path: Path) -> None:
    client = _app_with_spa(_spa_dir(tmp_path))
    assert "SPA" in client.get("/").text
    # 클라이언트 라우팅 — 서버에 없는 경로도 index.html로 폴백.
    assert "SPA" in client.get("/some/deep/route").text


def test_real_files_and_assets_served(tmp_path: Path) -> None:
    client = _app_with_spa(_spa_dir(tmp_path))
    assert client.get("/favicon.ico").text == "ico"
    assert client.get("/assets/app.js").status_code == 200


def test_missing_index_raises_not_silent(tmp_path: Path) -> None:
    # index.html 없으면 빈 화면으로 조용히 넘기지 않고 명확히 실패한다.
    with pytest.raises(SystemExit):
        _mount_spa(FastAPI(), str(tmp_path))
