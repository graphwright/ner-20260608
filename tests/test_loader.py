"""Tests for loader.py — JSONL hydration layer."""

import pytest

from ner_20260608.holmes_schema import (
    TruthStatus, Person, Location, Object, Event, Moment,
    Knows, LocatedIn, Contradicts, KnewAt,
)
from ner_20260608.loader import (
    _entity_class,
    _predicate_class,
    _is_higher_order,
    _truth_status,
    _hydrate_entities,
    _hydrate_events,
    _hydrate_moments,
    _hydrate_triplets,
    InstanceSet,
    load_instances,
)
from ner_20260608 import data_path


_PROV = dict(
    story_id="bohemia",
    paragraph_index=1,
    extraction_method="manual",
    extraction_confidence=1.0,
)


class TestEntityClass:
    def test_person(self):
        assert _entity_class("person") is Person

    def test_place(self):
        assert _entity_class("place") is Location

    def test_organization(self):
        assert _entity_class("organization") is Location

    def test_other(self):
        assert _entity_class("other") is Object

    def test_unknown_defaults_to_object(self):
        assert _entity_class("alien") is Object

    def test_case_insensitive(self):
        assert _entity_class("PERSON") is Person
        assert _entity_class("Place") is Location


class TestPredicateClass:
    def test_known_predicate(self):
        assert _predicate_class("Knows") is Knows

    def test_located_in(self):
        assert _predicate_class("LocatedIn") is LocatedIn

    def test_contradicts(self):
        assert _predicate_class("Contradicts") is Contradicts

    def test_unknown_returns_none(self):
        assert _predicate_class("Flies") is None

    def test_empty_string_returns_none(self):
        assert _predicate_class("") is None


class TestIsHigherOrder:
    def test_contradicts_is_higher_order(self):
        assert _is_higher_order(Contradicts) is True

    def test_knew_at_is_higher_order(self):
        # KnewAt.object_ is annotated as BaseStatement
        assert _is_higher_order(KnewAt) is True

    def test_knows_is_not_higher_order(self):
        assert _is_higher_order(Knows) is False

    def test_located_in_is_not_higher_order(self):
        assert _is_higher_order(LocatedIn) is False


class TestTruthStatusHelper:
    def test_valid_string(self):
        assert _truth_status("asserted_true") == TruthStatus.ASSERTED_TRUE

    def test_asserted_false(self):
        assert _truth_status("asserted_false") == TruthStatus.ASSERTED_FALSE

    def test_none_returns_hypothetical(self):
        assert _truth_status(None) == TruthStatus.HYPOTHETICAL

    def test_invalid_string_returns_hypothetical(self):
        assert _truth_status("nonsense") == TruthStatus.HYPOTHETICAL


class TestInstanceSet:
    def test_get_by_id(self):
        iset = InstanceSet()
        p = Person(id="wiki:Holmes", display_name="Sherlock Holmes")
        iset.add(p)
        assert iset.get("wiki:Holmes") is p

    def test_get_by_url(self):
        iset = InstanceSet()
        p = Person(id="wiki:Holmes", display_name="Sherlock Holmes")
        iset.add(p, wiki_url="https://bakerstreet.fandom.com/wiki/Sherlock_Holmes")
        assert iset.get("https://bakerstreet.fandom.com/wiki/Sherlock_Holmes") is p

    def test_get_missing_returns_none(self):
        iset = InstanceSet()
        assert iset.get("nonexistent") is None

    def test_by_id_checked_before_url(self):
        iset = InstanceSet()
        p1 = Person(id="direct:id", display_name="Direct")
        p2 = Person(id="other:id", display_name="Other")
        iset.add(p1)
        iset.add(p2, wiki_url="direct:id")
        assert iset.get("direct:id") is p1


