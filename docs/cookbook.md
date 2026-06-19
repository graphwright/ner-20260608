# Bohemia graph — cookbook

A practical guide to querying, testing, and extending the `ner_20260608` wheel.

> **Where this lives:** `docs/cookbook.md` in the source repo.
> Wheels bundle the top-level `README.md` as project metadata; to also ship
> this file, add `"docs/cookbook.md"` to `[tool.pdm.build] includes` in
> `pyproject.toml`.

---

## Installation

```bash
pip install dist/ner_20260608-0.1.0-py3-none-any.whl
# or from PyPI once published:
pip install ner_20260608
```

---

## Five-minute intro

```python
from ner_20260608 import load_bohemia_graph

g = load_bohemia_graph()          # loads bundled JSONL, ~100 ms

# Direct lookup — wiki: prefix or full URL both work
holmes = g.get("wiki:Sherlock_Holmes")
print(holmes)                     # Person('wiki:Sherlock_Holmes')

# Human-readable one-liner
print(g.describe("wiki:Irene_Adler"))   # Person(Irene Adler)

# Who does Watson know (asserted true)?
edges = g.edges_from("wiki:John_Watson", truth="asserted_true")
g.print_edges(edges)
```

---

## Interesting queries

### All people Holmes is connected to (2 hops)

```python
from ner_20260608 import load_bohemia_graph

g = load_bohemia_graph()
layers = g.bfs(["wiki:Sherlock_Holmes"], max_hops=2)
for i, layer in enumerate(layers):
    print(f"hop {i}: {len(layer)} nodes")

# Flatten and filter to Person nodes only
from ner_20260608.holmes_schema import Person

all_ids = set().union(*layers)
people = [g.get(eid) for eid in all_ids if isinstance(g.get(eid), Person)]
print([p.display_name for p in people if p])
```

### Events involving Irene Adler

```python
from ner_20260608.holmes_schema import Involves, Event

irene_events = g.edges_to("wiki:Irene_Adler", pred_type=Involves)
for e in irene_events:
    ev = g.get(e.subject.id)
    if isinstance(ev, Event):
        print(ev.description)
```

### Transitive location — Baker Street is in England

```python
from ner_20260608.holmes_schema import LocatedIn

reachable = g.transitive_closure("place:Baker_Street_221B", LocatedIn)
print(reachable)   # {'place:London', 'place:England', ...}
```

### Filter by truth_status — find disputed or hypothetical facts

```python
from ner_20260608.holmes_schema import TruthStatus

for eid, inst in g.by_id.items():
    ts = getattr(inst, "truth_status", None)
    if ts in (TruthStatus.DISPUTED, TruthStatus.HYPOTHETICAL):
        print(g.describe(eid))
```

### Epistemic query — what did Watson know, and when?

`KnewAt` is a higher-order predicate: its `object_` is itself a `BaseStatement`.

```python
from ner_20260608.holmes_schema import KnewAt

knew_edges = [
    inst for inst in g.by_id.values()
    if isinstance(inst, KnewAt)
    and inst.subject.id == "wiki:John_Watson"
    and inst.truth_status == TruthStatus.ASSERTED_TRUE
]

for k in knew_edges:
    stmt = g.describe(k.object_.id)
    when = k.moment.label if k.moment else "unknown moment"
    print(f"Watson knew [{stmt}] at [{when}]")
```

### Disguise chains — who is secretly whom?

```python
from ner_20260608.holmes_schema import DisguisedAs, HasTrueIdentity

for inst in g.by_id.values():
    if isinstance(inst, DisguisedAs):
        persona = g.describe(inst.object_.id)
        person = g.describe(inst.subject.id)
        print(f"{person}  disguised as  {persona}  [{inst.truth_status.value}]")
```

### Subgraph export — serialize neighbors to JSON

```python
import json
from ner_20260608.holmes_schema import BaseStatement

def subgraph_json(g, seed_ids, max_hops=2):
    layers = g.bfs(seed_ids, max_hops=max_hops)
    all_ids = set().union(*layers)
    nodes, edges = [], []
    for eid in all_ids:
        inst = g.get(eid)
        if inst is None:
            continue
        if isinstance(inst, BaseStatement):
            edges.append({
                "id": inst.id,
                "type": type(inst).__name__,
                "subject": inst.subject.id,
                "object": inst.object_.id,
                "truth_status": inst.truth_status.value,
            })
        else:
            nodes.append({
                "id": inst.id,
                "type": type(inst).__name__,
                "label": getattr(inst, "display_name", inst.id),
            })
    return json.dumps({"nodes": nodes, "edges": edges}, indent=2)

print(subgraph_json(g, ["wiki:Irene_Adler"]))
```

### Inspect the raw bundled data

```python
import json
from ner_20260608 import data_path

path = data_path("bohemia_triplets.jsonl")
with path.open() as fh:
    records = [json.loads(line) for line in fh]

print(f"{len(records)} triplets")
pred_counts = {}
for r in records:
    p = r.get("predicate", "?")
    pred_counts[p] = pred_counts.get(p, 0) + 1
for pred, n in sorted(pred_counts.items(), key=lambda x: -x[1]):
    print(f"  {n:4d}  {pred}")
```

