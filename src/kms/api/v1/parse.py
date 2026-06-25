"""문서 파싱 라우트 (POST /v1/parse/upload) — 임의 파일 1건을 IR로 환원해 미리보기 제공.

적재(`/v1/ingest/upload`)와 분리: 어떤 인덱스에도 영속되지 않는다. 사용자가 업로드한
파일은 임시 파일로만 추출에 쓰인 뒤 즉시 삭제(권한 매트릭스 밖, 디스크 잔존 방지).

응답: 추출 결과를 두 가지 형태로 반환한다.
  - `html`: 마크다운을 간단히 HTML로 렌더 + 이미지 마커를 data URL <img>로 치환.
  - `json`: IR을 사전 형태로 직렬화 (markdown·page_map·image_blobs 등 원본 데이터).
프런트는 HTML/JSON 탭으로 미리보기 + 다운로드 버튼을 제공한다.
"""

from __future__ import annotations

import logging
import re
import tempfile
from html import escape
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from pydantic import BaseModel, Field

from kms.adapters.ingestion.extract.pdf_digital import DoclingDigitalParser
from kms.adapters.ingestion.image_pdf import IMAGE_EXTS
from kms.adapters.ingestion.ir import MarkdownDoc, SlideDeck, Workbook
from kms.adapters.ingestion.jsonl import to_parse_jsonl
from kms.adapters.ingestion.structure import build_sections
from kms.adapters.storage import write_export
from kms.api.deps import get_current_user
from kms.api.v1._download import jsonl_response
from kms.config.settings import get_settings
from kms.domain.models import UserContext
from kms.services._page_render import _convert_to_pdf, render_page_previews

# Docling 파서는 무상태 — 프로세스 전역 단일 인스턴스 재사용 (모델 로드 비용 절감).
_DOCLING_PARSER = DoclingDigitalParser()
_DOCLING_SUPPORTED_EXTS = {
    ".pdf", ".docx", ".pptx", ".xlsx", ".xlsm", ".html", ".htm", ".txt", ".md",
    # 이미지도 docling OCR로 텍스트+좌표를 뽑는다(아래 IMAGE_EXTS와 일치).
    *IMAGE_EXTS,
}

# Office/reflowable 문서 — docling이 페이지·좌표를 못 주므로 LibreOffice로 PDF 변환 후
# 파싱한다(페이지 경계 + 요소 bbox 확보). PDF/이미지/HTML/TXT/MD는 변환 불요.
_OFFICE_CONVERT_EXTS = {
    ".docx", ".doc", ".odt", ".rtf",
    ".pptx", ".ppt", ".odp",
    ".xlsx", ".xls", ".xlsm", ".ods",
}

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["parse"])

# 업로드 한 파일 상한 (diff와 동일).
_MAX_UPLOAD_BYTES = 80 * 1024 * 1024  # 80 MiB

# image 마커: [IMAGE p=N sha=abc12345 xN] / [IMAGE sha=abc xN] / [IMAGE sha=unknown]
_IMAGE_MARKER_RE = re.compile(
    r"\[IMAGE(?:\s+p=(?P<page>\d+))?(?:\s+#\d+)?\s+sha=(?P<sha>[0-9a-f]+|unknown)(?:\s+x\d+)?\]"
)


class ParseResponse(BaseModel):
    """파싱 응답 — HTML + JSON 미리보기 + 페이지 PNG 프리뷰."""

    filename: str
    doc_type: str = Field(description="확장자 대문자(PDF/DOCX/...).")
    html: str = Field(description="미리보기용 HTML (이미지 data URL 인라인).")
    json_data: dict[str, Any] = Field(description="IR 원본 데이터 (다운로드용).")
    page_previews: dict[int, str] = Field(
        default_factory=dict,
        description="페이지 번호(1-base) → PNG data URL. 보조 — 미지원 시 빈 dict.",
    )
    page_map: list[tuple[int, int]] = Field(
        default_factory=list,
        description="MarkdownDoc의 (문자 오프셋, 페이지 번호) 쌍 목록 — HTML/JSON 코드 hover→페이지 매핑용.",
    )
    export_path: str | None = Field(
        default=None,
        description="공유 PVC(export_root)에 영속된 JSONL의 상대경로(예: parse/<name>.jsonl). 미영속/실패 시 None.",
    )
    export_error: str | None = Field(
        default=None,
        description="export 실패 사유(조용한 실패 금지). 핵심 미리보기는 그대로 반환된다.",
    )


