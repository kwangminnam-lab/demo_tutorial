"""DiffService 단위 테스트 — 라인 단위 분류 + 단어 강조 + 권한 인지.

순수 핵심 `diff_texts`는 문자열만 받아 바이너리 없이 검증한다. `diff_documents`는
.txt 파일(TxtExtractor → 평문)로 추출 경로와 권한 거부를 확인한다.

검증 핵심(AC):
- 추가/삭제/변경 라인을 정확히 분류하고 added/deleted/changed를 센다.
- 변경 라인에서 바뀐 단어만 WordSpan(changed=True).
- diff_documents: 한 문서라도 권한 밖이면 접근 거부(AuthorizationError).
"""

from pathlib import Path

import pytest

from kms.domain.access import AccessLevel
from kms.domain.errors import AuthorizationError
from kms.domain.models import DocumentMetadata, SourceType, UserContext
from kms.services.diff_service import DiffService

USER = UserContext(user_id="u1", department="영업부", access_level=AccessLevel.임직원)


def _meta(access: AccessLevel = AccessLevel.임직원) -> DocumentMetadata:
    return DocumentMetadata(source=SourceType.ONEDRIVE, access=access)


def test_diff_texts_classifies_added_deleted_changed_lines() -> None:
    # Arrange: 동일 라인(머리말·꼬리표)을 앵커로 둬 삭제/변경/추가를 분리한다.
    a = "머리말\n삭제될 행\n꼬리표\n매출 100억 달성"
    b = "머리말\n꼬리표\n매출 200억 달성\n추가된 행"

    # Act
    result = DiffService.diff_texts(a, b)

    # Assert: 카운트 — 삭제 1, 변경 1, 추가 1.
    assert result.deleted == 1
    assert result.changed == 1
    assert result.added == 1
    ops_by_kind = {op.op for op in result.ops}
    assert {"equal", "change", "delete", "add"} <= ops_by_kind


def test_diff_texts_marks_only_changed_words() -> None:
    # Arrange: 한 단어만 다른 한 줄.
    a = "매출 100억 달성"
    b = "매출 200억 달성"

    # Act
    result = DiffService.diff_texts(a, b)

    # Assert: change 연산 하나, 바뀐 단어만 changed=True.
    change_ops = [op for op in result.ops if op.op == "change"]
    assert len(change_ops) == 1
    op = change_ops[0]
    assert op.left_words is not None
    assert op.right_words is not None
    left_changed = {span.text for span in op.left_words if span.changed}
    right_changed = {span.text for span in op.right_words if span.changed}
    assert left_changed == {"100억"}
    assert right_changed == {"200억"}
    # 안 바뀐 단어는 changed=False.
    left_unchanged = {span.text for span in op.left_words if not span.changed}
    assert left_unchanged == {"매출", "달성"}


def test_diff_texts_identical_has_no_diff() -> None:
    text = "한 줄\n두 줄"
    result = DiffService.diff_texts(text, text)
    assert result.added == result.deleted == result.changed == 0
    assert all(op.op == "equal" for op in result.ops)


def test_diff_documents_extracts_and_diffs_visible_files(tmp_path: Path) -> None:
    # Arrange: 둘 다 임직원 권한, 사용자도 임직원 → 가시.
    path_a = tmp_path / "a.txt"
    path_b = tmp_path / "b.txt"
    path_a.write_text("매출 100억 달성", encoding="utf-8")
    path_b.write_text("매출 200억 달성", encoding="utf-8")

    # Act
    result = DiffService().diff_documents(
        path_a, path_b, _meta(), _meta(), USER
    )

    # Assert: 추출 후 라인 변경 1건 감지.
    assert result.changed == 1


def test_diff_documents_populates_page_previews_when_renderer_returns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """페이지 프리뷰 — 렌더러가 dict를 돌려주면 DiffResult에 양쪽 모두 채워진다.

    실제 PDF 렌더(pymupdf)는 무거운 의존이라 _page_render의 캐시 함수를 monkeypatch
    해 가짜 결과를 주입한다. 비교 본체(텍스트 diff) 결과와 독립적으로 채워지는지만
    검증한다 (마커는 변경 없이 ops로 들어옴).
    """
    path_a = tmp_path / "a.txt"
    path_b = tmp_path / "b.txt"
    path_a.write_text("매출 100억 달성", encoding="utf-8")
    path_b.write_text("매출 200억 달성", encoding="utf-8")

    fake_a = {1: "data:image/png;base64,A1", 2: "data:image/png;base64,A2"}
    fake_b = {1: "data:image/png;base64,B1"}
    captured: dict[str, tuple[tuple[str, str, tuple[str, ...]], ...]] = {}

    def fake_render(
        path_str: str,
        _mtime_ns: int,
        highlights_key: tuple[tuple[str, str, tuple[str, ...]], ...],
    ) -> dict[int, str]:
        captured[path_str] = highlights_key
        return fake_a if path_str.endswith("a.txt") else fake_b

    monkeypatch.setattr(
        "kms.services.diff_service.render_page_previews_highlighted_cached",
        fake_render,
    )

    result = DiffService().diff_documents(path_a, path_b, _meta(), _meta(), USER)

    assert result.page_previews_a == fake_a
    assert result.page_previews_b == fake_b
    # 본체 diff는 페이지 프리뷰와 직교 — 텍스트 변경은 그대로 잡힌다.
    assert result.changed == 1
    # 하이라이트 entry — (line, kind, changed_words) 3-tuple.
    left_hl = captured[str(path_a)]
    right_hl = captured[str(path_b)]
    assert any(kind == "change" for _t, kind, _w in left_hl)
    assert any(kind == "change" for _t, kind, _w in right_hl)
    # 라인에 "100억"/"200억"이 포함 + changed_words에도 포함.
    assert any("100억" in line for line, _k, _w in left_hl)
    assert any("200억" in line for line, _k, _w in right_hl)
    assert any("100억" in words for _l, _k, words in left_hl)
    assert any("200억" in words for _l, _k, words in right_hl)


