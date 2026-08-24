"""Application state: one object that owns the corpus, graph and memory."""
from __future__ import annotations

import datetime as _dt
from typing import Optional

from . import config
from .converse import Conversation
from .corpus import Merchant, build, summary
from .decision import decide
from .graph import ContextGraph
from .memory import MemoryStore
from .monitor import build_alerts, drift_cases
from .replay import distil, replay, run_gate
from .retrieval import TfIdf
from .signals import detect_all, profile_text


class App:
    def __init__(self) -> None:
        self.reset()

    # -- lifecycle ----------------------------------------------------------
    def reset(self) -> None:
        self.merchants: list[Merchant] = build()
        self.by_id: dict[str, Merchant] = {m.id: m for m in self.merchants}
        self.graph = ContextGraph.build(self.merchants)
        self.portfolio_volume = sum(
            m.monthly_volume for m in self.merchants if m.status == "approved")

        prime = [profile_text(m) for m in self.merchants]
        self.store = MemoryStore(prime_corpus=prime)

        # a case index for precedent retrieval over decided history
        self.cases = TfIdf()
        for m in self.merchants:
            if m.status in ("approved", "terminated", "declined"):
                self.cases.add(m.id, profile_text(m))
        self.cases.build()

        self.gate_history: list[dict] = []
        self.activity: list[dict] = []       # feeds the summary-bar digest
        self._seed_memory()
        self.conversation = Conversation(self)
        for a in self.alerts()[:4]:
            self._log("alert", f"{a.merchant} — {a.title}", a.posture_label)

    def _seed_memory(self) -> None:
        """Memory as it stands before the demo begins.

        Episodic records of a few real decisions, two procedural notes, and two
        already-promoted semantic patterns. The ebook rights pattern is
        deliberately absent -- that is what the demo teaches it.
        """
        terminated = [m for m in self.merchants if m.status == "terminated"][:14]
        for m in terminated:
            self.store.reconcile(
                f"{m.name} was approved and later terminated. {m.truth_note}",
                trigger=profile_text(m), kind="episodic", subject=m.id,
                category=m.truth_category, polarity="adverse", confidence=0.95,
                source=f"case file {m.id}", observed_at=m.decided_at or config.DEMO_TODAY,
                evidence=[m.id],
            )
        self.store.reconcile(
            "For catalogue or library claims: sample twenty titles, check them against "
            "public rights registries, and request two publisher agreements before "
            "approving.",
            trigger="catalogue library titles investigation procedure rights registry",
            kind="procedural", category="undisclosed_illegality", polarity="neutral",
            confidence=0.8, source="compliance playbook",
        )
        self.store.reconcile(
            "For any subscription product: attempt the cancellation flow yourself and "
            "record how many steps it takes.",
            trigger="subscription cancellation flow procedure steps trial renewal",
            kind="procedural", category="deceptive_billing", polarity="neutral",
            confidence=0.8, source="compliance playbook",
        )
        # Content patterns carry a text trigger; behavioural ones carry a
        # predicate. The refund-rate lesson is behavioural: matched on prose it
        # fires on any merchant whose pitch says "subscription" and "free
        # trial", which is most of the portfolio. Gated on the observable it
        # names, it fires only on merchants actually refunding at that rate.
        for cat, text, trig, pred in [
            ("product_drift",
             "Merchants underwritten as B2B AI tooling that later add companion or "
             "intimacy tiers move into a prohibited category on the same account.",
             "ai companion intimacy chat character roleplay adult tier pivot", None),
            ("deceptive_billing",
             "Subscription products with sustained refund rates above 15 percent are "
             "signalling a cancellation flow customers cannot find.",
             "subscription auto-renewal free trial cancel refund recurring billing",
             {"all": [{"field": "refund_rate", "op": ">=", "value": 0.15},
                      {"field": "settled_txns", "op": ">=", "value": 40}]}),
        ]:
            self.store.reconcile(
                text, trigger=trig, predicate=pred, kind="semantic", category=cat,
                polarity="adverse", confidence=0.7,
                source="distilled from prior incidents", promoted=True)

    # -- reads --------------------------------------------------------------
    def portfolio(self) -> dict:
        s = summary(self.merchants)
        active = [m for m in self.merchants if m.status == "approved"]
        alerts = self.alerts()
        sev = {"critical": 0, "high": 0, "medium": 0}
        for a in alerts:
            sev[a.severity] += 1
        annual = s["monthly_volume"] * 12
        return {
            **s,
            "annual_volume": round(annual, 2),
            "vamp": {
                "ratio": s["portfolio_vamp"],
                "above_standard": config.VAMP_ACQUIRER_ABOVE_STANDARD,
                "excessive": config.VAMP_ACQUIRER_EXCESSIVE,
                "headroom_pct": round(
                    (config.VAMP_ACQUIRER_ABOVE_STANDARD - s["portfolio_vamp"])
                    / config.VAMP_ACQUIRER_ABOVE_STANDARD * 100, 1),
            },
            "prepaid_exposure": round(sum(m.prepaid_balance for m in active), 2),
            "alerts": sev,
            "alert_total": len(alerts),
            "memory": self.store.counts(),
            "graph": self.graph.stats(),
            "last_reconciled": (self.activity[0]["at"] if self.activity else None),
            "queue_size": sum(1 for m in self.merchants if m.status == "pending"),
            "real_customers": sum(1 for m in self.merchants if m.real),
            "threshold": round(config.DECLINE_THRESHOLD, 4),
            "cost_ratio": round(
                config.COST_FALSE_APPROVE_USD / config.COST_FALSE_DECLINE_USD, 1),
        }

    def queue(self) -> list[dict]:
        out = []
        for m in self.merchants:
            if m.status != "pending":
                continue
            sigs = detect_all(m, self.graph, self.by_id, self.portfolio_volume, self.store)
            dec = decide(sigs, self._precedent(m))
            out.append({
                "id": m.id, "name": m.name, "domain": m.domain,
                "country": m.country, "category": m.category_claimed,
                "pitch": m.pitch, "applied_at": m.applied_at,
                "p_bad": dec.p_bad, "recommendation": dec.recommendation,
                "headline": dec.headline, "signal_count": len(sigs),
                "top_signal": sigs[0].title if sigs else "No signals fired",
                "scenario": m.scenario,
            })
        out.sort(key=lambda d: -d["p_bad"])
        return out

    def _precedent(self, m: Merchant, limit: int = 6) -> list[tuple[Merchant, float]]:
        """Historical merchants close enough to this one to count as precedent.

        The similarity floor matters more than it looks. Nearly every merchant
        here shares the terms "subscription", "saas", "github" and "email", so
        the tail of any search is filled with matches that are lexically real
        and evidentially empty. Left in, a single unlucky neighbour at cosine
        0.13 moves a clean merchant's odds by 3x. Genuine precedent in this
        corpus sits at 0.24 and above; below the floor the shared vocabulary is
        the corpus's, not the merchant's.
        """
        hits = self.cases.search(profile_text(m), limit=limit + 1, exclude=[m.id])
        return [(self.by_id[i], s) for i, s in hits
                if i in self.by_id and s >= config.PRECEDENT_MIN_SIMILARITY][:limit]

    def brief(self, merchant_id: str) -> Optional[dict]:
        m = self.by_id.get(merchant_id)
        if m is None:
            return None
        sigs = detect_all(m, self.graph, self.by_id, self.portfolio_volume, self.store)
        similar = self._precedent(m)
        dec = decide(sigs, similar)
        related = self.graph.related_merchants(m.id)[:6]

        precedent = []
        for other, sim in similar:
            precedent.append({
                "id": other.id, "name": other.name, "status": other.status,
                "category": other.category_claimed, "pitch": other.pitch,
                "similarity": sim,
                "outcome": ("terminated -- " + (other.truth_note or "")
                            if other.status == "terminated"
                            else other.status),
                "went_bad": other.truth_bad,
                "shared_terms": self.cases.overlap_terms(
                    profile_text(m), profile_text(other)),
                "rationale": other.rationale,
            })

        memories = [
            {"id": mem.id, "text": mem.text, "kind": mem.kind,
             "confidence": mem.confidence, "source": mem.source, "similarity": sim}
            for mem, sim in self.store.search(profile_text(m), limit=4)
        ]

        return {
            "merchant": m.to_dict(),
            "policy": {
                "claimed": m.category_claimed,
                "tier": ("prohibited" if m.category_claimed in config.POLICY_PROHIBITED
                         else "restricted" if m.category_claimed in config.POLICY_RESTRICTED
                         else "accepted"),
            },
            "signals": [s.to_dict() for s in sigs],
            "graph": self.graph.subgraph(m.id, related),
            "related": [{
                "target": r.target,
                "name": self.by_id[r.target].name if r.target in self.by_id else r.target,
                "status": self.by_id[r.target].status if r.target in self.by_id else "?",
                "score": r.score, "routes": [p.describe() for p in r.paths],
            } for r in related],
            "precedent": precedent,
            "memories": memories,
            "decision": dec.to_dict(),
        }

    def alerts(self):
        return build_alerts(self.merchants, self.graph, self.by_id,
                            self.store, self.portfolio_volume)

    def drift(self) -> list[dict]:
        return drift_cases(self.merchants)

    def memory_list(self, kind: Optional[str] = None, limit: int = 200) -> dict:
        recs = sorted(self.store.records.values(), key=lambda m: m.written_at, reverse=True)
        if kind:
            recs = [r for r in recs if r.kind == kind]
        return {
            "counts": self.store.counts(),
            "records": [r.to_dict() for r in recs[:limit]],
            "log": [r.to_dict() for r in self.store.log[-40:]][::-1],
        }

    # -- writes -------------------------------------------------------------
    def record_decision(self, merchant_id: str, action: str, rationale: str,
                        analyst: str = "analyst.you") -> dict:
        m = self.by_id.get(merchant_id)
        if m is None:
            return {"error": "unknown merchant"}
        status = {"approve": "approved", "conditions": "approved",
                  "decline": "declined", "escalate": "pending"}.get(action, "pending")
        m.status = status
        m.decided_at = config.DEMO_TODAY
        m.decided_by = analyst
        m.rationale = rationale.strip() or f"{action} (no rationale given)"

        polarity = "adverse" if action == "decline" else "clearing"
        rec = self.store.reconcile(
            f"{m.name}: {action} -- {m.rationale}",
            trigger=profile_text(m), kind="episodic", subject=m.id,
            category=None, polarity=polarity, confidence=0.9,
            source=analyst, observed_at=config.DEMO_TODAY, evidence=[m.id],
        )
        self._log("decision", f"{m.name} {status}",
                  (rationale or "").strip()[:110])
        return {"ok": True, "status": status, "reconciliation": rec.to_dict()}

    def ingest_incident(self, merchant_id: str) -> dict:
        """Confirm an outcome, distil a candidate memory, run the replay gate."""
        m = self.by_id.get(merchant_id)
        if m is None:
            return {"error": "unknown merchant"}
        before_counts = self.store.counts()
        candidate = distil(m, self.store)
        outcome = run_gate(candidate, self.merchants, self.graph, self.by_id,
                           self.store, self.portfolio_volume,
                           precedent=self._precedent)
        entry = {
            "at": _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
            "incident": {"id": m.id, "name": m.name,
                         "category": m.truth_category, "note": m.truth_note},
            **outcome.to_dict(),
            "memory_before": before_counts,
            "memory_after": self.store.counts(),
        }
        self.gate_history.append(entry)
        self._log("replay", f"{m.name} incident ingested",
                  outcome.verdict[:130])
        return entry

    def _log(self, kind: str, text: str, detail: str = "") -> None:
        self.activity.insert(0, {
            "at": _dt.datetime.now().strftime("%H:%M"),
            "kind": kind, "text": text, "detail": detail,
        })
        del self.activity[40:]

    def summary(self) -> dict:
        """The persistent bar: how are we doing, and what happened."""
        p = self.portfolio()
        v = p["vamp"]
        delta = sum(g["caught_delta"] for g in self.gate_history if g["promoted"])
        decided = sum(1 for a in self.activity if a["kind"] == "decision")
        declined = sum(1 for a in self.activity
                       if a["kind"] == "decision" and "declined" in a["text"])
        return {
            "kpis": [
                {"k": "Dispute ratio", "v": f"{v['ratio']*100:.3f}%",
                 "note": f"{v['headroom_pct']:.0f}% headroom to the {v['above_standard']*100:.2f}% line",
                 # meter tone tracks the KPI tone so the bar can't contradict the number
                 "tone": "ok" if v["ratio"] < v["above_standard"] * 0.8 else "warn",
                 "meter": min(v["ratio"] / v["above_standard"], 1.0),
                 "meter_tone": "ok" if v["ratio"] < v["above_standard"] * 0.8
                               else "warn" if v["ratio"] < v["above_standard"] else "bad"},
                {"k": "Prepaid exposure", "v": f"${p['prepaid_exposure']/1e6:.1f}M",
                 "note": "owed as service if merchants fail — not fraud", "tone": "warn"},
                {"k": "Needs a human", "v": str(p["alert_total"] + p["queue_size"]),
                 "note": f"{p['queue_size']} new, {p['alert_total']} on platform", "tone": ""},
                {"k": "Memory", "v": str(p["memory"]["active"]),
                 "note": (f"+{delta} caught on replay" if delta else "no replay yet"),
                 "tone": "ok" if delta else ""},
                {"k": "Decided today", "v": str(decided),
                 "note": (f"{declined} declined" if decided else "none yet"), "tone": ""},
            ],
            "activity": self.activity[:12],
            "activity_total": len(self.activity),
            "platform": {
                "name": config.PLATFORM_NAME, "countries": config.COUNTRIES_SUPPORTED,
                "businesses": config.BUSINESSES_ON_PLATFORM,
                "features": config.PLATFORM_FEATURES,
            },
        }

    def dashboard(self) -> dict:
        """The fuller picture behind the five numbers in the summary bar."""
        from collections import defaultdict
        p = self.portfolio()
        active = [m for m in self.merchants if m.status == "approved"]

        postures: dict[str, dict] = defaultdict(lambda: {"count": 0, "exposure": 0.0})
        for a in self.alerts():
            postures[a.posture_label]["count"] += 1
            postures[a.posture_label]["exposure"] += a.exposure

        cats: dict[str, dict] = defaultdict(lambda: {"count": 0, "volume": 0.0})
        for m in active:
            c = cats[m.category_claimed]
            c["count"] += 1
            c["volume"] += m.monthly_volume
        top_cats = sorted(cats.items(), key=lambda kv: -kv[1]["volume"])[:8]

        reals = []
        for m in sorted((x for x in self.merchants if x.real),
                        key=lambda x: -x.monthly_volume):
            dec = decide(detect_all(m, self.graph, self.by_id,
                                    self.portfolio_volume, self.store),
                         self._precedent(m))
            reals.append({
                "id": m.id, "name": m.name, "domain": m.domain,
                "category": m.category_claimed, "pitch": m.pitch,
                "monthly_volume": m.monthly_volume, "country": m.country,
                "p_bad": dec.p_bad, "refund_rate": m.refund_rate,
                "vamp": round(m.vamp_ratio(), 5),
            })

        delta = sum(g["caught_delta"] for g in self.gate_history if g["promoted"])
        return {
            "portfolio": p,
            "postures": [{"name": k, **v} for k, v in
                         sorted(postures.items(), key=lambda kv: -kv[1]["count"])],
            "categories": [{"name": k, **v} for k, v in top_cats],
            "real_customers": reals,
            "replay_delta": delta,
            "gate_runs": len(self.gate_history),
            "activity": self.activity[:10],
            "activity_total": len(self.activity),
            "operating": {
                "cost_false_approve": config.COST_FALSE_APPROVE_USD,
                "cost_false_decline": config.COST_FALSE_DECLINE_USD,
                "ratio": round(config.COST_FALSE_APPROVE_USD /
                               config.COST_FALSE_DECLINE_USD, 1),
                "threshold": round(config.DECLINE_THRESHOLD, 4),
            },
            "platform": {
                "name": config.PLATFORM_NAME,
                "countries": config.COUNTRIES_SUPPORTED,
                "businesses": config.BUSINESSES_ON_PLATFORM,
                "features": config.PLATFORM_FEATURES,
            },
        }

    #: Risk bands for the distribution donut. Boundaries are the operating
    #: point (§6.2) and its neighbourhood, not arbitrary quintiles.
    RISK_BANDS = [
        ("Low risk",       0.00, 0.04, "ok"),
        ("Medium risk",    0.04, config.DECLINE_THRESHOLD, "warn"),
        ("High risk",      config.DECLINE_THRESHOLD, config.DECLINE_THRESHOLD * 2, "high"),
        ("Very high risk", config.DECLINE_THRESHOLD * 2, 1.01, "bad"),
    ]

    def _score(self, m: Merchant) -> float:
        return decide(detect_all(m, self.graph, self.by_id, self.portfolio_volume,
                                 self.store), self._precedent(m)).p_bad

    def overview(self) -> dict:
        """Everything the Overview screen renders."""
        from collections import Counter
        p = self.portfolio()
        active = [m for m in self.merchants if m.status == "approved"]

        # scoring every active merchant is too slow for a page load; score the
        # ones that can actually move (any alert or signal) and band the rest low
        scored: dict[str, float] = {}
        for a in self.alerts():
            scored[a.merchant_id] = a.p_bad
        for m in self.merchants:
            if m.status == "pending":
                scored[m.id] = self._score(m)

        bands = Counter()
        for m in active:
            s = scored.get(m.id, 0.0)
            for name, lo, hi, _tone in self.RISK_BANDS:
                if lo <= s < hi:
                    bands[name] += 1
                    break
        total = sum(bands.values()) or 1
        dist = [{"name": n, "tone": t, "count": bands.get(n, 0),
                 "pct": round(bands.get(n, 0) / total * 100, 1)}
                for n, _lo, _hi, t in self.RISK_BANDS]

        recent = []
        for m in sorted((x for x in self.merchants if x.decided_at),
                        key=lambda x: x.decided_at, reverse=True)[:6]:
            s = scored.get(m.id, self._score(m) if m.status == "pending" else 0.0)
            if m.status == "pending":
                band = next(n for n, lo, hi, _t in self.RISK_BANDS if lo <= s < hi)
                tone = next(t for n, lo, hi, t in self.RISK_BANDS if lo <= s < hi)
                band = band.replace(" risk", "")
            else:
                # for a decided merchant the outcome IS the fact; re-banding it
                # against today's model prints "low risk" beside a decline
                band = m.status
                tone = {"approved": "ok", "declined": "warn",
                        "terminated": "bad"}.get(m.status, "mute")
            recent.append({"id": m.id, "name": m.name, "band": band, "tone": tone,
                           "category": m.category_claimed, "when": m.decided_at,
                           "status": m.status, "real": m.real})

        bad = sum(1 for m in self.merchants if m.truth_bad)

        # Real month-over-month deltas, read off decision dates in the corpus.
        # Two of the four stats genuinely have no prior period to compare
        # against -- the risk banding and the memory confidence are both
        # point-in-time -- so they carry a note instead of an invented arrow.
        dates = [m.decided_at for m in self.merchants if m.decided_at]
        asof = _dt.date.fromisoformat(max(dates)) if dates else _dt.date.today()
        cut1, cut2 = asof - _dt.timedelta(days=30), asof - _dt.timedelta(days=60)

        def _delta(pred) -> tuple[str, bool]:
            recent_n = prior_n = 0
            for m in self.merchants:
                if not m.decided_at or not pred(m):
                    continue
                d = _dt.date.fromisoformat(m.decided_at)
                if cut1 < d <= asof:
                    recent_n += 1
                elif cut2 < d <= cut1:
                    prior_n += 1
            if not prior_n:
                return (f"+{recent_n}" if recent_n else "flat"), True
            change = (recent_n - prior_n) / prior_n * 100
            return f"{change:+.0f}%", change >= 0

        d_new, up_new = _delta(lambda m: m.status == "approved")
        d_dec, up_dec = _delta(lambda m: m.status in ("approved", "declined"))
        conf = p["memory"]["mean_confidence"]

        return {
            "stats": [
                {"k": "Total merchants", "v": f"{p['approved']:,}",
                 "delta": d_new, "up": up_new, "note": "approved and live",
                 "tone": "lime", "icon": "users"},
                {"k": "High risk merchants",
                 "v": str(bands.get("High risk", 0) + bands.get("Very high risk", 0)),
                 "note": f"at or above the {config.DECLINE_THRESHOLD:.1%} operating point",
                 "tone": "red", "icon": "shield"},
                {"k": "Decisions on file", "v": f"{p['approved'] + p['declined']:,}",
                 "delta": d_dec, "up": up_dec, "note": "approvals and declines",
                 "tone": "purple", "icon": "chart"},
                {"k": "Memory confidence", "v": f"{conf * 100:.1f}%",
                 "note": f"mean across {p['memory']['active']} records in force",
                 "tone": "blue", "icon": "target"},
            ],
            "distribution": dist, "distribution_total": total,
            "recent": recent,
            "activity": self.activity[:6], "activity_total": len(self.activity),
            "confirmed_bad": bad,
        }

    def directory(self, q: str = "", band: str = "", limit: int = 40,
                  offset: int = 0) -> dict:
        """Searchable merchant directory over the whole corpus."""
        pool = [m for m in self.merchants if m.status in ("approved", "pending",
                                                          "terminated")]
        if q:
            ql = q.lower()
            pool = [m for m in pool if ql in m.name.lower()
                    or ql in m.domain.lower() or ql in m.category_claimed.lower()]
        scored = {a.merchant_id: a.p_bad for a in self.alerts()}
        rows = []
        for m in pool:
            s = scored.get(m.id)
            if s is None:
                s = self._score(m) if m.status == "pending" else 0.0
            name = next(n for n, lo, hi, _t in self.RISK_BANDS if lo <= s < hi)
            tone = next(t for n, lo, hi, t in self.RISK_BANDS if lo <= s < hi)
            if band and name != band:
                continue
            rows.append({"id": m.id, "name": m.name, "domain": m.domain,
                         "category": m.category_claimed, "country": m.country,
                         "status": m.status, "p_bad": round(s, 4),
                         "band": name, "tone": tone, "real": m.real,
                         "volume": m.monthly_volume,
                         "score": round(100 - s * 100),
                         "last": m.last_observed_at or m.decided_at or m.applied_at})
        rows.sort(key=lambda r: (-r["p_bad"], -r["volume"]))
        return {"total": len(rows), "rows": rows[offset:offset + limit],
                "offset": offset, "limit": limit,
                "bands": [b[0] for b in self.RISK_BANDS]}

    def ask(self, text: str) -> dict:
        r = self.conversation.handle(text).to_dict()
        act = (r.get("memory_action") or {}).get("action")
        if act:
            self._log("memory", f"You told it something — memory {act}",
                      text.strip()[:110])
        return r

    def transcript(self) -> list[dict]:
        return self.conversation.transcript

    def replay_now(self) -> dict:
        return replay(self.merchants, self.graph, self.by_id, self.store,
                      self.portfolio_volume, precedent=self._precedent).to_dict()
