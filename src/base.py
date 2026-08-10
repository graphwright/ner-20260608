"""Base package for the typed graph model.

Realizes the 4-tuple (T, Phi, V, tau) as a Pydantic class hierarchy: an
`Instance` root (membership in V) with two disjoint sorts, `EntityInstance`
and `BaseStatement`. Domain-specific entity and predicate types are declared
by subclassing these; see example.py for a worked domain.
"""

import sys
from typing import (
    Any,
    ClassVar,
    ForwardRef,
    Literal,
    Self,
    TypeVar,
    cast,
    get_args,
    get_origin,
)

from pydantic import BaseModel, ConfigDict, InstanceOf, model_validator

# truth_status vocabulary from formal-defns.md
TruthStatus = Literal[
    "asserted_true",
    "asserted_false",
    "hypothetical",
    "disputed",
    "retracted",
]

# How a claim was derived. A closed vocabulary (like TruthStatus) so values do not
# drift into "inferred" / "inference" / "derived" variants.
ExtractionMethod = Literal[
    "manual",
    "inferred",
    "quotation",
    "model_extraction",
]


class Provenance(BaseModel):
    """One provenance record describing how a statement was produced."""

    model_config = ConfigDict(frozen=True)

    source: str
    extraction_method: ExtractionMethod


class Instance(BaseModel):
    """A member of V. Common root of entity instances and statements."""

    model_config = ConfigDict(frozen=True)

    id: str
    provisional: ClassVar[bool] = False


class EntityInstance(Instance):
    """A member of V whose type is an entity type. Entity types are
    subclasses of this."""


SubjectT = TypeVar("SubjectT", bound=Instance)
ObjectT = TypeVar("ObjectT", bound=Instance)


class BaseStatement[SubjectT: Instance, ObjectT: Instance](Instance):
    """A predicate instance: a member of V and of the derived edge set E
    (E subset of V, since BaseStatement inherits Instance).

    Domain and range are sets of types, dom(p) and ran(p). Each is expressed as
    the single type parameter SubjectT / ObjectT, bound to a Union when the set
    has more than one member -- e.g. BaseStatement[Person | Organization, Vehicle]
    means dom = {Person, Organization}, ran = {Vehicle}. A singleton set is just
    one type. There is no custom validation; mypy enforces membership statically
    and Pydantic at construction time.

    Both SubjectT and ObjectT are bound to Instance (not EntityInstance), so
    either slot may be filled by a statement as well as an entity. Agentive
    predicates (e.g. KnewAt, an agent's attitude toward a proposition) typically
    keep their subject entity-typed by restricting SubjectT in their own
    declaration; symmetric relational predicates over propositions (e.g.
    Contradicts) may declare both slots as statement-typed. See R8.

    For a domain or range of "any statement", use `AnyStatement` (below) rather
    than a bare or Any-parametrized BaseStatement, so the concrete predicate type
    is preserved.

    `provenance` is stored as `tuple[Provenance, ...] | None`. For convenience,
    callers may pass a single `Provenance` or a `list[Provenance]`; the validator
    normalizes either form to a tuple before model construction.
    """

    subject: SubjectT
    object_: ObjectT
    truth_status: TruthStatus = "hypothetical"
    provenance: tuple[Provenance, ...] | None = None

    @model_validator(mode="before")
    @classmethod
    def _ensure_provenance_tuple(cls, data: dict[str, Any]) -> Any:
        """Normalize single or list provenance inputs to a tuple."""
        # if not isinstance(data, dict):
        #     return data
        provenance = data.get("provenance")
        if provenance is None or isinstance(provenance, tuple):
            return data
        normalized = dict(data)
        if isinstance(provenance, list):
            normalized["provenance"] = tuple(cast(list[Provenance], provenance))
        else:
            normalized["provenance"] = (provenance,)
        return normalized

    @model_validator(mode="after")
    def _reject_empty_provenance(self) -> Self:
        if self.provenance == ():
            raise ValueError(
                "provenance must contain at least one record when present; "
                "use None for ungrounded statements"
            )
        return self

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # Subscripting BaseStatement[...] makes Pydantic create generic submodels
        # while this module is still loading (e.g. the PartnerT bound below), which
        # fires this hook before the validator is defined. Skip until it exists;
        # real domain subclasses are always created after base.py finishes loading.
        validate = globals().get("_validate_inverse_declaration")
        if validate is not None:
            validate(cls)


# A domain or range meaning "any statement" (higher-order predication). Use this
# rather than a bare or Any-parametrized BaseStatement: InstanceOf validates by
# isinstance and keeps the value as-is, so the statement's concrete predicate type
# (tau) is preserved. A parametrized `BaseStatement[Any, Any]` slot would instead
# rebuild the stored value as the base class, discarding its type. Trade-off: a
# statement supplied as a raw dict is rejected -- reconstruct statements via the
# loader and pass instances, which is what the serialization design does anyway.
AnyStatement = InstanceOf[BaseStatement[Any, Any]]

