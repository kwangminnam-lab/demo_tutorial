"""조립 단일 지점 — `Settings`의 backend 선택 → 어댑터·서비스 조립(composition root).

런타임에 Fake/Real 구현을 **환경 변수로 선택**해 한곳에서 묶는다. 기본값은 전부
경량(fake/memory/ephemeral)이라 인프라·모델 없이도 조립이 성공하고 테스트가
돈다(ADR-007 어댑터 격리 — 구현 교체 가능하게 인터페이스 뒤로 분리).

레이어 분리: 도메인·서비스는 어댑터 인터페이스에만 의존하고, 구체 구현 선택은
여기에만 둔다. `create_app`이 이 모듈로 기본 의존성을 조립한다.
"""

from __future__ import annotations

from dataclasses import dataclass

from kms.adapters.extraction.base import FieldExtractor, VlmFieldExtractor
from kms.adapters.extraction.llm_extractor import LlmFieldExtractor
from kms.adapters.extraction.qwen_vl import QwenVlmExtractor
from kms.adapters.graph.base import GraphStore
from kms.adapters.graph.memory_store import InMemoryGraphStore
from kms.adapters.graph.neo4j_store import Neo4jGraphStore
from kms.adapters.ingestion.lines.registry import LineProviderRegistry
from kms.adapters.llm.base import LLMClient
from kms.adapters.llm.router import LLMRouter
from kms.adapters.llm.openai_compat import OpenAICompatLLM
from kms.adapters.observability.langfuse_tracer import LangfuseTracer
from kms.adapters.observability.mlflow_tracker import MlflowTracker
from kms.adapters.reranker.base import Reranker
from kms.adapters.reranker.bge_reranker import BgeReranker
from kms.adapters.reranker.fake import FakeReranker
from kms.adapters.reranker.qwen3_reranker import Qwen3Reranker
from kms.adapters.searchindex.base import SearchIndex
from kms.adapters.searchindex.memory_store import InMemorySearchIndex
from kms.adapters.searchindex.pg_store import PgSearchIndex
from kms.adapters.vectorstore.base import VectorStore
from kms.adapters.vectorstore.embedder import Embedder, FakeEmbedder
from kms.adapters.vectorstore.memory_store import InMemoryVectorStore
from kms.adapters.vectorstore.pg_store import PgVectorStore
from kms.adapters.vectorstore.sentence_transformer import SentenceTransformerEmbedder
from kms.config.settings import Settings
from kms.services.diff_service import DiffService
from kms.services.health import HealthService
from kms.services.ingestion_service import IngestionService
from kms.services.rag_service import RAGService
from kms.services.search_service import SearchService


@dataclass(frozen=True)
class AppServices:
    """조립된 유스케이스 서비스 묶음 — api 진입점이 의존성으로 꺼내 쓴다.

    인증(AuthService)·문서 해소기는 DB 세션·IdP 등 별도 조립 관심사라 여기 밖이다
    (앱 조립 시 `dependency_overrides`로 주입).
    """

    search: SearchService
    rag: RAGService
    ingestion: IngestionService
    diff: DiffService
    health: HealthService
    llm_router: LLMRouter


def build_embedder(settings: Settings) -> Embedder:
    """`embedder_backend`에 따라 임베더를 만든다 (기본 `fake`).

    `sentence_transformers`는 실 임베딩 모델 어댑터(in-process)다. 무거운 ML 의존
    (미설치 가능)은 `SentenceTransformerEmbedder` 생성자에서 lazy import하므로,
    미설치면 이 시점에 설치 안내가 담긴 명확한 에러가 난다(조용한 폴백 금지). 어댑터
    모듈 import 자체는 의존을 요구하지 않아 여기 정적 import는 미설치 환경에서도 안전하다.
    """
    backend = settings.embedder_backend
    if backend == "fake":
        return FakeEmbedder()
    if backend == "sentence_transformers":
        return SentenceTransformerEmbedder(
            settings.embedding_model_name, device=settings.embedding_device
        )
    raise ValueError(
        f"알 수 없는 embedder_backend: {backend!r} "
        "(허용: 'fake', 'sentence_transformers')"
    )


