"""parse `_render_html` 단위 테스트 — 충실 HTML 우선, 없으면 markdown 폴백.

파서(docling)가 export_to_html 로 표·구조 보존 HTML 을 주면 그대로 쓰고, 비어 있으면
markdown→HTML 폴백으로 렌더한다. 동작(우선순위)을 잠근다.
"""

from __future__ import annotations

from kms.adapters.ingestion.ir import MarkdownDoc
from kms.api.v1 import parse as parse_api


def test_render_html_prefers_ir_html_when_present() -> None:
    # ir.html 이 있으면 그대로 반환(템플릿 래핑/마크다운 변환 거치지 않음).
    ir = MarkdownDoc(
        markdown="# 머리말\n\n본문",
        html="<html><body><table><tr><td>셀</td></tr></table></body></html>",
    )
    out = parse_api._render_html(ir, "doc.pdf")
    assert out == ir.html
    assert "<table>" in out


def test_render_html_falls_back_to_markdown_when_html_empty() -> None:
    # ir.html 이 비면 markdown→HTML 폴백(제목 텍스트가 본문에 포함).
    ir = MarkdownDoc(markdown="# 머리말\n\n본문", html="")
    out = parse_api._render_html(ir, "doc.pdf")
    assert "머리말" in out
    assert "<html" in out.lower()


def test_render_html_falls_back_when_html_blank() -> None:
    # 공백만 있는 html 도 폴백 취급(strip 후 빈 값).
    ir = MarkdownDoc(markdown="본문", html="   \n  ")
    out = parse_api._render_html(ir, "doc.pdf")
    assert "본문" in out
