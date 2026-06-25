"""테스트 전역 설정 — ambient `.env`의 backend 선택을 격리한다.

개발용 `.env`는 실 인프라(opensearch/neo4j/sentence_transformers/openai_compat 등)를
가리킬 수 있다. 테스트는 CI(`.env` 없음)와 동일하게 경량 기본(fake/memory)으로 돌아야
하므로, **명시적 셸 export가 없으면** backend 선택 키를 경량값으로 고정한다.

원리: pydantic-settings는 OS 환경 변수를 `.env` 파일보다 우선한다. 이 모듈은 collection
시점(테스트 import 전, 첫 `get_settings()` 호출 전)에 실행되므로, 여기서 `setdefault`로
환경 변수를 심으면 `.env` 파일의 실 backend 값을 덮어쓴다. `setdefault`라 셸에서 직접
export한 값(예: `VECTOR_BACKEND=opensearch pytest ...`)은 존중한다.

integration 테스트는 store를 직접 생성해 검증하므로(Settings backend 경유 안 함) 이
설정의 영향을 받지 않는다 — 서버 미도달이면 각자 skip한다.
"""

import os

# create_app()의 기본 서비스 조립(build_services)이 실 인프라에 붙지 않도록 경량 고정.
_LIGHTWEIGHT_BACKENDS = {
    "EMBEDDER_BACKEND": "fake",
    # LLM은 fake 제거됨 — openai_compat은 lazy(첫 호출에 연결)라 조립만으론 서버
    # 불요. RAG를 실제로 호출하는 테스트는 StubLLM 라우터를 직접 override한다.
    "LLM_BACKEND": "openai_compat",
    "GRAPH_BACKEND": "memory",
    "SEARCH_BACKEND": "memory",
    "VECTOR_BACKEND": "memory",
    # 리랭커는 실모델 로드를 피하려 끈다(현 동작 유지 — build_reranker→None). rerank
    # 경로를 검증하는 테스트는 SearchService에 FakeReranker를 직접 주입한다.
    "RERANKER_ENABLED": "false",
}
for _key, _value in _LIGHTWEIGHT_BACKENDS.items():
    os.environ.setdefault(_key, _value)

# 위 변경이 캐시된 Settings에 반영되도록 무효화(이 시점 이전 호출이 있었을 경우 대비).
from kms.config.settings import get_settings  # noqa: E402

get_settings.cache_clear()
