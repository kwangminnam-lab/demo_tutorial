"""DocuX REST API에 대한 thin proxy 클라이언트 (MCP 툴 내부 사용).

토큰을 변형 없이 그대로 forward한다(MCP 무재승인 — K8S_MCP_INTEGRATION §9.3).
조용한 실패 금지 — HTTP 에러는 그대로 RuntimeError로 전파해 MCP 응답에 반영.
"""

from __future__ import annotations

import os
from typing import Any

import httpx


class InternalAPI:
    """DocuX FastAPI에 token-forward 요청을 보내는 thin client.

    인증은 항상 활성이라, chatbot이 보낸 토큰을 그대로 forward해 docux-api가 검증한다
    (토큰 없으면 docux-api가 401).
    """

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.base = (base_url or os.environ.get("DOCUX_API_BASE", "http://docux-api:8000")).rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        authorization: str | None = None,
        **kw: Any,
    ) -> dict[str, Any]:
        headers = kw.pop("headers", {}) or {}
        if authorization:
            headers["Authorization"] = authorization
        resp = await self._client.request(
            method, f"{self.base}{path}", headers=headers, **kw
        )
        if resp.status_code >= 400:
            # 본문에 토큰을 싣지 않는다. 상태 코드 + 짧은 본문만.
            raise RuntimeError(
                f"docux-api {method} {path} failed: {resp.status_code} {resp.text[:200]}"
            )
        if resp.headers.get("content-type", "").startswith("application/json"):
            return resp.json()
        return {"raw": resp.text}

    async def get(self, path: str, *, authorization: str | None = None, **kw: Any) -> dict[str, Any]:
        return await self._request("GET", path, authorization=authorization, **kw)

    async def post(self, path: str, *, authorization: str | None = None, **kw: Any) -> dict[str, Any]:
        return await self._request("POST", path, authorization=authorization, **kw)