def build_reranker(settings: Settings) -> Reranker | None:
    """`reranker_backend`에 따라 리랭커를 만든다. 비활성이면 None(재정렬 스킵).

    무거운 모델은 각 어댑터 생성자가 lazy load라(첫 score 호출에 가중치 적재) 조립
    시점엔 모델을 끌어오지 않는다. 디바이스는 임베딩과 같은 설정(`embedding_device`)을
    공유한다(None이면 cuda→mps→cpu 자동).
    """
    if not settings.reranker_enabled:
        return None
    backend = settings.reranker_backend
    if backend == "fake":
        return FakeReranker()
    if backend == "qwen3":
        return Qwen3Reranker(settings.reranker_model, device=settings.embedding_device)
    if backend == "bge":
        return BgeReranker(settings.reranker_model, device=settings.embedding_device)
    raise ValueError(
        f"알 수 없는 reranker_backend: {backend!r} (허용: 'qwen3', 'bge', 'fake')"
    )


def build_vectorstore(settings: Settings, embedder: Embedder) -> VectorStore:
    """`vector_backend`에 따라 벡터 저장소를 만든다 (기본 `memory`).

    `memory`는 외부 의존 없는 InMemory 구현(테스트·dev 기본)이다. `postgres`는
    pgvector 기반 실구현(운영) — psycopg를 lazy import하므로 미설치면 생성 시점에
    설치 안내 에러가 난다(조용한 폴백 금지).
    """
    backend = settings.vector_backend
    if backend == "memory":
        return InMemoryVectorStore(embedder)
    if backend == "postgres":
        # PostgreSQL + pgvector(ADR-019) — 운영 벡터 저장소.
        return PgVectorStore.from_settings(settings, embedder)
    raise ValueError(
        f"알 수 없는 vector_backend: {backend!r} (허용: 'memory', 'postgres')"
    )


def build_graph_store(settings: Settings) -> GraphStore:
    """`graph_backend`에 따라 그래프 저장소를 만든다 (기본 `memory`)."""
    backend = settings.graph_backend
    if backend == "memory":
        return InMemoryGraphStore()
    if backend == "neo4j":
        return Neo4jGraphStore.from_settings(settings)
    raise ValueError(
        f"알 수 없는 graph_backend: {backend!r} (허용: 'memory', 'neo4j')"
    )


def build_search_index(settings: Settings) -> SearchIndex:
    """`search_backend`에 따라 어휘 검색 인덱스를 만든다 (기본 `memory`).

    `memory`는 외부 의존 없는 InMemory 구현이다. `postgres`는 tsvector 기반 실구현
    (운영) — psycopg를 lazy import하므로 미설치면 생성 시점에 설치 안내 에러가 난다.
    """
    backend = settings.search_backend
    if backend == "memory":
        return InMemorySearchIndex()
    if backend == "postgres":
        # PostgreSQL tsvector(ADR-019) — 운영 어휘 검색 인덱스.
        return PgSearchIndex.from_settings(settings)
    raise ValueError(
        f"알 수 없는 search_backend: {backend!r} (허용: 'memory', 'postgres')"
    )


def build_llm(settings: Settings) -> LLMClient:
    """`llm_backend`에 따라 LLM 클라이언트를 만든다 (기본 `openai_compat`).

    `openai_compat`은 로컬 Gemma 추론 서버(OpenAI 호환)를 호출하는 실구현이다 —
    생성자는 lazy(첫 호출에 연결)라 서버 미기동이어도 조립은 성공한다. 외부 상용
    LLM API는 기본 경로가 아니다(ADR-007 — 민감 자료 외부 전송 금지). fake LLM은
    프로덕션에서 제거됐다 — 테스트는 스텁(`tests/_stub_llm`)을 주입한다.
    """
    backend = settings.llm_backend
    if backend == "openai_compat":
        return OpenAICompatLLM.from_settings(settings)
    raise ValueError(
        f"알 수 없는 llm_backend: {backend!r} (허용: 'openai_compat')"
    )


