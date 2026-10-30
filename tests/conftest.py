import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def clear_throttle_cache():
    """Throttle counters live in the shared cache and would leak between tests."""
    cache.clear()
    yield
    cache.clear()
