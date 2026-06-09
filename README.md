# NER-20270608

**Link:** [https://claude.ai/chat/a3eaadb6-09f1-4fdc-b179-04dd0024b254](https://claude.ai/chat/a3eaadb6-09f1-4fdc-b179-04dd0024b254)  

## User:

We've talked a bit about how the g533 Tower that I purchased is potentially a good fit for graphics traction but is not so good for querying and synthesizing responses. You would still agree with that assessment, correct?

## Assistant:

Yes, that assessment holds. The nuance from our conversation was actually more specific than "graphics traction" — the G533's RX 9060 XT with 16GB VRAM is well-suited for **batch inference workloads like the Graphwright ingestion pipeline** (NER, relationship extraction), where you're repeatedly feeding structured prompts through a model and don't need low latency.

Where it's weaker is **interactive query synthesis** — the orchestrator/synthesizer pattern where you want fast, high-quality multi-hop reasoning over graph results. Two compounding issues:

1. **ROCm vs CUDA** — AMD's ROCm stack still has more friction and occasional performance gaps vs CUDA for inference workloads in Ollama, even with improvements.
2. **Model quality ceiling** — The models that fit well in 16GB (qwen2.5:14b, qwen2.5-coder:14b) are good but not frontier-class. For complex synthesis over BFS results, you noticed during our gwchat work that smaller models struggle with the reasoning depth needed.

The conclusion we landed on was: use the G533 for ingestion (where throughput matters more than latency and quality), and keep frontier cloud models (Claude, GPT-4-class) for query synthesis where reasoning quality is the bottleneck. That still seems like the right division of labor.

Has something changed that's prompting the question, or are you revisiting the architecture?

## User:

So let's think about a pipeline design starting with the NER phase where the first thing we do is we take the sentences in a scandal in Bohemia and we number them and put one on each line so that we now have easy reference to the sentence, where it is in the story. And we can use that as a reference for the locations of the things that are mentioned in the story.

## Assistant:

Good starting point. A few design questions before we sketch the pipeline:

The sentences need to be numbered in a way that survives the whole pipeline — so the numbering scheme matters. A few options:

**Flat sequential** — sentence 1 through N across the whole story. Simple, unambiguous, easy to reference in NER output (`"sentence": 47`).

**Hierarchical** — paragraph.sentence, e.g. `3.2` for paragraph 3, sentence 2. More human-navigable when debugging, but slightly more complex as a reference key.

For NER purposes, flat sequential is almost certainly the right call — you just want a stable integer key that the model can emit alongside each entity mention.

**Practical questions:**

- What's your source text? Plain UTF-8 from Project Gutenberg? That'll have some cleanup needed (chapter headers, the story title line, "THE END" etc.) that you probably want stripped before numbering.
- Are you using spaCy's sentencizer, NLTK's punkt, or something else for sentence splitting? Conan Doyle's sentences are mostly well-behaved but there are some edge cases (dialogue with attribution, abbreviated titles like "Dr.", "Mr.", "Mrs.") that will trip up naive splitters.
- Do you want the numbered output as a plain text file (one sentence per line, `001\t<sentence>`), a JSON array, or something else? The format affects how you feed it into the NER phase.

My instinct: plain TSV or JSONL, flat sequential, spaCy for splitting with the `en_core_web_sm` sentencizer and a custom abbreviation list for the Doyle-specific cases. What do you have in mind?

## User:

I would think Jason l is the way to go, and I would want to use the LLM itself as a splitter rather than spacey.

## Assistant:

Makes sense on both counts. JSONL is the right choice — each line is self-contained, easy to stream, easy to append to or slice. And using the LLM as the splitter sidesteps the abbreviation problem entirely; it understands that "Dr. Watson" isn't a sentence boundary.

The practical question is chunking. You can't feed the whole story to the LLM in one shot and ask for numbered sentences — the output would be enormous and you'd have no reliable way to stitch chunks together with consistent numbering. So the approach would be:

1. **Split the raw text into paragraphs** (on double newlines) — this is trivial and robust.
2. **Feed paragraphs to the LLM in batches**, asking it to return JSONL where each object is `{"id": <int>, "text": "<sentence>"}`, with a **carry-in offset** so numbering is continuous across chunks.
3. **Concatenate the JSONL output** — since each line is independent and IDs are globally unique, this is just a cat.

Each JSONL record would look like:
```json
{"id": 1, "para": 1, "text": "To Sherlock Holmes she is always the woman."}
```

Including `para` is cheap and useful — it lets you reconstruct reading order and gives NER a coarser locality signal alongside the sentence ID.

The prompt to the LLM for each batch would be something like:

> Split the following text into individual sentences. Return one JSON object per line with keys `id` (integer, starting at {offset}), `para` (paragraph number, starting at {para_offset}), and `text` (the sentence, verbatim). Do not merge or alter sentences. Do not output anything else.

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

The one thing I'd watch for: `qwen2.5:14b` is generally well-behaved about following JSON-only instructions, but you may get occasional off-by-one errors in the `id` field (model resets to 1 mid-batch). The resume logic handles that for cross-batch continuity, but you might want a post-processing pass to re-sequence IDs from the final JSONL if strict monotonicity matters downstream.

> File: [/mnt/user-data/outputs/sentencize.py](https://claude.ai/api/organizations/840440cd-f1be-4af9-8726-7ed95cf3825c/conversations/a3eaadb6-09f1-4fdc-b179-04dd0024b254/wiggle/download-file?path=%2Fmnt%2Fuser-data%2Foutputs%2Fsentencize.py)

## User:

The next step in the process will be to take a collection of sentences, a chunk, and identify the nouns and pronouns and proper nouns in that chunk and group them together by which ones are likely to refer to the same person or object or place.

## Assistant:

That's the coreference resolution step — arguably the hardest part of the NER pipeline for literary text, because Doyle relies heavily on pronouns and circumlocutions ("the King", "the lady", "my client") that require world-knowledge to resolve.

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

**Chunk boundaries** — coref chains can span chunk boundaries. "She" in sentence 21 may resolve to "Irene Adler" introduced in sentence 8. You need a **carry-over context**: include the last N sentences of the previous chunk as read-only context, clearly marked so the model doesn't re-emit them as new mentions.

**Cross-chunk identity merging** — entity labels won't be perfectly consistent across chunks ("Irene Adler" vs "the lady" as the canonical label for the same entity). You'll need a merge pass afterward, probably another LLM call, that reconciles chunk-level entity labels into a global entity table.

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

**Context leak guard** — the parser explicitly filters out any mention whose `sentence_id` falls outside the current chunk. Models occasionally pull a sentence ID from the context block despite being told not to; this catches it silently.

**`type` field** — person/place/object/organization/other. Useful later when you're building the entity table and want to separate, say, "Baker Street" from "Holmes".

**What this doesn't yet solve** — the cross-chunk merge problem. After this runs you'll have `bohemia_coref.jsonl` with one record per chunk, and "Irene Adler" in chunk 1 and "the lady" in chunk 3 are not yet known to be the same entity. That's the next step: a merge/reconciliation pass that builds a global entity table with canonical labels and aggregated mention lists across the whole story.

Want to tackle that merge pass next, or run the first two steps against the actual text first to see what the output looks like?

> File: [/mnt/user-data/outputs/coref.py](https://claude.ai/api/organizations/840440cd-f1be-4af9-8726-7ed95cf3825c/conversations/a3eaadb6-09f1-4fdc-b179-04dd0024b254/wiggle/download-file?path=%2Fmnt%2Fuser-data%2Foutputs%2Fcoref.py)

## User:

Let's look at the merge pass, and for an authoritative ontology let's plan to use the Baker Street wiki.

## Assistant:

Good call. Using the Baker Street Wiki as canonical authority means your entity labels won't drift across chunks — "Irene Adler" and "the Prima Donna" and "the woman" all resolve to the same wiki page, and that page slug or URL becomes the stable identifier in your entity table.

The merge pass has two distinct sub-problems:

**1. Intra-story clustering** — grouping chunk-level labels that clearly refer to the same entity ("the King", "His Majesty", "Wilhelm Gottsreich Sigismond von Ormstein") into a single candidate entity before going anywhere near the wiki.

**2. Wiki linking** — taking each candidate entity and finding its Baker Street Wiki page, which gives you the canonical label, the URL as a stable ID, and potentially structured data (character type, appearances, relationships).

For the wiki side, the question is what's available. Baker Street Wiki is a Fandom wiki, so it has:
- A search endpoint: `https://bakerstreet.fandom.com/api.php?action=opensearch&search=<query>`
- Full page content via the MediaWiki API if you need it for disambiguation

The merge architecture I'd propose:

**Pass 1 — label clustering (LLM)**: Feed all unique entity labels from across all chunks to the LLM in one call (or batched if large), ask it to group labels that refer to the same entity and nominate a canonical name for each group.

**Pass 2 — wiki lookup (API)**: For each canonical name, query the Baker Street Wiki search API, take the top result, and store the URL as the entity's stable ID. Flag low-confidence matches for human review.

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

One wrinkle worth thinking about before coding: some entities in the story won't have Baker Street Wiki pages — incidental characters, generic locations. You'll want a `wiki_url: null` fallback rather than a failed lookup blocking the pipeline.

Want me to go ahead and code all three passes, or would you rather see the wiki API behavior first with a quick exploratory fetch?

## User:

For those unfindable incidental characters and things, use an ID like "provisional:<number>".

## Assistant:

Good. That keeps every entity in the table addressable with a stable ID regardless of wiki coverage, and the `provisional:` prefix makes it obvious during downstream processing which ones lack authoritative grounding. You could also later do a second-pass wiki attempt on provisionals if the canonical name improves after merging.

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

**Two-attempt wiki lookup** — first tries the canonical name, then falls back to the longest alias (which is often the most specific proper name). "the woman" won't find a wiki page; "Irene Adler" will.

**`entity_id` scheme** — `wiki:Irene_Adler` for linked entities, `provisional:1` for everything else. The wiki slug is derived directly from the URL so it's stable across runs.

**`--skip-wiki` flag** — useful for fast iteration during development; assigns provisional IDs to everything so you can inspect the clustering output before burning wiki API calls.

**Flat mention output** — `bohemia_mentions.jsonl` has one record per mention with `entity_id`, `canonical`, `wiki_url`, `sentence_id`, `span`, and `confidence`. That's your primary query surface downstream — you can join it back to the sentence JSONL on `sentence_id` to reconstruct any mention in context.

At this point the full pipeline is `sentencize.py → coref.py → merge.py`, producing three artifacts: sentences, entities, and mentions. The natural next step would be a simple query tool that takes an entity name and returns all its mentions with surrounding sentence context. Want to add that, or is there another direction you want to go first?

> File: [/mnt/user-data/outputs/merge.py](https://claude.ai/api/organizations/840440cd-f1be-4af9-8726-7ed95cf3825c/conversations/a3eaadb6-09f1-4fdc-b179-04dd0024b254/wiggle/download-file?path=%2Fmnt%2Fuser-data%2Foutputs%2Fmerge.py)

