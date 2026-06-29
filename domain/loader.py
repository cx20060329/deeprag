"""DeepRAG — Domain Config Loader.

Load, register, and discover domain configurations.
Supports JSON/YAML files and Python preset modules.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from domain.config import DomainConfig

# In-memory registry of domain configs
_registry: dict[str, "DomainConfig"] = {}


def register_domain_config(config: "DomainConfig") -> None:
    """Register a domain config in the runtime registry.

    Args:
        config: DomainConfig to register.

    Raises:
        ValueError: If a domain with the same name is already registered.
    """
    if config.name in _registry:
        raise ValueError(
            f"Domain '{config.name}' is already registered. "
            f"Use unregister_domain_config() first to replace it."
        )
    _registry[config.name] = config


def unregister_domain_config(name: str) -> None:
    """Remove a domain config from the registry."""
    _registry.pop(name, None)


def get_domain_config(name: str) -> "DomainConfig | None":
    """Get a registered domain config by name.

    Returns None if not found.
    """
    return _registry.get(name)


def load_domain_config(name: str) -> "DomainConfig":
    """Load a domain config by name.

    Resolution order:
    1. Already registered in memory
    2. Built-in preset (domain.presets.{name})
    3. JSON file at DEEPRAG_DOMAIN_CONFIG env var path
    4. JSON file at ./domain_configs/{name}.json

    Args:
        name: Domain name (e.g., 'bcm', 'generic').

    Returns:
        DomainConfig instance.

    Raises:
        ValueError: If the domain config cannot be found.
    """
    # 1. In-memory registry
    if name in _registry:
        return _registry[name]

    # 2. Built-in preset
    try:
        if name == "bcm":
            from domain.presets.bcm import BCM_DOMAIN
            _registry[name] = BCM_DOMAIN
            return BCM_DOMAIN
        elif name == "generic":
            from domain.presets.generic import GENERIC_DOMAIN
            _registry[name] = GENERIC_DOMAIN
            return GENERIC_DOMAIN
    except ImportError:
        pass

    # 3. JSON file from env var
    config_path = os.getenv("DEEPRAG_DOMAIN_CONFIG", "")
    if config_path and Path(config_path).exists():
        return load_domain_config_from_file(config_path)

    # 4. JSON file in local directory
    local_path = Path(f"domain_configs/{name}.json")
    if local_path.exists():
        return load_domain_config_from_file(str(local_path))

    raise ValueError(
        f"Domain config '{name}' not found. "
        f"Available presets: {list_domains()}. "
        f"Register custom configs with register_domain_config()."
    )


def load_domain_config_from_file(file_path: str) -> "DomainConfig":
    """Load a domain config from a JSON or YAML file.

    Args:
        file_path: Path to a .json or .yaml file.

    Returns:
        DomainConfig instance.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Domain config file not found: {file_path}")

    if path.suffix in (".yaml", ".yml"):
        try:
            import yaml
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except ImportError:
            raise ImportError("PyYAML is required to load YAML domain configs. Install with: pip install pyyaml")
    else:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)

    from domain.config import DomainConfig
    return DomainConfig.from_dict(data)


def save_domain_config_to_file(config: "DomainConfig", file_path: str) -> None:
    """Save a domain config to a JSON file.

    Args:
        config: DomainConfig to save.
        file_path: Output file path (.json).
    """
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config.to_dict(), f, ensure_ascii=False, indent=2)


def list_domains() -> list[str]:
    """List all available domain config names.

    Includes registered configs, built-in presets, and discovered JSON files.
    """
    names = set(_registry.keys())

    # Built-in presets
    for preset_name in ("bcm", "generic"):
        try:
            load_domain_config(preset_name)
            names.add(preset_name)
        except (ValueError, ImportError):
            pass

    # JSON files in local directory
    local_dir = Path("domain_configs")
    if local_dir.exists():
        for f in local_dir.glob("*.json"):
            names.add(f.stem)

    return sorted(names)


def get_or_create_domain(name: str, **kwargs) -> "DomainConfig":
    """Get an existing domain config or create one with defaults.

    If the domain doesn't exist, creates a minimal DomainConfig with the
    given name and any provided kwargs.

    Args:
        name: Domain name.
        **kwargs: Passed to DomainConfig constructor if creating.

    Returns:
        Existing or new DomainConfig.
    """
    existing = get_domain_config(name)
    if existing:
        return existing

    from domain.config import DomainConfig
    config = DomainConfig(name=name, **kwargs)
    register_domain_config(config)
    return config
