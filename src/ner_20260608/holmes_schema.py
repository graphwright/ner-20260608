"""Holmes Schema — packaged copy used by the installable wheel.

This file mirrors the repo's schema module but lives inside the real import
package so installed users can rely on package-relative imports.
"""

from __future__ import annotations

from typing import ClassVar, TypeVar

from pydantic import BaseModel, ConfigDict

from base import (
    AnyStatement,
    AnyStmt,
    BaseStatement,
    EntityInstance,
    Symmetric,
    Transitive,
)


class SherlockEntity(EntityInstance):
    """Base entity with optional source metadata from the JSONL catalog."""

    canonical: str
    aliases: tuple[str, ...] = ()
    wiki_url: str | None = None
    raw_type: str | None = None

    def __str__(self) -> str:
        return self.canonical


class Person(SherlockEntity):
    """A person in the story world."""


class Organization(SherlockEntity):
    """An organization in the story world."""


class Location(SherlockEntity):
    """A place/location in the story world."""


class Object(SherlockEntity):
    """A tangible or conceptual object in the story world."""


class Event(SherlockEntity):
    """An event node imported from event extraction."""


class Moment(SherlockEntity):
    """A time/moment node imported from timeline extraction."""


class OtherEntity(SherlockEntity):
    """Fallback entity type when the source type is unknown."""


SubjectT = TypeVar("SubjectT", bound=SherlockEntity)
ObjectT = TypeVar("ObjectT", bound=SherlockEntity)


class StoryMetadata(BaseModel):
    """Story-extraction metadata shared by extracted (not inferred) predicates."""

    model_config = ConfigDict(frozen=True)

    story_id: str
    paragraph_index: int | None = None
    sentence_ids: tuple[int, ...] = ()
    asserting_narrator_id: str | None = None
    extraction_confidence: float | None = None
    narrator_confidence: float | None = None
    raw_extraction_method: str | None = None


class StoryStatement[SubjectT: SherlockEntity, ObjectT: SherlockEntity](
    BaseStatement[SubjectT, ObjectT], StoryMetadata
):
    """Statement enriched with story-extraction metadata from triplet rows."""

    def __str__(self) -> str:
        return f"{type(self).__name__}({self.subject} -> {self.object_})"


class Involves(StoryStatement[Event, Person]):
    """An event involves a person."""


class OccurredAt(StoryStatement[Event, Moment]):
    """An event occurred at a specific moment."""


class Possesses(StoryStatement[Person, Object]):
    """A person possesses an object."""


class AssociatedWith(StoryStatement[Person, Location]):
    """A person is associated with a location."""


class Knows(StoryStatement[Person, Person], Symmetric):
    """A social knowledge relation between two people."""


class LocatedIn(StoryStatement[Location, Location], Transitive):
    """A transitive containment/location relation."""


class HappenedIn(StoryStatement[Event, Location]):
    """An event took place in a location.

    This captures event->place structure that is often only implicit in event
    identifiers/descriptions from extraction output.
    """


class KnewAt(BaseStatement[Person, AnyStmt], StoryMetadata):
    """A person's epistemic state toward some statement, as of a moment."""

    object_: AnyStatement
    moment: Moment


class Contradicts(BaseStatement[AnyStatement, AnyStatement], StoryMetadata, Symmetric):
    """One statement contradicts another."""

    subject: AnyStatement
    object_: AnyStatement
    provisional: ClassVar[bool] = True


class PhysicallyIn(BaseStatement[Object, Location]):
    """An object is physically located in a place.

    This is a *derivable* predicate, not an extracted one: it is produced by
    inference (e.g. the mystery Horn clause), never imported from a triplet row.
    It therefore subclasses ``BaseStatement`` directly rather than
    ``StoryStatement`` -- an inferred fact has no story-extraction metadata
    (no ``story_id``, paragraph index, or extraction confidence), and requiring
    those fields would make it impossible for the datalog engine to construct a
    derived head. Keeping extracted and inferred predicates on separate branches
    of the hierarchy is the honest ontology: provenance-of-extraction belongs
    only to things that were extracted.
    """