@router.post("/parse/upload", response_model=ParseResponse)
async def parse_upload(
    file: UploadFile = File(...),
    user: UserContext = Depends(get_current_user),
) -> ParseResponse:
    """업로드한 파일을 Docling으로 파싱해 HTML/JSON 미리보기를 반환한다.

    Docling 단독 사용 — 다중 폴백 체인(Open-Parse/OCR/평문) 미사용. 미지원 포맷·
    Docling 실패 시 그대로 에러를 돌려준다(조용한 실패 금지).
    제한: 80 MiB. 미지원 포맷은 422.
    """
    user  # noqa: B018 — 인증 의존만 강제(미인증 401), 사용자 컨텍스트는 본 경로에서 미사용.
    filename = file.filename or "upload"
    suffix = _safe_suffix(filename)
    data = await _read_bounded(file)

    # 스캔/사진 이미지: docling OCR로 텍스트 + 요소별 좌표(bbox)를 뽑아 본문 분할과
    # "코드 hover → 원본 위치 강조"를 제공한다. OCR이 빈 결과면 프리뷰 전용으로 폴백.
    if suffix in IMAGE_EXTS:
        try:
            ir, page_previews = _docling_extract(suffix, data, render_previews=True)
        except HTTPException:
            return _parse_image(filename, suffix, data)
        if not isinstance(ir, MarkdownDoc) or not ir.markdown.strip():
            return _parse_image(filename, suffix, data)
        return _build_parse_response(ir, page_previews, filename, suffix)

    ir, page_previews = _docling_extract(suffix, data, render_previews=True)
    return _build_parse_response(ir, page_previews, filename, suffix)


def _build_parse_response(
    ir: MarkdownDoc | SlideDeck | Workbook,
    page_previews: dict[int, str],
    filename: str,
    suffix: str,
) -> ParseResponse:
    """IR + 프리뷰 → ParseResponse (디지털 문서·이미지 OCR 공용). 서버 JSONL 영속 포함."""
    doc_type = suffix.lstrip(".").upper() if suffix else ""
    json_data = _ir_to_dict(ir, filename, doc_type)
    html = _render_html(ir, filename)
    page_map = list(ir.page_map) if isinstance(ir, MarkdownDoc) else []
    # 서버측 영속(F5): 같은 IR을 JSONL로 export_root/parse/<name>.jsonl에 쓴다.
    # 다운로드(/parse/jsonl)와 별개 — export 실패는 미리보기 응답을 막지 않는다(graceful).
    export_path, export_error = _persist_parse_jsonl(ir, filename, page_map)
    return ParseResponse(
        filename=filename,
        doc_type=doc_type,
        html=html,
        json_data=json_data,
        page_previews=page_previews,
        page_map=page_map,
        export_path=export_path,
        export_error=export_error,
    )


@router.post("/parse/jsonl")
async def parse_jsonl(
    file: UploadFile = File(...),
    user: UserContext = Depends(get_current_user),
) -> Response:
    """업로드 파일을 파싱해 JSONL(섹션당 1줄)로 다운로드한다(application/x-ndjson).

    미리보기용 `/parse/upload`(html/json)와 별개 — 같은 IR을 줄단위로 직렬화해 첨부로
    내려준다. 스캔 이미지는 텍스트 레이어가 없어 대상이 아니다(필드 추출 사용 → 422).
    """
    user  # noqa: B018 — 인증 의존만 강제(미인증 401), 사용자 컨텍스트는 본 경로에서 미사용.
    filename = file.filename or "upload"
    suffix = _safe_suffix(filename)
    data = await _read_bounded(file)

    ir, _ = _docling_extract(suffix, data, render_previews=False)
    # 이미지 OCR이 빈 결과면 변환할 본문이 없다 — 명시 422(조용한 빈 파일 금지).
    if suffix in IMAGE_EXTS and (
        not isinstance(ir, MarkdownDoc) or not ir.markdown.strip()
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="이미지에서 텍스트를 추출하지 못했습니다(OCR 빈 결과) — 필드 추출을 쓰세요.",
        )
    page_map = list(ir.page_map) if isinstance(ir, MarkdownDoc) else []
    body = to_parse_jsonl(ir, filename, page_map)
    return jsonl_response(body, filename)


