"""graph.py — in-memory typed graph over BaseStatement/EntityInstance objects.

No database, no MCP server. Load a set of instances, build indexes, run
BFS and named queries directly against the Python objects.

Typical usage:

    from ner_20260608 import load_bohemia_graph

    g = load_bohemia_graph()
    g.bfs(['sib:persona:count_von_kramm'], max_hops=2)
"""

from __future__ import annotations
from collections import defaultdict
from typing import Iterable


"""
## Helpers

Duck-typed predicates used during graph construction. Avoiding a direct
import of `EntityInstance` and `BaseStatement` keeps `graph.py` decoupled
from the schema module — any object that has the right attributes will be
indexed correctly.
"""


_WIKI_PREFIX = "https://bakerstreet.fandom.com/wiki/"


def _canonicalize_id(entity_id: str) -> str:
    if entity_id.startswith(_WIKI_PREFIX):
        return "wiki:" + entity_id[len(_WIKI_PREFIX):]
    return entity_id


def _is_entity(obj):
    return hasattr(obj, 'id') and not callable(obj)


def _is_statement(obj):
    return hasattr(obj, 'subject') and hasattr(obj, 'object_') and hasattr(obj, 'truth_status')


"""
## Graph

`Graph` is an in-memory knowledge graph indexed for O(1) neighbor lookup.
Instances are bucketed into three indexes: `by_id` for direct access,
`out_edges` keyed by `subject.id` for forward traversal, and `in_edges`
keyed by `object_.id` for backward traversal.

Because predicate instances are also entities under the unified Statement
model, they are indexed in `by_id` and can themselves appear as the
subject or object of higher-order predicates.
"""


class Graph:

    def __init__(self, instances: Iterable):
        self.by_id: dict = {}
        self.out_edges: dict[str, list] = defaultdict(list)   # subject.id -> [stmt]
        self.in_edges: dict[str, list] = defaultdict(list)    # object_.id -> [stmt]

        for inst in instances:
            if not _is_entity(inst):
                continue
            self.by_id[inst.id] = inst
            if _is_statement(inst):
                self.out_edges[inst.subject.id].append(inst)
                self.in_edges[inst.object_.id].append(inst)

    @classmethod
    def from_module(cls, module) -> "Graph":
        """Build a Graph from all EntityInstance values in a module's namespace."""
        return cls(
            v for v in vars(module).values()
            if _is_entity(v) and not isinstance(v, type)
        )

    def get(self, entity_id: str):
        """Return the instance for entity_id, normalizing wiki URLs to canonical form."""
        return self.by_id.get(_canonicalize_id(entity_id))

    def edges_from(self, entity_id: str, pred_type=None, truth=None) -> list:
        """Outward edges from entity_id, optionally filtered by type and truth_status."""
        edges = self.out_edges.get(_canonicalize_id(entity_id), [])
        if pred_type:
            edges = [e for e in edges if isinstance(e, pred_type)]
        if truth:
            truth_set = {truth} if not isinstance(truth, (set, list, tuple)) else set(truth)
            edges = [e for e in edges if e.truth_status.value in truth_set or e.truth_status in truth_set]
        return edges

    def edges_to(self, entity_id: str, pred_type=None, truth=None) -> list:
        """Inward edges to entity_id, optionally filtered by type and truth_status."""
        edges = self.in_edges.get(_canonicalize_id(entity_id), [])
        if pred_type:
            edges = [e for e in edges if isinstance(e, pred_type)]
        if truth:
            truth_set = {truth} if not isinstance(truth, (set, list, tuple)) else set(truth)
            edges = [e for e in edges if e.truth_status.value in truth_set or e.truth_status in truth_set]
        return edges

    def bfs(self, seed_ids: list[str], max_hops: int = 3, pred_types=None, truth_values=('asserted_true',)) -> list[set[str]]:
        """BFS from seed_ids. Returns one set per hop layer.

        Traverses both outward and inward edges so symmetric predicates (e.g.
        Knows) and reverse relationships (e.g. Events that Involve a person)
        are reachable regardless of the direction they were stored. The
        traversed statement instances are also added to layers so higher-order
        predicates can be followed in later hops.
        """
        visited: set[str] = set(seed_ids)
        frontier: set[str] = set(seed_ids)
        layers: list[set[str]] = [set(seed_ids)]

        for _ in range(max_hops):
            next_frontier: set[str] = set()
            for eid in frontier:
                for edge in self.out_edges.get(eid, []):
                    if pred_types and not isinstance(edge, tuple(pred_types)):
                        continue
                    if edge.truth_status.value not in truth_values:
                        continue
                    for nid in (edge.object_.id, edge.id):
                        if nid not in visited:
                            visited.add(nid)
                            next_frontier.add(nid)
                for edge in self.in_edges.get(eid, []):
                    if pred_types and not isinstance(edge, tuple(pred_types)):
                        continue
                    if edge.truth_status.value not in truth_values:
                        continue
                    for nid in (edge.subject.id, edge.id):
                        if nid not in visited:
                            visited.add(nid)
                            next_frontier.add(nid)
            layers.append(next_frontier)
            frontier = next_frontier
            if not frontier:
                break

        return layers

    def transitive_closure(self, entity_id: str, pred_type, truth_values=('asserted_true',)) -> set[str]:
        """All entities reachable from entity_id by following pred_type transitively."""
        visited: set[str] = set()
        frontier: set[str] = {entity_id}
        while frontier:
            next_f: set[str] = set()
            for eid in frontier:
                for edge in self.out_edges.get(eid, []):
                    if not isinstance(edge, pred_type):
                        continue
                    if edge.truth_status.value not in truth_values:
                        continue
                    obj_id = edge.object_.id
                    if obj_id not in visited:
                        visited.add(obj_id)
                        next_f.add(obj_id)
            frontier = next_f
        return visited

    def describe(self, entity_id: str) -> str:
        """Human-readable description of an instance by id."""
        inst = self.get(entity_id)
        if inst is None:
            return f"<not found: {entity_id}>"
        if _is_statement(inst):
            subj = getattr(inst.subject, 'display_name', inst.subject.id)
            obj = getattr(inst.object_, 'display_name', inst.object_.id)
            return f"{type(inst).__name__}({subj} → {obj})"
        name = getattr(inst, 'display_name', None) or getattr(inst, 'label', None) or entity_id
        return f"{type(inst).__name__}({name})"

    def print_edges(self, edges: list, indent: int = 2) -> None:
        pad = ' ' * indent
        for e in edges:
            subj = getattr(e.subject, 'display_name', e.subject.id)
            obj = getattr(e.object_, 'display_name', None) or self.describe(e.object_.id)
            ts = e.truth_status.value
            print(f"{pad}{type(e).__name__}:  {subj}  →  {obj}  [{ts}]")
