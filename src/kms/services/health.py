"""헬스체크 — 현재 backend 구성과 도달성을 graceful하게 보고한다.

각 구성요소(embedder·vectorstore·graph·llm)를 ping 수준으로만 확인하고, 도달
실패는 예외를 잡아 `{"ok": false, "detail": ...}`로 표기한다(전체 500 금지 —
헬스체크 자체가 죽으면 무용). 하나라도 down이면 `status`는 `degraded`다.

무거운 작업 금지: 모델 로드·대량 쿼리를 하지 않는다. embedder는 의존 설치 여부만
보고하고(`find_spec`), 나머지는 heartbeat/connectivity/`GET /models` ping만 한다.

시크릿 무노출: 예외 메시지에는 base_url·자격증명이 섞일 수 있으므로 응답에는
예외 **타입명만** 싣는다(`detail`). 자격증명·토큰·base_url을 응답에 넣지 않는다.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from typing import Any

from kms.adapters.graph.base import GraphStore
from kms.adapters.graph.neo4j_store import Neo4jGraphStore
from kms.adapters.llm.base import LLMClient
from kms.adapters.llm.openai_compat import OpenAICompatLLM
from kms.adapters.searchindex.base import SearchIndex
from kms.adapters.searchindex.pg_store import PgSearchIndex
from kms.adapters.vectorstore.base import VectorStore
from kms.config.settings import Settings


@dataclass(frozen=True)
class HealthService:
    """현재 backend 구성과 도달성을 보고하는 헬스체크 유스케이스.

    조립된 어댑터 인스턴스를 그대로 받아 ping한다(인프라 없는 기본 구성에서도
    성공). 도달성 확인 외의 비즈니스 로직은 없다.
    """

    settings: Settings
    vectorstore: VectorStore
    graph_store: GraphStore
    llm: LLMClient
    search_index: SearchIndex

    def report(self) -> dict[str, Any]:
        """전체 상태 + 구성요소별 상태를 반환한다. 항상 표기만 하고 던지지 않는다."""
        backends = {
            "embedder": self._check_embedder(),
            "vectorstore": self._check_vectorstore(),
            "graph": self._check_graph(),
            "llm": self._check_llm(),
            "search_index": self._check_search_index(),
        }
        status = "ok" if all(check["ok"] for check in backends.values()) else "degraded"
        return {"status": status, "backends": backends}

    def _check_embedder(self) -> dict[str, Any]:
        # 모델을 로드하지 않는다(무거운 작업 금지) — 의존 설치 여부만 확인한다.
        backend = self.settings.embedder_backend
        if backend == "fake":
            return {"backend": backend, "ok": True}
        if backend == "sentence_transformers":
            installed = importlib.util.find_spec("sentence_transformers") is not None
            if installed:
                return {"backend": backend, "ok": True}
            return {
                "backend": backend,
                "ok": False,
                "detail": "sentence_transformers 미설치",
            }
        return {"backend": backend, "ok": False, "detail": "알 수 없는 embedder backend"}

    def _check_vectorstore(self) -> dict[str, Any]:
        backend = self.settings.vector_backend
        try:
            self.vectorstore.ping()
        except Exception as exc:
            return {"backend": backend, "ok": False, "detail": _safe_detail(exc)}
        return {"backend": backend, "ok": True}

    def _check_graph(self) -> dict[str, Any]:
        backend = self.settings.graph_backend
        if backend == "memory":
            # in-memory 그래프는 외부 의존이 없어 항상 도달 가능.
            return {"backend": backend, "ok": True}
        if backend == "neo4j":
            try:
                if isinstance(self.graph_store, Neo4jGraphStore):
                    self.graph_store.ping()
            except Exception as exc:
                return {"backend": backend, "ok": False, "detail": _safe_detail(exc)}
            return {"backend": backend, "ok": True}
        return {"backend": backend, "ok": False, "detail": "알 수 없는 graph backend"}

    def _check_llm(self) -> dict[str, Any]:
        backend = self.settings.llm_backend
        if backend == "openai_compat":
            try:
                if isinstance(self.llm, OpenAICompatLLM):
                    self.llm.ping()
            except Exception as exc:
                return {"backend": backend, "ok": False, "detail": _safe_detail(exc)}
            return {"backend": backend, "ok": True}
        return {"backend": backend, "ok": False, "detail": "알 수 없는 llm backend"}

    def _check_search_index(self) -> dict[str, Any]:
        backend = self.settings.search_backend
        if backend == "memory":
            # in-memory 인덱스는 외부 의존이 없어 항상 도달 가능.
            return {"backend": backend, "ok": True}
        if backend == "postgres":
            try:
                if isinstance(self.search_index, PgSearchIndex):
                    self.search_index.ping()
            except Exception as exc:
                return {"backend": backend, "ok": False, "detail": _safe_detail(exc)}
            return {"backend": backend, "ok": True}
        return {
            "backend": backend,
            "ok": False,
            "detail": "알 수 없는 search_index backend",
        }


def _safe_detail(exc: Exception) -> str:
    """예외를 응답용 detail로 변환한다 — **타입명만** 노출(시크릿 무노출).

    예외 메시지(`str(exc)`)에는 base_url·자격증명·연결 문자열이 섞일 수 있어
    응답·로그에 싣지 않는다. 어떤 종류의 실패인지(타입)만 알린다.
    """
    return type(exc).__name__
