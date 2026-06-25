"""페이지 이미지 렌더 유틸 단위 테스트 — 모든 포맷 대상, LibreOffice 변환 경로 포함.

실제 PDF 렌더(pymupdf)와 LibreOffice 변환(subprocess)은 무거운 외부 의존이라
가짜 모듈/함수 주입으로 결정론적으로 검증한다. 검증 핵심:

- 미지원 확장자 → 빈 dict
- PDF → 바로 pymupdf
- 비PDF → LibreOffice 변환 → pymupdf
- LibreOffice 부재 → 비PDF는 빈 dict (PDF는 영향 없음)
- pymupdf 부재 → 모든 포맷 빈 dict
- 캐시 (path, mtime) 키 동작
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from kms.services import _page_render
from kms.services._page_render import (
    render_page_previews,
    render_page_previews_highlighted_cached,
)

_FAKE_PNG = b"\x89PNG\r\n\x1a\nFAKE"
_FAKE_PNG_URL = f"data:image/png;base64,{base64.b64encode(_FAKE_PNG).decode('ascii')}"


def _install_fake_pymupdf(
    monkeypatch: pytest.MonkeyPatch, n_pages: int = 3
) -> None:
    """가짜 pymupdf 모듈을 sys.modules에 주입 — 렌더 결과 결정론적."""

    class _Pix:
        def tobytes(self, _fmt: str) -> bytes:
            return _FAKE_PNG

    class _Page:
        def get_pixmap(self, *, matrix: object, alpha: bool) -> _Pix:  # noqa: ARG002
            return _Pix()

    class _Doc:
        def __len__(self) -> int:
            return n_pages

        def load_page(self, _i: int) -> _Page:
            return _Page()

        def close(self) -> None:
            return None

    fake = SimpleNamespace(
        open=lambda _p: _Doc(),
        Matrix=lambda _x, _y: object(),
    )
    monkeypatch.setitem(sys.modules, "pymupdf", fake)


def test_unsupported_extension_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "doc.xyz"
    path.write_text("anything", encoding="utf-8")
    assert render_page_previews(path) == {}


def test_pdf_renders_directly_via_pymupdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    _install_fake_pymupdf(monkeypatch, n_pages=2)

    previews = render_page_previews(pdf, max_pages=5)
    assert previews == {1: _FAKE_PNG_URL, 2: _FAKE_PNG_URL}


def test_pdf_max_pages_caps_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    _install_fake_pymupdf(monkeypatch, n_pages=10)

    previews = render_page_previews(pdf, max_pages=3)
    assert set(previews.keys()) == {1, 2, 3}


def test_pymupdf_missing_returns_empty_for_pdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setitem(sys.modules, "pymupdf", None)
    monkeypatch.setitem(sys.modules, "fitz", None)

    assert render_page_previews(pdf) == {}


@pytest.mark.parametrize("ext", [".docx", ".pptx", ".xlsx", ".html", ".txt", ".md"])
def test_non_pdf_converts_via_libreoffice_then_renders(
    ext: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """비PDF 포맷은 LibreOffice 변환 후 동일 pymupdf 경로로 렌더된다."""
    src = tmp_path / f"doc{ext}"
    src.write_bytes(b"binary or text payload")
    _install_fake_pymupdf(monkeypatch, n_pages=2)

    converted = tmp_path / "converted.pdf"
    converted.write_bytes(b"%PDF-1.4 converted")
    cleanup_log: list[Path] = []

    def fake_convert(path: Path) -> Path:
        assert path == src
        return converted

    def fake_unlink(*, missing_ok: bool = False) -> None:  # noqa: ARG001
        cleanup_log.append(converted)

    monkeypatch.setattr(_page_render, "_convert_to_pdf", fake_convert)
    # 임시 변환 파일이 렌더 후 정리되는지 확인하기 위해 Path.unlink를 가로채지 않고
    # 실제 파일을 두고 cleanup 후 부재 검증.

    previews = render_page_previews(src)
    assert previews == {1: _FAKE_PNG_URL, 2: _FAKE_PNG_URL}
    assert not converted.exists(), "변환 PDF는 렌더 후 즉시 삭제되어야 함"


def test_non_pdf_without_libreoffice_returns_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LibreOffice가 없으면 비PDF는 빈 dict (graceful degrade)."""
    src = tmp_path / "doc.docx"
    src.write_bytes(b"binary payload")
    _install_fake_pymupdf(monkeypatch)
    monkeypatch.setattr(_page_render, "_find_soffice", lambda: None)

    assert render_page_previews(src) == {}


