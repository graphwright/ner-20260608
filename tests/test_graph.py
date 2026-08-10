"""Tests for graph.py — in-memory graph traversal and query."""

import warnings
from typing import TypedDict

import pytest

from ner_20260608 import load_bohemia_graph
from ner_20260608.graph import Graph, _canonicalize_id
from ner_20260608.holmes_schema import Knows, LocatedIn, Location, Person

# ---- shared provenance kwargs ----
#
# TruthStatus is a Literal["asserted_true", ...] alias (base.py), not an Enum —
# statements take truth_status as a plain string, not a TruthStatus.MEMBER.


class _Provenance(TypedDict):
    """Matches StoryMetadata's field names/types so **_PROV unpacking into a
    statement constructor is checked key-by-key by mypy instead of collapsing
    to dict[str, object]."""

    story_id: str
    paragraph_index: int | None
    raw_extraction_method: str | None
    extraction_confidence: float | None


_PROV: _Provenance = {
    "story_id": "bohemia",
    "paragraph_index": 1,
    "raw_extraction_method": "manual",
    "extraction_confidence": 1.0,
}


# ---- fixtures ----


@pytest.fixture(scope="module")
def person_graph() -> Graph:
    """Holmes -[Knows/TRUE]-> Watson, Holmes -[Knows/FALSE]-> Irene."""
    holmes = Person(id="wiki:Sherlock_Holmes", canonical="Sherlock Holmes")
    watson = Person(id="wiki:John_Watson", canonical="John Watson")
    irene = Person(id="wiki:Irene_Adler", canonical="Irene Adler")
    knows_hw = Knows(
        id="stmt:knows:hw",
        subject=holmes,
        object_=watson,
        truth_status="asserted_true",
        **_PROV,
    )
    knows_hi = Knows(
        id="stmt:knows:hi",
        subject=holmes,
        object_=irene,
        truth_status="asserted_false",
        **_PROV,
    )
    return Graph([holmes, watson, irene, knows_hw, knows_hi])


@pytest.fixture(scope="module")
def location_graph() -> Graph:
    """Baker Street -[LocatedIn]-> London -[LocatedIn]-> England.

    Uses synthetic place: ids (not real corpus entities) to test traversal logic
    in isolation from id-canonicalization concerns.
    """
    baker = Location(id="place:Baker_Street", canonical="Baker Street")
    london = Location(id="place:London", canonical="London")
    england = Location(id="place:England", canonical="England")
    prov: _Provenance = {**_PROV, "paragraph_index": 0}
    loc_bl = LocatedIn(
        id="stmt:loc:bl",
        subject=baker,
        object_=london,
        truth_status="asserted_true",
        **prov,
    )
    loc_le = LocatedIn(
        id="stmt:loc:le",
        subject=london,
        object_=england,
        truth_status="asserted_true",
        **prov,
    )
    return Graph([baker, london, england, loc_bl, loc_le])


# ---- integration smoke tests ----


def test_graph_loads() -> None:
    g = load_bohemia_graph(warn=False)
    assert len(g.by_id) > 0


def test_canonical_id_lookup() -> None:
    g = load_bohemia_graph(warn=False)
    assert g.get("wiki:Sherlock_Holmes") is not None


def test_full_url_alias_lookup() -> None:
    g = load_bohemia_graph(warn=False)
    assert g.get("https://bakerstreet.fandom.com/wiki/Sherlock_Holmes") is not None


def test_describe_canonical() -> None:
    g = load_bohemia_graph(warn=False)
    result = g.describe("wiki:Sherlock_Holmes")
    assert "not found" not in result


def test_describe_full_url() -> None:
    g = load_bohemia_graph(warn=False)
    result = g.describe("https://bakerstreet.fandom.com/wiki/Sherlock_Holmes")
    assert "not found" not in result


# ---- _canonicalize_id ----


