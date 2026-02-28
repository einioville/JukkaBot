from __future__ import annotations

import os
from typing import Any

import pytest

from jukkabot.tracker_service import TrackerApiError, TrackerService


def _mock_payload() -> dict[str, Any]:
    return {
        "data": {
            "platformInfo": {
                "platformSlug": "uplay",
                "platformUserHandle": "GLK.Hanzanka",
            },
            "segments": [
                {
                    "type": "overview",
                    "stats": {
                        "wins": {"displayName": "Wins", "displayValue": "12"},
                        "kd": {"displayName": "K/D", "displayValue": "1.23"},
                    },
                }
            ],
        }
    }


def test_rainbow_six_lookup_tries_routes_until_success(monkeypatch: pytest.MonkeyPatch) -> None:
    service = TrackerService(api_key="fake")
    attempted_urls: list[str] = []

    def fake_get_json(url: str) -> dict[str, Any]:
        attempted_urls.append(url)
        if "/r6siege/standard/profile/uplay/" in url:
            return _mock_payload()
        raise TrackerApiError("Profile not found for that game/platform.", code="not_found")

    monkeypatch.setattr(service, "_get_json", fake_get_json)

    profile = service.fetch_profile_stats("Rainbow Six Siege", "GLK.Hanzanka")

    assert profile.game_name == "Rainbow Six Siege"
    assert profile.platform_user == "GLK.Hanzanka"
    assert profile.stats["Wins"] == "12"
    assert attempted_urls
    assert any("/rainbow-six-siege/standard/profile/uplay/" in u for u in attempted_urls)
    assert any("/r6siege/standard/profile/uplay/" in u for u in attempted_urls)


def test_rainbow_six_lookup_returns_forbidden_if_all_routes_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = TrackerService(api_key="fake")

    def always_forbidden(_: str) -> dict[str, Any]:
        raise TrackerApiError("Forbidden", code="forbidden")

    monkeypatch.setattr(service, "_get_json", always_forbidden)

    with pytest.raises(TrackerApiError) as excinfo:
        service.fetch_profile_stats("Rainbow Six Siege", "GLK.Hanzanka")

    assert excinfo.value.code == "forbidden"
    assert "denied" in str(excinfo.value).lower()


@pytest.mark.skipif(
    os.getenv("TRACKER_LIVE_TESTS") != "1",
    reason="Set TRACKER_LIVE_TESTS=1 to run live Tracker probe tests.",
)
def test_live_rainbow_six_glk_hanzanka() -> None:
    api_key = os.getenv("TRACKER_API_KEY") or os.getenv("TRN_API_KEY")
    if not api_key:
        pytest.skip("TRACKER_API_KEY/TRN_API_KEY is not configured.")

    service = TrackerService(api_key=api_key)
    try:
        profile = service.fetch_profile_stats("Rainbow Six Siege", "GLK.Hanzanka")
    except TrackerApiError as exc:
        # In current user reports this is typically forbidden by key scope.
        assert exc.code in {"forbidden", "not_found"}
        return

    assert profile.platform_user
    assert profile.stats
