"""Talking to the memory layer.

Three things a person needs to do with a memory: ask it what it knows, tell it
something new, and correct it when it is wrong. This module handles all three
over plain text.

Intent parsing is deliberately deterministic rather than model-driven. An
answer about why a merchant was declined has to be reproducible and traceable
to its sources -- if the explanation layer hallucinates, the audit trail the
rest of the system is built on is worthless. Every answer here cites the
memories, graph paths and cases it used.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from . import config
from .decision import decide
from .signals import detect_all, profile_text

ASK = "ask"
TELL = "tell"
FEEDBACK = "feedback"

_QUESTION_OPENERS = {
    "what", "why", "how", "who", "when", "where", "which", "does", "did", "do",
    "is", "are", "was", "were", "have", "has", "should", "can", "could", "would",
    "show", "tell", "list", "explain", "summarise", "summarize", "give",
}

_ADVERSE_WORDS = {
    "risky", "risk", "fraud", "fraudulent", "bad", "dangerous", "prohibited",
    "decline", "reject", "terminate", "piracy", "pirated", "scam", "abuse",
    "suspicious", "avoid", "never", "problem", "watch", "concern", "illegal",
}
_CLEARING_WORDS = {
    "fine", "safe", "legitimate", "legit", "approve", "acceptable", "ok",
    "okay", "good", "clean", "harmless", "allow", "normal",
}

_PORTFOLIO_WORDS = {
    "portfolio", "overall", "doing", "health", "vamp", "ratio", "exposure",
    "standing", "status", "summary", "overview", "state", "everything",
}


@dataclass
class Turn:
    intent: str
    answer: str
    evidence: list[dict] = field(default_factory=list)
    memory_action: Optional[dict] = None
    replay: Optional[dict] = None
    subject: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "intent": self.intent, "answer": self.answer, "evidence": self.evidence,
            "memory_action": self.memory_action, "replay": self.replay,
            "subject": self.subject,
        }


#: Words that state a verdict or frame an instruction rather than describe the
#: thing being judged. They belong in the human-readable statement but ruin it
#: as a retrieval key -- "merchants ... are risky and should be declined" would
#: otherwise match every merchant equally.
_SCAFFOLD = {
    "merchant", "merchants", "should", "must", "need", "needs", "always",
    "declined", "decline", "approve", "approved", "reject", "rejected", "treat",
    "flag", "flagged", "watch", "consider", "think", "believe", "want", "make",
    "sure", "please", "note", "remember", "risky", "risk", "fine", "safe", "bad",
    "good", "them", "they", "when", "without", "with", "any", "all", "more",
    "less", "very", "really", "just", "also", "like", "seems", "looks",
}


def derive_trigger(text: str) -> str:
    """Strip verdict and instruction words, keeping what the rule is *about*."""
    from .retrieval import tokenize
    keep = [t for t in tokenize(text) if t not in _SCAFFOLD]
    return " ".join(keep) if keep else text


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower()).strip()


def classify(text: str) -> str:
    t = _norm(text)
    if not t:
        return ASK
    words = t.split()
    first = words[0]
    if text.strip().endswith("?"):
        return ASK
    if first in _QUESTION_OPENERS:
        # "tell me about X" is a question; "telegram fulfilment is risky" is not
        if first in {"tell", "show", "list", "give", "explain"}:
            return ASK
        return ASK
    if re.search(r"\b(you (were|are) wrong|that was wrong|actually|correct(ion)?|"
                 r"disagree|override|should have|shouldn't have|was fine|"
                 r"was legitimate|not a problem)\b", t):
        return FEEDBACK
    return TELL


def polarity_of(text: str) -> str:
    t = set(_norm(text).split())
    adverse = len(t & _ADVERSE_WORDS)
    clearing = len(t & _CLEARING_WORDS)
    if re.search(r"\b(not|isn't|aren't|wasn't|never)\b.{0,24}\b(risky|bad|fraud|problem)\b",
                 _norm(text)):
        return "clearing"
    if adverse > clearing:
        return "adverse"
    if clearing > adverse:
        return "clearing"
    return "neutral"


class Conversation:
    """Stateless over the App; holds only the transcript."""

    def __init__(self, app) -> None:
        self.app = app
        self.transcript: list[dict] = []
        self._names = {m.name.lower(): m.id for m in app.merchants}
        self._domains = {m.domain.lower(): m.id for m in app.merchants}

    # -- entity lookup ------------------------------------------------------
    def find_merchant(self, text: str) -> Optional[str]:
        low = text.lower()
        for dom, mid in self._domains.items():
            if dom in low:
                return mid
        best, best_len = None, 0
        for name, mid in self._names.items():
            if len(name) > 4 and name in low and len(name) > best_len:
                best, best_len = mid, len(name)
        return best

    # -- entry point --------------------------------------------------------
    def handle(self, text: str) -> Turn:
        text = (text or "").strip()
        if not text:
            return Turn(ASK, "Ask me what memory holds, or tell me something to remember.")
        intent = classify(text)
        subject = self.find_merchant(text)

        if intent == ASK:
            turn = (self._answer_about_merchant(subject, text) if subject
                    else self._answer_about_portfolio(text) if self._is_portfolio(text)
                    else self._answer_about_topic(text))
        elif intent == FEEDBACK:
            turn = self._record_feedback(text, subject)
        else:
            turn = self._record_assertion(text, subject)

        self.transcript.append({"you": text, **turn.to_dict()})
        return turn

    def _is_portfolio(self, text: str) -> bool:
        return bool(set(_norm(text).split()) & _PORTFOLIO_WORDS)

    # -- answering ----------------------------------------------------------
    def _answer_about_merchant(self, mid: str, text: str) -> Turn:
        app = self.app
        m = app.by_id[mid]
        sigs = detect_all(m, app.graph, app.by_id, app.portfolio_volume, app.store)
        similar = app._precedent(m)
        dec = decide(sigs, similar)
        related = app.graph.related_merchants(m.id)[:3]
        mems = app.store.for_subject(m.id)

        lines = []
        state = {"pending": "is awaiting a decision", "approved": "was approved",
                 "declined": "was declined",
                 "terminated": "was approved and later terminated"}.get(m.status, m.status)
        lines.append(f"**{m.name}** {state}. Current estimate is "
                     f"**{dec.p_bad*100:.1f}% probability of going bad**, against a decline "
                     f"threshold of {config.DECLINE_THRESHOLD*100:.1f}%. "
                     f"The recommendation is: {dec.headline.lower()}.")

        if sigs:
            lines.append("\nWhat is driving that:")
            for s in sigs[:4]:
                lines.append(f"- {s.title} ({config.POSTURES.get(s.posture, s.posture)}, "
                             f"likelihood ratio {s.lr}). {s.detail}")
        else:
            lines.append("\nNothing fired. No signal in any of the four postures, and "
                         "no graph link to a merchant we have already judged.")

        if related:
            lines.append("\nWho it is connected to:")
            for r in related:
                other = app.by_id[r.target]
                lines.append(f"- **{other.name}** ({other.status}) by "
                             f"{r.corroboration} route"
                             f"{'s' if r.corroboration > 1 else ''}: "
                             + "; ".join(p.describe() for p in r.paths))

        bad_prec = [p for p, _s in similar if p.truth_bad]
        if similar:
            lines.append(f"\nPrecedent: {len(bad_prec)} of the {len(similar)} closest "
                         f"historical cases went bad. {dec.precedent_note}")

        if mems:
            lines.append("\nWhat memory already holds about it:")
            for x in mems[:3]:
                lines.append(f"- {x.text} _(source: {x.source}, confidence {x.confidence})_")

        if m.rationale:
            lines.append(f"\nRationale on file: “{m.rationale}”")

        evidence = ([{"kind": "signal", "label": s.title, "detail": s.detail} for s in sigs[:4]]
                    + [{"kind": "graph", "label": app.by_id[r.target].name,
                        "detail": "; ".join(p.describe() for p in r.paths)} for r in related]
                    + [{"kind": "memory", "label": x.source, "detail": x.text} for x in mems[:3]])
        return Turn(ASK, "\n".join(lines), evidence, subject=m.id)

    def _answer_about_topic(self, text: str) -> Turn:
        app = self.app
        hits = app.store.search(text, limit=5, promoted_only=False)
        case_hits = app.cases.search(text, limit=40)
        cases = [(app.by_id[i], s) for i, s in case_hits if i in app.by_id]
        matched = [m for m, _s in cases]
        bad = [m for m in matched if m.truth_bad]

        lines = []
        if hits:
            lines.append(f"Memory holds **{len(hits)} record"
                         f"{'s' if len(hits) != 1 else ''}** relevant to that:")
            for mem, sim in hits:
                tag = mem.kind
                if mem.kind == "semantic" and not mem.promoted:
                    tag += ", awaiting the replay gate"
                lines.append(f"- _{tag}_ — {mem.text} "
                             f"_(confidence {mem.confidence}, source: {mem.source}, "
                             f"match {sim:.2f})_")
        else:
            lines.append("Memory holds nothing matching that yet.")

        if matched:
            rate = len(bad) / len(matched)
            lines.append(f"\nIn the portfolio, **{len(matched)} merchants** resemble it, "
                         f"and **{len(bad)} went bad** ({rate*100:.0f}%, against a "
                         f"{config.CONFIRMED_BAD_RATE*100:.1f}% base rate).")
            if bad:
                lines.append("Examples: " + ", ".join(
                    f"{b.name} ({(b.truth_category or '').replace('_',' ')})"
                    for b in bad[:4]) + ".")
            if rate > config.CONFIRMED_BAD_RATE * 3 and not hits:
                lines.append("\nThat is well above the base rate and memory holds no "
                             "pattern for it. Worth telling me what the rule should be — "
                             "I will record it and show you what it would have changed.")

        evidence = ([{"kind": "memory", "label": m.source, "detail": m.text} for m, _s in hits]
                    + [{"kind": "case", "label": b.name,
                        "detail": b.truth_note or b.pitch} for b in bad[:5]])
        return Turn(ASK, "\n".join(lines), evidence)

    def _answer_about_portfolio(self, text: str) -> Turn:
        p = self.app.portfolio()
        v = p["vamp"]
        alerts = self.app.alerts()
        crit = [a for a in alerts if a.severity == "critical"]

        head = ("comfortable" if v["headroom_pct"] > 25
                else "thin" if v["headroom_pct"] > 0 else "breached")
        lines = [
            f"**{p['approved']:,} active merchants**, ${p['annual_volume']/1e6:.0f}M "
            f"annualised.",
            "",
            f"**Dispute ratio {v['ratio']*100:.3f}%.** The acquirer line is "
            f"{v['above_standard']*100:.2f}%, so headroom is {v['headroom_pct']:.0f}% and "
            f"{head}. This is the number that matters most: it is shared across every "
            f"merchant on Dodo's MIDs, so one bad actor at volume moves it for everybody.",
            "",
            f"**${p['prepaid_exposure']/1e6:.1f}M of prepaid exposure.** Not fraud — this "
            f"is service already paid for that Dodo would owe as refunds if those "
            f"merchants failed. It dwarfs the assumed ${config.ANNUAL_ADMISSION_LOSS_USD/1000:.0f}k "
            f"annual fraud loss, which is worth saying out loud.",
            "",
            f"**{p['alert_total']} open alerts**, {p['alerts']['critical']} critical. "
            f"**{p['queue_size']} applications** awaiting a decision.",
        ]
        if crit:
            lines.append("\nWhat needs attention first:")
            for a in crit[:4]:
                lines.append(f"- **{a.merchant}** — {a.title} "
                             f"({a.posture_label.lower()}, ${a.exposure:,.0f} exposed)")
        lines.append(f"\nMemory holds {p['memory']['active']} active records. "
                     f"The graph has {p['graph']['nodes']:,} entities.")
        evidence = [{"kind": "alert", "label": a.merchant, "detail": a.title} for a in crit[:5]]
        return Turn(ASK, "\n".join(lines), evidence)

    # -- writing ------------------------------------------------------------
    def _record_assertion(self, text: str, subject: Optional[str]) -> Turn:
        app = self.app
        pol = polarity_of(text)
        general = subject is None

        rec = app.store.reconcile(
            text,
            trigger=derive_trigger(text) if general else profile_text(app.by_id[subject]),
            kind="semantic" if general else "episodic",
            subject=subject,
            category=None,
            polarity=pol,
            confidence=0.88,          # a human said it, so it carries weight
            source="founder (told directly)",
            promoted=True,            # human instruction applies immediately
        )

        lines = [f"Recorded. Memory reconciled: **{rec.action}** — {rec.reason}"]
        if pol == "neutral":
            lines.append("\nI read that as neither adverse nor clearing, so it is stored "
                         "as context rather than as something that moves a score. Say "
                         "“risky” or “fine” explicitly if you meant it "
                         "to weigh on decisions.")
        elif general:
            lines.append(f"\nIt is live now and will be consulted on every future case "
                         f"that matches. Because a human asserted it, it skips the replay "
                         f"gate — but here is what it would have done historically, so you "
                         f"can see whether it helps or just adds noise.")

        replay = None
        if general and pol == "adverse":
            replay = self._impact(rec.memory)
            if replay:
                lines.append(
                    f"\n**Historical impact:** {replay['caught_delta']:+d} confirmed-bad "
                    f"merchants caught, {replay['false_flag_delta']:+d} legitimate merchants "
                    f"wrongly flagged, over {replay['population']} past decisions.")
                if replay["false_flag_delta"] > 0:
                    lines.append("That is a real cost. Consider narrowing the wording, or "
                                 "tell me to drop it.")
                elif replay["caught_delta"] == 0:
                    lines.append("No historical effect — it may be too specific to match "
                                 "anything, or it may already be covered.")

        return Turn(TELL, "\n".join(lines), memory_action=rec.to_dict(),
                    replay=replay, subject=subject)

    def _record_feedback(self, text: str, subject: Optional[str]) -> Turn:
        app = self.app
        pol = polarity_of(text)
        if pol == "neutral":
            pol = "clearing"
        target = f" about {app.by_id[subject].name}" if subject else ""
        rec = app.store.reconcile(
            text,
            trigger=profile_text(app.by_id[subject]) if subject else text,
            kind="episodic" if subject else "semantic",
            subject=subject, category=None, polarity=pol, confidence=0.92,
            source="founder (correction)", promoted=True,
        )
        lines = [f"Correction recorded{target}. Memory reconciled: **{rec.action}** — "
                 f"{rec.reason}"]
        if rec.action == "INVALIDATE":
            lines.append("\nThe fact it contradicts is superseded, not deleted, so the "
                         "decision it originally supported can still be reconstructed.")
        elif rec.action == "DISPUTED":
            lines.append("\nIt contradicts something held at higher confidence, so both "
                         "are retained and flagged. Nothing was silently overwritten.")
        return Turn(FEEDBACK, "\n".join(lines), memory_action=rec.to_dict(), subject=subject)

    def _impact(self, memory) -> Optional[dict]:
        """Replay a just-asserted memory over history, on a sample for speed."""
        from .replay import replay as run_replay
        app = self.app
        pool = [m for m in app.merchants if m.status in ("approved", "terminated")]
        bad = [m for m in pool if m.truth_bad]
        clean = [m for m in pool if not m.truth_bad]
        step = max(1, len(clean) // 200)
        sample = bad + clean[::step]

        was = memory.promoted
        memory.promoted = False
        before = run_replay(app.merchants, app.graph, app.by_id, app.store,
                            app.portfolio_volume, sample)
        memory.promoted = True
        after = run_replay(app.merchants, app.graph, app.by_id, app.store,
                           app.portfolio_volume, sample)
        memory.promoted = was
        return {
            "population": len(sample),
            "caught_delta": after.caught - before.caught,
            "false_flag_delta": after.false_flags - before.false_flags,
            "before": before.to_dict(), "after": after.to_dict(),
        }