class TestCanonicalizeId:
    def test_full_url_normalized(self) -> None:
        result = _canonicalize_id("https://bakerstreet.fandom.com/wiki/Sherlock_Holmes")
        assert result == "wiki:Sherlock_Holmes"

    def test_canonical_id_unchanged(self) -> None:
        assert _canonicalize_id("wiki:Sherlock_Holmes") == "wiki:Sherlock_Holmes"

    def test_unrelated_id_unchanged(self) -> None:
        assert _canonicalize_id("place:London") == "place:London"

    def test_partial_prefix_unchanged(self) -> None:
        assert (
            _canonicalize_id("https://bakerstreet.fandom.com/")
            == "https://bakerstreet.fandom.com/"
        )


# ---- Graph id-collision warning ----


class TestIdCollision:
    def test_collision_warns(self) -> None:
        p1 = Person(id="wiki:Holmes", canonical="Sherlock Holmes")
        p2 = Person(id="wiki:Holmes", canonical="Sherlock Holmes (duplicate)")
        with pytest.warns(UserWarning, match="id collision"):
            Graph([p1, p2])

    def test_no_collision_no_warning(self) -> None:
        p1 = Person(id="wiki:Holmes", canonical="Sherlock Holmes")
        p2 = Person(id="wiki:Watson", canonical="Dr. Watson")

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            Graph([p1, p2])  # should not raise


# ---- Graph.get ----


class TestGraphGet:
    def test_get_canonical(self, person_graph: Graph) -> None:
        assert person_graph.get("wiki:Sherlock_Holmes") is not None

    def test_get_full_url(self, person_graph: Graph) -> None:
        assert (
            person_graph.get("https://bakerstreet.fandom.com/wiki/Sherlock_Holmes")
            is not None
        )

    def test_get_missing_returns_none(self, person_graph: Graph) -> None:
        assert person_graph.get("wiki:Moriarty") is None


# ---- Graph.edges_from ----


class TestEdgesFrom:
    def test_all_edges_returned(self, person_graph: Graph) -> None:
        edges = person_graph.edges_from("wiki:Sherlock_Holmes")
        assert len(edges) == 2

    def test_pred_type_filter(self, person_graph: Graph) -> None:
        edges = person_graph.edges_from("wiki:Sherlock_Holmes", pred_type=Knows)
        assert len(edges) == 2

    def test_pred_type_filter_no_match(self, person_graph: Graph) -> None:
        edges = person_graph.edges_from("wiki:Sherlock_Holmes", pred_type=LocatedIn)
        assert len(edges) == 0

    def test_truth_filter_string(self, person_graph: Graph) -> None:
        edges = person_graph.edges_from("wiki:Sherlock_Holmes", truth="asserted_true")
        assert len(edges) == 1
        assert edges[0].id == "stmt:knows:hw"

    def test_truth_filter_false_value(self, person_graph: Graph) -> None:
        edges = person_graph.edges_from("wiki:Sherlock_Holmes", truth="asserted_false")
        assert len(edges) == 1
        assert edges[0].id == "stmt:knows:hi"

    def test_full_url_normalized(self, person_graph: Graph) -> None:
        edges = person_graph.edges_from(
            "https://bakerstreet.fandom.com/wiki/Sherlock_Holmes"
        )
        assert len(edges) == 2

    def test_missing_entity_returns_empty(self, person_graph: Graph) -> None:
        assert person_graph.edges_from("wiki:Moriarty") == []

    def test_leaf_node_returns_empty(self, person_graph: Graph) -> None:
        assert person_graph.edges_from("wiki:John_Watson") == []


# ---- Graph.edges_to ----


class TestEdgesTo:
    def test_inward_edges(self, person_graph: Graph) -> None:
        edges = person_graph.edges_to("wiki:John_Watson")
        assert len(edges) == 1
        assert edges[0].id == "stmt:knows:hw"

    def test_truth_filter(self, person_graph: Graph) -> None:
        edges = person_graph.edges_to("wiki:Irene_Adler", truth="asserted_false")
        assert len(edges) == 1

    def test_pred_type_filter(self, person_graph: Graph) -> None:
        edges = person_graph.edges_to("wiki:John_Watson", pred_type=Knows)
        assert len(edges) == 1

    def test_pred_type_filter_no_match(self, person_graph: Graph) -> None:
        edges = person_graph.edges_to("wiki:John_Watson", pred_type=LocatedIn)
        assert len(edges) == 0

    def test_full_url_normalized(self, person_graph: Graph) -> None:
        edges = person_graph.edges_to("https://bakerstreet.fandom.com/wiki/John_Watson")
        assert len(edges) == 1

    def test_missing_entity_returns_empty(self, person_graph: Graph) -> None:
        assert person_graph.edges_to("wiki:Moriarty") == []


