"""Tests for rewrite.py — ID normalization pass."""

import json
import pytest

from rewrite import (
    _make_slug,
    build_remap,
    rewrite_entity,
    rewrite_triplet,
)


class TestMakeSlug:
    def test_person(self):
        assert _make_slug("Mrs. Turner", "person") == "person:mrs_turner"

    def test_place(self):
        assert _make_slug("Baker Street", "place") == "place:baker_street"

    def test_object(self):
        assert _make_slug("Holmes's index", "object") == "obj:holmess_index"

    def test_slash_becomes_or(self):
        assert _make_slug("sofa / couch", "object") == "obj:sofa_or_couch"

    def test_ampersand_becomes_and(self):
        assert _make_slug("Gross & Hankeys", "place") == "place:gross_and_hankeys"

    def test_hyphen_preserved(self):
        assert _make_slug("note-book", "object") == "obj:note-book"

    def test_unknown_type_becomes_entity(self):
        assert _make_slug("Something", "other").startswith("entity:")

    def test_organization(self):
        assert _make_slug("Gesellschaft", "organization") == "org:gesellschaft"

    def test_colon_in_name(self):
        slug = _make_slug("5:15 train", "object")
        assert ":" not in slug.split(":", 1)[1]  # no colon after prefix

    def test_no_double_underscores(self):
        slug = _make_slug("A   B", "object")
        assert "__" not in slug


class TestBuildRemap:
    def test_provisional_mapped_to_slug(self):
        records = [{"entity_id": "provisional:1", "canonical": "Baker Street", "type": "place"}]
        remap = build_remap(records)
        assert remap["provisional:1"] == "place:baker_street"

    def test_wiki_url_mapped_via_wiki_url_field(self):
        records = [{
            "entity_id": "wiki:Sherlock_Holmes",
            "canonical": "Sherlock Holmes",
            "type": "person",
            "wiki_url": "https://bakerstreet.fandom.com/wiki/Sherlock_Holmes",
        }]
        remap = build_remap(records)
        assert remap["https://bakerstreet.fandom.com/wiki/Sherlock_Holmes"] == "wiki:Sherlock_Holmes"

    def test_already_canonical_entity_not_remapped(self):
        records = [{"entity_id": "wiki:Holmes", "canonical": "Holmes", "type": "person"}]
        remap = build_remap(records)
        assert "wiki:Holmes" not in remap

    def test_collision_resolved_with_suffix(self):
        records = [
            {"entity_id": "provisional:1", "canonical": "Baker Street", "type": "place"},
            {"entity_id": "provisional:2", "canonical": "Baker Street", "type": "place"},
        ]
        remap = build_remap(records)
        assert remap["provisional:1"] == "place:baker_street"
        assert remap["provisional:2"] == "place:baker_street_2"

    def test_mixed_records(self):
        records = [
            {"entity_id": "wiki:Holmes", "canonical": "Sherlock Holmes", "type": "person",
             "wiki_url": "https://bakerstreet.fandom.com/wiki/Sherlock_Holmes"},
            {"entity_id": "provisional:7", "canonical": "Holmes's cigarette", "type": "object"},
        ]
        remap = build_remap(records)
        assert "https://bakerstreet.fandom.com/wiki/Sherlock_Holmes" in remap
        assert "provisional:7" in remap
        assert "wiki:Holmes" not in remap


class TestRewriteEntity:
    def test_provisional_entity_id_rewritten(self):
        rec = {"entity_id": "provisional:1", "canonical": "Baker Street", "type": "place"}
        remap = {"provisional:1": "place:baker_street"}
        out = rewrite_entity(rec, remap)
        assert out["entity_id"] == "place:baker_street"

    def test_canonical_entity_unchanged(self):
        rec = {"entity_id": "wiki:Holmes", "canonical": "Sherlock Holmes"}
        remap = {}
        out = rewrite_entity(rec, remap)
        assert out["entity_id"] == "wiki:Holmes"

    def test_other_fields_preserved(self):
        rec = {"entity_id": "provisional:1", "canonical": "Baker Street",
               "type": "place", "wiki_url": None}
        remap = {"provisional:1": "place:baker_street"}
        out = rewrite_entity(rec, remap)
        assert out["canonical"] == "Baker Street"
        assert out["type"] == "place"

    def test_id_field_fallback(self):
        rec = {"id": "provisional:1", "canonical": "Baker Street"}
        remap = {"provisional:1": "place:baker_street"}
        out = rewrite_entity(rec, remap)
        assert out["id"] == "place:baker_street"