def build_line_registry() -> LineProviderRegistry:
    """좌표 라인 추출 레지스트리 — 디지털 PDF(pymupdf). 스캔 OCR provider는 후속(ADR-024)."""
    return LineProviderRegistry()


def build_field_extractor(settings: Settings) -> FieldExtractor:
    """필드추출기 — 기본 LLM(gemma=gpt-oss 게이트웨이)으로 구조화 추출(ADR-024 phase 1).

    추출용 LLM은 챗봇 기본 클라이언트와 같은 `build_llm`을 재사용한다(인쇄/디지털
    문서는 텍스트 LLM으로 충분 — 이미지 비전 불요).
    """
    return LlmFieldExtractor(build_llm(settings))


def build_vlm_extractor(settings: Settings) -> VlmFieldExtractor | None:
    """손글씨/스캔 추출기(사내 Qwen3-VL vLLM) — `VLM_BASE_URL` 설정 시에만.

    외부 전송 0(사내망 추론) — 폐쇄망에서 Gemini가 막혀 사내 Qwen3-VL로 대체한다.
    base_url 미설정이면 None을 돌려 서비스가 디지털만 동작시키고 손글씨 요청은 명확한
    에러로 거부하게 한다(조용한 폴백 금지). 챗봇 LLM과 무관.
    """
    if not settings.vlm_base_url:
        return None
    return QwenVlmExtractor(
        settings.vlm_base_url,
        settings.vlm_model,
        api_key=settings.vlm_api_key,
        # AI OCR 성능을 플랫폼 MLflow로 추적(설정 시) — 미설정이면 Noop.
        tracker=MlflowTracker.from_settings(settings),
    )


def build_services(settings: Settings) -> AppServices:
    """선택된 backend로 어댑터를 만들고 유스케이스 서비스로 조립한다.

    같은 `embedder`·`vectorstore`·`graph_store` 인스턴스를 검색·적재 서비스에
    공유 주입한다(적재가 색인한 청크를 검색이 그대로 읽도록 — 일관성).
    """
    embedder = build_embedder(settings)
    vectorstore = build_vectorstore(settings, embedder)
    graph_store = build_graph_store(settings)
    llm_client = build_llm(settings)
    # 같은 search_index 인스턴스를 검색·적재에 공유 주입한다(적재가 색인한 파일을
    # 검색이 그대로 읽도록 — vectorstore/graph 공유 주입과 동일 원칙).
    search_index = build_search_index(settings)

    reranker = build_reranker(settings)
    search = SearchService(
        vectorstore, graph_store, embedder, settings, search_index, reranker=reranker
    )
    rag = RAGService(search, llm_client, settings)
    # 적재 시 검색 요약(description)을 LLM으로 생성하도록 같은 llm_client를 주입한다.
    ingestion = IngestionService(
        vectorstore, graph_store, embedder, search_index, summarizer=llm_client
    )
    diff = DiffService()
    # 헬스체크는 같은 어댑터 인스턴스를 ping해 실제 구성·도달성을 반영한다.
    health = HealthService(
        settings, vectorstore, graph_store, llm_client, search_index
    )
    # 단일 사내 LLM 제공자 — 기본 게이트웨이(gpt-oss-120b). tracer: Langfuse 추적(설정 시)으로
    # 반환 클라이언트를 감싸 호출을 기록(best-effort). (듀얼 LLM provider 라우팅 제거됨.)
    llm_router = LLMRouter(
        default_client=llm_client,
        settings=settings,
        tracer=LangfuseTracer.from_settings(settings),
    )

    return AppServices(
        search=search, rag=rag, ingestion=ingestion, diff=diff, health=health,
        llm_router=llm_router,
    )
