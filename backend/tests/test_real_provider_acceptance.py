"""Opt-in real-provider acceptance; skipped unless explicitly enabled."""

from __future__ import annotations

import os

import pytest

from scripts.run_real_provider_acceptance import run_acceptance


pytestmark = pytest.mark.real_provider


@pytest.mark.skipif(
    os.getenv("REAL_PROVIDER_ACCEPTANCE", "").strip().lower()
    not in {"1", "true", "yes", "on"},
    reason="set REAL_PROVIDER_ACCEPTANCE=1 and MODEL_* credentials to enable",
)
def test_real_openai_compatible_provider_acceptance(tmp_path) -> None:
    summary = run_acceptance(tmp_path, timeout_seconds=30.0)
    assert summary["fallback_verified"] is True
    assert summary["normalized_error_verified"] is True
    assert summary["usage"]["logical_calls"] == 3
    assert summary["usage"]["provider_attempts"] == 6
    assert summary["stream_semantics"] == "canonical_final_response"
    assert summary["marker_verified"] is True
    assert summary["provider_specific_fields_status"] in {
        "preserved",
        "not_returned_by_provider",
        "unsupported",
    }
    assert all(
        int(item["token_usage"]["total_tokens"] or 0) > 0
        for item in summary["responses"]
    )
    assert summary["callback_counts"]["starts"] == 3
    assert summary["callback_counts"]["ends"] == 3
    assert summary["callback_counts"]["errors"] == 0
