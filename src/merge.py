"""
merge.py

Three-pass merge of chunk-level coreference output (bohemia_coref.jsonl)
into a global entity table (bohemia_entities.jsonl).

Pass 1 — Label clustering (Claude API):
    Collect all unique entity labels across chunks. Send the full label list
    to Claude in a single API call. Claude's world-knowledge of Holmes canon
    handles circumlocutions ("His Majesty", "the King", "Wilhelm Gottsreich")
    correctly without needing batching for a single story.

Pass 2 — Wiki linking (Baker Street Fandom opensearch + Claude judgment):
    For each canonical entity, fetch up to 5 opensearch candidates from the
    Baker Street Wiki. Ask Claude to judge which candidate (if any) is the
    correct article — with an explicit "none" option. This eliminates spurious
    matches from loose string similarity. Unlinked entities get provisional:<n>.

Pass 3 — Mention rewriting:
    Flatten all mentions from coref output, resolve to canonical entity,
    emit one JSONL record per mention (bohemia_mentions.jsonl).

Output files:
    bohemia_entities.jsonl   — one record per canonical entity
    bohemia_mentions.jsonl   — one record per mention (flat, with entity_id)

Environment:
    ANTHROPIC_API_KEY   required

Usage:
    python merge.py --coref bohemia_coref.jsonl \
                    --entities bohemia_entities.jsonl \
                    --mentions bohemia_mentions.jsonl

    python merge.py --coref bohemia_coref.jsonl \
                    --entities bohemia_entities.jsonl \
                    --mentions bohemia_mentions.jsonl \
                    --skip-wiki
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed; rely on environment directly

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL      = "claude-sonnet-4-20250514"
WIKI_SEARCH_URL   = "https://bakerstreet.fandom.com/api.php"
WIKI_RATE_LIMIT   = 0.4      # seconds between wiki API calls
MAX_RETRIES       = 3
WIKI_JUDGE_BATCH  = 10       # entities per wiki-judgment API call

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
    data = resp.json()
    return data["content"][0]["text"]


# ---------------------------------------------------------------------------
# JSON extraction (raw_decode scanner — same as coref.py)
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

def load_coref(path: Path) -> list[dict]:
    records = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def collect_unique_labels(coref_records: list[dict]) -> list[str]:
    seen: set[str] = set()
    for rec in coref_records:
        for ent in rec.get("entities", []):
            label = ent.get("label", "").strip()
            if label:
                seen.add(label)
    return sorted(seen)


# ---------------------------------------------------------------------------
# Pass 1 — label clustering via Claude
# ---------------------------------------------------------------------------

CLUSTER_SYSTEM = """\
You are an expert on Sherlock Holmes fiction, specialising in entity resolution.
Output only JSON objects, one per line. No prose, no markdown fences, no empty objects.
Every line must be a complete, valid JSON object.
"""

CLUSTER_USER_TMPL = """\
The following labels were extracted from "A Scandal in Bohemia" by Conan Doyle.
Many refer to the same character, place, or object using different surface forms.

Group every label that refers to the same entity into one cluster.
For each cluster output one JSON object on its own line:
{{"canonical": "most specific proper name available", "aliases": ["every", "label", "in", "this", "group"], "type": "person|place|object|organization|other"}}

Rules:
- Every input label must appear in exactly one group (in canonical or aliases).
- canonical must be the most specific, proper name available in the group.
  If no proper name exists, use the most informative descriptive label.
- Do not invent any label not present in the input list.
- Merge pronouns ("he", "she", "I", "his") only when the referent is
  unambiguous given the full label list.
- Do not output {{}}.

