from types import SimpleNamespace

import pytest

from ndwinfo.matching.source_binding import _resolve_graph


class _Session:
    def __init__(self, graph):
        self.graph = graph

    def scalar(self, _query):
        return self.graph


def test_ready_graph_requires_explicit_shadow_permission():
    graph = SimpleNamespace(id=7, status="ready", is_active=False)
    with pytest.raises(RuntimeError, match="is not active"):
        _resolve_graph(_Session(graph), 7)

    assert _resolve_graph(_Session(graph), 7, allow_inactive=True) is graph


def test_shadow_permission_never_accepts_failed_graph():
    graph = SimpleNamespace(id=8, status="failed", is_active=False)
    with pytest.raises(RuntimeError, match="is not active"):
        _resolve_graph(_Session(graph), 8, allow_inactive=True)


def test_shadow_permission_requires_an_explicit_import_id():
    graph = SimpleNamespace(id=9, status="ready", is_active=False)
    with pytest.raises(RuntimeError, match="is not active"):
        _resolve_graph(_Session(graph), None, allow_inactive=True)
