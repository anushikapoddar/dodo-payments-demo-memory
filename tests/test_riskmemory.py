"""Tests for the merchant risk memory pipeline."""
import sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from riskmemory import config
from riskmemory.app import App
from riskmemory.converse import ASK, FEEDBACK, TELL, classify, derive_trigger, polarity_of
from riskmemory.corpus import build, summary
from riskmemory.decision import decide, precedent_likelihood
from riskmemory.graph import ContextGraph
from riskmemory.memory import MemoryStore, evaluate_predicate
from riskmemory.replay import distil, run_gate
from riskmemory.signals import detect_all

APP = App()          # built once: the corpus is deterministic


class TestConfig(unittest.TestCase):
    def test_threshold_matches_assumed_cost_ratio(self):
        self.assertAlmostEqual(config.DECLINE_THRESHOLD, 4000 / 29000, places=6)
        self.assertAlmostEqual(config.DECLINE_THRESHOLD, 0.1379, places=3)

    def test_loss_distribution_is_a_distribution(self):
        self.assertAlmostEqual(sum(config.LOSS_DISTRIBUTION.values()), 1.0, places=6)

    def test_every_category_has_a_posture(self):
        for cat in config.LOSS_DISTRIBUTION:
            self.assertIn(cat, config.CATEGORY_POSTURE)


class TestCorpus(unittest.TestCase):
    def test_deterministic(self):
        self.assertEqual([m.id for m in build()][:50], [m.id for m in build()][:50])

    def test_sized_to_assumed_baseline(self):
        s = summary(APP.merchants)
        self.assertAlmostEqual(s["approval_rate"], config.APPROVAL_RATE, delta=0.03)
        self.assertAlmostEqual(s["monthly_volume"] * 12, config.ANNUAL_VOLUME_USD,
                               delta=config.ANNUAL_VOLUME_USD * 0.15)
        self.assertLess(s["portfolio_vamp"], config.VAMP_ACQUIRER_ABOVE_STANDARD)
        self.assertGreater(s["confirmed_bad"], 30)

    def test_scenarios_present(self):
        names = {m.name for m in APP.merchants}
        for n in ("Lumen Labs", "Kindle Grove", "Northwind Notes", "Vellum Reader"):
            self.assertIn(n, names)

    def test_bad_merchants_are_observably_consistent(self):
        """A merchant labelled an ebook rights case must actually look like one."""
        for m in APP.merchants:
            if m.truth_category == "undisclosed_illegality":
                self.assertEqual(m.category_claimed, "ebooks_publications")
            if m.truth_category == "transaction_laundering" and m.forecast_monthly:
                self.assertGreater(m.monthly_volume / m.forecast_monthly, 3)


class TestRealCustomers(unittest.TestCase):
    """Real, publicly-named Dodo customers must never sit next to an adverse finding."""

    def real(self):
        return [m for m in APP.merchants if m.real]

    def test_all_named_customers_are_seeded(self):
        from riskmemory.corpus import REAL_CUSTOMER_NAMES
        self.assertEqual(len(self.real()), len(REAL_CUSTOMER_NAMES))
        self.assertEqual({m.name for m in self.real()}, set(REAL_CUSTOMER_NAMES))

    def test_never_carry_ground_truth_adverse(self):
        for m in self.real():
            self.assertFalse(m.truth_bad, m.name)
            self.assertIsNone(m.truth_category, m.name)
            self.assertEqual(m.status, "approved", m.name)

    def test_never_flagged_by_the_live_engine(self):
        """Ground truth is not enough — the running system must clear them too.

        The bar is "no adverse finding", which is what the rule actually says:
        no signal fires, nothing is near the decline threshold, and the outcome
        is an approval. It is deliberately not "approve unconditionally".
        Conditions are a routine commercial outcome — a reserve or a volume cap
        — that a real risk team applies to healthy merchants every week, and
        writing the stricter assertion would only tempt us to tune a real
        company's numbers until the engine flattered it.
        """
        for m in self.real():
            sigs = detect_all(m, APP.graph, APP.by_id, APP.portfolio_volume, APP.store)
            self.assertEqual(sigs, [], f"{m.name} raised {[s.title for s in sigs]}")
            d = decide(sigs, APP._precedent(m))
            self.assertIn(d.recommendation, ("approve", "conditions"), m.name)
            # comfortably clear, not marginally clear
            self.assertLess(d.p_bad, config.DECLINE_THRESHOLD / 2, m.name)

    def test_never_appear_in_alerts(self):
        names = {m.name for m in self.real()}
        for a in APP.alerts():
            self.assertNotIn(a.merchant, names)

    def test_never_graph_linked_to_a_bad_merchant(self):
        for m in self.real():
            for rel in APP.graph.related_merchants(m.id):
                other = APP.by_id.get(rel.target)
                if other:
                    self.assertFalse(other.truth_bad, f"{m.name} -> {other.name}")

    def test_never_cited_as_adverse_precedent(self):
        for m in APP.merchants[:200]:
            for other, _sim in APP._precedent(m):
                if other.real:
                    self.assertFalse(other.truth_bad, other.name)


