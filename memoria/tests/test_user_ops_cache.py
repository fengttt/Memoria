"""Unit tests for _with_cache in user_ops — no DB required."""

import json
import time
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from memoria.api.routers.user_ops import _cache, _TTL, _with_cache


@pytest.fixture(autouse=True)
def clear_cache():
    _cache.clear()
    yield
    _cache.clear()


def _mock_db_factory(rows=None):
    """Return a factory whose every call returns the same mock DB session.
    `rows`: if not None, db.execute().first() returns this value on first call.
    """
    db = MagicMock()
    db.execute.return_value.first.return_value = rows
    factory = MagicMock(return_value=db)
    return factory, db


class TestInMemoryCache:
    def test_first_call_runs_fn(self):
        factory, db = _mock_db_factory()
        result = _with_cache("u1", "consolidate", lambda: {"status": "done"}, False, factory)
        assert result == {"status": "done"}

    def test_second_call_returns_cached(self):
        factory, db = _mock_db_factory()
        _with_cache("u1", "consolidate", lambda: {"status": "done"}, False, factory)

        ran = False

        def should_not_run():
            nonlocal ran
            ran = True
            return {}

        result = _with_cache("u1", "consolidate", should_not_run, False, factory)
        assert result["cached"] is True
        assert "cooldown_remaining_s" in result
        assert not ran

    def test_force_bypasses_cache(self):
        factory, db = _mock_db_factory()
        _with_cache("u1", "consolidate", lambda: {"v": 1}, False, factory)
        result = _with_cache("u1", "consolidate", lambda: {"v": 2}, True, factory)
        assert result == {"v": 2}


class TestDBFallback:
    def test_db_hit_within_ttl_returns_cached(self):
        """When in-memory cache is empty but DB has a recent run, return cached."""
        recent_ts = datetime.fromtimestamp(time.time() - 60)
        db_result = (json.dumps({"from": "db"}), recent_ts)
        factory, db = _mock_db_factory(rows=db_result)

        ran = False

        def should_not_run():
            nonlocal ran
            ran = True
            return {}

        result = _with_cache("u1", "consolidate", should_not_run, False, factory)
        assert result["cached"] is True
        assert result["from"] == "db"
        assert not ran

    def test_db_hit_populates_in_memory_cache(self):
        """DB cache hit should also populate in-memory cache for next call."""
        recent_ts = datetime.fromtimestamp(time.time() - 60)
        db_result = (json.dumps({"from": "db"}), recent_ts)
        factory, db = _mock_db_factory(rows=db_result)

        _with_cache("u1", "consolidate", lambda: {}, False, factory)
        assert ("u1", "consolidate") in _cache

    def test_db_expired_runs_fn(self):
        """DB row exists but TTL expired — should run fn."""
        old_ts = datetime.fromtimestamp(time.time() - _TTL["consolidate"] - 10)
        db_result = (json.dumps({"old": True}), old_ts)
        factory, db = _mock_db_factory(rows=db_result)

        result = _with_cache("u1", "consolidate", lambda: {"fresh": True}, False, factory)
        assert result == {"fresh": True}

    def test_db_no_rows_runs_fn(self):
        factory, db = _mock_db_factory(rows=None)
        result = _with_cache("u1", "reflect", lambda: {"insights": 3}, False, factory)
        assert result == {"insights": 3}


class TestPersistWrite:
    def test_result_written_to_db(self):
        db_read = MagicMock()
        db_read.execute.return_value.first.return_value = None
        db_write = MagicMock()
        factory = MagicMock(side_effect=[db_read, db_write])

        _with_cache("u1", "consolidate", lambda: {"status": "ok"}, False, factory)
        db_write.execute.assert_called_once()
        sql_arg = db_write.execute.call_args[0][0]
        assert "INSERT" in sql_arg.text
        db_write.commit.assert_called_once()

    def test_db_write_failure_still_returns_result(self):
        db_read = MagicMock()
        db_read.execute.return_value.first.return_value = None
        db_write = MagicMock()
        db_write.execute.side_effect = Exception("db down")
        factory = MagicMock(side_effect=[db_read, db_write])

        result = _with_cache("u1", "consolidate", lambda: {"ok": True}, False, factory)
        assert result == {"ok": True}
        db_write.rollback.assert_called()
