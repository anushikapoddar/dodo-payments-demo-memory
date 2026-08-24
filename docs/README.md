# Documents

Three source documents and the merged artifact built from them.

| File | What it is |
|---|---|
| `problem-statement.html` | Why this is a company-level risk, the four failure modes, the assumed numbers |
| `pipeline.html` | Five diagrams: end-to-end flow, the context graph, memory reconciliation, the decision, the replay gate |
| `console-design.html` | The console as built, and the four places the design and the data disagreed |
| `merchant-risk-memory.html` | All three as one tabbed page — **this is the one to share** |

## Rebuilding the merged artifact

```bash
cd docs && python3 build_merged.py
```

Edit a source document, re-run, republish. Do not edit `merchant-risk-memory.html`
directly — it is generated and your changes will be overwritten.

`build_merged.py` auto-namespaces each source document's CSS under `#tab-<id>`.
This is not optional tidiness: all three files independently define `.tbl`,
`.chip`, `.k` and `.tag` with different meanings, so concatenating their
stylesheets without scoping silently breaks two of the three tabs.
