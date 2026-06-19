"""Tests for holmes_schema.py — entity/statement class hierarchy."""

import pytest
from pydantic import ValidationError

from ner_20260608.holmes_schema import (
    TruthStatus,
    EntityInstance,
    BaseStatement,
    Person, Persona, Location, Object, Document, Event, Moment, Plan,
    AssociatedWith, Knows, LocatedIn, Involves, OccurredAt,
    KnewAt, DisguisedAs, HasTrueIdentity, Possesses, Contradicts, Executes,
    statement_id,
    get_inverse,
)


# --- shared provenance kwargs ---

_PROV = dict(
    story_id="bohemia",
    paragraph_index=1,
    extraction_method="manual",
    extraction_confidence=1.0,
)


class TestTruthStatus:
    def test_string_values(self):
        assert TruthStatus.ASSERTED_TRUE.value == "asserted_true"
        assert TruthStatus.ASSERTED_FALSE.value == "asserted_false"
        assert TruthStatus.HYPOTHETICAL.value == "hypothetical"
        assert TruthStatus.DISPUTED.value == "disputed"
        assert TruthStatus.RETRACTED.value == "retracted"

    def test_coerce_from_string(self):
        assert TruthStatus("asserted_true") is TruthStatus.ASSERTED_TRUE

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            TruthStatus("made_up")


class TestEntityInstance:
    def test_construction(self):
        e = EntityInstance(id="foo:bar")
        assert e.id == "foo:bar"

    def test_frozen(self):
        e = EntityInstance(id="foo:bar")
        with pytest.raises(ValidationError):
            e.id = "something_else"

    def test_repr_contains_id(self):
        e = EntityInstance(id="foo:bar")
        assert "foo:bar" in repr(e)


class TestBaseStatement:
    def test_default_truth_status_is_hypothetical(self):
        b = BaseStatement(id="stmt:x")
        assert b.truth_status == TruthStatus.HYPOTHETICAL

    def test_explicit_truth_status(self):
        b = BaseStatement(id="stmt:x", truth_status=TruthStatus.ASSERTED_TRUE)
        assert b.truth_status == TruthStatus.ASSERTED_TRUE


class TestStatementId:
    def test_format(self):
        assert statement_id("wiki:Holmes", "Knows", "wiki:Watson") == "stmt:wiki:Holmes:Knows:wiki:Watson"

    def test_parts_preserved(self):
        sid = statement_id("a", "B", "c")
        assert "a" in sid and "B" in sid and "c" in sid


class TestGetInverse:
    def test_disguised_as_inverse_is_has_true_identity(self):
        assert get_inverse(DisguisedAs) is HasTrueIdentity

    def test_has_true_identity_inverse_is_disguised_as(self):
        assert get_inverse(HasTrueIdentity) is DisguisedAs

    def test_knows_has_no_inverse(self):
        assert get_inverse(Knows) is None

    def test_located_in_has_no_inverse(self):
        assert get_inverse(LocatedIn) is None

    def test_associated_with_has_no_inverse(self):
        assert get_inverse(AssociatedWith) is None


class TestStrRepr:
    """str() is the human-readable presentation layer; repr() shows the canonical id."""

    def test_person_str_is_display_name(self):
        p = Person(id="wiki:Holmes", display_name="Sherlock Holmes")
        assert str(p) == "Sherlock Holmes"

    def test_person_repr_contains_id(self):
        p = Person(id="wiki:Holmes", display_name="Sherlock Holmes")
        assert "wiki:Holmes" in repr(p)

    def test_location_str_is_display_name(self):
        loc = Location(id="wiki:London", display_name="London")
        assert str(loc) == "London"

    def test_event_str_is_description(self):
        e = Event(id="sib:kings_visit", story_id="bohemia", description="The King visits Baker Street.")
        assert str(e) == "The King visits Baker Street."

    def test_moment_str_is_label(self):
        m = Moment(id="sib:morning", story_id="bohemia", label="Morning of 21 March")
        assert str(m) == "Morning of 21 March"

    def test_plan_str_is_description(self):
        plan = Plan(id="sib:plan", story_id="bohemia", description="Stage a fire alarm")
        assert str(plan) == "Stage a fire alarm"

    def test_base_statement_str_fallback(self):
        b = BaseStatement(id="stmt:x")
        assert str(b) == "BaseStatement"

    def test_predicate_str_shows_subject_and_object(self):
        h = Person(id="wiki:Holmes", display_name="Sherlock Holmes")
        w = Person(id="wiki:Watson", display_name="Dr. Watson")
        k = Knows(id="stmt:1", subject=h, object_=w, **_PROV)
        result = str(k)
        assert "Knows" in result
        assert "Sherlock Holmes" in result
        assert "Dr. Watson" in result
        assert "→" in result

    def test_str_not_parsed_for_type(self):
        """The type must be determined by isinstance, not by parsing str()."""
        h = Person(id="wiki:Holmes", display_name="Sherlock Holmes")
        assert isinstance(h, Person)
        # str() is for display only — never use it for type dispatch
        assert str(h) == h.display_name


