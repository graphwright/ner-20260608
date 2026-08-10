"""loader.py — JSONL → Pydantic hydration layer.

Reads the pipeline JSONL files produced by the NER pipeline and constructs live
EntityInstance and BaseStatement objects conforming to ``holmes_schema.py``.

This copy is package-relative so it works when installed from a wheel.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast, get_origin, get_type_hints

from pydantic import ValidationError

from base import BaseStatement, Instance

from . import holmes_schema as _schema
from .holmes_schema import (
    Event,
    Location,
    Moment,
    Object,
    Organization,
    OtherEntity,
    Person,
    SherlockEntity,
)

if TYPE_CHECKING:
    from .graph import Graph

_NER_TYPE_TO_CLASS: dict[str, type[SherlockEntity]] = {
    "person": Person,
    "organization": Organization,
    "place": Location,
    "object": Object,
    "other": OtherEntity,
}

_VALID_TRUTH_STATUS: set[str] = {
    "asserted_true",
    "asserted_false",
    "hypothetical",
    "disputed",
    "retracted",
}


def _entity_class(ner_type: str) -> type[SherlockEntity] | None:
    """Return the EntityInstance subclass for a NER type string, or None if unmapped.

    Callers must warn and skip on None — do not silently coerce unmapped types to
    Object, as that can produce downstream domain/range violations.
    """
    return _NER_TYPE_TO_CLASS.get(ner_type.lower())


_PREDICATE_CLASSES: dict[str, type[BaseStatement[Any, Any]]] = {
    name: obj
    for name, obj in vars(_schema).items()
    if isinstance(obj, type)
    and issubclass(obj, BaseStatement)
    and obj is not BaseStatement
}


def _predicate_class(name: str) -> type[BaseStatement[Any, Any]] | None:
    return _PREDICATE_CLASSES.get(name)


def _is_higher_order(pred_cls: type[BaseStatement[Any, Any]]) -> bool:
    """Return True if subject or object_ is annotated as BaseStatement.

    Uses get_type_hints() rather than __annotations__ so inherited field
    annotations are visible and forward references are resolved.
    """
    try:
        hints = get_type_hints(pred_cls)
    except ValidationError:
        hints = pred_cls.__annotations__
    for fname in ("subject", "object_"):
        ann = hints.get(fname)
        if ann is BaseStatement:
            return True
        if isinstance(ann, type) and issubclass(ann, BaseStatement):
            return True
        if get_origin(ann) is BaseStatement:
            return True
        if isinstance(ann, str) and ann == "BaseStatement":
            return True
    return False


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


@dataclass
class InstanceSet:
    """All hydrated instances keyed by id."""

    by_id: dict[str, Instance] = field(default_factory=dict)
    by_url: dict[str, Instance] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def add(self, inst: Instance, wiki_url: str | None = None) -> None:
        self.by_id[inst.id] = inst
        if wiki_url:
            self.by_url[wiki_url] = inst

    def get(self, entity_id: str) -> Instance | None:
        result = self.by_id.get(entity_id)
        if result is None:
            result = self.by_url.get(entity_id)
        return result


def _hydrate_entities(records: list[dict[str, Any]], iset: InstanceSet) -> None:
    # entity_id and the wiki: / place: prefix come from the upstream extraction
    # pipeline; this loader does not canonicalize or validate them.
    for rec in records:
        ner_type = rec.get("type", "other")
        cls = _entity_class(ner_type)
        entity_id = rec.get("entity_id") or rec.get("id")
        if not entity_id:
            iset.warnings.append(f"entity record missing id: {rec!r:.120}")
            continue
        if cls is None:
            iset.warnings.append(
                f"entity {entity_id!r}: unmapped NER type {ner_type!r} — skipping; "
                "add a mapping to _NER_TYPE_TO_CLASS or a new entity type to the schema"
            )
            continue
        try:
            inst = cls(
                id=entity_id,
                canonical=rec.get("canonical", entity_id),
                aliases=tuple(rec.get("aliases", ())),
                wiki_url=rec.get("wiki_url"),
                raw_type=rec.get("type"),
            )
        except ValidationError as exc:
            iset.warnings.append(f"entity {entity_id!r} failed: {exc}")
            continue
        iset.add(inst, wiki_url=rec.get("wiki_url"))


def _hydrate_events(records: list[dict[str, Any]], iset: InstanceSet) -> None:
    for rec in records:
        event_id = rec.get("id")
        if not event_id:
            iset.warnings.append(f"event record missing id: {rec!r:.120}")
            continue
        try:
            inst = Event(
                id=event_id,
                canonical=rec.get("description", event_id),
                aliases=(),
                raw_type="event",
            )
        except ValidationError as exc:
            iset.warnings.append(f"event {event_id!r} failed: {exc}")
            continue
        iset.add(inst)


def _hydrate_moments(records: list[dict[str, Any]], iset: InstanceSet) -> None:
    for rec in records:
        moment_id = rec.get("id")
        if not moment_id:
            iset.warnings.append(f"moment record missing id: {rec!r:.120}")
            continue
        try:
            inst = Moment(
                id=moment_id,
                canonical=rec.get("label", moment_id),
                aliases=(),
                raw_type="moment",
            )
        except ValidationError as exc:
            iset.warnings.append(f"moment {moment_id!r} failed: {exc}")
            continue
        iset.add(inst)


def _truth_status(
    raw: str | None,
    # warnings_list and triplet_id are optional so bare _truth_status(raw) works
    # in tests; the real loader path always supplies both via _build_predicate_kwargs.
    warnings_list: list[str] | None = None,
    triplet_id: str = "?",
) -> str:
    if raw is None:
        return "hypothetical"
    if raw not in _VALID_TRUTH_STATUS:
        if warnings_list is not None:
            warnings_list.append(
                f"triplet {triplet_id!r}: unrecognised truth_status {raw!r} — "
                "defaulting to HYPOTHETICAL; this triplet will be invisible to "
                "asserted-graph traversals (bfs, transitive_closure, MCP tools)"
            )
        return "hypothetical"
    return raw


def _build_predicate_kwargs(
    rec: dict[str, Any],
    subject: Instance,
    object_: Instance,
    iset: InstanceSet,
) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "id": rec["id"],
        "truth_status": _truth_status(
            rec.get("truth_status"),
            warnings_list=iset.warnings,
            triplet_id=rec.get("id", "?"),
        ),
        "subject": subject,
        "object_": object_,
    }
    if "extraction_method" in rec:
        kwargs["story_id"] = rec.get("story_id", "unknown")
        kwargs["paragraph_index"] = rec.get("paragraph_index")
        kwargs["sentence_ids"] = tuple(rec.get("sentence_ids", ()))
        kwargs["asserting_narrator_id"] = rec.get("asserting_narrator_id")
        kwargs["raw_extraction_method"] = rec["extraction_method"]
        kwargs["extraction_confidence"] = float(rec.get("extraction_confidence", 1.0))
    nc = rec.get("narrator_confidence")
    if nc is not None:
        kwargs["narrator_confidence"] = float(nc)
    if rec.get("predicate") == "KnewAt" and "moment_id" in rec:
        moment = iset.get(rec["moment_id"])
        if isinstance(moment, Moment):
            kwargs["moment"] = moment
        else:
            iset.warnings.append(
                f"KnewAt {rec['id']!r}: moment_id {rec['moment_id']!r} not found"
            )
    return kwargs


def _hydrate_triplets(records: list[dict[str, Any]], iset: InstanceSet) -> None:
    deferred: list[dict[str, Any]] = []

    for rec in records:
        pred_name = rec.get("predicate", "")
        pred_cls = _predicate_class(pred_name)
        if pred_cls is None:
            iset.warnings.append(
                f"unknown predicate {pred_name!r} in {rec.get('id')!r}"
            )
            continue
        if _is_higher_order(pred_cls):
            deferred.append(rec)
            continue
        _hydrate_one_triplet(rec, pred_cls, iset)

    # Fixpoint: retry higher-order predicates until no new referents are added.
    # One pass handles KnewAt → Knows; two handles Contradicts → KnewAt → Knows;
    # and so on for arbitrarily deep chains (schema R8 sets no depth limit).
    # Termination is keyed on whether the deferred set shrank (not on global
    # iset.by_id growth, which would re-attempt genuinely unresolvable triplets
    # on every pass until unrelated chains exhaust).
    while deferred:
        remaining = []
        for rec in deferred:
            pred_name = rec.get("predicate", "")
            pred_cls = _predicate_class(pred_name)
            if pred_cls is None:
                continue
            subject_id = rec.get("subject_id")
            object_id = rec.get("object_id")
            if (subject_id and iset.get(subject_id) is None) or (
                object_id and iset.get(object_id) is None
            ):
                remaining.append(rec)
                continue
            _hydrate_one_triplet(rec, pred_cls, iset)
        if len(remaining) == len(deferred):
            for rec in remaining:
                trip_id = rec.get("id", "?")
                subject_id = rec.get("subject_id")
                object_id = rec.get("object_id")
                iset.warnings.append(
                    f"higher-order triplet {trip_id!r}: referent(s) "
                    f"subject={subject_id!r} object={object_id!r} "
                    "unresolvable after fixpoint — skipping"
                )
            break
        deferred = remaining


def _hydrate_one_triplet(
    rec: dict[str, Any], pred_cls: type[BaseStatement[Any, Any]], iset: InstanceSet
) -> None:
    trip_id = rec.get("id", "?")
    subject_id = rec.get("subject_id")
    object_id = rec.get("object_id")

    subject = iset.get(subject_id) if subject_id else None
    object_ = iset.get(object_id) if object_id else None

    if subject is None:
        iset.warnings.append(
            f"triplet {trip_id!r}: subject_id {subject_id!r} not found — skipping"
        )
        return
    if object_ is None:
        iset.warnings.append(
            f"triplet {trip_id!r}: object_id {object_id!r} not found — skipping"
        )
        return

    kwargs = _build_predicate_kwargs(rec, subject, object_, iset)
    try:
        inst = pred_cls(**cast(dict[str, Any], kwargs))
        iset.add(inst)
    except ValidationError as exc:
        iset.warnings.append(f"triplet {trip_id!r} ({pred_cls.__name__}) failed: {exc}")


def load_instances(
    entities: Path,
    events: Path,
    moments: Path,
    triplets: Path | None = None,
    *,
    warn: bool = True,
    sentence_cutoff: int | None = None,
) -> InstanceSet:
    """Hydrate JSONL files into an InstanceSet.

    sentence_cutoff — if given, only triplets whose sentence_ids are all
    strictly less than this value are loaded.  Use this to build a
    temporally-bounded subgraph (e.g. everything up to but not including
    Holmes's revelation at sentence 485).
    """
    iset = InstanceSet()

    _hydrate_entities(_load_jsonl(entities), iset)
    _hydrate_events(_load_jsonl(events), iset)
    _hydrate_moments(_load_jsonl(moments), iset)
    if triplets is not None and triplets.exists():
        raw = _load_jsonl(triplets)
        if sentence_cutoff is not None:
            raw = [
                r
                for r in raw
                if not r.get("sentence_ids") or max(r["sentence_ids"]) < sentence_cutoff
            ]
        _hydrate_triplets(raw, iset)

    if warn and iset.warnings:
        for w in iset.warnings:
            print(f"[loader] {w}", file=sys.stderr)

    return iset


def load_graph(
    entities: Path,
    events: Path,
    moments: Path,
    triplets: Path | None = None,
    *,
    warn: bool = True,
    sentence_cutoff: int | None = None,
) -> Graph:
    """Hydrate JSONL files and return a ready-to-use Graph.

    sentence_cutoff — see load_instances.
    """
    from .graph import Graph

    iset = load_instances(
        entities, events, moments, triplets, warn=warn, sentence_cutoff=sentence_cutoff
    )
    return Graph(iset.by_id.values())
