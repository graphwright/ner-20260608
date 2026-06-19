# Chapter 2: The Holmes Corpus

*A Scandal in Bohemia* is eight thousand words. It is self-contained, well-known, and structurally demanding. The plot turns on identity concealment, epistemic asymmetry, and one person's ability to out-think another — which means a schema that can faithfully represent the story must handle belief, deception, temporal knowledge, and contested facts. That is exactly the kind of stress test a typed knowledge graph needs.

This chapter walks through the complete Holmes pipeline: schema design, the five ingestion stages, and the in-memory graph that results. Every design choice is motivated by something the domain forced. The surprises are as instructive as the clean parts.

---

## 2.1 Why Holmes?

Three properties make *A Scandal in Bohemia* an unusual choice for a knowledge graph worked example, and all three are features rather than accidents.

**Epistemic richness.** The story is not just about what happened; it is about who knew what and when. Watson narrates. Holmes deduces. The King conceals his identity. Irene Adler deceives Holmes about where she keeps the photograph. Godfrey Norton appears and disappears in a way that Watson witnesses but does not fully understand. A graph that only records facts — *Irene lives at Briony Lodge, Holmes visited it on 21 March* — misses most of what is interesting. The schema needs to represent belief states, epistemic moments ("Watson came to know that the King was in disguise at this moment"), and the temporal dimension of knowledge.

**Literary circumlocution.** Doyle almost never calls his characters by the same name twice in a row. The King is variously "my royal client", "His Majesty", "Count Von Kramm", "a large man with a broad florid face and a strong assertive chin", and his full name, Wilhelm Gottsreich Sigismond von Ormstein. A pipeline that has to resolve all of these to a single entity under pressure from an 8,000-word corpus, with only a fan wiki as the authority, is a genuine stress test for entity resolution.

**A thin external ontology.** Baker Street Wiki is a well-maintained fan wiki with good coverage of the major characters and reasonable coverage of the canonical locations. But it is not a curated ontology in the way MeSH or UMLS are. Many incidental characters — the groom Holmes bribes, the cab driver, the witnesses at the wedding — have no wiki page at all. The pipeline must handle these gracefully, falling back to provisional IDs without failing.

These three properties together produce a domain where you cannot rely on the ontology to do the hard work, where local models are not strong enough for the reasoning-intensive passes, and where the schema needs to model epistemic states — not just physical facts. That combination forces every interesting design decision that makes the architecture general.

---

## 2.2 The Holmes Schema

The schema is in `src/ner_20260608/holmes_schema.py`. It is the executable specification: every type and constraint is enforced by Pydantic at construction time and by the Python type system statically. Designing the schema was an inductive process — it was built by annotating the story, not pre-designed from first principles.

### Entity types

Eight entity types cover the Holmes domain:

| Type | Purpose |
|------|---------|
| `Person` | A real individual: Holmes, Watson, Irene Adler, the King |
| `Persona` | A role a person plays: Count Von Kramm, the Nonconformist Clergyman |
| `Location` | A place: 221B Baker Street, Briony Lodge, London |
| `Object` | A physical thing: the cabinet photograph |
| `Document` | A written artifact with story context: the King's advance note, Irene's farewell letter |
| `Event` | A discrete occurrence: the fake fire alarm, the wedding at St. Monica's |
| `Moment` | A temporal anchor for events and epistemic changes |
| `Plan` | A course of action (provisional — the `Executes` predicate is not yet in the pipeline) |

`Persona` is the first schema decision that the domain forced. Doyle uses disguise as a plot device so heavily that it demanded its own type. A `Persona` is not a `Person` — it is a role played by a person. Holmes disguised as a Nonconformist Clergyman and the King disguised as Count Von Kramm are both `Persona` instances. The `DisguisedAs` and `HasTrueIdentity` predicates link personas to their underlying persons. Without this distinction, the graph would either merge the King and Count Von Kramm into the same node (wrong) or treat them as separate people with no connection (also wrong).