def test_diff_documents_omits_page_previews_when_renderer_returns_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """렌더러가 빈 dict를 돌려주면(예: LibreOffice 부재) 페이지 프리뷰 없이 진행한다.

    포맷 자체는 이제 모두 지원(.txt 포함 — LibreOffice 변환 경로). 환경에 따라
    렌더러가 빈 dict일 수 있다 — 그 경우에도 본체 diff는 정상 진행해야 한다.
    """
    path_a = tmp_path / "a.txt"
    path_b = tmp_path / "b.txt"
    path_a.write_text("동일 라인", encoding="utf-8")
    path_b.write_text("동일 라인", encoding="utf-8")

    monkeypatch.setattr(
        "kms.services.diff_service.render_page_previews_highlighted_cached",
        lambda _p, _m, _h: {},
    )

    result = DiffService().diff_documents(path_a, path_b, _meta(), _meta(), USER)
    assert result.page_previews_a == {}
    assert result.page_previews_b == {}
    # 본체 diff (동일 텍스트) → 변경 0건이지만 ops는 비어있지 않음.
    assert result.changed == 0
    assert result.added == 0
    assert result.deleted == 0


def test_collect_highlights_change_carries_full_line_plus_changed_words() -> None:
    """change entry는 라인 전체 + 변경 단어 튜플 (라인 매치 후 단어 위치 색칠용)."""
    from kms.domain.models import DiffOp, DiffResult, WordSpan
    from kms.services.diff_service import _collect_highlights

    result = DiffResult(
        ops=[
            DiffOp(op="equal", left="공통", right="공통"),
            DiffOp(op="delete", left="삭제된 라인"),
            DiffOp(op="add", right="추가된 라인"),
            DiffOp(
                op="change",
                left="가격 100억 원",
                right="가격 200억 원",
                left_words=[
                    WordSpan(text="가격", changed=False),
                    WordSpan(text="100억", changed=True),
                    WordSpan(text="원", changed=False),
                ],
                right_words=[
                    WordSpan(text="가격", changed=False),
                    WordSpan(text="200억", changed=True),
                    WordSpan(text="원", changed=False),
                ],
            ),
        ],
        added=1,
        deleted=1,
        changed=1,
    )
    left, right = _collect_highlights(result)
    assert left == (
        ("삭제된 라인", "delete", ()),
        ("가격 100억 원", "change", ("100억",)),
    )
    assert right == (
        ("추가된 라인", "add", ()),
        ("가격 200억 원", "change", ("200억",)),
    )


def test_collect_highlights_change_without_words_has_empty_tuple() -> None:
    """word 정보 없는 change는 changed_words=() → _apply_highlights에서 라인 전체 색칠."""
    from kms.domain.models import DiffOp, DiffResult
    from kms.services.diff_service import _collect_highlights

    result = DiffResult(
        ops=[DiffOp(op="change", left="원본 라인", right="수정 라인")],
        added=0,
        deleted=0,
        changed=1,
    )
    left, right = _collect_highlights(result)
    assert left == (("원본 라인", "change", ()),)
    assert right == (("수정 라인", "change", ()),)


def test_collect_highlights_drops_too_short_changed_words() -> None:
    """변경 단어 중 2자 미만은 changed_words에서 제외."""
    from kms.domain.models import DiffOp, DiffResult, WordSpan
    from kms.services.diff_service import _collect_highlights

    result = DiffResult(
        ops=[
            DiffOp(
                op="change",
                left="값 A 이상",
                right="값 B 이상",
                left_words=[
                    WordSpan(text="값", changed=False),
                    WordSpan(text="A", changed=True),
                    WordSpan(text="이상", changed=False),
                ],
                right_words=[
                    WordSpan(text="값", changed=False),
                    WordSpan(text="B", changed=True),
                    WordSpan(text="이상", changed=False),
                ],
            ),
        ],
        added=0,
        deleted=0,
        changed=1,
    )
    left, right = _collect_highlights(result)
    # "A"/"B" 1자 → 제외. 라인은 그대로, changed_words=().
    assert left == (("값 A 이상", "change", ()),)
    assert right == (("값 B 이상", "change", ()),)


def test_diff_documents_denies_when_one_document_unauthorized(tmp_path: Path) -> None:
    # Arrange: b 문서가 사장 전용 → 임직원 사용자에겐 권한 밖.
    path_a = tmp_path / "a.txt"
    path_b = tmp_path / "b.txt"
    path_a.write_text("내용 A", encoding="utf-8")
    path_b.write_text("내용 B", encoding="utf-8")

    # Act / Assert: 한 문서라도 권한 밖이면 접근 거부.
    with pytest.raises(AuthorizationError):
        DiffService().diff_documents(
            path_a, path_b, _meta(), _meta(access=AccessLevel.사장), USER
        )