def test_libreoffice_timeout_returns_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "doc.pptx"
    src.write_bytes(b"binary payload")
    _install_fake_pymupdf(monkeypatch)
    monkeypatch.setattr(_page_render, "_find_soffice", lambda: "/fake/soffice")

    def fake_run(*_args: object, **_kw: object) -> object:
        import subprocess

        raise subprocess.TimeoutExpired(cmd=["soffice"], timeout=1)

    monkeypatch.setattr(_page_render.subprocess, "run", fake_run)

    assert render_page_previews(src) == {}


def test_libreoffice_nonzero_returncode_returns_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "doc.html"
    src.write_text("<html/>", encoding="utf-8")
    _install_fake_pymupdf(monkeypatch)
    monkeypatch.setattr(_page_render, "_find_soffice", lambda: "/fake/soffice")

    def fake_run(*_args: object, **_kw: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=1, stdout=b"", stderr=b"err")

    monkeypatch.setattr(_page_render.subprocess, "run", fake_run)

    assert render_page_previews(src) == {}


def test_apply_highlights_line_match_then_word_intersection() -> None:
    """change는 라인 매치 후 라인 rect와 교차하는 변경 단어만 색칠.

    핵심 false positive 방지 시나리오: 페이지에 "50억"이 여러 곳 있고 변경 라인
    안 "50억"만 색칠되어야 한다. 라인 밖 "50억"은 무시.
    """
    from types import SimpleNamespace

    from kms.services._page_render import (
        _HIGHLIGHT_COLORS,
        _apply_highlights,
    )

    class _Annot:
        def set_colors(self, *, stroke: tuple) -> None:
            self.color = stroke

        def set_opacity(self, op: float) -> None:
            self.opacity = op

        def update(self) -> None:
            return None

    def _rect(x0, y0, x1, y1):
        return SimpleNamespace(x0=x0, y0=y0, x1=x1, y1=y1)

    annotated: list[tuple[Any, _Annot]] = []

    class _Page:
        def search_for(self, text: str) -> list[object]:
            # 라인 매치 — "순이익 50억"은 페이지에 한 곳 (y=200).
            if text == "순이익 50억":
                return [_rect(0, 200, 200, 220)]
            # 단어 "50억"은 페이지에 두 곳 — y=200 (라인 안), y=400 (다른 라인).
            if text == "50억":
                return [_rect(80, 200, 120, 220), _rect(50, 400, 90, 420)]
            return []

        def add_highlight_annot(self, rect: object) -> _Annot:
            annot = _Annot()
            annotated.append((rect, annot))
            return annot

    page = _Page()
    # change entry: 라인 "순이익 50억", 변경 단어 ("50억",)
    _apply_highlights(page, [("순이익 50억", "change", ("50억",))])

    # 라인 안 50억(y=200)만 색칠 — 라인 밖 50억(y=400)은 무시.
    assert len(annotated) == 1
    rect, annot = annotated[0]
    assert rect.y0 == 200
    assert annot.color == _HIGHLIGHT_COLORS["change"]


