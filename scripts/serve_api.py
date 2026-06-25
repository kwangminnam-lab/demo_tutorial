"""API composition root — create_app()에 인증(auth)을 주입해 실제로 서빙한다.

`create_app()`은 search/rag/ingestion/diff/health만 factory로 주입하고,
인증(`get_auth_service`)과 문서 해소기(`get_document_resolver`)는 DB 세션·IdP 등
별도 조립 관심사라 placeholder로 둔다(미주입 호출은 RuntimeError). 이 스크립트가
그 composition root다: DB 세션·JwtIdentityProvider 기반 AuthService를 주입하고
uvicorn으로 띄운다.

backend(임베더·LLM·graph·vectorstore)는 모두 환경 변수(Settings)로 선택한다 —
docs/INFRA.md 참고. 인증은 **항상 활성**이다(우회/익명 모드 없음): 비밀번호 로그인
(`POST /v1/auth/login`)으로 서명 JWT를 발급하고 `JwtIdentityProvider`가 검증한다.
기동 시 마스터 계정이 없으면 기본 마스터를 시드한다(이후 비번 변경 권장).

사용:
    # 의존(임베더·LLM 등)은 env로 선택, JWT_SECRET 주입 후 기동
    JWT_SECRET=<비밀키> python scripts/serve_api.py --host 127.0.0.1 --port 8000
"""
from __future__ import annotations

import argparse
import os
from datetime import UTC, datetime
from pathlib import Path

import uvicorn

from kms.adapters.auth.passwords import hash_password
from kms.adapters.auth.tokens import JwtCodec
from kms.adapters.db.engine import create_engine, create_sessionmaker
from kms.adapters.db.extraction_repository import ExtractionRepository
from kms.adapters.db.models import Base
from kms.adapters.db.repository import AccountRepository
from kms.adapters.document_registry import build_document_resolver, content_doc_id
from kms.api.app import create_app
from kms.api.deps import (
    get_account_service,
    get_auth_service,
    get_document_resolver,
    get_extraction_service,
)
from kms.domain.access import AccessLevel
from kms.domain.models import DocumentMetadata, SourceType
from kms.config.settings import get_settings
from kms.factory import build_field_extractor, build_line_registry, build_vlm_extractor
from kms.services.account_service import AccountService
from kms.services.auth_service import AuthService, JwtIdentityProvider
from kms.services.extraction_service import ExtractionService

# 마스터 부트스트랩 기본값(ADR-017). 최초 1회 시드, 이후 변경 권장.
_MASTER_EMAIL = "platform.master@makinarocks.ai"
_MASTER_INITIAL_PASSWORD = "mrx12341234!!"


# 데모용 source 디렉토리 → 접근 레벨 정책. 부서별 RBAC는 후속(ADR-005)이라,
# 적재(_api_ingest)와 동일하게 단순화한다: onedrive=사장 전용, 그 외=임직원.
_SOURCE_DIRS: dict[str, tuple[SourceType, AccessLevel]] = {
    "slack": (SourceType.SLACK, AccessLevel.임직원),
    "googledrive": (SourceType.GOOGLEDRIVE, AccessLevel.임직원),
    "onedrive": (SourceType.ONEDRIVE, AccessLevel.사장),
}
_DOC_EXTS = {".pdf", ".docx", ".pptx", ".xlsx", ".txt"}
_DEFAULT_DEPT = "기획"


