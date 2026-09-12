"""Single source of public legal-operator metadata.

Do not set the formed flag until CQC exists and the Oryntra IP assignment has
actually been executed.  The pre-formation language is intentionally safe.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class LegalOperator:
    effective_date: str
    contact_email: str
    formed_and_assigned: bool

    @property
    def operator_statement(self) -> str:
        if self.formed_and_assigned:
            return "Oryntra is owned and operated by Cowles Quantitative Corporation, a Florida corporation."
        return "Oryntra is in development and is not yet operated by Cowles Quantitative Corporation."

    @property
    def ownership_statement(self) -> str:
        if self.formed_and_assigned:
            return "Oryntra, its software, models, branding, and related intellectual property are owned by Cowles Quantitative Corporation or its licensors."
        return "Oryntra, its software, models, branding, and related intellectual property are owned by their current rights holders or licensors."


def _env_bool(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() in {"1", "true", "yes", "on"}


def legal_operator() -> LegalOperator:
    return LegalOperator(
        effective_date=os.getenv("ORYNTRA_LEGAL_EFFECTIVE_DATE", "September 8, 2026").strip(),
        contact_email=os.getenv("ORYNTRA_LEGAL_CONTACT_EMAIL", "legal@cowlesquantcorp.com").strip(),
        formed_and_assigned=_env_bool("ORYNTRA_CQC_FORMED_AND_IP_ASSIGNED"),
    )


def render_legal_template(html: str) -> str:
    operator = legal_operator()
    return (html.replace("{{LEGAL_EFFECTIVE_DATE}}", operator.effective_date)
            .replace("{{LEGAL_CONTACT_EMAIL}}", operator.contact_email)
            .replace("{{LEGAL_OPERATOR_STATEMENT}}", operator.operator_statement)
            .replace("{{LEGAL_OWNERSHIP_STATEMENT}}", operator.ownership_statement))