class TestHydrateEntities:
    def test_person_hydrated(self):
        iset = InstanceSet()
        _hydrate_entities(
            [{"type": "person", "entity_id": "wiki:Holmes", "canonical": "Sherlock Holmes"}],
            iset,
        )
        inst = iset.get("wiki:Holmes")
        assert isinstance(inst, Person)
        assert inst.display_name == "Sherlock Holmes"

    def test_place_becomes_location(self):
        iset = InstanceSet()
        _hydrate_entities(
            [{"type": "place", "entity_id": "place:London", "canonical": "London"}],
            iset,
        )
        assert isinstance(iset.get("place:London"), Location)

    def test_id_field_fallback(self):
        iset = InstanceSet()
        _hydrate_entities(
            [{"type": "person", "id": "wiki:Watson", "canonical": "John Watson"}],
            iset,
        )
        assert iset.get("wiki:Watson") is not None

    def test_missing_id_produces_warning(self):
        iset = InstanceSet()
        _hydrate_entities([{"type": "person", "canonical": "Nameless"}], iset)
        assert len(iset.warnings) == 1
        assert len(iset.by_id) == 0

    def test_wiki_url_indexed(self):
        iset = InstanceSet()
        _hydrate_entities([{
            "type": "person",
            "entity_id": "wiki:Holmes",
            "canonical": "Sherlock Holmes",
            "wiki_url": "https://bakerstreet.fandom.com/wiki/Sherlock_Holmes",
        }], iset)
        assert iset.get("https://bakerstreet.fandom.com/wiki/Sherlock_Holmes") is not None


class TestHydrateEvents:
    def test_event_hydrated(self):
        iset = InstanceSet()
        _hydrate_events(
            [{"id": "event:1", "story_id": "bohemia", "description": "Holmes receives a client"}],
            iset,
        )
        inst = iset.get("event:1")
        assert isinstance(inst, Event)
        assert inst.description == "Holmes receives a client"

    def test_missing_id_produces_warning(self):
        iset = InstanceSet()
        _hydrate_events([{"story_id": "bohemia", "description": "No ID"}], iset)
        assert len(iset.warnings) == 1

    def test_default_story_id(self):
        iset = InstanceSet()
        _hydrate_events([{"id": "event:1"}], iset)
        assert iset.get("event:1").story_id == "unknown"


class TestHydrateMoments:
    def test_moment_hydrated(self):
        iset = InstanceSet()
        _hydrate_moments(
            [{"id": "moment:1", "story_id": "bohemia", "label": "Opening scene"}],
            iset,
        )
        inst = iset.get("moment:1")
        assert isinstance(inst, Moment)
        assert inst.label == "Opening scene"
        assert inst.narrator is None

    def test_moment_with_valid_narrator(self):
        iset = InstanceSet()
        holmes = Person(id="wiki:Holmes", display_name="Sherlock Holmes")
        iset.add(holmes)
        _hydrate_moments([{
            "id": "moment:1",
            "story_id": "bohemia",
            "label": "Opening",
            "narrator_id": "wiki:Holmes",
        }], iset)
        assert iset.get("moment:1").narrator is holmes

    def test_narrator_not_found_warns_but_moment_added(self):
        iset = InstanceSet()
        _hydrate_moments([{
            "id": "moment:1",
            "story_id": "bohemia",
            "label": "Opening",
            "narrator_id": "wiki:Ghost",
        }], iset)
        assert len(iset.warnings) == 1
        assert iset.get("moment:1") is not None
        assert iset.get("moment:1").narrator is None

    def test_missing_id_produces_warning(self):
        iset = InstanceSet()
        _hydrate_moments([{"story_id": "bohemia", "label": "Opening"}], iset)
        assert len(iset.warnings) == 1


