# Merchant Risk Memory

An anti-fragile AI memory layer for merchant intelligence and fraud detection at
Dodo Payments. It continuously learns from new merchant data, builds a contextual
memory graph, and refreshes risk assessments to uncover hidden patterns,
relationships and emerging fraud signals.

Working demo of the system described in the
[problem statement, pipeline and console design](https://claude.ai/code/artifact/31f3b9ac-a375-4662-a191-6dab48fde813).
Built as a demo for Dodo Payments — everything runs on synthetic data.

## Run it

```bash
./run.sh
```

Opens <http://127.0.0.1:8765>. **No dependencies** — Python 3.11+ standard
library only. No pip install, no npm, no build step, nothing to configure.

```bash
python3 -m unittest discover -s tests -v     # 56 tests
```

New here? Read **[HANDOFF.md](HANDOFF.md)** first — it covers the problem, the
four risk postures, the assumed numbers, and the constraints that matter.

## What it does

Eight views, each mapping to a section of the problem statement:

| View | What it shows |
|---|---|
| **Overview** | Four figures, risk distribution cut at the operating point, portfolio exposure, recent evaluations, memory activity |
| **Merchants** | The whole corpus, searchable and filterable, ranked by P(bad) |
| **Evaluations** | One worklist — new applications and on-platform alerts together |
| **Memory layer** | Every add / update / invalidate / no-op with provenance, plus the replay gate |
| **Graph explorer** | The corroborating routes between a merchant and the rest of the portfolio |
| **Alerts** | Post-approval lifecycle and drift, by risk posture |
| **Chat** | Ask, tell or correct in plain text; every answer cites its sources |
| **History** | Past conversations and what each one changed |

### The demo path

1. **Review queue** → **Lumen Labs** (~93% P(bad)). Every field on the
   application is clean. The case is carried entirely by three independent
   graph routes to *Vellum Reader*, terminated in March. Decline it with a
   rationale; watch memory reconcile the write.
2. **Alerts** → four postures side by side. Two of them — a merchant *failing*
   with $486k of prepaid service outstanding, and one *being attacked* via
   payout redirection — are invisible to any fraud-detection framing.
3. **Replay gate** → ingest **Quartz Habit** (deceptive billing). The system
   distils a pattern, replays every historical decision with and without it,
   and promotes it only because it catches **+3 more with no new false flags**.
   That delta is what "anti-fragile" means as a number.

## Talking to the memory layer

The **Ask memory** view handles three things, routed by deterministic intent
parsing rather than a model — an explanation of why a merchant was declined has
to be reproducible and traceable, and a hallucinated one would make the whole
audit trail worthless.

- **Ask** — "Why is Lumen Labs risky?", "What do we know about ebook
  catalogues?", "How are we doing overall?" Answers cite the signals, graph
  paths, precedent cases and memories they used. Asking never writes.
- **Tell** — "Merchants selling unlimited ebook libraries without publisher
  licences are risky." This writes to memory *and immediately replays it over
  past decisions*, so you see whether it catches more or just adds noise before
  you trust it.
- **Correct** — "Actually Marlow Type was fine." Recorded as a correction at
  higher confidence, superseding rather than deleting what it contradicts.

Human assertions skip the replay gate, because a founder saying so is the
authority — but the historical impact is still shown, so a rule that would
wrongly flag legitimate merchants is visible immediately rather than after it
has done damage.

## Dodo brand and real customers

Colours come from `dodopayments.com/brand`: lime `#C6FE1E`, forest `#004F32`, blue
`#1264FF`, and their actual body ink `#00160D` — a green-black, not a grey. The dark
theme uses their forest-green family rather than a neutral black. `config.BRAND` holds
the palette and a test asserts the stylesheet stays in sync with it.

All **seventeen** real, publicly-named Dodo customers are seeded — **Mole, Vibe3D,
Draftly, ReplyDaddy, CatDoes, Indilingo, Scira AI, PeerPush, IndieKit, Betide Studio,
Healthify, Parakeet AI, MATIKS, GPAI, Cardboard, SurgeGrowth, Vaya**. Product
descriptions are factual; volumes and dispute figures are illustrative and the UI
says so.

**A real merchant never appears next to an adverse finding.** Not flagged, not declined,
not in a fraud ring, not graph-linked to a terminated merchant, never cited as adverse
precedent. This is six tests plus an assertion in `build()`, not a convention — the
corpus is randomised, so a reseed could otherwise sweep a real name into the bad sample
silently. They are seeded as what they are: healthy approved merchants, which also gives
the system a genuine population to correctly leave alone.

That control population is a test as much as a feature, and it earned its keep: two
customers tripped the guard during the build, both from prose similarity being mistaken
for evidence. Both are fixed with regression tests — see the next section.

## Design decisions worth knowing

**No hardcoded "catalogue implies piracy" rule.** That insight is not policy —
it is learned from the Vellum Reader incident, arrives as a distilled memory,
and only takes effect after passing the replay gate. Hardcoding it would
pre-empt the loop the system exists to demonstrate.

**Memories carry both a text trigger and an optional structured predicate.**
Text catches *content* patterns; predicates catch *behavioural* ones. A
refund-rate pattern keyed on pitch text would never generalise, because a
deceptive-billing merchant's pitch reads like any other SaaS. This is §2.1's
content-versus-behavioural split showing up in the code.

**Victim outcomes are excluded from precedent.** Card testing and account
takeover are not properties of a merchant's business, so they must not
poison the inference for merchants selling similar products.

**Contradictions must be better evidenced to win.** A lower-confidence
contradiction is recorded as `DISPUTED` rather than silently overwriting a
well-evidenced fact — the memory-poisoning failure of §9.6. Nothing is ever
deleted; facts are superseded, so any past decision can be replayed against
the memory as it stood.

**Retrieval stems conservatively.** Without it "libraries" never matches
"library" and "ebooks" never matches "ebook", which breaks pattern matching in a
way that looks like memory simply holding nothing.

**Hub nodes are not traversed.** A nameserver shared by 900 merchants explains
nothing. A payout holder name shared by three explains a great deal.

**A memory that names a number is gated on that number.** A pattern reading
"refund rates above 15 percent" with no predicate matches on *text*, so it fires
on any pitch containing "subscription" and "free trial" — which is most of the
portfolio. Behavioural lessons gate on the observable, never on the wording.

**Precedent has a measured similarity floor of 0.20.** Below it, neighbours match
only on `subscription`, `saas`, `github`, `email` — vocabulary the whole corpus
shares. Left in, one unlucky neighbour at cosine 0.13 moves a clean merchant's
odds by 3x. Genuine precedent here sits at 0.24 and above.

**The predicate evaluator is a whitelisted interpreter,** not `eval`. Memories
are written by an automated distiller; one that could execute arbitrary code
would be a remote-code-execution hole dressed up as a learning loop.

## Layout

```
riskmemory/
  config.py     assumed constants — every §6 number lives here and nowhere else
  corpus.py     deterministic synthetic population (seed 20260820)
  graph.py      context graph, entity resolution, corroborating-path search
  retrieval.py  hand-rolled TF-IDF + cosine, no numpy
  memory.py     add/update/invalidate/no-op lifecycle, provenance, predicates
  signals.py    detectors, one family per posture
  decision.py   likelihood ratios in odds space against the 13.8% threshold
  converse.py   intent parsing and evidence-cited answers
  monitor.py    lifecycle alerts and drift reconciliation
  replay.py     distillation and the replay gate
  app.py        application state
  server.py     stdlib HTTP server
web/            vanilla JS console, no framework
tests/          56 tests
docs/           the three source documents + the merged artifact
```

## Every number here is assumed

The corpus is sized to the assumed baseline in §6.1 — 4,000 applications,
65% approval, 45 confirmed-bad, $150M annualised, VAMP under the 0.50%
acquirer line. The decline threshold of 13.8% is derived from the assumed
6:1 cost ratio in §6.2. **None of it is measured.** Replace any figure in
`config.py` and everything downstream re-derives.