def test_apply_highlights_add_delete_paints_full_line() -> None:
    """add/delete는 라인 전체 색칠 (변경 단어 없음)."""
    from types import SimpleNamespace

    from kms.services._page_render import _HIGHLIGHT_COLORS, _apply_highlights

    class _Annot:
        def set_colors(self, *, stroke: tuple) -> None:
            self.color = stroke

        def set_opacity(self, op: float) -> None:
            return None

        def update(self) -> None:
            return None

    def _rect(x0, y0, x1, y1):
        return SimpleNamespace(x0=x0, y0=y0, x1=x1, y1=y1)

    annotated: list[_Annot] = []

    class _Page:
        def search_for(self, text: str) -> list[object]:
            return [_rect(0, 100, 200, 120)] if "삭제" in text else []

        def add_highlight_annot(self, rect: object) -> _Annot:
            a = _Annot()
            annotated.append(a)
            return a

    page = _Page()
    _apply_highlights(page, [("삭제된 라인", "delete", ())])

    assert len(annotated) == 1
    assert annotated[0].color == _HIGHLIGHT_COLORS["delete"]


def test_apply_highlights_skips_markers_and_short_text() -> None:
    """마커·짧은 텍스트는 search 자체 X."""
    from kms.services._page_render import _apply_highlights

    searched: list[str] = []

    class _Page:
        def search_for(self, text: str) -> list[object]:
            searched.append(text)
            return []

        def add_highlight_annot(self, _rect: object) -> object:
            return object()

    _apply_highlights(
        _Page(),
        [
            ("[IMAGE p=1 sha=abc]", "delete", ()),
            ("| 표 | 행 |", "change", ()),
            ("", "change", ()),
            ("a", "change", ()),
        ],
    )
    # 4건 모두 _normalize_for_search에서 None → search 안 함.
    assert searched == []


def test_apply_highlights_skips_when_line_not_found() -> None:
    """라인 매치 실패 시 단어 검색도 안 함 (무관한 곳 색칠 방지)."""
    from kms.services._page_render import _apply_highlights

    annotated: list[object] = []

    class _Page:
        def search_for(self, _text: str) -> list[object]:
            return []  # 페이지에 라인 없음

        def add_highlight_annot(self, rect: object) -> object:
            annotated.append(rect)
            return object()

    _apply_highlights(
        _Page(),
        [("존재하지 않는 라인", "change", ("단어",))],
    )
    # 라인 매치 0건 → 단어 검색도 안 함 → annot 0건.
    assert annotated == []


def test_tighten_punct_spacing_removes_extractor_artifacts() -> None:
    """괄호·구두점 둘레의 추출기 공백만 제거하고 단어 공백은 보존한다."""
    from kms.services._page_render import _tighten_punct_spacing

    assert _tighten_punct_spacing("[ 런웨이 요금정책 ]") == "[런웨이 요금정책]"
    assert _tighten_punct_spacing("유저 수 무관하게 ,") == "유저 수 무관하게,"
    # 단어 사이 공백은 그대로.
    assert _tighten_punct_spacing("순이익 50억 증가") == "순이익 50억 증가"


def test_apply_highlights_matches_via_punct_tighten_fallback() -> None:
    """원본(추출기 공백 포함)은 0건이어도 구두점 보정본으로 매칭되면 색칠한다.

    회귀: 추출 라인 '[ 런웨이 요금정책 ]'은 PDF 원문 '[런웨이 요금정책]'과 공백이
    달라 search_for(원본)가 0건이 되어 하이라이트가 누락됐다(마커 미적용 증상).
    """
    from types import SimpleNamespace

    from kms.services._page_render import _HIGHLIGHT_COLORS, _apply_highlights

    class _Annot:
        def set_colors(self, *, stroke: tuple) -> None:
            self.color = stroke

        def set_opacity(self, op: float) -> None:
            return None

        def update(self) -> None:
            return None

    annotated: list[_Annot] = []

    class _Page:
        def search_for(self, text: str) -> list[object]:
            # PDF 원문은 공백 없는 형태만 존재 — 추출기 공백 포함 원본은 0건.
            if text == "[런웨이 요금정책]":
                return [SimpleNamespace(x0=0, y0=10, x1=200, y1=30)]
            return []

        def add_highlight_annot(self, _rect: object) -> _Annot:
            a = _Annot()
            annotated.append(a)
            return a

    _apply_highlights(_Page(), [("[ 런웨이 요금정책 ]", "delete", ())])

    assert len(annotated) == 1
    assert annotated[0].color == _HIGHLIGHT_COLORS["delete"]


