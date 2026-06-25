"""통합 이미지 SPA 서빙 — 백엔드가 프론트 dist를 루트에서 서빙(있을 때만).

dist 디렉터리(DOCUX_FRONTEND_DIST)가 있으면 `/`·클라이언트 라우트는 index.html을,
실제 자산은 그 파일을 돌려준다. dist가 없으면 마운트하지 않아 순수 API(무회귀).
SPA 마운트는 API 라우트보다 **뒤**에 등록돼 API를 가리지 않아야 한다.

정적 서빙은 서비스 스택(DB·LLM)을 안 타므로 env 없이 검증 가능 — 라우트 우선순위는
구조(routes 순서)로 본다(헬스 호출은 LLM 빌드를 유발해 단위 범위 밖).
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from starlette.routing import Mount

from kms.api.app import create_app


def _make_dist(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_text("<!doctype html><title>DocuX</title>", encoding="utf-8")
    assets = root / "assets"
    assets.mkdir(exist_ok=True)
    (assets / "app.js").write_text("console.log('docux')", encoding="utf-8")


def test_no_dist_is_api_only(monkeypatch, tmp_path: Path) -> None:
    # dist 미존재 → SPA 미마운트 → 루트 라우트 없음(404). 서비스 스택 안 탐.
    monkeypatch.setenv("DOCUX_FRONTEND_DIST", str(tmp_path / "missing"))
    app = create_app()
    assert not any(getattr(r, "name", None) == "spa" for r in app.routes)
    assert TestClient(app).get("/").status_code == 404


def test_dist_serves_spa_and_assets(monkeypatch, tmp_path: Path) -> None:
    dist = tmp_path / "frontend"
    _make_dist(dist)
    monkeypatch.setenv("DOCUX_FRONTEND_DIST", str(dist))
    client = TestClient(create_app())

    root = client.get("/")  # 루트 → index.html
    assert root.status_code == 200
    assert "DocuX" in root.text

    asset = client.get("/assets/app.js")  # 실제 자산 → 그 파일
    assert asset.status_code == 200
    assert "docux" in asset.text

    spa = client.get("/parse")  # 클라이언트 라우트(실파일 없음) → index.html 폴백
    assert spa.status_code == 200
    assert "DocuX" in spa.text


def test_api_routes_registered_before_spa_mount(monkeypatch, tmp_path: Path) -> None:
    # SPA 마운트는 API 라우트보다 뒤 → `/healthz` 등 API가 우선 매칭(안 가려짐).
    dist = tmp_path / "frontend"
    _make_dist(dist)
    monkeypatch.setenv("DOCUX_FRONTEND_DIST", str(dist))
    app = create_app()

    spa_idx = next(
        i
        for i, r in enumerate(app.routes)
        if isinstance(r, Mount) and getattr(r, "name", None) == "spa"
    )
    health_idx = next(
        i for i, r in enumerate(app.routes) if getattr(r, "path", None) == "/healthz"
    )
    assert health_idx < spa_idx  # API가 SPA 마운트보다 먼저 = 우선 매칭
