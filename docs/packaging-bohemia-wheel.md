# Packaging the Bohemia graph as a wheel

This branch packages the existing Bohemia dataset and graph-query code as an
installable Python distribution.

## What changed

The reusable runtime code now lives under the real import package:

- `src/ner_20260608/__init__.py`
- `src/ner_20260608/graph.py`
- `src/ner_20260608/loader.py`

The package also bundles the graph artifacts as package data under:

- `src/ner_20260608/data/bohemia_entities.jsonl`
- `src/ner_20260608/data/bohemia_events.jsonl`
- `src/ner_20260608/data/bohemia_moments.jsonl`
- `src/ner_20260608/data/bohemia_triplets.jsonl`

A small convenience API is exposed from `ner_20260608`:

- `load_bohemia_graph()`
- `load_bohemia_instances()`
- `data_path(filename)`

## Why this layout

This is the standard `src/` packaging layout. The important shift is that the
installable import package is now self-contained:

- code lives inside `src/ner_20260608/`
- imports inside the package are package-relative
- data files are accessed with `importlib.resources`

That makes the package work after installation from a wheel, not just from a
source checkout.

## Build workflow

From the repository root:

```bash
python -m pip install --upgrade build
python -m build
```

This should produce artifacts in `dist/`, typically:

- `ner_20260608-0.1.0-py3-none-any.whl`
- `ner_20260608-0.1.0.tar.gz`

## Install the wheel

```bash
pip install dist/ner_20260608-0.1.0-py3-none-any.whl
```

## Basic usage

```python
from ner_20260608 import load_bohemia_graph

g = load_bohemia_graph()
print(g.describe("wiki:Sherlock_Holmes"))
print(g.bfs(["wiki:Sherlock_Holmes"], max_hops=2))
```

## Data access

If you need direct access to the bundled JSONL files:

```python
from ner_20260608 import data_path

print(data_path("bohemia_triplets.jsonl"))
```

## Compatibility note

The old top-level modules `src/graph.py` and `src/loader.py` remain as small
forwarding shims so existing imports keep working during the transition.

## Next possible improvements

- move `holmes_schema.py` fully into the package and convert all remaining code
  to package-relative imports
- add a small CLI (`python -m ner_20260608`)
- split the generic graph library from the Bohemia dataset package if you want
  multiple story datasets later