def test_prefix_candidates_shrinks_on_word_boundary() -> None:
    """긴 라인은 단어 경계로 점점 짧은 prefix를 길이 내림차순으로 만든다(최소 길이까지)."""
    from kms.services._page_render import _SEARCH_MIN_PREFIX, _prefix_candidates

    cands = _prefix_candidates("런웨이 요금정책 개정안 상세 비교 항목")
    # 길이 내림차순 + 모두 단어 경계(중간 절단 없음) + 최소 길이 이상.
    assert cands == sorted(cands, key=len, reverse=True)
    assert all(len(c) >= _SEARCH_MIN_PREFIX for c in cands)
    assert all(not c.endswith(" ") for c in cands)
    # 원본의 진부분문자열(라인 시작부터) — 위치 정확성 보장.
    assert all("런웨이 요금정책 개정안 상세 비교 항목".startswith(c) for c in cands)
    # 단어 1개뿐이면 단축 후보 없음(자기 자신 제외).
    assert _prefix_candidates("단어") == []


def test_apply_highlights_matches_via_prefix_shrink_fallback() -> None:
    """full-line은 0건이어도 점진적 단축 prefix가 라인 앞부분에 매칭되면 색칠한다.

    회귀: 추출 라인이 길어 렌더 PDF에서 줄바꿈되면 search_for(full-line)가 0건이
    되어 마커가 전부 누락됐다(증상: 페이지 프리뷰에 마커 미표시). 라인 시작부터의
    짧은 prefix는 한 줄에 들어와 매칭된다.
    """
    from types import SimpleNamespace

    from kms.services._page_render import _HIGHLIGHT_COLORS, _apply_highlights

    line = "런웨이 요금정책 개정안 상세 비교 항목 전체 목록"

    class _Annot:
        def set_colors(self, *, stroke: tuple) -> None:
            self.color = stroke

        def set_opacity(self, op: float) -> None:
            return None

        def update(self) -> None:
            return None

    annotated: list[_Annot] = []

    class _Page:
        def search_for(self, text: str) -> list[object]:
            # PDF는 라인 앞부분만 한 줄에 존재 — full-line은 줄바꿈으로 0건.
            if text == "런웨이 요금정책 개정안":
                return [SimpleNamespace(x0=0, y0=10, x1=200, y1=30)]
            return []

        def add_highlight_annot(self, _rect: object) -> _Annot:
            a = _Annot()
            annotated.append(a)
            return a

    _apply_highlights(_Page(), [(line, "delete", ())])

    assert len(annotated) == 1
    assert annotated[0].color == _HIGHLIGHT_COLORS["delete"]


def test_highlighted_cache_keyed_by_path_mtime_and_highlights(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(경로, mtime_ns, highlights tuple) 동일 키면 캐시 hit. mtime/highlights 다르면 miss."""
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    render_page_previews_highlighted_cached.cache_clear()

    calls: list[tuple[str, object]] = []

    def _fake_render(p: Path, *, highlights: object = None) -> dict[int, str]:
        calls.append((str(p), highlights))
        return {1: _FAKE_PNG_URL}

    monkeypatch.setattr(_page_render, "render_page_previews", _fake_render)

    hl1 = (("라인 A", "change", ("변경",)),)
    hl2 = (("라인 B", "delete", ()),)

    first = render_page_previews_highlighted_cached(str(pdf), 1234, hl1)
    second = render_page_previews_highlighted_cached(str(pdf), 1234, hl1)
    assert first is second
    assert len(calls) == 1

    # 같은 path/mtime이지만 highlights 다름 → 캐시 miss.
    render_page_previews_highlighted_cached(str(pdf), 1234, hl2)
    assert len(calls) == 2

    # mtime 다름 → 캐시 miss.
    third = render_page_previews_highlighted_cached(str(pdf), 5678, hl1)
    assert len(calls) == 3
    assert third[1].startswith("data:image/png;base64,")
