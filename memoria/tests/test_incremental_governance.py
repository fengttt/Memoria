"""Unit tests for incremental governance, tiered reflection, and observability."""

from dataclasses import dataclass, field
from unittest.mock import MagicMock, patch

import pytest

from memoria.core.memory.reflection.engine import (
    ReflectionEngine,
    ReflectionResult,
    SynthesizedInsight,
)
from memoria.core.memory.reflection.importance import DAILY_THRESHOLD
from memoria.core.memory.interfaces import ReflectionCandidate
from memoria.core.memory.types import Memory, MemoryType, TrustTier


def _mem(mid: str = "m1", sid: str = "s1", score: float = 0.8) -> Memory:
    return Memory(
        memory_id=mid,
        user_id="u1",
        memory_type=MemoryType.SEMANTIC,
        content=f"content-{mid}",
        initial_confidence=score,
        session_id=sid,
    )


def _candidate(importance: float, signal: str = "semantic_cluster") -> ReflectionCandidate:
    c = ReflectionCandidate(
        memories=[_mem("m1", "s1"), _mem("m2", "s2")],
        signal=signal,
        session_ids=["s1", "s2"],
    )
    c.importance_score = importance
    return c


class TestTieredReflection:
    """Candidates below llm_threshold are returned without LLM calls."""

    def test_low_importance_skips_llm(self):
        provider = MagicMock()
        # One candidate below llm_threshold, one above
        provider.get_reflection_candidates.return_value = [
            _candidate(0.3),  # below both thresholds
            _candidate(0.6),  # above threshold, above llm_threshold
        ]
        writer = MagicMock()
        llm = MagicMock()
        llm.chat.return_value = '[]'

        engine = ReflectionEngine(
            provider, writer, llm,
            threshold=0.2,       # both pass this
            llm_threshold=0.5,   # only 0.6 passes this
        )
        result = engine.reflect("u1")

        assert result.candidates_found == 2
        assert result.candidates_passed == 2
        assert result.candidates_skipped_low_importance == 1
        assert len(result.low_importance_candidates) == 1
        assert result.llm_calls == 1  # only the high-importance one

    def test_all_high_importance_all_synthesized(self):
        provider = MagicMock()
        provider.get_reflection_candidates.return_value = [
            _candidate(0.8),
            _candidate(0.9),
        ]
        writer = MagicMock()
        llm = MagicMock()
        llm.chat.return_value = '[]'

        engine = ReflectionEngine(
            provider, writer, llm,
            threshold=0.5,
            llm_threshold=0.5,
        )
        result = engine.reflect("u1")

        assert result.candidates_skipped_low_importance == 0
        assert result.llm_calls == 2

    def test_all_below_threshold_no_work(self):
        provider = MagicMock()
        provider.get_reflection_candidates.return_value = [
            _candidate(0.1),
        ]
        writer = MagicMock()
        llm = MagicMock()

        engine = ReflectionEngine(
            provider, writer, llm,
            threshold=0.5,
            llm_threshold=0.5,
        )
        result = engine.reflect("u1")

        assert result.candidates_passed == 0
        assert result.llm_calls == 0


class TestIncrementalGovernance:
    """_has_changes_since_last_governance skips users with no new writes."""

    def _make_scheduler(self):
        from memoria.core.memory.tabular.governance import GovernanceScheduler

        db_factory = MagicMock()
        return GovernanceScheduler(db_factory)

    def test_no_prior_run_returns_true(self):
        sched = self._make_scheduler()
        db = MagicMock()
        # No prior governance run
        db.execute.return_value.scalar.return_value = None
        assert sched._has_changes_since_last_governance(db, "u1") is True

    def test_no_memories_returns_false(self):
        sched = self._make_scheduler()
        db = MagicMock()
        from datetime import datetime
        # Has prior run, but no memories
        db.execute.return_value.scalar.side_effect = [datetime(2026, 1, 1), None]
        assert sched._has_changes_since_last_governance(db, "u1") is False

    def test_newer_changes_returns_true(self):
        sched = self._make_scheduler()
        db = MagicMock()
        from datetime import datetime
        # Last run at Jan 1, latest change at Jan 2
        db.execute.return_value.scalar.side_effect = [
            datetime(2026, 1, 1),
            datetime(2026, 1, 2),
        ]
        assert sched._has_changes_since_last_governance(db, "u1") is True

    def test_no_changes_since_run_returns_false(self):
        sched = self._make_scheduler()
        db = MagicMock()
        from datetime import datetime
        # Last run at Jan 2, latest change at Jan 1
        db.execute.return_value.scalar.side_effect = [
            datetime(2026, 1, 2),
            datetime(2026, 1, 1),
        ]
        assert sched._has_changes_since_last_governance(db, "u1") is False

    def test_db_error_fails_open(self):
        sched = self._make_scheduler()
        db = MagicMock()
        db.execute.side_effect = Exception("db error")
        # Should fail-open: return True so governance still runs
        assert sched._has_changes_since_last_governance(db, "u1") is True


class TestObservability:
    """GovernanceCycleResult carries structured observability data."""

    def test_result_has_observability_fields(self):
        from memoria.core.memory.tabular.governance import GovernanceCycleResult

        r = GovernanceCycleResult()
        assert r.input_memories == 0
        assert r.users_processed == 0
        assert r.users_skipped_no_changes == 0
        assert r.reflection_candidates_found == 0
        assert r.reflection_candidates_synthesized == 0
        assert r.reflection_candidates_skipped_low_importance == 0

    def test_reflection_result_has_skip_count(self):
        r = ReflectionResult()
        assert r.candidates_skipped_low_importance == 0
        assert r.low_importance_candidates == []

    def test_config_has_llm_threshold(self):
        from memoria.core.memory.config import MemoryGovernanceConfig

        cfg = MemoryGovernanceConfig()
        assert cfg.reflection_llm_threshold == 0.5
        # Can be overridden
        cfg2 = MemoryGovernanceConfig(reflection_llm_threshold=0.7)
        assert cfg2.reflection_llm_threshold == 0.7
