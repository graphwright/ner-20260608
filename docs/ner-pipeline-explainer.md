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

### Fundamental differences

| Dimension | Sherlock Holmes (current) | Medical literature |
|---|---|---|
| Entity types | Person, Place, Object, Organisation | Drug, Disease, Gene, Protein, Dosage, Adverse Event, Clinical Trial, Patient Cohort |
| Coreference | Pronoun chains across paragraphs | Abbreviation expansion ("AML" → "acute myeloid leukaemia"), anaphoric noun phrases ("the compound", "this cohort") |
| Predicate vocabulary | Knows, LocatedIn, Possesses, DisguisedAs | Treats, Contraindicated­With, Metabolised­By, UpRegulates, AssociatedWith (risk), Dosage­For, ReportedIn |
| Temporal reasoning | Narrative chronology, reported speech | Trial phases, treatment windows, follow-up periods, dose schedules |
| Ground-truth sources | Baker Street Fandom wiki | UMLS, MeSH, DrugBank, ClinVar, UniProt, ClinicalTrials.gov |
| Ambiguity profile | Literary circumlocution, pronoun chains | Polysemous abbreviations (e.g. "MS" = multiple sclerosis *or* mass spectrometry), cross-species gene names |

### Step-by-step changes

**Step 1 — Sentencize:** Minimal change needed. Medical abstracts are already
well-structured, so a rule-based sentence splitter (`spaCy` with a biomedical
model, or `scispaCy`) is often sufficient and faster than an LLM call. For
clinical notes (informal, heavily abbreviated) an LLM splitter remains
beneficial.

**Step 2 — Coreference:** The system prompt must be rewritten for biomedical
text. Abbreviation expansion ("the drug" → which drug?) and species-specific
pronoun contexts require domain-tuned examples. A biomedical-fine-tuned model
(e.g. `BioMedBERT`, `PubMedBERT`, or a fine-tuned `qwen2.5`) outperforms a
general-purpose LLM at the same parameter count. The chunk-and-overlap strategy
is retained.

**Step 3 — Entity Merge:** The wiki-linking target changes entirely. Instead
of the Baker Street Fandom wiki, linking should target:
- **UMLS Metathesaurus** for diseases and symptoms
- **DrugBank** or **ChEMBL** for drugs and small molecules
- **UniProt** for proteins and genes
- **ClinicalTrials.gov** for trials

The label-clustering prompt must know that two labels that look very similar
("methotrexate" vs. "MTX") are the same entity, while two labels that look
different ("HER2" vs. "ERBB2") are also the same entity. This requires
biomedical world-knowledge — a frontier model or a specialised biomedical NER
model (e.g. `SciSpaCy`'s `en_ner_bionlp13cg_md`) is needed here.

**Step 4 — Events & Moments:** The predicate ontology shifts dramatically.
Instead of narrative events ("Holmes disguises himself"), the relevant events are
clinical: treatment administration, adverse event onset, trial enrollment,
lab-result observation. The event-extraction prompt must be rewritten around
clinical trial structure, PICO framing (Population, Intervention, Comparison,
Outcome), and causal/correlational hedging ("was associated with", "did not
significantly differ"). Frontier models remain necessary; specialised biomedical
LLMs (e.g. MedPaLM 2, BioGPT, or fine-tuned Llama variants) may outperform
general-purpose Claude on highly technical content.

**Step 5 — Triplets:** The predicate vocabulary (`holmes_schema.py`) must be
replaced with a biomedical relation schema. A suitable starting point is the
relation types in the **BioRED** or **DDI Extraction** shared tasks:
`Treats`, `Causes`, `Contraindicated_With`, `Metabolised_By`,
`UpRegulates`, `DownRegulates`, `Associated_With`. The slot-filling prompt
must be rewritten with biomedical examples and the alias-ID strategy is retained
as-is.

**Step 6 — Promotion:** The confidence threshold calibration would need
re-tuning for biomedical text (where false positives carry higher stakes than
in literary analysis). Domain-specific human review of a sample is strongly
recommended before treating `asserted_true` records as reliable clinical facts.

### What could still run locally in the medical domain?

The local/frontier split looks similar to the literary case, with one important
shift: biomedical-fine-tuned models in the 7B–14B range (e.g. `BioMistral-7B`,
`Meditron-7B`) are substantially better than general-purpose models of the same
size on entity recognition and relation extraction from scientific text. Using
such a model via Ollama for steps 1, 2, and 5 would close much of the quality
gap with a frontier model — without incurring API cost.

Steps 3 and 4 remain the hardest to run locally because they require broad
world-knowledge of the biomedical literature (entity disambiguation across
synonyms and abbreviations, clinical trial reasoning). Frontier models or
specialised biomedical APIs (e.g. a self-hosted BiomedBERT ensemble) are the
practical options here.

---

## Summary

This pipeline is designed around a clear principle: use the cheapest model that
is *good enough* for each step. Sentence splitting and slot-filling are
well-bounded tasks that local 14B models handle reliably. Entity
disambiguation and narrative reasoning require broad world-knowledge that only
frontier models currently provide reliably.

For a medical literature adaptation, the same architectural pattern applies —
keep the JSONL-based, stage-by-stage design and the local/frontier two-tier
model strategy — but replace the domain-specific prompts, predicate schema,
and knowledge-base linking targets with their biomedical counterparts.
