"""OpenAI 호환 로컬 추론 서버 LLM 어댑터 — `LLMClient` 프로토콜의 실구현(기본 backend).

`Settings.llm_base_url`(OpenAI 호환 `/v1` 포함)·`llm_model_name`으로 로컬
Gemma 추론 서버(Ollama/vLLM 등)의 chat completions를 호출한다. 외부 상용 LLM
API는 쓰지 않는다(ADR-007 — 민감 자료 외부 전송 금지). 로컬 서버는 보통 키가
불요하지만, `llm_api_key`가 설정되면 Bearer로 전달한다. **이 어댑터의 실서버
연결은 테스트하지 않는다** — 인터페이스 일치(프로토콜 만족)만 보장한다.

api_key·base_url 등은 로깅·예외 메시지에 싣지 않는다(CONVENTIONS: 시크릿 무로깅).
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import httpx

from kms.adapters.llm.base import TokenUsage
from kms.config.settings import Settings


def _usage_from(raw: object) -> TokenUsage | None:
    """OpenAI 호환 응답의 `usage` dict → TokenUsage(없거나 빈값이면 None)."""
    if not isinstance(raw, dict):
        return None
    pt = raw.get("prompt_tokens")
    ct = raw.get("completion_tokens")
    tt = raw.get("total_tokens")
    if pt is None and ct is None and tt is None:
        return None
    pt_i = int(pt or 0)
    ct_i = int(ct or 0)
    tt_i = int(tt) if tt is not None else pt_i + ct_i
    return TokenUsage(prompt_tokens=pt_i, completion_tokens=ct_i, total_tokens=tt_i)


class OpenAICompatLLM:
    """OpenAI 호환 로컬 추론 서버 클라이언트. `LLMClient` 프로토콜을 만족한다."""

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        api_key: str | None = None,
        max_tokens: int = 10000,
        timeout: float = 120.0,
        disable_thinking: bool = False,
    ) -> None:
        # base_url은 `/v1`까지 포함한다(Settings 기본 `http://localhost:8001/v1`).
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._max_tokens = max_tokens
        self._timeout = timeout
        self._disable_thinking = disable_thinking

    @classmethod
    def from_settings(cls, settings: Settings) -> "OpenAICompatLLM":
        """Settings의 `llm_*` 값으로 인스턴스를 만든다."""
        return cls(
            settings.llm_base_url,
            settings.llm_model_name,
            api_key=settings.llm_api_key,
            max_tokens=settings.llm_max_tokens,
            disable_thinking=settings.llm_disable_thinking,
        )

    def ping(self, *, timeout: float = 2.0) -> None:
        """추론 서버 도달성 확인 — `GET /models`를 짧은 타임아웃으로 호출한다.

        헬스체크(ping 수준)에서만 쓴다. 무거운 추론을 돌리지 않는다. 도달 불가·
        4xx/5xx면 예외를 던진다(호출자가 잡아 degraded로 표기). base_url·api_key는
        예외 메시지에 노출하지 않도록 호출자가 타입명만 보고한다(시크릿 무로깅).
        """
        with httpx.Client(timeout=timeout) as client:
            response = client.get(f"{self._base_url}/models", headers=self._headers())
            response.raise_for_status()

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        response_format: dict[str, object] | None = None,
    ) -> str:
        text, _ = self.generate_with_usage(
            prompt, system=system, response_format=response_format
        )
        return text

    def generate_with_usage(
        self,
        prompt: str,
        *,
        system: str | None = None,
        response_format: dict[str, object] | None = None,
    ) -> tuple[str, TokenUsage | None]:
        """`generate`와 동일하되 토큰 사용량(usage)도 함께 반환한다(관측용).

        서버가 `usage`를 안 주면 usage=None(추적은 토큰 없이 진행). `generate`는
        이 메서드에 위임하므로 본문 파싱은 한 곳에만 둔다(하위호환: 텍스트만 필요한
        호출자는 `generate`를 그대로 쓴다).
        """
        payload = self._payload(
            prompt, system, stream=False, response_format=response_format
        )
        with httpx.Client(timeout=self._timeout) as client:
            response = client.post(
                f"{self._base_url}/chat/completions",
                json=payload,
                headers=self._headers(),
            )
            response.raise_for_status()
            data = response.json()
        # thinking 모델은 content 없이 reasoning만 줄 수 있다(토큰 소진 등). content가
        # 비면 빈 문자열을 돌려 호출자가 "근거 없음/빈 답"으로 다루게 한다(조용한 실패
        # 아님 — KeyError로 죽지 않게 방어하되 reasoning은 답변으로 쓰지 않는다).
        content = data["choices"][0]["message"].get("content")
        text = str(content) if content else ""
        return text, _usage_from(data.get("usage"))

    def stream(self, prompt: str, *, system: str | None = None) -> Iterator[str]:
        # 텍스트 조각만 필요한 호출자용(하위호환) — usage 항목은 걸러낸다.
        for item in self.stream_with_usage(prompt, system=system):
            if isinstance(item, str):
                yield item

    def stream_with_usage(
        self, prompt: str, *, system: str | None = None
    ) -> Iterator[str | TokenUsage]:
        """텍스트 조각을 순서대로 yield하고, 마지막에 누적 usage(있으면)를 1건 yield한다.

        `stream_options.include_usage=true`로 서버가 마지막 청크에 usage를 싣게 한다.
        호출자(TracedLLMClient)는 str 조각만 답변으로 쓰고 TokenUsage는 관측에 쓴다.
        """
        payload = self._payload(prompt, system, stream=True)
        with httpx.Client(timeout=self._timeout) as client:
            with client.stream(
                "POST",
                f"{self._base_url}/chat/completions",
                json=payload,
                headers=self._headers(),
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    content, usage = self._parse_sse(line)
                    if content:
                        yield content
                    if usage is not None:
                        yield usage

    def _payload(
        self,
        prompt: str,
        system: str | None,
        *,
        stream: bool,
        response_format: dict[str, object] | None = None,
    ) -> dict[str, object]:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        payload: dict[str, object] = {
            "model": self._model,
            "messages": messages,
            "stream": stream,
            "max_tokens": self._max_tokens,
        }
        if stream:
            # 마지막 청크에 누적 usage(토큰)를 실어 보내게 요청 — Langfuse Usage/Cost용.
            payload["stream_options"] = {"include_usage": True}
        if response_format is not None:
            # OpenAI 호환 구조화 출력(예: {"type":"json_object"}). 서버가 지원하면
            # JSON을 강제한다. mlx_lm 등 미지원 서버는 무시할 수 있어 호출자가 방어
            # 파싱한다(조용한 실패 아님 — 무시돼도 본문은 여전히 텍스트로 옴).
            payload["response_format"] = response_format
        if self._disable_thinking:
            # Gemma 등 thinking 모델의 reasoning 출력을 끈다 — reasoning이 토큰을
            # 소진해 답변이 잘리는 것을 막는다. OpenAI 호환 서버(mlx_lm 등)가 chat
            # 템플릿에 그대로 전달한다.
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        return payload

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    @staticmethod
    def _parse_sse(line: str) -> tuple[str | None, TokenUsage | None]:
        """OpenAI 호환 SSE 한 줄 → (델타 콘텐츠 조각, usage). 둘 다 없으면 (None, None).

        include_usage 시 마지막 usage 청크는 choices가 비고 usage만 온다 → 콘텐츠는
        None, usage만 반환. 일반 청크는 그 반대.
        """
        if not line or not line.startswith("data:"):
            return None, None
        data = line[len("data:") :].strip()
        if not data or data == "[DONE]":
            return None, None
        payload = json.loads(data)
        choices = payload.get("choices") or []
        content: str | None = None
        if choices:
            delta = choices[0].get("delta", {})
            raw = delta.get("content")
            content = raw if raw else None
        return content, _usage_from(payload.get("usage"))