`Moment` has an important epistemic variant. A `Moment` without a `narrator` field is an objective time anchor: "Evening of 20 March 1888." A `Moment` with a `narrator` is epistemic: it records the moment *from a specific character's perspective*, i.e. the moment a person came to know something. Watson's moment of learning that the King was in disguise is a different thing from the King's actual moment of removing his mask, even though they happened at the same calendar time.

```python
moment_watson_sees_king_unmasked = Moment(
    id="sib:moment:watson_sees_king_unmasked",
    story_id=STORY,
    label="Watson witnesses the King remove his mask",
    narrator=watson,   # epistemic: this is Watson's moment of discovery
)

moment_kings_visit = Moment(
    id="sib:moment:kings_visit_evening",
    story_id=STORY,
    label="Evening of 20 March 1888 — King visits Baker Street",
    # no narrator: objective timeline anchor
)
```

### Predicate types and their traits

```python
class Knows(BaseStatement, ProvenanceMixin, EpistemicMixin, Symmetric):
    subject: Person
    object_: Person

class LocatedIn(BaseStatement, ProvenanceMixin, Transitive):
    subject: Location
    object_: Location

class DisguisedAs(BaseStatement, ProvenanceMixin, EpistemicMixin,
                   Inverse['HasTrueIdentity']):
    subject: Person
    object_: Persona

class HasTrueIdentity(BaseStatement, ProvenanceMixin, EpistemicMixin,
                      Functional, Inverse[DisguisedAs]):
    subject: Persona
    object_: Person

class KnewAt(BaseStatement, ProvenanceMixin, EpistemicMixin):
    subject: Person
    object_: BaseStatement   # ← higher-order: range is any predicate instance
    moment: Moment

class Contradicts(BaseStatement, ProvenanceMixin, Symmetric):
    subject: BaseStatement   # ← both subject and object_ are predicate instances
    object_: BaseStatement
```

The traits — `Symmetric`, `Transitive`, `Functional`, `Inverse` — are Python mixin classes inherited alongside `BaseStatement`. They are introspectable at runtime: `issubclass(LocatedIn, Transitive)` is `True`. The `Inverse` trait is generic: `Inverse[DisguisedAs]` on `HasTrueIdentity` records the partner predicate as a type-level annotation, making the inverse relationship recoverable without any external lookup table.

`Functional` on `HasTrueIdentity` is a schema claim: each persona has exactly one true identity. No persona is two people. The schema asserts this; enforcement requires a separate validation pass (not yet implemented).

`KnewAt` is the schema's most important design decision. Its `object_` field is annotated as `BaseStatement` — any predicate instance at all. This enables sentences like "Watson came to know that the King was disguised as Count Von Kramm at the moment the mask was removed." The target of `KnewAt` is not a new Statement node; it is the existing `DisguisedAs` instance — a full member of $V$ that `KnewAt` simply points at:

```python
e_watson_knew_king_disguised = KnewAt(
    id=_sid(watson, KnewAt, e_king_as_count),
    subject=watson,
    object_=e_king_as_count,     # this IS e_king_as_count — no wrapper needed
    moment=moment_watson_sees_king_unmasked,
    **_p(57, watson),
)
```

This is $E \subseteq V$ in practice: `e_king_as_count` is a `DisguisedAs` instance and simultaneously a member of $V$ that any predicate whose range includes `BaseStatement` can reference directly.

### Provenance and epistemic mixins

Every predicate type in the Holmes schema inherits `ProvenanceMixin`:

```python
class ProvenanceMixin(BaseModel):
    story_id: str
    paragraph_index: int
    asserting_narrator: Person | None = None
    extraction_method: str
    extraction_confidence: float = Field(ge=0.0, le=1.0)
```

`paragraph_index` ties every claim to its location in the source text. `asserting_narrator` is typically Watson, occasionally `None` for events narrated in the omniscient voice. `extraction_confidence` is `1.0` for manually annotated instances and a model-emitted float for pipeline-extracted ones. `extraction_method` distinguishes `"manual"`, `"llm-triplet-extraction"`, and `"inferred"` (for future rule-derived instances).

