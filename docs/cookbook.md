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
print(holmes)                     # Sherlock Holmes   (str uses display_name)
print(repr(holmes))               # Person('wiki:Sherlock_Holmes')  (repr shows id)

# describe() delegates to str()
print(g.describe("wiki:Irene_Adler"))   # Irene Adler

# Who does Watson know (asserted true)?
edges = g.edges_from("wiki:John_Watson", truth="asserted_true")
g.print_edges(edges)
# → Knows(Dr. Watson → Sherlock Holmes)  [asserted_true]
```

---

## Evidence assembly just before the revelation

This example builds a temporally-bounded subgraph — everything up to but not
including the moment Holmes reveals the photograph's location — and shows what
evidence is available to support the conclusion.

> **What this example does and does not do.** The code below assembles the
> evidence base: it shows which facts in the pre-cutoff graph bear on the
> question of who has the photograph. It does *not* mechanically derive
> `Possesses(Irene, photograph)` as a new statement — that would require a
> rule (in the `Rule(φ ⇒ ψ)` sense from the formal spec) and an inference
> engine to fire it. Neither exists yet. Step 5 verifies the conclusion using
> the full graph, which is a spoiler check, not a proof. The gap between
> "evidence assembled" and "conclusion derived" is where rule-based inference
> would live.

**The scene:** Holmes and Watson have just walked away from Briony Lodge after
the staged fire alarm. Watson asks: *"You have the photograph?"* Holmes replies:
*"I know where it is."* That exchange is sentence 485–486. We stop the graph
one sentence before it.

### Step 1 — build the pre-revelation subgraph

```python
from ner_20260608 import load_bohemia_graph
from ner_20260608.holmes_schema import Possesses, Involves

CUTOFF = 485  # sentence 485: "You have the photograph?"

pre = load_bohemia_graph(sentence_cutoff=CUTOFF, warn=False)
# sentence_cutoff is exclusive: triplets are included only when
# max(sentence_ids) < CUTOFF. Sentence 485 itself is NOT in `pre`.
```

### Step 2 — what does the subgraph say Irene Adler possesses?

```python
irene_possesses = pre.edges_from(
    "wiki:Irene_Adler", pred_type=Possesses, truth="asserted_true"
)
print([e.object_.display_name for e in irene_possesses])
# → ["Irene Adler's purse", "Irene Adler's watch"]
```

The photograph is absent. The subgraph contains no `Possesses` statement linking
Irene to the photograph — that statement only appears at sentence 511, after Holmes
observes her reach for it during the smoke-rocket alarm.

### Step 3 — trace the photograph evidence chain

Even without the possession statement, the subgraph holds three events connecting
Irene to the photograph:

```python
photo_events = [
    e.subject
    for e in pre.edges_to(
        "wiki:Irene_Adler", pred_type=Involves, truth="asserted_true"
    )
    if "photograph" in e.subject.description.lower()
]
for ev in photo_events:
    print(repr(ev))        # Event('sib:event:joint_photograph_revealed')
    print(" ", ev)         # The King reveals that he and Irene Adler were both...
    print(" ", ev.description)
```

Output:

```
Event('sib:event:joint_photograph_revealed')
  The King reveals that he and Irene Adler were both in the compromising
  photograph together, alarming Holmes.
Event('sib:event:irene_threatens_to_send_photograph')
  Irene Adler threatens to send the compromising photograph to the King's
  betrothed on the day the betrothal is publicly proclaimed.
Event('sib:event:holmes_discusses_photograph_location')
  Holmes reasoned aloud to Watson about where Irene Adler would conceal the
  photograph, concluding she would not carry it on her person.
```

`repr(ev)` shows the canonical id; `str(ev)` (or just `ev.description`) gives the
human-readable description. Neither is parsed for type information — type is
determined by `isinstance(ev, Event)`.

> **Note on existing corpus ids:** The Bohemia pipeline produced ids in the form
> `sib:event:X` — the `event:` segment predates the R9 guideline that synthetic
> entity ids should omit the type segment. The existing data is acceptable because
> nothing in the system parses that segment for type dispatch. New corpora should
> follow the guideline: `sib:kings_visit`, not `sib:event:kings_visit`.

These three events establish:
1. The photograph exists and Irene has it (King's own testimony).
2. Irene intends to use it as leverage — she will not destroy it.
3. Holmes has already reasoned that she keeps it hidden at home, not on her person.

### Step 4 — the plan execution events

The subgraph also contains the full Briony Lodge fire-alarm sequence:

```python
plan_event_ids = {
    "sib:event:holmes_explains_plan_to_watson",
    "sib:event:holmes_watson_pace_briony_lodge",
    "sib:event:holmes_feigns_injury",
    "sib:event:irene_tends_to_injured_holmes",
    "sib:event:holmes_signals_need_for_air",
    "sib:event:watson_tosses_smoke_rocket",
    "sib:event:holmes_declares_false_alarm",
    "sib:event:watson_rejoins_holmes",
}

