"""
Configuration management for LLM Memory.

Supports:
- File-based configuration (.llm-memory/config.yaml)
- Environment variables
- Programmatic configuration
"""

import json
import os
from pathlib import Path
from typing import Any, List, Literal, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class EmbeddingConfig(BaseSettings):
    """Embedding provider configuration."""

    model_config = SettingsConfigDict(env_prefix="LLM_MEMORY_EMBEDDING_", populate_by_name=True)

    provider: Literal[
        "auto",
        "sentence-transformers",
        "openai",
        "ollama",
        "custom",
        "none",
        "noop",
    ] = "sentence-transformers"
    model: str = "all-MiniLM-L6-v2"
    api_key: Optional[str] = Field(default=None, validation_alias="EMBEDDING_API_KEY")
    api_base: Optional[str] = Field(default=None, validation_alias="EMBEDDING_API_BASE")


class StorageConfig(BaseSettings):
    """Storage configuration."""

    model_config = SettingsConfigDict(env_prefix="LLM_MEMORY_STORAGE_", populate_by_name=True)

    data_dir: Path = Path(".llm-memory/data")
    vector_db: Literal["chroma", "memory"] = "chroma"
    backend: Literal["sqlite", "neo4j"] = "sqlite"  # Default to sqlite for ease of use

    # Client-Server Mode
    mode: Literal["local", "client", "server"] = "local"
    server_url: str = "http://localhost:8000"
    api_key: Optional[str] = Field(default=None, validation_alias="LLM_MEMORY_API_KEY")
    jwt_token: Optional[str] = Field(default=None, validation_alias="LLM_MEMORY_JWT_TOKEN")

    # Neo4j Configuration
    neo4j_uri: str = Field(default="bolt://localhost:7687", validation_alias="NEO4J_URI")
    neo4j_user: str = Field(default="neo4j", validation_alias="NEO4J_USER")
    neo4j_password: str = Field(default="", validation_alias="NEO4J_PASSWORD")


class CompressionConfig(BaseSettings):
    """Memory compression configuration."""

    model_config = SettingsConfigDict(env_prefix="LLM_MEMORY_COMPRESSION_", populate_by_name=True)

    # When to compress episodic memories to semantic
    episodic_threshold: int = 10  # After N related episodes

    # How aggressively to compress (0.0 = keep detail, 1.0 = maximum compression)
    compression_level: float = 0.5

    # LLM to use for compression (if available)
    llm_provider: Optional[Literal["openai", "ollama", "anthropic"]] = None
    llm_model: Optional[str] = None


class CaptureConfig(BaseSettings):
    """Automatic capture configuration."""

    model_config = SettingsConfigDict(env_prefix="LLM_MEMORY_CAPTURE_", populate_by_name=True)

    # Git capture settings
    git_enabled: bool = True
    git_auto_install_hooks: bool = False
    git_parse_conventional_commits: bool = True

    # Test capture settings
    tests_enabled: bool = False
    tests_pytest_plugin: bool = False

    # Conversation capture settings
    conversation_enabled: bool = False  # Requires explicit opt-in
    llm_provider: Optional[Literal["openai", "ollama", "anthropic"]] = None
    llm_model: Optional[str] = None


class RecallConfig(BaseSettings):
    """Proactive recall configuration."""

    model_config = SettingsConfigDict(env_prefix="LLM_MEMORY_RECALL_", populate_by_name=True)

    proactive: bool = True
    file_triggered: bool = True
    error_matching: bool = True


class AnalysisConfig(BaseSettings):
    """Pattern detection and analysis configuration."""

    model_config = SettingsConfigDict(env_prefix="LLM_MEMORY_ANALYSIS_", populate_by_name=True)

    pattern_detection: bool = True
    auto_extract: bool = True
    run_interval_hours: int = 24


class QualityConfig(BaseSettings):
    """Memory quality management configuration."""

    model_config = SettingsConfigDict(env_prefix="LLM_MEMORY_QUALITY_", populate_by_name=True)

    deduplication: bool = True
    similarity_threshold: float = 0.9
    conflict_detection: bool = True


class FeedbackConfig(BaseSettings):
    """Feedback and validation configuration."""

    model_config = SettingsConfigDict(env_prefix="LLM_MEMORY_FEEDBACK_", populate_by_name=True)

    collection: bool = True
    auto_validate: bool = True
    validate_interval_hours: int = 168  # Weekly


