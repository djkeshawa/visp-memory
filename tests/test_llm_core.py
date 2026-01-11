import pytest
from unittest.mock import Mock, patch
from llm_memory.core.llm import create_llm_client, OpenAIClient, AnthropicClient, OllamaClient

import sys
from unittest.mock import MagicMock

# Mock optional dependencies before importing/using them
mock_openai = MagicMock()
mock_anthropic = MagicMock()
mock_ollama = MagicMock()

def test_create_openai_client():
    with patch.dict(sys.modules, {"openai": mock_openai}):
        client = create_llm_client("openai", api_key="sk-test")
        assert isinstance(client, OpenAIClient)

def test_create_anthropic_client():
    with patch.dict(sys.modules, {"anthropic": mock_anthropic}):
        client = create_llm_client("anthropic", api_key="sk-ant-test")
        assert isinstance(client, AnthropicClient)

def test_create_ollama_client():
    with patch.dict(sys.modules, {"ollama": mock_ollama}):
        client = create_llm_client("ollama")
        assert isinstance(client, OllamaClient)

def test_create_unknown_provider():
    with pytest.raises(ValueError, match="Unknown provider"):
        create_llm_client("unknown")

def test_openai_completion():
    with patch.dict(sys.modules, {"openai": mock_openai}):
        # Setup mock response
        mock_instance = mock_openai.OpenAI.return_value
        mock_instance.chat.completions.create.return_value.choices = [
            Mock(message=Mock(content="Hello OpenAI"))
        ]
        
        client = OpenAIClient(api_key="sk-test")
        response = client.completion("Hi")
        assert response == "Hello OpenAI"
        mock_instance.chat.completions.create.assert_called_once()

def test_anthropic_completion():
    with patch.dict(sys.modules, {"anthropic": mock_anthropic}):
        # Setup mock response
        mock_instance = mock_anthropic.Anthropic.return_value
        mock_instance.messages.create.return_value.content = [
            Mock(text="Hello Anthropic")
        ]
        
        client = AnthropicClient(api_key="sk-test")
        response = client.completion("Hi")
        assert response == "Hello Anthropic"

def test_ollama_completion():
    with patch.dict(sys.modules, {"ollama": mock_ollama}):
        # Setup mock response
        mock_ollama.chat.return_value = {"message": {"content": "Hello Ollama"}}
        
        client = OllamaClient()
        response = client.completion("Hi")
        assert response == "Hello Ollama"