executed = [
    e.subject
    for e in pre.edges_to(
        "wiki:Sherlock_Holmes", pred_type=Involves, truth="asserted_true"
    )
    if e.subject.id in plan_event_ids
]
print(f"{len(executed)} plan events confirmed in subgraph")
# → 7 plan events confirmed in subgraph
```

Seven of the eight plan-execution events are reachable via Holmes's Involves
edges (the eighth, `watson_tosses_smoke_rocket`, involves Watson only). The
plan was designed specifically to make Irene reveal the photograph's hiding
place by instinct (Holmes explains this to Watson in
`holmes_explains_plan_to_watson`). The execution is complete. A rule-based
inference engine with the right `Rule(φ ⇒ ψ)` declaration could derive
`Possesses(Irene, photograph)` from this evidence — but none exists yet.

### Step 5 — confirm with the full graph (spoiler check, not a proof)

```python
full = load_bohemia_graph(warn=False)

full_possesses = full.edges_from(
    "wiki:Irene_Adler", pred_type=Possesses, truth="asserted_true"
)
print([e.object_.display_name for e in full_possesses])
# → ["Irene Adler's purse", "Irene Adler's watch", "Irene Adler's photograph",
#    "male costume"]
```

`Possesses: Irene_Adler → irene_adlers_photograph` appears in the full graph
at sentence 511, extracted after Holmes observes her reaction to the smoke
rocket. The evidence assembled from the pre-cutoff subgraph is sufficient to
support that conclusion — but assembling evidence and deriving a new statement
are different operations. The latter requires rule declarations and an inference
engine that this project does not yet have.

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

# No truth= arg: includes all truth statuses (asserted_true, hypothetical, disputed…).
# bfs() and transitive_closure() default to asserted_true only — see their signatures.
irene_events = g.edges_to("wiki:Irene_Adler", pred_type=Involves)
for e in irene_events:
    ev = g.get(e.subject.id)
    if isinstance(ev, Event):
        print(ev.description)
```

### Transitive location — 221B Baker Street is in London

The LLM-extracted JSONL graph has sparse `LocatedIn` coverage. The manual instance
graph in `scandal_instances.py` has the full geographic chain. Use
`Graph.from_module` to query it:

> **Note:** `scandal_instances.py` lives in the source repo under `src/` and is
> not shipped in the wheel. On a clean install, `import scandal_instances` will
> fail — clone the repo and add `src/` to `sys.path`, or run examples from the
> repo root with `pdm run python`.

```python
import sys, importlib
import scandal_instances as si   # repo only — not in wheel; see note above
from ner_20260608.graph import Graph
from ner_20260608.holmes_schema import LocatedIn

g_manual = Graph.from_module(si)
reachable = g_manual.transitive_closure("wiki:221B_Baker_Street", LocatedIn)
print(reachable)   # {'wiki:London'}
# (Briony Lodge in St. John's Wood, which is in London, is reachable via
#  a two-hop chain — transitive_closure follows it automatically.)
```

Location ids follow the same authority as Person ids: wiki-anchored entities use
the `wiki:` prefix; unanchored ones (extracted without a matching wiki article)
use the `place:` corpus prefix. `_canonicalize_id` normalises full Baker Street
Wiki URLs to `wiki:X` automatically, so both forms work in `g.get()` and traversal.

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
        print(f"{inst.subject}  disguised as  {inst.object_}  [{inst.truth_status.value}]")
        # → Sherlock Holmes  disguised as  Nonconformist Clergyman  [asserted_true]
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
                "label": str(inst),
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
            results.append({"id": eid, "label": str(inst)})
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

Add the class to `holmes_schema.py`:

```python
class Employs(BaseStatement, ProvenanceMixin):
    """Person employs another Person (e.g. Holmes employs the Baker Street Irregulars)."""
    subject: Person
    object_: Person
```

Two separate mechanisms must both see the new class:

1. **`model_rebuild()` loop** (bottom of `holmes_schema.py`) — Pydantic requires
   this to resolve forward references between classes. Omitting it causes
   `ValidationError` at construction time, not import time.

2. **`_PREDICATE_CLASSES` scan** (`loader.py`) — built automatically at import time
   by scanning `holmes_schema` for `BaseStatement` subclasses. No manual step
   needed; just make sure the class is in `holmes_schema.py` before the loader
   imports it.

So the only manual step is adding `Employs` to the `model_rebuild()` list.

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
# => Irene Adler

# All asserted-true edges out of a node
python -m ner_20260608 edges-from wiki:Irene_Adler

# 2-hop neighbourhood (multiple seed IDs accepted)
python -m ner_20260608 bfs wiki:Irene_Adler
python -m ner_20260608 bfs wiki:Sherlock_Holmes wiki:Irene_Adler
```
