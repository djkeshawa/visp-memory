"""
Configuration management for LLM Memory.

Supports:
- File-based configuration (.llm-memory/config.yaml)
- Environment variables
- Programmatic configuration
"""

from pathlib import Path
from typing import Literal, Optional
from pydantic import Field
from pydantic_settings import BaseSettings
import json


class EmbeddingConfig(BaseSettings):
    """Embedding provider configuration."""

    provider: Literal["sentence-transformers", "openai", "ollama", "custom"] = "sentence-transformers"
    model: str = "all-MiniLM-L6-v2"
    api_key: Optional[str] = Field(default=None, env="EMBEDDING_API_KEY")
    api_base: Optional[str] = Field(default=None, env="EMBEDDING_API_BASE")

    class Config:
        env_prefix = "LLM_MEMORY_EMBEDDING_"


class StorageConfig(BaseSettings):
    """Storage configuration."""

    data_dir: Path = Path(".llm-memory/data")
    vector_db: Literal["chroma", "memory"] = "chroma"

    class Config:
        env_prefix = "LLM_MEMORY_STORAGE_"


class CompressionConfig(BaseSettings):
    """Memory compression configuration."""

    # When to compress episodic memories to semantic
    episodic_threshold: int = 10  # After N related episodes

    # How aggressively to compress (0.0 = keep detail, 1.0 = maximum compression)
    compression_level: float = 0.5

    # LLM to use for compression (if available)
    llm_provider: Optional[Literal["openai", "ollama", "anthropic"]] = None
    llm_model: Optional[str] = None

    class Config:
        env_prefix = "LLM_MEMORY_COMPRESSION_"


class CaptureConfig(BaseSettings):
    """Automatic capture configuration."""

    # Git capture settings
    git_enabled: bool = True
    git_auto_install_hooks: bool = False
    git_parse_conventional_commits: bool = True

    # Test capture settings
    tests_enabled: bool = False
    tests_pytest_plugin: bool = False

    # Conversation capture settings
    conversation_enabled: bool = False  # Requires explicit opt-in

    class Config:
        env_prefix = "LLM_MEMORY_CAPTURE_"


class RecallConfig(BaseSettings):
    """Proactive recall configuration."""

    proactive: bool = True
    file_triggered: bool = True
    error_matching: bool = True

    class Config:
        env_prefix = "LLM_MEMORY_RECALL_"


class AnalysisConfig(BaseSettings):
    """Pattern detection and analysis configuration."""

    pattern_detection: bool = True
    auto_extract: bool = True
    run_interval_hours: int = 24

    class Config:
        env_prefix = "LLM_MEMORY_ANALYSIS_"


class QualityConfig(BaseSettings):
    """Memory quality management configuration."""

    deduplication: bool = True
    similarity_threshold: float = 0.9
    conflict_detection: bool = True

    class Config:
        env_prefix = "LLM_MEMORY_QUALITY_"


class FeedbackConfig(BaseSettings):
    """Feedback and validation configuration."""

    collection: bool = True
    auto_validate: bool = True
    validate_interval_hours: int = 168  # Weekly

    class Config:
        env_prefix = "LLM_MEMORY_FEEDBACK_"


class MemoryConfig(BaseSettings):
    """Main configuration for LLM Memory system."""

    # Project identification
    project_name: str = "default"
    project_type: Literal["code", "writing", "research", "general"] = "general"

    # Sub-configurations
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
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

    class Config:
        env_prefix = "LLM_MEMORY_"

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

        return cls(**data)

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
