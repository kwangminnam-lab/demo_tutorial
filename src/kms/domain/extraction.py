"""문서 필드추출(IDP) 도메인 모델 — 외부 의존 없음(의존성 방향 최내곽).

금융 문서에서 사용자가 지정한 스키마(필드 목록)대로 값을 뽑고, 근거 위치를
바운딩박스(B-Box)로 되짚는 기능(phase 1, ADR-024)의 핵심 타입이다.

흐름: 문서 → `LineProvider`가 `TextLine`(텍스트+좌표) 목록 추출 →
`FieldExtractor`가 `ExtractionSchema`대로 LLM 추출 → `ExtractedField`(값+근거 라인
+bbox) → `ExtractionResult`로 영속. 좌표는 LLM이 아니라 OCR/추출기(라인)에서 온다
(LLM은 텍스트만 보고 `evidence_line_ids`만 반환 → 라인의 bbox 역참조).

FastAPI·DB·LLM을 import하지 않는다.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

# (x0, y0, x1, y1) — PDF 좌표계(point, 72dpi) 축 정렬 사각형. pymupdf Rect와 동일 규약.
BBox = tuple[float, float, float, float]

# 추출 값의 출처 — 인쇄체(print) vs 손글씨(handwriting). 손글씨는 오인식 위험이 커
# needs_review를 강제한다(phase 1은 print만, 손글씨 tier는 후속 VLM).
FieldSource = str  # "print" | "handwriting"


class TextLine(BaseModel):
    """문서 한 줄 — 텍스트 + 페이지 + bbox. LLM 추출의 grounding 단위.

    `line_id`는 문서 전체에서 0부터 매기는 일련번호다(페이지 경계 무관). LLM에
    `[line_id] text`로 번호 매겨 투입하고, LLM이 돌려준 `evidence_line_ids`를 이
    번호로 역참조해 bbox를 확정한다.
    """

    line_id: int
    text: str
    page: int  # 1-base
    bbox: BBox


class SchemaField(BaseModel):
    """추출할 필드 정의(스키마 한 항목). 사용자 지정 또는 자동 생성."""

    key: str = Field(min_length=1, description="필드명 (예: 계약일, 계약금액)")
    type: str = Field(default="String", description="값 타입 힌트: String|int|float|boolean|date")
    description: str | None = Field(default=None, description="추출 지침(LLM 힌트)")
    required: bool = False
    # 한 필드가 여러 값을 가질 수 있는가(예: 품목 리스트). 메타데이터로 저장 — 추출 로직
    # 연동은 후속(현재는 단일 값 추출). model_dump로 JSONB에 함께 영속된다.
    multi: bool = False


class ExtractionSchema(BaseModel):
    """재사용 가능한 추출 스키마(필드 목록 템플릿). 문서종류별로 1개 만들어 재사용."""

    id: int | None = None
    name: str = Field(min_length=1)
    doc_type: str | None = Field(default=None, description="대상 문서종류(예: 계약서)")
    fields: list[SchemaField] = Field(min_length=1)
    auto_generated: bool = False
    created_by: str | None = None
    created_at: datetime | None = None


class ExtractedField(BaseModel):
    """추출 결과 한 필드 — 값 + 근거 라인 + bbox + 신뢰도.

    `bbox`/`page`는 `evidence_line_ids`가 가리키는 라인들에서 역산한다(같은 페이지
    근거들의 합집합 사각형). 근거를 못 찾으면 None(값만 있고 위치 미확정).
    """

    key: str
    value: str | None = None
    page: int | None = None
    bbox: BBox | None = None
    evidence_line_ids: list[int] = Field(default_factory=list)
    source: FieldSource = "print"
    confidence: float | None = None
    # 손글씨거나 신뢰도가 임계 미만이면 True — 자동 확정 금지, 사람 확인 필요.
    needs_review: bool = False


class ExtractionResult(BaseModel):
    """문서 1건 × 스키마 1개의 추출 결과. doc_id로 원문과 연결."""

    id: int | None = None
    doc_id: str
    schema_id: int | None = None
    fields: list[ExtractedField] = Field(default_factory=list)
    created_by: str | None = None
    created_at: datetime | None = None
