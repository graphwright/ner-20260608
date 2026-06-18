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

**Link:** [https://claude.ai/chat/a3eaadb6-09f1-4fdc-b179-04dd0024b254](https://claude.ai/chat/a3eaadb6-09f1-4fdc-b179-04dd0024b254)  

## User:

We've talked a bit about how the g533 Tower that I purchased is potentially a good fit for graph extraction but is not so good for querying and synthesizing responses. You would still agree with that [...]

## Assistant:

Yes, that assessment holds. The nuance from our conversation was actually more specific than "graphics traction" — the G533's RX 9060 XT with 16GB VRAM is well-suited for **batch inference workloads[...]

Where it's weaker is **interactive query synthesis** — the orchestrator/synthesizer pattern where you want fast, high-quality multi-hop reasoning over graph results. Two compounding issues:

1. **ROCm vs CUDA** — AMD's ROCm stack still has more friction and occasional performance gaps vs CUDA for inference workloads in Ollama, even with improvements.
2. **Model quality ceiling** — The models that fit well in 16GB (qwen2.5:14b, qwen2.5-coder:14b) are good but not frontier-class. For complex synthesis over BFS results, you noticed during our gwcha[...]

The conclusion we landed on was: use the G533 for ingestion (where throughput matters more than latency and quality), and keep frontier cloud models (Claude, GPT-4-class) for query synthesis where rea[...]

Has something changed that's prompting the question, or are you revisiting the architecture?

## User:

So let's think about a pipeline design starting with the NER phase where the first thing we do is we take the sentences in a scandal in Bohemia and we number them and put one on each line so that we n[...]

## Assistant:

Good starting point. A few design questions before we sketch the pipeline:

The sentences need to be numbered in a way that survives the whole pipeline — so the numbering scheme matters. A few options:

**Flat sequential** — sentence 1 through N across the whole story. Simple, unambiguous, easy to reference in NER output (`"sentence": 47`).

**Hierarchical** — paragraph.sentence, e.g. `3.2` for paragraph 3, sentence 2. More human-navigable when debugging, but slightly more complex as a reference key.

For NER purposes, flat sequential is almost certainly the right call — you just want a stable integer key that the model can emit alongside each entity mention.

**Practical questions:**

- What's your source text? Plain UTF-8 from Project Gutenberg? That'll have some cleanup needed (chapter headers, the story title line, "THE END" etc.) that you probably want stripped before numbering[...]
- Are you using spaCy's sentencizer, NLTK's punkt, or something else for sentence splitting? Conan Doyle's sentences are mostly well-behaved but there are some edge cases (dialogue with attribution, a[...]
- Do you want the numbered output as a plain text file (one sentence per line, `001\t<sentence>`), a JSON array, or something else? The format affects how you feed it into the NER phase.

My instinct: plain TSV or JSONL, flat sequential, spaCy for splitting with the `en_core_web_sm` sentencizer and a custom abbreviation list for the Doyle-specific cases. What do you have in mind?

## User:

I would think Jason l is the way to go, and I would want to use the LLM itself as a splitter rather than spacey.

## Assistant:

Makes sense on both counts. JSONL is the right choice — each line is self-contained, easy to stream, easy to append to or slice. And using the LLM as the splitter sidesteps the abbreviation problem [...]

The practical question is chunking. You can't feed the whole story to the LLM in one shot and ask for numbered sentences — the output would be enormous and you'd have no reliable way to stitch chunk[...]

1. **Split the raw text into paragraphs** (on double newlines) — this is trivial and robust.
2. **Feed paragraphs to the LLM in batches**, asking it to return JSONL where each object is `{"id": <int>, "text": "<sentence>"}`, with a **carry-in offset** so numbering is continuous across chunks.
3. **Concatenate the JSONL output** — since each line is independent and IDs are globally unique, this is just a cat.

Each JSONL record would look like:
```json
{"id": 1, "para": 1, "text": "To Sherlock Holmes she is always the woman."}
```

Including `para` is cheap and useful — it lets you reconstruct reading order and gives NER a coarser locality signal alongside the sentence ID.

The prompt to the LLM for each batch would be something like:

> Split the following text into individual sentences. Return one JSON object per line with keys `id` (integer, starting at {offset}), `para` (paragraph number, starting at {para_offset}), and `text` ([...]

The "verbatim" instruction matters — you don't want the model normalizing quotes or fixing Doyle's punctuation.

Want to sketch the Python harness for this?

## User:

Yes please

## Assistant:

A few things worth calling out:

**Resume support** — if the run dies mid-way, it reads the highest `id` and `para` from the existing output file and picks up from the next unprocessed paragraph. Critical for a story-length job.

**Tolerant parser** — the model will occasionally emit markdown fences or a preamble sentence. The parser strips fences and tries a regex salvage on lines that look like JSON but have leading junk.

**`temperature: 0.0`** — deterministic output matters here; you want the same sentence boundaries every time, not creative variation.

**`--gutenberg` flag** — pass it if your source is from Project Gutenberg; it strips the legal header/footer before paragraph extraction.

Typical invocation:
```bash
python sentencize.py \
  --input bohemia.txt \
  --output bohemia_sentences.jsonl \
  --gutenberg \
  --model qwen2.5:14b
```

The one thing I'd watch for: `qwen2.5:14b` is generally well-behaved about following JSON-only instructions, but you may get occasional off-by-one errors in the `id` field (model resets to 1 mid-batch[...]

> File: [/mnt/user-data/outputs/sentencize.py](https://claude.ai/api/organizations/840440cd-f1be-4af9-8726-7ed95cf3825c/conversations/a3eaadb6-09f1-4fdc-b179-04dd0024b254/wiggle/download-file?path=%2F[...]

## User:

The next step in the process will be to take a collection of sentences, a chunk, and identify the nouns and pronouns and proper nouns in that chunk and group them together by which ones are likely to [...]

## Assistant:

That's the coreference resolution step — arguably the hardest part of the NER pipeline for literary text, because Doyle relies heavily on pronouns and circumlocutions ("the King", "the lady", "my cl[...]

The key design question is what the LLM returns. I'd suggest the output schema be something like:

```json
{"chunk_id": "1-20", "entities": [
  {"label": "Irene Adler", "mentions": [
    {"sentence_id": 3, "span": "the woman"},
    {"sentence_id": 7, "span": "she"},
    {"sentence_id": 12, "span": "Irene Adler"}
  ]},
  {"label": "Sherlock Holmes", "mentions": [
    {"sentence_id": 1, "span": "Sherlock Holmes"},
    {"sentence_id": 4, "span": "he"},
    {"sentence_id": 9, "span": "my friend"}
  ]}
]}
```

The `label` is the LLM's best canonical name for the entity — it can upgrade a pronoun cluster to a proper name if it sees one in the chunk. The `span` is verbatim text from the sentence.

A few design tensions worth resolving before writing code:

**Chunk boundaries** — coref chains can span chunk boundaries. "She" in sentence 21 may resolve to "Irene Adler" introduced in sentence 8. You need a **carry-over context**: include the last N sente[...]

**Cross-chunk identity merging** — entity labels won't be perfectly consistent across chunks ("Irene Adler" vs "the lady" as the canonical label for the same entity). You'll need a merge pass afterw[...]

**Confidence** — for ambiguous pronouns ("it", "they") an optional `"confidence": 0.7` field lets you filter during the merge pass.

Does that schema look right to you, or do you want to adjust the structure before we write the harness?

## User:

That all looks pretty reasonable, go ahead and code that please.

## Assistant:

Typical invocation:
```bash
python coref.py \
  --input bohemia_sentences.jsonl \
  --output bohemia_coref.jsonl \
  --chunk-size 20 \
  --overlap 3
```

A few design notes worth flagging:

**Context leak guard** — the parser explicitly filters out any mention whose `sentence_id` falls outside the current chunk. Models occasionally pull a sentence ID from the context block despite bein[...]

**`type` field** — person/place/object/organization/other. Useful later when you're building the entity table and want to separate, say, "Baker Street" from "Holmes".

**What this doesn't yet solve** — the cross-chunk merge problem. After this runs you'll have `bohemia_coref.jsonl` with one record per chunk, and "Irene Adler" in chunk 1 and "the lady" in chunk 3 a[...]

Want to tackle that merge pass next, or run the first two steps against the actual text first to see what the output looks like?

> File: [/mnt/user-data/outputs/coref.py](https://claude.ai/api/organizations/840440cd-f1be-4af9-8726-7ed95cf3825c/conversations/a3eaadb6-09f1-4fdc-b179-04dd0024b254/wiggle/download-file?path=%2Fmnt%2[...]

## User:

Let's look at the merge pass, and for an authoritative ontology let's plan to use the Baker Street wiki.

## Assistant:

Good call. Using the Baker Street Wiki as canonical authority means your entity labels won't drift across chunks — "Irene Adler" and "the Prima Donna" and "the woman" all resolve to the same wiki pa[...]

The merge pass has two distinct sub-problems:

**1. Intra-story clustering** — grouping chunk-level labels that clearly refer to the same entity ("the King", "His Majesty", "Wilhelm Gottsreich Sigismond von Ormstein") into a single candidate ent[...]

**2. Wiki linking** — taking each candidate entity and finding its Baker Street Wiki page, which gives you the canonical label, the URL as a stable ID, and potentially structured data (character typ[...]

For the wiki side, the question is what's available. Baker Street Wiki is a Fandom wiki, so it has:
- A search endpoint: `https://bakerstreet.fandom.com/api.php?action=opensearch&search=<query>`
- Full page content via the MediaWiki API if you need it for disambiguation

The merge architecture I'd propose:

**Pass 1 — label clustering (LLM)**: Feed all unique entity labels from across all chunks to the LLM in one call (or batched if large), ask it to group labels that refer to the same entity and nomin[...]

**Pass 2 — wiki lookup (API)**: For each canonical name, query the Baker Street Wiki search API, take the top result, and store the URL as the entity's stable ID. Flag low-confidence matches for hum[...]

**Pass 3 — mention rewriting**: Walk back through `bohemia_coref.jsonl` and rewrite every mention's entity label to the canonical form, adding a `wiki_url` field.

The output entity table would look like:

```json
{
  "canonical": "Irene Adler",
  "wiki_url": "https://bakerstreet.fandom.com/wiki/Irene_Adler",
  "type": "person",
  "aliases": ["the woman", "the lady", "she", "her", "the Prima Donna"],
  "mentions": [
    {"sentence_id": 1, "span": "the woman", "confidence": 1.0},
    {"sentence_id": 7, "span": "she", "confidence": 0.9}
  ]
}
```

One wrinkle worth thinking about before coding: some entities in the story won't have Baker Street Wiki pages — incidental characters, generic locations. You'll want a `wiki_url: null` fallback rath[...]

Want me to go ahead and code all three passes, or would you rather see the wiki API behavior first with a quick exploratory fetch?

## User:

For those unfindable incidental characters and things, use an ID like "provisional:<number>".

## Assistant:

Good. That keeps every entity in the table addressable with a stable ID regardless of wiki coverage, and the `provisional:` prefix makes it obvious during downstream processing which ones lack authori[...]

Coding all three passes now:

> Created file, presented file
>
> - Merge pass - cluster chunk-level coref labels, link to Baker Street Wiki, emit global entity table
> - Presenting file(s)...
> - **Done**

Typical invocation:
```bash
python merge.py \
  --coref bohemia_coref.jsonl \
  --entities bohemia_entities.jsonl \
  --mentions bohemia_mentions.jsonl
```

A few design notes:

**Two-attempt wiki lookup** — first tries the canonical name, then falls back to the longest alias (which is often the most specific proper name). "the woman" won't find a wiki page; "Irene Adler" w[...]

**`entity_id` scheme** — `wiki:Irene_Adler` for linked entities, `provisional:1` for everything else. The wiki slug is derived directly from the URL so it's stable across runs.

**`--skip-wiki` flag** — useful for fast iteration during development; assigns provisional IDs to everything so you can inspect the clustering output before burning wiki API calls.

**Flat mention output** — `bohemia_mentions.jsonl` has one record per mention with `entity_id`, `canonical`, `wiki_url`, `sentence_id`, `span`, and `confidence`. That's your primary query surface do[...]

At this point the full pipeline is `sentencize.py → coref.py → merge.py`, producing three artifacts: sentences, entities, and mentions. The natural next step would be a simple query tool that take[...]

> File: [/mnt/user-data/outputs/merge.py](https://claude.ai/api/organizations/840440cd-f1be-4af9-8726-7ed95cf3825c/conversations/a3eaadb6-09f1-4fdc-b179-04dd0024b254/wiggle/download-file?path=%2Fmnt%2[...]


---
---
---
---

## User:

> 6/9/2026 6:49:34

The next step is to build triplets. Strategically, we could either (a) dig into the text to find what's there, or (b) we could rely on an a-priori predicate set that we cooked up earlier. Given the re[...]

## Assistant:

> 6/9/2026 6:49:46

The same logic applies as with clustering — predicate extraction from literary text is a reasoning task, not a pattern-matching task, and the two sub-problems have different characters.

**Predicate set design (a-priori)** is a one-time schema decision. You want predicates that are meaningful, consistent, and reusable across stories — `located_at`, `employed_by`, `romantically_linke[...]

**Triplet extraction from text** is where the volume is. Given a sentence, a predicate set, and a mention index, "find all (subject, predicate, object) triples that hold in this sentence" is exactly t[...]

So the strategy: **design the predicate set with Claude (or by hand), then run extraction locally on the G533 against the sentence JSONL + mention index.** The mention index is key — you pass in the[...]

The a-priori predicate set also has a deeper payoff: it's reusable across all Holmes stories, and eventually maps cleanly to a typed graph schema where predicates become edge types. That's the Graphwr[...]

Want to start by drafting the predicate set with me, or do you have one from earlier work we should pull in?

## User:

> 6/9/2026 6:53:18

> File: holmes_schema.py

> File: graph.py

Here is something from earlier work. Does this appear suitable?

## Assistant:

> 6/9/2026 6:53:24

Yes, this is exactly the right foundation. A few observations:

`graph.py` is clean and well-suited. The `out_edges`/`in_edges` dual index, BFS with hop layers, and `truth_status` filtering are all things you'll want for querying a Holmes story graph. The `truth_s[...]

Can you share `holmes_schema.py` as well? The document block only came through for `graph.py`. The schema is where the predicate set lives, and I want to see what edge types you already defined before[...]

## User:

> 6/9/2026 6:54:03

> File: holmes_schema.py

try it now

## Assistant:

> 6/9/2026 6:54:21

> Viewed file
>
> - Read holmes_schema.py
> - **Done**

This is excellent — and it's already further along than I expected. A few observations before we design the extraction prompt:

**The predicate set is well-suited for triplet extraction.** The predicates that will fire most often in Bohemia are `AssociatedWith`, `Knows`, `Possesses`, `DisguisedAs`/`HasTrueIdentity`, and `Invol[...]

**The `ProvenanceMixin` fields map cleanly onto what we have.** `story_id` is fixed ("bohemia"), `paragraph_index` is derivable from the sentence JSONL (`para` field), `extraction_confidence` is the m[...]

**`truth_status` defaults to `HYPOTHETICAL`** in the schema, which is exactly right for pipeline output — a separate promotion pass can flip things to `ASSERTED_TRUE` based on evidence weight.

**The entity IDs are already resolved.** `entity_id` in `bohemia_entities.jsonl` is either `wiki:<slug>` or `provisional:<n>`, and `wiki:<slug>` maps directly to Baker Street Wiki URIs — which are t[...]

The extraction strategy I'd propose: run locally on the G533, one chunk at a time, with the mention index for that chunk injected into the prompt. The prompt tells the model exactly which predicates e[...]

Ready to write `triplets.py`?

## User:

> 6/9/2026 6:55:10

> File: 

Let me share some notes from that work so you have full context here.

## Assistant:

> 6/9/2026 6:55:30

This is very helpful context — `scandal_instances.py` is essentially the gold standard for what the triplet extraction pipeline should produce. A few things this clarifies:

**The entity ID convention is already settled.** Wiki-linked entities use the full Baker Street Wiki URI (`https://bakerstreet.fandom.com/wiki/Sherlock_Holmes`), and corpus-local entities use a `sib:`[...]

**The predicate set divides cleanly into two extraction tiers:**

*Local (G533) — slot-filling against known entities:*
- `AssociatedWith` — Person → Location
- `Knows` — Person → Person
- `LocatedIn` — Location → Location
- `Possesses` — Person → Object|Document
- `DisguisedAs` / `HasTrueIdentity` — Person → Persona / Persona → Person
- `Involves` — Event → Person|Persona
- `OccurredAt` — Event → Moment

*Defer to frontier model or manual:*
- `KnewAt` — higher-order, requires pointing at another predicate instance
- `Contradicts` — higher-order, same problem
- `Executes` — needs Plan entities which require their own extraction pass

**`Event`, `Moment`, and `Plan` are corpus-local constructs** that don't exist in `bohemia_entities.jsonl` — the coref pipeline wouldn't have produced them since they're not noun phrases referring t[...]

**The `ProvenanceMixin` fields are all available from context:**
- `story_id` = `"scandal_in_bohemia"` (fixed)
- `paragraph_index` = from the `para` field in `bohemia_sentences.jsonl`
- `asserting_narrator` = usually `watson` for this story; the model can flag exceptions
- `extraction_method` = `"llm-triplet-extraction"`
- `extraction_confidence` = float emitted by the model

The practical question before writing `triplets.py`: how do you want to handle `Event` and `Moment` instances? Options:

1. **Mint them inline** — the extraction pass emits new corpus-local IDs (`sib:event:...`, `sib:moment:...`) alongside triplets that reference them, and a separate collector assembles them into enti[...]
2. **Separate event extraction pass** — run a dedicated pass that identifies discrete events and moments, assigns IDs, then feed that index into the triplet pass.
3. **Defer `Involves`/`OccurredAt`** — skip event-anchored predicates in the automated pass, handle them manually or with a frontier model, and focus the local pass on the simpler dyadic predicates [...]

Option 3 is the most conservative and gets you real graph data fastest. Option 2 is cleaner architecturally. What's your preference?

## User:

> 6/9/2026 6:56:00

Let's do option 2

## Assistant:

> 6/9/2026 6:56:15

Good choice. The event extraction pass is conceptually cleaner and produces a richer index for the triplet pass — once you have named events with stable IDs, `Involves` and `OccurredAt` become strai[...]

The event extraction pass needs to do three things:

**1. Identify discrete events** — find sentences or sentence clusters that describe a thing that happened: an action, an arrival, a revelation, a deception. Not states ("Irene lives at Briony Lodge"[...]

**2. Identify moments** — temporal anchors, either explicit ("on the evening of March 20th") or implicit ("when Holmes arrived"). Moments come in two flavors per the schema: objective timeline (no `[...]`

**3. Assign stable corpus-local IDs** — `sib:event:<slug>` and `sib:moment:<slug>` where the slug is a short snake_case description. Content-addressed would be cleaner but slugs are more debuggable.

The output is two JSONL files — `bohemia_events.jsonl` and `bohemia_moments.jsonl` — that feed into the triplet pass as part of the entity index alongside `bohemia_entities.jsonl`.

Each event record:
```json
{
  "id": "sib:event:kings_visit",
  "description": "The King of Bohemia visits Holmes at Baker Street, initially disguised as Count Von Kramm.",
  "sentence_ids": [43, 44, 45],
  "para": 12,
  "participants": ["wiki:Sherlock_Holmes", "wiki:Wilhelm_von_Ormstein"],
  "extraction_confidence": 0.95
}
```

Each moment record:
```json
{
  "id": "sib:moment:kings_visit_evening",
  "label": "Evening of 20 March 1888 — King visits Baker Street",
  "event_id": "sib:event:kings_visit",
  "narrator_id": null,
  "sentence_ids": [43],
  "extraction_confidence": 0.9
}
```

The `participants` field in the event record is pre-linking — the model pulls from the known entity index, which means the triplet pass gets `Involves` edges nearly for free. The `event_id` on momen[...]

This pass warrants a frontier model call — event and moment identification requires narrative reasoning, distinguishing state from action, and handling Doyle's indirect narration (Watson often descr[...]

Ready to write `events.py`?

## User:

> 6/9/2026 6:56:23

yes please

## Assistant:

> 6/9/2026 6:57:55

> Created file, presented file
>
> - Event and moment extraction pass using Claude API
> - Presenting file(s)...
> - **Done**

A few design decisions worth noting:

**Sidecar progress file** — since events and moments go to separate output files there's no single file to scan for resume state, so a `.bohemia_events_progress.json` sidecar tracks completed chunk [...]

**`known_event_ids` threading** — events are accumulated into a set as each chunk is processed and passed into `validate_moment` so moment `event_id` references can be checked. Forward references (a[...]

**`SlugRegistry`** — enforces global uniqueness of `sib:event:` and `sib:moment:` slugs within a run. If Claude emits the same slug in two chunks (which happens at chunk boundaries for events that s[...]

**Entity ID expansion** — `load_entities` expands `wiki:Sherlock_Holmes` → `https://bakerstreet.fandom.com/wiki/Sherlock_Holmes` so the prompt shows full URIs matching the schema's ID convention, [...]

Typical invocation:
```bash
python events.py \
  --sentences bohemia_sentences.jsonl \
  --entities  bohemia_entities.jsonl \
  --events    bohemia_events.jsonl \
  --moments   bohemia_moments.jsonl
```

After this runs successfully, `triplets.py` has everything it needs: entities, events, and moments all indexed with stable IDs.

> File: [/mnt/user-data/outputs/events.py](https://claude.ai/api/organizations/840440cd-f1be-4af9-8726-7ed95cf3825c/conversations/a3eaadb6-09f1-4fdc-b179-04dd0024b254/wiggle/download-file?path=%2Fmnt%[...]

## User:

> 6/9/2026 7:00:36

pardon the flaky memory of an old man, we haven't written triples.py yet, have we?

## Assistant:

> 6/9/2026 7:00:41

No, we haven't — `triplets.py` is the next thing on the list. We've built:

1. `sentencize.py` — plain text → numbered sentence JSONL
2. `coref.py` — sentences → chunk-level entity/mention clusters
3. `merge.py` — clusters → global entity table + flat mention index (with Claude for clustering and wiki judgment)
4. `events.py` — sentences + entity index → events and moments JSONL

`triplets.py` is the step that reads all four of those outputs and emits predicate instances — the actual edges of the typed graph — as JSONL. Ready to write it?

## User:

> 6/9/2026 7:00:55

yes please

## Assistant:

> 6/9/2026 7:02:48

> Created file, presented file
>
> - Triplet extraction pass - reads sentences, entities, events, moments and emits predicate instances as JSONL
> - Presenting file(s)...
> - **Done**

Typical invocation:
```bash
python triplets.py \
  --sentences bohemia_sentences.jsonl \
  --entities  bohemia_entities.jsonl \
  --events    bohemia_events.jsonl \
  --moments   bohemia_moments.jsonl \
  --output    bohemia_triplets.jsonl
```

A few design notes worth calling out:

**Local model, not Claude.** This is the slot-filling pass — the entity index constrains the output space so tightly that `qwen2.5:14b` handles it well. The prompt injects partitioned entity IDs (pe[...]

**Domain/range enforcement in Python.** `validate_triplet` checks predicate, subject type, and object type against `DOMAIN_RANGE` which mirrors `holmes_schema.py` exactly. A `Knows(Location → Person[...]

**`TripletRegistry`** deduplicates on `(predicate, subject_id, object_id)` across the whole run, including across resume boundaries — it pre-loads existing output on startup.

**`truth_status` is always `hypothetical`.** Consistent with the schema's lifecycle: the pipeline creates instances as hypothetical, a separate promotion pass upgrades them to `asserted_true` based on[...]

**Deferred predicates are noted but not attempted.** `KnewAt`, `Contradicts`, and `Executes` need either higher-order object references or `Plan` entities — those warrant their own pass, likely with[...]

The full pipeline is now complete end-to-end: `sentencize → coref → merge → events → triplets`.

> File: [/mnt/user-data/outputs/triplets.py](https://claude.ai/api/organizations/840440cd-f1be-4af9-8726-7ed95cf3825c/conversations/a3eaadb6-09f1-4fdc-b179-04dd0024b254/wiggle/download-file?path=%2Fmn[...]

## User:

> 6/9/2026 7:18:08

Looks like trouble

```
➜  ner-20260608 git:(main) pdm run python src/triplets.py \
  --sentences bohemia_sentences.jsonl \
  --entities  bohemia_entities.jsonl \
  --events    bohemia_events.jsonl \
  --moments   bohemia_moments.jsonl \
  --output    bohemia_triplets.jsonl
Loaded: 689 sentences, 72 entities, 178 events, 36 moments
Entity index: 31 persons, 1 personas, 10 locations, 19 objects, 178 events, 36 moments
Generated 46 chunks (size=15, overlap=3)
  [  1/46] chunk 1-15 (15 sentences, 0 context) ...   [warn] unknown subject_id: 'sib:entity:watson'
  [warn] unknown subject_id: 'sib:entity:watson'
  [warn] unknown subject_id: 'sib:entity:watson'
  [warn] unknown subject_id: 'sib:location:serpentine_avenue'
  [warn] unknown subject_id: 'sib:entity:adler'
  [warn] unknown subject_id: 'sib:persona:client'
  [warn] unknown subject_id: 'sib:persona:client'
  [warn] unknown object_id: 'sib:entity:watson'
  [warn] unknown object_id: 'sib:entity:adler'
  [warn] unknown object_id: 'sib:entity:king'
  [warn] unknown object_id: 'sib:entity:honoria_mrs_watson'
```

## Assistant:

> 6/9/2026 7:20:45

The model is inventing IDs rather than using the ones from the index. It's ignoring the injected entity index and constructing its own `sib:entity:` scheme. Two problems:

1. **The entity IDs we're injecting are long Baker Street Wiki URIs** — `https://bakerstreet.fandom.com/wiki/Sherlock_Holmes` — which the model finds unwieldy and substitutes with its own compact [...]

2. **178 events is a lot to inject into every chunk prompt.** That's drowning the useful signal and likely pushing the model toward inventing IDs rather than scanning a wall of text.

Both are fixable. The approach:

**Short alias IDs in the prompt** — inject a compact alias like `person:sherlock_holmes` in the prompt, map it back to the canonical URI in the validator. The model works with short IDs; we expand t[...]

**Filter events/moments to the chunk window** — only inject events and moments whose `sentence_ids` overlap or are near the current chunk, rather than the full 178. This dramatically shrinks the pro[...]

Let me patch those two things:

> Viewed file
>
> - Read current triplets.py to make targeted edits
> - **Done**

Good, I have the full picture. Two surgical changes needed:

1. **Short alias IDs in the prompt** with a lookup table to expand back to canonical IDs post-validation
2. **Filter events/moments to chunk window** — only inject those whose `sentence_ids` are within a window around the current chunk

> Edited file, ran command, presented file
>
> - Add alias ID scheme and chunk-window filtering for entity index
> - Rewrite triplets.py with alias ID scheme and event window filtering
> - Presenting file(s)...
> - **Done**

The two root causes are fixed:

**Alias IDs** — the prompt now shows `person:sherlock_holmes -> Sherlock Holmes` instead of `https://bakerstreet.fandom.com/wiki/Sherlock_Holmes -> Sherlock Holmes`. The model uses the short alias; [...]

**Event window filtering** — instead of all 178 events, each chunk now sees only the events and moments whose `sentence_ids` fall within `--event-window` sentences of the chunk boundaries (default �[...]

The `[warn] unknown subject alias` messages will still appear if the model invents a completely novel ID, but that should now be rare since the aliases are short and memorable.

> File: [/mnt/user-data/outputs/triplets.py](https://claude.ai/api/organizations/840440cd-f1be-4af9-8726-7ed95cf3825c/conversations/a3eaadb6-09f1-4fdc-b179-04dd0024b254/wiggle/download-file?path=%2Fmn[...]

## User:

> 6/9/2026 7:30:03

Still a bit of trouble

```
➜  ner-20260608 git:(main) pdm run python src/triplets.py --sentences bohemia_sentences.jsonl --entities  bohemia_entities.jsonl --events    bohemia_events.jsonl --moments   bohemia_moments.jsonl --[...]
Loaded: 689 sentences, 72 entities, 178 events, 36 moments
Alias index: 31 persons, 1 personas, 10 locations, 19 objects, 178 events, 36 moments
Generated 46 chunks (size=15, overlap=3, event-window=+/-30)
  [  1/46] chunk 1-15 (15 sents, 0 ctx, 14 evts, 1 moms) ... -> 4 triplets (total 4)
  [  2/46] chunk 16-30 (15 sents, 3 ctx, 16 evts, 2 moms) ...   [warn] unknown object alias: 'person:dr_watson'
  [warn] unknown object alias: 'person:dr_watson'
-> 8 triplets (total 12)
  [  3/46] chunk 31-45 (15 sents, 3 ctx, 22 evts, 2 moms) ... [error attempt 1: timed out] 
```

## Assistant:

> 6/9/2026 7:30:55

Two issues visible here:

**`person:dr_watson` unknown** — the alias generated from "Dr Watson" is `person:dr_watson` but the model is emitting `person:dr_watson` with a period or slightly different slug. Let's check what al[...]

The `make_alias` function does `re.sub(r"[^a-z0-9]+", "_", name.lower())` — so "Dr Watson" → `person:dr_watson` which looks right. But "Dr. Watson" (with period) → same result. The issue is like[...]

**Timeout on chunk 3** — 22 events + 2 moments is still a lot of prompt. The default Ollama timeout is 180s but `qwen2.5:14b` on a 15-sentence chunk with 22 events injected may just be slow. Two kno[...]

1. Reduce `--event-window` from 30 to 15 — cuts the event list significantly
2. Increase the httpx timeout

Let me fix both the alias debug visibility and the timeout:

> Viewed 3 files
>
> - Check the ollama_chat timeout and the alias generation
> - Find ollama_chat function and timeout
> - Find make_alias and build_alias_tables
> - **Done**

Three fixes needed:

1. **Timeout** — bump from 180s to 300s and use httpx's `(connect, read)` tuple form so only the read timeout is long
2. **`person:dr_watson` alias collision** — "Dr Watson" generates `person:dr_watson` but the prompt template hardcodes `person:dr_watson` as the example narrator, which the model copies literally ev[...]
3. **Event window default** — drop from 30 to 15

> Edited 3 files
>
> - Bump timeout, fix hardcoded narrator alias example, drop event window default
> - Bump httpx timeout
> - Remove hardcoded narrator alias example from prompt — model copies it literally
> - **Done**

Now add the `--dump-aliases` flag so you can inspect the actual alias table without running the full extraction:

> Edited 2 files, presented file
>
> - Add --dump-aliases flag to main
> - Add dump-aliases logic after alias tables are built
> - Presenting file(s)...
> - **Done**

Three changes:

**Timeout** — now `httpx.Timeout(10.0, read=360.0)`: 10s to connect, 6 minutes to read. The slow chunk was almost certainly a long generation, not a hung connection.

**Hardcoded narrator alias removed** — the prompt used to say `usually person:dr_watson` as an example, which the model was copying verbatim even when Watson's actual alias was `person:dr_watson_` o[...]

**Event window halved** — 15 instead of 30, which should roughly halve the events injected per chunk and reduce generation time.

Run this first to see exactly what aliases were generated:
```bash
pdm run python src/triplets.py \
  --sentences bohemia_sentences.jsonl \
  --entities  bohemia_entities.jsonl \
  --events    bohemia_events.jsonl \
  --moments   bohemia_moments.jsonl \
  --output    bohemia_triplets.jsonl \
  --dump-aliases 2>&1 | head -60
```

That'll show you the exact alias for Watson and confirm whether `person:dr_watson` is in the table or not.

> File: [/mnt/user-data/outputs/triplets.py](https://claude.ai/api/organizations/840440cd-f1be-4af9-8726-7ed95cf3825c/conversations/a3eaadb6-09f1-4fdc-b179-04dd0024b254/wiggle/download-file?path=%2Fmn[...]

## User:

> 6/9/2026 7:32:42

> File: 

## Assistant:

> 6/9/2026 7:32:53

The alias table looks correct. Watson is `person:dr_watson` — wait, he's not there at all. He's listed as `person:john` because the canonical in `bohemia_entities.jsonl` is "John" (the wiki linked h[...]

That's a data quality issue in the entity table, not a code bug. Two ways to fix it:

**Option A — patch the entity table**: edit `bohemia_entities.jsonl` to change the canonical for that record from "John" to "Dr Watson" (or "Dr. John H. Watson"), then rerun `triplets.py`. One-time [...]

**Option B — add alias synonyms**: for each entity, generate aliases from both the canonical name AND any aliases in the entity record that are more recognizable, so "Dr Watson", "Watson", "Dr. John[...]

Option A is the right call here — "John" is a poor canonical, it's genuinely a data quality problem from the clustering pass merging Watson's first name mention with his full name. The fix is in the[...]

While you're in there it's worth scanning for similar problems — `person:your_majesty` and `person:woman` are both going to cause alias mismatches since the model will try `person:the_king` or `pers[...]

Want me to implement Option B in `build_alias_tables`?

## User:

> 6/9/2026 7:35:20

uh oh

```
{"canonical": "Sherlock Holmes", "aliases": ["Holmes", "the detective", "Sherlock Holmes"], "type": "person", "wiki_url": "https://bakerstreet.fandom.com/wiki/Sherlock_Holmes", "entity_id": "wiki:Sher[...]
{"canonical": "Dr Watson", "aliases": ["Watson", "Doctor", "narrator", "the narrator", "I", "Dr Watson"], "type": "person", "wiki_url": "https://bakerstreet.fandom.com/wiki/John_Watson", "entity_id": [...]
{"canonical": "Wilhelm Gottsreich Sigismond von Ormstein", "aliases": ["King of Bohemia", "Count Von Kramm", "Bohemian nobleman", "King", "the King", "the King of Bohemia", "client", "my client", "the[...]
{"canonical": "Irene Adler", "aliases": ["Irene Norton", "Miss Irene", "Miss Irene Adler", "the woman", "lady", "the lady", "Madame", "the occupant of Briony Lodge", "Irene Adler"], "type": "person", [...]
{"canonical": "Godfrey Norton", "aliases": ["Mr. Godfrey Norton", "Norton", "gentleman", "husband", "her husband", "Godfrey Norton"], "type": "person", "wiki_url": "https://bakerstreet.fandom.com/wiki[...]
{"canonical": "Watson's wife", "aliases": ["Watson's wife"], "type": "person", "wiki_url": "https://bakerstreet.fandom.com/wiki/Mary_Watson", "entity_id": "wiki:Mary_Watson"}
{"canonical": "Mrs. Turner", "aliases": ["the landlady", "Mrs. Turner"], "type": "person", "wiki_url": null, "entity_id": "provisional:28"}
{"canonical": "Mary Jane", "aliases": ["maid", "the maid", "Mary Jane"], "type": "person", "wiki_url": null, "entity_id": "provisional:29"}
{"canonical": "John", "aliases": ["John"], "type": "person", "wiki_url": "https://bakerstreet.fandom.com/wiki/John_Watson", "entity_id": "wiki:John_Watson"}
```

Watson appears (at least) twice, second time as "John".

## Assistant:

> 6/9/2026 7:36:30

Right — the clustering pass produced two separate clusters that both linked to `John_Watson`. "Dr Watson" got clustered correctly with its aliases, and "John" (a bare first-name mention somewhere in[...]

The fix belongs in `merge.py` — after wiki linking, deduplicate on `entity_id`: if two clusters resolve to the same `entity_id`, merge them (union their aliases, keep the more specific canonical, ke[...]

The bug is that the wiki judgment pass creates a many-to-one situation (multiple clusters → same wiki URL) that nothing currently detects. Here's the fix — a `dedup_by_entity_id` function to add t[...]

> Viewed 3 files, edited file, ran 3 commands
>
> - Find where entity table is written in merge.py
> - Find where clusters are written to entities file
> - Add dedup_by_entity_id function and call it before writing entity table
> - Find exact text around wiki_count line
> - Patch merge.py to call dedup_by_entity_id
> - Check exact whitespace on those lines
> - Use sed to insert dedup call and add the function definition
> - **Done**

Good. Now add the function itself before `main()`:

> Ran 4 commands, presented file
>
> - Find insertion point before main()
> - Check what's at line 454
> - Insert dedup_by_entity_id function before main()
> - Verify merge.py parses cleanly
> - Presenting file(s)...
> - **Done**

Clean. What `dedup_by_entity_id` does:

- Groups all clusters by `entity_id` after the wiki linking pass
- If a group has more than one cluster (the collision case), merges them: unions all aliases, picks the canonical with the most words as the most specific name ("Dr Watson" beats "John", "Wilhelm Gott[...]
- Logs each merge to stderr so you can see what collapsed
- Provisional IDs are assigned sequentially and are unique by construction, so they never collide here

After rerunning `merge.py` you should see something like:
```
[dedup] merged 2 clusters -> wiki:John_Watson: ['Dr Watson', 'John']
```

And the entity table will have one Watson record with the full alias set including "John", "Watson", "Doctor", "narrator", "I", "Dr Watson" — all resolving to `wiki:John_Watson`. That also fixes the[...]

> File: [/mnt/user-data/outputs/merge.py](https://claude.ai/api/organizations/840440cd-f1be-4af9-8726-7ed95cf3825c/conversations/a3eaadb6-09f1-4fdc-b179-04dd0024b254/wiggle/download-file?path=%2Fmnt%2[...]

## User:

> 6/9/2026 8:08:16

```shell
➜  ner-20260608 git:(main) pdm run python src/merge.py \
  --coref bohemia_coref.jsonl \
  --entities bohemia_entities.jsonl \
  --mentions bohemia_mentions.jsonl && pdm run python src/events.py \
  --sentences bohemia_sentences.jsonl \
  --entities  bohemia_entities.jsonl \
  --events    bohemia_events.jsonl \
  --moments   bohemia_moments.jsonl && pdm run python src/triplets.py \
  --sentences bohemia_sentences.jsonl \
  --entities  bohemia_entities.jsonl \
  --events    bohemia_events.jsonl \
  --moments   bohemia_moments.jsonl \
  --output    bohemia_triplets.jsonl \
  --dump-aliases 2>&1 | head -60

=== Pass 1: Label clustering (Claude API) ===
Loaded 35 chunk records from bohemia_coref.jsonl
Found 118 unique entity labels
  Sending 118 labels to Claude for clustering ... 
  [warn] label not covered by Claude, adding singleton: 'Gross & Hankey’s'

  [warn] label not covered by Claude, adding singleton: 'St. John’s Wood'
-> 77 clusters
Produced 77 entity clusters

=== Pass 2: Wiki linking (opensearch + Claude judgment) ===
  Fetching wiki candidates ...
  Asking Claude to judge wiki candidates (1-10 of 52) ... -> 8/10 linked
  Asking Claude to judge wiki candidates (11-20 of 52) ... -> 2/10 linked
  Asking Claude to judge wiki candidates (21-30 of 52) ... -> 1/10 linked
  Asking Claude to judge wiki candidates (31-40 of 52) ... -> 1/10 linked
  Asking Claude to judge wiki candidates (41-50 of 52) ... -> 0/10 linked
  Asking Claude to judge wiki candidates (51-52 of 52) ... -> 0/2 linked
  Wilhelm Gottsreich Sigismond von Ormstein     -> wiki:Wilhelm_von_Ormstein
  Sherlock Holmes                               -> wiki:Sherlock_Holmes
  Dr Watson                                     -> wiki:John_Watson
  Irene Adler                                   -> wiki:Irene_Adler
  Godfrey Norton                                -> wiki:Godfrey_Norton
  Watson's wife                                 -> wiki:Mary_Watson
  Mrs. Turner                                   -> provisional:26
  Mary Jane                                     -> provisional:27
  Clotilde Lothman von Saxe-Meningen            -> wiki:Clotilde_Lothman_von_Saxe-Meiningen
  John                                          -> wiki:John_Watson
  Baker Street                                  -> provisional:28
  Briony Lodge                                  -> wiki:Briony_Lodge
  Church of St. Monica                          -> wiki:Church_of_St_Monica
  Serpentine Avenue                             -> provisional:29
  Serpentine-mews                               -> provisional:30
  St. John's Wood                               -> provisional:31
  Eglow, Eglonitz, Egria                        -> provisional:32
  Odessa                                        -> provisional:33
  Trincomalee                                   -> provisional:34
  the Inner Temple                              -> provisional:35
  drawing-room                                  -> provisional:36
  chambers                                      -> provisional:37
  steps                                         -> provisional:38
  House of Ormstein                             -> provisional:39
  Gross & Hankey's                              -> provisional:40
  Atkinson brothers                             -> provisional:41
  reigning family of Holland                    -> provisional:42
  medical profession                            -> provisional:43
  Hebrew rabbi                                  -> provisional:44
  German                                        -> wiki:Germany
  London slavey                                 -> provisional:45
  Doctor                                        -> wiki:John_Watson
  a groom                                       -> provisional:46
  burglars                                      -> provisional:47
  cabby                                         -> provisional:48
  clergyman                                     -> provisional:49
  loafers                                       -> provisional:50
  man                                           -> provisional:51
  ostlers                                       -> provisional:52
  servant maids                                 -> provisional:53
  spectators                                    -> provisional:54
  staff-commander                               -> provisional:55
  the coachman                                  -> provisional:56
  the crowd                                     -> provisional:57
  the elderly woman                             -> provisional:58
  the injured man                               -> provisional:59
  the man who wrote the note                    -> provisional:60
  the pair                                      -> provisional:61
  the paper maker                               -> provisional:62
  the young person                              -> provisional:63
  visitor                                       -> provisional:64
  the baby                                      -> provisional:65
  Wiki-linked: 12  Provisional: 65
  [dedup] merged 3 clusters -> wiki:John_Watson: ['Dr Watson', 'John', 'Doctor']
  After dedup: 75 entities
  Entity table written to bohemia_entities.jsonl

=== Pass 3: Mention rewriting ===
  Written 534 mention records to bohemia_mentions.jsonl

Done.
  Entities : bohemia_entities.jsonl
  Mentions : bohemia_mentions.jsonl
Loaded 689 sentences, 75 entities
Generated 23 chunks (size=30, overlap=5)
Resuming: 23 chunks already processed
  [  1/23] 1-30 already done, skipping
  [  2/23] 31-60 already done, skipping
  [  3/23] 61-90 already done, skipping
  [  4/23] 91-120 already done, skipping
  [  5/23] 121-150 already done, skipping
  [  6/23] 151-180 already done, skipping
  [  7/23] 181-210 already done, skipping
  [  8/23] 211-240 already done, skipping
  [  9/23] 241-270 already done, skipping
  [ 10/23] 271-300 already done, skipping
  [ 11/23] 301-330 already done, skipping
  [ 12/23] 331-360 already done, skipping
  [ 13/23] 361-390 already done, skipping
  [ 14/23] 391-420 already done, skipping
  [ 15/23] 421-450 already done, skipping
  [ 16/23] 451-480 already done, skipping
  [ 17/23] 481-510 already done, skipping
  [ 18/23] 511-540 already done, skipping
  [ 19/23] 541-570 already done, skipping
  [ 20/23] 571-600 already done, skipping
  [ 21/23] 601-630 already done, skipping
  [ 22/23] 631-660 already done, skipping
  [ 23/23] 661-689 already done, skipping

Done.
  Events  :    0  → bohemia_events.jsonl
  Moments :    0  → bohemia_moments.jsonl
Loaded: 689 sentences, 75 entities, 178 events, 36 moments
Alias index: 31 persons, 1 personas, 13 locations, 18 objects, 178 events, 36 moments

--- Person ---
  person:a_groom                                      a groom  [provisional:46]
  person:burglars                                     burglars  [provisional:47]
  person:cabby                                        cabby  [provisional:48]
  person:clotilde_lothman_von_saxe_meningen           Clotilde Lothman von Saxe-Meningen  [https://bakerstreet.fandom.com/wiki/Clotilde_Lothman_von_Saxe-Meiningen]
  person:dr_watson                                    Dr Watson  [https://bakerstreet.fandom.com/wiki/John_Watson]
  person:german                                       German  [https://bakerstreet.fandom.com/wiki/Germany]
  person:godfrey_norton                               Godfrey Norton  [https://bakerstreet.fandom.com/wiki/Godfrey_Norton]
  person:hebrew_rabbi                                 Hebrew rabbi  [provisional:44]
  person:irene_adler                                  Irene Adler  [https://bakerstreet.fandom.com/wiki/Irene_Adler]
  person:loafers                                      loafers  [provisional:50]
  person:london_slavey                                London slavey  [provisional:45]
  person:man                                          man  [provisional:51]
  person:mary_jane                                    Mary Jane  [provisional:27]
  person:mrs_turner                                   Mrs. Turner  [provisional:26]
  person:ostlers                                      ostlers  [provisional:52]
  person:servant_maids                                servant maids  [provisional:53]
  person:sherlock_holmes                              Sherlock Holmes  [https://bakerstreet.fandom.com/wiki/Sherlock_Holmes]
  person:spectators                                   spectators  [provisional:54]
  person:staff_commander                              staff-commander  [provisional:55]
  person:the_baby                                     the baby  [provisional:65]
  person:the_coachman                                 the coachman  [provisional:56]
  person:the_crowd                                    the crowd  [provisional:57]
  person:the_elderly_woman                            the elderly woman  [provisional:58]
  person:the_injured_man                              the injured man  [provisional:59]
  person:the_man_who_wrote_the_note                   the man who wrote the note  [provisional:60]
  person:the_pair                                     the pair  [provisional:61]
  person:the_paper_maker                              the paper maker  [provisional:62]
  person:the_young_person                             the young person  [provisional:63]
  person:visitor                                      visitor  [provisional:64]
  person:watson_s_wife                                Watson's wife  [https://bakerstreet.fandom.com/wiki/Mary_Watson]
  person:wilhelm_gottsreich_sigismond_von_ormstei     Wilhelm Gottsreich Sigismond von Ormstein  [https://bakerstreet.fandom.com/wiki/Wilhelm_von_Ormstein]

--- Persona ---
  persona:clergyman                                   clergyman  [provisional:49]

--- Location ---
  location:baker_street                               Baker Street  [provisional:28]
  location:briony_lodge                               Briony Lodge  [https://bakerstreet.fandom.com/wiki/Briony_Lodge]
  location:chambers                                   chambers  [provisional:37]
  location:church_of_st_monica                        Church of St. Monica  [https://bakerstreet.fandom.com/wiki/Church_of_St_Monica]
  location:drawing_room                               drawing-room  [provisional:36]
  location:eglow_eglonitz_egria                       Eglow, Eglonitz, Egria  [provisional:32]
  location:odessa                                     Odessa  [provisional:33]
  location:serpentine_avenue                          Serpentine Avenue  [provisional:29]
  location:serpentine_mews                            Serpentine-mews  [provisional:30]
  location:st_john_s_wood                             St. John's Wood  [provisional:31]
  location:steps                                      steps  [provisional:38]
  location:the_inner_temple                           the Inner Temple  [provisional:35]
  location:trincomalee                                Trincomalee  [provisional:34]

--- Object ---
  object:a_cabinet_photograph                         a cabinet photograph  [provisional:1]
  object:a_sheet_of_holmes_s_note_book                a sheet of Holmes's note-book  [provisional:3]
  object:cab                                          cab  [provisional:8]
  object:carriage                                     carriage  [provisional:7]
  object:chamois_leather_bag                          chamois leather bag  [provisional:4]
```

## Assistant:

> 6/9/2026 8:08:29

Good progress — the dedup worked perfectly:

```
[dedup] merged 3 clusters -> wiki:John_Watson: ['Dr Watson', 'John', 'Doctor']
```

`person:dr_watson` is now in the alias table where it belongs. A few other things worth noting from this output:

**`person:german` linked to `wiki:Germany`** — that's a spurious wiki link. "German" in context means "a German person" or an adjective, not the country. Claude over-linked that one. Worth a manual [...]

**`events.py` skipped entirely** — the sidecar progress file from the previous run marked all 23 chunks as done, so it found 0 new events. Since you re-ran `merge.py` and the entity table changed, t[...]

**`triplets.py` similarly** — it's still sitting on the old progress file, so when you run the full extraction it'll also need its sidecar cleared.

The cleanup sequence before the next full run:
```bash
rm -f bohemia_events.jsonl bohemia_moments.jsonl .bohemia_events_progress.json
rm -f bohemia_triplets.jsonl .bohemia_triplets_progress.json
```

Then fix the `german` entity, and rerun `events.py` followed by `triplets.py`. Want me to also implement the alias synonym registration (Option B) in `build_alias_tables` while we're here, so all the [...]


```