"""The context graph.

Section 5.1 of the problem statement: the disqualifying signal is usually not
an attribute of the applicant, it is a *relationship* to something already
known. This module turns merchants into entities and edges so that
"two hops from a merchant we terminated" becomes a query rather than something
an analyst happens to remember.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Iterable, Optional

from .corpus import Merchant

#: edge label -> how much weight the relationship carries as a risk signal.
#: A shared payout branch is far more meaningful than a shared registrar.
EDGE_WEIGHT = {
    "controlled_by": 1.0,
    "pays_out_to": 1.0,
    "banks_at": 0.85,
    "payout_named": 0.90,
    "reuses_terms_of": 0.95,
    "shares_template_with": 0.45,
    "resolves_to": 0.30,
    "registered_via": 0.15,
    "fulfils_via": 0.35,
    "incorporated_in": 0.10,
}

#: Kinds we refuse to traverse through at any degree: essentially every indie
#: merchant shares a registrar and a country, so they connect everything to
#: everything and explain nothing.
NOISY_BRIDGES = {"registrar", "country"}


@dataclass(frozen=True)
class Node:
    id: str
    kind: str
    label: str


@dataclass(frozen=True)
class Edge:
    src: str
    dst: str
    label: str

    @property
    def weight(self) -> float:
        return EDGE_WEIGHT.get(self.label, 0.2)


@dataclass
class Related:
    """A related merchant plus every distinct route that reaches it."""
    target: str
    paths: list["Path"]
    score: float

    @property
    def corroboration(self) -> int:
        return len(self.paths)

    def bridges(self) -> list[str]:
        return [p.hops[0][2] for p in self.paths if p.hops]


@dataclass
class Path:
    """A route from an applicant to another merchant, with its explanation."""
    target: str
    hops: list[tuple[str, str, str]]   # (from_label, edge_label, to_label)
    score: float

    @property
    def length(self) -> int:
        return len(self.hops)

    def describe(self) -> str:
        if not self.hops:
            return ""
        parts = [self.hops[0][0]]
        for _src, label, dst in self.hops:
            parts.append(f"--{label}--> {dst}")
        return " ".join(parts)


class ContextGraph:
    def __init__(self) -> None:
        self.nodes: dict[str, Node] = {}
        self.adj: dict[str, list[Edge]] = defaultdict(list)
        self.merchant_node: dict[str, str] = {}

    # -- construction -------------------------------------------------------
    def add_node(self, node_id: str, kind: str, label: str) -> str:
        if node_id not in self.nodes:
            self.nodes[node_id] = Node(node_id, kind, label)
        return node_id

    def add_edge(self, src: str, dst: str, label: str) -> None:
        self.adj[src].append(Edge(src, dst, label))
        self.adj[dst].append(Edge(dst, src, label))

    def ingest(self, m: Merchant) -> None:
        mid = self.add_node(f"merchant:{m.id}", "merchant", m.name)
        self.merchant_node[m.id] = mid

        def link(node_id: str, kind: str, label: str, edge: str) -> None:
            self.add_edge(mid, self.add_node(node_id, kind, label), edge)

        link(f"person:{m.founder}", "person", m.founder, "controlled_by")
        link(f"payout:{m.payout_iban}", "payout_account", m.payout_iban, "pays_out_to")
        link(f"branch:{m.payout_branch}", "bank_branch", m.payout_branch, "banks_at")
        link(f"holder:{m.payout_holder}", "payout_holder", m.payout_holder, "payout_named")
        link(f"terms:{m.terms_hash}", "terms_page", f"terms {m.terms_hash}", "reuses_terms_of")
        link(f"tpl:{m.site_template}", "site_template", m.site_template, "shares_template_with")
        link(f"ns:{m.nameserver}", "nameserver", m.nameserver, "resolves_to")
        link(f"reg:{m.registrar}", "registrar", m.registrar, "registered_via")
        link(f"country:{m.country}", "country", m.country, "incorporated_in")
        for ch in m.fulfilment:
            link(f"channel:{ch}", "channel", ch, "fulfils_via")

    @classmethod
    def build(cls, merchants: Iterable[Merchant]) -> "ContextGraph":
        g = cls()
        for m in merchants:
            g.ingest(m)
        return g

    # -- queries ------------------------------------------------------------
    def neighbours(self, node_id: str) -> list[Edge]:
        return self.adj.get(node_id, [])

    def degree(self, node_id: str) -> int:
        return len(self.adj.get(node_id, []))

    def related_merchants(
        self,
        merchant_id: str,
        max_hops: int = 2,
        max_bridge_degree: int = 40,
        max_paths_per_target: int = 5,
    ) -> list[Related]:
        """Merchants reachable from this one, with every route that got there.

        ``max_bridge_degree`` is what makes this useful rather than noise: a
        nameserver shared by 900 merchants explains nothing, so we refuse to
        traverse through hub nodes. A payout branch shared by three merchants
        explains a great deal.

        Independent routes are combined with a noisy-OR rather than a max, so
        four weak corroborating links can outweigh one strong one -- which is
        how a human analyst actually reasons about this.
        """
        start = self.merchant_node.get(merchant_id)
        if start is None:
            return []

        by_target: dict[str, dict[str, Path]] = defaultdict(dict)
        q: deque[tuple[str, list[tuple[str, str, str]], float]] = deque()
        q.append((start, [], 1.0))
        visited_bridges: set[str] = set()

        while q:
            node_id, hops, score = q.popleft()
            if len(hops) >= max_hops:
                continue
            for edge in self.neighbours(node_id):
                nxt = self.nodes[edge.dst]
                if nxt.kind in NOISY_BRIDGES:
                    continue
                if nxt.kind != "merchant" and self.degree(edge.dst) > max_bridge_degree:
                    continue

                hop = (self.nodes[edge.src].label, edge.label, nxt.label)
                new_hops = hops + [hop]
                new_score = score * edge.weight

                if nxt.kind == "merchant":
                    if edge.dst == start:
                        continue
                    target = edge.dst.split(":", 1)[1]
                    bridge = new_hops[0][2] if new_hops else ""
                    prev = by_target[target].get(bridge)
                    if prev is None or new_score > prev.score:
                        by_target[target][bridge] = Path(target, new_hops, round(new_score, 4))
                    continue

                if edge.dst in visited_bridges:
                    continue
                visited_bridges.add(edge.dst)
                q.append((edge.dst, new_hops, new_score))

        out: list[Related] = []
        for target, paths_by_bridge in by_target.items():
            paths = sorted(paths_by_bridge.values(), key=lambda p: -p.score)
            paths = paths[:max_paths_per_target]
            # noisy-OR over independent routes
            miss = 1.0
            for p in paths:
                miss *= (1.0 - min(p.score, 0.99))
            out.append(Related(target, paths, round(1.0 - miss, 4)))
        return sorted(out, key=lambda r: (-r.score, -r.corroboration))

    def subgraph(self, merchant_id: str, related: list[Related]) -> dict:
        """Node/edge payload for the case-brief visualisation."""
        start = self.merchant_node.get(merchant_id)
        if start is None:
            return {"nodes": [], "edges": []}
        nodes: dict[str, dict] = {
            start: {"id": start, "kind": "merchant", "label": self.nodes[start].label,
                    "role": "applicant"}
        }
        edges: list[dict] = []
        seen_edges: set[tuple[str, str, str]] = set()

        for rel in related:
            for path in rel.paths:
                cur = start
                for _src_label, label, dst_label in path.hops:
                    dst = None
                    for e in self.neighbours(cur):
                        if self.nodes[e.dst].label == dst_label and e.label == label:
                            dst = e.dst
                            break
                    if dst is None:
                        break
                    if dst not in nodes:
                        kind = self.nodes[dst].kind
                        nodes[dst] = {
                            "id": dst, "kind": kind, "label": dst_label,
                            "role": "related_merchant" if kind == "merchant" else "bridge",
                        }
                    key = (cur, dst, label)
                    if key not in seen_edges:
                        seen_edges.add(key)
                        edges.append({"src": cur, "dst": dst, "label": label,
                                      "weight": EDGE_WEIGHT.get(label, 0.2)})
                    cur = dst
        return {"nodes": list(nodes.values()), "edges": edges}

    def stats(self) -> dict:
        kinds: dict[str, int] = defaultdict(int)
        for n in self.nodes.values():
            kinds[n.kind] += 1
        return {
            "nodes": len(self.nodes),
            "edges": sum(len(v) for v in self.adj.values()) // 2,
            "by_kind": dict(sorted(kinds.items(), key=lambda kv: -kv[1])),
        }
