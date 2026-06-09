"""
Holmes Schema — Python realization of the typed graph schema for the
Sherlock Holmes canonical corpus (Conan Doyle, 60 stories).

Schema version: 0.7.0-draft
Authoritative ontology: Baker Street Wiki (https://bakerstreet.fandom.com)

Identity conventions
--------------------
Entities with an external authority (Person, Location, Object, Document):
    id IS the Baker Street Wiki URI.

Corpus-local entities (Event, Moment, Persona, Plan):
    id is a synthetic identifier, stable within the corpus but not grounded
    in any external authority.

Predicate instances (all BaseStatement subclasses):
    id is content-addressed — derived deterministically from
    (subject.id, predicate_type_name, object_.id). Two instances expressing
    the same proposition compute the same id by construction.

Unified Statement model
-----------------------
There is no separate Statement entity type. Every predicate instance inherits
from BaseStatement, which inherits from EntityInstance. A predicate instance
is simultaneously:

  - An entity: it has an id and is a member of V.
  - A proposition: it has subject, object_, and truth_status.
  - A potential edge in the asserted graph: if truth_status == asserted_true.

Higher-order predicates (KnewAt, Contradicts) are simply predicates whose
domain or range includes BaseStatement — the base class for all predicate
types. This is a type declaration, not a runtime promotion. Every predicate
instance is referable from birth; whether any other predicate can point at
it is determined by the type annotations in Phi.

The former Statement entity type, AssertionMixin, and the assertion field
group have been removed. truth_status is universal on BaseStatement,
eliminating the possibility of status drift between two representations
of the same proposition.
"""

import sys
from typing import ClassVar, ForwardRef, Generic, Literal, TypeVar, get_args, get_origin
from enum import Enum
from pydantic import BaseModel, Field, ConfigDict


"""
## Truth status

`TruthStatus` is the graph's explicit commitment to a proposition. Every
predicate instance carries this field; the asserted graph is the projection
where `truth_status == ASSERTED_TRUE`. Under the unified model, *presence*
in the graph does not assert — `truth_status` carries the assertion explicitly.

Lifecycle: `hypothetical → asserted_true | asserted_false → disputed | retracted`
"""


class TruthStatus(str, Enum):
    """Graph-level commitment to a proposition.

    Every predicate instance carries truth_status. Under the unified model,
    presence does NOT assert — truth_status carries the assertion explicitly.

    Lifecycle: hypothetical → asserted_true | asserted_false → disputed | retracted
    """
    ASSERTED_TRUE = "asserted_true"
    ASSERTED_FALSE = "asserted_false"
    HYPOTHETICAL = "hypothetical"
    DISPUTED = "disputed"
    RETRACTED = "retracted"


"""
## Trait markers

Traits are declarative semantic properties of predicate types — they belong
to the class, not to any instance. `Trait` must not inherit from `BaseModel`
so that Pydantic's `ModelMetaclass` stays in control of class construction.

`Inverse[P]` is generic and names the paired predicate type. `get_inverse`
resolves it at runtime by inspecting `__orig_bases__` — the list Python
preserves from the class statement, including un-evaluated generic arguments.
"""


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
    """Return the declared inverse predicate type, if any."""
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


"""
## Base classes

`EntityInstance` is the root of the hierarchy: every object in the graph has
an `id`. For plain entities this is an authoritative URI or a corpus-local
synthetic identifier; for predicate instances it is content-addressed via
`statement_id`.

`BaseStatement` extends `EntityInstance` with `subject`, `object_`, and
`truth_status`. Because it inherits from `EntityInstance`, every predicate
instance is a full member of V — it can be referenced by higher-order
predicates like `KnewAt` without any special promotion.
"""


class EntityInstance(BaseModel):
    """Base class for all entity types (members of T_ent).

    Every instance has an id. For predicate instances (BaseStatement subclasses),
    id is content-addressed. For plain entity instances, id is either an
    authoritative URI or a corpus-local synthetic identifier."""
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)
    id: str

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.id!r})"


def statement_id(subject_id: str, predicate_name: str, object_id: str) -> str:
    """Compute the content-addressed id for a predicate instance.

    Two instances expressing the same proposition compute the same id.
    This is the mechanism that makes mate-lookup O(1) and eliminates
    the possibility of duplicate representations."""
    return f"stmt:{subject_id}:{predicate_name}:{object_id}"


