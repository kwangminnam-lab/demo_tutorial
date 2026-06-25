"""다운로드 응답 헬퍼 — JSONL 본문을 attachment로 내려보낸다(api 레이어).

직렬화는 adapters(`jsonl.py`)가, HTTP 포장만 여기서 한다. 파일명에 한글이 있어도
헤더가 깨지지 않도록 RFC 5987 `filename*`(UTF-8 percent-encoding)을 함께 싣는다.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi import Response

#: NDJSON(줄단위 JSON) — 줄마다 독립 JSON 객체. text/plain보다 의미가 명확.
_NDJSON_MEDIA_TYPE = "application/x-ndjson"


def jsonl_response(body: str, filename: str) -> Response:
    """JSONL 문자열을 `.jsonl` 첨부 다운로드로 반환한다.

    `filename`은 원본 파일명(확장자 무관) — `.jsonl`로 치환해 내려준다. 비ASCII는
    ASCII 헤더에서 깨지므로 ASCII fallback + `filename*`(UTF-8) 둘 다 싣는다.
    """
    stem = Path(filename).stem or "document"
    download_name = f"{stem}.jsonl"
    ascii_name = download_name.encode("ascii", "ignore").decode() or "document.jsonl"
    disposition = (
        f'attachment; filename="{ascii_name}"; '
        f"filename*=UTF-8''{quote(download_name)}"
    )
    return Response(
        content=body,
        media_type=_NDJSON_MEDIA_TYPE,
        headers={"Content-Disposition": disposition},
    )
