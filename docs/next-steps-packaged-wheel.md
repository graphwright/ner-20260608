# Next steps for the packaged Bohemia wheel

This branch successfully builds an installable wheel for the Bohemia graph package:

- package import name: `ner_20260608`
- built artifacts include:
  - `dist/ner_20260608-0.1.0-py3-none-any.whl`
  - `dist/ner_20260608-0.1.0.tar.gz`

The package install smoke test passed far enough to prove:

- the wheel installs
- packaged data files are present in `site-packages`
- `load_bohemia_graph()` runs
- the graph hydrates successfully

However, there is one remaining usability issue and a short cleanup checklist.

---

## 1. Remaining issue: ID aliasing

The packaged graph stores canonical entity IDs in short form like:

- `wiki:Sherlock_Holmes`
- `wiki:John_Watson`

But users may naturally try full Baker Street URLs like:

- `https://bakerstreet.fandom.com/wiki/Sherlock_Holmes`

Current behavior:

```python
g.describe("https://bakerstreet.fandom.com/wiki/Sherlock_Holmes")
# -> <not found: https://bakerstreet.fandom.com/wiki/Sherlock_Holmes>
```

while:

```python
g.describe("wiki:Sherlock_Holmes")
```

works.

### Recommended fix

Update `src/ner_20260608/graph.py` so graph lookups normalize full wiki URLs to the canonical `wiki:` form.

Suggested approach:

- add a helper like `_canonicalize_id(entity_id: str) -> str`
- convert
  - `https://bakerstreet.fandom.com/wiki/Sherlock_Holmes`
  - to `wiki:Sherlock_Holmes`
- use that normalization in:
  - `describe()`
  - `edges_from()`
  - `edges_to()`
  - any new `get()` helper

### Suggested implementation sketch

```python
def _canonicalize_id(entity_id: str) -> str:
    prefix = "https://bakerstreet.fandom.com/wiki/"
    if entity_id.startswith(prefix):
        return "wiki:" + entity_id[len(prefix):]
    return entity_id
```

Then add:

```python
def get(self, entity_id: str):
    return self.by_id.get(_canonicalize_id(entity_id))
```

And change internal lookups to use `self.get(...)` or normalized IDs.

---

## 2. Verify packaged data files are full copies

Earlier in this branch, sample/truncated packaged JSONL files were added for:

- `src/ner_20260608/data/bohemia_events.jsonl`
- `src/ner_20260608/data/bohemia_triplets.jsonl`

These should be replaced with the full source files from repo root:

- `bohemia_events.jsonl`
- `bohemia_triplets.jsonl`

If not already done, run:

```bash
cp bohemia_events.jsonl src/ner_20260608/data/bohemia_events.jsonl
cp bohemia_triplets.jsonl src/ner_20260608/data/bohemia_triplets.jsonl
git add src/ner_20260608/data/bohemia_events.jsonl src/ner_20260608/data/bohemia_triplets.jsonl
git commit -m "Replace packaged sample JSONL files with full dataset copies"
git push
```

---

## 3. Rebuild and retest the wheel

After the aliasing fix and full data copy, rebuild:

```bash
pdm run python -m build
```

Then create a clean test environment and reinstall:

```bash
python3 -m venv /tmp/ner-wheel-test
source /tmp/ner-wheel-test/bin/activate
python -m pip install dist/ner_20260608-0.1.0-py3-none-any.whl
```

---

## 4. Final smoke test

Run:

```bash
python - <<'PY'
from ner_20260608 import load_bohemia_graph, data_path

print(data_path("bohemia_entities.jsonl"))
print(data_path("bohemia_events.jsonl"))
print(data_path("bohemia_moments.jsonl"))
print(data_path("bohemia_triplets.jsonl"))

g = load_bohemia_graph(warn=False)

print("instance count:", len(g.by_id))
print(g.describe("wiki:Sherlock_Holmes"))
print(g.describe("https://bakerstreet.fandom.com/wiki/Sherlock_Holmes"))
PY
```

Expected success criteria:

- all four packaged data paths print under `site-packages/ner_20260608/data/`
- graph loads without error
- instance count is nonzero
- both Sherlock Holmes lookup forms succeed

---

## 5. Optional cleanup

These are optional, not blockers.

### a. Keep compatibility shims for now
These files currently provide backward compatibility:

- `src/graph.py`
- `src/loader.py`
- `src/holmes_schema.py`

They can stay for now.

### b. Tighten README examples
Prefer examples that use the packaged convenience API:

```python
from ner_20260608 import load_bohemia_graph
g = load_bohemia_graph()
```

### c. Add a tiny test
A lightweight regression test would be valuable, for example:

- import package
- load graph
- assert `wiki:Sherlock_Holmes` exists
- assert full URL lookup also works after alias normalization

---

## 6. Definition of done

This packaging effort is done when all of the following are true:

- full dataset files are bundled in `src/ner_20260608/data/`
- wheel builds successfully
- wheel installs in a clean environment
- `load_bohemia_graph()` works from installed wheel
- both `wiki:` and full Baker Street URL forms resolve for key entities

---

## 7. Useful commands summary

### Build
```bash
pdm run python -m build
```

### Copy full packaged data
```bash
cp bohemia_events.jsonl src/ner_20260608/data/bohemia_events.jsonl
cp bohemia_triplets.jsonl src/ner_20260608/data/bohemia_triplets.jsonl
```

### Clean install test
```bash
python3 -m venv /tmp/ner-wheel-test
source /tmp/ner-wheel-test/bin/activate
python -m pip install dist/ner_20260608-0.1.0-py3-none-any.whl
```

### Smoke test
```bash
python - <<'PY'
from ner_20260608 import load_bohemia_graph
g = load_bohemia_graph(warn=False)
print(len(g.by_id))
print(g.describe("wiki:Sherlock_Holmes"))
print(g.describe("https://bakerstreet.fandom.com/wiki/Sherlock_Holmes"))
PY
```
