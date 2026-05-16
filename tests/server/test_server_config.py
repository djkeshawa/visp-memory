from llm_memory.config import MemoryConfig, ServerConfig
from llm_memory.server.app import get_cors_options


def test_default_cors_options_are_not_wildcard():
    config = MemoryConfig()

    options = get_cors_options(config)

    assert "*" not in options["allow_origins"]
    assert "http://localhost:3000" in options["allow_origins"]
    assert options["allow_credentials"] is True


def test_wildcard_cors_disables_credentials():
    config = MemoryConfig()
    config.server.cors_origins = ["*"]
    config.server.cors_allow_credentials = True

    options = get_cors_options(config)

    assert options["allow_origins"] == ["*"]
    assert options["allow_credentials"] is False


def test_cors_origins_accept_comma_separated_values():
    config = ServerConfig(cors_origins="https://app.example,http://localhost:3000")

    assert config.cors_origins == [
        "https://app.example",
        "http://localhost:3000",
    ]
