"""Holmes Schema — packaged copy used by the installable wheel.

This file mirrors the repo's schema module but lives inside the real import
package so installed users can rely on package-relative imports.
"""

import sys
from typing import ClassVar, ForwardRef, Generic, Literal, TypeVar, get_args, get_origin
from enum import Enum
from pydantic import BaseModel, Field, ConfigDict


class TruthStatus(str, Enum):
    ASSERTED_TRUE = "asserted_true"
    ASSERTED_FALSE = "asserted_false"
    HYPOTHETICAL = "hypothetical"
    DISPUTED = "disputed"
    RETRACTED = "retracted"


class Trait:
    """Marker base for all semantic traits."""


class Transitive(Trait): ...
class Symmetric(Trait): ...
class Functional(Trait): ...
class InverseFunctional(Trait): ...

P = TypeVar('P', bound='BaseStatement')


class Inverse(Trait, Generic[P]):
    """This predicate is the inverse of P."""


def get_inverse(cls: type['BaseStatement']) -> type['BaseStatement'] | None:
    module = sys.modules[cls.__module__].__dict__
    for base in getattr(cls, '__orig_bases__', []):
        if get_origin(base) is Inverse:
            args = get_args(base)
            if args:
                arg = args[0]
                if isinstance(arg, str):
                    return module.get(arg)
                if isinstance(arg, ForwardRef):
                    return module.get(arg.__forward_arg__)
                return arg
    return None


class EntityInstance(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)
    id: str

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.id!r})"


def statement_id(subject_id: str, predicate_name: str, object_id: str) -> str:
    return f"stmt:{subject_id}:{predicate_name}:{object_id}"


class BaseStatement(EntityInstance):
    truth_status: TruthStatus = TruthStatus.HYPOTHETICAL


class Person(EntityInstance):
    display_name: str


class Persona(EntityInstance):
    display_name: str


class Location(EntityInstance):
    display_name: str


class Object(EntityInstance):
    display_name: str


class Document(EntityInstance):
    display_name: str
    story_id: str
    document_type: Literal["letter", "photograph", "telegram", "newspaper", "other"]


class Event(EntityInstance):
    story_id: str
    description: str


class Moment(EntityInstance):
    story_id: str
    label: str
    narrator: Person | None = None


class Plan(EntityInstance):
    provisional: ClassVar[bool] = True
    story_id: str
    description: str
    goal: str | None = None


class ProvenanceMixin(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)
    story_id: str
    paragraph_index: int
    asserting_narrator: Person | None = None
    extraction_method: str
    extraction_confidence: float = Field(ge=0.0, le=1.0)


class EpistemicMixin(BaseModel):
    model_config = ConfigDict(frozen=True)
    narrator_confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class AssociatedWith(BaseStatement, ProvenanceMixin):
    subject: Person
    object_: Location


class Knows(BaseStatement, ProvenanceMixin, EpistemicMixin, Symmetric):
    subject: Person
    object_: Person


class LocatedIn(BaseStatement, ProvenanceMixin, Transitive):
    subject: Location
    object_: Location


class Involves(BaseStatement, ProvenanceMixin):
    subject: Event
    object_: Person | Persona


class OccurredAt(BaseStatement, ProvenanceMixin):
    subject: Event
    object_: Moment


class KnewAt(BaseStatement, ProvenanceMixin, EpistemicMixin):
    subject: Person
    object_: BaseStatement
    moment: Moment


class DisguisedAs(BaseStatement, ProvenanceMixin, EpistemicMixin, Inverse['HasTrueIdentity']):
    subject: Person
    object_: Persona


class HasTrueIdentity(BaseStatement, ProvenanceMixin, EpistemicMixin, Functional, Inverse[DisguisedAs]):
    subject: Persona
    object_: Person


class Possesses(BaseStatement, ProvenanceMixin, EpistemicMixin):
    subject: Person
    object_: Object | Document


class Contradicts(BaseStatement, ProvenanceMixin, Symmetric):
    provisional: ClassVar[bool] = True
    subject: BaseStatement
    object_: BaseStatement


class Executes(BaseStatement, ProvenanceMixin):
    provisional: ClassVar[bool] = True
    subject: Person
    object_: Plan


for _cls in [
    Person, Persona, Location, Object, Document,
    Event, Moment, Plan,
    AssociatedWith, Knows, LocatedIn, Involves, OccurredAt,
    KnewAt, DisguisedAs, HasTrueIdentity, Possesses, Contradicts, Executes,
]:
    _cls.model_rebuild()
