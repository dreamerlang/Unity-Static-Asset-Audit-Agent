"""Test fixtures shared across all test modules."""
import os

import pytest

TEST_PROJECT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "test_project")
)


@pytest.fixture(scope="session")
def test_project_root():
    """Return the absolute path to the test_project root."""
    return TEST_PROJECT