# Shorthand for "a statement of any subject/object type", for annotations that
# would otherwise repeat BaseStatement[Any, Any] (and its type: ignore[type-arg]).
type AnyStmt = BaseStatement[Any, Any]


# ---------------------------------------------------------------------------
# Traits: declarative semantic properties of a predicate *type* (R1).
#
# A trait belongs to the predicate type, never to an individual statement. It
# is realized as a mixin class inherited alongside a BaseStatement subclass:
#
#     class Knows(BaseStatement[Person, Person], Symmetric): ...
#
# All traits share a common `Trait` marker base, enabling uniform introspection
# with issubclass(x, Trait). The unparameterized traits are plain markers,
# introspectable with issubclass(). Inverse is generic, parameterized by the
# partner predicate type and resolved with get_inverse(). Rule (the Datalog
# escape hatch) has no clean type-level expression, so it is declared in prose
# on the predicate class and realized as a callable the inference engine
# invokes -- not here.
# ---------------------------------------------------------------------------


class Trait:
    """Marker base for all semantic predicate traits, enabling issubclass(x, Trait)."""


class Symmetric(Trait):
    """p(x, y) implies p(y, x)."""


class Transitive(Trait):
    """p(x, y) and p(y, z) imply p(x, z)."""


class Functional(Trait):
    """Each subject has at most one object under p."""


class InverseFunctional(Trait):
    """Each object has at most one subject under p."""


PartnerT = TypeVar("PartnerT", bound=BaseStatement[Any, Any])


class Inverse[PartnerT: BaseStatement[Any, Any]](Trait):
    """p(x, y) implies p'(y, x), where the partner predicate p' is supplied as
    the type argument -- e.g. class ChildOf(BaseStatement[...], Inverse[ParentOf]).

    Declared one-way: `get_inverse(ChildOf)` is `ParentOf`, but
    `get_inverse(ParentOf)` is `None` unless `ParentOf` also declares the inverse.
    The declaring class's domain/range must be the swap of the partner's; a
    mismatch is a TypeError at import (see `_validate_inverse_declaration`)."""


def get_inverse(
    stmt_type: type[BaseStatement[Any, Any]],
) -> type[BaseStatement[Any, Any]] | None:
    """Return the partner predicate type if `stmt_type` declares Inverse[...],
    else None.

    Resolves the type argument from the declared bases. A partner given as a
    forward reference -- `Inverse["ParentOf"]`, needed when the partner is
    defined later or the two predicates are mutual inverses -- is resolved
    against the declaring module's globals. This is runtime introspection of the
    schema; it does not touch or revalidate instances.
    """
    for base in getattr(stmt_type, "__orig_bases__", ()):
        if get_origin(base) is Inverse:
            (partner,) = get_args(base)
            if isinstance(partner, ForwardRef):
                partner = partner.__forward_arg__
            if isinstance(partner, str):
                module = sys.modules.get(stmt_type.__module__)
                resolved = getattr(module, partner, None)
                if resolved is None:
                    raise NameError(
                        f"Inverse partner {partner!r} of {stmt_type.__name__} is "
                        f"not resolvable in module {stmt_type.__module__!r}"
                    )
                partner = resolved
            return cast(type[BaseStatement[Any, Any]], partner)
    return None


def _tn(t: Any) -> str:
    """A display name for a type or type expression (handles unions with no __name__)."""
    return getattr(t, "__name__", repr(t))


def _domain_range(cls: type) -> tuple[Any, Any] | None:
    """The (subject_type, object_type) type arguments of a predicate class.

    A parametrized `BaseStatement[A, B]` is a concrete Pydantic submodel (not a
    typing alias), so `get_origin`/`get_args` do not see it; the arguments live in
    `__pydantic_generic_metadata__` on the submodel in the class's MRO.
    """
    for c in getattr(cls, "__mro__", ()):
        meta = getattr(c, "__pydantic_generic_metadata__", None)
        if meta and meta.get("origin") is BaseStatement:
            args = meta.get("args")
            if args and len(args) == 2:
                return args[0], args[1]
    return None


def _validate_inverse_declaration(cls: type) -> None:  # pyright: ignore[reportUnusedFunction]
    """If `cls` declares `Inverse[P]`, require its domain/range to be P's swapped.

    Runs from `BaseStatement.__init_subclass__`, so a mismatch is caught at import.
    Skips a forward-reference partner (unresolvable at definition time) and any
    case where the domain/range cannot be read as two concrete type arguments.
    """
    partner: Any = None
    for base in getattr(cls, "__orig_bases__", ()):
        if get_origin(base) is Inverse:
            (partner,) = get_args(base)
            break
    if partner is None or isinstance(partner, (str, ForwardRef)):
        return
    own = _domain_range(cls)
    part = _domain_range(partner)
    if own is None or part is None:
        return
    (a, b), (c, d) = own, part
    if not (a == d and b == c):
        raise TypeError(
            f"{cls.__name__} declares Inverse[{_tn(partner)}] but its domain/range "
            f"({_tn(a)}, {_tn(b)}) is not the swap of {_tn(partner)}'s "
            f"({_tn(c)}, {_tn(d)})"
        )
