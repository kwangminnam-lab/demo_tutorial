"""DocuX MCP 서버 — FastMCP streamable HTTP transport.

6개 툴을 노출하고 DOCUX_API_BASE의 REST API에 proxy한다. 카탈로그 챗봇이
MCP servers에 등록하는 endpoint다 (K8S_MCP_INTEGRATION §9.1·§9.4).

실행:
    python -m kms.mcp.server          # 9000 포트 streamable-http
    PORT=8080 python -m kms.mcp.server # 포트 오버라이드

도구:
    docux.search           — 통합 검색 (BM25 + 벡터 + RRF + reranker)
    docux.ask_rag          — 근거 + citation (LLM은 호출자가 수행)
    docux.get_document     — 파일 본문 + 메타
    docux.compare_documents— 두 문서 시맨틱 diff
    docux.parse_document   — 파일 → HTML/JSON + 페이지 프리뷰
    docux.list_sources     — 적재된 소스 + 카운트
"""

from __future__ import annotations

import os
from typing import Any

from kms.mcp.api_client import InternalAPI

try:
    from mcp.server.fastmcp import Context, FastMCP
except ImportError as exc:  # pragma: no cover - 의존성 안내
    raise ImportError(
        "MCP 의존성 미설치 — `uv add mcp` 또는 pyproject.toml의 mcp 의존성을 확인하세요."
    ) from exc


mcp = FastMCP("docux", stateless_http=True)
api = InternalAPI(base_url=os.environ.get("DOCUX_API_BASE"))


def _forward_token(ctx: Context | None) -> str | None:
    """현재 MCP 요청의 Authorization 헤더를 그대로 추출 (없으면 None).

    인증은 항상 활성이라 토큰이 None이면 docux-api가 401로 막는다(우회 없음).
    """
    if ctx is None:
        return None
    try:
        # request 가 None 일 수 있으나 AttributeError 로 잡아 None 반환한다(아래 except).
        return ctx.request_context.request.headers.get("authorization")  # type: ignore[union-attr]
    except AttributeError:
        return None


@mcp.tool()
async def search(
    query: str,
    top_k: int = 10,
    sources: list[str] | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """사내 문서 하이브리드 검색 (BM25 + 벡터 + RRF + reranker)."""
    params: dict[str, Any] = {"q": query, "top_k": top_k}
    if sources:
        params["sources"] = ",".join(sources)
    return await api.get("/v1/search", params=params, authorization=_forward_token(ctx))


@mcp.tool()
async def ask_rag(
    question: str,
    top_k: int = 8,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """질문에 대한 근거 + citation 반환. 답변 생성은 호출자(챗봇 LLM)가 수행."""
    return await api.post(
        "/v1/rag",
        json={"query": question, "top_k": top_k},
        authorization=_forward_token(ctx),
    )


@mcp.tool()
async def get_document(doc_id: str, ctx: Context | None = None) -> dict[str, Any]:
    """문서 본문 + 메타. 접근 권한은 토큰 기반으로 docux-api가 강제."""
    return await api.get(f"/v1/files/{doc_id}", authorization=_forward_token(ctx))


@mcp.tool()
async def compare_documents(
    doc_a_id: str,
    doc_b_id: str,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """두 문서 시맨틱 diff + 페이지 프리뷰(색칠 annotation)."""
    return await api.post(
        "/v1/diff",
        json={"a": doc_a_id, "b": doc_b_id},
        authorization=_forward_token(ctx),
    )


@mcp.tool()
async def parse_document(
    file_uri: str,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """파일을 HTML/JSON + 페이지 프리뷰로 파싱한다."""
    return await api.post(
        "/v1/parse",
        json={"file_uri": file_uri},
        authorization=_forward_token(ctx),
    )


@mcp.tool()
async def list_sources(ctx: Context | None = None) -> dict[str, Any]:
    """적재된 소스 목록 + 카운트 + 마지막 인덱싱 시각."""
    return await api.get("/v1/sources", authorization=_forward_token(ctx))


def main() -> None:
    """엔트리포인트 — streamable HTTP transport로 MCP 서버 기동."""
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "9000"))
    # FastMCP.run 은 transport 별 kwargs(host/port)를 forwarding 하나 스텁엔 미선언.
    mcp.run(transport="streamable-http", host=host, port=port)  # type: ignore[call-arg]


if __name__ == "__main__":
    main()