---

## Writing pytest tests

### Smoke tests against the bundled graph

```python
# tests/test_smoke.py
import pytest
from ner_20260608 import load_bohemia_graph
from ner_20260608.holmes_schema import Person, Knows, TruthStatus


@pytest.fixture(scope="session")
def g():
    return load_bohemia_graph(warn=False)


def test_graph_non_empty(g):
    assert len(g.by_id) > 50


def test_holmes_exists(g):
    assert g.get("wiki:Sherlock_Holmes") is not None


def test_watson_knows_holmes(g):
    edges = g.edges_from("wiki:John_Watson", pred_type=Knows, truth="asserted_true")
    targets = {e.object_.id for e in edges}
    assert "wiki:Sherlock_Holmes" in targets


def test_bfs_reaches_irene(g):
    layers = g.bfs(["wiki:Sherlock_Holmes"], max_hops=3)
    all_ids = set().union(*layers)
    assert "wiki:Irene_Adler" in all_ids
```

### Unit tests with synthetic fixture graphs

Synthetic graphs let you test graph logic without depending on LLM-extracted data,
so they never fail because an extraction changed.

```python
# tests/conftest.py  (or inline in a test file)
import pytest
from ner_20260608.graph import Graph
from ner_20260608.holmes_schema import (
    Person, Location, Knows, AssociatedWith, TruthStatus,
)

_PROV = dict(
    story_id="test",
    paragraph_index=0,
    extraction_method="manual",
    extraction_confidence=1.0,
)


@pytest.fixture(scope="module")
def trio():
    """Holmes knows Watson (true) and Irene (false)."""
    holmes = Person(id="wiki:Sherlock_Holmes", display_name="Sherlock Holmes")
    watson = Person(id="wiki:John_Watson", display_name="John Watson")
    irene  = Person(id="wiki:Irene_Adler",  display_name="Irene Adler")
    k_hw = Knows(
        id="stmt:hw", subject=holmes, object_=watson,
        truth_status=TruthStatus.ASSERTED_TRUE, **_PROV,
    )
    k_hi = Knows(
        id="stmt:hi", subject=holmes, object_=irene,
        truth_status=TruthStatus.ASSERTED_FALSE, **_PROV,
    )
    return Graph([holmes, watson, irene, k_hw, k_hi])


def test_truth_filter_keeps_only_true(trio):
    edges = trio.edges_from("wiki:Sherlock_Holmes", truth="asserted_true")
    assert len(edges) == 1
    assert edges[0].object_.id == "wiki:John_Watson"


def test_bfs_skips_false_edges_by_default(trio):
    layers = trio.bfs(["wiki:Sherlock_Holmes"], max_hops=1)
    hop1 = layers[1]
    assert "wiki:John_Watson" in hop1
    assert "wiki:Irene_Adler" not in hop1
```

### Parameterized truth_status tests

```python
import pytest
from ner_20260608.holmes_schema import TruthStatus

@pytest.mark.parametrize("ts,expected_count", [
    ("asserted_true",  1),
    ("asserted_false", 1),
    ("hypothetical",   0),
])
def test_truth_filter_parametrized(trio, ts, expected_count):
    edges = trio.edges_from("wiki:Sherlock_Holmes", truth=ts)
    assert len(edges) == expected_count
```

### Testing with `InstanceSet` directly

When you want to check loading logic (e.g. warn on bad records) without building
a full Graph:

```python
from ner_20260608.loader import load_instances
from ner_20260608 import data_path


def test_no_unexpected_warnings():
    iset = load_instances(
        entities=data_path("bohemia_entities.jsonl"),
        events=data_path("bohemia_events.jsonl"),
        moments=data_path("bohemia_moments.jsonl"),
        triplets=data_path("bohemia_triplets.jsonl"),
        warn=False,
    )
    # Some warnings are expected (unknown predicates in early extractions);
    # assert the count stays below a threshold rather than requiring zero.
    assert len(iset.warnings) < 20, iset.warnings
```

---

## MCP wrapper

Expose the graph as an MCP server so Claude (or any MCP client) can query it
via tool calls. Install the MCP SDK first:

```bash
pip install mcp
```