class BaseStatement(EntityInstance):
    """Base class for all predicate types (members of T_pred).

    A BaseStatement is simultaneously:
    - An entity (has an id, inherits from EntityInstance, is a member of V)
    - A proposition (has subject, object_, truth_status)
    - A potential edge in the asserted graph (if truth_status == asserted_true)

    This is the formal definition's E ⊆ V realized in Python's class hierarchy.

    Subclasses declare typed subject and object_ fields that constitute domain
    and range constraints, enforced by Pydantic at construction time (R6).

    truth_status defaults to HYPOTHETICAL. Ingestion pipelines create instances
    as hypothetical; a consensus/promotion pass sets truth_status based on
    evidence. The asserted graph is the projection where
    truth_status == ASSERTED_TRUE.
    """
    truth_status: TruthStatus = TruthStatus.HYPOTHETICAL


"""
## Entity types (T_ent)

Plain entity classes cover the domain concepts that appear in the stories:
people, personas (disguises), locations, physical objects, documents, discrete
events, points in time, and goal-directed plans.

`Moment` carries a `narrator` field that doubles as an axis selector: absent
means the objective story timeline; present means that person's epistemic
timeline. One type handles both axes the corpus needs.
"""


class Person(EntityInstance):
    """A human character with an independent existence in the fictional world.
    id IS the Baker Street Wiki URI."""
    display_name: str


class Persona(EntityInstance):
    """An assumed identity adopted as a disguise. Not treated as a subtype of
    Person — predicates accepting both declare an explicit union (Person | Persona).
    id is a corpus-local synthetic identifier."""
    display_name: str


class Location(EntityInstance):
    """A physical place referenced in the stories.
    id IS the Baker Street Wiki URI."""
    display_name: str


class Object(EntityInstance):
    """A significant physical object.
    id IS the Baker Street Wiki URI."""
    display_name: str


class Document(EntityInstance):
    """A load-bearing text or image artifact: letter, photograph, telegram,
    newspaper. Distinct from Object because documents carry content and
    authenticity properties physical objects do not.
    id IS the Baker Street Wiki URI."""
    display_name: str
    story_id: str
    document_type: Literal["letter", "photograph", "telegram", "newspaper", "other"]


class Event(EntityInstance):
    """A discrete occurrence within a story: an action, observation, revelation.
    id is corpus-local."""
    story_id: str
    description: str


class Moment(EntityInstance):
    """A named point on a timeline.

    narrator absent  → objective story timeline (when something happened).
    narrator present → that Person's epistemic timeline (when they learned it).

    One type carries both axes the corpus demands. id is corpus-local."""
    story_id: str
    label: str
    narrator: Person | None = None


class Plan(EntityInstance):
    """A goal-directed sequence of actions attributed to an agent.
    id is corpus-local."""
    provisional: ClassVar[bool] = True
    story_id: str
    description: str
    goal: str | None = None


"""
## Field group mixins (Φ components)

Field groups are reusable bundles of fields — Pydantic mixins that predicate
classes inherit alongside `BaseStatement`. Named "field groups" rather than
"trait groups" to avoid collision with the formal trait vocabulary.

`ProvenanceMixin` tracks where an assertion comes from in the text.
`EpistemicMixin` records the narrator's in-world certainty, which is distinct
from the pipeline's `extraction_confidence`.

`AssertionMixin` has been removed. `truth_status` is now universal on
`BaseStatement`, eliminating the status-drift problem that arose when two
objects (an edge and a Statement entity) could carry `truth_status`
independently — status now lives in exactly one place.
"""


class ProvenanceMixin(BaseModel):
    """Source-tracing fields. Required on every evidential predicate.

    extraction_confidence: pipeline confidence that the text asserts this.
    asserting_narrator: who in the story reported it; absent if the source
        is the omniscient narrator or is unattributable."""
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)
    story_id: str
    paragraph_index: int
    asserting_narrator: Person | None = None
    extraction_method: str
    extraction_confidence: float = Field(ge=0.0, le=1.0)


class EpistemicMixin(BaseModel):
    """In-world certainty. Distinct from extraction_confidence (pipeline) and
    from truth_status (graph-level commitment).

    narrator_confidence: how certain the in-story narrator is at narration time."""
    model_config = ConfigDict(frozen=True)
    narrator_confidence: float | None = Field(default=None, ge=0.0, le=1.0)