Labels:
{labels}
"""


def cluster_labels_claude(labels: list[str]) -> list[dict]:
    """
    Send all unique labels to Claude in a single call.
    For a single story the label count is small enough that batching is
    unnecessary and would introduce cross-batch consistency problems.
    Falls back to singleton clusters for any label Claude omits.
    """
    label_block = "\n".join(f"- {l}" for l in labels)
    user = CLUSTER_USER_TMPL.format(labels=label_block)

    print(f"  Sending {len(labels)} labels to Claude for clustering ...", end=" ", flush=True)

    raw = ""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            raw = claude(CLUSTER_SYSTEM, user, max_tokens=8192)
            break
        except Exception as e:
            print(f"[error attempt {attempt}: {e}]", end=" ", flush=True)
            if attempt == MAX_RETRIES:
                raise
            time.sleep(2 ** attempt)

    clusters: list[dict] = []
    assigned: set[str] = set()

    for obj in extract_json_objects(raw):
        canonical   = str(obj.get("canonical", "")).strip()
        aliases     = [str(a).strip() for a in obj.get("aliases", []) if str(a).strip()]
        entity_type = str(obj.get("type", "other")).strip()

        if not canonical:
            print(f"\n  [warn] cluster missing canonical: {obj}", file=sys.stderr)
            continue
        if canonical not in aliases:
            aliases.append(canonical)

        # Deduplicate: drop aliases already claimed by a prior cluster
        aliases = [a for a in aliases if a not in assigned]
        if not aliases:
            continue

        assigned.update(aliases)
        clusters.append({"canonical": canonical, "aliases": aliases, "type": entity_type})

    # Safety net: any label Claude dropped becomes a singleton
    for label in labels:
        if label not in assigned:
            print(f"\n  [warn] label not covered by Claude, adding singleton: {label!r}",
                  file=sys.stderr)
            clusters.append({"canonical": label, "aliases": [label], "type": "other"})

    print(f"-> {len(clusters)} clusters")
    return clusters


# ---------------------------------------------------------------------------
# Pass 2 — wiki linking with Claude judgment
# ---------------------------------------------------------------------------

WIKI_JUDGE_SYSTEM = """\
You are an expert on Sherlock Holmes fiction and the Baker Street Wiki (bakerstreet.fandom.com).
Output only JSON objects, one per line. No prose, no markdown fences.
"""

WIKI_JUDGE_USER_TMPL = """\
For each entity below, I fetched candidate articles from the Baker Street Wiki.
Decide which candidate (if any) is the correct article for that entity as it
appears in "A Scandal in Bohemia". Output one JSON object per entity:
{{"entity": "canonical name", "wiki_url": "URL or null", "wiki_title": "title or null"}}

Set wiki_url and wiki_title to null if none of the candidates is correct.
Do not guess. Only link when you are confident it is the right Baker Street Wiki article.

