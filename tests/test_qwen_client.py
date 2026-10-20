from types import SimpleNamespace

import pytest

from eval_tool.qwen_client import QwenLLM


class _SyncCompletions:
    def create(self, **kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="plain text"))]
        )


class _AsyncCompletions:
    async def create(self, **kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="async text"))]
        )


def _llm_without_network() -> QwenLLM:
    llm = object.__new__(QwenLLM)
    llm.model_name = "test-model"
    llm.max_tokens = 100
    llm.temperature = 0.0
    llm.client = SimpleNamespace(
        chat=SimpleNamespace(completions=_SyncCompletions())
    )
    llm.async_client = SimpleNamespace(
        chat=SimpleNamespace(completions=_AsyncCompletions())
    )
    return llm


def test_generate_without_schema_returns_plain_text():
    assert _llm_without_network().generate("prompt") == "plain text"


@pytest.mark.asyncio
async def test_async_generate_without_schema_returns_plain_text():
    assert await _llm_without_network().a_generate("prompt") == "async text"
