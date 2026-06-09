"""
graph.py — in-memory typed graph over BaseStatement/EntityInstance objects.

No database, no MCP server. Load a set of instances, build indexes, run
BFS and named queries directly against the Python objects.

Typical usage:

    from graph import Graph
    import scandal_instances
    g = Graph.from_module(scandal_instances)
    g.bfs(['sib:persona:count_von_kramm'], max_hops=2)
"""

from __future__ import annotations
from collections import defaultdict
from typing import Iterable, Type


"""
## Helpers

Duck-typed predicates used during graph construction. Avoiding a direct
import of `EntityInstance` and `BaseStatement` keeps `graph.py` decoupled
from the schema module — any object that has the right attributes will be
indexed correctly.
"""


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
    def from_module(cls, module) -> Graph:
        """Build a Graph from all EntityInstance values in a module's namespace."""
        return cls(
            v for v in vars(module).values()
            if _is_entity(v) and not isinstance(v, type)
        )

    # ── Basic traversal ────────────────────────────────────────────────────

    def edges_from(self, entity_id: str,
                   pred_type=None,
                   truth=None) -> list:
        """Outward edges from entity_id, optionally filtered by type and truth_status."""
        edges = self.out_edges.get(entity_id, [])
        if pred_type:
            edges = [e for e in edges if isinstance(e, pred_type)]
        if truth:
            truth_set = {truth} if not isinstance(truth, (set, list, tuple)) else set(truth)
            edges = [e for e in edges if e.truth_status.value in truth_set or e.truth_status in truth_set]
        return edges

    def edges_to(self, entity_id: str,
                 pred_type=None,
                 truth=None) -> list:
        """Inward edges to entity_id, optionally filtered by type and truth_status."""
        edges = self.in_edges.get(entity_id, [])
        if pred_type:
            edges = [e for e in edges if isinstance(e, pred_type)]
        if truth:
            truth_set = {truth} if not isinstance(truth, (set, list, tuple)) else set(truth)
            edges = [e for e in edges if e.truth_status.value in truth_set or e.truth_status in truth_set]
        return edges

    # ── BFS ───────────────────────────────────────────────────────────────

    def bfs(self, seed_ids: list[str],
            max_hops: int = 3,
            pred_types=None,
            truth_values=('asserted_true',)) -> list[set[str]]:
        """BFS from seed_ids. Returns a list of sets — one per hop layer
        (layer 0 = seeds). Traverses outward edges only.

        truth_values: tuple of truth_status values to follow. Default is
        asserted-only (the first-order asserted graph). Pass
        ('asserted_true', 'disputed', 'hypothetical') to traverse all.

        pred_types: if given, a list of predicate classes to follow;
        others are ignored. None means follow all.
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
                    # Navigate to the object entity.
                    obj_id = edge.object_.id
                    if obj_id not in visited:
                        visited.add(obj_id)
                        next_frontier.add(obj_id)
                    # The edge itself is in V; add it too so higher-order
                    # predicates can follow it in later hops.
                    if edge.id not in visited:
                        visited.add(edge.id)
                        next_frontier.add(edge.id)
            layers.append(next_frontier)
            frontier = next_frontier
            if not frontier:
                break

        return layers

    # ── Transitive closure ────────────────────────────────────────────────

    def transitive_closure(self, entity_id: str,
                           pred_type,
                           truth_values=('asserted_true',)) -> set[str]:
        """All entities reachable from entity_id by following pred_type
        transitively. Does not include entity_id itself."""
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

    # ── Display helpers ────────────────────────────────────────────────────

    def describe(self, entity_id: str) -> str:
        """Human-readable description of an instance by id."""
        inst = self.by_id.get(entity_id)
        if inst is None:
            return f"<not found: {entity_id}>"
        if _is_statement(inst):
            subj = getattr(inst.subject, 'display_name', inst.subject.id)
            obj  = getattr(inst.object_, 'display_name', inst.object_.id)
            return f"{type(inst).__name__}({subj} → {obj})"
        name = getattr(inst, 'display_name', None) or getattr(inst, 'label', None) or entity_id
        return f"{type(inst).__name__}({name})"

    def print_edges(self, edges: list, indent: int = 2) -> None:
        pad = ' ' * indent
        for e in edges:
            subj = getattr(e.subject, 'display_name', e.subject.id)
            obj  = getattr(e.object_, 'display_name', None) or self.describe(e.object_.id)
            ts   = e.truth_status.value
            print(f"{pad}{type(e).__name__}:  {subj}  →  {obj}  [{ts}]")
