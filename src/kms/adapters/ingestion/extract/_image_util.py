"""이미지 blob → data URL 변환 유틸 (문서 비교 UI에서 라벨 대신 실제 그림 렌더용).

매직 바이트로 컨테이너를 추정해 적절한 MIME을 부여한다 — 확장자/메타가 없는
embedded 이미지(예: PDF xref blob)도 다룬다.
"""

from __future__ import annotations

import base64
import hashlib


def detect_content_type(blob: bytes) -> str:
    """이미지 바이트 매직 시그니처로 MIME 추정. 미상이면 PNG로 폴백.

    PNG·JPEG·GIF·BMP·WebP·TIFF 정도만 지원 — 대부분의 사무 문서 그림을 다룬다.
    """
    if not blob:
        return "image/png"
    if blob.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if blob.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if blob.startswith(b"GIF87a") or blob.startswith(b"GIF89a"):
        return "image/gif"
    if blob.startswith(b"BM"):
        return "image/bmp"
    if blob.startswith(b"RIFF") and len(blob) >= 12 and blob[8:12] == b"WEBP":
        return "image/webp"
    if blob.startswith(b"II*\x00") or blob.startswith(b"MM\x00*"):
        return "image/tiff"
    return "image/png"


def to_data_url(blob: bytes, content_type: str | None = None) -> str:
    """`data:image/...;base64,...` data URL 생성. content_type 미지정이면 추정."""
    mime = content_type or detect_content_type(blob)
    return f"data:{mime};base64,{base64.b64encode(blob).decode('ascii')}"


def sha8(blob: bytes) -> str:
    """SHA-256 hex digest의 앞 8자 — 마커·맵 키로 쓰는 짧은 해시."""
    return hashlib.sha256(blob).hexdigest()[:8]
