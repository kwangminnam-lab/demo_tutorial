"""환경 변수를 한 곳에서 타입화·검증해 제공하는 설정 모듈.

코드 곳곳에서 `os.getenv`를 직접 부르지 않고 `get_settings()`로 접근한다.
시크릿(비밀번호·키)에는 기본값을 두지 않는다 — 약한 기본값이 운영에 새는 것을 막는다.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """환경 변수/`.env`에서 로드하는 타입화된 애플리케이션 설정."""

    # PostgreSQL (관계형: SSO 계정·access·소스 레지스트리·적재 이력)
    database_url: str

    # 저장 경로 루트 — code-server 공유 PVC 적재(F3/F5)를 위해 설정화한다.
    # 기본값은 현행 하드코딩(`data`)과 동일해 동작 불변(하위 호환). 클러스터에선
    # export_root를 code-server 공유 PVC 마운트 경로로 오버라이드한다.
    # env_prefix가 ""라 다른 필드와 달리 명시 별칭(DOCUX_*)으로 네임스페이스를 둔다.
    data_root: str = Field(default="data", validation_alias="DOCUX_DATA_ROOT")
    export_root: str = Field(
        default="data/export", validation_alias="DOCUX_EXPORT_ROOT"
    )

    # 파싱 OCR 엔진(스캔 PDF·이미지) — 한국어 인식용. docling 기본 RapidOCR은 중국어
    # 모델이라 한글이 한자로 깨진다. "auto"면 가용 엔진을 자동 선택:
    #   ocrmac(Apple Vision, macOS·한글 우수) → easyocr(ko, 크로스플랫폼) → rapidocr(폴백).
    # 클러스터(Linux)에선 DOCUX_PARSE_OCR_ENGINE=easyocr 로 고정(이미지에 easyocr 포함).
    parse_ocr_engine: str = Field(
        default="auto", validation_alias="DOCUX_PARSE_OCR_ENGINE"
    )
    # OCR 언어(콤마구분, 2글자 코드). 엔진별 코드로 매핑된다(ko→ko-KR/ko, en→en-US/en).
    parse_ocr_lang: str = Field(default="ko,en", validation_alias="DOCUX_PARSE_OCR_LANG")

    # 벡터 저장소(청크 임베딩 의미 검색) backend — 기본 memory(InMemory, 서버 불요).
    # postgres면 pgvector k-NN(운영). (OpenSearch backend는 제거됨 — ADR-020.)
    vector_backend: str = "memory"  # memory | postgres

    # backend 선택 (kms.factory) — 기본은 전부 경량(fake/memory)이라 인프라·모델
    # 없이도 앱이 뜨고 테스트가 통과한다. 실구현은 환경 변수로 켠다.
    # fake | sentence_transformers (in-process). (hf_api/openai_compat 임베더는 제거됨 — ADR-020.)
    embedder_backend: str = "memory"
    # 임베딩 모델 — Qwen3-Embedding-0.6B(1024차원, 다국어). 바꾸면 pgvector 컬럼
    # 차원이 달라져 재색인(테이블 재생성)이 필요하다.
    embedding_model_name: str = "Qwen/Qwen3-Embedding-0.6B"
    # 임베딩 디바이스 — None(기본)이면 cuda→mps→cpu 자동 선택. 명시 시 강제("cuda"/"cpu"/"mps").
    embedding_device: str | None = None
    llm_backend: str = "openai_compat"  # openai_compat (로컬 Gemma) — fake 제거됨
    graph_backend: str = "memory"  # memory | neo4j

    # 통합 검색 인덱스 (어휘 검색) backend 선택 — 기본 memory(InMemory, 인프라 불요).
    # postgres면 tsvector(운영). (OpenSearch backend는 제거됨 — ADR-020.)
    search_backend: str = "memory"  # memory | postgres

    # Neo4j (GraphDB: 문서·엔티티 관계) — 시크릿 기본값 없음
    neo4j_uri: str
    neo4j_user: str
    neo4j_password: str

    # 로컬 Gemma 추론 모델 경로 (로컬 서빙 시 — ADR-007)
    llm_model_path: str = "./local_models/gemma_model_e4b_mlx_q4"

    # 기본(gemma 슬롯) LLM = OpenAI 호환 `/v1` 서버. 로컬 서버(localhost:8001) 또는
    # **Groq**(무료 티어, OpenAI 호환) 모두 같은 어댑터로 호출한다(ADR-019). Groq면
    # 배포 env로: LLM_BASE_URL=https://api.groq.com/openai/v1, LLM_API_KEY=<groq키>,
    # LLM_MODEL_NAME=gemma2-9b-it. 로컬 서버는 키 불요.
    llm_base_url: str = "http://localhost:8001/v1"
    llm_model_name: str = "gemma"
    llm_api_key: str | None = None
    # 응답 토큰 상한. thinking을 끄면(아래) reasoning이 토큰을 안 먹으므로 답변에
    # 온전히 쓰인다 — 요약·다문단 답변까지 잘리지 않게 넉넉히 둔다.
    llm_max_tokens: int = 1536
    # Gemma는 reasoning(사고)을 먼저 출력하는 thinking 모델이라, 켜두면 reasoning이
    # 토큰을 소진해 최종 content가 잘리거나 빈다(잘린 개조식 등 "이상한" 답변의 원인).
    # chat_template의 enable_thinking=false로 끄면 답변만 자연스럽게 생성된다.
    # OpenAI 호환 서버(mlx_lm 등)에 `chat_template_kwargs`로 전달한다.
    llm_disable_thinking: bool = True

    # (듀얼 LLM provider=qwen3 설정은 제거됨 — 챗봇은 사내 단일 LLM(기본 게이트웨이)만 사용.)

    # ── Gemini VLM (손글씨/스캔 필드추출 전용, ADR-025) ──
    # ⚠ 외부 전송: 손글씨·스캔 문서를 처리할 때만 페이지 이미지를 Google로 보낸다.
    # 디지털 PDF(기본 경로)는 로컬 LLM만 쓰고 외부 전송 0. key 미설정이면 VLM 비활성
    # (디지털만 동작, 손글씨 요청은 명확한 에러). 키는 코드/git에 넣지 않는다(.env/시크릿).
    # 챗봇 LLM과 무관 — gemini는 챗봇서 제거됨(ADR-023), 여기선 IDP 비전 추출에만 쓴다.
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash"

    # ── 사내 Qwen3-VL VLM (손글씨/스캔 필드추출 — 외부 전송 0) ──
    # 사내 vLLM(OpenAI 호환 `/v1`) 엔드포인트로 페이지 이미지를 보고 필드를 추출한다.
    # Gemini(외부) 대체 — 추론이 사내망에서 끝나 폐쇄망 안전. base_url 미설정이면 VLM
    # 비활성(디지털만 동작, 손글씨 요청은 명확한 에러). 사내 서버라 키 보통 불요.
    # 예: VLM_BASE_URL=http://qwen3-vl-4b.001-doxdemo.svc.cluster.local:8000/v1
    vlm_base_url: str | None = None
    vlm_model: str = "Qwen3-VL-4B-Instruct"
    vlm_api_key: str | None = None

    # 청킹 파라미터 (adapters/ingestion/chunk) — 구조 분할 후 size-cap 2차 분할 기준.
    # 거대 청크는 임베딩·검색 품질을 떨어뜨리므로 size-cap으로 폭을 제한한다.
    chunk_size: int = 800
    chunk_overlap: int = 200
    xlsx_rows_per_chunk: int = 20

    # RRF (Reciprocal Rank Fusion) — 벡터 유사도 + BM25 어휘 매칭의 rank를 합쳐 통합 점수.
    # k=60 표준(논문 권고). 클수록 상위 rank의 우위가 약해지고 평탄해진다.
    rrf_k: int = 60

    # 리랭커 — RRF top-N을 정밀 재정렬. 비활성 시 RRF 결과 그대로 반환.
    # backend: qwen3(Qwen3-Reranker, causal LM) | bge(cross-encoder) | fake(테스트).
    # 모델은 backend와 맞춰야 한다(qwen3↔Qwen/Qwen3-Reranker-*, bge↔BAAI/bge-reranker-*).
    reranker_enabled: bool = True
    reranker_backend: str = "qwen3"
    reranker_model: str = "Qwen/Qwen3-Reranker-0.6B"
    reranker_top_n: int = 20

    # 부서 가중 (ADR-013) — 문서 작성자 부서가 요청자 부서와 같을 때 관련도 점수에
    # 더하는 soft boost. 권한 필터를 통과한 결과의 순위만 바꾼다(노출 여부 불변).
    # 과하면 타 부서의 더 관련 높은 자료가 밀리므로 설정값으로 보정 가능하게 둔다.
    department_boost_weight: float = 0.2

    # SSO (OAuth2/OIDC) — 미설정 환경 허용
    sso_issuer: str | None = None
    sso_client_id: str | None = None

    # 세션 토큰(JWT) 서명 비밀키 (ADR-017 비밀번호 인증). **항상 인증 활성**이므로
    # 필수다 — 미설정이면 기동 거부(약한 기본값 금지, 코드/git 미포함, 시크릿 매니저로 주입).
    jwt_secret: str | None = None
    jwt_expire_minutes: int = 720  # 세션 유효 12시간

    # CORS 허용 오리진 — 프론트(GitHub Pages 등)가 다른 오리진의 백엔드를 호출할 때
    # 필요(ADR-019). 콤마 구분 문자열로 받아 `cors_origins` 프로퍼티가 리스트로 준다.
    # 비우면 CORS 미들웨어 미설치(동일 오리진 전용 — 로컬 dev는 vite proxy로 충분).
    # 예: "https://user.github.io,https://docux.example.com". 와일드카드("*")는 인증
    # 쿠키와 함께 못 쓰므로(브라우저 규칙) 명시 오리진을 권장한다.
    cors_allow_origins: str = ""

    # ── Langfuse LLM 관측(추적) — 플랫폼 Langfuse 활용 (DocuX×Runway LLMOps 데모) ──
    # 챗봇 LLM 호출(provider·model·입출력·지연)을 Langfuse로 추적해 gpt-oss/qwen3 A/B
    # 비교 대시보드를 만든다. enabled+키 설정 시에만 동작 — 추적 실패는 답변을 막지
    # 않는다(best-effort). 키는 시크릿(코드/깃 금지 — env/시크릿 매니저).
    langfuse_enabled: bool = False
    langfuse_base_url: str | None = None          # env LANGFUSE_BASE_URL (예: https://langfuse.try.mrxrunway.ai)
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None

    # ── MLflow: AI OCR(Qwen3-VL) 성능 추적 — 플랫폼 MLflow (DocuX×Runway LLMOps 데모) ──
    # AI OCR 추출 1회당 run 1건을 남겨 지연(render/vlm/ground)·추출품질(fill_rate·신뢰도)을
    # 모델/zoom 설정별로 비교한다. enabled+tracking_uri 설정 시에만 동작 — 추적 실패는
    # 추출을 막지 않는다(best-effort). uri는 in-cluster MLflow svc 또는 게이트웨이 호스트.
    mlflow_enabled: bool = False
    mlflow_tracking_uri: str | None = None        # env MLFLOW_TRACKING_URI
    mlflow_experiment: str = "docux-ai-ocr"

    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    @property
    def cors_origins(self) -> list[str]:
        """`cors_allow_origins`(콤마 구분)를 정규화된 오리진 리스트로 변환."""
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    """프로세스당 1회 로드되는 캐시된 설정 인스턴스를 반환한다."""
    # 값은 환경 변수/`.env`에서 채워지므로 생성자 인자가 없다 (pydantic-settings).
    return Settings()  # type: ignore[call-arg]
