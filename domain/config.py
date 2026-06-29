"""DeepRAG — Domain Configuration System.

Captures ALL domain-specific knowledge (entity types, regex patterns,
state names, chunk types, LLM prompts, etc.) in a single pluggable config.

Usage:
    from domain.config import DomainConfig, ExtractionConfig, ChunkingConfig
    from domain.loader import load_domain_config, register_domain_config

    # Load a preset
    config = load_domain_config("bcm")

    # Register a custom domain
    custom = DomainConfig(name="medical", ...)
    register_domain_config(custom)
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Sub-configs
# ---------------------------------------------------------------------------

@dataclass
class ExtractionConfig:
    """Regex patterns and keywords for entity & relationship extraction.

    All fields are optional — set to None or "" to disable a pattern.
    """

    # --- Entity patterns ---
    signal_pattern: str = ""
    """Regex for signal/identifier names."""

    state_pattern: str = ""
    """Regex for state names (English)."""

    state_cn_pattern: str = ""
    """Regex for Chinese state markers ("XX状态", "XX模式")."""

    parameter_pattern: str = ""
    """Regex for parameter/config names."""

    fault_pattern: str = ""
    """Regex for fault/DTC patterns."""

    can_id_pattern: str = ""
    """Regex for CAN ID hex values (0xNNN). Set empty to disable."""

    can_message_pattern: str = ""
    """Regex for CAN message names. Set empty to disable."""

    pin_pattern: str = ""
    """Regex for hardware PIN patterns. Set empty to disable."""

    function_title_pattern: str = ""
    """Regex for function description section titles."""

    function_text_pattern: str = ""
    """Regex for function names in text."""

    # --- Relationship patterns ---
    transition_pattern: str = ""
    """Regex for state transition relationships."""

    output_signal_pattern: str = ""
    """Regex for signal output relationships."""

    output_generic_pattern: str = ""
    """Regex for generic output relationships."""

    trigger_pattern: str = ""
    """Regex for trigger condition relationships."""

    trigger_edge_pattern: str = ""
    """Regex for edge-trigger relationships."""

    trigger_kw_pattern: str = ""
    """Regex for trigger keyword relationships."""

    depends_pattern: str = ""
    """Regex for dependency relationships."""

    depends_state_pattern: str = ""
    """Regex for state-dependent relationships."""

    controls_pattern: str = ""
    """Regex for control relationships."""

    controls_enable_pattern: str = ""
    """Regex for enable/disable relationships."""

    requires_pattern: str = ""
    """Regex for requirement relationships."""

    configures_pattern: str = ""
    """Regex for configuration relationships."""

    configures_via_pattern: str = ""
    """Regex for via-configuration relationships."""

    reports_pattern: str = ""
    """Regex for reporting relationships."""

    reports_dtc_pattern: str = ""
    """Regex for DTC reporting relationships."""

    references_pattern: str = ""
    """Regex for cross-reference relationships."""

    references_section_pattern: str = ""
    """Regex for section reference relationships."""

    cross_module_pattern: str = ""
    """Regex for cross-module reference patterns."""

    # --- Classification keywords ---
    state_names: list[str] = field(default_factory=list)
    """State names for heuristic entity classification."""

    function_keywords: list[str] = field(default_factory=list)
    """Substrings that indicate a function entity."""

    power_terminal_names: list[str] = field(default_factory=list)
    """Names that indicate a hardware pin entity."""


@dataclass
class ChunkingConfig:
    """Domain-specific chunking patterns."""

    chunk_type_patterns: dict[str, str] = field(default_factory=dict)
    """Mapping: chunk_type → regex pattern for detection."""

    key_term_patterns: list[str] = field(default_factory=list)
    """Regex patterns for domain-specific key term detection."""

    module_abbrev_map: dict[str, str] = field(default_factory=dict)
    """Mapping: full module name → abbreviation."""

    target_token_min: int = 800
    """Minimum target tokens per chunk."""

    target_token_max: int = 2000
    """Maximum target tokens per chunk."""


@dataclass
class StateMachineConfig:
    """Domain-specific state machine definitions."""

    module_states: dict[str, dict[str, dict]] = field(default_factory=dict)
    """Mapping: module_name → {state_name → {properties}}."""

    section_to_module_map: dict[int, str] = field(default_factory=dict)
    """Mapping: section number → module name (for document-specific chapter layout)."""


@dataclass
class LLMPromptConfig:
    """Domain-specific LLM system prompts."""

    answer_system_prompt: str = ""
    """System prompt for answer generation (Stage 9)."""

    compressor_system_prompt: str = ""
    """System prompt for context compression (Stage 8)."""

    vlm_system_prompt: str = ""
    """System prompt for VLM image analysis."""

    vlm_simple_prompt: str = ""
    """Simplified VLM prompt for faster analysis."""

    agent_system_prompt: str = ""
    """System prompt for DAG agent answer synthesis."""

    agent_company_context: str = ""
    """Company/domain context injected into agent system prompt."""


@dataclass
class IntentConfig:
    """Domain-specific intent analysis configuration."""

    module_aliases: dict[str, str] = field(default_factory=dict)
    """Mapping: user-facing term → canonical module name."""

    query_type_keywords: dict[str, list[str]] = field(default_factory=dict)
    """Mapping: query_type → trigger keywords."""


@dataclass
class DAGTemplateDef:
    """Definition of a DAG reasoning template."""

    name: str = ""
    trigger_keywords: list[str] = field(default_factory=list)
    description: str = ""
    nodes: list[dict] = field(default_factory=list)


@dataclass
class DAGConfig:
    """Domain-specific DAG agent configuration."""

    templates: list[DAGTemplateDef] = field(default_factory=list)
    default_module: str = ""
    state_names: list[str] = field(default_factory=list)
    template_descriptions_prompt: str = ""
    keyword_override_map: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Main DomainConfig
# ---------------------------------------------------------------------------

@dataclass
class DomainConfig:
    """Complete domain configuration for a document type.

    This captures EVERY domain-specific aspect of the RAG system:
    - What entity types exist
    - How to extract them (regex patterns)
    - How to chunk documents
    - What state machines look like
    - What LLM prompts to use
    - How to route queries (DAG templates)

    Usage:
        config = DomainConfig(
            name="bcm",
            display_name="汽车BCM车身控制模块",
            entity_types=["module", "state", "signal", ...],
            extraction=ExtractionConfig(signal_pattern="...", ...),
            ...
        )
    """

    # --- Identity ---
    name: str = ""
    """Short identifier: 'bcm', 'medical', 'legal', etc."""

    display_name: str = ""
    """Human-readable display name."""

    description: str = ""
    """One-line description of the domain."""

    # --- Entity & Relationship types ---
    entity_types: list[str] = field(default_factory=list)
    """Entity types recognized in this domain."""

    relationship_types: list[str] = field(default_factory=list)
    """Relationship types recognized in this domain."""

    # --- Sub-configs ---
    extraction: ExtractionConfig = field(default_factory=ExtractionConfig)
    """Entity & relationship extraction patterns."""

    chunking: ChunkingConfig = field(default_factory=ChunkingConfig)
    """Document chunking configuration."""

    state_machine: StateMachineConfig | None = None
    """State machine definitions. None = no state machine extraction."""

    llm_prompts: LLMPromptConfig = field(default_factory=LLMPromptConfig)
    """LLM system prompts for each pipeline stage."""

    intent: IntentConfig = field(default_factory=IntentConfig)
    """Intent analysis / query routing configuration."""

    dag: DAGConfig = field(default_factory=DAGConfig)
    """DAG agent reasoning configuration."""

    def to_dict(self) -> dict:
        """Serialize to a JSON-compatible dict."""
        from dataclasses import asdict
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "DomainConfig":
        """Deserialize from a dict."""
        return cls(
            name=data.get("name", ""),
            display_name=data.get("display_name", ""),
            description=data.get("description", ""),
            entity_types=data.get("entity_types", []),
            relationship_types=data.get("relationship_types", []),
            extraction=ExtractionConfig(**data.get("extraction", {})),
            chunking=ChunkingConfig(**data.get("chunking", {})),
            state_machine=(
                StateMachineConfig(**data["state_machine"])
                if data.get("state_machine")
                else None
            ),
            llm_prompts=LLMPromptConfig(**data.get("llm_prompts", {})),
            intent=IntentConfig(**data.get("intent", {})),
            dag=DAGConfig(
                templates=[
                    DAGTemplateDef(**t) for t in data.get("dag", {}).get("templates", [])
                ],
                default_module=data.get("dag", {}).get("default_module", ""),
                state_names=data.get("dag", {}).get("state_names", []),
                template_descriptions_prompt=data.get("dag", {}).get("template_descriptions_prompt", ""),
                keyword_override_map=data.get("dag", {}).get("keyword_override_map", {}),
            ),
        )
