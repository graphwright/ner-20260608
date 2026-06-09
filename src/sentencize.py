"""
sentencize.py

Split a plain-text story into numbered sentences using an LLM, emitting JSONL.
Each output record: {"id": <int>, "para": <int>, "text": "<sentence>"}

Processes one paragraph per LLM call via the Ollama /api/chat endpoint.
No JSON mode — the model returns raw JSONL lines which we parse directly.

Usage:
    python sentencize.py --input bohemia.txt --output bohemia_sentences.jsonl
    python sentencize.py --input bohemia.txt --output bohemia_sentences.jsonl \\
        --model qwen2.5:14b --ollama http://192.168.1.162:11434

Resume: if output already exists, reads highest sentence id and para and
continues from the next unprocessed paragraph.
"""

import argparse
import json
import re
import sys
from pathlib import Path

import httpx

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MODEL = "qwen2.5:14b"
DEFAULT_OLLAMA = "http://192.168.1.162:11434"
MAX_RETRIES = 3

SYSTEM = (
    "You are a sentence splitter. "
    "Given a paragraph, output each sentence as a separate JSON object on its own line. "
    "Output ONLY JSON lines — no prose, no explanation, no markdown fences."
)

# One paragraph at a time. Keeping the prompt minimal reduces confabulation.
USER_TMPL = (
    'Split this paragraph into sentences. '
    'For each sentence output exactly: {{"id": N, "para": P, "text": "...verbatim..."}}\n'
    'id starts at {id_offset}, para is {para_num}.\n\n'
    '{paragraph}'
)

# ---------------------------------------------------------------------------
# Gutenberg stripping / paragraph extraction
# ---------------------------------------------------------------------------

GUTENBERG_HEADER_RE = re.compile(
    r"\*\*\* START OF (THE|THIS) PROJECT GUTENBERG.*?\*\*\*",
    re.IGNORECASE | re.DOTALL,
)
GUTENBERG_FOOTER_RE = re.compile(
    r"\*\*\* END OF (THE|THIS) PROJECT GUTENBERG.*",
    re.IGNORECASE | re.DOTALL,
)


def strip_gutenberg(text: str) -> str:
    text = GUTENBERG_HEADER_RE.split(text, maxsplit=1)[-1]
    text = GUTENBERG_FOOTER_RE.split(text, maxsplit=1)[0]
    return text.strip()


def extract_paragraphs(text: str) -> list[str]:
    raw = re.split(r"\n{2,}", text)
    paras = []
    for p in raw:
        p = p.strip()
        if not p:
            continue
        # Drop all-caps chapter/section headers
        if re.fullmatch(r"[A-Z\s.\-IVX]+", p) and len(p) < 60:
            continue
        paras.append(p)
    return paras


# ---------------------------------------------------------------------------
# Ollama chat client
# ---------------------------------------------------------------------------

def chat(system: str, user: str, model: str, base_url: str) -> str:
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
    resp = httpx.post(url, json=payload, timeout=120.0)
    resp.raise_for_status()
    return resp.json()["message"]["content"]


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_response(raw: str, para_num: int) -> list[dict]:
    """
    Extract sentence records from raw LLM output.
    Handles:
      - Clean JSONL (one object per line)
      - Objects embedded in prose lines
      - Numbered list lines like: 1. {"id": ...}
    """
    records = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("```"):
            continue

        # Strip leading list markers: "1. " or "- "
        line = re.sub(r"^\d+\.\s+", "", line)
        line = re.sub(r"^-\s+", "", line)

        obj = None
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            # Try to extract a JSON object substring
            m = re.search(r'\{[^{}]+\}', line)
            if m:
                try:
                    obj = json.loads(m.group())
                except json.JSONDecodeError:
                    pass

        if obj is None:
            if line:
                print(f"  [skip] {line[:80]}", file=sys.stderr)
            continue

        try:
            obj["id"]   = int(obj["id"])
            obj["para"] = int(obj.get("para", para_num))
            obj["text"] = str(obj["text"]).strip()
        except (KeyError, ValueError) as e:
            print(f"  [warn] bad record ({e}): {obj}", file=sys.stderr)
            continue

        if obj["text"]:
            records.append(obj)

    return records


# ---------------------------------------------------------------------------
# Resume support
# ---------------------------------------------------------------------------

def read_existing_output(path: Path) -> tuple[int, int]:
    """Return (max_sentence_id, max_para_num) from existing JSONL, or (0, 0)."""
    if not path.exists():
        return 0, 0
    max_id = max_para = 0
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                max_id   = max(max_id,   int(obj.get("id",   0)))
                max_para = max(max_para, int(obj.get("para", 0)))
            except (json.JSONDecodeError, ValueError):
                pass
    return max_id, max_para


# ---------------------------------------------------------------------------
# Per-paragraph processing
# ---------------------------------------------------------------------------

def process_paragraph(
    paragraph: str,
    para_num: int,
    id_offset: int,
    model: str,
    base_url: str,
) -> list[dict]:
    user = USER_TMPL.format(
        id_offset=id_offset,
        para_num=para_num,
        paragraph=paragraph,
    )
    for attempt in range(1, MAX_RETRIES + 1):
        raw = chat(SYSTEM, user, model, base_url)
        records = parse_response(raw, para_num)
        if records:
            # Re-sequence ids from id_offset in case model drifted
            for i, rec in enumerate(records):
                rec["id"] = id_offset + i
            return records
        print(f"  [warn] empty parse attempt {attempt}/{MAX_RETRIES}", file=sys.stderr)
        if attempt < MAX_RETRIES:
            print(f"  [debug] raw output was: {raw[:200]!r}", file=sys.stderr)
    print(f"  [error] giving up on para {para_num}", file=sys.stderr)
    return []


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="LLM sentence splitter → JSONL")
    parser.add_argument("--input",     required=True)
    parser.add_argument("--output",    required=True)
    parser.add_argument("--model",     default=DEFAULT_MODEL)
    parser.add_argument("--ollama",    default=DEFAULT_OLLAMA)
    parser.add_argument("--gutenberg", action="store_true",
                        help="Strip Project Gutenberg header/footer")
    args = parser.parse_args()

    input_path  = Path(args.input)
    output_path = Path(args.output)

    raw_text = input_path.read_text(encoding="utf-8")
    if args.gutenberg:
        raw_text = strip_gutenberg(raw_text)

    paragraphs = extract_paragraphs(raw_text)
    print(f"Extracted {len(paragraphs)} paragraphs from {input_path.name}")

    max_id, max_para = read_existing_output(output_path)
    start_idx = max_para          # paragraphs already done (para numbers are 1-based)
    id_cursor = max_id
    if start_idx:
        print(f"Resuming after para {max_para}, sentence id {max_id}")

    with output_path.open("a", encoding="utf-8") as out_fh:
        for i, para in enumerate(paragraphs[start_idx:], start=start_idx + 1):
            print(f"  para {i:>3}/{len(paragraphs)}  sid={id_cursor+1:>4} ...",
                  end=" ", flush=True)

            records = process_paragraph(
                paragraph=para,
                para_num=i,
                id_offset=id_cursor + 1,
                model=args.model,
                base_url=args.ollama,
            )

            for rec in records:
                out_fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out_fh.flush()

            id_cursor += len(records)
            print(f"→ {len(records)} sentences  (total {id_cursor})")

    print(f"\nDone. {id_cursor} sentences written to {output_path}")


if __name__ == "__main__":
    main()
