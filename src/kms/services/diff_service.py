"""문서 비교 유스케이스 — 파싱 + 라인/단어 diff + 페이지 프리뷰 (ADR-008).

흐름(ARCHITECTURE 문서 비교):

1. 두 문서 모두 요청 사용자 권한에 가시일 때만 비교한다(`is_visible_to`).
   한 쪽이라도 권한 밖이면 `AuthorizationError`로 거부한다 — 권한 밖 내용이
   diff 결과·페이지 프리뷰로 새지 않게 한다(CONVENTIONS 권한 인지).
2. 형식별 추출기(파서)로 두 파일을 평문화한다(IR → 텍스트). 코어 비교 로직은
   바이너리를 모른다 — 순수 핵심 `diff_texts`는 문자열만 받는다.
3. `difflib.SequenceMatcher`로 라인 시퀀스를 비교해 equal/add/delete/change를
   산출하고, `change` 라인 쌍은 단어 단위 diff로 바뀐 단어만 표시한다.
4. **페이지 단위 이미지 프리뷰**를 함께 렌더한다(PDF 등 지원 포맷). 사용자가
   원본 페이지를 그대로 보면서 diff를 검토할 수 있게 한다. 프리뷰는 보조
   기능이라 미지원 포맷·렌더러 부재 시 빈 dict로 graceful degrade.

순수 핵심(`diff_texts`)과 부수효과 경계(`diff_documents` — 파일 추출 + 페이지
렌더)를 나눠, diff 로직을 바이너리 없이 결정론적으로 테스트할 수 있게 한다
(ADR-009). 마커(`[IMAGE sha=...]`/`[TABLE r=N c=N]`/`| 셀 |`)는 추출기 출력
그대로 보존되며 비교 로직은 마커를 일반 라인으로 취급한다(diff 정확성 유지).
"""

from __future__ import annotations

from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

from kms.adapters.ingestion.extract import get_extractor
from kms.adapters.ingestion.ir import IR, MarkdownDoc, SlideDeck, Workbook
from kms.domain.errors import AuthorizationError
from kms.domain.models import (
    DiffOp,
    DiffResult,
    DocumentMetadata,
    UserContext,
    WordSpan,
    is_visible_to,
)
from kms.services._page_render import (
    HighlightKind,
    render_page_previews_highlighted_cached,
)


