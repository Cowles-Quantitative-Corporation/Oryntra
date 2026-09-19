from fastapi import HTTPException

from backend.model_access import require_minerva_baseline_quant_access
from backend.routes.quant import PublicBrowserQuantResearchRequest


def test_minerva_baseline_quant_lab_requires_a_paid_workspace():
    try:
        require_minerva_baseline_quant_access({"subscription": None})
    except HTTPException as exc:
        assert exc.status_code == 402
        assert exc.detail["code"] == "MODEL_SUBSCRIPTION_REQUIRED"
    else:
        raise AssertionError("Base access unexpectedly reached Minerva baseline")


def test_minerva_baseline_quant_lab_accepts_pro_and_cqc_max():
    assert require_minerva_baseline_quant_access({"subscription": {"plan_code": "pro"}}) is None
    assert require_minerva_baseline_quant_access({"subscription": {"plan_code": "cqc_max"}}) is None


def test_public_quant_model_catalog_excludes_private_tba9():
    assert PublicBrowserQuantResearchRequest.validate_public_model("v8_official") == "v8_official"
    assert PublicBrowserQuantResearchRequest.validate_public_model("minerva_baseline") == "minerva_baseline"
    try:
        PublicBrowserQuantResearchRequest.validate_public_model("tba9")
    except ValueError:
        pass
    else:
        raise AssertionError("Private TBA9 entered the public Quant Lab catalog")
