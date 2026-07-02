# How This Repository Does NER: A Step-by-Step Explainer

This document walks through the Named Entity Recognition (NER) pipeline in this
repository, explains the design rationale for each step, discusses which steps can
run entirely on local hardware and which require frontier cloud models, and
describes how the pipeline would need to change for a substantially different
domain such as medical literature.

---

## The Pipeline at a Glance

```
bohemia.txt
    │
    ▼  Step 1 — Sentencize
bohemia_sentences.jsonl
    │
    ▼  Step 2 — Coreference Resolution
bohemia_coref.jsonl
    │
    ▼  Step 3 — Entity Merge & Wiki Linking
bohemia_entities.jsonl + bohemia_mentions.jsonl
    │
    ▼  Step 4 — Event & Moment Extraction
bohemia_events.jsonl + bohemia_moments.jsonl
    │
    ▼  Step 5 — Triplet Extraction
bohemia_triplets.jsonl
    │
    ▼  Step 6 — Truth Promotion
bohemia_triplets.jsonl  (updated in place)
```

All intermediate files are newline-delimited JSON (JSONL) so every stage is
independently inspectable, resumable, and swappable.

---

## Step-by-Step Walkthrough

### Step 1 — Sentencize (`src/sentencize.py`)

**What happens:** The raw text (a Sherlock Holmes story from Project Gutenberg)
is split into numbered sentences. Each output record carries a stable integer
sentence ID, a paragraph number, and the verbatim sentence text.

**Why an LLM instead of a rule-based splitter?** Literary prose is hostile to
naive regex or `nltk` splitters: dialogue punctuation, mid-sentence dashes,
sentence-final abbreviations, and run-on Victorian clauses all cause failures.
An LLM handles these gracefully because it understands meaning rather than
surface patterns.

**Output schema:**
```json
{"id": 42, "para": 7, "text": "He was, I take it, the most perfect reasoning machine..."}
```

---

### Step 2 — Coreference Resolution (`src/coref.py`)

**What happens:** The sentence list is processed in overlapping chunks. For
each chunk the model is asked to identify every entity mention (proper names,
titles, common-noun descriptions, and pronouns) and cluster them under a
canonical label. The overlap window provides context that prevents the model
from breaking entity threads at chunk boundaries.

**Output schema (one record per chunk):**
```json
{
  "chunk_id": "1-20",
  "sentences": [1, 2, ..., 20],
  "entities": [
    {
      "label": "Irene Adler",
      "type": "person",
      "mentions": [
        {"sentence_id": 3, "span": "the woman", "confidence": 1.0},
        {"sentence_id": 7, "span": "she",        "confidence": 0.9}
      ]
    }
  ]
}
```

---

### Step 3 — Entity Merge & Wiki Linking (`src/merge.py`)

**What happens (three sub-passes):**

1. **Label clustering** — All unique entity labels collected across every chunk
   are sent to Claude in a single call. Claude uses its knowledge of the Holmes
   canon to unify circumlocutions ("His Majesty", "the King",
   "Wilhelm Gottsreich") into one canonical entry.
2. **Wiki linking** — For each canonical entity, up to five candidates are
   fetched from the Baker Street Fandom wiki via its OpenSearch API. Claude
   judges which candidate (if any) is the correct article. This eliminates
   spurious matches from loose string similarity.
3. **Mention rewriting** — Every raw mention from the coreference output is
   resolved to its canonical entity ID and written to a flat mentions table.

**Output schemas:**

`bohemia_entities.jsonl` — one record per canonical entity with a stable ID and
optional wiki URL.

`bohemia_mentions.jsonl` — one flat record per mention with the canonical
`entity_id`, the sentence it appeared in, and the verbatim span.

---

### Step 4 — Event & Moment Extraction (`src/events.py`)

**What happens:** The sentence list and entity table are jointly processed by a
frontier model to identify discrete *events* (actions and state changes) and
*moments* (temporal anchors). The model distinguishes direct action from
reported speech ("Watson describes what Holmes told him"), resolves implicit
temporal references, and assigns participant entity IDs from the known entity
index.

**Output schemas:**

`bohemia_events.jsonl` — one record per discrete event:
```json
{
  "id": "sib:event:adler-disguise",
  "description": "Irene Adler disguises herself as a young man",
  "sentence_ids": [112, 113],
  "participants": ["sib:entity:irene-adler"],
  "extraction_confidence": 0.95
}
```

`bohemia_moments.jsonl` — one record per temporal anchor linked to an event.