`EpistemicMixin` adds a single field, `narrator_confidence`, for predicates where Watson's epistemic certainty is worth recording separately from extraction confidence:

```python
e_watson_knows_of_irene = Knows(
    id=_sid(watson, Knows, irene_adler),
    subject=watson, object_=irene_adler,
    **{**_p(63, watson), "extraction_confidence": 0.7},
)
```

Watson expresses uncertainty about whether he knows Irene Adler in the sense of genuine acquaintance. The `0.7` here is the extractor's judgment that this Knows edge is real but hedged.

### Identity: canonical IDs and `__str__`

Every entity in the schema has an `id` — a string assigned at construction that is never derived from any other field and never parsed back. For persons and locations with Baker Street Wiki pages, the id is the full wiki URL:

```python
holmes = Person(
    id="https://bakerstreet.fandom.com/wiki/Sherlock_Holmes",
    display_name="Sherlock Holmes",
)
```

For corpus-local entities with no external authority, it is a `sib:` namespaced slug:

```python
evt_fake_fire_alarm = Event(
    id="sib:event:fake_fire_alarm",
    story_id=STORY,
    description="Holmes, disguised as a clergyman, stages a fake fire alarm ...",
)
```

The slug contains no type segment (not `sib:event:fake_fire_alarm`, which was an earlier convention; the schema guide now recommends `sib:fake_fire_alarm` — the event type is in the Python class, not the id string). The existing corpus IDs predate this recommendation and are grandfathered.

Display is entirely separate. `__str__` returns `display_name` for entities that have one, `description` or `label` for synthetic entities, and `ClassName(subject → object)` for predicate instances:

```python
>>> str(holmes)
'Sherlock Holmes'
>>> repr(holmes)
"Person('https://bakerstreet.fandom.com/wiki/Sherlock_Holmes')"
>>> str(e_king_as_count)
'DisguisedAs(Wilhelm Gottsreich Sigismond von Ormstein → Count Von Kramm)'
```

No code anywhere in the system parses an `id` string to determine a type. That is the type system's job.

### Predicate IDs: content-addressed with `statement_id()`

For the manually annotated `scandal_instances.py` corpus, predicate IDs are content-addressed:

```python
def statement_id(subject_id: str, predicate_name: str, object_id: str) -> str:
    return f"stmt:{subject_id}:{predicate_name}:{object_id}"
```

This means re-constructing the same fact from a different passage yields the same ID — re-extraction is confirmation, not a duplicate. The embedded predicate name is for debugging, not dispatch; nothing parses it back. For the pipeline-extracted JSONL, each triplet gets a unique ID per extraction event, preserving the full audit trail.

---

## 2.3 The Ingestion Pipeline

The pipeline has five stages:

```
bohemia.txt
    ↓ sentencize.py
bohemia_sentences.jsonl      {"id": 42, "para": 11, "text": "..."}
    ↓ coref.py
bohemia_coref.jsonl          chunk-level entity/mention clusters
    ↓ merge.py
bohemia_entities.jsonl       global entity table with canonical IDs
bohemia_mentions.jsonl       flat mention index
    ↓ events.py
bohemia_events.jsonl         discrete events with participant links
bohemia_moments.jsonl        temporal anchors
    ↓ triplets.py
bohemia_triplets.jsonl       predicate instances (all truth_status=hypothetical)
```

Each stage is a stateless transform over JSONL. Intermediate files can be inspected, re-run independently, or fed into other tools. All five stages support resume: they read the existing output on startup and skip already-processed records.

### sentencize.py — raw text to numbered sentences

The first decision is the splitter. Standard options (spaCy's sentencizer, NLTK's punkt) trip on Doyle's abbreviations: "Dr.", "Mr.", "Mrs." all produce false sentence boundaries. The LLM splitter sidesteps this entirely — it understands that "Dr. Watson" is not a sentence boundary.

The approach: split the raw text on double newlines to get paragraphs (robust, requires no LLM), then send each paragraph to a local model (`qwen2.5:14b` via Ollama) with a carry-in offset for continuous numbering. `temperature=0.0` ensures deterministic output.

