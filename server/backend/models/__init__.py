"""Private Oryntra Control model-discovery adapters.

The frozen model implementations remain in their historical modules.  This
package is intentionally only a registry layer used by the private Control UI.
"""

from .registry import ModelSpec, build_model_config, get_model_spec, list_model_specs

__all__ = ["ModelSpec", "build_model_config", "get_model_spec", "list_model_specs"]