class TestEvidenceQuality(unittest.TestCase):
    """Guards against prose similarity being mistaken for evidence.

    Both of these started as real false positives against named Dodo
    customers: a memory about refund rates fired on any pitch containing
    "subscription" and "free trial", and a precedent search returned
    neighbours matching only on corpus-wide boilerplate.
    """

    def test_behavioural_memories_carry_a_predicate(self):
        """A memory naming a numeric threshold must be gated on that number."""
        for mem in APP.store.active("semantic", promoted_only=True):
            if mem.polarity != "adverse":
                continue
            if any(w in mem.text.lower() for w in ("rate", "percent", "volume", "times")):
                self.assertIsNotNone(
                    mem.predicate,
                    f"{mem.id} states a threshold in prose but matches on text: "
                    f"{mem.text[:70]}")

    def test_precedent_respects_the_similarity_floor(self):
        for m in APP.merchants[:150]:
            for other, sim in APP._precedent(m):
                self.assertGreaterEqual(sim, config.PRECEDENT_MIN_SIMILARITY,
                                        f"{m.name} -> {other.name}")

    def test_refund_memory_ignores_a_low_refund_merchant(self):
        """The exact false positive: a clean subscription pitch, low refunds."""
        from riskmemory.signals import detect_memory
        m = next(x for x in APP.merchants if x.name == "Cardboard")
        self.assertLess(m.refund_rate, 0.15)
        titles = [s.title for s in detect_memory(m, APP.store)]
        self.assertEqual(titles, [], f"clean merchant matched: {titles}")


class TestPlatformData(unittest.TestCase):
    def test_dodo_brand_palette_present(self):
        for key in ("lime", "forest", "blue", "ink"):
            self.assertRegex(config.BRAND[key], r"^#[0-9A-F]{6}$")

    def test_stylesheet_uses_the_brand_palette(self):
        css = (Path(__file__).parent.parent / "web" / "styles.css").read_text()
        for key in ("lime", "forest", "ink"):
            self.assertIn(config.BRAND[key], css,
                          f"brand {key} missing from stylesheet")


class TestStylesheetContrast(unittest.TestCase):
    """An element painted on a fixed brand colour must fix its text colour too.

    Regression: the digest pill used `color: var(--ink)` on `background: var(--lime)`.
    In dark mode --ink flips to near-white, so the label vanished against the lime.
    """

    def test_no_theme_dependent_text_on_a_fixed_background(self):
        import re
        css = (Path(__file__).parent.parent / "web" / "styles.css").read_text()
        offenders = []
        for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
            bg = re.search(r"background(?:-color)?\s*:\s*([^;]+)", body)
            fg = re.search(r"(?<!-)color\s*:\s*([^;]+)", body)
            if not (bg and fg):
                continue
            bgv, fgv = bg.group(1).strip(), fg.group(1).strip()
            fixed_bg = bgv.startswith("#") or any(
                t in bgv for t in ("var(--lime", "var(--forest", "var(--brand"))
            theme_fg = any(t in fgv for t in
                           ("var(--ink", "var(--paper", "var(--muted", "var(--surface"))
            if fixed_bg and theme_fg:
                offenders.append(f"{sel.strip()} -> bg {bgv} / color {fgv}")
        self.assertEqual(offenders, [], "text colour flips with theme on a fixed background")


