"""LineProvider 레지스트리 — 우선순위대로 provider 를 시도해 라인을 뽑는다.

등록 순서(우선순위):
  1) PymupdfLineProvider — 디지털 PDF(텍스트 레이어) 라인+bbox.
  2) PaddleLineProvider  — 스캔 PDF/이미지(텍스트 레이어 없음) OCR 라인+bbox. paddleocr
     (무거움) 의존이라 `is_available()` lazy import 로 가드 — 미설치 환경(Mac)에선 자동 제외
     (클러스터 전용). 손글씨는 별도 VLM tier(서비스가 빈 결과 시 폴백).

`extract_lines()` 는 가용·지원 provider 를 순서대로 시도해 첫 비어있지 않은 결과를 쓴다.
디지털 PDF 는 pymupdf 가 즉시 반환(paddle 미호출), 스캔본/이미지는 pymupdf 빈 결과 →
OCR 캐스케이드. 전부 빈 결과면 빈 리스트 — 호출자(서비스)가 VLM/값없음 폴백을 명확히 판단한다.
"""

from __future__ import annotations

from pathlib import Path

from kms.adapters.ingestion.lines.base import LineProvider
from kms.adapters.ingestion.lines.paddle_lines import PaddleLineProvider
from kms.adapters.ingestion.lines.pymupdf_lines import PymupdfLineProvider

from kms.domain.extraction import TextLine


def default_line_providers() -> list[LineProvider]:
    """기본 우선순위 provider 목록(디지털 → OCR)."""
    return [PymupdfLineProvider(), PaddleLineProvider()]


class LineProviderRegistry:
    """확장자/가용성으로 provider를 고르는 레지스트리."""

    def __init__(self, providers: list[LineProvider] | None = None) -> None:
        self._providers = (
            list(providers) if providers is not None else default_line_providers()
        )

    def supports(self, path: Path) -> bool:
        return any(
            p.is_available() and p.supports(path) for p in self._providers
        )

    def resolve(self, path: Path) -> LineProvider:
        """첫 가용·지원 provider를 반환한다. 없으면 RuntimeError(조용한 실패 금지)."""
        for provider in self._providers:
            if provider.is_available() and provider.supports(path):
                return provider
        raise RuntimeError(
            f"라인 추출을 지원하는 provider가 없습니다 ({path.name}). "
            "디지털 PDF(pymupdf) 또는 스캔/이미지(PaddleOCR, 클러스터)만 지원합니다."
        )

    def extract_lines(self, path: Path) -> list[TextLine]:
        """가용·지원 provider 를 순서대로 시도해 첫 비어있지 않은 결과를 반환한다.

        디지털 PDF 는 pymupdf 가 라인을 반환하고 종료(paddle 미호출 — 성능 영향 0).
        스캔본/이미지는 pymupdf 가 빈 결과 → OCR provider 로 캐스케이드. 전부 빈 결과면 []
        (호출자가 VLM/값없음 폴백 판단 — 조용한 실패 아님).
        """
        for provider in self._providers:
            if not (provider.is_available() and provider.supports(path)):
                continue
            lines = provider.lines(path)
            if lines:
                return lines
        return []
