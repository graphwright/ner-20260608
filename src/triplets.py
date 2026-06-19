"""
triplets.py

Triplet extraction pass — reads the full entity/event/moment index and the
sentence JSONL, emits predicate instances (typed graph edges) as JSONL.

Reads:
    bohemia_sentences.jsonl   — sentence JSONL from sentencize.py
    bohemia_entities.jsonl    — entity table from merge.py
    bohemia_events.jsonl      — events from events.py
    bohemia_moments.jsonl     — moments from events.py

Writes:
    bohemia_triplets.jsonl    — one record per predicate instance

Each output record is a predicate instance conforming to holmes_schema.py.
Fields:
    {
      "id":                    "stmt:<subject_id>:<predicate>:<object_id>",
      "predicate":             "AssociatedWith" | "Knows" | "LocatedIn" |
                               "Possesses" | "DisguisedAs" | "HasTrueIdentity" |
                               "Involves" | "OccurredAt",
      "subject_id":            "<entity_id>",
      "subject_type":          "Person" | "Location" | "Event" | "Persona" | ...,
      "object_id":             "<entity_id>",
      "object_type":           "Person" | "Location" | "Object" | "Document" | ...,
      "truth_status":          "hypothetical" (default; promotion pass sets asserted_true),
      "story_id":              "scandal_in_bohemia",
      "paragraph_index":       int,
      "sentence_ids":          [int, ...],
      "asserting_narrator_id": "<entity_id>" | null,
      "extraction_method":     "llm-triplet-extraction",
      "extraction_confidence": float,
      "narrator_confidence":   float | null   (epistemic fields only)
    }

Deferred (require higher-order predication, separate pass):
    KnewAt, Contradicts, Executes

Strategy:
    - One LLM call per chunk (local Ollama, qwen2.5:14b)
    - Short alias IDs injected into prompt; expanded back to canonical IDs
      in the validator. This prevents the model inventing its own ID scheme.
    - Events/moments filtered to a window around the current chunk to keep
      prompt size manageable (178 events injected verbatim was the failure mode).
    - Model does slot-filling against known aliases — no open NER.

Environment:
    OLLAMA_BASE   optional, default http://192.168.1.162:11434
    OLLAMA_MODEL  optional, default qwen2.5:14b

Usage:
    python triplets.py \\
        --sentences bohemia_sentences.jsonl \\
        --entities  bohemia_entities.jsonl \\
        --events    bohemia_events.jsonl \\
        --moments   bohemia_moments.jsonl \\
        --output    bohemia_triplets.jsonl

    python triplets.py \\
        --sentences bohemia_sentences.jsonl \\
        --entities  bohemia_entities.jsonl \\
        --events    bohemia_events.jsonl \\
        --moments   bohemia_moments.jsonl \\
        --output    bohemia_triplets.jsonl \\
        --chunk-size 15 --overlap 3 --event-window 30 \\
        --model qwen2.5:14b \\
        --ollama http://192.168.1.162:11434
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import httpx

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
ANTHROPIC_MODEL_MAP = {
    "sonnet-4.6": "claude-sonnet-4-6",
}
DEFAULT_OLLAMA = os.environ.get("OLLAMA_BASE", "http://192.168.1.162:11434")
DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:14b")
STORY_ID = "scandal_in_bohemia"
MAX_RETRIES = 3
DEFAULT_CHUNK_SIZE = 15
DEFAULT_OVERLAP = 3
DEFAULT_EVENT_WIN = 15

# ---------------------------------------------------------------------------
# Predicate catalogue
# ---------------------------------------------------------------------------

PREDICATES = """
Predicate catalogue — use ONLY these predicate names:

  AssociatedWith
    subject_type : Person
    object_type  : Location
    meaning      : Person is habitually associated with a location

  Knows
    subject_type : Person
    object_type  : Person
    meaning      : Person has an acquaintance or professional relationship with another
    trait        : Symmetric — emit ONE direction only (subject alias alphabetically first)

  LocatedIn
    subject_type : Location
    object_type  : Location
    meaning      : A location is situated within another location
    trait        : Transitive — emit direct containment only, not inferred chains

  Possesses
    subject_type : Person
    object_type  : Object | Document
    meaning      : Person possesses an Object or Document

  DisguisedAs
    subject_type : Person
    object_type  : Persona
    meaning      : Person adopted a disguise (Persona)

  HasTrueIdentity
    subject_type : Persona
    object_type  : Person
    meaning      : A Persona conceals this real Person
    trait        : Functional — a persona conceals exactly one person

  Involves
    subject_type : Event
    object_type  : Person | Persona
    meaning      : Event involves this entity as a participant

  OccurredAt
    subject_type : Event
    object_type  : Moment
    meaning      : Event is anchored to this point in time

