"""Tests for loader.py against the current packaged schema."""

from ner_20260608 import data_path
from ner_20260608.holmes_schema import (
    Contradicts,
    Event,
    KnewAt,
    Knows,
    LocatedIn,
    Location,
    Moment,
    Organization,
    OtherEntity,
    Person,
)
from ner_20260608.loader import (
    InstanceSet,
    _entity_class,
    _hydrate_entities,
    _hydrate_events,
    _hydrate_moments,
    _hydrate_triplets,
    _is_higher_order,
    _predicate_class,
    _truth_status,
    load_instances,
)

_PROV = {
    "story_id": "bohemia",
    "paragraph_index": 1,
    "sentence_ids": [1],
    "asserting_narrator_id": "wiki:John_Watson",
    "extraction_method": "manual",
    "extraction_confidence": 1.0,
}


class TestEntityClass:
    def test_person(self) -> None:
        assert _entity_class("person") is Person

    def test_place(self) -> None:
        assert _entity_class("place") is Location

    def test_organization(self) -> None:
        assert _entity_class("organization") is Organization

    def test_other(self) -> None:
        assert _entity_class("other") is OtherEntity

    def test_unknown_returns_none(self) -> None:
        assert _entity_class("alien") is None


class TestPredicateClass:
    def test_known_predicate(self) -> None:
        assert _predicate_class("Knows") is Knows

    def test_located_in(self) -> None:
        assert _predicate_class("LocatedIn") is LocatedIn

    def test_contradicts(self) -> None:
        assert _predicate_class("Contradicts") is Contradicts

    def test_unknown_returns_none(self) -> None:
        assert _predicate_class("Flies") is None


class TestIsHigherOrder:
    def test_contradicts_is_higher_order(self) -> None:
        assert _is_higher_order(Contradicts) is True

    def test_knew_at_is_higher_order(self) -> None:
        assert _is_higher_order(KnewAt) is True

    def test_knows_is_not_higher_order(self) -> None:
        assert _is_higher_order(Knows) is False


class TestTruthStatusHelper:
    def test_valid_string(self) -> None:
        assert _truth_status("asserted_true") == "asserted_true"

    def test_none_returns_hypothetical(self) -> None:
        assert _truth_status(None) == "hypothetical"

    def test_invalid_string_returns_hypothetical(self) -> None:
        assert _truth_status("nonsense") == "hypothetical"

    def test_invalid_string_warns_via_list(self) -> None:
        warnings: list[str] = []
        result = _truth_status(
            "typo_asserted", warnings_list=warnings, triplet_id="stmt:x"
        )
        assert result == "hypothetical"
        assert len(warnings) == 1
        assert "typo_asserted" in warnings[0]


class TestInstanceSet:
    def test_get_by_id(self) -> None:
        iset = InstanceSet()
        p = Person(id="wiki:Holmes", canonical="Sherlock Holmes")
        iset.add(p)
        assert iset.get("wiki:Holmes") is p

    def test_get_by_url(self) -> None:
        iset = InstanceSet()
        p = Person(id="wiki:Holmes", canonical="Sherlock Holmes")
        iset.add(p, wiki_url="https://bakerstreet.fandom.com/wiki/Sherlock_Holmes")
        assert iset.get("https://bakerstreet.fandom.com/wiki/Sherlock_Holmes") is p


class TestHydrateEntities:
    def test_person_hydrated(self) -> None:
        iset = InstanceSet()
        _hydrate_entities(
            [
                {
                    "type": "person",
                    "entity_id": "wiki:Holmes",
                    "canonical": "Sherlock Holmes",
                    "aliases": ["Holmes"],
                }
            ],
            iset,
        )
        inst = iset.get("wiki:Holmes")
        assert isinstance(inst, Person)
        assert inst.canonical == "Sherlock Holmes"
        assert inst.aliases == ("Holmes",)

    def test_unmapped_ner_type_warns_and_skips(self) -> None:
        iset = InstanceSet()
        _hydrate_entities(
            [{"type": "alien", "entity_id": "x:1", "canonical": "Unknown"}],
            iset,
        )
        assert len(iset.warnings) == 1
        assert iset.get("x:1") is None


