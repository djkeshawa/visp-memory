"""Task-aware LLM routing with optional MCP client sampling."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any, Literal, Optional

from llm_memory.config import LLMConfig
from llm_memory.core.llm import create_llm_client

ModelTask = Literal[
    "extraction",
    "reconciliation",
    "consolidation",
    "merge_suggestion",
    "reflection",
    "intent_verification",
    "reranking",
    "answer",
]


class ModelUnavailableError(RuntimeError):
    """Raised when a task requires a model but no route is configured."""


class ModelRouter:
    """Route memory tasks to client sampling or a configured provider."""

    def __init__(self, config: LLMConfig):
        self.config = config
        self._client = None

    @property
    def configured(self) -> bool:
        return self.config.provider != "none"

    def status(self) -> dict[str, Any]:
        return {
            "provider": self.config.provider,
            "model": self.config.model,
            "configured": self.configured,
            "supports_client_sampling": True,
            "timeout_seconds": self.config.timeout_seconds,
            "max_output_tokens": self.config.max_output_tokens,
            "tasks": [
                "extraction",
                "reconciliation",
                "consolidation",
                "merge_suggestion",
                "reflection",
                "intent_verification",
                "reranking",
                "answer",
            ],
        }

    def complete(
        self,
        task: ModelTask,
        prompt: str,
        *,
        system_prompt: Optional[str] = None,
        sampling_callback: Optional[Callable[[str, str], str]] = None,
    ) -> dict[str, str]:
        if sampling_callback is not None:
            return {
                "text": sampling_callback(prompt, system_prompt or ""),
                "provider": "mcp-sampling",
                "model": "client-selected",
                "task": task,
            }
        if not self.configured:
            raise ModelUnavailableError("No server-side LLM provider is configured")
        if self._client is None:
            api_key = self.config.api_key or self._provider_api_key(self.config.provider)
            self._client = create_llm_client(
                self.config.provider,
                model=self.config.model,
                api_key=api_key,
                base_url=self.config.base_url,
            )
        text = self._client.completion(
            prompt,
            system_prompt=system_prompt,
            max_output_tokens=self.config.max_output_tokens,
            timeout=self.config.timeout_seconds,
        )
        return {
            "text": text,
            "provider": self.config.provider,
            "model": self.config.model or getattr(self._client, "model", "default"),
            "task": task,
        }

    def complete_json(
        self,
        task: ModelTask,
        prompt: str,
        *,
        system_prompt: Optional[str] = None,
        sampling_callback: Optional[Callable[[str, str], str]] = None,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        result = self.complete(
            task,
            prompt,
            system_prompt=system_prompt,
            sampling_callback=sampling_callback,
        )
        text = result["text"].strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise ValueError("Model returned JSON that is not an object")
        return payload, result

    @staticmethod
    def _provider_api_key(provider: str) -> Optional[str]:
        if provider == "openai":
            return os.environ.get("OPENAI_API_KEY")
        if provider == "anthropic":
            return os.environ.get("ANTHROPIC_API_KEY")
        return None
