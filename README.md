# NER-20270608

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

### `src/coref.py`

**What it does:** Reads sentencized JSONL, processes overlapping chunks, and
asks the LLM to output entity clusters and mentions per chunk.

**Why it exists:** Coreference is required before canonical entities can be
built; references like "the woman", "she", and "Irene Adler" must be grouped
before graph construction.

**Design decisions:** Uses overlapping chunk windows to stay within context
limits while preserving boundary continuity. Provides prior sentences as
read-only context to improve resolution without double counting. Runs on local
Ollama for throughput. Parsing uses `JSONDecoder.raw_decode` scanning to
recover valid JSON objects even when output includes extra text.

### `src/merge.py`

**What it does:** Performs a three-pass merge over chunk-level coref output:
(1) canonical label clustering, (2) Baker Street Wiki candidate linking, and
(3) flattened mention rewriting into canonical IDs.

**Why it exists:** Chunk-level extraction creates duplicate and variant labels
for the same entity; a global reconciliation pass is required to build a stable
entity table.

**Design decisions:** Uses Claude for high-quality label clustering and
disambiguation in Holmes-specific naming patterns. Uses Baker Street Wiki as an
external authority for stable IDs when possible. Falls back to
`provisional:<n>` when no confident wiki link exists. Wiki matching is judged
with an explicit "none" path to reduce false positives.

### `src/events.py`

**What it does:** Reads sentences plus canonical entities, then extracts
discrete events and temporal anchors (moments), writing `bohemia_events.jsonl`
and `bohemia_moments.jsonl`.

**Why it exists:** Events and time anchors provide narrative structure for
timeline queries and later reasoning. Without this layer, the graph is mostly
static entity relations.

**Design decisions:** Uses Claude because event boundaries, indirect narration,
and temporal anchoring are reasoning-heavy. Keeps chunking with overlap for
context continuity. Grounds event participants to known entity IDs, and defers
higher-order predicates (such as KnewAt/Contradicts/Plans) to future passes.

### `src/triplets.py`

**What it does:** Reads sentences, entities, events, and moments, then emits
typed predicate instances (graph edges) as JSONL.

**Why it exists:** This is the graph-materialization stage: it converts the
intermediate event/entity index into queryable typed relations with provenance.

**Design decisions:** Uses local Ollama here for cost-efficient slot-filling
over a closed predicate catalog. Injects short alias IDs into prompts and
expands them back to canonical IDs in validation to prevent invented ID
formats. Filters events/moments to a sentence window around each chunk to keep
prompts bounded. Uses content-addressed IDs to simplify deduplication.

### `src/holmes_schema.py`

**What it does:** Defines the typed graph schema as Pydantic models: entity
types, predicate types, and truth-status semantics.

**Why it exists:** Enforces a single contract across all pipeline stages so
outputs remain type-safe and composable.

**Design decisions:** Uses a unified statement model where predicate instances
are first-class entities, enabling higher-order predicates over statements.
External authority IDs (Baker Street Wiki URIs) are used where available;
corpus-local concepts use synthetic `sib:` IDs.

### `src/graph.py`

**What it does:** Builds an in-memory typed graph index over schema instances
with traversal helpers (`edges_from`, `edges_to`, BFS, transitive closure).

**Why it exists:** Provides direct local querying and neighborhood exploration
without requiring a database or MCP layer.

**Design decisions:** Uses duck typing to stay decoupled from schema module
imports while still indexing entities/statements correctly. Defaults BFS to
`asserted_true` edges with configurable truth filtering. Indexes statements as
nodes as well as edges so higher-order traversal remains possible.