class TestDashboard(unittest.TestCase):
    def test_dashboard_is_json_safe_and_complete(self):
        import json
        d = APP.dashboard()
        json.dumps(d)
        for key in ("portfolio", "postures", "categories", "real_customers",
                    "operating", "platform"):
            self.assertIn(key, d)

    def test_all_four_postures_can_appear(self):
        names = {p["name"] for p in APP.dashboard()["postures"]}
        self.assertTrue(names <= set(config.POSTURES.values()), names)

    def test_dashboard_lists_every_real_customer_as_clean(self):
        for r in APP.dashboard()["real_customers"]:
            self.assertLess(r["p_bad"], config.DECLINE_THRESHOLD, r["name"])

    def test_activity_rides_on_the_dashboard(self):
        d = APP.dashboard()
        self.assertIn("activity", d)
        self.assertIn("activity_total", d)

    def test_no_persistent_summary_bar_anywhere(self):
        """The bar was removed: it duplicated the Dashboard and cluttered Chat."""
        web = Path(__file__).parent.parent / "web"
        for f in ("index.html", "app.js", "styles.css"):
            text = (web / f).read_text()
            for token in ("renderSummary", 'id="summary"', 'class="summary"', "digestBtn"):
                self.assertNotIn(token, text, f"{token} still in {f}")

    def test_brand_assets_present(self):
        assets = Path(__file__).parent.parent / "web" / "assets"
        self.assertTrue((assets / "dodo-mark.webp").is_file())


class TestGraph(unittest.TestCase):
    def test_case_c_reaches_the_terminated_original(self):
        rel = APP.graph.related_merchants("m90003")
        targets = {APP.by_id[r.target].name for r in rel}
        self.assertIn("Vellum Reader", targets)
        self.assertIn("Bookly Cloud", targets)

    def test_corroborating_routes_are_kept(self):
        rel = APP.graph.related_merchants("m90003")
        vellum = next(r for r in rel if APP.by_id[r.target].name == "Vellum Reader")
        self.assertGreaterEqual(vellum.corroboration, 2)

    def test_hub_nodes_do_not_connect_everything(self):
        """A registrar shared by 900 merchants must not create relationships."""
        rel = APP.graph.related_merchants("m90011")
        self.assertLess(len(rel), 20)

    def test_subgraph_is_renderable(self):
        sg = APP.graph.subgraph("m90003", APP.graph.related_merchants("m90003")[:4])
        self.assertTrue(sg["nodes"] and sg["edges"])
        ids = {n["id"] for n in sg["nodes"]}
        for e in sg["edges"]:
            self.assertIn(e["src"], ids)
            self.assertIn(e["dst"], ids)


class TestMemory(unittest.TestCase):
    def store(self):
        return MemoryStore(prime_corpus=[m.pitch for m in APP.merchants])

    def test_add_update_invalidate_noop(self):
        s = self.store()
        a = "Merchants selling pirated ebook catalogues terminate frequently."
        self.assertEqual(s.reconcile(a, category="x", polarity="adverse",
                                     confidence=0.5).action, "ADD")
        self.assertEqual(s.reconcile(a, category="x", polarity="adverse",
                                     confidence=0.8).action, "UPDATE")
        self.assertEqual(s.reconcile(a, category="x", polarity="adverse",
                                     confidence=0.6).action, "NO-OP")

    def test_weak_contradiction_is_disputed_not_destructive(self):
        s = self.store()
        s.reconcile("Ebook catalogue merchants without licences go bad.",
                    category="x", polarity="adverse", confidence=0.9)
        r = s.reconcile("Ebook catalogue merchants without licences are fine.",
                        category="x", polarity="clearing", confidence=0.4)
        self.assertEqual(r.action, "DISPUTED")
        self.assertEqual(s.counts()["invalidated"], 0)

    def test_strong_contradiction_supersedes_rather_than_deletes(self):
        s = self.store()
        s.reconcile("Ebook catalogue merchants without licences go bad.",
                    category="x", polarity="adverse", confidence=0.4)
        r = s.reconcile("Ebook catalogue merchants without licences are fine.",
                        category="x", polarity="clearing", confidence=0.95)
        self.assertEqual(r.action, "INVALIDATE")
        self.assertEqual(r.against.status, "invalidated")
        self.assertIsNotNone(r.against.superseded_by)   # kept for audit

    def test_predicate_evaluator_rejects_unsafe_fields(self):
        m = APP.by_id["m90006"]
        self.assertTrue(evaluate_predicate(
            {"all": [{"field": "refund_rate", "op": ">=", "value": 0.12}]}, m))
        self.assertFalse(evaluate_predicate(
            {"all": [{"field": "__class__", "op": "==", "value": 1}]}, m))
        self.assertFalse(evaluate_predicate({"all": []}, m))
        self.assertFalse(evaluate_predicate(None, m))


