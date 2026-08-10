"""graph.py — in-memory typed graph over BaseStatement/EntityInstance objects.

No database, no MCP server. Load a set of instances, build indexes, run
BFS and named queries directly against the Python objects.

Typical usage:

    from ner_20260608 import load_bohemia_graph

    g = load_bohemia_graph()
    g.bfs(['sib:persona:count_von_kramm'], max_hops=2)
"""

from __future__ import annotations

import warnings
from collections import defaultdict
from collections.abc import Iterable
from types import ModuleType
from typing import Any, Protocol, TypeGuard

"""
## Helpers

Duck-typed predicates used during graph construction. Avoiding a direct
import of `EntityInstance` and `BaseStatement` keeps `graph.py` decoupled
from the schema module — any object that has the right attributes will be
indexed correctly.
"""


_WIKI_PREFIX = "https://bakerstreet.fandom.com/wiki/"


class _HasId(Protocol):
    id: str


class _StatementLike(_HasId, Protocol):
    subject: _HasId
    object_: _HasId
    truth_status: object


def _canonicalize_id(entity_id: str) -> str:
    if entity_id.startswith(_WIKI_PREFIX):
        return "wiki:" + entity_id[len(_WIKI_PREFIX) :]
    return entity_id


def _is_entity(obj: object) -> TypeGuard[_HasId]:
    return hasattr(obj, "id") and not callable(obj)


def _is_statement(obj: object) -> TypeGuard[_StatementLike]:
    return (
        hasattr(obj, "subject")
        and hasattr(obj, "object_")
        and hasattr(obj, "truth_status")
    )


def _truth_value(stmt: _StatementLike) -> str:
    value = getattr(stmt, "truth_status", "hypothetical")
    return str(getattr(value, "value", value))


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
    def __init__(self, instances: Iterable[object]):
        self.by_id: dict[str, _HasId] = {}
        self.out_edges: dict[str, list[_StatementLike]] = defaultdict(list)
        self.in_edges: dict[str, list[_StatementLike]] = defaultdict(list)

        for inst in instances:
            if not _is_entity(inst):
                continue
            if inst.id in self.by_id:
                warnings.warn(
                    f"Graph: id collision on {inst.id!r} — overwriting; "
                    "use unique ids per extraction or statement_id() for canonical facts",
                    stacklevel=2,
                )
            self.by_id[inst.id] = inst
            if _is_statement(inst):
                self.out_edges[inst.subject.id].append(inst)
                self.in_edges[inst.object_.id].append(inst)

    @classmethod
    def from_module(cls, module: ModuleType) -> Graph:
        """Build a Graph from all EntityInstance values in a module's namespace."""
        return cls(
            v
            for v in vars(module).values()
            if _is_entity(v) and not isinstance(v, type)
        )

    def get(self, entity_id: str) -> _HasId | None:
        """Return the instance for entity_id, normalizing wiki URLs to canonical form."""
        return self.by_id.get(_canonicalize_id(entity_id))

    def edges_from(
        self,
        entity_id: str,
        pred_type: type[Any] | None = None,
        truth: str | Iterable[str] | None = None,
    ) -> list[_StatementLike]:
        """Outward edges from entity_id, optionally filtered by type and truth_status."""
        edges = self.out_edges.get(_canonicalize_id(entity_id), [])
        if pred_type:
            edges = [e for e in edges if isinstance(e, pred_type)]
        if truth:
            truth_set = (
                {truth} if not isinstance(truth, (set, list, tuple)) else set(truth)
            )
            edges = [e for e in edges if _truth_value(e) in truth_set]
        return edges

    def edges_to(
        self,
        entity_id: str,
        pred_type: type[Any] | None = None,
        truth: str | Iterable[str] | None = None,
    ) -> list[_StatementLike]:
        """Inward edges to entity_id, optionally filtered by type and truth_status."""
        edges = self.in_edges.get(_canonicalize_id(entity_id), [])
        if pred_type:
            edges = [e for e in edges if isinstance(e, pred_type)]
        if truth:
            truth_set = (
                {truth} if not isinstance(truth, (set, list, tuple)) else set(truth)
            )
            edges = [e for e in edges if _truth_value(e) in truth_set]
        return edges

    def bfs(
        self,
        seed_ids: list[str],
        max_hops: int = 3,
        pred_types: Iterable[type[Any]] | None = None,
        truth_values: tuple[str, ...] = ("asserted_true",),
    ) -> list[set[str]]:
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
                    if _truth_value(edge) not in truth_values:
                        continue
                    for nid in (edge.object_.id, edge.id):
                        if nid not in visited:
                            visited.add(nid)
                            next_frontier.add(nid)
                for edge in self.in_edges.get(eid, []):
                    if pred_types and not isinstance(edge, tuple(pred_types)):
                        continue
                    if _truth_value(edge) not in truth_values:
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

    def transitive_closure(
        self,
        entity_id: str,
        pred_type: type[Any],
        truth_values: tuple[str, ...] = ("asserted_true",),
    ) -> set[str]:
        """All entities reachable from entity_id by following pred_type transitively."""
        visited: set[str] = set()
        frontier: set[str] = {entity_id}
        while frontier:
            next_f: set[str] = set()
            for eid in frontier:
                for edge in self.out_edges.get(eid, []):
                    if not isinstance(edge, pred_type):
                        continue
                    if _truth_value(edge) not in truth_values:
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
        return str(inst)

    def print_edges(self, edges: Iterable[_StatementLike], indent: int = 2) -> None:
        pad = " " * indent
        for e in edges:
            print(f"{pad}{e}  [{_truth_value(e)}]")
