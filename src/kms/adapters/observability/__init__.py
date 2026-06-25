"""관측(observability) 어댑터 — LLM 호출 추적(Langfuse).

`Tracer` 프로토콜 + `LangfuseTracer`(실구현) + `NoopTracer`(미설정/테스트). 추적은
best-effort — 실패해도 답변 생성을 막지 않는다(조용한 실패 아님: 사유 로깅).
"""

from kms.adapters.observability.base import Tracer
from kms.adapters.observability.langfuse_tracer import LangfuseTracer, NoopTracer

__all__ = ["Tracer", "LangfuseTracer", "NoopTracer"]
