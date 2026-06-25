"""RAG 챗봇 라우트 (POST /v1/rag) — LibreChat이 호출할 권한 인지 RAG 경계.

진입점은 얇게(CONVENTIONS·ADR-011): 본문 파싱 → 인증된 `UserContext` 획득 →
`RAGService`에 위임 → 응답 매핑만 한다. 권한 필터·부서 가중·근거 0건 처리·출처
인용 등 비즈니스 로직은 전부 `RAGService`(서비스 레이어)에 있다.

기본은 SSE 스트리밍(`text/event-stream`)으로 부분 응답을 전달한다(ADR-004 5초 내
체감). `?stream=false`면 완성된 `Answer`를 JSON으로 한 번에 반환하는 폴백이다.
권한 강제: `get_current_user`의 `UserContext`를 그대로 서비스에 넘긴다 — 권한 밖
근거는 서비스가 retrieval 단계에서 거르므로 응답·스트림에 새지 않는다.
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import APIRouter, Depends, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from kms.adapters.llm.router import LLMRouter
from kms.api.deps import get_current_user, get_llm_router, get_rag_service
from kms.domain.models import UserContext
from kms.services.rag_service import RAGService

router = APIRouter(prefix="/v1", tags=["rag"])


class RagRequest(BaseModel):
    """RAG 질의 본문 — 질문 + 검색 근거 개수(top_k)."""

    query: str = Field(min_length=1, description="자연어 질의")
    top_k: int = Field(default=8, ge=1, le=100, description="검색 근거 최대 개수")


@router.post("/rag")
def rag(
    request: RagRequest,
    user: UserContext = Depends(get_current_user),
    service: RAGService = Depends(get_rag_service),
    llm_router: LLMRouter = Depends(get_llm_router),
    stream: bool = Query(default=True, description="SSE 스트리밍 여부"),
    citations_only: bool = Query(
        default=False,
        description="True면 LLM 생성 없이 출처(citations)·근거 여부만 반환(stream=false 필요).",
    ),
) -> Response:
    """권한 인지 근거 기반 RAG 답변. 기본 SSE 스트리밍, `stream=false`면 Answer JSON.

    `?citations_only=true`(비스트리밍)면 LLM을 호출하지 않고 출처만 조립해 반환한다 —
    스트리밍으로 본문을 받은 클라가 출처만 보강할 때 LLM 재생성 지연을 없앤다.
    LLM은 사내 단일 모델(gpt-oss-120b 게이트웨이)을 쓴다(provider 선택 없음, 외부 키 불요).
    """
    # 출처만 필요한 경로는 LLM 생성을 통째로 건너뛴다(인용 표시 지연 제거).
    if citations_only:
        answer = service.citations(request.query, user, top_k=request.top_k)
        return JSONResponse(content=jsonable_encoder(answer))

    client = llm_router.for_request()

    if stream:
        events = _sse_events(
            service.stream_answer(request.query, user, top_k=request.top_k, llm_client=client)
        )
        return StreamingResponse(events, media_type="text/event-stream")
    answer = service.answer(request.query, user, top_k=request.top_k, llm_client=client)
    return JSONResponse(content=jsonable_encoder(answer))


def _sse_events(chunks: Iterator[str]) -> Iterator[str]:
    """LLM 텍스트 조각을 SSE `data:` 이벤트로 감싼다(조각 내 개행은 다중 data 라인).

    스트림 종료는 `[DONE]` 센티넬로 알린다(LibreChat/OpenAI 호환 관례).
    """
    for chunk in chunks:
        payload = "\n".join(f"data: {line}" for line in chunk.split("\n"))
        yield f"{payload}\n\n"
    yield "data: [DONE]\n\n"
