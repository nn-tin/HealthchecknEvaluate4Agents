from __future__ import annotations

import os
from typing import TypeVar, overload

import instructor
from deepeval.models import DeepEvalBaseLLM
from openai import AsyncOpenAI, OpenAI
from pydantic import BaseModel


SchemaT = TypeVar("SchemaT", bound=BaseModel)


class QwenLLM(DeepEvalBaseLLM):
    """OpenAI-compatible Qwen adapter usable by DeepEval and the MVP services."""

    def __init__(
        self,
        *,
        model_name: str,
        base_url: str,
        api_key: str,
        max_tokens: int = 8192,
        timeout_seconds: float = 300.0,
        temperature: float = 0.0,
    ):
        if not base_url:
            raise ValueError("QWEN_BASE_URL is required")
        if not api_key:
            raise ValueError("QWEN_API_KEY is required")
        normalized = base_url.rstrip("/")
        if not normalized.endswith("/v1"):
            normalized = f"{normalized}/v1"
        self.model_name = model_name
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.client = OpenAI(
            base_url=normalized,
            api_key=api_key,
            timeout=timeout_seconds,
            max_retries=1,
        )
        self.async_client = AsyncOpenAI(
            base_url=normalized,
            api_key=api_key,
            timeout=timeout_seconds,
            max_retries=1,
        )

    def load_model(self):
        return self.client

    def get_model_name(self) -> str:
        return self.model_name

    @staticmethod
    def _extra_kwargs() -> dict:
        return {
            "extra_body": {
                "chat_template_kwargs": {"enable_thinking": False},
            }
        }

    @overload
    def generate(self, prompt: str) -> str: ...

    @overload
    def generate(self, prompt: str, schema: type[SchemaT]) -> SchemaT: ...

    def generate(
        self,
        prompt: str,
        schema: type[SchemaT] | None = None,
    ) -> SchemaT | str:
        if schema is None:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                **self._extra_kwargs(),
            )
            return response.choices[0].message.content or ""

        last_error: Exception | None = None
        for mode in _instructor_modes():
            client = instructor.from_openai(self.client, mode=mode)
            try:
                return client.chat.completions.create(  # type: ignore[return-value]
                    model=self.model_name,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                    response_model=schema,
                    max_retries=3,
                    **self._extra_kwargs(),
                )
            except Exception as exc:
                last_error = exc
        raise last_error or RuntimeError("Qwen structured generation failed")

    @overload
    async def a_generate(self, prompt: str) -> str: ...

    @overload
    async def a_generate(self, prompt: str, schema: type[SchemaT]) -> SchemaT: ...

    async def a_generate(
        self,
        prompt: str,
        schema: type[SchemaT] | None = None,
    ) -> SchemaT | str:
        if schema is None:
            response = await self.async_client.chat.completions.create(
                model=self.model_name,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                **self._extra_kwargs(),
            )
            return response.choices[0].message.content or ""

        last_error: Exception | None = None
        for mode in _instructor_modes():
            client = instructor.from_openai(self.async_client, mode=mode)
            try:
                return await client.chat.completions.create(  # type: ignore[return-value]
                    model=self.model_name,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                    response_model=schema,
                    max_retries=3,
                    **self._extra_kwargs(),
                )
            except Exception as exc:
                last_error = exc
        raise last_error or RuntimeError("Qwen structured generation failed")


def _instructor_modes() -> list[instructor.Mode]:
    configured = os.getenv("INSTRUCTOR_MODE", "JSON_SCHEMA").strip().upper()
    primary = getattr(instructor.Mode, configured, instructor.Mode.JSON_SCHEMA)
    return [primary] if primary == instructor.Mode.JSON else [primary, instructor.Mode.JSON]


class QwenClient:
    def __init__(self, llm: QwenLLM):
        self.llm = llm

    def structured(self, prompt: str, schema: type[SchemaT]) -> SchemaT:
        return self.llm.generate(prompt, schema)
