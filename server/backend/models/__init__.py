"""Private Oryntra model registry.

New research models should register through this package instead of being hard-coded
into the desktop UI. Frozen historical implementation files are intentionally left
in their original locations so old fingerprints/import paths remain reproducible.
"""
from .registry import ModelSpec, build_model_config, get_model_spec, list_model_specs, registry_payload

__all__ = [
    "ModelSpec", "build_model_config", "get_model_spec", "list_model_specs", "registry_payload",
]
