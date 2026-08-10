"""Tests for the packaged Holmes schema (current model)."""

from base import Symmetric, Transitive
from ner_20260608.holmes_schema import (
    AssociatedWith,
    Contradicts,
    Event,
    HappenedIn,
    Involves,
    KnewAt,
    Knows,
    LocatedIn,
    Location,
    Moment,
    Object,
    OccurredAt,
    Organization,
    OtherEntity,
    Person,
    PhysicallyIn,
    Possesses,
)


def _meta() -> dict[str, object]:
    return {
        "story_id": "bohemia",
        "paragraph_index": 1,
        "sentence_ids": (10, 11),
        "asserting_narrator_id": "wiki:John_Watson",
        "raw_extraction_method": "llm-triplet-extraction",
        "extraction_confidence": 0.95,
    }


def _person(entity_id: str, name: str) -> Person:
    return Person(id=entity_id, canonical=name)


def test_person_str_is_canonical() -> None:
    p = _person("wiki:Sherlock_Holmes", "Sherlock Holmes")
    assert str(p) == "Sherlock Holmes"


def test_entity_subtypes_construct() -> None:
    org = Organization(id="org:scotland_yard", canonical="Scotland Yard")
    other = OtherEntity(id="other:mystery", canonical="Unknown")
    assert org.raw_type is None
    assert other.canonical == "Unknown"


def test_knows_has_story_metadata_fields() -> None:
    h = _person("wiki:Sherlock_Holmes", "Sherlock Holmes")
    w = _person("wiki:John_Watson", "Dr. Watson")
    k = Knows(
        id="stmt:1", subject=h, object_=w, truth_status="asserted_true", **_meta()
    )

    assert k.story_id == "bohemia"
    assert k.asserting_narrator_id == "wiki:John_Watson"
    assert k.raw_extraction_method == "llm-triplet-extraction"
    assert k.truth_status == "asserted_true"


def test_located_in_and_knows_have_expected_traits() -> None:
    assert issubclass(LocatedIn, Transitive)
    assert issubclass(Knows, Symmetric)


def test_physically_in_is_inferred_statement_without_story_metadata() -> None:
    obj = Object(id="obj:violin", canonical="Violin")
    place = Location(id="place:baker_street", canonical="Baker Street")
    rel = PhysicallyIn(id="stmt:p", subject=obj, object_=place)

    assert rel.truth_status == "hypothetical"


def test_contradicts_is_provisional_and_higher_order() -> None:
    h = _person("wiki:Sherlock_Holmes", "Sherlock Holmes")
    w = _person("wiki:John_Watson", "Dr. Watson")

    s1 = Knows(id="stmt:k1", subject=h, object_=w, **_meta())
    s2 = Knows(id="stmt:k2", subject=w, object_=h, **_meta())
    c = Contradicts(id="stmt:c", subject=s1, object_=s2, **_meta())

    assert c.provisional is True
    assert c.subject is s1
    assert c.object_ is s2


def test_knew_at_accepts_statement_object_and_moment() -> None:
    h = _person("wiki:Sherlock_Holmes", "Sherlock Holmes")
    w = _person("wiki:John_Watson", "Dr. Watson")
    m = Moment(id="moment:opening", canonical="Opening scene")
    knows = Knows(id="stmt:k1", subject=h, object_=w, **_meta())

    knew = KnewAt(
        id="stmt:knew",
        subject=h,
        object_=knows,
        moment=m,
        **_meta(),
    )
    assert knew.object_ is knows
    assert knew.moment is m


def test_first_order_predicates_construct() -> None:
    h = _person("wiki:Sherlock_Holmes", "Sherlock Holmes")
    loc = Location(id="place:baker_street", canonical="Baker Street")
    obj = Object(id="obj:cigar_case", canonical="Cigar case")
    ev = Event(id="event:visit", canonical="The King visits")
    moment = Moment(id="moment:1", canonical="Night of 20 March")

    assert isinstance(
        AssociatedWith(id="stmt:a", subject=h, object_=loc, **_meta()), AssociatedWith
    )
    assert isinstance(
        Possesses(id="stmt:p", subject=h, object_=obj, **_meta()), Possesses
    )
    assert isinstance(Involves(id="stmt:i", subject=ev, object_=h, **_meta()), Involves)
    assert isinstance(
        OccurredAt(id="stmt:o", subject=ev, object_=moment, **_meta()), OccurredAt
    )
    assert isinstance(
        HappenedIn(id="stmt:h", subject=ev, object_=loc, **_meta()), HappenedIn
    )