def build_doc_entries(data_root: Path) -> list[tuple[Path, DocumentMetadata]]:
    """`data/<source>/` 트리를 스캔해 (파일, 메타) 목록을 만든다.

    diff 해소기의 입력이다. source는 디렉토리명으로, access는 `_SOURCE_DIRS`
    정책으로 결정한다(적재와 동일 규약). 추출 불가 확장자는 건너뛴다.
    """
    now = datetime.now(UTC)
    entries: list[tuple[Path, DocumentMetadata]] = []
    for sub, (source, access) in _SOURCE_DIRS.items():
        for path in sorted((data_root / sub).glob("*")):
            if not path.is_file() or path.suffix.lower() not in _DOC_EXTS:
                continue
            entries.append(
                (
                    path,
                    DocumentMetadata(
                        source=source,
                        access=access,
                        author_department=_DEFAULT_DEPT,
                        source_url=f"local://{path.name}",
                        ingested_at=now,
                    ),
                )
            )
    return entries


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="통합 지식 관리 시스템 API 서버")
    p.add_argument("--host", default="127.0.0.1")
    # PaaS(Render/Fly 등)는 바인딩 포트를 $PORT로 주입한다 — 미지정 시 그 값을
    # 따르고(무료 배포 호환, ADR-019), 없으면 로컬 기본 8000. CLI --port가 최우선.
    p.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    p.add_argument(
        "--data-root",
        default=None,
        metavar="DIR",
        help="문서 비교(diff) 해소기가 스캔할 자료 루트. 지정 시 data/<source>/ 문서를 "
        "doc_id·파일명으로 비교 가능하게 등록한다. 예: data",
    )
    p.add_argument(
        "--static-dir",
        default=os.environ.get("STATIC_DIR"),
        metavar="DIR",
        help="정적 프론트(SPA) 빌드 산출물 디렉토리. 지정 시 같은 오리진에서 SPA를 "
        "서빙한다(단일 이미지 토폴로지 — nginx·CORS 불요). 예: /app/frontend",
    )
    return p


def _mount_spa(app, static_dir: str) -> None:  # type: ignore[no-untyped-def]
    """정적 프론트(SPA)를 API 라우터 뒤에 마운트한다 — 단일 이미지 토폴로지(같은 오리진).

    /v1·/healthz·/docs 등은 create_app()에서 먼저 등록돼 우선 매칭되고, 그 외 GET
    경로는 해시 자산(StaticFiles) 또는 index.html로 폴백한다(클라이언트 라우팅).
    같은 오리진이라 CORS·nginx가 불요하다. 디렉토리에 index.html이 없으면 조용히
    넘기지 않고 명확히 실패한다(오타로 빈 화면 나는 것 방지 — 조용한 실패 금지).
    """
    from fastapi.staticfiles import StaticFiles
    from starlette.responses import FileResponse

    root = Path(static_dir)
    index = root / "index.html"
    if not index.is_file():
        raise SystemExit(f"--static-dir에 index.html이 없습니다: {root}")

    assets = root / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def _spa(full_path: str):  # type: ignore[no-untyped-def]
        candidate = root / full_path
        if full_path and candidate.is_file():
            return FileResponse(str(candidate))
        return FileResponse(str(index))

    print(f"[spa] 정적 프론트 서빙: {root}")