---

### Step 5 — Triplet Extraction (`src/triplets.py`)

**What happens:** The full entity/event/moment index plus the sentence text are
processed in overlapping chunks. For each chunk, the model performs
*slot-filling*: it maps sentence content onto a fixed predicate vocabulary
(`AssociatedWith`, `Knows`, `LocatedIn`, `Possesses`, `DisguisedAs`,
`HasTrueIdentity`, `Involves`, `OccurredAt`). Short alias IDs are injected into
the prompt and expanded back to canonical IDs in the validator, preventing the
model from inventing its own ID scheme.

**Output schema (one record per predicate instance):**
```json
{
  "id":                    "stmt:sib:entity:holmes:Knows:sib:entity:watson",
  "predicate":             "Knows",
  "subject_id":            "sib:entity:holmes",
  "subject_type":          "Person",
  "object_id":             "sib:entity:watson",
  "object_type":           "Person",
  "truth_status":          "hypothetical",
  "extraction_confidence": 0.97,
  "sentence_ids":          [14]
}
```

---

### Step 6 — Truth Promotion (`src/promote.py`)

**What happens:** LLM extraction conservatively marks every triplet as
`hypothetical`. This deterministic, rule-based pass promotes triplets according
to their `extraction_confidence` score:

| Confidence | New `truth_status` |
|---|---|
| ≥ 0.9 | `asserted_true` |
| ≥ 0.7 | `disputed` |
| < 0.7 | `hypothetical` (unchanged) |

No LLM call is made; this step is pure Python logic.

---

## Local vs. Frontier Model: Where Each Step Lives

The pipeline intentionally maintains two tiers:

- **Local Ollama** (`qwen2.5:14b`, running on local hardware) — high-volume,
  lower-reasoning passes where cost and throughput matter most.
- **Claude (Anthropic API, `claude-sonnet-4-6`)** — passes that require deep
  narrative reasoning, world-knowledge disambiguation, or canonical judgment.

| Step | Default model | Can run fully locally? | Notes |
|---|---|---|---|
| 1 — Sentencize | `qwen2.5:14b` (Ollama) | **Yes** | A capable local model handles literary sentence splitting well. Claude is available as a `--anthropic` flag if accuracy must be maximised. |
| 2 — Coreference | `qwen2.5:14b` (Ollama) | **Yes** | Chunk-level coreference works well locally. Pronoun resolution accuracy is modestly better with a frontier model, but local quality is acceptable. |
| 3 — Entity Merge | `claude-sonnet-4-6` (API) | **Partial** | Pass 1 (label clustering) requires world-knowledge of Holmes canon — hard to do well locally. Pass 2 (wiki linking) is also frontier by default but could be skipped (`--skip-wiki`) to run fully locally with a weaker entity index. Pass 3 (mention rewriting) is pure Python. |
| 4 — Events & Moments | `claude-sonnet-4-6` (API) | **No** | Narrative reasoning, reported-speech disambiguation, and implicit temporal inference are the weakest points of 14B-class local models. Frontier quality is critical here. |
| 5 — Triplets | `qwen2.5:14b` (Ollama) | **Yes** | Slot-filling against a fixed predicate vocabulary is well-suited to local models. The alias-ID scheme keeps the prompt tractable and removes open-ended generation. |
| 6 — Promotion | (no LLM) | **Yes** | Pure rule-based logic. |

**Practical guidance:** A fully local run (steps 1, 2, 5, 6 on Ollama; skipping
the wiki-linking sub-pass in step 3; substituting a local model for events in
step 4) will produce a usable but noisier knowledge graph. The frontier-model
calls in steps 3 and 4 are where the most information is lost when downgrading
to a local model.

---

## Adapting the Pipeline for Medical Literature

Medical literature (PubMed abstracts, clinical notes, drug-label text) differs
from literary fiction in almost every dimension that matters for NER. Here is
how each stage would need to change.

The analysis below is grounded in a working implementation: the `medlit` module
in the `graphwright/kgraph` repository has been battle-tested on nearly 200
PubMed papers and represents the concrete lessons learned from that work.

---

### Fundamental differences

