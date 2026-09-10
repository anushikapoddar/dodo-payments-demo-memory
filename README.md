# Merchant Risk Memory

A decision engine for merchant risk at Dodo Payments (merchant of record).
It **recommends**. A human **decides**. **Memory learns** from that decision.

Working demo on **synthetic data**. Not a production underwriting model.

## How a decision works

This is **not** a neural net trained on Dodo’s book. It is composed evidence,
shown as a probability so the arithmetic is visible.

**On every assessment**

1. Take the merchant packet Dodo already collected (signup, product, ID country,
   how they deliver). Import it — don’t retype it.
2. Start from a **prior**: ~1.7% of approvals go bad.
3. Multiply by **signals** — policy, geo, copy vs category, graph links to a
   terminated merchant, local night hours, thin web, similar past cases, what
   analysts already declined.
4. That product is **P(bad)**. Open **Why this recommendation? → The calculation**
   to see every multiplier.
5. Recommend Approve / Review / Decline. The rationale you type is what memory
   learns from.

**Where 1.7% comes from**  
It is the starting belief *before* we look at this merchant — not a rate the
engine discovered. The demo assumes ~45 confirmed-bad merchants out of ~2,600
approvals (problem statement §6.1). **Assumed, not measured.** If no signals
fire, P(bad) stays ~1.7% (a clean SaaS). 84% means “starting from 1.7%, this
stack of evidence got that strong.”

**Assumed (in `config.py`, replace when Dodo has the real figure)**

| Number | Role |
|---|---|
| 1.7% confirmed-bad among approvals | Prior |
| Wrong approve costs ~6× a wrong decline | Operating point ~13.8% |
| Each signal’s likelihood ratio | How hard a finding pushes the odds |

**Actually computed**  
Which signals fired, how they compose, who this applicant is linked to, whether
we’ve seen this *shape* before.

**What “learning” means**  
Not gradient descent. Decline a gambling-shaped merchant → the next one in that
vertical scores hotter. Unrelated SaaS does not. Too few labeled outcomes (~45)
to train a classifier; that’s why the design is retrieval + graph + memory.

**Verified vs not**

| Verified | Not verified |
|---|---|
| Clean merchants stay near the prior | That 1.7% is Dodo’s real bad rate |
| Planted cases fire (Lumen, Nightwell, services, geo) | That 84% ≈ true P(go bad) |
| Memory heats the same vertical, not the whole book | Out-of-sample performance on the live book |
| Named Dodo customers are never adverse precedent | Weights fit to historical outcomes |

One line: *we can see the right things at signup, show our working, and learn
from the analyst. We cannot yet say the percentage is Dodo’s true probability.*

## Run it

```bash
./run.sh
```

Opens <http://127.0.0.1:8765>. **No dependencies** — Python 3.11+ standard
library only. No pip install, no npm, no build step.

```bash
python3 -m unittest discover -s tests -q     # 82 tests
```

Longer context: **[HANDOFF.md](HANDOFF.md)**.

## What you’ll see

| View | What it shows |
|---|---|
| **Homepage** | Portfolio snapshot and **Assess a merchant** |
| **Assessment history** | Session assessments and recorded decisions |
| **Merchants** | The corpus, searchable, ranked by P(bad) |
| **Memory layer** | Add / update / invalidate / no-op, plus the replay gate |
| **Evaluations** | Queue and live alerts |
| **Context graph** | Corroborating routes to merchants already judged |
| **Alerts** | Post-approval lifecycle, by risk posture |

### Demo path

1. **Assess a merchant** → **Import from Dodo** (the packet they already submitted).
2. **Westbrook AP Live** — IN entity, live EST classes. Flagged at signup, no volume yet.
3. **Nightwell Academy** — same idea after they’re live; night is **IST**, not UTC.
4. **Lumen Labs** — every field looks clean; the graph still reaches a terminated merchant. Decline with a rationale; memory learns.
5. **Quill Harbor** — they ticked Services. Policy hard-decline.

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
  config.py        assumed constants — every §6 number lives here and nowhere else
  applications.py  inbound Dodo signup packets (demo inbox)
  corpus.py        deterministic synthetic population (seed 20260820)
  graph.py         context graph, entity resolution, corroborating-path search
  retrieval.py     hand-rolled TF-IDF + cosine, no numpy
  memory.py        add/update/invalidate/no-op lifecycle, provenance, predicates
  signals.py       detectors, one family per posture
  decision.py      likelihood ratios in odds space against the 13.8% threshold
  monitor.py       lifecycle alerts and drift reconciliation
  replay.py        distillation and the replay gate
  app.py           application state
  server.py        stdlib HTTP server
web/               vanilla JS console, no framework
tests/             82 tests
docs/              the three source documents + the merged artifact
```

## Every number here is assumed

The corpus is sized to the assumed baseline in §6.1 — 4,000 applications,
65% approval, 45 confirmed-bad, $150M annualised, VAMP under the 0.50%
acquirer line. The decline threshold of 13.8% is derived from the assumed
6:1 cost ratio in §6.2. **None of it is measured.** Replace any figure in
`config.py` and everything downstream re-derives.
