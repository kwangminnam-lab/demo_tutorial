"""파일 원본 접근 라우트 (GET /v1/files/{doc_id}) — 검색 결과 원본 열기·다운로드.

진입점은 얇게: 인증 → `SearchService.get_file`로 메타 해소(권한 인지 단계에서 막음)
→ 디스크에서 파일 위치 해소 → `FileResponse`. 비즈니스 로직(권한 강제·메타 조회)은
서비스에 있다. **권한 밖 문서는 존재 자체를 노출하지 않게** 404로 매핑한다
(권한 누설 방지, ADR-005).

`source_url`이 `local://<basename>` 형식이라 디스크 위치는 source 폴더(`data/<top>/`)
하위에서 같은 이름 파일을 찾아 해소한다. 같은 이름이 여러 곳에 있으면 첫 매칭을 쓴다
(콘텐츠는 doc_id=sha256으로 식별되므로 동일 콘텐츠는 같은 doc — 위치 모호성 없음).

`download=True`면 `Content-Disposition: attachment`로 다운로드. 기본은 inline(브라우저
표시 — PDF/이미지는 탭에서 보임). 시크릿·내부 경로는 응답·로그에 싣지 않는다.
"""

from __future__ import annotations

import mimetypes
import unicodedata
from pathlib import Path
from urllib.parse import quote, unquote

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse

from kms.api.deps import (
    get_current_user,
    get_ingestion_service,
    get_search_service,
    require_master,
)
from kms.config.settings import get_settings
from kms.domain.models import FileDoc, UserContext
from kms.services.ingestion_service import IngestionService
from kms.services.search_service import SearchService

router = APIRouter(prefix="/v1", tags=["files"])


def _basename_from_source_url(source_url: str | None) -> str | None:
    """`local://<name>`·`https://…/name` 등에서 파일명만 추출. 없으면 None."""
    if not source_url:
        return None
    without_scheme = source_url.split("://", 1)[-1]
    name = without_scheme.rsplit("/", 1)[-1]
    return unquote(name) or None


def _walk_for_name(root: Path, name: str) -> Path | None:
    """`root` 하위를 재귀로 걸어 정확히 `name` basename인 파일을 찾는다.

    `Path.rglob(name)`을 쓰지 않는 이유: `[`·`]`·`?`·`*`가 든 파일명을 glob 패턴으로
    오해석한다(예: `[마키나락스]` → 문자 클래스 매칭). 수동 walk + 문자열 비교로
    리터럴 매칭을 보장한다.

    한글 파일명은 NFC로 정규화한 뒤 비교한다 — macOS 파일시스템은 NFD로 저장하지만
    source_url(인덱스)은 NFC로 저장돼 직접 비교 시 항상 불일치하기 때문이다.
    """
    if not root.exists():
        return None
    target = unicodedata.normalize("NFC", name)
    for p in root.rglob("*"):
        if p.is_file() and unicodedata.normalize("NFC", p.name) == target:
            return p
    return None


def _resolve_local_path(doc: FileDoc) -> Path | None:
    """`data/<source>/` 하위에서 파일명을 찾아 절대 경로를 반환. 없으면 None.

    먼저 source 폴더로 좁혀 탐색(중복 이름 충돌 최소화). 못 찾으면 `data/` 전체로
    폴백한다. 작은 코퍼스에서는 둘 다 무겁지 않다.
    """
    name = _basename_from_source_url(doc.source_url)
    if name is None:
        return None
    data_root = Path(get_settings().data_root)
    return _walk_for_name(data_root / doc.source.value, name) or _walk_for_name(
        data_root, name
    )


@router.get("/files/{doc_id}")
def get_file(
    doc_id: str,
    download: bool = Query(default=False, description="True면 다운로드, False면 inline"),
    user: UserContext = Depends(get_current_user),
    service: SearchService = Depends(get_search_service),
) -> FileResponse:
    """파일 원본을 반환한다. 권한 밖 또는 미존재는 404로 통일(권한 누설 방지)."""
    doc = service.get_file(doc_id, user)
    if doc is None:
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다")
    path = _resolve_local_path(doc)
    if path is None or not path.is_file():
        # 인덱스엔 있으나 디스크 원본이 없을 수 있음(이동/삭제) — 사용자에겐 동일하게 404.
        raise HTTPException(status_code=404, detail="원본 파일을 찾을 수 없습니다")

    filename = path.name
    media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    # RFC 5987로 비ASCII 파일명을 안전하게 인코딩(한글 파일명 다운로드 대응).
    disposition_kind = "attachment" if download else "inline"
    headers = {
        "Content-Disposition": (
            f"{disposition_kind}; filename*=UTF-8''{quote(filename)}"
        )
    }
    return FileResponse(path=path, media_type=media_type, headers=headers, filename=filename)


@router.delete(
    "/files/{doc_id}",
    status_code=204,
    dependencies=[Depends(require_master)],
)
def delete_file(
    doc_id: str,
    service: IngestionService = Depends(get_ingestion_service),
) -> Response:
    """문서를 PG(벡터·어휘)·Neo4j에서 일괄 삭제한다 — **마스터 전용**(require_master).

    멤버는 403, 미인증은 401. 존재하지 않는 doc_id는 404. 성공은 204(본문 없음).
    삭제 로직은 IngestionService.delete_document(세 저장소 일괄)에 둔다.
    """
    deleted = service.delete_document(doc_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다")
    return Response(status_code=204)