def _persist_parse_jsonl(
    ir: MarkdownDoc | SlideDeck | Workbook,
    filename: str,
    page_map: list[tuple[int, int]],
) -> tuple[str | None, str | None]:
    """파싱 JSONL을 export_root/parse/<stem>.jsonl에 영속한다(공유 PVC, F5).

    (상대경로, 에러) 튜플을 반환한다 — 성공이면 (상대경로, None), 실패면 (None, 사유).
    export 실패(PVC 미마운트·권한 등)는 미리보기 응답을 막지 않되 조용히 삼키지 않는다
    (warning 로깅 + 응답 export_error 노출).
    """
    export_root = get_settings().export_root
    body = to_parse_jsonl(ir, filename, page_map)
    out_name = f"{Path(filename).stem or 'document'}.jsonl"
    try:
        write_export("parse", out_name, body, export_root)
    except OSError as exc:
        logger.warning("파싱 JSONL export 실패 (file=%s): %s", filename, exc)
        return None, f"export 실패: {type(exc).__name__}"
    return f"parse/{out_name}", None


def _docling_extract(
    suffix: str, data: bytes, *, render_previews: bool
) -> tuple[MarkdownDoc | SlideDeck | Workbook, dict[int, str]]:
    """업로드 바이트를 Docling으로 파싱해 (IR, 페이지 프리뷰)를 반환한다.

    `/parse/upload`와 `/parse/jsonl`가 공유한다(조용한 폴백 금지 — 미지원/미설치/실패는
    명시 에러). `render_previews=False`면 프리뷰 렌더를 건너뛴다(jsonl 경로엔 불필요).
    """
    # 지원 확장자 사전 검증 — Docling이 처리 불가한 형식이면 422.
    if suffix not in _DOCLING_SUPPORTED_EXTS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Docling 미지원 형식: {suffix}",
        )

    # Docling 의존 미설치 환경에서는 일관 에러로 노출 (조용한 폴백 금지).
    if not _DOCLING_PARSER.is_available():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Docling 미설치 — `uv pip install docling`로 의존 설치 필요",
        )

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)

    # Office 문서(DOCX 등)는 reflowable라 docling이 페이지·좌표를 못 준다(page_map=[(0,1)],
    # bbox 없음 → 페이지 구분·박스 불가). LibreOffice로 **PDF 변환 후** docling에 넘기면
    # 실제 페이지 경계 + 요소별 bbox를 얻고, 프리뷰도 같은 PDF에서 렌더해 일관된다.
    # soffice 부재 시 변환 None → 원본으로 폴백(기존 동작, 페이지 단위).
    converted_pdf: Path | None = None
    parse_path = tmp_path
    if suffix in _OFFICE_CONVERT_EXTS:
        converted_pdf = _convert_to_pdf(tmp_path)
        if converted_pdf is not None:
            parse_path = converted_pdf

    try:
        try:
            ir = _DOCLING_PARSER.parse(parse_path)
        except Exception as exc:
            logger.warning("Docling 파싱 실패: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Docling 파싱 실패: {type(exc).__name__}",
            ) from exc
        if ir is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Docling이 본문을 추출하지 못했습니다(빈 결과).",
            )
        # 페이지 PNG 프리뷰 — 변환된 PDF(있으면)에서 렌더해 page_map/blocks와 페이지가 일치.
        page_previews = render_page_previews(parse_path) if render_previews else {}
    finally:
        tmp_path.unlink(missing_ok=True)
        if converted_pdf is not None:
            converted_pdf.unlink(missing_ok=True)
    return ir, page_previews