```json
{"id": 1,   "para": 1, "text": "To Sherlock Holmes she is always the woman."}
{"id": 2,   "para": 1, "text": "I have seldom heard him mention her under any other name."}
{"id": 42,  "para": 11, "text": "His Majesty had hardly spoken before Holmes had sprung from his chair and advanced towards him."}
```

The `para` field is cheap and valuable: it provides a coarser locality signal alongside `id` for downstream stages.

*A Scandal in Bohemia* produces 689 sentences across 218 paragraphs.

### coref.py — entity mention clusters

The coreference pass identifies which nouns and pronouns refer to the same entity. For a story that calls the King "my royal client", "His Majesty", and "this extraordinary-looking man" in successive paragraphs, this is genuinely difficult.

The pass runs locally on `qwen2.5:14b`. Each call covers a window of 20 sentences with 3 sentences of carry-in context from the previous chunk (to catch references like "she" in sentence 21 resolving to "Irene Adler" introduced in sentence 8). The model returns:

```json
{
  "chunk_id": "1-20",
  "entities": [
    {
      "label": "Irene Adler",
      "type": "person",
      "mentions": [
        {"sentence_id": 1, "span": "the woman", "confidence": 0.95},
        {"sentence_id": 7, "span": "she", "confidence": 0.85}
      ]
    }
  ]
}
```

A context leak guard filters any mention whose `sentence_id` falls outside the current chunk — models occasionally pull IDs from the context block despite instruction.

The coref pass runs entirely locally. This is the volume pass: 46 chunks for a single story, each requiring one LLM call. At scale (hundreds of papers), local throughput matters more than reasoning quality here — the merge pass will correct clustering errors; the coref pass just needs to produce reasonable candidates.

### merge.py — global entity table

The coref pass produces per-chunk entity labels. The merge pass resolves them into a global entity table with canonical IDs. It has three sub-passes.

**Pass 1 — label clustering via Claude API.** All unique entity labels across all chunks are sent to Claude in a single call. Claude has strong world-knowledge of Holmes canon and can correctly merge "His Majesty", "the King", "Count Von Kramm", "my client", and "Wilhelm Gottsreich Sigismond von Ormstein" into one entity. A 14B local model cannot reliably do this — it lacks the narrative world-knowledge and produces inconsistent merges at chunk boundaries.

The output is a set of clusters, each with a canonical label and an alias list:

```json
{
  "canonical": "Wilhelm Gottsreich Sigismond von Ormstein",
  "aliases": ["King of Bohemia", "Count Von Kramm", "the King", "my client",
              "His Majesty", "the extraordinary-looking man"],
  "type": "person"
}
```

**Pass 2 — Baker Street Wiki lookup with Claude judgment.** For each canonical entity, the merge pass queries the Baker Street Wiki opensearch endpoint. This returns candidate URLs by string similarity — which is sufficient for "Irene Adler" but unreliable for "the woman" (which might match an unrelated article) or "the groom" (which has no relevant article at all).

Instead of accepting the opensearch result blindly, the pass sends the top candidates to Claude and asks a binary judgment: is this the correct Baker Street Wiki article for this entity, given the story context? Claude returns `true` or `null`, eliminating spurious links. Entities that receive `null` get a `provisional:N` ID.

```json
{"canonical": "Irene Adler",
 "wiki_url": "https://bakerstreet.fandom.com/wiki/Irene_Adler",
 "entity_id": "wiki:Irene_Adler",
 "aliases": ["the woman", "the lady", "Irene Norton", ...]}

{"canonical": "the groom",
 "wiki_url": null,
 "entity_id": "provisional:14",
 "aliases": ["the ostler", "the groom"]}
```

**Post-merge deduplication.** Multiple clusters sometimes link to the same wiki page — the most common case in practice is a character whose first name and full name appear as separate coref clusters (Watson appeared as both "Dr Watson" and "John"). The dedup pass groups by `entity_id` and merges: union the alias lists, pick the longer canonical name, emit one record.