"""
## Predicate types (T_pred)

Each predicate class inherits from `BaseStatement` (which inherits from
`EntityInstance`), so every instance has an `id`, `subject`, `object_`, and
`truth_status`. The `id` should be computed via `statement_id()` at
construction.

MRO convention: `BaseStatement` first, then field group mixins, then Trait
and `Inverse` markers last.

Higher-order predicates (`KnewAt`, `Contradicts`) accept `BaseStatement` as
their subject or object type — any predicate instance can fill that role.
This is a type declaration, not a runtime mechanism: every predicate instance
is referable from birth.
"""


class AssociatedWith(BaseStatement, ProvenanceMixin):
    """Person is habitually connected to a location."""
    subject: Person
    object_: Location


class Knows(BaseStatement, ProvenanceMixin, EpistemicMixin, Symmetric):
    """Person has an acquaintance or professional relationship with another.
    Deliberately coarse; split into sub-predicates only if the corpus rewards it.
    Symmetric: if Holmes knows Watson, Watson knows Holmes."""
    subject: Person
    object_: Person


class LocatedIn(BaseStatement, ProvenanceMixin, Transitive):
    """A location is situated within another location. Transitive.
    Asserted edges carry source provenance; inferred transitive edges carry
    inference provenance instead."""
    subject: Location
    object_: Location


class Involves(BaseStatement, ProvenanceMixin):
    """Event involves an entity as a participant. Accepts Person or Persona —
    if Holmes attended in disguise, the Persona is the participant visible to
    other characters; the real Person is recoverable via HasTrueIdentity."""
    subject: Event
    object_: Person | Persona


class OccurredAt(BaseStatement, ProvenanceMixin):
    """Event is anchored to its objective point in time. The Moment's narrator
    field must be absent (objective timeline). Distinct from KnewAt."""
    subject: Event
    object_: Moment


class KnewAt(BaseStatement, ProvenanceMixin, EpistemicMixin):
    """Person came to know a proposition at a given Moment.

    Higher-order: object_ is BaseStatement, meaning any predicate instance
    can be the known proposition. The knower is the subject; the proposition
    is the object; the moment is a typed field on the instance.

    truth_status on this instance is the graph's commitment to the claim
    "Person knew this at that moment" — distinct from the object_'s own
    truth_status, which is the commitment to the proposition itself.
    Watson can know (truth_status=asserted_true on KnewAt) a proposition
    that is false (truth_status=asserted_false on the object_)."""
    subject: Person
    object_: BaseStatement    # higher-order: range includes all predicate types
    moment: Moment


class DisguisedAs(BaseStatement, ProvenanceMixin, EpistemicMixin,
                  Inverse['HasTrueIdentity']):
    """Real Person adopted a Persona. A person may hold many disguises.
    Inverse of HasTrueIdentity."""
    subject: Person
    object_: Persona


class HasTrueIdentity(BaseStatement, ProvenanceMixin, EpistemicMixin,
                      Functional, Inverse[DisguisedAs]):
    """A Persona conceals exactly one real Person. Functional: a persona
    cannot conceal more than one person. Inverse of DisguisedAs."""
    subject: Persona
    object_: Person


class Possesses(BaseStatement, ProvenanceMixin, EpistemicMixin):
    """Person possesses an Object or Document. Added when the photograph
    in Bohemia forced the issue."""
    subject: Person
    object_: Object | Document


class Contradicts(BaseStatement, ProvenanceMixin, Symmetric):
    """One proposition directly contradicts another. Symmetric.

    Higher-order: both subject and object_ are BaseStatement, meaning any
    predicate instance can participate. Used to surface conflicts between
    belief states or between a character's belief and ground truth."""
    provisional: ClassVar[bool] = True
    subject: BaseStatement    # higher-order
    object_: BaseStatement    # higher-order


class Executes(BaseStatement, ProvenanceMixin):
    """Person is the agent responsible for carrying out a Plan."""
    provisional: ClassVar[bool] = True
    subject: Person
    object_: Plan


"""
## Forward reference resolution

Pydantic's `model_rebuild()` resolves any `ForwardRef` annotations left from
the class definitions above — in particular the `Inverse['HasTrueIdentity']`
string annotation on `DisguisedAs`, which references a class declared later.
This call must come after all classes are defined.
"""


for _cls in [
    Person, Persona, Location, Object, Document,
    Event, Moment, Plan,
    AssociatedWith, Knows, LocatedIn, Involves, OccurredAt,
    KnewAt, DisguisedAs, HasTrueIdentity, Possesses, Contradicts, Executes,
]:
    _cls.model_rebuild()