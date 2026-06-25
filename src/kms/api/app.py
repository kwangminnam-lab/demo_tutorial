"""FastAPI 앱 팩토리 — 라우터를 조립하고 backend 선택 기본 의존성을 주입한다.

진입점은 얇게(CONVENTIONS): 앱 생성 + 라우터 등록 + composition root 연결만 한다.
서비스 의존성은 `kms.factory.build_services(get_settings())`로 조립해 placeholder
(`get_search_service`·`get_rag_service`·`get_ingestion_service`·`get_diff_service`)를
덮어쓴다. 조립은 **첫 요청 시점에 lazy**로 일어나므로 `create_app()` 호출만으로는
Settings(env)를 강제 로드하지 않는다 — 테스트·운영이 `dependency_overrides`로
자기 조립을 주입할 여지를 남긴다.

인증(`get_auth_service`)·문서 해소기(`get_document_resolver`)는 DB 세션·IdP 등
별도 조립 관심사라 placeholder로 둔다 — 미주입 호출은 명확히 실패한다(조용한
기본값 금지, ADR-005 인증).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI
from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from kms.api import health
from kms.api.deps import (
    get_diff_service,
    get_health_service,
    get_ingestion_service,
    get_llm_router,
    get_rag_service,
    get_search_service,
)
from kms.api.v1 import (
    diff,
    export,
    extract,
    files,
    ingest,
    members,
    me,
    parse,
    rag,
    search,
)
from kms.config.settings import get_settings
from kms.factory import AppServices, build_services


def create_app() -> FastAPI:
    """`/v1` 라우트(search·ingest·rag·diff·export)를 포함한 FastAPI 앱을 생성한다."""
    app = FastAPI(title="통합 지식 관리 시스템 API", version="1")
    # 데모 모드(ADR-026): 로그인 라우트 제거 — 인증 비활성(get_current_user가 고정 마스터 반환).
    app.include_router(members.router)
    app.include_router(search.router)
    app.include_router(me.router)
    app.include_router(files.router)
    app.include_router(ingest.router)
    app.include_router(rag.router)
    app.include_router(diff.router)
    app.include_router(parse.router)
    app.include_router(extract.router)
    app.include_router(export.router)
    app.include_router(health.router)
    _install_default_services(app)
    _install_spa(app)  # 통합 이미지: 프론트(SPA)를 루트에서 서빙(dist 있을 때만).
    return app


logger = logging.getLogger(__name__)

# 통합(단일) 이미지에서 빌드된 프론트 정적 산출물 경로(기본). 디렉터리가 있으면 백엔드가
# SPA를 루트(`/`)에서 직접 서빙한다(nginx 불요·동일 오리진 → 프론트의 상대 `/v1` 호출이
# 그대로 이 백엔드로 간다). 없으면(분리 빌드·로컬) 마운트하지 않아 순수 API로 동작한다.
_DEFAULT_FRONTEND_DIST = "/app/frontend"


class _SpaStaticFiles(StaticFiles):
    """SPA용 정적 서빙 — 없는 경로는 index.html로 폴백(클라이언트 라우팅 지원)."""

    async def get_response(self, path: str, scope: Scope):  # type: ignore[no-untyped-def]
        # StaticFiles는 미존재 파일에 404를 **raise**(반환 아님)하므로 둘 다 처리한다.
        # /parse 같은 클라이언트 라우트는 실제 파일이 없으니 index.html을 돌려준다.
        try:
            response = await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code == 404:
                return await super().get_response("index.html", scope)
            raise
        if response.status_code == 404:
            return await super().get_response("index.html", scope)
        return response


def _install_spa(app: FastAPI) -> None:
    """프론트 dist가 있으면 루트에 SPA 정적 서빙을 마운트한다(API 라우트 뒤 → 안 가림).

    `/v1`·`/healthz` 등 API 라우트는 먼저 등록돼 우선 매칭되고, 그 외 경로만 이 마운트가
    받아 정적 파일 또는 index.html(SPA 폴백)을 서빙한다. dist 부재 시 마운트하지 않아
    분리 빌드·테스트는 순수 API로 무회귀 동작한다(조용한 변경 아님 — 부재는 로깅).
    """
    dist = Path(os.environ.get("DOCUX_FRONTEND_DIST", _DEFAULT_FRONTEND_DIST))
    if not (dist.is_dir() and (dist / "index.html").is_file()):
        logger.info("프론트 dist 없음(%s) — API 전용으로 기동(통합 SPA 미마운트).", dist)
        return
    app.mount("/", _SpaStaticFiles(directory=str(dist), html=True), name="spa")
    logger.info("통합 SPA 서빙 활성 — 루트에서 프론트 정적 산출물 제공(%s).", dist)


def _install_default_services(app: FastAPI) -> None:
    """기본 backend(fake/memory/ephemeral)로 조립한 서비스를 placeholder에 연결한다.

    실제 조립(`build_services`)은 첫 미override 의존성 요청 시점에 1회만 일어난다 —
    `create_app()`이 곧장 `get_settings()`(env 필수 필드)를 강제 로드하지 않도록.
    테스트가 같은 placeholder를 자기 서비스로 덮어쓰면 이 기본 조립은 호출되지 않는다.
    """
    cached: list[AppServices] = []

    def services() -> AppServices:
        if not cached:
            cached.append(build_services(get_settings()))
        return cached[0]

    app.dependency_overrides[get_search_service] = lambda: services().search
    app.dependency_overrides[get_rag_service] = lambda: services().rag
    app.dependency_overrides[get_ingestion_service] = lambda: services().ingestion
    app.dependency_overrides[get_diff_service] = lambda: services().diff
    app.dependency_overrides[get_health_service] = lambda: services().health
    app.dependency_overrides[get_llm_router] = lambda: services().llm_router
