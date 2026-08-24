"""Lifecycle monitoring for merchants already on the platform.

Admission asks "should we let them in". This asks "is what we underwrote still
what is happening", which is a different question with a different vocabulary:
alerts and severities, not approve/decline.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

from . import config
from .corpus import Merchant
from .decision import decide
from .graph import ContextGraph
from .signals import detect_all


@dataclass
class Alert:
    merchant_id: str
    merchant: str
    posture: str
    posture_label: str
    category: str
    severity: str            # critical | high | medium
    title: str
    detail: str
    evidence: list[str]
    p_bad: float
    exposure: float
    lead_signal: str

    def to_dict(self) -> dict:
        return asdict(self)


_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2}


def _severity(p: float, posture: str, lr: float) -> str:
    if posture == "attacked" and lr >= 10:
        return "critical"
    if p >= config.DECLINE_THRESHOLD * 2 or lr >= 20:
        return "critical"
    if p >= config.DECLINE_THRESHOLD or lr >= 6:
        return "high"
    return "medium"


def build_alerts(merchants: list[Merchant], graph: ContextGraph,
                 by_id: dict[str, Merchant], store, portfolio_volume: float) -> list[Alert]:
    out: list[Alert] = []
    for m in merchants:
        if m.status != "approved":
            continue
        sigs = detect_all(m, graph, by_id, portfolio_volume, store)
        if not sigs:
            continue
        dec = decide(sigs)
        lead = sigs[0]
        if lead.posture == "memory" and len(sigs) > 1:
            lead = next((s for s in sigs if s.posture != "memory"), lead)
        if dec.p_bad < 0.05 and lead.lr < 4:
            continue
        exposure = m.prepaid_balance if lead.category == "insolvency" else m.monthly_volume
        out.append(Alert(
            merchant_id=m.id, merchant=m.name, posture=lead.posture,
            posture_label=config.POSTURES.get(lead.posture, lead.posture),
            category=lead.category,
            severity=_severity(dec.p_bad, lead.posture, lead.lr),
            title=lead.title, detail=lead.detail, evidence=lead.evidence,
            p_bad=dec.p_bad, exposure=round(exposure, 2),
            lead_signal=lead.id,
        ))
    out.sort(key=lambda a: (_SEVERITY_ORDER[a.severity], -a.exposure))
    return out


def drift_cases(merchants: list[Merchant]) -> list[dict]:
    """Merchants where observed_selling has diverged from claims_to_sell."""
    out = []
    for m in merchants:
        if m.status != "approved" or not m.observed_category:
            continue
        if m.observed_category == m.category_claimed:
            continue
        out.append({
            "merchant_id": m.id, "merchant": m.name,
            "claimed_category": m.category_claimed,
            "observed_category": m.observed_category,
            "claims_to_sell": m.offering_claimed,
            "observed_selling": m.offering_observed,
            "prohibited_now": m.observed_category in config.POLICY_PROHIBITED,
            "last_observed_at": m.last_observed_at,
            "monthly_volume": m.monthly_volume,
            "refund_rate": m.refund_rate,
        })
    out.sort(key=lambda d: (not d["prohibited_now"], -d["monthly_volume"]))
    return out