Entities and candidates:
{entities_block}
"""


def wiki_search_candidates(query: str) -> list[tuple[str, str]]:
    """Return up to 5 (title, url) pairs from Baker Street opensearch."""
    try:
        resp = httpx.get(
            WIKI_SEARCH_URL,
            params={
                "action": "opensearch",
                "search": query,
                "limit": 5,
                "namespace": 0,
                "format": "json",
            },
            timeout=10.0,
            headers={"User-Agent": "graphwright-ner-pipeline/1.0"},
        )
        resp.raise_for_status()
        data = resp.json()
        titles = data[1] if len(data) > 1 else []
        urls   = data[3] if len(data) > 3 else []
        return list(zip(titles, urls))
    except Exception as e:
        print(f"\n  [warn] wiki search failed for {query!r}: {e}", file=sys.stderr)
        return []


def assign_wiki_urls_claude(clusters: list[dict]) -> list[dict]:
    """
    For each cluster warranting a wiki lookup, fetch opensearch candidates then
    ask Claude to judge. Batches judgment calls to reduce API round-trips.
    """
    lookup_types      = {"person", "place", "organization"}
    provisional_counter = 1

    # Split clusters into those needing lookup and those that don't
    pending:            list[dict]                          = []
    no_lookup:          list[dict]                          = []
    entity_candidates:  dict[str, list[tuple[str, str]]]    = {}

    print("  Fetching wiki candidates ...")
    for cluster in clusters:
        if cluster["type"] not in lookup_types:
            no_lookup.append(cluster)
            continue

        canonical  = cluster["canonical"]
        candidates = wiki_search_candidates(canonical)
        time.sleep(WIKI_RATE_LIMIT)

        # Fallback: try longest alias if canonical search returned nothing
        if not candidates:
            best_alias = max(
                (a for a in cluster["aliases"] if a != canonical),
                key=len, default=None,
            )
            if best_alias:
                candidates = wiki_search_candidates(best_alias)
                time.sleep(WIKI_RATE_LIMIT)

        entity_candidates[canonical] = candidates
        pending.append(cluster)

    # Assign provisional IDs to non-lookup types immediately
    for cluster in no_lookup:
        cluster["wiki_url"]  = None
        cluster["entity_id"] = f"provisional:{provisional_counter}"
        provisional_counter += 1

    # Batch pending clusters into Claude judgment calls
    judgments: dict[str, tuple[str | None, str | None]] = {}

    for batch_start in range(0, len(pending), WIKI_JUDGE_BATCH):
        batch = pending[batch_start : batch_start + WIKI_JUDGE_BATCH]
        lines = []
        for cluster in batch:
            canonical = cluster["canonical"]
            cands     = entity_candidates.get(canonical, [])
            cand_str  = (
                "; ".join(f'"{t}" -> {u}' for t, u in cands)
                if cands else "(no candidates found)"
            )
            lines.append(f'- "{canonical}": {cand_str}')

        user = WIKI_JUDGE_USER_TMPL.format(entities_block="\n".join(lines))

        print(
            f"  Asking Claude to judge wiki candidates "
            f"({batch_start + 1}-{batch_start + len(batch)} of {len(pending)}) ...",
            end=" ", flush=True,
        )

        raw = ""
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                raw = claude(WIKI_JUDGE_SYSTEM, user, max_tokens=2048)
                break
            except Exception as e:
                print(f"[error attempt {attempt}: {e}]", end=" ", flush=True)
                if attempt == MAX_RETRIES:
                    raise
                time.sleep(2 ** attempt)

        accepted = 0
        for obj in extract_json_objects(raw):
            entity = str(obj.get("entity", "")).strip()
            url    = obj.get("wiki_url")
            title  = obj.get("wiki_title")
            if entity:
                resolved_url   = str(url).strip()   if url   and url   != "null" else None
                resolved_title = str(title).strip() if title and title != "null" else None
                judgments[entity] = (resolved_url, resolved_title)
                if resolved_url:
                    accepted += 1

        print(f"-> {accepted}/{len(batch)} linked")

    # Apply judgments back to clusters
    for cluster in pending:
        canonical    = cluster["canonical"]
        url, _title  = judgments.get(canonical, (None, None))

        if url:
            slug = url.rstrip("/").split("/wiki/")[-1]
            cluster["wiki_url"]  = url
            cluster["entity_id"] = f"wiki:{slug}"
        else:
            cluster["wiki_url"]  = None
            cluster["entity_id"] = f"provisional:{provisional_counter}"
            provisional_counter += 1

        print(f"  {canonical:45s} -> {cluster['entity_id']}")

    return clusters


# ---------------------------------------------------------------------------
# Pass 3 — mention rewriting
# ---------------------------------------------------------------------------

def build_alias_index(clusters: list[dict]) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for cluster in clusters:
        for alias in cluster["aliases"]:
            index[alias] = cluster
    return index


def rewrite_mentions(
    coref_records: list[dict],
    alias_index:   dict[str, dict],
    mentions_path: Path,
) -> None:
    unresolved: set[str] = set()
    count = 0

    with mentions_path.open("w", encoding="utf-8") as fh:
        for rec in coref_records:
            chunk_id = rec["chunk_id"]
            for ent in rec.get("entities", []):
                raw_label = ent.get("label", "").strip()
                cluster   = alias_index.get(raw_label)

                if cluster is None:
                    unresolved.add(raw_label)
                    entity_id   = None
                    canonical   = raw_label
                    wiki_url    = None
                    entity_type = ent.get("type", "other")
                else:
                    entity_id   = cluster["entity_id"]
                    canonical   = cluster["canonical"]
                    wiki_url    = cluster["wiki_url"]
                    entity_type = cluster["type"]

                for mention in ent.get("mentions", []):
                    record = {
                        "entity_id":   entity_id,
                        "canonical":   canonical,
                        "wiki_url":    wiki_url,
                        "type":        entity_type,
                        "raw_label":   raw_label,
                        "sentence_id": mention["sentence_id"],
                        "span":        mention["span"],
                        "confidence":  mention.get("confidence", 1.0),
                        "chunk_id":    chunk_id,
                    }
                    fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                    count += 1

    if unresolved:
        print(
            f"  [warn] {len(unresolved)} labels not in cluster index "
            f"(written with entity_id=null):",
            file=sys.stderr,
        )
        for u in sorted(unresolved):
            print(f"    - {u}", file=sys.stderr)

    print(f"  Written {count} mention records to {mentions_path}")


# ---------------------------------------------------------------------------
# Post-link deduplication
# ---------------------------------------------------------------------------

def dedup_by_entity_id(clusters: list[dict]) -> list[dict]:
    """
    After wiki linking, multiple clusters may resolve to the same entity_id
    (e.g. "Dr Watson" and "John" both link to wiki:John_Watson).
    Merge them: union aliases, keep the longer/more-specific canonical,
    keep the wiki_url, emit one record per entity_id.
    Provisional IDs are unique by construction so they never collide.
    """
    import sys
    from collections import defaultdict
    groups: dict[str, list[dict]] = defaultdict(list)
    for c in clusters:
        groups[c["entity_id"]].append(c)

    merged = []
    for entity_id, group in groups.items():
        if len(group) == 1:
            merged.append(group[0])
            continue
        # Keep canonical with most words (most specific name)
        best = max(group, key=lambda c: len(c["canonical"].split()))
        all_aliases: list[str] = []
        seen_aliases: set[str] = set()
        for c in group:
            for a in c.get("aliases", []):
                if a not in seen_aliases:
                    all_aliases.append(a)
                    seen_aliases.add(a)
        if best["canonical"] not in seen_aliases:
            all_aliases.append(best["canonical"])
        print(
            f"  [dedup] merged {len(group)} clusters -> {entity_id}: "
            f"{[c['canonical'] for c in group]}",
            file=sys.stderr,
        )
        merged.append({
            "canonical":  best["canonical"],
            "aliases":    all_aliases,
            "type":       best["type"],
            "wiki_url":   best.get("wiki_url"),
            "entity_id":  entity_id,
        })
    return merged


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Merge coref chunks -> global entity table (Claude API)"
    )
    parser.add_argument("--coref",     required=True, help="Input: bohemia_coref.jsonl")
    parser.add_argument("--entities",  required=True, help="Output: entity table JSONL")
    parser.add_argument("--mentions",  required=True, help="Output: flat mention JSONL")
    parser.add_argument("--skip-wiki", action="store_true",
                        help="Skip wiki lookup; assign provisional IDs to all entities")
    args = parser.parse_args()

    coref_path    = Path(args.coref)
    entities_path = Path(args.entities)
    mentions_path = Path(args.mentions)

    # ---- Pass 1: cluster via Claude ------------------------------------
    print("\n=== Pass 1: Label clustering (Claude API) ===")
    coref_records = load_coref(coref_path)
    print(f"Loaded {len(coref_records)} chunk records from {coref_path.name}")

    unique_labels = collect_unique_labels(coref_records)
    print(f"Found {len(unique_labels)} unique entity labels")

    clusters = cluster_labels_claude(unique_labels)
    print(f"Produced {len(clusters)} entity clusters")

    # ---- Pass 2: wiki linking via Claude judgment ----------------------
    print("\n=== Pass 2: Wiki linking (opensearch + Claude judgment) ===")
    if args.skip_wiki:
        print("  (skipped -- assigning provisional IDs to all entities)")
        for i, cluster in enumerate(clusters, 1):
            cluster["wiki_url"]  = None
            cluster["entity_id"] = f"provisional:{i}"
    else:
        clusters = assign_wiki_urls_claude(clusters)

    wiki_count = sum(1 for c in clusters if c.get("wiki_url"))
    prov_count = sum(1 for c in clusters if not c.get("wiki_url"))
    print(f"  Wiki-linked: {wiki_count}  Provisional: {prov_count}")

    # ---- Post-link dedup: merge clusters sharing the same entity_id ----
    clusters = dedup_by_entity_id(clusters)
    print(f"  After dedup: {len(clusters)} entities")

    with entities_path.open("w", encoding="utf-8") as fh:
        for cluster in clusters:
            fh.write(json.dumps(cluster, ensure_ascii=False) + "\n")
    print(f"  Entity table written to {entities_path}")

    # ---- Pass 3: mention rewriting ------------------------------------
    print("\n=== Pass 3: Mention rewriting ===")
    alias_index = build_alias_index(clusters)
    rewrite_mentions(coref_records, alias_index, mentions_path)

    print("\nDone.")
    print(f"  Entities : {entities_path}")
    print(f"  Mentions : {mentions_path}")


if __name__ == "__main__":
    main()
