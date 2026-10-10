"""A caller's max_output_tokens may lower the operator's cap, never raise it."""

from unittest import mock

import pytest

from visp_memory.config import LLMConfig
from visp_memory.core.model_router import ModelRouter


def _router(cap: int) -> tuple[ModelRouter, mock.Mock]:
    router = ModelRouter(LLMConfig(provider="openai", max_output_tokens=cap))
    router._client = mock.Mock(completion=mock.Mock(return_value="ok"))
    return router, router._client.completion


@pytest.mark.parametrize("requested,expected", [(None, 800), (128, 128), (4000, 800)])
def test_requested_output_tokens_are_capped_by_config(requested, expected):
    router, completion = _router(800)

    router.complete("answer", "hi", max_output_tokens=requested)

    assert completion.call_args.kwargs["max_output_tokens"] == expected