DO NOT emit KnewAt, Contradicts, or Executes — those are deferred.
DO NOT invent predicate names not in this list.
"""

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

TRIPLET_SYSTEM = """\
You are a knowledge graph construction engine for Sherlock Holmes fiction.
You extract typed predicate instances (subject, predicate, object) from story sentences.
Output only JSON objects, one per line. No prose, no markdown fences, no empty objects.
Every line must be a complete, valid JSON object.
"""

TRIPLET_USER_TMPL = """\
You are reading "A Scandal in Bohemia" by Conan Doyle, narrated by Dr Watson.

CONTEXT (preceding sentences — do not extract from these):
{context_block}

CHUNK (extract predicate instances from these sentences only):
{chunk_block}

--- ENTITY INDEX ---
CRITICAL: Use ONLY the alias keys shown below (left of "->") as subject_id and object_id.
Do NOT use the names on the right. Do NOT invent IDs.

Persons:
{persons_block}

Personas:
{personas_block}

Locations:
{locations_block}

Objects and Documents:
{objects_block}

Events (near this chunk):
{events_block}

Moments (near this chunk):
{moments_block}

--- PREDICATE CATALOGUE ---
{predicates}

--- TASK ---
For each relationship expressed or strongly implied in the CHUNK sentences,
output one JSON object:
{{
  "predicate":             "<predicate name from catalogue>",
  "subject_id":            "<alias from entity index, e.g. person:sherlock_holmes>",
  "subject_type":          "<entity type, e.g. Person>",
  "object_id":             "<alias from entity index>",
  "object_type":           "<entity type>",
  "sentence_ids":          [list of integer sentence ids from CHUNK that express this],
  "para":                  <paragraph number of primary sentence>,
  "asserting_narrator_id": "<narrator alias from entity index, or null>",
  "extraction_confidence": <float 0.0-1.0>,
  "narrator_confidence":   <float 0.0-1.0 for Possesses/DisguisedAs/HasTrueIdentity, else null>
}}

Rules:
- subject_id and object_id MUST be alias keys from the entity index (e.g. person:sherlock_holmes).
  If an entity has no alias in the index, skip the triplet entirely.