| Dimension | Sherlock Holmes (current) | Medical literature |
|---|---|---|
| Entity types | Person, Place, Object, Organisation | Disease, Gene, Drug, Protein, Hormone, Enzyme, Biomarker, Symptom, Procedure, Mutation, Pathway, BiologicalProcess, AnatomicalStructure, and metadata types (Author, Institution, Paper) |
| Coreference | Pronoun chains across paragraphs | Abbreviation expansion ("AML" → "acute myeloid leukaemia"), anaphoric noun phrases ("the compound", "this cohort"), British/American spelling variants |
| Predicate vocabulary | Knows, LocatedIn, Possesses, DisguisedAs | TREATS, CAUSES, INHIBITS, REGULATES, INCREASES_RISK, PREVENTS, INTERACTS_WITH, ENCODES, SUBTYPE_OF, INDICATES, LOCATED_IN, ASSOCIATED_WITH |
| Temporal reasoning | Narrative chronology, reported speech | Trial phases, treatment windows, follow-up periods, dose schedules |
| Ground-truth sources | Baker Street Fandom wiki | UMLS (CUI), HGNC (genes), RxNorm (drugs), UniProt (proteins), MeSH (diseases), ROR (institutions), ORCID (authors) |
| Ambiguity profile | Literary circumlocution, pronoun chains | Polysemous abbreviations ("MS" = multiple sclerosis *or* mass spectrometry), cross-species gene names, British/American spelling |
| Scale | One document | Hundreds of papers; cross-paper entity deduplication is essential |

---

### A restructured pipeline for medical literature

The literary pipeline maps well to a series of distinct stages, but the medical
literature variant that emerged from the kgraph/medlit work uses a different
four-stage structure that is better suited to processing a large corpus of
scientific papers:

```
pmc_xmls/  (JATS-XML or pre-parsed JSON)
    │
    ▼  Stage 0 — fetch_vocab  (cheap entity-only pass across all papers)
vocab/vocab.json + seeded_synonym_cache.json
    │
    ▼  Stage 1 — extract  (full entity + relationship extraction, one LLM call per paper)
bundles/paper_PMC*.json
    │
    ▼  Stage 2 — ingest / dedup  (cross-paper deduplication and entity promotion)
merged/entities.json + relationships.json
    │
    ▼  Stage 3 — build_bundle  (package for graph ingestion)
output/ (kgbundle format)
```

---

### Stage 0 — fetch_vocab

**What it does:** Before running full extraction, a cheap LLM call per paper
extracts entities only (no relationships). Results are merged into a shared
`vocab.json` keyed by `(normalized name, type)`. Each entry accumulates the
list of papers it appeared in and any abbreviations the LLM noted.

**Why this matters:** The vocabulary pre-pass solves two problems:

1. *Consistent naming*: The extraction prompt in Stage 1 can include the
   accumulated vocabulary, biasing the model toward canonical entity names seen
   across the corpus rather than paper-specific surface forms.
2. *UMLS type validation*: Any `umls_id` the model assigns is validated against
   the UMLS Metathesaurus API. If the assigned entity type conflicts with the
   UMLS semantic type for that CUI, it is automatically corrected. This catches
   a common class of LLM error (typing a drug as a protein, or a gene as a
   disease) before the expensive extraction pass runs.

**Output:** `vocab.json` (entity list) and `seeded_synonym_cache.json` (a
pre-seeded merge index that feeds Stage 2 deduplication).

**Local vs. frontier:** The vocabulary prompt is intentionally simpler than the
full extraction prompt. Local models (e.g. `BioMistral-7B` via Ollama) handle
it well and are the practical choice at corpus scale. UMLS validation is an API
call independent of which model was used.

---

### Stage 1 — extract (entities + relationships, one call per paper)

**What it does:** Unlike the literary pipeline's six sequential steps, the
medical variant does entity extraction and relationship extraction in a single
LLM call per paper. The model is given the full paper text (title + body, up to
500 k characters) and returns a JSON bundle with:

- `entities` — each with `id`, `name`, `class` (entity type), optional
  `canonical_id` (UMLS CUI, HGNC, etc.)
- `relationships` — subject/object entity IDs, `predicate`, `linguistic_trust`,
  `evidence_ids`
- `evidence_entities` — text spans that justify each relationship, with
  `paper_id:section:paragraph_idx:llm` IDs
- `paper` — metadata (title, authors, DOI, PMC ID, study type)

A second, cheaper LLM call extracts structured study-design metadata:
`study_type` (RCT, observational, meta-analysis, …), `sample_size`,
`multicenter`, and `held_out_validation`.

**Provenance expansion:** Author, Institution, and Paper entities plus
`AUTHORED`, `AFFILIATED_WITH`, and `DESCRIBED` relationships are automatically
derived from the paper metadata and injected into the bundle. This wires the
authorship graph without any additional LLM calls.