def _parse_image(filename: str, suffix: str, data: bytes) -> ParseResponse:
    """스캔 이미지 파싱 — 페이지 프리뷰만 렌더하고 본문은 추출(VLM)에 위임한다.

    이미지는 텍스트 레이어가 없어 Docling 대상이 아니다. 좌측 프리뷰로 원본을 보여주고
    HTML/JSON에는 안내를 담아, 같은 화면 우하단 필드 추출(손글씨/스캔 모드)로 값을 뽑게 한다.
    """
    note = "(스캔 이미지 — 텍스트 레이어 없음. 우측 필드 추출(손글씨/스캔 모드)으로 값을 뽑으세요.)"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)
    try:
        page_previews = render_page_previews(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)
    doc_type = suffix.lstrip(".").upper() if suffix else "IMAGE"
    return ParseResponse(
        filename=filename,
        doc_type=doc_type,
        html=f"<p>{escape(note)}</p>",
        json_data={"filename": filename, "type": "Image", "note": note},
        page_previews=page_previews,
        page_map=[],
    )


def _safe_suffix(name: str | None) -> str:
    """원본 파일명에서 확장자만 보존(소문자). 임시 경로 주입 차단."""
    if not name:
        return ""
    suffix = Path(name).suffix.lower()
    return suffix if all(c.isalnum() or c == "." for c in suffix) else ""


async def _read_bounded(upload: UploadFile) -> bytes:
    """업로드 바이트를 한도 내에서만 읽는다. 초과 시 413."""
    data = await upload.read(_MAX_UPLOAD_BYTES + 1)
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"파일이 너무 큽니다(상한 {_MAX_UPLOAD_BYTES // (1024 * 1024)} MiB).",
        )
    return data


def _blocks_to_sections(blocks: list[Any]) -> list[dict[str, Any]]:
    """Docling 블록(DocBlock) → JSON 섹션 레코드(문단/표 단위 + 정규화 bbox).

    `bbox`는 [x, y, w, h](페이지 대비 0~1, top-left)로, 프런트가 페이지 프리뷰 위에
    그대로 % 오버레이해 실제 문단/표 위치를 강조한다. 좌표 미가용이면 bbox=None.
    """
    return [
        {
            "index": index,
            "kind": b.kind,
            "page": b.page,
            "bbox": list(b.bbox) if b.bbox is not None else None,
            "text": b.text,
        }
        for index, b in enumerate(blocks)
    ]


def _segment_markdown(
    markdown: str, page_map: list[tuple[int, int]]
) -> list[dict[str, Any]]:
    """본문을 단락/섹션 단위 레코드로 분할한다(각 레코드에 페이지 태그).

    1순위: heading 경계 섹션(`build_sections`). heading이 없어 1덩어리로 떨어지면
    빈 줄(`\\n\\n`) 기준 **단락 블록**으로 폴백해 항상 잘게 구분되게 한다.
    """
    sections = build_sections(markdown, page_map)
    if len(sections) > 1:
        return [
            {"index": i, "level": s.level, "title": s.title, "page": s.page, "text": s.text}
            for i, s in enumerate(sections)
        ]

    # heading 없는 평문 → 단락(빈 줄) 단위로 분할, 오프셋으로 페이지 태그.
    blocks: list[dict[str, Any]] = []
    offset = 0
    index = 0
    for raw_block in re.split(r"\n[ \t]*\n", markdown):
        block = raw_block.strip()
        start = markdown.find(raw_block, offset)
        if start >= 0:
            offset = start + len(raw_block)
        if block:
            blocks.append(
                {
                    "index": index,
                    "level": 0,
                    "title": None,
                    "page": _page_for_offset(page_map, start if start >= 0 else offset),
                    "text": block,
                }
            )
            index += 1
    if blocks:
        return blocks
    # 완전 빈 문서 방지 — 단일 레코드라도 보존.
    return [{"index": 0, "level": 0, "title": None, "page": 1, "text": markdown.strip()}]


