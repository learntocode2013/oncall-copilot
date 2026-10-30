"""The package imports, and its only public name is still a stub."""

import oncall_graph
import pytest
from oncall_graph import build_graph


def test_import_graph():
    assert oncall_graph.__all__ == ["build_graph"]


def test_build_graph_is_not_implemented_yet():
    with pytest.raises(NotImplementedError):
        build_graph({})