- Only extract from CHUNK sentences, never from CONTEXT.
- sentence_ids must be integers from the CHUNK.
- Respect domain/range constraints strictly.
- Do not emit duplicate triplets (same predicate + subject_id + object_id).
- Do not output {{}}.
"""

def resolve_model(model: str, anthropic: bool) -> str:
    if anthropic:
        return ANTHROPIC_MODEL_MAP.get(model, model)
    return model


def ollama_chat(system: str, user: str, model: str, base_url: str) -> str:
    url = f"{base_url.rstrip('/')}/api/chat"
    payload = {
        "model": model,
        "stream": False,
        "options": {"temperature": 0.0},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
    }
    resp = httpx.post(url, json=payload, timeout=httpx.Timeout(10.0, read=360.0))
    resp.raise_for_status()
    return resp.json()["message"]["content"]


def anthropic_chat(system: str, user: str, model: str) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    payload = {
        "model": resolve_model(model, anthropic=True),
        "max_tokens": 4096,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    resp = httpx.post(
        ANTHROPIC_API_URL,
        json=payload,
        headers={
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        },
        timeout=httpx.Timeout(10.0, read=360.0),
    )
    resp.raise_for_status()
    return resp.json()["content"][0]["text"]


def chat(system: str, user: str, model: str, anthropic: bool, base_url: str | None) -> str:
    if anthropic:
        return anthropic_chat(system, user, model)
    assert base_url is not None
    return ollama_chat(system, user, model, base_url)

def extract_json_objects(raw: str) -> list[dict]:
    decoder = json.JSONDecoder()
    objects = []
    i = 0
    while i < len(raw):
        start = raw.find("{", i)
        if start == -1:
            break
        try:
            obj, end_idx = decoder.raw_decode(raw, start)
            if isinstance(obj, dict) and obj:
                objects.append(obj)
            i = end_idx
        except json.JSONDecodeError:
            i = start + 1
    return objects


# ---------------------------------------------------------------------------
# Load helpers
# ---------------------------------------------------------------------------

def load_sentences(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    rows.sort(key=lambda s: s["id"])
    return rows


def load_entities(path: Path) -> dict[str, dict]:
    """Returns {entity_id: record}."""
    index: dict[str, dict] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            entity_id = rec.get("entity_id", "")
            if not entity_id:
                continue
            index[entity_id] = rec
    return index


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


# ---------------------------------------------------------------------------
# Entity type classification
# ---------------------------------------------------------------------------

TYPE_MAP = {
    "person":       "Person",
    "place":        "Location",
    "object":       "Object",
    "organization": "Organization",
    "other":        "Other",
}

PERSONA_HINTS = {"persona", "disguise", "alias", "clergyman", "count von kramm",
                 "nonconformist"}


def classify_entity(rec: dict) -> str:
    raw_type = rec.get("type", "other").lower()
    canonical = rec.get("canonical", "").lower()
    if any(h in canonical for h in PERSONA_HINTS):
        return "Persona"
    return TYPE_MAP.get(raw_type, "Other")


# ---------------------------------------------------------------------------
# Alias ID scheme
#
# The model consistently ignores long URIs and invents its own IDs.
# Solution: inject short aliases (e.g. "person:sherlock_holmes") into the
# prompt; validate against aliases; expand back to canonical IDs on output.
# ---------------------------------------------------------------------------

def make_alias(name: str, schema_type: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:40]
    return f"{schema_type.lower()}:{slug}"


def build_alias_tables(
    entity_index: dict[str, dict],
    events: list[dict],
    moments: list[dict],
) -> tuple[dict[str, dict[str, str]], dict[str, str], dict[str, str]]:
    """
    Returns:
        partitions  — {schema_type: {alias: display_name}}  for prompt
        alias_to_id — {alias: canonical_entity_id}          for expansion
        alias_to_type — {alias: schema_type}                for validation
    """
    parts: dict[str, dict[str, str]] = {
        "Person": {}, "Persona": {}, "Location": {},
        "Object": {}, "Event":   {}, "Moment":   {},
    }
    alias_to_id:   dict[str, str] = {}
    alias_to_type: dict[str, str] = {}
    seen: dict[str, int] = {}

    def unique(base: str) -> str:
        if base not in seen:
            seen[base] = 1
            return base
        seen[base] += 1
        return f"{base}_{seen[base]}"

    for eid, rec in entity_index.items():
        stype = classify_entity(rec)
        if stype not in parts:
            continue
        canonical = rec.get("canonical", eid)
        alias = unique(make_alias(canonical, stype))
        parts[stype][alias] = canonical
        alias_to_id[alias]   = eid
        alias_to_type[alias] = stype

    for evt in events:
        alias = unique(make_alias(evt.get("description", evt["id"])[:40], "event"))
        parts["Event"][alias] = evt.get("description", evt["id"])
        alias_to_id[alias]   = evt["id"]
        alias_to_type[alias] = "Event"

    for mom in moments:
        alias = unique(make_alias(mom.get("label", mom["id"])[:40], "moment"))
        parts["Moment"][alias] = mom.get("label", mom["id"])
        alias_to_id[alias]   = mom["id"]
        alias_to_type[alias] = "Moment"

    return parts, alias_to_id, alias_to_type


def filter_events_moments(
    partitions:    dict[str, dict[str, str]],
    alias_to_id:   dict[str, str],
    id_to_event:   dict[str, dict],
    id_to_moment:  dict[str, dict],
    chunk_min: int,
    chunk_max: int,
    window: int,
) -> dict[str, dict[str, str]]:
    """Return partitions with Event/Moment filtered to the chunk window."""
    lo, hi = chunk_min - window, chunk_max + window
    filtered = {k: dict(v) for k, v in partitions.items()}

    filtered["Event"] = {
        alias: name
        for alias, name in partitions["Event"].items()
        if any(lo <= sid <= hi
               for sid in id_to_event.get(
                   alias_to_id.get(alias, ""), {}).get("sentence_ids", []))
    }
    filtered["Moment"] = {
        alias: name
        for alias, name in partitions["Moment"].items()
        if any(lo <= sid <= hi
               for sid in id_to_moment.get(
                   alias_to_id.get(alias, ""), {}).get("sentence_ids", []))
    }
    return filtered


def format_partition(partition: dict[str, str], limit: int = 72) -> str:
    if not partition:
        return "  (none)"
    lines = []
    for alias, name in sorted(partition.items()):
        display = name if len(name) <= limit else name[:limit - 3] + "..."
        lines.append(f"  {alias}  ->  {display}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Content-addressed statement ID
# ---------------------------------------------------------------------------

def statement_id(subject_id: str, predicate: str, object_id: str) -> str:
    return f"stmt:{subject_id}:{predicate}:{object_id}"


# ---------------------------------------------------------------------------
# Domain / range enforcement
# ---------------------------------------------------------------------------

VALID_PREDICATES = {
    "AssociatedWith", "Knows", "LocatedIn", "Possesses",
    "DisguisedAs", "HasTrueIdentity", "Involves", "OccurredAt",
}

DOMAIN_RANGE: dict[str, tuple[set[str], set[str]]] = {
    "AssociatedWith":  ({"Person"},   {"Location"}),
    "Knows":           ({"Person"},   {"Person"}),
    "LocatedIn":       ({"Location"}, {"Location"}),
    "Possesses":       ({"Person"},   {"Object", "Document"}),
    "DisguisedAs":     ({"Person"},   {"Persona"}),
    "HasTrueIdentity": ({"Persona"},  {"Person"}),
    "Involves":        ({"Event"},    {"Person", "Persona"}),
    "OccurredAt":      ({"Event"},    {"Moment"}),
}


def valid_types(predicate: str, subj_type: str, obj_type: str) -> bool:
    if predicate not in DOMAIN_RANGE:
        return False
    dom, ran = DOMAIN_RANGE[predicate]
    return subj_type in dom and obj_type in ran


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

class TripletRegistry:
    def __init__(self):
        self._seen: set[tuple[str, str, str]] = set()

    def register(self, predicate: str, subject_id: str, object_id: str) -> bool:
        key = (predicate, subject_id, object_id)
        if key in self._seen:
            return False
        self._seen.add(key)
        return True

    def load_existing(self, path: Path) -> None:
        if not path.exists():
            return
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    self._seen.add((rec["predicate"], rec["subject_id"], rec["object_id"]))
                except (json.JSONDecodeError, KeyError):
                    pass


# ---------------------------------------------------------------------------
# Record validation — aliases in, canonical IDs out
# ---------------------------------------------------------------------------

def validate_triplet(
    obj:            dict,
    valid_sids:     set[int],
    alias_to_id:    dict[str, str],
    alias_to_type:  dict[str, str],
    registry:       TripletRegistry,
) -> dict | None:

    required = {
        "predicate", "subject_id", "subject_type",
        "object_id", "object_type", "sentence_ids",
        "para", "extraction_confidence",
    }
    if not required.issubset(obj.keys()):
        return None

    predicate    = str(obj["predicate"]).strip()
    subj_alias   = str(obj["subject_id"]).strip()
    obj_alias    = str(obj["object_id"]).strip()
    subj_type    = str(obj["subject_type"]).strip()
    obj_type     = str(obj["object_type"]).strip()

    if predicate not in VALID_PREDICATES:
        print(f"  [warn] unknown predicate: {predicate!r}", file=sys.stderr)
        return None

    # Resolve aliases to canonical IDs
    subject_id = alias_to_id.get(subj_alias)
    object_id  = alias_to_id.get(obj_alias)

    if subject_id is None:
        print(f"  [warn] unknown subject alias: {subj_alias!r}", file=sys.stderr)
        return None
    if object_id is None:
        print(f"  [warn] unknown object alias: {obj_alias!r}", file=sys.stderr)
        return None

    # Use alias_to_type as ground truth for type — model's subject_type/object_type
    # fields are advisory but may be wrong; prefer what we know from the index.
    resolved_subj_type = alias_to_type.get(subj_alias, subj_type)
    resolved_obj_type  = alias_to_type.get(obj_alias,  obj_type)

    if not valid_types(predicate, resolved_subj_type, resolved_obj_type):
        print(
            f"  [warn] domain/range violation: "
            f"{predicate}({resolved_subj_type} -> {resolved_obj_type})",
            file=sys.stderr,
        )
        return None

    sids = [int(s) for s in obj["sentence_ids"] if int(s) in valid_sids]
    if not sids:
        return None

    if not registry.register(predicate, subject_id, object_id):
        return None  # duplicate

    try:
        confidence = float(obj["extraction_confidence"])
    except (ValueError, TypeError):
        confidence = 0.5

    narrator_confidence = None
    raw_nc = obj.get("narrator_confidence")
    if raw_nc is not None and str(raw_nc) != "null":
        try:
            narrator_confidence = float(raw_nc)
        except (ValueError, TypeError):
            pass

    # Resolve narrator alias
    raw_narrator = obj.get("asserting_narrator_id")
    asserting_narrator_id = None
    if raw_narrator and str(raw_narrator) != "null":
        asserting_narrator_id = alias_to_id.get(str(raw_narrator).strip())

    return {
        "id":                    statement_id(subject_id, predicate, object_id),
        "predicate":             predicate,
        "subject_id":            subject_id,
        "subject_type":          resolved_subj_type,
        "object_id":             object_id,
        "object_type":           resolved_obj_type,
        "truth_status":          "hypothetical",
        "story_id":              STORY_ID,
        "paragraph_index":       int(obj["para"]),
        "sentence_ids":          sids,
        "asserting_narrator_id": asserting_narrator_id,
        "extraction_method":     "llm-triplet-extraction",
        "extraction_confidence": round(confidence, 3),
        "narrator_confidence":   narrator_confidence,
    }


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def make_chunks(sentences: list[dict], chunk_size: int, overlap: int):
    chunks = []
    for start in range(0, len(sentences), chunk_size):
        chunk   = sentences[start : start + chunk_size]
        context = sentences[max(0, start - overlap) : start]
        chunks.append((context, chunk))
    return chunks


def format_block(sentences: list[dict]) -> str:
    if not sentences:
        return "(none)"
    return "\n".join(f"[{s['id']}] {s['text']}" for s in sentences)


def chunk_id_str(chunk: list[dict]) -> str:
    return f"{chunk[0]['id']}-{chunk[-1]['id']}"


# ---------------------------------------------------------------------------
# Resume support
# ---------------------------------------------------------------------------

def read_done_chunks(output_path: Path) -> set[str]:
    sidecar = output_path.parent / f".{output_path.stem}_progress.json"
    if not sidecar.exists():
        return set()
    try:
        return set(json.loads(sidecar.read_text()))
    except Exception:
        return set()


def mark_chunk_done(output_path: Path, cid: str, done: set[str]) -> None:
    sidecar = output_path.parent / f".{output_path.stem}_progress.json"
    done.add(cid)
    sidecar.write_text(json.dumps(sorted(done)))


# ---------------------------------------------------------------------------
# Main processing
# ---------------------------------------------------------------------------

def process_chunk(
    context: list[dict],
    chunk: list[dict],
    partitions: dict[str, dict[str, str]],
    alias_to_id: dict[str, str],
    alias_to_type: dict[str, str],
    registry: TripletRegistry,
    model: str,
    anthropic: bool,
    base_url: str | None,
    debug: bool = False,
) -> list[dict]:

    user = TRIPLET_USER_TMPL.format(
        context_block   = format_block(context),
        chunk_block     = format_block(chunk),
        persons_block   = format_partition(partitions["Person"]),
        personas_block  = format_partition(partitions["Persona"]),
        locations_block = format_partition(partitions["Location"]),
        objects_block   = format_partition(partitions["Object"]),
        events_block    = format_partition(partitions["Event"]),
        moments_block   = format_partition(partitions["Moment"]),
        predicates      = PREDICATES,
    )

    valid_ids = {s["id"] for s in chunk}
    raw = ""

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            raw = chat(TRIPLET_SYSTEM, user, model, anthropic, base_url)
            break
        except Exception as e:
            print(f"[error attempt {attempt}: {e}]", end=" ", flush=True)
            if attempt == MAX_RETRIES:
                raise
            time.sleep(2 ** attempt)

    if debug:
        print(f"\n  [debug] raw:\n{raw[:800]}\n", file=sys.stderr)

    triplets = []
    for obj in extract_json_objects(raw):
        rec = validate_triplet(obj, valid_ids, alias_to_id, alias_to_type, registry)
        if rec:
            triplets.append(rec)

    return triplets


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Triplet extraction -> JSONL (local Ollama)"
    )
    parser.add_argument("--sentences", required=True)
    parser.add_argument("--entities", required=True)
    parser.add_argument("--events", required=True)
    parser.add_argument("--moments", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--ollama", default=DEFAULT_OLLAMA)
    parser.add_argument("--anthropic", action="store_true")
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    parser.add_argument("--overlap", type=int, default=DEFAULT_OVERLAP)
    parser.add_argument("--event-window", type=int, default=DEFAULT_EVENT_WIN, help="Sentence radius around chunk for event/moment filtering")
    parser.add_argument("--dump-aliases", action="store_true", help="Print the full alias table and exit")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    sentences_path = Path(args.sentences)
    entities_path  = Path(args.entities)
    events_path    = Path(args.events)
    moments_path   = Path(args.moments)
    output_path    = Path(args.output)

    sentences    = load_sentences(sentences_path)
    entity_index = load_entities(entities_path)
    events       = load_jsonl(events_path)
    moments      = load_jsonl(moments_path)

    print(f"Loaded: {len(sentences)} sentences, {len(entity_index)} entities, "
          f"{len(events)} events, {len(moments)} moments")

    # Build alias tables
    partitions, alias_to_id, alias_to_type = build_alias_tables(
        entity_index, events, moments
    )

    # Build lookup dicts for event/moment window filtering
    id_to_event  = {e["id"]: e for e in events}
    id_to_moment = {m["id"]: m for m in moments}

    all_alias_ids = set(alias_to_id.keys())

    print(f"Alias index: "
          f"{len(partitions['Person'])} persons, "
          f"{len(partitions['Persona'])} personas, "
          f"{len(partitions['Location'])} locations, "
          f"{len(partitions['Object'])} objects, "
          f"{len(partitions['Event'])} events, "
          f"{len(partitions['Moment'])} moments")

    if args.dump_aliases:
        for stype, entries in partitions.items():
            print(f"\n--- {stype} ---")
            for alias, name in sorted(entries.items()):
                canonical_id = alias_to_id.get(alias, "?")
                print(f"  {alias:50s}  {name}  [{canonical_id}]")
        return

    registry = TripletRegistry()
    registry.load_existing(output_path)

    chunks = make_chunks(sentences, args.chunk_size, args.overlap)
    print(f"Generated {len(chunks)} chunks "
          f"(size={args.chunk_size}, overlap={args.overlap}, "
          f"event-window=+/-{args.event_window})")

    done_chunks = read_done_chunks(output_path)
    if done_chunks:
        print(f"Resuming: {len(done_chunks)} chunks already processed")

    total = 0

    with output_path.open("a", encoding="utf-8") as out_fh:
        for i, (context, chunk) in enumerate(chunks, 1):
            cid = chunk_id_str(chunk)
            if cid in done_chunks:
                print(f"  [{i:>3}/{len(chunks)}] {cid} already done, skipping")
                continue

            chunk_min = chunk[0]["id"]
            chunk_max = chunk[-1]["id"]

            # Filter events/moments to window around this chunk
            local_partitions = filter_events_moments(
                partitions, alias_to_id,
                id_to_event, id_to_moment,
                chunk_min, chunk_max, args.event_window,
            )

            print(
                f"  [{i:>3}/{len(chunks)}] chunk {cid} "
                f"({len(chunk)} sents, {len(context)} ctx, "
                f"{len(local_partitions['Event'])} evts, "
                f"{len(local_partitions['Moment'])} moms) ...",
                end=" ", flush=True,
            )

            triplets = process_chunk(
                context,
                chunk,
                local_partitions,
                alias_to_id,
                alias_to_type,
                registry,
                args.model,
                args.anthropic,
                None if args.anthropic else args.ollama,
                args.debug,
            )

            for rec in triplets:
                out_fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out_fh.flush()

            total += len(triplets)
            mark_chunk_done(output_path, cid, done_chunks)
            print(f"-> {len(triplets)} triplets (total {total})")

    print(f"\nDone. {total} triplets written to {output_path}")


if __name__ == "__main__":
    main()