class TestHydrateTriplets:
    def _base_iset(self):
        iset = InstanceSet()
        iset.add(Person(id="wiki:Holmes", display_name="Sherlock Holmes"))
        iset.add(Person(id="wiki:Watson", display_name="John Watson"))
        return iset

    def test_normal_triplet(self):
        iset = self._base_iset()
        _hydrate_triplets([{
            "id": "stmt:knows:1",
            "predicate": "Knows",
            "subject_id": "wiki:Holmes",
            "object_id": "wiki:Watson",
            "truth_status": "asserted_true",
            **_PROV,
        }], iset)
        inst = iset.get("stmt:knows:1")
        assert isinstance(inst, Knows)
        assert inst.truth_status == TruthStatus.ASSERTED_TRUE
        assert inst.subject.id == "wiki:Holmes"

    def test_unknown_predicate_warns(self):
        iset = self._base_iset()
        _hydrate_triplets([{
            "id": "stmt:flies:1",
            "predicate": "Flies",
            "subject_id": "wiki:Holmes",
            "object_id": "wiki:Watson",
        }], iset)
        assert len(iset.warnings) == 1
        assert iset.get("stmt:flies:1") is None

    def test_missing_subject_warns(self):
        iset = self._base_iset()
        _hydrate_triplets([{
            "id": "stmt:knows:1",
            "predicate": "Knows",
            "subject_id": "wiki:Moriarty",
            "object_id": "wiki:Watson",
            **_PROV,
        }], iset)
        assert any("subject" in w for w in iset.warnings)

    def test_missing_object_warns(self):
        iset = self._base_iset()
        _hydrate_triplets([{
            "id": "stmt:knows:1",
            "predicate": "Knows",
            "subject_id": "wiki:Holmes",
            "object_id": "wiki:Moriarty",
            **_PROV,
        }], iset)
        assert any("object" in w for w in iset.warnings)

    def test_higher_order_predicate_hydrated(self):
        iset = self._base_iset()
        # Pre-populate two statements for Contradicts to reference
        h = iset.get("wiki:Holmes")
        w = iset.get("wiki:Watson")
        s1 = Knows(id="stmt:knows:1", subject=h, object_=w, truth_status=TruthStatus.ASSERTED_TRUE, **_PROV)
        s2 = Knows(id="stmt:knows:2", subject=w, object_=h, truth_status=TruthStatus.ASSERTED_FALSE, **_PROV)
        iset.add(s1)
        iset.add(s2)
        _hydrate_triplets([{
            "id": "stmt:contradicts:1",
            "predicate": "Contradicts",
            "subject_id": "stmt:knows:1",
            "object_id": "stmt:knows:2",
            "truth_status": "asserted_true",
            **_PROV,
        }], iset)
        inst = iset.get("stmt:contradicts:1")
        assert isinstance(inst, Contradicts)
        assert inst.subject is s1
        assert inst.object_ is s2


class TestLoadInstances:
    def test_loads_real_data(self):
        iset = load_instances(
            entities=data_path("bohemia_entities.jsonl"),
            events=data_path("bohemia_events.jsonl"),
            moments=data_path("bohemia_moments.jsonl"),
            triplets=data_path("bohemia_triplets.jsonl"),
            warn=False,
        )
        assert len(iset.by_id) > 0

    def test_sherlock_holmes_present(self):
        iset = load_instances(
            entities=data_path("bohemia_entities.jsonl"),
            events=data_path("bohemia_events.jsonl"),
            moments=data_path("bohemia_moments.jsonl"),
            warn=False,
        )
        assert iset.get("wiki:Sherlock_Holmes") is not None

    def test_sherlock_accessible_by_full_url(self):
        iset = load_instances(
            entities=data_path("bohemia_entities.jsonl"),
            events=data_path("bohemia_events.jsonl"),
            moments=data_path("bohemia_moments.jsonl"),
            warn=False,
        )
        url = "https://bakerstreet.fandom.com/wiki/Sherlock_Holmes"
        assert iset.get(url) is not None

    def test_triplets_optional(self):
        iset = load_instances(
            entities=data_path("bohemia_entities.jsonl"),
            events=data_path("bohemia_events.jsonl"),
            moments=data_path("bohemia_moments.jsonl"),
            warn=False,
        )
        assert len(iset.by_id) > 0