class ServerConfig(BaseSettings):
    """Server configuration for shared mode."""

    model_config = SettingsConfigDict(env_prefix="LLM_MEMORY_SERVER_", populate_by_name=True)

    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: List[str] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:8000",
            "http://127.0.0.1:8000",
        ]
    )
    cors_allow_credentials: bool = True

    # Authentication
    auth_enabled: bool = True
    jwt_secret: str = Field(default="", validation_alias="LLM_MEMORY_JWT_SECRET")
    jwt_algorithm: str = "HS256"
    jwt_expiry_hours: int = 24

    # API Key fallback (backward compatible)
    api_keys: List[str] = Field(default_factory=list)

    # Multi-tenancy
    allow_anonymous: bool = False
    default_team: Optional[str] = None

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: Any) -> List[str]:
        """Accept JSON-style lists or comma-separated env/config values."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


class MemoryConfig(BaseSettings):
    """Main configuration for LLM Memory system."""

    model_config = SettingsConfigDict(env_prefix="LLM_MEMORY_", populate_by_name=True)

    # Project identification
    project_name: str = "default"
    project_type: Literal["code", "writing", "research", "general"] = "general"
    repo_id: Optional[str] = Field(default=None, validation_alias="LLM_MEMORY_REPO_ID")

    # Sub-configurations
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    compression: CompressionConfig = Field(default_factory=CompressionConfig)
    capture: CaptureConfig = Field(default_factory=CaptureConfig)
    recall: RecallConfig = Field(default_factory=RecallConfig)
    analysis: AnalysisConfig = Field(default_factory=AnalysisConfig)
    quality: QualityConfig = Field(default_factory=QualityConfig)
    feedback: FeedbackConfig = Field(default_factory=FeedbackConfig)

    # Memory behavior
    auto_compress: bool = True  # Automatically compress old memories
    decay_enabled: bool = True  # Let unused memories fade
    decay_halflife_days: int = 30  # How quickly memories fade

    @classmethod
    def from_file(cls, path: Path) -> "MemoryConfig":
        """Load configuration from YAML or JSON file."""
        if not path.exists():
            return cls()

        content = path.read_text()

        if path.suffix in (".yaml", ".yml"):
            try:
                import yaml

                data = yaml.safe_load(content)
            except ImportError:
                raise ImportError("PyYAML required for YAML config: pip install pyyaml")
        else:
            data = json.loads(content)

        config = cls(**data)
        config.apply_env_overrides()
        return config

    @classmethod
    def find_and_load(cls, start_dir: Path = None) -> "MemoryConfig":
        """Find config file by walking up directory tree."""
        start_dir = start_dir or Path.cwd()

        for parent in [start_dir] + list(start_dir.parents):
            for name in ("llm-memory.yaml", "llm-memory.json", ".llm-memory/config.yaml"):
                config_path = parent / name
                if config_path.exists():
                    config = cls.from_file(config_path)
                    # Set data_dir relative to config location
                    if not config.storage.data_dir.is_absolute():
                        config.storage.data_dir = parent / config.storage.data_dir
                    return config

        return cls()

    def apply_env_overrides(self):
        """Apply deployment environment overrides after file-based config loading."""
        env_overrides = {
            "LLM_MEMORY_REPO_ID": ("repo_id",),
            "LLM_MEMORY_STORAGE_DATA_DIR": ("storage", "data_dir"),
            "LLM_MEMORY_STORAGE_BACKEND": ("storage", "backend"),
            "LLM_MEMORY_STORAGE_MODE": ("storage", "mode"),
            "LLM_MEMORY_STORAGE_SERVER_URL": ("storage", "server_url"),
            "LLM_MEMORY_API_KEY": ("storage", "api_key"),
            "LLM_MEMORY_JWT_TOKEN": ("storage", "jwt_token"),
            "NEO4J_URI": ("storage", "neo4j_uri"),
            "NEO4J_USER": ("storage", "neo4j_user"),
            "NEO4J_PASSWORD": ("storage", "neo4j_password"),
            "LLM_MEMORY_EMBEDDING_PROVIDER": ("embedding", "provider"),
            "LLM_MEMORY_EMBEDDING_MODEL": ("embedding", "model"),
            "EMBEDDING_API_KEY": ("embedding", "api_key"),
            "EMBEDDING_API_BASE": ("embedding", "api_base"),
            "LLM_MEMORY_SERVER_AUTH_ENABLED": ("server", "auth_enabled"),
            "LLM_MEMORY_JWT_SECRET": ("server", "jwt_secret"),
            "LLM_MEMORY_SERVER_CORS_ORIGINS": ("server", "cors_origins"),
            "LLM_MEMORY_SERVER_CORS_ALLOW_CREDENTIALS": (
                "server",
                "cors_allow_credentials",
            ),
            "LLM_MEMORY_SERVER_API_KEYS": ("server", "api_keys"),
            "LLM_MEMORY_SERVER_ALLOW_ANONYMOUS": ("server", "allow_anonymous"),
            "LLM_MEMORY_SERVER_DEFAULT_TEAM": ("server", "default_team"),
            "LLM_MEMORY_SERVER_JWT_EXPIRY_HOURS": ("server", "jwt_expiry_hours"),
        }

        for env_name, path in env_overrides.items():
            if env_name not in os.environ:
                continue

            target = self
            for attr in path[:-1]:
                target = getattr(target, attr)
            value = os.environ[env_name]
            if path == ("storage", "data_dir"):
                value = Path(value)
            elif path in {
                ("server", "auth_enabled"),
                ("server", "cors_allow_credentials"),
                ("server", "allow_anonymous"),
            }:
                value = value.lower() in {"1", "true", "yes", "on"}
            elif path == ("server", "jwt_expiry_hours"):
                value = int(value)
            elif path == ("server", "cors_origins"):
                value = ServerConfig.parse_cors_origins(value)
            elif path == ("server", "api_keys"):
                value = ServerConfig.parse_cors_origins(value)
            setattr(target, path[-1], value)

    def save(self, path: Path):
        """Save configuration to file."""
        path.parent.mkdir(parents=True, exist_ok=True)

        data = self.model_dump()

        # Convert Path objects to strings
        def convert_paths(obj):
            if isinstance(obj, dict):
                return {k: convert_paths(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [convert_paths(v) for v in obj]
            elif isinstance(obj, Path):
                return str(obj)
            return obj

        data = convert_paths(data)

        if path.suffix in (".yaml", ".yml"):
            try:
                import yaml

                content = yaml.dump(data, default_flow_style=False)
            except ImportError:
                raise ImportError("PyYAML required for YAML config: pip install pyyaml")
        else:
            content = json.dumps(data, indent=2)

        path.write_text(content)


def load_config(start_dir: Path = None) -> "MemoryConfig":
    """Helper function to load configuration."""
    return MemoryConfig.find_and_load(start_dir)
