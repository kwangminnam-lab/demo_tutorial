"""문서 해소기(DocumentResolver) 구현 — doc_id를 실제 파일 경로·메타로 매핑한다.

diff 라우트(POST /v1/diff)는 doc_id를 `(Path, DocumentMetadata)`로 해소해야 두 문서를
비교할 수 있다(`api/deps.py`의 `DocumentResolver` 계약). 해소 *방법*은 진입 경계가
모르는 조립 관심사다 — 이 모듈이 그 구현이고, 조립은 composition root(serve_api)에서
주입한다.

doc_id는 적재(`ingestion_service._content_hash`)와 동일한 `sha256(파일 bytes)` 규약을
따른다 — 따라서 같은 파일은 시스템 전역에서 같은 doc_id를 가진다(벡터 색인과 일관).
사람이 해시를 다루기 번거로우므로 파일명 별칭(alias)으로도 같은 항목을 찾게 한다
(dev/데모 편의). 미등록 키는 `KeyError`(라우트가 404로 변환).
"""

from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Iterable
from pathlib import Path

from kms.api.deps import DocumentResolver
from kms.domain.models import DocumentMetadata


def content_doc_id(path: Path) -> str:
    """파일 콘텐츠의 SHA-256 hexdigest — 적재와 동일한 멱등 doc_id 규약."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _key(s: str) -> str:
    """조회 키 정규화 — NFC.

    macOS는 한글 파일명을 NFD(자모 분리)로 저장한다(`path.name`도 NFD). 브라우저·
    JSON 입력은 보통 NFC라 그대로면 dict 조회가 어긋난다. 별칭 등록·조회를 모두 NFC로
    정규화해 일치시킨다(해시 doc_id는 ASCII라 정규화 무영향)."""
    return unicodedata.normalize("NFC", s)


def build_document_resolver(
    entries: Iterable[tuple[Path, DocumentMetadata]],
) -> DocumentResolver:
    """`(파일 경로, 메타)` 목록으로 doc_id 기반 해소기를 만든다.

    각 파일의 `sha256(콘텐츠)`를 doc_id로 등록하고(적재와 동일 규약), 파일명도 별칭
    키로 등록해 사람이 식별자를 다루기 쉽게 한다. doc_id가 우선이며 파일명 별칭은
    충돌 시 먼저 등록된 항목을 유지한다. 미등록 키 조회는 `KeyError`.
    """
    table: dict[str, tuple[Path, DocumentMetadata]] = {}
    for path, meta in entries:
        if not path.is_file():
            raise FileNotFoundError(f"문서 파일을 찾을 수 없습니다: {path}")
        table[content_doc_id(path)] = (path, meta)
        table.setdefault(_key(path.name), (path, meta))  # 파일명 별칭(NFC, 데모 편의)

    def resolver(doc_id: str) -> tuple[Path, DocumentMetadata]:
        return table[_key(doc_id)]  # 미등록 → KeyError → 라우트가 404

    return resolver
