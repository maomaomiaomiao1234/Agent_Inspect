import pytest

from agent_trace_review.demo import demo_bundles
from agent_trace_review.storage import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "data")


@pytest.fixture
def bundle():
    return demo_bundles()[0]
