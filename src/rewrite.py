"""
rewrite.py

ID normalization pass — rewrites two classes of opaque IDs across the
Bohemia pipeline JSONL files:

1. Raw Baker Street wiki URLs  →  canonical wiki: IDs
   https://bakerstreet.fandom.com/wiki/Sherlock_Holmes
       →  wiki:Sherlock_Holmes

2. Provisional numeric IDs  →  slug-based IDs from canonical name + type
   provisional:7  (object, "Holmes's cigarette")
       →  obj:holmess_cigarette

Both mappings are derived entirely from bohemia_entities.jsonl, so the
rewrite is deterministic and requires no LLM calls.

Fields rewritten in bohemia_entities.jsonl:
    entity_id

Fields rewritten in bohemia_triplets.jsonl:
    subject_id, object_id, asserting_narrator_id
    id  (recomputed as stmt:{subject_id}:{predicate}:{object_id})

Usage:
    python rewrite.py
    python rewrite.py --entities bohemia_entities.jsonl \\
                      --triplets bohemia_triplets.jsonl
    python rewrite.py --entities-out entities_new.jsonl \\
                      --triplets-out triplets_new.jsonl
"""

import argparse
import json
import re
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Slug generation
# ---------------------------------------------------------------------------

_WIKI_PREFIX = "https://bakerstreet.fandom.com/wiki/"

_TYPE_PREFIX: dict[str, str] = {
    "person":       "person",
    "place":        "place",
    "object":       "obj",
    "organization": "org",
    "other":        "entity",
}


def _make_slug(canonical: str, ner_type: str) -> str:
    prefix = _TYPE_PREFIX.get(ner_type.lower(), "entity")
    s = canonical.lower()
    s = s.replace(" / ", "_or_")
    s = s.replace(" & ", "_and_")
    s = s.replace("&", "_and_")
    s = re.sub(r"['\",.]", "", s)
    s = re.sub(r"[\s/:]+", "_", s)
    s = re.sub(r"[^a-z0-9_\-]", "", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return f"{prefix}:{s}"


# ---------------------------------------------------------------------------
# Build remap table
# ---------------------------------------------------------------------------

def build_remap(entity_records: list[dict]) -> dict[str, str]:
    """Return a mapping from old ID → new ID for every entity that needs one.

    Two cases:
    - provisional:N  →  slug derived from canonical name + type
    - wiki_url field  →  entity_id  (for entities already canonicalized by the
      merge pass; their raw URLs still appear in triplet subject/object fields)
    """
    remap: dict[str, str] = {}
    seen_slugs: dict[str, int] = {}  # track collisions

    for rec in entity_records:
        entity_id = rec.get("entity_id") or rec.get("id", "")

        # Already-linked entities: map their raw wiki_url → canonical entity_id
        wiki_url = rec.get("wiki_url")
        if wiki_url and wiki_url not in remap:
            remap[wiki_url] = entity_id

        # provisional:N → slug
        if entity_id.startswith("provisional:"):
            canonical = rec.get("canonical", entity_id)
            ner_type = rec.get("type", "other")
            base_slug = _make_slug(canonical, ner_type)

            count = seen_slugs.get(base_slug, 0) + 1
            seen_slugs[base_slug] = count
            new_id = base_slug if count == 1 else f"{base_slug}_{count}"

            remap[entity_id] = new_id

    return remap


# ---------------------------------------------------------------------------
# Apply remap
# ---------------------------------------------------------------------------

def _remap_id(value: str | None, remap: dict[str, str]) -> str | None:
    if value is None:
        return None
    return remap.get(value, value)


def rewrite_entity(rec: dict, remap: dict[str, str]) -> dict:
    out = dict(rec)
    if "entity_id" in out:
        out["entity_id"] = _remap_id(out["entity_id"], remap)
    elif "id" in out:
        out["id"] = _remap_id(out["id"], remap)
    return out


def rewrite_triplet(rec: dict, remap: dict[str, str]) -> dict:
    out = dict(rec)
    out["subject_id"] = _remap_id(out.get("subject_id"), remap)
    out["object_id"] = _remap_id(out.get("object_id"), remap)
    if "asserting_narrator_id" in out:
        out["asserting_narrator_id"] = _remap_id(out["asserting_narrator_id"], remap)
    # Recompute statement ID from (possibly updated) subject/predicate/object
    pred = out.get("predicate", "")
    subj = out.get("subject_id", "")
    obj = out.get("object_id", "")
    out["id"] = f"stmt:{subj}:{pred}:{obj}"
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize entity IDs in Bohemia JSONL files.")
    parser.add_argument("--entities", default="bohemia_entities.jsonl", type=Path)
    parser.add_argument("--triplets", default="bohemia_triplets.jsonl", type=Path)
    parser.add_argument("--entities-out", default=None, type=Path,
                        help="Output entities file (default: overwrite --entities)")
    parser.add_argument("--triplets-out", default=None, type=Path,
                        help="Output triplets file (default: overwrite --triplets)")
    args = parser.parse_args()

    entities_out = args.entities_out or args.entities
    triplets_out = args.triplets_out or args.triplets

    with args.entities.open(encoding="utf-8") as fh:
        entity_records = [json.loads(l) for l in fh if l.strip()]

    with args.triplets.open(encoding="utf-8") as fh:
        triplet_records = [json.loads(l) for l in fh if l.strip()]

    remap = build_remap(entity_records)
    print(f"Remap table: {len(remap)} entries", file=sys.stderr)

    new_entities = [rewrite_entity(r, remap) for r in entity_records]
    new_triplets = [rewrite_triplet(r, remap) for r in triplet_records]

    # Stats
    e_changed = sum(1 for a, b in zip(entity_records, new_entities) if a != b)
    t_changed = sum(1 for a, b in zip(triplet_records, new_triplets) if a != b)
    print(f"Entities changed: {e_changed}/{len(new_entities)}", file=sys.stderr)
    print(f"Triplets changed: {t_changed}/{len(new_triplets)}", file=sys.stderr)

    with entities_out.open("w", encoding="utf-8") as fh:
        for r in new_entities:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    with triplets_out.open("w", encoding="utf-8") as fh:
        for r in new_triplets:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"Wrote {len(new_entities)} entities → {entities_out}", file=sys.stderr)
    print(f"Wrote {len(new_triplets)} triplets → {triplets_out}", file=sys.stderr)


if __name__ == "__main__":
    main()
