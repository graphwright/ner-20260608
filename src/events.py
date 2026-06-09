"""
events.py

Event and Moment extraction from a sentencized Holmes story.

Reads:
    bohemia_sentences.jsonl   — sentence JSONL from sentencize.py
    bohemia_entities.jsonl    — entity table from merge.py

Writes:
    bohemia_events.jsonl      — one record per discrete event
    bohemia_moments.jsonl     — one record per temporal anchor

This is a frontier-model pass (Claude API). Event/moment identification
requires narrative reasoning that local models handle poorly: distinguishing
state from action, handling indirect narration (Watson reports what Holmes
told him), and resolving implicit temporal anchors.

Event record schema:
    {
      "id": "sib:event:<slug>",
      "description": "...",
      "sentence_ids": [int, ...],
      "para": int,
      "participants": ["entity_id", ...],   # from known entity index
      "extraction_confidence": float
    }

Moment record schema:
    {
      "id": "sib:moment:<slug>",
      "label": "...",
      "event_id": "sib:event:<slug>" | null,
      "narrator_id": "entity_id" | null,    # null = objective timeline
      "sentence_ids": [int, ...],
      "extraction_confidence": float
    }

Higher-order predicates (KnewAt, Contradicts) and Plans are deferred —
this pass focuses on first-order events and their temporal anchors.

Environment:
    ANTHROPIC_API_KEY   required

Usage:
    python events.py \\
        --sentences bohemia_sentences.jsonl \\
        --entities  bohemia_entities.jsonl \\
        --events    bohemia_events.jsonl \\
        --moments   bohemia_moments.jsonl

    python events.py \\
        --sentences bohemia_sentences.jsonl \\
        --entities  bohemia_entities.jsonl \\
        --events    bohemia_events.jsonl \\
        --moments   bohemia_moments.jsonl \\
        --chunk-size 30 \\
        --overlap 5
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

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL      = "claude-sonnet-4-20250514"
STORY_ID          = "scandal_in_bohemia"
WIKI_BASE         = "https://bakerstreet.fandom.com/wiki/"
MAX_RETRIES       = 3
DEFAULT_CHUNK     = 30
DEFAULT_OVERLAP   = 5

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

EVENT_SYSTEM = """\
You are an expert literary analyst specialising in Sherlock Holmes fiction.
You extract discrete events and temporal anchors from story text.
Output only JSON objects, one per line. No prose, no markdown fences, no empty objects.
"""

EVENT_USER_TMPL = """\
You are reading "A Scandal in Bohemia" by Conan Doyle. The story is narrated
by Dr Watson in the first person.

Below are sentences from the story, each prefixed with [sentence_id].

CONTEXT (preceding sentences — do not extract from these):
{context_block}

CHUNK (extract from these sentences):
{chunk_block}

Known entities (id → canonical name):
{entity_index}

--- TASK A: Extract discrete EVENTS ---

An event is a bounded action or occurrence — something that HAPPENED, with
agency and temporal extent. It is NOT a state ("Irene lives at Briony Lodge")
or a description.

Good examples: a visit, an arrival, a wedding, a disguise being adopted,
a fire alarm being triggered, a discovery.

For each event output ONE JSON object:
{{
  "record_type": "event",
  "id": "sib:event:<short_snake_case_slug>",
  "description": "one concise sentence describing what happened",
  "sentence_ids": [list of integer sentence ids from the CHUNK that anchor this event],
  "para": <paragraph number of the primary sentence>,
  "participants": [list of entity ids from the known entity index who are active participants],
  "extraction_confidence": <float 0.0-1.0>
}}

--- TASK B: Extract temporal MOMENTS ---

