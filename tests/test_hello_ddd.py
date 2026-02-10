"""Unit tests for the hello_ddd module."""

import pytest

from src.hello_ddd import greet


@pytest.mark.unit
class TestGreet:
    """Tests for the greet function."""

    def test_greet_returns_hello_ddd(self):
        """Test that greet() returns the expected greeting."""
        assert greet() == "Hello, DDD!"

    def test_greet_returns_string(self):
        """Test that greet() returns a string type."""
        result = greet()
        assert isinstance(result, str)
