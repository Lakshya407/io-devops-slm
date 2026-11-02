"""LiteLLM client.

The agent talks to the existing LiteLLM gateway through its OpenAI-compatible
API. The model alias is configuration, never code.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx
from openai import AsyncOpenAI, APIStatusError, APITimeoutError, APIConnectionError, RateLimitError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from agent.config import Settings

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


@dataclass
class LLMResponse:
    text: str
    model: str
    latency_ms: int
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class LLMClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = AsyncOpenAI(
            base_url=settings.litellm_base_url,
            api_key=settings.litellm_api_key or "not-needed",
            timeout=settings.llm_timeout_seconds,
            max_retries=0,
        )

    async def aclose(self) -> None:
        await self.client.close()

    @retry(
        retry=retry_if_exception_type((APITimeoutError, APIConnectionError, RateLimitError)),
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=4),
        reraise=True,
    )
    async def _create(self, **kwargs: Any):
        return await self.client.chat.completions.create(**kwargs)

    async def complete(
        self,
        messages: List[Dict[str, str]],
        *,
        max_tokens: int = 900,
        temperature: float = 0.0,
        json_mode: bool = False,
    ) -> LLMResponse:
        if not self.settings.model_is_local and not self.settings.allow_cloud_code_context:
            raise LLMError(
                "the configured model is not marked local; set AGENT_MODEL_IS_LOCAL=true or "
                "AGENT_ALLOW_CLOUD_CODE_CONTEXT=true before sending source code to a cloud provider"
            )
        started = time.monotonic()
        kwargs: Dict[str, Any] = {
            "model": self.settings.agent_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "extra_body": {"num_ctx": self.settings.model_num_ctx},
        }
        try:
            try:
                response = await self._create(
                    **kwargs,
                    response_format={"type": "json_object"} if json_mode else None,
                )
            except APIStatusError as exc:
                # Some Ollama/LiteLLM combinations reject response_format. Fall back
                # to prompt-only JSON and let the parser validate the result.
                if json_mode and exc.status_code in {400, 422}:
                    log.warning("json_mode unsupported by gateway; retrying without response_format")
                    response = await self._create(**kwargs)
                else:
                    raise
        except Exception as exc:  # noqa: BLE001
            log.error("LLM call failed: %s", exc.__class__.__name__)
            raise LLMError(f"model call failed: {exc}") from exc
        latency_ms = int((time.monotonic() - started) * 1000)
        choice = response.choices[0] if response.choices else None
        text = (choice.message.content if choice and choice.message else "") or ""
        usage = response.usage
        result = LLMResponse(
            text=text,
            model=response.model or self.settings.agent_model,
            latency_ms=latency_ms,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            total_tokens=getattr(usage, "total_tokens", 0) or 0,
        )
        log.info(
            "llm model=%s latency_ms=%s tokens=%s/%s",
            result.model, result.latency_ms, result.prompt_tokens, result.completion_tokens,
        )
        return result

    async def health(self) -> Dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(
                    self.settings.litellm_base_url.rstrip("/") + "/models",
                    headers={"Authorization": f"Bearer {self.settings.litellm_api_key}"},
                )
            return {"ok": response.status_code == 200, "status_code": response.status_code}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
