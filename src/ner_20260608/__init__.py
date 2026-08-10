"""Installable package for the Bohemia typed-graph dataset and query helpers.

The package bundles the reusable graph/schema/loader code together with the
produced JSONL artifacts so callers can do:

    from ner_20260608 import load_bohemia_graph

and query the packaged graph directly after installing the wheel.
"""

from importlib.resources import files
from pathlib import Path

from .graph import Graph
from .loader import InstanceSet, load_graph, load_instances

__all__ = [
    "Graph",
    "InstanceSet",
    "data_path",
    "load_bohemia_graph",
    "load_bohemia_instances",
    "load_graph",
    "load_instances",
]


def data_path(filename: str) -> Path:
    """Return a filesystem path to a bundled data file.

    Uses ``importlib.resources`` so the package works when installed from a
    wheel, not just from a source checkout.
    """
    return Path(str(files(__package__).joinpath("data", filename)))


def load_bohemia_instances(
    *, warn: bool = True, sentence_cutoff: int | None = None
) -> InstanceSet:
    """Load the bundled Bohemia JSONL artifacts into an ``InstanceSet``.

    sentence_cutoff — if given, only triplets whose sentence_ids are all
    strictly less than this value are loaded.  See ``load_instances``.
    """
    return load_instances(
        entities=data_path("bohemia_entities.jsonl"),
        events=data_path("bohemia_events.jsonl"),
        moments=data_path("bohemia_moments.jsonl"),
        triplets=data_path("bohemia_triplets.jsonl"),
        warn=warn,
        sentence_cutoff=sentence_cutoff,
    )


def load_bohemia_graph(
    *, warn: bool = True, sentence_cutoff: int | None = None
) -> Graph:
    """Load the bundled Bohemia JSONL artifacts into a ready-to-query ``Graph``.

    sentence_cutoff — if given, only triplets whose sentence_ids are all
    strictly less than this value are loaded.  See ``load_instances``.
    """
    return load_graph(
        entities=data_path("bohemia_entities.jsonl"),
        events=data_path("bohemia_events.jsonl"),
        moments=data_path("bohemia_moments.jsonl"),
        triplets=data_path("bohemia_triplets.jsonl"),
        warn=warn,
        sentence_cutoff=sentence_cutoff,
    )