class TestRewriteTriplet:
    def _base(self):
        return {
            "id": "stmt:https://bakerstreet.fandom.com/wiki/Sherlock_Holmes:Possesses:provisional:7",
            "predicate": "Possesses",
            "subject_id": "https://bakerstreet.fandom.com/wiki/Sherlock_Holmes",
            "object_id": "provisional:7",
            "asserting_narrator_id": "https://bakerstreet.fandom.com/wiki/John_Watson",
            "truth_status": "asserted_true",
        }

    def _remap(self):
        return {
            "https://bakerstreet.fandom.com/wiki/Sherlock_Holmes": "wiki:Sherlock_Holmes",
            "https://bakerstreet.fandom.com/wiki/John_Watson": "wiki:John_Watson",
            "provisional:7": "obj:holmess_cigarette",
        }

    def test_subject_id_rewritten(self):
        out = rewrite_triplet(self._base(), self._remap())
        assert out["subject_id"] == "wiki:Sherlock_Holmes"

    def test_object_id_rewritten(self):
        out = rewrite_triplet(self._base(), self._remap())
        assert out["object_id"] == "obj:holmess_cigarette"

    def test_narrator_id_rewritten(self):
        out = rewrite_triplet(self._base(), self._remap())
        assert out["asserting_narrator_id"] == "wiki:John_Watson"

    def test_id_recomputed(self):
        out = rewrite_triplet(self._base(), self._remap())
        assert out["id"] == "stmt:wiki:Sherlock_Holmes:Possesses:obj:holmess_cigarette"

    def test_truth_status_preserved(self):
        out = rewrite_triplet(self._base(), self._remap())
        assert out["truth_status"] == "asserted_true"

    def test_unmapped_ids_unchanged(self):
        rec = {
            "id": "stmt:sib:event:1:Involves:wiki:Holmes",
            "predicate": "Involves",
            "subject_id": "sib:event:1",
            "object_id": "wiki:Holmes",
            "asserting_narrator_id": "wiki:Watson",
        }
        out = rewrite_triplet(rec, {})
        assert out["subject_id"] == "sib:event:1"
        assert out["object_id"] == "wiki:Holmes"


class TestRewriteCLI:
    def test_roundtrip(self, tmp_path):
        import subprocess, sys

        entities_in = tmp_path / "entities.jsonl"
        triplets_in = tmp_path / "triplets.jsonl"
        entities_out = tmp_path / "entities_out.jsonl"
        triplets_out = tmp_path / "triplets_out.jsonl"

        entities_in.write_text(json.dumps({
            "entity_id": "provisional:1", "canonical": "Baker Street",
            "type": "place", "wiki_url": None,
        }) + "\n")
        triplets_in.write_text(json.dumps({
            "id": "stmt:provisional:1:AssociatedWith:provisional:1",
            "predicate": "AssociatedWith",
            "subject_id": "provisional:1",
            "object_id": "provisional:1",
            "asserting_narrator_id": None,
            "truth_status": "asserted_true",
        }) + "\n")

        result = subprocess.run(
            [sys.executable, "src/rewrite.py",
             "--entities", str(entities_in),
             "--triplets", str(triplets_in),
             "--entities-out", str(entities_out),
             "--triplets-out", str(triplets_out)],
            capture_output=True, text=True,
            cwd="/Users/wware/tmp/ner-20260608",
        )
        assert result.returncode == 0

        e = json.loads(entities_out.read_text())
        t = json.loads(triplets_out.read_text())
        assert e["entity_id"] == "place:baker_street"
        assert t["subject_id"] == "place:baker_street"
        assert t["id"] == "stmt:place:baker_street:AssociatedWith:place:baker_street"
