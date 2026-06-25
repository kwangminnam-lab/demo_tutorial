"""헬스체크 라우터 (GET /healthz) — 공개 엔드포인트(인증 불요).

진입점은 얇게(CONVENTIONS): `HealthService.report()`를 호출해 그대로 반환만 한다.
도달성 판단·graceful 표기는 전부 서비스에 있다. 서비스가 down도 표기로 흡수하므로
이 라우트는 항상 200을 반환한다(헬스체크가 죽으면 무용 — 500 금지).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from kms.api.deps import get_health_service
from kms.services.health import HealthService

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz(
    health: HealthService = Depends(get_health_service),
) -> dict[str, Any]:
    """현재 backend 구성·도달성을 보고한다. down이어도 200 + degraded."""
    return health.report()
