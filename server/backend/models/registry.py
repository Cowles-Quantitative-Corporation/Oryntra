"""Dynamic private model registry used by Oryntra Control.

A model is Control-testable only when a module in ``backend.models`` exports a
``MODEL_SPECS`` iterable whose members expose an id and a builder.  This avoids
mistaking every backend helper module for a tradable model while making future
models appear in Control without a UI code change.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import importlib
import pkgutil
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class ModelSpec:
    id: str
    label: str
    description: str
    builder: Callable[..., Any]
    family: str = "universal"
    status: str = "research"
    generation: str = "unknown"
    default: bool = False
    supports_phase2: bool = True
    supports_phase3: bool = True
    supports_phase4: bool = True
    role: str = "portfolio"
    source_module: str = ""

    def public_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out.pop("builder", None)
        return out


_CACHE: dict[str, ModelSpec] | None = None


def _coerce(raw: Any, module_name: str) -> ModelSpec:
    if isinstance(raw, ModelSpec):
        return ModelSpec(**{**raw.__dict__, "source_module": raw.source_module or module_name})
    values = raw.__dict__ if hasattr(raw, "__dict__") else dict(raw)
    return ModelSpec(
        id=str(values["id"]).strip().lower(),
        label=str(values["label"]),
        description=str(values.get("description") or ""),
        builder=values["builder"],
        family=str(values.get("family") or "universal"),
        status=str(values.get("status") or "research"),
        generation=str(values.get("generation") or "unknown"),
        default=bool(values.get("default", False)),
        supports_phase2=bool(values.get("supports_phase2", True)),
        supports_phase3=bool(values.get("supports_phase3", True)),
        supports_phase4=bool(values.get("supports_phase4", True)),
        role=str(values.get("role") or "portfolio"),
        source_module=module_name,
    )


def _discover() -> dict[str, ModelSpec]:
    package = importlib.import_module(__package__)
    found: dict[str, ModelSpec] = {}
    for module_info in pkgutil.iter_modules(package.__path__):
        if module_info.name.startswith("_") or module_info.name in {"registry"}:
            continue
        module_name = f"{__package__}.{module_info.name}"
        module = importlib.import_module(module_name)
        for raw in getattr(module, "MODEL_SPECS", ()):
            spec = _coerce(raw, module_name)
            if not spec.id or spec.id in found:
                raise RuntimeError(f"Duplicate/invalid Control model id: {spec.id!r}")
            if not callable(spec.builder):
                raise RuntimeError(f"Model {spec.id} has no callable builder")
            found[spec.id] = spec
    if not found:
        raise RuntimeError("No Oryntra Control models were discovered")
    return found


def _models(refresh: bool = False) -> dict[str, ModelSpec]:
    global _CACHE
    if _CACHE is None or refresh:
        _CACHE = _discover()
    return _CACHE


def list_model_specs(*, refresh: bool = False) -> list[ModelSpec]:
    values = list(_models(refresh).values())
    return sorted(values, key=lambda x: (0 if x.default else 1, x.generation, x.label.lower()))


def get_model_spec(model_id: str) -> ModelSpec:
    key = str(model_id or "").strip().lower()
    try:
        return _models()[key]
    except KeyError as exc:
        raise ValueError(f"Unknown registered model: {model_id}") from exc


def build_model_config(model_id: str, **overrides: Any) -> Any:
    spec = get_model_spec(model_id)
    return spec.builder(**overrides)


def registry_fingerprint() -> str:
    root = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for path in sorted(root.glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def registry_payload() -> dict[str, Any]:
    models = [spec.public_dict() for spec in list_model_specs()]
    default = next((item["id"] for item in models if item.get("default")), models[0]["id"])
    return {"models": models, "default_model": default, "registry_fingerprint": registry_fingerprint()}