class TestHydrateEventsAndMoments:
    def test_event_hydrated_with_canonical_description(self) -> None:
        iset = InstanceSet()
        _hydrate_events(
            [{"id": "event:1", "description": "Holmes receives a client"}],
            iset,
        )
        inst = iset.get("event:1")
        assert isinstance(inst, Event)
        assert inst.canonical == "Holmes receives a client"

    def test_moment_hydrated_with_canonical_label(self) -> None:
        iset = InstanceSet()
        _hydrate_moments(
            [{"id": "moment:1", "label": "Opening scene"}],
            iset,
        )
        inst = iset.get("moment:1")
        assert isinstance(inst, Moment)
        assert inst.canonical == "Opening scene"


class TestHydrateTriplets:
    def _base_iset(self) -> InstanceSet:
        iset = InstanceSet()
        iset.add(Person(id="wiki:Holmes", canonical="Sherlock Holmes"))
        iset.add(Person(id="wiki:Watson", canonical="John Watson"))
        return iset

    def test_normal_triplet(self) -> None:
        iset = self._base_iset()
        _hydrate_triplets(
            [
                {
                    "id": "stmt:knows:1",
                    "predicate": "Knows",
                    "subject_id": "wiki:Holmes",
                    "object_id": "wiki:Watson",
                    "truth_status": "asserted_true",
                    **_PROV,
                }
            ],
            iset,
        )
        inst = iset.get("stmt:knows:1")
        assert isinstance(inst, Knows)
        assert inst.truth_status == "asserted_true"
        assert inst.raw_extraction_method == "manual"

    def test_bad_truth_status_warns_and_defaults(self) -> None:
        iset = self._base_iset()
        _hydrate_triplets(
            [
                {
                    "id": "stmt:knows:1",
                    "predicate": "Knows",
                    "subject_id": "wiki:Holmes",
                    "object_id": "wiki:Watson",
                    "truth_status": "typo_asserted_true",
                    **_PROV,
                }
            ],
            iset,
        )
        inst = iset.get("stmt:knows:1")
        assert isinstance(inst, Knows)
        assert inst.truth_status == "hypothetical"
        assert any("truth_status" in w for w in iset.warnings)

    def test_higher_order_predicate_hydrated(self) -> None:
        iset = self._base_iset()
        h = iset.get("wiki:Holmes")
        w = iset.get("wiki:Watson")
        s1 = Knows(id="stmt:knows:1", subject=h, object_=w, **_PROV)
        s2 = Knows(id="stmt:knows:2", subject=w, object_=h, **_PROV)
        iset.add(s1)
        iset.add(s2)
        _hydrate_triplets(
            [
                {
                    "id": "stmt:contradicts:1",
                    "predicate": "Contradicts",
                    "subject_id": "stmt:knows:1",
                    "object_id": "stmt:knows:2",
                    "truth_status": "asserted_true",
                    **_PROV,
                }
            ],
            iset,
        )
        inst = iset.get("stmt:contradicts:1")
        assert isinstance(inst, Contradicts)
        assert inst.subject is s1
        assert inst.object_ is s2


class TestLoadInstances:
    def test_loads_real_data(self) -> None:
        iset = load_instances(
            entities=data_path("bohemia_entities.jsonl"),
            events=data_path("bohemia_events.jsonl"),
            moments=data_path("bohemia_moments.jsonl"),
            triplets=data_path("bohemia_triplets.jsonl"),
            warn=False,
        )
        assert len(iset.by_id) > 0

    def test_sherlock_holmes_present(self) -> None:
        iset = load_instances(
            entities=data_path("bohemia_entities.jsonl"),
            events=data_path("bohemia_events.jsonl"),
            moments=data_path("bohemia_moments.jsonl"),
            warn=False,
        )
        assert iset.get("wiki:Sherlock_Holmes") is not None