**Key prompt conventions that emerged from practice:**

- `linguistic_trust` on every relationship — `"asserted"`, `"suggested"`, or
  `"speculative"` — tracks how hedged the source language is ("was associated
  with" vs. "significantly reduced").
- Evidence IDs use the format `{paper_id}:{section}:{paragraph_idx}:llm`. The
  model outputs `==CURRENT_PAPER==` as a placeholder (it cannot know its own
  PMC ID); the pipeline replaces this at write time.
- Entity type disambiguation rules are embedded in the prompt:
  *classify at the most specific functional role* (Enzyme over Protein,
  Hormone over Protein), extract pathological processes (hyperplasia,
  atrophy) as Symptom entities.
- Common high-value entities are always extracted even if only mentioned
  tangentially: chemotherapy regimens (FOLFIRINOX, gemcitabine), biomarkers
  (CA19-9, KRAS), radiation modalities (SBRT, IMRT), surgical procedures.

**Local vs. frontier:** All three LLM backends (`anthropic`, `openai`, `ollama`)
are supported. The hard part for local models is accurate UMLS CUI assignment and
type disambiguation. The vocabulary pre-pass helps, but frontier models
(`claude-sonnet-*`) still produce noticeably fewer type errors and more complete
canonical ID coverage on complex oncology papers.

---

### Stage 2 — ingest / dedup

**What it does:** Reads all per-paper bundles and merges entities across the
corpus into a single deduplicated graph. This is the stage where scale forces
the most complexity.

**Deduplication strategy (multiple passes in priority order):**

1. *Authoritative ID match* — entities sharing a UMLS CUI, HGNC ID, RxNorm ID,
   UniProt accession, or MeSH ID are merged immediately regardless of name
   differences. The ID hierarchy: HGNC > UMLS > RxNorm > UniProt > MeSH.
2. *SAME_AS resolution* — the model sometimes outputs explicit
   `SAME_AS` relationships (e.g. "methotrexate" SAME_AS "MTX"). These are
   resolved before dedup so the synonym cache can be seeded.
3. *Synonym cache lookup* — the seeded cache from `fetch_vocab` ensures that
   name variants seen across papers are resolved consistently.
4. *Embedding similarity* — remaining unmatched entities are compared by cosine
   similarity of their name embeddings; pairs above 0.88 cosine similarity are
   merged. This catches spelling variants and paraphrases not covered by the
   vocabulary pre-pass.
5. *British/American spelling normalisation* — a hardcoded map handles common
   pairs (`hyperglycaemia`/`hyperglycemia`, `leukaemia`/`leukemia`, etc.)
   before any lookup.

**What could run locally:** Dedup is pure Python + embedding inference; no
frontier LLM is needed here. A local embedding model (e.g. `nomic-embed-text`
via Ollama) is sufficient for the similarity step.

---

### Stage 3 — build_bundle

**What it does:** Packages the merged entities and relationships from Stage 2
into the `kgbundle` format for graph ingestion, cross-referencing back to the
per-paper source bundles and optionally copying the original JATS-XML files
alongside the bundle for provenance tracing.

---

### Mapping to the Holmes pipeline stages

For reference, here is how the literary pipeline stages map to the medical
literature pipeline:

| Holmes stage | Medical equivalent | Notes |
|---|---|---|
| Step 1 — Sentencize | (absorbed into Stage 1 parser) | JATS-XML has explicit section structure; sentence splitting is handled by `JournalArticleParser`. For clinical notes an LLM splitter is still useful. |
| Step 2 — Coreference | (absorbed into Stage 1 prompt) | Abbreviation expansion and anaphora are handled in the single-pass extraction prompt; the chunk-and-overlap strategy is replaced by chunking at section boundaries. |
| Step 3 — Entity Merge / Wiki Linking | Stage 0 (vocab pre-pass) + Stage 2 (dedup) | Wiki linking is replaced by UMLS/HGNC/RxNorm/UniProt authority lookup. |
| Step 4 — Events & Moments | Stage 1 (single-call extraction) | Discrete event/moment modelling is replaced by PICO-framed relationship extraction with `linguistic_trust` annotation. |
| Step 5 — Triplet Extraction | Stage 1 (same call) | Slot-filling into a fixed predicate vocabulary is retained; the predicate set is replaced with a biomedical schema (see below). |
| Step 6 — Promotion | Stage 2 (dedup + canonical ID assignment) | Confidence-based promotion is folded into the cross-paper dedup pass. `linguistic_trust` replaces the binary confidence threshold. |

---

### Predicate vocabulary for medical literature

The kgraph/medlit implementation uses these predicates (derived from the
`domain_spec.py` in `graphwright/kgraph`):

| Predicate | Subject types | Object types | Notes |
|---|---|---|---|
| `TREATS` | Drug, Procedure | Disease | Therapeutic use |
| `CAUSES` | Gene, Mutation, Hormone | Disease, Symptom | Causal mechanism |
| `INHIBITS` | Drug, Protein | Protein, Pathway | Inhibition |
| `REGULATES` | Drug, Gene | Gene, Pathway | Up- or down-regulation |
| `INCREASES_RISK` | Gene, Mutation | Disease | Risk factor |
| `PREVENTS` | Drug | Disease | Prophylactic use |
| `INTERACTS_WITH` | Drug | Drug | Drug-drug interaction (symmetric) |
| `ENCODES` | Gene | Protein | |
| `SUBTYPE_OF` | Disease | Disease | Nosological hierarchy |
| `INDICATES` | Biomarker, Evidence | Disease | Diagnostic signal |
| `LOCATED_IN` | Symptom, Disease | AnatomicalStructure | Anatomical site |
| `ASSOCIATED_WITH` | any | any | General; use when no specific predicate fits (symmetric) |
| `SAME_AS` | any | any | Coreference / synonym merge signal |
| `AUTHORED` | Author | Paper | Auto-generated from metadata |
| `AFFILIATED_WITH` | Author | Institution | Auto-generated from metadata |
| `DESCRIBED` | Paper | any | Top 2 central entities per paper |
| `CITES` | Paper | Paper | Citation graph |

---

### Full entity type set

The entity types used in the battle-tested implementation (from
`medlit/medlit/domain_spec.py` in `graphwright/kgraph`):

**Biomedical:** Disease, Gene, Drug, Protein, Hormone, Enzyme, Biomarker,
Symptom, Procedure, Mutation, Pathway, BiologicalProcess, AnatomicalStructure,
Hypothesis

**Metadata (auto-extracted from paper header):** Author, Institution, Paper

**Epidemiological:** Location, Ethnicity

**Internal:** Evidence (text-span evidence entities)

---

### Canonical ID authorities

The dedup stage recognises these authoritative IDs for entity merging (highest
to lowest priority for genes):

| Authority | Format | Used for |
|---|---|---|
| HGNC | `HGNC:12345` | Genes (highest priority) |
| UMLS | `C0000000` (CUI) | Diseases, symptoms, procedures |
| RxNorm | `RxNorm:12345` | Drugs |
| UniProt | `P12345` / `Q12345` | Proteins |
| MeSH | `D012345` | Diseases, drugs |
| ROR | `https://ror.org/…` | Institutions |
| ORCID | `ORCID:…` | Authors |
| PMC | `PMC1234567` | Papers |

---

### What could still run locally in the medical domain?

The local/frontier split looks similar to the literary case, with one important
shift: biomedical-fine-tuned models in the 7B–14B range (e.g. `BioMistral-7B`,
`Meditron-7B`) are substantially better than general-purpose models of the same
size on entity recognition and relation extraction from scientific text. Using
such a model via Ollama for the vocab pre-pass and entity extraction closes
much of the quality gap with a frontier model — without incurring API cost.

The hardest parts to run locally are accurate UMLS CUI assignment (requires
broad biomedical knowledge) and type disambiguation on ambiguous entities.
Frontier models or specialised biomedical APIs (e.g. a self-hosted BiomedBERT
ensemble) remain the practical options for high-quality canonical ID coverage.

The dedup stage (Stage 2) and build_bundle stage (Stage 3) require no frontier
LLM at all; they run on CPU with a local embedding model.

---

## Summary

This pipeline is designed around a clear principle: use the cheapest model that
is *good enough* for each step. Sentence splitting and slot-filling are
well-bounded tasks that local 14B models handle reliably. Entity
disambiguation and narrative reasoning require broad world-knowledge that only
frontier models currently provide reliably.

For a medical literature adaptation, the same architectural pattern applies —
keep the stage-by-stage design and the local/frontier two-tier model strategy —
but replace the domain-specific prompts, predicate schema, and knowledge-base
linking targets with their biomedical counterparts. The restructured four-stage
pipeline (vocab pre-pass → single-call extraction → cross-paper dedup →
bundle build) that emerged from the kgraph/medlit work is a proven starting
point.
