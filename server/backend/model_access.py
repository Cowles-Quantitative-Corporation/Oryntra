"""Shared access contract for selectable research models.

The UI may show a model before it is runnable, but API routes still enforce
the same entitlement and release state so a browser request cannot bypass it.
"""
from fastapi import HTTPException

MINERVA_MODEL_ID = "minerva_v1"
MINERVA_BASELINE_QUANT_MODEL_ID = "minerva_baseline"
# Scanner requests are deliberately single-symbol pattern analyses. The only
# released scanner engine is V8 Official; Minerva belongs to Quant Lab and TBA9
# remains private integrity infrastructure.
SCANNER_MODEL_IDS = {"v8"}


def require_model_access(user: dict, model: str) -> None:
    if model != MINERVA_MODEL_ID:
        return
    if not user.get("subscription"):
        raise HTTPException(status_code=402, detail={"code": "MODEL_SUBSCRIPTION_REQUIRED", "model": model, "message": "Minerva access is reserved for an active subscription."})
    raise HTTPException(status_code=409, detail={"code": "MODEL_RESEARCH_NOT_RELEASED", "model": model, "message": "Minerva remains a research candidate and cannot run in the workspace yet."})


def require_minerva_baseline_quant_access(user: dict) -> None:
    """Grant the frozen multi-symbol Minerva baseline only to paid workspaces.

    This is deliberately separate from ``require_model_access``: Minerva is
    not a one-symbol scanner engine and must never be smuggled into scanner or
    backtest endpoints by a browser-supplied model name.
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
