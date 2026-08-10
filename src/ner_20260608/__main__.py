import sys

from ner_20260608 import load_bohemia_graph


def main() -> None:
    g = load_bohemia_graph(warn=False)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"

    if cmd == "describe" and len(sys.argv) > 2:
        print(g.describe(sys.argv[2]))

    elif cmd == "edges-from" and len(sys.argv) > 2:
        edges = g.edges_from(sys.argv[2], truth="asserted_true")
        g.print_edges(edges)

    elif cmd == "bfs" and len(sys.argv) > 2:
        layers = g.bfs(sys.argv[2:], max_hops=2)
        for i, layer in enumerate(layers):
            for eid in sorted(layer):
                print(f"hop{i}  {g.describe(eid)}")

    else:
        print("usage: python -m ner_20260608 describe|edges-from|bfs <entity_id>...")


if __name__ == "__main__":
    main()