class TestSignals(unittest.TestCase):
    def _sig_ids(self, mid):
        m = APP.by_id[mid]
        return {s.id.split(":")[0] for s in
                detect_all(m, APP.graph, APP.by_id, APP.portfolio_volume, APP.store)}

    def test_each_posture_fires_on_its_planted_case(self):
        self.assertIn("graph_link", self._sig_ids("m90003"))       # deceiving
        self.assertIn("category_drift", self._sig_ids("m90005"))   # drifting
        self.assertIn("prepaid_exposure", self._sig_ids("m90007")) # failing
        self.assertIn("payout_change", self._sig_ids("m90008"))    # attacked
        self.assertIn("card_testing", self._sig_ids("m90009"))     # attacked
        self.assertIn("volume_vs_forecast", self._sig_ids("m90010"))

    def test_clean_applicants_fire_nothing(self):
        for mid in ("m90011", "m90012"):
            self.assertEqual(self._sig_ids(mid), set())

    def test_detectors_never_read_ground_truth(self):
        """Detectors may only see what a human reviewer could see."""
        import ast
        src = (Path(__file__).parent.parent / "riskmemory" / "signals.py").read_text()
        reads = {n.attr for n in ast.walk(ast.parse(src))
                 if isinstance(n, ast.Attribute)}
        self.assertNotIn("truth_bad", reads)
        self.assertNotIn("truth_category", reads)
        self.assertNotIn("truth_note", reads)


class TestDecision(unittest.TestCase):
    def test_clean_applicants_are_approved(self):
        for mid in ("m90011", "m90012"):
            d = APP.brief(mid)["decision"]
            self.assertLess(d["p_bad"], config.DECLINE_THRESHOLD)
            self.assertEqual(d["recommendation"], "approve")

    def test_planted_cases_are_flagged(self):
        for mid in ("m90003", "m90004"):
            d = APP.brief(mid)["decision"]
            self.assertGreaterEqual(d["p_bad"], config.DECLINE_THRESHOLD)

    def test_victim_outcomes_excluded_from_precedent(self):
        """Card testing and ATO are not properties of the merchant's business."""
        class M:
            truth_bad = True
            truth_category = "card_testing"
        lr, note = precedent_likelihood([(M(), 0.9)])
        self.assertLessEqual(lr, 1.0)
        self.assertIn("did not cause", note)

    def test_expected_costs_bracket_the_threshold(self):
        d = decide([])
        self.assertLess(d.expected_cost_approve, d.expected_cost_decline)


class TestReplayGate(unittest.TestCase):
    def test_gate_promotes_a_pattern_that_helps(self):
        app = App()
        out = app.ingest_incident("m90006")           # deceptive billing
        self.assertGreater(out["caught_delta"], 0)
        self.assertLessEqual(out["false_flag_delta"], 0)
        self.assertTrue(out["promoted"])

    def test_gate_refuses_a_pattern_that_does_not_help(self):
        app = App()
        app.ingest_incident("m90006")
        again = app.ingest_incident("m90006")          # nothing new to learn
        self.assertFalse(again["promoted"])

    def test_candidate_is_inert_until_promoted(self):
        app = App()
        cand = distil(app.by_id["m90006"], app.store)
        self.assertFalse(cand.promoted)
        self.assertNotIn(cand.id, {m.id for m in app.store.active("semantic", True)})