def _page_for_offset(page_map: list[tuple[int, int]], offset: int) -> int | None:
    """문자 오프셋이 속한 페이지(page_map의 마지막 start <= offset). 비면 None."""
    page: int | None = None
    for start, number in page_map:
        if offset >= start:
            page = number
        else:
            break
    return page


def _ir_to_dict(
    ir: MarkdownDoc | SlideDeck | Workbook, filename: str, doc_type: str = ""
) -> dict[str, Any]:
    """IR(중간표현)을 사전 형태로 직렬화 — JSON 다운로드용.

    `type`은 **원본 문서 형식**(PDF/DOCX/PPTX/XLSX — 확장자 기반)을 담는다. IR 내부
    표현 종류(MarkdownDoc/SlideDeck/Workbook)는 `ir_kind`로 따로 노출한다(혼동 방지 —
    이전엔 type에 IR 클래스명이 들어가 PDF가 'MarkdownDoc'으로 보이는 문제가 있었다).
    """
    if isinstance(ir, MarkdownDoc):
        # 본문을 통짜 markdown 대신 **단락/표 단위**로 분할해 내려준다. Docling 블록(문단·표
        # 단위 + 페이지 내 정규화 bbox)이 있으면 그걸 쓰고(코드 hover→원본 정밀 위치 강조),
        # 없으면 markdown을 heading/단락으로 분할해 폴백(페이지 태그까지).
        return {
            "filename": filename,
            "type": doc_type or "unknown",
            "ir_kind": "MarkdownDoc",
            "sections": _blocks_to_sections(ir.blocks)
            if ir.blocks
            else _segment_markdown(ir.markdown, ir.page_map),
            "page_map": ir.page_map,
            "image_blobs": dict(ir.image_blobs),
        }
    if isinstance(ir, SlideDeck):
        return {
            "filename": filename,
            "type": doc_type or "unknown",
            "ir_kind": "SlideDeck",
            "slides": [
                {"number": s.number, "text": s.body} for s in ir.slides
            ],
            "image_blobs": dict(ir.image_blobs),
        }
    if isinstance(ir, Workbook):
        return {
            "filename": filename,
            "type": doc_type or "unknown",
            "ir_kind": "Workbook",
            "tables": [
                {"title": t.title, "columns": t.columns, "rows": t.rows}
                for t in ir.tables
            ],
            "image_blobs": dict(ir.image_blobs),
        }
    return {"filename": filename, "type": doc_type or "unknown", "ir_kind": "unknown"}


def _render_html(ir: MarkdownDoc | SlideDeck | Workbook, filename: str) -> str:
    """IR을 단일 HTML 문서로 렌더 — 미리보기 + 다운로드 둘 다 사용.

    파서가 충실도 높은 HTML(표·구조 보존, 예: docling export_to_html)을 줬으면 그걸 쓰고,
    없으면 markdown→HTML 폴백으로 렌더한다.
    """
    if isinstance(ir, MarkdownDoc) and ir.html.strip():
        return ir.html
    text = _ir_to_text(ir)
    body = _markdown_to_html(text, _image_blob_map(ir))
    title = escape(filename)
    return _HTML_TEMPLATE.format(title=title, body=body)


def _ir_to_text(ir: MarkdownDoc | SlideDeck | Workbook) -> str:
    """IR을 단일 마크다운/평문 문자열로 평탄화."""
    if isinstance(ir, MarkdownDoc):
        return ir.markdown
    if isinstance(ir, SlideDeck):
        parts = []
        for slide in ir.slides:
            parts.append(f"## 슬라이드 {slide.number}\n\n{slide.body}\n")
        return "\n".join(parts)
    if isinstance(ir, Workbook):
        parts = []
        for table in ir.tables:
            parts.append(f"## {table.title}\n")
            if table.columns:
                parts.append("| " + " | ".join(table.columns) + " |")
                parts.append("|" + "|".join(["---"] * len(table.columns)) + "|")
                for row in table.rows:
                    cells = [str(row.get(c, "")) for c in table.columns]
                    parts.append("| " + " | ".join(cells) + " |")
            parts.append("")
        return "\n".join(parts)
    return ""