**Pass 3 — mention rewriting.** Walk back through `bohemia_coref.jsonl` and rewrite every mention's entity label to the canonical form, adding `entity_id` and `wiki_url`. The output `bohemia_mentions.jsonl` has one record per mention and is the primary query surface for downstream stages.

### events.py — events and moments

Events and moments are corpus-local constructs that the coref pipeline does not produce — they are not noun phrases referring to named entities. They must be extracted separately.

This pass uses Claude. Identifying discrete events ("Holmes stages the fake fire alarm at Briony Lodge") rather than states ("Irene Adler lives at Briony Lodge"), and extracting temporal anchors tied to narrative moments, requires narrative reasoning that `qwen2.5:14b` does not reliably provide. It confuses states and events, misses implicit temporal markers, and produces inconsistent IDs across chunks.

Each Claude call covers a window of sentences with the known entity index injected into the prompt. The model returns events and moments with participant links using the entity IDs from `bohemia_entities.jsonl`:

```json
{"id": "sib:event:fake_fire_alarm",
 "description": "Holmes, disguised as a clergyman, stages a fake fire alarm at Briony Lodge.",
 "sentence_ids": [196, 197, 198],
 "para": 65,
 "participants": ["https://bakerstreet.fandom.com/wiki/Sherlock_Holmes",
                  "https://bakerstreet.fandom.com/wiki/Irene_Adler"],
 "extraction_confidence": 0.97}

{"id": "sib:moment:fake_fire_evening",
 "label": "Evening of 21 March 1888 — fake fire alarm at Briony Lodge",
 "event_id": "sib:event:fake_fire_alarm",
 "narrator_id": null,
 "sentence_ids": [196],
 "extraction_confidence": 0.92}
```

A `SlugRegistry` enforces global uniqueness of `sib:event:` and `sib:moment:` slugs within a run. A progress sidecar file (`.bohemia_events_progress.json`) records completed chunk IDs for resume.

*A Scandal in Bohemia* produces 178 events and 36 moments.

### triplets.py — predicate instances

The triplet pass is the slot-filling stage. Given the full entity/event/moment index and a chunk of sentences, the model identifies predicate instances: subject, predicate type, object, and provenance fields.

This pass runs locally. The reason is the schema: by the time the triplet pass runs, the entity index is complete and the predicate vocabulary is fixed. The model is not doing open NER or creative reasoning — it is filling slots from a constrained set of known IDs and known predicate names. `qwen2.5:14b` handles this reliably.

**The alias scheme.** Injecting full Baker Street Wiki URLs into the prompt (`https://bakerstreet.fandom.com/wiki/Sherlock_Holmes`) produces poor results — the model finds the URLs unwieldy and substitutes its own compact scheme. Instead, the prompt uses short aliases:

```
person:sherlock_holmes  →  Sherlock Holmes
person:dr_watson        →  Dr. John H. Watson
location:briony_lodge   →  Briony Lodge, Serpentine Avenue
```

The alias table is built from the entity JSONL and includes all known aliases from the clustering pass, not just the canonical name. A validator expands aliases back to canonical IDs and enforces domain/range constraints. Model output referencing an unknown alias is warned and dropped; model output with a valid alias but a type mismatch (`Knows(Location → Person)`) is also dropped.

**Event window filtering.** Only events and moments whose `sentence_ids` fall within ±15 sentences of the current chunk are injected into the prompt. Injecting all 178 events produces slow generation and worse output — the model drowns in irrelevant context.

**Output convention.** Every predicate instance in the pipeline output has `truth_status: "hypothetical"`. This is a schema-level convention: the pipeline reports what it found, not its epistemic commitment. A separate promotion pass (not yet implemented in the automated pipeline; done manually in `scandal_instances.py`) upgrades claims to `asserted_true` based on evidence weight.

```json
{"id": "trip:042",
 "predicate": "AssociatedWith",
 "subject_id": "wiki:Irene_Adler",
 "object_id": "wiki:Briony_Lodge",
 "truth_status": "hypothetical",
 "story_id": "scandal_in_bohemia",
 "paragraph_index": 118,
 "asserting_narrator_id": "wiki:John_Watson",
 "extraction_method": "llm-triplet-extraction",
 "extraction_confidence": 0.93,
 "sentence_ids": [118, 119]}
```