# ---- Graph.bfs ----


class TestBFS:
    def test_seed_in_layer_zero(self, person_graph: Graph) -> None:
        layers = person_graph.bfs(["wiki:Sherlock_Holmes"], max_hops=1)
        assert "wiki:Sherlock_Holmes" in layers[0]

    def test_asserted_true_edge_traversed(self, person_graph: Graph) -> None:
        layers = person_graph.bfs(["wiki:Sherlock_Holmes"], max_hops=1)
        assert "wiki:John_Watson" in layers[1]

    def test_asserted_false_edge_not_traversed_by_default(
        self, person_graph: Graph
    ) -> None:
        layers = person_graph.bfs(["wiki:Sherlock_Holmes"], max_hops=1)
        assert "wiki:Irene_Adler" not in layers[1]

    def test_truth_values_override(self, person_graph: Graph) -> None:
        layers = person_graph.bfs(
            ["wiki:Sherlock_Holmes"],
            max_hops=1,
            truth_values=("asserted_true", "asserted_false"),
        )
        assert "wiki:John_Watson" in layers[1]
        assert "wiki:Irene_Adler" in layers[1]

    def test_statement_node_added_to_layer(self, person_graph: Graph) -> None:
        layers = person_graph.bfs(["wiki:Sherlock_Holmes"], max_hops=1)
        assert "stmt:knows:hw" in layers[1]

    def test_empty_seed(self, person_graph: Graph) -> None:
        layers = person_graph.bfs([], max_hops=3)
        assert layers[0] == set()

    def test_max_hops_respected(self, location_graph: Graph) -> None:
        layers = location_graph.bfs(["place:Baker_Street"], max_hops=1)
        assert "place:London" in layers[1]
        # England is 2 hops away; should not appear in layer[1]
        assert "place:England" not in layers[1]

    def test_two_hops_reaches_further(self, location_graph: Graph) -> None:
        layers = location_graph.bfs(["place:Baker_Street"], max_hops=2)
        all_visited = set().union(*layers)
        assert "place:England" in all_visited


# ---- Graph.transitive_closure ----


class TestTransitiveClosure:
    def test_full_chain(self, location_graph: Graph) -> None:
        reachable = location_graph.transitive_closure("place:Baker_Street", LocatedIn)
        assert "place:London" in reachable
        assert "place:England" in reachable

    def test_does_not_include_start_node(self, location_graph: Graph) -> None:
        reachable = location_graph.transitive_closure("place:Baker_Street", LocatedIn)
        assert "place:Baker_Street" not in reachable

    def test_leaf_node_returns_empty(self, location_graph: Graph) -> None:
        reachable = location_graph.transitive_closure("place:England", LocatedIn)
        assert len(reachable) == 0

    def test_wrong_pred_type_returns_empty(self, location_graph: Graph) -> None:
        reachable = location_graph.transitive_closure("place:Baker_Street", Knows)
        assert len(reachable) == 0


# ---- Graph.describe ----


class TestDescribe:
    def test_entity_describe(self, person_graph: Graph) -> None:
        result = person_graph.describe("wiki:Sherlock_Holmes")
        assert result == "Sherlock Holmes"

    def test_statement_describe(self, person_graph: Graph) -> None:
        result = person_graph.describe("stmt:knows:hw")
        assert "Knows" in result
        assert "Sherlock Holmes" in result
        assert "John Watson" in result
        assert "not found" not in result

    def test_missing_describe(self, person_graph: Graph) -> None:
        result = person_graph.describe("wiki:Moriarty")
        assert "not found" in result
        assert "wiki:Moriarty" in result

    def test_full_url_describe(self, person_graph: Graph) -> None:
        result = person_graph.describe(
            "https://bakerstreet.fandom.com/wiki/Sherlock_Holmes"
        )
        assert result == "Sherlock Holmes"