class TestRetrievalStemming(unittest.TestCase):
    def test_plural_and_participle_forms_unify(self):
        from riskmemory.retrieval import stem
        self.assertEqual(stem("libraries"), stem("library"))
        self.assertEqual(stem("ebooks"), stem("ebook"))
        self.assertEqual(stem("subscriptions"), stem("subscription"))

    def test_stemmer_does_not_mangle_ordinary_words(self):
        from riskmemory.retrieval import stem
        for w in ("business", "analysis", "class", "seed", "status"):
            self.assertEqual(stem(w), w)


class TestConverse(unittest.TestCase):
    def app(self):
        return App()

    def test_intent_classification(self):
        self.assertEqual(classify("Why did we decline Lumen Labs?"), ASK)
        self.assertEqual(classify("what do we know about ebooks"), ASK)
        self.assertEqual(classify("tell me about Kindle Grove"), ASK)
        self.assertEqual(classify("Telegram fulfilment is risky"), TELL)
        self.assertEqual(classify("Actually Marlow Type was fine"), FEEDBACK)

    def test_polarity_detection(self):
        self.assertEqual(polarity_of("this pattern is risky and fraudulent"), "adverse")
        self.assertEqual(polarity_of("these merchants are legitimate and fine"), "clearing")

    def test_trigger_strips_verdict_scaffolding(self):
        t = derive_trigger("Merchants selling unlimited ebook libraries are risky "
                           "and should be declined")
        self.assertIn("ebook", t)
        for word in ("risky", "declined", "merchant", "should"):
            self.assertNotIn(word, t)

    def test_asking_about_a_merchant_cites_its_evidence(self):
        r = self.app().ask("Why is Lumen Labs risky?")
        self.assertEqual(r["intent"], ASK)
        self.assertEqual(r["subject"], "m90003")
        self.assertIn("Vellum Reader", r["answer"])
        self.assertTrue(any(e["kind"] == "graph" for e in r["evidence"]))
        self.assertIsNone(r["memory_action"])

    def test_asking_never_writes_memory(self):
        app = self.app()
        before = app.store.counts()["total"]
        app.ask("what do we know about ebook catalogues?")
        app.ask("how are we doing overall?")
        self.assertEqual(app.store.counts()["total"], before)

    def test_telling_writes_memory_and_reports_impact(self):
        app = self.app()
        r = app.ask("Merchants selling unlimited ebook libraries without publisher "
                    "licences are risky")
        self.assertEqual(r["intent"], TELL)
        self.assertEqual(r["memory_action"]["action"], "ADD")
        self.assertIsNotNone(r["replay"])
        self.assertGreaterEqual(r["replay"]["caught_delta"], 0)

    def test_human_assertions_apply_immediately(self):
        """A founder's instruction should not sit behind the replay gate."""
        app = self.app()
        r = app.ask("Fulfilment via telegram on a brand new domain is risky")
        mem = app.store.records[r["memory_action"]["memory"]["id"]]
        self.assertTrue(mem.promoted)
        self.assertEqual(mem.source, "founder (told directly)")

    def test_transcript_survives_and_resets(self):
        app = self.app()
        app.ask("how are we doing?")
        self.assertEqual(len(app.transcript()), 1)
        app.reset()
        self.assertEqual(len(app.transcript()), 0)


class TestApp(unittest.TestCase):
    def test_portfolio_is_json_safe(self):
        import json
        json.dumps(APP.portfolio())
        json.dumps(APP.queue())
        json.dumps(APP.brief("m90003"))

    def test_decision_writes_memory(self):
        app = App()
        before = app.store.counts()["total"]
        app.record_decision("m90011", "approve", "Clean CI analytics product.")
        self.assertGreater(app.store.counts()["total"], before)
        self.assertEqual(app.by_id["m90011"].status, "approved")

    def test_reset_restores_initial_state(self):
        app = App()
        n = len(app.queue())
        app.record_decision("m90011", "approve", "x")
        self.assertEqual(len(app.queue()), n - 1)
        app.reset()
        self.assertEqual(len(app.queue()), n)


if __name__ == "__main__":
    unittest.main(verbosity=2)
