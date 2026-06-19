import importlib.util


def pytest_ignore_collect(collection_path, config):
    if collection_path.name == "test_promote.py":
        if importlib.util.find_spec("promote") is None:
            return True
