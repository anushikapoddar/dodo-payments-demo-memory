"""Turning signals into a decision, with the arithmetic left visible.

Section 6.2 fixes the operating point: a wrongful approval is assumed to cost
about six times a wrongful decline, which puts the indifference point at
P(bad) ~ 13.8%. This module composes evidence in odds space and compares the
result against that threshold, so a reviewer can see every step rather than a
bare score.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional

from . import config
from .signals import Signal

#: Signals are correlated -- a young domain and Telegram fulfilment travel
#: together -- so multiplying raw likelihood ratios overstates the evidence.
#: Each successive signal is discounted, which keeps a pile of weak correlated
#: observations from stacking into false certainty.
DAMPING = 0.62


@dataclass
class Decision:
    p_bad: float
    prior: float
    posterior_odds: float
    recommendation: str          # approve | conditions | escalate | decline
    headline: str
    expected_cost_approve: float
    expected_cost_decline: float
    threshold: float
    contributions: list[dict]
    precedent_lr: float
    precedent_note: str

    def to_dict(self) -> dict:
        return asdict(self)


def _odds(p: float) -> float:
    p = min(max(p, 1e-9), 1 - 1e-9)
    return p / (1 - p)


def _prob(o: float) -> float:
    return o / (1 + o)


def _counts_as_precedent(m: object) -> bool:
    if not getattr(m, "truth_bad", False):
        return False
    cat = getattr(m, "truth_category", None)
    posture = config.CATEGORY_POSTURE.get(cat or "", "")
    return posture in config.PRECEDENT_RELEVANT_POSTURES


def precedent_likelihood(similar: list[tuple[object, float]]) -> tuple[float, str]:
    """How much do comparable historical cases move the odds?

    Two corrections that matter more than they look. Merchants that went bad
    through no fault of their own -- card testing run through their checkout,
    an account takeover -- are excluded, because precedent matches on product
    and pitch and those outcomes are not properties of the business. And the
    rate is smoothed toward the base rate, so one bad match in six cannot swing
    the result by an order of magnitude.
    """
    if not similar:
        return 1.0, "No comparable historical cases retrieved."

    weighted_all = sum(sim for _m, sim in similar)
    if weighted_all <= 0:
        return 1.0, "No comparable historical cases retrieved."

    weighted_bad = sum(sim for m, sim in similar if _counts_as_precedent(m))
    n_bad = sum(1 for m, _s in similar if _counts_as_precedent(m))
    excluded = sum(1 for m, _s in similar
                   if getattr(m, "truth_bad", False) and not _counts_as_precedent(m))

    base = config.CONFIRMED_BAD_RATE
    a = config.PRECEDENT_SMOOTHING
    rate = (weighted_bad + a * base) / (weighted_all + a)

    lr = max(0.35, min(rate / base, 12.0))
    note = (f"{n_bad} of {len(similar)} closest historical matches went bad "
            f"({rate*100:.1f}% smoothed, against a {base*100:.1f}% base rate).")
    if excluded:
        note += (f" {excluded} further match{'es' if excluded != 1 else ''} had an "
                 f"adverse outcome the merchant did not cause (card testing or "
                 f"account takeover) and {'were' if excluded != 1 else 'was'} excluded.")
    return round(lr, 3), note


def decide(signals: list[Signal], similar: Optional[list] = None,
           prior: float = config.CONFIRMED_BAD_RATE) -> Decision:
    odds = _odds(prior)
    contributions: list[dict] = []

    for i, s in enumerate(sorted(signals, key=lambda x: -x.lr)):
        damped = s.lr ** (DAMPING ** i)
        before = odds
        odds *= damped
        contributions.append({
            "id": s.id, "title": s.title, "posture": s.posture,
            "posture_label": config.POSTURES.get(s.posture, s.posture),
            "category": s.category, "raw_lr": s.lr, "applied_lr": round(damped, 3),
            "p_before": round(_prob(before), 4), "p_after": round(_prob(odds), 4),
        })

    p_lr, p_note = precedent_likelihood(similar or [])
    if abs(p_lr - 1.0) > 1e-6:
        before = odds
        odds *= p_lr
        contributions.append({
            "id": "precedent", "title": "Retrieved precedent", "posture": "memory",
            "posture_label": "Memory", "category": "precedent",
            "raw_lr": p_lr, "applied_lr": p_lr,
            "p_before": round(_prob(before), 4), "p_after": round(_prob(odds), 4),
        })

    p = _prob(odds)
    ec_approve = p * config.COST_FALSE_APPROVE_USD
    ec_decline = (1 - p) * config.COST_FALSE_DECLINE_USD

    if p >= config.DECLINE_THRESHOLD * 2:
        rec, head = "decline", "Decline"
    elif p >= config.DECLINE_THRESHOLD:
        rec, head = "escalate", "Escalate to a senior reviewer"
    elif p >= config.AUTO_APPROVE_THRESHOLD:
        rec, head = "conditions", "Approve with conditions"
    else:
        rec, head = "approve", "Approve"

    return Decision(
        p_bad=round(p, 4), prior=prior, posterior_odds=round(odds, 4),
        recommendation=rec, headline=head,
        expected_cost_approve=round(ec_approve, 2),
        expected_cost_decline=round(ec_decline, 2),
        threshold=round(config.DECLINE_THRESHOLD, 4),
        contributions=contributions, precedent_lr=p_lr, precedent_note=p_note,
    )