class DiffService:
    """두 문서의 텍스트 차이를 라인·단어 단위로 감지하는 비교 유스케이스."""

    @staticmethod
    def diff_texts(a: str, b: str) -> DiffResult:
        """두 평문의 라인 단위 차이를 계산한다(순수 함수 — 부수효과 없음).

        `SequenceMatcher`(라인 시퀀스)의 블록을 라인 연산으로 옮긴다:
        equal→`equal`, insert→`add`, delete→`delete`, replace→`change`.
        `replace` 블록은 라인 쌍을 묶어 `change`로 보고하며, 남는 라인은
        각각 `add`/`delete`로 떨군다(라인 수가 다른 교체 처리).
        """
        left_lines = a.splitlines()
        right_lines = b.splitlines()
        matcher = SequenceMatcher(a=left_lines, b=right_lines, autojunk=False)

        ops: list[DiffOp] = []
        added = deleted = changed = 0
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                for line in left_lines[i1:i2]:
                    ops.append(DiffOp(op="equal", left=line, right=line))
            elif tag == "delete":
                for line in left_lines[i1:i2]:
                    ops.append(DiffOp(op="delete", left=line, right=None))
                    deleted += 1
            elif tag == "insert":
                for line in right_lines[j1:j2]:
                    ops.append(DiffOp(op="add", left=None, right=line))
                    added += 1
            else:  # replace — 라인 쌍은 change, 남는 라인은 add/delete.
                block_left = left_lines[i1:i2]
                block_right = right_lines[j1:j2]
                paired = min(len(block_left), len(block_right))
                for left, right in zip(
                    block_left[:paired], block_right[:paired], strict=True
                ):
                    left_words, right_words = _word_spans(left, right)
                    ops.append(
                        DiffOp(
                            op="change",
                            left=left,
                            right=right,
                            left_words=left_words,
                            right_words=right_words,
                        )
                    )
                    changed += 1
                for line in block_left[paired:]:
                    ops.append(DiffOp(op="delete", left=line, right=None))
                    deleted += 1
                for line in block_right[paired:]:
                    ops.append(DiffOp(op="add", left=None, right=line))
                    added += 1

        return DiffResult(ops=ops, added=added, deleted=deleted, changed=changed)

    def diff_documents(
        self,
        path_a: Path,
        path_b: Path,
        meta_a: DocumentMetadata,
        meta_b: DocumentMetadata,
        user: UserContext,
    ) -> DiffResult:
        """두 문서 파일을 평문화해 비교하고 페이지 프리뷰까지 함께 반환한다.

        한 쪽이라도 사용자 권한 밖이면 `AuthorizationError`(접근 거부)를 던진다 —
        그 경우 어떤 결과도(텍스트·이미지·페이지 프리뷰) 생성하지 않는다.
        """
        if not is_visible_to(meta_a, user) or not is_visible_to(meta_b, user):
            raise AuthorizationError("접근 거부: 권한 밖 문서는 비교할 수 없습니다.")
        mtime_a = path_a.stat().st_mtime_ns
        mtime_b = path_b.stat().st_mtime_ns
        text_a, blobs_a = _extract_with_blobs_cached(str(path_a), mtime_a)
        text_b, blobs_b = _extract_with_blobs_cached(str(path_b), mtime_b)
        result = self.diff_texts(text_a, text_b)
        result.image_blobs = {**blobs_a, **blobs_b}
        # 페이지 프리뷰 — 변경된 텍스트로 형광 색칠 (add/delete/change).
        # 양쪽 다른 hl 집합 → 변환·렌더 두 번. 캐시 키에 hl tuple 포함이라 재diff 즉시.
        left_hl, right_hl = _collect_highlights(result)
        result.page_previews_a = render_page_previews_highlighted_cached(
            str(path_a), mtime_a, left_hl
        )
        result.page_previews_b = render_page_previews_highlighted_cached(
            str(path_b), mtime_b, right_hl
        )
        return result


#: Highlight tuple: (line_text, kind, changed_words_tuple)
#: - kind: "add" | "delete" | "change" (`_page_render.HighlightKind` Literal 재사용 —
#:   렌더 경계로 그대로 넘어가므로 동일 타입이어야 타입 체크가 맞는다)
#: - changed_words_tuple: kind="change"일 때만 채움. 라인 매치 후 그 안에서
#:   변경 단어 위치만 정확히 색칠하기 위한 컨텍스트.
HighlightEntry = tuple[str, HighlightKind, tuple[str, ...]]


def _collect_highlights(
    result: DiffResult,
) -> tuple[tuple[HighlightEntry, ...], tuple[HighlightEntry, ...]]:
    """DiffResult를 좌/우 페이지 하이라이트 entry로 변환.

    각 entry는 (라인 전체, kind, 변경 단어 튜플):
      - delete → 왼쪽 (라인, "delete", ())  — 라인 전체 색칠
      - add    → 오른쪽 (라인, "add", ())   — 라인 전체 색칠
      - change → 양쪽 (라인, "change", (변경 단어들,)) — **라인 매치 후 그 안의
                 변경 단어 위치만** 색칠 (공통 부분은 보존). 변경 단어 정보가
                 없으면 폴백으로 라인 전체.

    반환은 tuple-of-tuples(hashable) — 캐시 키로 그대로 쓰임. 중복은 dedupe.
    """
    left: list[HighlightEntry] = []
    right: list[HighlightEntry] = []
    for op in result.ops:
        if op.op == "delete" and op.left:
            left.append((op.left, "delete", ()))
        elif op.op == "add" and op.right:
            right.append((op.right, "add", ()))
        elif op.op == "change":
            if op.left:
                left.append((op.left, "change", _changed_words(op.left_words)))
            if op.right:
                right.append((op.right, "change", _changed_words(op.right_words)))
    return _dedupe(left), _dedupe(right)