A moment is a named point in time that anchors an event or a character's
epistemic state. It may be:
  - Objective: when something happened on the story timeline (narrator_id = null)
  - Epistemic: when a character LEARNED something (narrator_id = that person's entity_id)

For each temporal anchor output ONE JSON object:
{{
  "record_type": "moment",
  "id": "sib:moment:<short_snake_case_slug>",
  "label": "brief human-readable label, e.g. 'Evening of 20 March 1888'",
  "event_id": "sib:event:<slug> of the event this moment anchors, or null",
  "narrator_id": "entity_id of the person on whose epistemic timeline this sits, or null",
  "sentence_ids": [list of integer sentence ids from the CHUNK that express this moment],
  "extraction_confidence": <float 0.0-1.0>
}}

Rules:
- Extract from CHUNK sentences only, never from CONTEXT.
- slug must be short, lowercase, underscores only, globally unique within the story.
- participants must be entity ids from the known entity index — do not invent ids.
- If a participant's entity id is not in the known index, omit them rather than inventing.
- Distinguish action (event) from state (not an event).
- Do not extract the same event twice across overlapping chunks — prefer the chunk
  where the event is most fully described.
- Watson's narration is the default asserting narrator; flag epistemic moments
  (what a character came to know) with the appropriate narrator_id.
- Output nothing if there are no events or moments in this chunk.
- Do not output {{}}.
"""

# ---------------------------------------------------------------------------
# Claude API client
# ---------------------------------------------------------------------------

def claude(system: str, user: str, max_tokens: int = 4096) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    payload = {
        "model": CLAUDE_MODEL,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    resp = httpx.post(
        ANTHROPIC_API_URL,
        json=payload,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        timeout=120.0,
    )
    resp.raise_for_status()
    return resp.json()["content"][0]["text"]


# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------

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


def load_entities(path: Path) -> dict[str, str]:
    """
    Returns {entity_id: canonical_name}.
    Expands wiki: slugs to full Baker Street Wiki URIs.
    """
    index: dict[str, str] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            raw_id    = rec.get("entity_id", "")
            canonical = rec.get("canonical", "")
            if not raw_id or not canonical:
                continue
            # Expand wiki: slug → full URI
            if raw_id.startswith("wiki:"):
                slug   = raw_id[len("wiki:"):]
                full_id = f"{WIKI_BASE}{slug}"
            else:
                full_id = raw_id
            index[full_id] = canonical
    return index


def format_entity_index(index: dict[str, str]) -> str:
    lines = [f"  {eid} → {name}" for eid, name in sorted(index.items())]
    return "\n".join(lines) if lines else "  (none)"


# ---------------------------------------------------------------------------
# Slug registry — ensures uniqueness within a run
# ---------------------------------------------------------------------------

class SlugRegistry:
    def __init__(self):
        self._seen: set[str] = set()

    def register(self, slug: str) -> str:
        """Accept slug if unseen; append _2, _3 ... if collision."""
        if slug not in self._seen:
            self._seen.add(slug)
            return slug
        n = 2
        while f"{slug}_{n}" in self._seen:
            n += 1
        unique = f"{slug}_{n}"
        self._seen.add(unique)
        return unique

    def seen(self, slug: str) -> bool:
        return slug in self._seen


event_slugs   = SlugRegistry()
moment_slugs  = SlugRegistry()


# ---------------------------------------------------------------------------
# Record validation
# ---------------------------------------------------------------------------

_SLUG_RE = re.compile(r'^sib:(event|moment):[a-z][a-z0-9_]*$')


def validate_event(obj: dict, valid_sentence_ids: set[int],
                   entity_index: dict[str, str]) -> dict | None:
    required = {"id", "description", "sentence_ids", "para",
                "participants", "extraction_confidence"}
    if not required.issubset(obj.keys()):
        print(f"  [warn] event missing fields: {obj}", file=sys.stderr)
        return None

    eid = str(obj["id"]).strip()
    if not _SLUG_RE.match(eid) or not eid.startswith("sib:event:"):
        print(f"  [warn] bad event id: {eid!r}", file=sys.stderr)
        return None

    # Deduplicate slug
    slug = eid[len("sib:event:"):]
    if event_slugs.seen(eid):
        # Already emitted this event — skip (cross-chunk dedup)
        return None
    event_slugs.register(eid)

    # Filter sentence_ids to chunk only
    sids = [int(s) for s in obj["sentence_ids"] if int(s) in valid_sentence_ids]
    if not sids:
        print(f"  [warn] event {eid} has no valid sentence_ids", file=sys.stderr)
        return None

    # Filter participants to known entity ids
    raw_participants = obj.get("participants", [])
    participants = [p for p in raw_participants if p in entity_index]
    unknown = set(raw_participants) - set(participants)
    if unknown:
        print(f"  [warn] event {eid} unknown participants dropped: {unknown}",
              file=sys.stderr)

    try:
        confidence = float(obj["extraction_confidence"])
    except (ValueError, TypeError):
        confidence = 0.5

    return {
        "id":                   eid,
        "description":          str(obj["description"]).strip(),
        "sentence_ids":         sids,
        "para":                 int(obj["para"]),
        "participants":         participants,
        "extraction_confidence": round(confidence, 3),
    }


def validate_moment(obj: dict, valid_sentence_ids: set[int],
                    entity_index: dict[str, str],
                    known_event_ids: set[str]) -> dict | None:
    required = {"id", "label", "event_id", "narrator_id",
                "sentence_ids", "extraction_confidence"}
    if not required.issubset(obj.keys()):
        print(f"  [warn] moment missing fields: {obj}", file=sys.stderr)
        return None

    mid = str(obj["id"]).strip()
    if not _SLUG_RE.match(mid) or not mid.startswith("sib:moment:"):
        print(f"  [warn] bad moment id: {mid!r}", file=sys.stderr)
        return None

    if moment_slugs.seen(mid):
        return None
    moment_slugs.register(mid)

    sids = [int(s) for s in obj["sentence_ids"] if int(s) in valid_sentence_ids]
    if not sids:
        print(f"  [warn] moment {mid} has no valid sentence_ids", file=sys.stderr)
        return None

    # event_id: must be a known event id or null
    raw_event_id = obj.get("event_id")
    event_id = None
    if raw_event_id and raw_event_id != "null":
        if raw_event_id in known_event_ids:
            event_id = raw_event_id
        else:
            # Event may not have been extracted yet (forward reference).
            # Keep it — the triplet pass will tolerate dangling refs.
            event_id = raw_event_id

    # narrator_id: must be a known entity id or null
    raw_narrator = obj.get("narrator_id")
    narrator_id = None
    if raw_narrator and raw_narrator != "null":
        if raw_narrator in entity_index:
            narrator_id = raw_narrator
        else:
            print(f"  [warn] moment {mid} unknown narrator_id: {raw_narrator!r}",
                  file=sys.stderr)

    try:
        confidence = float(obj["extraction_confidence"])
    except (ValueError, TypeError):
        confidence = 0.5

    return {
        "id":                   mid,
        "label":                str(obj["label"]).strip(),
        "event_id":             event_id,
        "narrator_id":          narrator_id,
        "sentence_ids":         sids,
        "extraction_confidence": round(confidence, 3),
    }


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def make_chunks(
    sentences: list[dict],
    chunk_size: int,
    overlap: int,
) -> list[tuple[list[dict], list[dict]]]:
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


def chunk_id(chunk: list[dict]) -> str:
    return f"{chunk[0]['id']}-{chunk[-1]['id']}"


# ---------------------------------------------------------------------------
# Resume helpers
# ---------------------------------------------------------------------------

def read_done_chunks(events_path: Path, moments_path: Path) -> set[str]:
    """
    We use a sidecar file to track which chunk_ids have been processed,
    since events and moments go to separate output files.
    """
    sidecar = events_path.parent / f".{events_path.stem}_progress.json"
    if not sidecar.exists():
        return set()
    try:
        return set(json.loads(sidecar.read_text()))
    except Exception:
        return set()


def mark_chunk_done(events_path: Path, chunk_id_str: str, done: set[str]) -> None:
    sidecar = events_path.parent / f".{events_path.stem}_progress.json"
    done.add(chunk_id_str)
    sidecar.write_text(json.dumps(sorted(done)))


# ---------------------------------------------------------------------------
# Main processing
# ---------------------------------------------------------------------------

def process_chunk(
    context:      list[dict],
    chunk:        list[dict],
    entity_index: dict[str, str],
    known_event_ids: set[str],
) -> tuple[list[dict], list[dict]]:
    """Returns (events, moments) extracted from this chunk."""

    user = EVENT_USER_TMPL.format(
        context_block=format_block(context),
        chunk_block=format_block(chunk),
        entity_index=format_entity_index(entity_index),
    )

    valid_ids = {s["id"] for s in chunk}
    raw = ""

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            raw = claude(EVENT_SYSTEM, user, max_tokens=4096)
            break
        except Exception as e:
            print(f"[error attempt {attempt}: {e}]", end=" ", flush=True)
            if attempt == MAX_RETRIES:
                raise
            time.sleep(2 ** attempt)

    events:  list[dict] = []
    moments: list[dict] = []

    for obj in extract_json_objects(raw):
        record_type = obj.get("record_type", "")

        if record_type == "event":
            rec = validate_event(obj, valid_ids, entity_index)
            if rec:
                events.append(rec)
                known_event_ids.add(rec["id"])

        elif record_type == "moment":
            rec = validate_moment(obj, valid_ids, entity_index, known_event_ids)
            if rec:
                moments.append(rec)

        else:
            if obj:  # non-empty but unrecognised record_type
                print(f"  [warn] unknown record_type: {obj.get('record_type')!r}",
                      file=sys.stderr)

    return events, moments


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract events and moments from sentencized Holmes story (Claude API)"
    )
    parser.add_argument("--sentences",   required=True)
    parser.add_argument("--entities",    required=True)
    parser.add_argument("--events",      required=True)
    parser.add_argument("--moments",     required=True)
    parser.add_argument("--chunk-size",  type=int, default=DEFAULT_CHUNK)
    parser.add_argument("--overlap",     type=int, default=DEFAULT_OVERLAP)
    parser.add_argument("--debug",       action="store_true")
    args = parser.parse_args()

    sentences_path = Path(args.sentences)
    entities_path  = Path(args.entities)
    events_path    = Path(args.events)
    moments_path   = Path(args.moments)

    sentences    = load_sentences(sentences_path)
    entity_index = load_entities(entities_path)
    print(f"Loaded {len(sentences)} sentences, {len(entity_index)} entities")

    chunks = make_chunks(sentences, args.chunk_size, args.overlap)
    print(f"Generated {len(chunks)} chunks (size={args.chunk_size}, overlap={args.overlap})")

    done_chunks     = read_done_chunks(events_path, moments_path)
    known_event_ids: set[str] = set()

    # Pre-populate known_event_ids from any existing output (resume case)
    if events_path.exists():
        with events_path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        rec = json.loads(line)
                        known_event_ids.add(rec["id"])
                        event_slugs.register(rec["id"])
                    except Exception:
                        pass
    if moments_path.exists():
        with moments_path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        rec = json.loads(line)
                        moment_slugs.register(rec["id"])
                    except Exception:
                        pass

    if done_chunks:
        print(f"Resuming: {len(done_chunks)} chunks already processed")

    total_events = total_moments = 0

    with (
        events_path.open("a",  encoding="utf-8") as evt_fh,
        moments_path.open("a", encoding="utf-8") as mom_fh,
    ):
        for i, (context, chunk) in enumerate(chunks, 1):
            cid = chunk_id(chunk)
            if cid in done_chunks:
                print(f"  [{i:>3}/{len(chunks)}] {cid} already done, skipping")
                continue

            print(
                f"  [{i:>3}/{len(chunks)}] chunk {cid} "
                f"({len(chunk)} sentences, {len(context)} context) ...",
                end=" ", flush=True,
            )

            events, moments = process_chunk(
                context, chunk, entity_index, known_event_ids
            )

            if args.debug and not events and not moments:
                print(f"\n  [debug] no records extracted from chunk {cid}",
                      file=sys.stderr)

            for rec in events:
                evt_fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            for rec in moments:
                mom_fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

            evt_fh.flush()
            mom_fh.flush()

            total_events  += len(events)
            total_moments += len(moments)
            mark_chunk_done(events_path, cid, done_chunks)

            print(f"→ {len(events)} events, {len(moments)} moments")

    print(f"\nDone.")
    print(f"  Events  : {total_events:>4}  → {events_path}")
    print(f"  Moments : {total_moments:>4}  → {moments_path}")


if __name__ == "__main__":
    main()
