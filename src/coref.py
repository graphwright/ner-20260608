"""
coref.py

Coreference resolution over a JSONL sentence file (output of sentencize.py).
For each chunk of sentences, asks the LLM to identify entity mention clusters
and emit a JSONL record per chunk.

Each output record:
{
  "chunk_id": "1-20",
  "sentences": [1, 2, ..., 20],
  "entities": [
    {
      "label": "Irene Adler",
      "type": "person",
      "mentions": [
        {"sentence_id": 3, "span": "the woman", "confidence": 1.0},
        {"sentence_id": 7, "span": "she", "confidence": 0.9}
      ]
    }
  ]
}

Usage:
    python coref.py --input bohemia_sentences.jsonl --output bohemia_coref.jsonl
    python coref.py --input bohemia_sentences.jsonl --output bohemia_coref.jsonl \\
        --chunk-size 20 --overlap 3 --model qwen2.5:14b

Resume: if output file exists, already-processed chunk_ids are skipped.
"""

import argparse
import json
import os
import sys
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
DEFAULT_MODEL = "qwen2.5:14b"
DEFAULT_OLLAMA = "http://192.168.1.162:11434"
DEFAULT_CHUNK_SIZE = 20
DEFAULT_OVERLAP = 3
MAX_RETRIES = 3

SYSTEM = (
    "You are a coreference resolution engine for literary fiction. "
    "Output only JSON objects, one per line, no prose, no markdown fences. "
    "Each line must be a complete, valid JSON object. "
    "Never output empty objects like {}."
)

USER_TMPL = """\
You are analysing a passage from a Sherlock Holmes story for coreference.

CONTEXT (read-only — do NOT emit mentions from these sentence ids):
{context_block}

CHUNK (analyse every sentence here):
{chunk_block}

Find every person, place, and named object mentioned in the CHUNK. \
For each entity, collect ALL mentions: proper names, titles ("the King", \
"my client"), common nouns ("the woman", "the detective"), and pronouns \
("he", "she", "it", "they") when you can resolve them. \
Be exhaustive — it is better to over-include than to miss mentions.

Output one JSON object per line, no other text:
{{"label": "canonical name", "type": "person|place|object|organization|other", "mentions": [{{"sentence_id": N, "span": "exact phrase from text", "confidence": 0.0-1.0}}, ...]}}

Rules:
- Only use sentence_ids from the CHUNK (not CONTEXT).
- label must be non-empty — use the most specific name you can determine.
- confidence < 0.8 for genuinely ambiguous pronouns only.
- Do not output {{}}.
"""

def resolve_model(model: str, anthropic: bool) -> str:
    if anthropic:
        return ANTHROPIC_MODEL_MAP.get(model, model)
    return model


def load_sentences(path: Path) -> list[dict]:
    sentences = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                sentences.append(json.loads(line))
    sentences.sort(key=lambda s: s["id"])
    return sentences


def format_block(sentences: list[dict]) -> str:
    if not sentences:
        return "(none)"
    return "\n".join(f"[{s['id']}] {s['text']}" for s in sentences)


def read_done_chunks(path: Path) -> set[str]:
    if not path.exists():
        return set()
    done = set()
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    done.add(json.loads(line)["chunk_id"])
                except (json.JSONDecodeError, KeyError):
                    pass
    return done


def ollama_chat(system: str, user: str, model: str, base_url: str) -> str:
    url = f"{base_url.rstrip('/')}/api/chat"
    payload = {
        "model": model,
        "stream": False,
        "options": {"temperature": 0.0},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    resp = httpx.post(url, json=payload, timeout=180.0)
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
        timeout=180.0,
    )
    resp.raise_for_status()
    return resp.json()["content"][0]["text"]


def chat(system: str, user: str, model: str, anthropic: bool, base_url: str | None) -> str:
    if anthropic:
        return anthropic_chat(system, user, model)
    assert base_url is not None
    return ollama_chat(system, user, model, base_url)

def extract_json_objects(raw: str) -> list[dict]:
    """
    Extract all JSON objects from raw text using JSONDecoder.raw_decode.
    This correctly handles braces inside string values, pretty-printed objects,
    and objects embedded in surrounding prose.
    """
    decoder = json.JSONDecoder()
    objects = []
    i = 0
    while i < len(raw):
        # Skip to the next '{'
        start = raw.find("{", i)
        if start == -1:
            break
        try:
            obj, end_idx = decoder.raw_decode(raw, start)
            if isinstance(obj, dict):
                objects.append(obj)
            i = end_idx  # end_idx is absolute position in raw
        except json.JSONDecodeError:
            i = start + 1  # not a valid object here, advance past this '{'
    return objects