def _image_blob_map(ir: MarkdownDoc | SlideDeck | Workbook) -> dict[str, str]:
    return dict(getattr(ir, "image_blobs", {}))


def _markdown_to_html(text: str, image_blobs: dict[str, str]) -> str:
    """간단한 마크다운 → HTML 변환. 헤더·표·이미지 마커·코드블록 정도만 처리.

    외부 의존성 없는 최소 변환기. 안전을 위해 일반 텍스트는 escape, 표·헤딩만 태그화.
    """
    lines = text.split("\n")
    out: list[str] = []
    in_table = False
    table_rows: list[list[str]] = []

    def flush_table() -> None:
        nonlocal in_table, table_rows
        if not in_table:
            return
        out.append('<table class="parsed-table">')
        for i, row in enumerate(table_rows):
            tag = "th" if i == 0 else "td"
            out.append("<tr>")
            for cell in row:
                out.append(f"<{tag}>{escape(cell)}</{tag}>")
            out.append("</tr>")
        out.append("</table>")
        in_table = False
        table_rows = []

    for raw in lines:
        line = raw.rstrip()
        # 이미지 마커 → <img>
        if m := _IMAGE_MARKER_RE.fullmatch(line.strip()):
            flush_table()
            sha = m.group("sha")
            data_url = image_blobs.get(sha)
            if data_url:
                out.append(
                    f'<div class="parsed-image"><img src="{escape(data_url)}" alt="image {sha}" /></div>'
                )
            else:
                out.append(f'<div class="parsed-image-fallback">[이미지 sha={escape(sha)}]</div>')
            continue
        # 표 행
        if line.startswith("|") and line.endswith("|"):
            in_table = True
            cells = [c.strip() for c in line[1:-1].split("|")]
            # 마크다운 표 구분선(---) 스킵
            if all(set(c.replace(":", "")) <= {"-"} for c in cells):
                continue
            table_rows.append(cells)
            continue
        flush_table()
        # 헤더
        if line.startswith("### "):
            out.append(f"<h3>{escape(line[4:])}</h3>")
        elif line.startswith("## "):
            out.append(f"<h2>{escape(line[3:])}</h2>")
        elif line.startswith("# "):
            out.append(f"<h1>{escape(line[2:])}</h1>")
        elif line.startswith("[TABLE "):
            # 표 시작 캡션(다음 행들이 | 셀 | 형식)은 본문에 노출하지 않음 — 그냥 스킵.
            continue
        elif line.strip() == "":
            out.append("")
        else:
            out.append(f"<p>{escape(line)}</p>")
    flush_table()
    return "\n".join(out)


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8" />
<title>{title}</title>
<style>
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Apple SD Gothic Neo",
      "Malgun Gothic", "Segoe UI", Roboto, sans-serif;
    line-height: 1.65; color: #0f172a; max-width: 900px;
    margin: 24px auto; padding: 0 16px;
  }}
  h1, h2, h3 {{ color: #1d4ed8; margin-top: 1.5em; }}
  h1 {{ font-size: 22px; }}
  h2 {{ font-size: 18px; }}
  h3 {{ font-size: 16px; }}
  p {{ margin: 0.4em 0; }}
  .parsed-table {{
    border-collapse: collapse; margin: 1em 0; font-size: 13px;
  }}
  .parsed-table th, .parsed-table td {{
    border: 1px solid #e2e8f0; padding: 6px 10px; text-align: left;
  }}
  .parsed-table th {{ background: #f1f5f9; font-weight: 700; }}
  .parsed-image img {{
    max-width: 100%; max-height: 400px; border: 1px solid #e2e8f0;
    border-radius: 6px; margin: 0.5em 0;
  }}
  .parsed-image-fallback {{
    color: #64748b; font-size: 12px; font-style: italic;
  }}
</style>
</head>
<body>
<h1>{title}</h1>
{body}
</body>
</html>
"""
