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

    def test_organization_is_unmapped(self):
        # organization is not in the schema; warn and skip rather than misroute to Location
        assert _entity_class("organization") is None

    def test_other(self):
        assert _entity_class("other") is Object

    def test_unknown_returns_none(self):
        # unmapped types return None; callers warn and skip
        assert _entity_class("alien") is None

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

    def test_invalid_string_warns_via_list(self):
        warnings: list[str] = []
        result = _truth_status("typo_asserted", warnings_list=warnings, triplet_id="stmt:x")
        assert result == TruthStatus.HYPOTHETICAL
        assert len(warnings) == 1
        assert "typo_asserted" in warnings[0]
        assert "stmt:x" in warnings[0]

    def test_none_does_not_warn(self):
        warnings: list[str] = []
        _truth_status(None, warnings_list=warnings)
        assert warnings == []


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

    def test_unmapped_ner_type_warns_and_skips(self):
        iset = InstanceSet()
        _hydrate_entities([{
            "type": "organization",
            "entity_id": "org:scotland_yard",
            "canonical": "Scotland Yard",
        }], iset)
        assert len(iset.warnings) == 1
        assert "unmapped" in iset.warnings[0]
        assert iset.get("org:scotland_yard") is None

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

    def test_bad_truth_status_warns_via_iset(self):
        iset = self._base_iset()
        _hydrate_triplets([{
            "id": "stmt:knows:1",
            "predicate": "Knows",
            "subject_id": "wiki:Holmes",
            "object_id": "wiki:Watson",
            "truth_status": "typo_asserted_true",
            **_PROV,
        }], iset)
        assert any("truth_status" in w for w in iset.warnings)
        assert iset.get("stmt:knows:1") is not None  # still added, with HYPOTHETICAL

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


    def test_deep_higher_order_fixpoint(self):
        """Contradicts pointing at a KnewAt requires two deferred passes.

        File order: Contradicts first (object_ = KnewAt, not yet built),
        then KnewAt (object_ = Knows, already in iset from pre-seeding).
        The fixpoint loop resolves Contradicts in the second iteration.
        """
        iset = self._base_iset()
        h = iset.get("wiki:Holmes")
        w = iset.get("wiki:Watson")
        knows = Knows(id="stmt:knows:1", subject=h, object_=w,
                      truth_status=TruthStatus.ASSERTED_TRUE, **_PROV)
        moment = Moment(id="moment:1", story_id="bohemia", label="Opening")
        iset.add(knows)
        iset.add(moment)

        _hydrate_triplets([
            {   # Contradicts comes first — its object_ (KnewAt) isn't built yet
                "id": "stmt:contradicts:1",
                "predicate": "Contradicts",
                "subject_id": "stmt:knows:1",
                "object_id": "stmt:knew:1",
                **_PROV,
            },
            {   # KnewAt comes second — its object_ (Knows) is already in iset
                "id": "stmt:knew:1",
                "predicate": "KnewAt",
                "subject_id": "wiki:Holmes",
                "object_id": "stmt:knows:1",
                "moment_id": "moment:1",
                **_PROV,
            },
        ], iset)
        assert iset.get("stmt:knew:1") is not None, "KnewAt should be hydrated"
        assert iset.get("stmt:contradicts:1") is not None, "Contradicts should resolve in second pass"

    def test_first_order_in_same_call_resolves_before_deferred(self):
        """Higher-order triplet referencing a first-order triplet in the same
        _hydrate_triplets call, with the first-order record appearing AFTER
        the higher-order one in file order.

        The initial pass must build Knows before the deferred loop runs,
        so KnewAt can resolve in the first deferred iteration.
        """
        iset = self._base_iset()
        iset.add(Moment(id="moment:1", story_id="bohemia", label="Opening"))

        _hydrate_triplets([
            {   # KnewAt comes first — its object_ (Knows below) isn't built yet
                "id": "stmt:knew:1",
                "predicate": "KnewAt",
                "subject_id": "wiki:Holmes",
                "object_id": "stmt:knows:1",
                "moment_id": "moment:1",
                **_PROV,
            },
            {   # Knows comes second in file order — first-order, built in initial pass
                "id": "stmt:knows:1",
                "predicate": "Knows",
                "subject_id": "wiki:Holmes",
                "object_id": "wiki:Watson",
                "truth_status": "asserted_true",
                **_PROV,
            },
        ], iset)
        assert iset.get("stmt:knows:1") is not None, "Knows should be hydrated in initial pass"
        assert iset.get("stmt:knew:1") is not None, "KnewAt should resolve in first deferred iteration"


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