---

## 2.4 Loading: The Fixpoint Problem

The five pipeline stages produce JSONL. The loader in `src/ner_20260608/loader.py` hydrates these records into live Pydantic instances. Most hydration is straightforward: read the record, look up subject and object IDs in the `InstanceSet`, construct the predicate instance.

Higher-order predicates break this pattern. `KnewAt` takes a `BaseStatement` in its `object_` field — meaning it can only be constructed after the target predicate instance has been built. In file order, a `KnewAt` record pointing at a `Knows` record may appear before the `Knows` record. Worse, a `Contradicts` may point at a `KnewAt` that points at a `Knows`, requiring three passes to resolve.

The loader handles this with a fixpoint loop. On the first pass, all first-order predicates are hydrated immediately. Higher-order predicates (those whose `subject` or `object_` type annotation is `BaseStatement` or a subclass) are deferred. The deferred list is then retried in a loop until it stops shrinking:

```python
while deferred:
    remaining = []
    for rec in deferred:
        subject_id = rec.get("subject_id")
        object_id  = rec.get("object_id")
        if (subject_id and iset.get(subject_id) is None) or \
           (object_id  and iset.get(object_id)  is None):
            remaining.append(rec)
            continue
        _hydrate_one_triplet(rec, pred_cls, iset)
    if len(remaining) == len(deferred):   # no progress this iteration
        for rec in remaining:
            iset.warnings.append(
                f"higher-order triplet {rec['id']!r}: referent(s) unresolvable — skipping"
            )
        break
    deferred = remaining
```

The termination condition is key: the loop exits when the *deferred set* stops shrinking, not when the global instance set stops growing. The distinction matters when some higher-order triplets have genuinely unresolvable referents (a missing entity ID in the JSONL). Keying on global growth would re-attempt those records on every pass until other chains exhausted — an O(n²) waste. Keying on deferred-set shrinkage terminates in one extra iteration after the last resolvable record is processed.

The loader also handles NER type routing with explicit failure semantics. `organization` is not mapped to `Location` (a semantic error from an earlier version). Unmapped NER types produce a warning and skip, rather than silently coercing to `Object` — a coercion that would produce downstream domain/range violations.

---

## 2.5 The In-Memory Graph

`graph.py` provides the in-memory index. Construction is O(n) over the instance set; all subsequent operations are O(degree) or better.

```python
from ner_20260608 import load_bohemia_graph

g = load_bohemia_graph()          # ~100ms; loads bundled JSONL from the wheel

holmes = g.get("wiki:Sherlock_Holmes")
print(holmes)                     # Sherlock Holmes
print(repr(holmes))               # Person('wiki:Sherlock_Holmes')
```

Both the `wiki:` slug form and the full Baker Street Wiki URL are valid lookup keys — `_canonicalize_id` normalizes full URLs to slug form internally.

### Traversal: edges_from and edges_to

```python
from ner_20260608.holmes_schema import Possesses, Involves

# What does Irene Adler possess, per asserted facts?
edges = g.edges_from("wiki:Irene_Adler",
                     pred_type=Possesses,
                     truth="asserted_true")
for e in edges:
    print(f"  {e.object_.display_name}")   # Cabinet photograph of Irene Adler and the King

# What events involve Irene Adler?
events = g.edges_to("wiki:Irene_Adler", pred_type=Involves)
for e in events:
    print(f"  {e.subject}")   # the Event's __str__ returns its description
```

`edges_from` and `edges_to` accept a `truth` parameter that can be a string value (`"asserted_true"`), a `TruthStatus` enum member, or a set of either. No filter means return all edges regardless of truth status.

### BFS

```python
layers = g.bfs(["wiki:Sherlock_Holmes"], max_hops=2)
# layers[0] = {'wiki:Sherlock_Holmes'}
# layers[1] = statement IDs and entity IDs reachable in one hop
# layers[2] = everything reachable in two hops
```