def parse_entity_response(raw: str, valid_ids: set[int]) -> list[dict]:
    entities = []

    for obj in extract_json_objects(raw):
        # Skip empty sentinel objects the model sometimes prepends
        if not obj:
            continue

        label = str(obj.get("label", "")).strip()
        entity_type = str(obj.get("type", "other")).strip()
        mentions_raw = obj.get("mentions", [])

        if not isinstance(mentions_raw, list):
            print(f"  [warn] malformed entity record: {obj}", file=sys.stderr)
            continue

        # Empty label means the model couldn't name the entity — skip silently
        if not label:
            continue

        mentions = []
        for m in mentions_raw:
            if not isinstance(m, dict):
                continue
            try:
                sid = int(m["sentence_id"])
                span = str(m["span"]).strip()
                confidence = float(m.get("confidence", 1.0))
            except (KeyError, ValueError, TypeError) as e:
                print(f"  [warn] bad mention ({e}): {m}", file=sys.stderr)
                continue

            if sid not in valid_ids:
                continue  # model leaked a context sentence
            if not span:
                continue

            mentions.append({
                "sentence_id": sid,
                "span": span,
                "confidence": round(confidence, 3),
            })

        if mentions:
            entities.append({
                "label": label,
                "type": entity_type,
                "mentions": mentions,
            })

    return entities


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


def chunk_id(chunk: list[dict]) -> str:
    return f"{chunk[0]['id']}-{chunk[-1]['id']}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def process_chunk(
    context_sents: list[dict],
    chunk_sents: list[dict],
    model: str,
    anthropic: bool,
    base_url: str | None,
    debug: bool = False,
) -> list[dict]:
    user = USER_TMPL.format(
        context_block=format_block(context_sents),
        chunk_block=format_block(chunk_sents),
    )
    valid_ids = {s["id"] for s in chunk_sents}

    for attempt in range(1, MAX_RETRIES + 1):
        raw = chat(SYSTEM, user, model, anthropic, base_url)
        if debug:
            print(f"\n  [debug] raw attempt {attempt}:\n{raw}\n", file=sys.stderr)
        objects = extract_json_objects(raw)
        # Non-empty objects only — {} doesn't count as a successful parse
        real_objects = [o for o in objects if o]
        if not real_objects and attempt < MAX_RETRIES:
            print(f"  [warn] no non-empty JSON objects on attempt {attempt}/{MAX_RETRIES}", file=sys.stderr)
            if not debug:
                print(f"  [debug] raw: {raw[:300]!r}", file=sys.stderr)
            continue
        entities = parse_entity_response(raw, valid_ids)
        if entities or real_objects:
            # Got entities, or valid non-empty JSON that filtered to nothing
            # (e.g. all-dialogue chunk with no resolvable entities) — both fine
            return entities
        print(f"  [warn] all records filtered on attempt {attempt}/{MAX_RETRIES}", file=sys.stderr)

    print("  [error] all retries exhausted", file=sys.stderr)
    return []


def main() -> None:
    parser = argparse.ArgumentParser(description="Coreference resolution → JSONL")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--ollama", default=DEFAULT_OLLAMA)
    parser.add_argument("--anthropic", action="store_true")
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    parser.add_argument("--overlap", type=int, default=DEFAULT_OVERLAP)
    parser.add_argument("--debug", action="store_true", help="Print full raw LLM output for every chunk")
    parser.add_argument("--only-chunk", type=int, default=None, metavar="N", help="Process only chunk N (1-based); useful for spot inspection")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    sentences = load_sentences(input_path)
    print(f"Loaded {len(sentences)} sentences from {input_path.name}")

    chunks = make_chunks(sentences, args.chunk_size, args.overlap)
    print(f"Generated {len(chunks)} chunks (size={args.chunk_size}, overlap={args.overlap})")

    done_chunks = read_done_chunks(output_path)
    if done_chunks:
        print(f"Resuming: {len(done_chunks)} chunks already processed")

    with output_path.open("a", encoding="utf-8") as out_fh:
        for i, (context, chunk) in enumerate(chunks, 1):
            if args.only_chunk is not None and i != args.only_chunk:
                continue

            cid = chunk_id(chunk)
            if cid in done_chunks and args.only_chunk is None:
                print(f"  [{i}/{len(chunks)}] chunk {cid} already done, skipping")
                continue

            print(
                f"  [{i}/{len(chunks)}] chunk {cid} "
                f"({len(chunk)} sentences, {len(context)} context) ...",
                end=" ", flush=True,
            )

            entities = process_chunk(
                context,
                chunk,
                args.model,
                args.anthropic,
                None if args.anthropic else args.ollama,
                debug=args.debug,
            )

            record = {
                "chunk_id": cid,
                "sentences": [s["id"] for s in chunk],
                "entities": entities,
            }
            out_fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            out_fh.flush()

            mention_count = sum(len(e["mentions"]) for e in entities)
            print(f"→ {len(entities)} entities, {mention_count} mentions")

    print(f"\nDone. Results in {output_path}")


if __name__ == "__main__":
    main()
