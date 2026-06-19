# NER-20270608

## Packaged wheel: queryable Bohemia

This repository now also has a packaging branch, `packaged-bohemia-wheel`, that
turns the Bohemia graph code and artifacts into an installable Python package.

On that branch, the intended import is:

```python
from ner_20260608 import load_bohemia_graph

g = load_bohemia_graph()
```

Build notes and usage notes for the wheel live in:

- `docs/packaging-bohemia-wheel.md`

The packaged runtime code lives under:

- `src/ner_20260608/__init__.py`
- `src/ner_20260608/graph.py`
- `src/ner_20260608/loader.py`
- `src/ner_20260608/holmes_schema.py`

and the packaged data files live under:

- `src/ner_20260608/data/`

## Makefile workflow

This repo now includes a top-level `Makefile` for running the pipeline stages in sequence.

### Main targets

```bash
make sentences
make coref
make merge
make events
make triplets
make all
```

`make all` is equivalent to `make triplets` and runs the pipeline through the final triplet extraction stage.

### Fresh rebuild targets

Some stages use progress dotfiles to support resume behavior. If you want to force a clean rerun of a resumable stage, use:

```bash
make fresh-events
make fresh-triplets
```

### Cleanup targets

```bash
make clean-events
make clean-triplets
make clean-derived
make clean
```

`make clean` is an alias for `make clean-derived`.

### Common overrides

The `Makefile` exposes common knobs as variables you can override on the command line:

```bash
make sentences SENT_GUTENBERG=1
make coref COREF_CHUNK_SIZE=25 COREF_OVERLAP=5
make triplets TRIPLETS_CHUNK_SIZE=12 TRIPLETS_EVENT_WINDOW=10
make all OLLAMA=http://host:11434
```

Available variables include:
- `OLLAMA`
- `EVENTS_OLLAMA`
- `TRIPLETS_OLLAMA`
- `SENT_MODEL`
- `COREF_MODEL`
- `TRIPLETS_MODEL`
- `SENT_GUTENBERG`
- `COREF_CHUNK_SIZE`
- `COREF_OVERLAP`
- `TRIPLETS_CHUNK_SIZE`
- `TRIPLETS_OVERLAP`
- `TRIPLETS_EVENT_WINDOW`

## Pipeline Overview

This repository's pipeline converts a plain-text story into a queryable, typed
knowledge graph in five sequential stages. Each stage writes JSONL output that
becomes input to the next stage, so the whole process is transparent and
inspectable end-to-end.

The implementation intentionally uses two LLM tiers:

- **Local Ollama (`qwen2.5:14b`)** for high-volume, lower-reasoning extraction
  passes where cost and throughput matter most.
- **Claude (frontier API)** for stages that need deeper narrative reasoning,
  disambiguation, and temporal interpretation.

The final artifact is a set of typed graph edges (triplets) grounded in
sentences and linked to a global entity/event index, suitable for in-memory BFS
and other graph queries.

## Script-by-script details

### `src/sentencize.py`

**What it does:** Splits a raw text file (optionally stripping Project
Gutenberg header/footer) into numbered sentences and writes JSONL records with
`{id, para, text}`.

**Why it exists:** Downstream stages need stable sentence IDs for precise
grounding. Paragraph-level text is too coarse for mention spans and evidence
links.

**Design decisions:** Uses an LLM rather than rule-based sentence splitting
because literary punctuation, dialogue, and clause structure are error-prone
for naive splitters. It processes one paragraph per call to contain failure
scope, uses flat sequential IDs for simpler joins, and supports resumable runs
by reading existing output.