def _changed_words(words: list[WordSpan] | None) -> tuple[str, ...]:
    """words에서 changed=True인 단어만 추출 (2자 이상). 정보 없으면 빈 튜플."""
    if not words:
        return ()
    return tuple(w.text for w in words if w.changed and len(w.text.strip()) >= 2)


def _dedupe(items: list[HighlightEntry]) -> tuple[HighlightEntry, ...]:
    """삽입 순서 보존 dedupe — 같은 entry를 한 번만 검색하게."""
    seen: set[HighlightEntry] = set()
    out: list[HighlightEntry] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return tuple(out)


def _word_spans(left: str, right: str) -> tuple[list[WordSpan], list[WordSpan]]:
    """변경 라인 쌍을 단어 단위로 비교해 좌/우 `WordSpan` 목록을 만든다.

    공백으로 토큰을 나눠 `SequenceMatcher`로 비교한다. 양쪽 공통 단어는
    `changed=False`, 한쪽에만 있거나 교체된 단어는 `changed=True`로 표시한다.
    """
    left_tokens = left.split()
    right_tokens = right.split()
    matcher = SequenceMatcher(a=left_tokens, b=right_tokens, autojunk=False)

    left_spans: list[WordSpan] = []
    right_spans: list[WordSpan] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        changed = tag != "equal"
        for token in left_tokens[i1:i2]:
            left_spans.append(WordSpan(text=token, changed=changed))
        for token in right_tokens[j1:j2]:
            right_spans.append(WordSpan(text=token, changed=changed))
    return left_spans, right_spans


def _extract_with_blobs(path: Path) -> tuple[str, dict[str, str]]:
    """형식별 추출기로 IR을 뽑아 (평문, image_blobs) 튜플로 반환한다.

    image_blobs는 sha8 → `data:image/...;base64,...` data URL. 본문에 박힌
    `[IMAGE sha=...]` 마커가 UI에서 실제 그림으로 렌더될 때 src로 쓰인다.
    """
    extractor = get_extractor(path)
    ir = extractor.extract(path)
    return _ir_to_text(ir), getattr(ir, "image_blobs", {})


@lru_cache(maxsize=64)
def _extract_with_blobs_cached(
    path_str: str, mtime_ns: int
) -> tuple[str, dict[str, str]]:
    """`_extract_with_blobs`의 캐시 래퍼 — 같은 (경로,mtime)이면 재추출 생략."""
    return _extract_with_blobs(Path(path_str))


def _ir_to_text(ir: IR) -> str:
    """추출 IR(`MarkdownDoc`/`SlideDeck`/`Workbook`)을 비교용 평문으로 평탄화한다."""
    if isinstance(ir, MarkdownDoc):
        return ir.markdown
    if isinstance(ir, SlideDeck):
        parts: list[str] = []
        for slide in ir.slides:
            if slide.title is not None:
                parts.append(slide.title)
            parts.append(slide.body)
            if slide.notes is not None:
                parts.append(slide.notes)
        return "\n".join(parts)
    if isinstance(ir, Workbook):
        parts = []
        for table in ir.tables:
            parts.append(table.title)
            parts.append("\t".join(table.columns))
            for row in table.rows:
                parts.append("\t".join(str(row.get(col, "")) for col in table.columns))
        return "\n".join(parts)
    # IR 유니온에 없는 타입 — 조용히 빈 문자열 반환하지 않고 명시적으로 실패.
    raise TypeError(f"지원하지 않는 IR 타입: {type(ir).__name__}")
