"""DeepRAG — Domain Configuration System.

Provides a pluggable DomainConfig system that captures all domain-specific
knowledge (entity types, regex patterns, state names, chunk types,
LLM prompts, DAG templates) in a single configuration object.

Quick start:
    from domain import load_domain_config, list_domains

    # Load built-in BCM preset
    config = load_domain_config("bcm")

    # List all available domains
    print(list_domains())  # ['bcm', 'generic']

    # Register a custom domain
    from domain.config import DomainConfig
    custom = DomainConfig(name="my_domain", ...)
    register_domain_config(custom)
"""

from domain.config import (
    ChunkingConfig,
    DAGConfig,
    DAGTemplateDef,
    DomainConfig,
    ExtractionConfig,
    IntentConfig,
    LLMPromptConfig,
    StateMachineConfig,
)
from domain.loader import (
    get_domain_config,
    get_or_create_domain,
    list_domains,
    load_domain_config,
    load_domain_config_from_file,
    register_domain_config,
    save_domain_config_to_file,
    unregister_domain_config,
)

__all__ = [
    # Main config
    "DomainConfig",
    "ExtractionConfig",
    "ChunkingConfig",
    "StateMachineConfig",
    "LLMPromptConfig",
    "IntentConfig",
    "DAGConfig",
    "DAGTemplateDef",
    # Loader
    "load_domain_config",
    "load_domain_config_from_file",
    "save_domain_config_to_file",
    "register_domain_config",
    "unregister_domain_config",
    "get_domain_config",
    "get_or_create_domain",
    "list_domains",
]