BFS traverses both outward and inward edges, so symmetric predicates (`Knows`) and event participation (`Involves`) are reachable regardless of the direction they were stored. Statement nodes are added to layers — a `KnewAt` reachable in hop 1 makes the statement it points at reachable in hop 2. The default `truth_values=('asserted_true',)` filter excludes hypothetical and disputed claims from traversal.

### Transitive closure

```python
from ner_20260608.holmes_schema import LocatedIn

reachable = g.transitive_closure("wiki:221B_Baker_Street", LocatedIn)
# → {'wiki:London'}
```

`LocatedIn` is declared `Transitive` in the schema. The transitive closure follows it recursively through the asserted graph. The manual instance graph in `scandal_instances.py` has the geographic chain: Briony Lodge → St. John's Wood → London; 221B Baker Street → London.

> **Note on `scandal_instances.py`:** This file is in the source repository under `src/` and is not shipped in the wheel. On a clean install, `import scandal_instances` will fail. Use `Graph.from_module` with the repo on the path, or run from the repo root with `pdm run python`. See the cookbook for the full invocation pattern.

### Temporal queries: sentence_cutoff

`load_bohemia_graph(sentence_cutoff=N)` loads only triplets whose `sentence_ids` are all strictly less than N. This builds a temporally-bounded subgraph: everything the graph knew before sentence N.

```python
CUTOFF = 485  # Holmes says "You have the photograph?" — we stop before this

pre = load_bohemia_graph(sentence_cutoff=CUTOFF, warn=False)

# Does the pre-revelation graph contain Irene's possession of the photograph?
photo_edges = pre.edges_from(
    "wiki:Irene_Adler",
    pred_type=Possesses,
    truth="asserted_true",
)
# → [] — the Possesses edge is at sentence 511, after the cutoff
```

This is useful for reasoning about what the characters could have known at any point in the narrative. Combined with `KnewAt` edges and their attached `Moment` instances, it enables questions like: "What did Watson know, and when did he come to know it, as of sentence 200?"

---

## 2.6 Design Decisions and Their Consequences

**Local vs. frontier allocation.** The coref and triplet passes run on a local `qwen2.5:14b` model. The clustering and event extraction passes use Claude via the API. This division is not aesthetic — it reflects the nature of each task. Coref and triplet extraction are slot-filling against constrained schemas; the local model is adequate and the volume is high (46 chunks per story; hundreds of thousands of chunks at medical scale). Clustering requires narrative world-knowledge that the local model does not have; event extraction requires reasoning about states vs. actions that the local model consistently gets wrong. The frontier model is used only where reasoning quality is the bottleneck.

**The domain service wall.** Every surprise in the Holmes pipeline lived inside the domain service boundary: spurious wiki links, the "John"/"Dr Watson" deduplication bug, the alias scheme for prompt injection. None of these forced changes to the loader, graph, or schema. That is the test of a clean boundary, and it passed.

**Provisional IDs as principled output.** `provisional:N` IDs are not failures — they are the correct output for entities that have no Baker Street Wiki page. The pipeline produces a queryable graph even with partial ontology coverage. Provisional entities can be manually upgraded to canonical IDs in a later pass without re-running any pipeline stage.

**`truth_status: hypothetical` as a pipeline invariant.** The pipeline never promotes claims to `asserted_true`. That is a deliberate choice: the pipeline reports what it extracted, not what is true. Promotion requires a judgment that the pipeline is not equipped to make automatically. The manually annotated `scandal_instances.py` has `truth_status: asserted_true` throughout because every claim in it was verified by a human against the source text.

**The unified Statement model.** The decision to make `E ⊆ V` — every predicate instance a full member of the vertex set — eliminated what would otherwise have been a separate reification mechanism. `KnewAt` and `Contradicts` simply declare their domain or range as `BaseStatement` and get higher-order predication for free. There is no Statement node type, no three-edge structural overhead, no multi-hop traversal tax for the common case. The fixpoint problem in the loader is the only complexity this introduces, and it is tractable.
