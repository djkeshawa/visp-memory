"""
LLM Client Factory

Centralizes the creation of LLM clients (OpenAI, Anthropic, Ollama)
to be used across the system (compression, capture, conflict detection).
"""

from typing import Protocol


class LLMClient(Protocol):
    """Protocol for LLM interactions."""

    def completion(self, prompt: str, system_prompt: str = None, **kwargs) -> str: ...


class OpenAIClient:
    def __init__(self, api_key: str = None, model: str = "gpt-4o-mini", base_url: str = None):
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError("openai required: pip install openai")
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    def completion(self, prompt: str, system_prompt: str = None, **kwargs) -> str:
        max_output_tokens = kwargs.pop("max_output_tokens", kwargs.pop("max_tokens", None))
        request = {"model": self.model, "input": prompt, **kwargs}
        if system_prompt:
            request["instructions"] = system_prompt
        if max_output_tokens:
            request["max_output_tokens"] = max_output_tokens
        response = self.client.responses.create(**request)
        return response.output_text.strip()


class AnthropicClient:
    def __init__(self, api_key: str = None, model: str = "claude-3-haiku-20240307"):
        try:
            from anthropic import Anthropic
        except ImportError:
            raise ImportError("anthropic required: pip install anthropic")
        self.client = Anthropic(api_key=api_key)
        self.model = model

    def completion(self, prompt: str, system_prompt: str = None, **kwargs) -> str:
        messages = [{"role": "user", "content": prompt}]
        max_tokens = kwargs.pop("max_output_tokens", kwargs.pop("max_tokens", 800))

        response = self.client.messages.create(
            model=self.model,
            system=system_prompt or "",
            messages=messages,
            max_tokens=max_tokens,
            **kwargs,
        )
        return response.content[0].text.strip()


class OllamaClient:
    def __init__(self, model: str = "llama3.2"):
        try:
            import ollama
        except ImportError:
            raise ImportError("ollama required: pip install ollama")
        self.model = model
        self.client = ollama

    def completion(self, prompt: str, system_prompt: str = None, **kwargs) -> str:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        max_tokens = kwargs.pop("max_output_tokens", kwargs.pop("max_tokens", None))
        kwargs.pop("timeout", None)
        if max_tokens:
            options = dict(kwargs.pop("options", {}) or {})
            options["num_predict"] = max_tokens
            kwargs["options"] = options
        response = self.client.chat(model=self.model, messages=messages, **kwargs)
        return response["message"]["content"].strip()


def create_llm_client(
    provider: str, model: str = None, api_key: str = None, base_url: str = None
) -> LLMClient:
    """Factory to create an LLM client."""
    if provider == "openai":
        return OpenAIClient(api_key=api_key, model=model or "gpt-5.4-mini", base_url=base_url)
    elif provider == "anthropic":
        return AnthropicClient(api_key=api_key, model=model or "claude-3-haiku-20240307")
    elif provider == "ollama":
        return OllamaClient(model=model or "llama3.2")
    else:
        raise ValueError(f"Unknown provider: {provider}")
