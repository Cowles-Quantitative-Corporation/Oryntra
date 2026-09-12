"""Shared access contract for selectable research models.

The UI may show a model before it is runnable, but API routes still enforce
the same entitlement and release state so a browser request cannot bypass it.
"""
from fastapi import HTTPException

MINERVA_MODEL_ID = "minerva_v1"
SCANNER_MODEL_IDS = {"official", "universal_v2", MINERVA_MODEL_ID}


def require_model_access(user: dict, model: str) -> None:
    if model != MINERVA_MODEL_ID:
        return
    if not user.get("subscription"):
        raise HTTPException(status_code=402, detail={"code": "MODEL_SUBSCRIPTION_REQUIRED", "model": model, "message": "Minerva access is reserved for an active subscription."})
    raise HTTPException(status_code=409, detail={"code": "MODEL_RESEARCH_NOT_RELEASED", "model": model, "message": "Minerva remains a research candidate and cannot run in the workspace yet."})
