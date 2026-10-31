"""Shared access contract for selectable research models.

The UI may show a model before it is runnable, but API routes still enforce
the same entitlement and release state so a browser request cannot bypass it.
"""
from fastapi import HTTPException

MINERVA_MODEL_ID = "minerva_v1"
MINERVA_BASELINE_QUANT_MODEL_ID = "minerva_baseline"
# The normal scanner accepts V8 and the paid Minerva baseline. Minerva remains
# cross-sectional under the hood: the browser supplies the requested symbol plus
# a compact comparison universe so the backend can run the same frozen ridge
# profile used by Quant Lab without ever receiving a provider key.
SCANNER_MODEL_IDS = {"v8", MINERVA_BASELINE_QUANT_MODEL_ID}
# Earlier public clients labelled this same released scanner profile
# "official" (and a short-lived Quant Lab selector used "v8_official").
# Accept those persisted browser values at the request boundary, then run the
# one current public scanner engine. This avoids making a user's workspace
# depend on which HTML revision happened to load first.
SCANNER_MODEL_ALIASES = {
    "v8": "v8",
    "official": "v8",
    "v8_official": "v8",
    "minerva": MINERVA_BASELINE_QUANT_MODEL_ID,
    "minerva_v1": MINERVA_BASELINE_QUANT_MODEL_ID,
    "minerva_baseline": MINERVA_BASELINE_QUANT_MODEL_ID,
}


def normalize_scanner_model(model: str) -> str:
    normalized = SCANNER_MODEL_ALIASES.get(str(model or "").strip().lower())
    if normalized is None:
        raise ValueError("Choose a supported scanner model.")
    return normalized


def require_model_access(user: dict, model: str) -> None:
    if model not in {MINERVA_MODEL_ID, MINERVA_BASELINE_QUANT_MODEL_ID}:
        return
    if not user.get("subscription"):
        raise HTTPException(
            status_code=402,
            detail={
                "code": "MODEL_SUBSCRIPTION_REQUIRED",
                "model": MINERVA_BASELINE_QUANT_MODEL_ID,
                "message": "Minerva scanner access is included with Oryntra Pro and Max.",
            },
        )


def require_minerva_baseline_quant_access(user: dict) -> None:
    """Grant the frozen multi-symbol Minerva baseline only to paid workspaces.

    Quant Lab and Scanner share the same paid Minerva entitlement. Scanner runs
    the frozen profile on a compact cross-sectional browser-supplied universe;
    Quant Lab can run a larger user-selected universe.
    """
    if not user.get("subscription"):
        raise HTTPException(
            status_code=402,
            detail={
                "code": "MODEL_SUBSCRIPTION_REQUIRED",
                "model": MINERVA_BASELINE_QUANT_MODEL_ID,
                "message": "The frozen Minerva baseline is included with Oryntra Pro and Max in Quant Lab.",
            },
        )