class TestEntityClasses:
    def test_person_construction(self):
        p = Person(id="wiki:Holmes", display_name="Sherlock Holmes")
        assert p.id == "wiki:Holmes"
        assert p.display_name == "Sherlock Holmes"

    def test_persona_construction(self):
        p = Persona(id="sib:persona:count", display_name="Count von Kramm")
        assert p.display_name == "Count von Kramm"

    def test_location_construction(self):
        loc = Location(id="place:London", display_name="London")
        assert loc.display_name == "London"

    def test_object_construction(self):
        obj = Object(id="obj:violin", display_name="Violin")
        assert obj.display_name == "Violin"

    def test_event_construction(self):
        e = Event(id="event:1", story_id="bohemia", description="A meeting")
        assert e.story_id == "bohemia"
        assert e.description == "A meeting"

    def test_moment_construction(self):
        m = Moment(id="moment:1", story_id="bohemia", label="Opening scene")
        assert m.label == "Opening scene"
        assert m.narrator is None

    def test_moment_with_narrator(self):
        p = Person(id="wiki:Watson", display_name="John Watson")
        m = Moment(id="moment:1", story_id="bohemia", label="Opening", narrator=p)
        assert m.narrator is p

    def test_plan_construction(self):
        plan = Plan(id="plan:1", story_id="bohemia", description="Steal photo", goal="Blackmail")
        assert plan.goal == "Blackmail"

    def test_plan_goal_optional(self):
        plan = Plan(id="plan:1", story_id="bohemia", description="Steal photo")
        assert plan.goal is None


class TestDocument:
    def test_valid_document_type_letter(self):
        doc = Document(id="doc:1", display_name="A Letter", story_id="bohemia", document_type="letter")
        assert doc.document_type == "letter"

    def test_valid_document_type_photograph(self):
        doc = Document(id="doc:1", display_name="Photo", story_id="bohemia", document_type="photograph")
        assert doc.document_type == "photograph"

    def test_invalid_document_type_raises(self):
        with pytest.raises(ValidationError):
            Document(id="doc:1", display_name="X", story_id="bohemia", document_type="scroll")


class TestProvenanceMixin:
    def _make_knows(self, holmes, watson, confidence):
        return Knows(
            id="stmt:knows:1",
            subject=holmes,
            object_=watson,
            truth_status=TruthStatus.ASSERTED_TRUE,
            story_id="bohemia",
            paragraph_index=1,
            extraction_method="manual",
            extraction_confidence=confidence,
        )

    def test_valid_extraction_confidence(self):
        h = Person(id="wiki:Holmes", display_name="Sherlock Holmes")
        w = Person(id="wiki:Watson", display_name="John Watson")
        k = self._make_knows(h, w, 0.9)
        assert k.extraction_confidence == pytest.approx(0.9)

    def test_extraction_confidence_above_one_raises(self):
        h = Person(id="wiki:Holmes", display_name="Sherlock Holmes")
        w = Person(id="wiki:Watson", display_name="John Watson")
        with pytest.raises(ValidationError):
            self._make_knows(h, w, 1.5)

    def test_extraction_confidence_negative_raises(self):
        h = Person(id="wiki:Holmes", display_name="Sherlock Holmes")
        w = Person(id="wiki:Watson", display_name="John Watson")
        with pytest.raises(ValidationError):
            self._make_knows(h, w, -0.1)

    def test_asserting_narrator_optional(self):
        h = Person(id="wiki:Holmes", display_name="Sherlock Holmes")
        w = Person(id="wiki:Watson", display_name="John Watson")
        k = self._make_knows(h, w, 1.0)
        assert k.asserting_narrator is None


class TestEpistemicMixin:
    def _persons(self):
        h = Person(id="wiki:Holmes", display_name="Sherlock Holmes")
        w = Person(id="wiki:Watson", display_name="John Watson")
        return h, w

    def test_narrator_confidence_defaults_to_none(self):
        h, w = self._persons()
        k = Knows(id="stmt:1", subject=h, object_=w, **_PROV)
        assert k.narrator_confidence is None

    def test_narrator_confidence_valid(self):
        h, w = self._persons()
        k = Knows(id="stmt:1", subject=h, object_=w, narrator_confidence=0.75, **_PROV)
        assert k.narrator_confidence == pytest.approx(0.75)

    def test_narrator_confidence_out_of_range_raises(self):
        h, w = self._persons()
        with pytest.raises(ValidationError):
            Knows(id="stmt:1", subject=h, object_=w, narrator_confidence=2.0, **_PROV)


class TestPredicateClasses:
    def test_knows_requires_two_persons(self):
        h = Person(id="wiki:Holmes", display_name="Sherlock Holmes")
        w = Person(id="wiki:Watson", display_name="John Watson")
        k = Knows(id="stmt:1", subject=h, object_=w, **_PROV)
        assert k.subject is h
        assert k.object_ is w

    def test_involves_requires_event_subject(self):
        e = Event(id="event:1", story_id="bohemia", description="A meeting")
        p = Person(id="wiki:Holmes", display_name="Sherlock Holmes")
        inv = Involves(id="stmt:1", subject=e, object_=p, **_PROV)
        assert inv.subject is e

    def test_occurred_at_requires_moment_object(self):
        e = Event(id="event:1", story_id="bohemia", description="A meeting")
        m = Moment(id="moment:1", story_id="bohemia", label="Opening")
        occ = OccurredAt(id="stmt:1", subject=e, object_=m, **_PROV)
        assert occ.object_ is m

    def test_contradicts_accepts_statements_as_subject_object(self):
        b1 = BaseStatement(id="stmt:a")
        b2 = BaseStatement(id="stmt:b")
        c = Contradicts(id="stmt:c", subject=b1, object_=b2, **_PROV)
        assert c.subject is b1
        assert c.object_ is b2