def _seed_master(session_factory) -> None:  # type: ignore[no-untyped-def]
    """마스터 계정이 없으면 기본 마스터를 시드한다 (ADR-017, 최초 1회).

    이미 마스터가 있으면 아무것도 하지 않는다(비번 덮어쓰지 않음). 시드 비번은
    기본값이라 운영 전 변경해야 한다 — 로그로 경고한다(평문은 로그에도 안 남긴다).
    """
    with session_factory() as session:
        repo = AccountRepository(session)
        existing = repo.get_by_email(_MASTER_EMAIL)
        if existing is not None:
            return
        repo.create(
            _MASTER_EMAIL,
            department="관리",
            access_level=int(AccessLevel.마스터),
            password_hash=hash_password(_MASTER_INITIAL_PASSWORD),
        )
        session.commit()
    print(
        f"[seed] 마스터 계정 생성: {_MASTER_EMAIL} "
        "(초기 비밀번호 사용 중 — 운영 전 반드시 변경)."
    )


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    settings = get_settings()

    engine = create_engine(settings)
    Base.metadata.create_all(engine)  # dev 편의: 테이블 없으면 생성(운영은 alembic)
    session_factory = create_sessionmaker(engine)

    # JWT 세션 토큰 코덱(ADR-017). 인증은 항상 활성이라 시크릿은 필수다 —
    # 미설정이면 기동 거부(약한 키 금지, 우회 모드 없음).
    jwt_secret = settings.jwt_secret
    if not jwt_secret:
        raise SystemExit(
            "JWT_SECRET 미설정 — 비밀번호 로그인에 필요합니다. .env/시크릿 매니저로 주입하세요."
        )
    codec = JwtCodec(jwt_secret, expire_minutes=settings.jwt_expire_minutes)

    # 마스터 부트스트랩: 마스터 계정이 하나도 없으면 기본 마스터를 시드한다(ADR-017).
    _seed_master(session_factory)

    app = create_app()

    # CORS: 프론트(GitHub Pages 등)가 다른 오리진에서 이 API를 호출할 때 필요(ADR-019).
    # create_app()은 env-free 계약이라 여기 composition root에서 settings로 켠다.
    # 오리진 미설정이면 미설치(동일 오리진 전용 — 로컬 dev는 vite proxy로 충분).
    if settings.cors_origins:
        from fastapi.middleware.cors import CORSMiddleware

        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        print(f"[cors] 허용 오리진: {', '.join(settings.cors_origins)}")

    # 정적 프론트(SPA) 서빙 — 단일 이미지 토폴로지. dist/를 API와 같은 오리진에서
    # 서빙해 CORS·nginx를 없앤다. API 라우터 뒤(마지막)에 폴백 라우트로 등록한다.
    if args.static_dir:
        _mount_spa(app, args.static_dir)

    # 인증 주입(yield 의존): 요청마다 세션을 열고 끝나면 닫는다(세션 누수 방지, 보안 V6).
    # 로그인·인증은 읽기라 커밋 불요.
    def auth_service():  # type: ignore[no-untyped-def]
        session = session_factory()
        try:
            repo = AccountRepository(session)
            yield AuthService(JwtIdentityProvider(codec), repo, codec=codec)
        finally:
            session.close()

    # 멤버 관리 주입(yield 의존): 쓰기(추가/삭제)가 영속되도록 성공 시 커밋한다.
    # 라우트가 예외를 던지면 커밋 전 close → 부분 쓰기 롤백(마스터 전용은 require_master).
    def account_service():  # type: ignore[no-untyped-def]
        session = session_factory()
        try:
            yield AccountService(AccountRepository(session))
            session.commit()
        finally:
            session.close()

    # 필드추출 주입(yield 의존): registry/extractor는 무상태 공유(1회 조립), repo만
    # 요청 세션에 묶는다. 쓰기(스키마/결과 저장)가 영속되도록 성공 시 커밋한다.
    line_registry = build_line_registry()
    field_extractor = build_field_extractor(settings)
    # 손글씨/스캔 추출기 — 사내 Qwen3-VL vLLM(VLM_BASE_URL 설정 시). 폐쇄망 내부추론, 외부 전송 0.
    vlm_extractor = build_vlm_extractor(settings)
    if vlm_extractor is not None:
        print("[extract] VLM(사내 Qwen3-VL) 손글씨/스캔 추출 활성 — 외부 전송 0(내부 추론)")

    def extraction_service():  # type: ignore[no-untyped-def]
        session = session_factory()
        try:
            repo = ExtractionRepository(session)
            yield ExtractionService(repo, line_registry, field_extractor, vlm_extractor)
            session.commit()
        finally:
            session.close()

    app.dependency_overrides[get_auth_service] = auth_service
    app.dependency_overrides[get_account_service] = account_service
    app.dependency_overrides[get_extraction_service] = extraction_service

    # 문서 해소기 주입: --data-root 지정 시 data/ 문서를 doc_id/파일명으로 비교 가능.
    # 미지정이면 placeholder 유지(미구성 호출은 RuntimeError → diff는 graceful 에러).
    if args.data_root:
        entries = build_doc_entries(Path(args.data_root))
        resolver = build_document_resolver(entries)
        app.dependency_overrides[get_document_resolver] = lambda: resolver
        print(f"[doc] diff 해소기 등록: 문서 {len(entries)}건 (root={args.data_root})")
        for path, meta in entries:
            print(f"  - {meta.access.name:>3} {meta.source.value:<11} "
                  f"{content_doc_id(path)[:12]}…  {path.name}")

    print(f"[serve] backends: embedder={settings.embedder_backend} "
          f"llm={settings.llm_backend} graph={settings.graph_backend}")
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