```python
# bohemia_mcp.py
from mcp.server.fastmcp import FastMCP
from ner_20260608 import load_bohemia_graph
from ner_20260608.holmes_schema import BaseStatement

mcp = FastMCP("bohemia-graph")
_g = None   # lazy singleton


def _graph():
    global _g
    if _g is None:
        _g = load_bohemia_graph(warn=False)
    return _g


@mcp.tool()
def describe_entity(entity_id: str) -> str:
    """Return a one-line description of any entity or statement by ID."""
    return _graph().describe(entity_id)


@mcp.tool()
def edges_from(entity_id: str, truth: str = "asserted_true") -> list[dict]:
    """Return all outward edges from entity_id with the given truth_status."""
    edges = _graph().edges_from(entity_id, truth=truth)
    return [
        {
            "id": e.id,
            "predicate": type(e).__name__,
            "object": e.object_.id,
            "truth_status": e.truth_status.value,
        }
        for e in edges
    ]


@mcp.tool()
def edges_to(entity_id: str, truth: str = "asserted_true") -> list[dict]:
    """Return all inward edges to entity_id with the given truth_status."""
    edges = _graph().edges_to(entity_id, truth=truth)
    return [
        {
            "id": e.id,
            "predicate": type(e).__name__,
            "subject": e.subject.id,
            "truth_status": e.truth_status.value,
        }
        for e in edges
    ]


@mcp.tool()
def bfs(seed_ids: list[str], max_hops: int = 2) -> list[list[str]]:
    """BFS from seed_ids. Returns one list of IDs per hop layer."""
    layers = _graph().bfs(seed_ids, max_hops=max_hops)
    return [sorted(layer) for layer in layers]


@mcp.tool()
def find_by_type(type_name: str) -> list[dict]:
    """Return all instances whose Python class name matches type_name.

    Valid type names: Person, Persona, Location, Object, Event, Moment,
    Knows, Involves, Possesses, AssociatedWith, LocatedIn, OccurredAt,
    KnewAt, DisguisedAs, HasTrueIdentity, Contradicts, Executes.
    """
    g = _graph()
    results = []
    for eid, inst in g.by_id.items():
        if type(inst).__name__ == type_name:
            results.append({
                "id": eid,
                "label": getattr(inst, "display_name", None)
                         or getattr(inst, "description", None)
                         or eid,
            })
    return results


if __name__ == "__main__":
    mcp.run()
```

Register it in your Claude Code MCP config (`.claude/mcp_config.json`):

```json
{
  "mcpServers": {
    "bohemia": {
      "command": "python",
      "args": ["bohemia_mcp.py"]
    }
  }
}
```

Then Claude can call `bfs(["wiki:Irene_Adler"], max_hops=2)` or
`find_by_type("DisguisedAs")` directly in conversation.

---

## Building a Graph from your own JSONL

The loader and graph classes are not tied to the Bohemia dataset. Pass any
compatible JSONL paths to `load_graph`:

```python
from pathlib import Path
from ner_20260608.loader import load_graph

g = load_graph(
    entities=Path("my_entities.jsonl"),
    events=Path("my_events.jsonl"),
    moments=Path("my_moments.jsonl"),
    triplets=Path("my_triplets.jsonl"),
)
```

Or build a Graph directly from Python objects (useful for tests or demos):

```python
from ner_20260608.graph import Graph
from ner_20260608.holmes_schema import Person, Knows, TruthStatus

a = Person(id="p:alice", display_name="Alice")
b = Person(id="p:bob",   display_name="Bob")
knows = Knows(
    id="stmt:ab", subject=a, object_=b,
    truth_status=TruthStatus.ASSERTED_TRUE,
    story_id="demo", paragraph_index=0,
    extraction_method="manual", extraction_confidence=1.0,
)
g = Graph([a, b, knows])
```

---

## Adding a predicate to the schema

Add the class to `holmes_schema.py` and call `model_rebuild()` at the bottom:

```python
class Employs(BaseStatement, ProvenanceMixin):
    """Person employs another Person (e.g. Holmes employs the Baker Street Irregulars)."""
    subject: Person
    object_: Person
```

Then include it in the `model_rebuild()` loop at the bottom of the file.
The loader will pick it up automatically via the `_PREDICATE_CLASSES` dict in
`loader.py` (it scans the schema module at import time).

---

## Minimal CLI

Add this to `src/ner_20260608/__main__.py` so the package can be invoked as
`python -m ner_20260608 describe wiki:Irene_Adler`.

> **Note:** If `python` is not on your PATH (e.g. in a PDM-managed environment),
> use `pdm run python -m ner_20260608 ...` instead.

```python
import sys
from ner_20260608 import load_bohemia_graph

def main():
    g = load_bohemia_graph(warn=False)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"

    if cmd == "describe" and len(sys.argv) > 2:
        print(g.describe(sys.argv[2]))

    elif cmd == "edges-from" and len(sys.argv) > 2:
        edges = g.edges_from(sys.argv[2], truth="asserted_true")
        g.print_edges(edges)

    elif cmd == "bfs" and len(sys.argv) > 2:
        layers = g.bfs(sys.argv[2:], max_hops=2)
        for i, layer in enumerate(layers):
            for eid in sorted(layer):
                print(f"hop{i}  {g.describe(eid)}")

    else:
        print("usage: python -m ner_20260608 describe|edges-from|bfs <entity_id>...")

if __name__ == "__main__":
    main()
```

Example usage:

```bash
# Single-line label for an entity
python -m ner_20260608 describe wiki:Irene_Adler
# => Person(Irene Adler)

# All asserted-true edges out of a node
python -m ner_20260608 edges-from wiki:Irene_Adler

# 2-hop neighbourhood (multiple seed IDs accepted)
python -m ner_20260608 bfs wiki:Irene_Adler
python -m ner_20260608 bfs wiki:Sherlock_Holmes wiki:Irene_Adler
```
