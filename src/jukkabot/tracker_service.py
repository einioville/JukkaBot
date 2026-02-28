from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


@dataclass(frozen=True, slots=True)
class TrackerGame:
    name: str
    api_ids: tuple[str, ...]
    platforms: tuple[str, ...]
    coming_soon: bool = False


TRACKER_GAMES: tuple[TrackerGame, ...] = (
    TrackerGame(
        name="Valorant",
        api_ids=("valorant",),
        platforms=("riot",),
    ),
    TrackerGame(
        name="League of Legends",
        api_ids=("league-of-legends", "lol"),
        platforms=("riot",),
    ),
    TrackerGame(
        name="Fortnite",
        api_ids=("fortnite",),
        platforms=("epic", "xbl", "psn"),
    ),
    TrackerGame(
        name="Marvel Rivals",
        api_ids=("marvel-rivals", "marvelrivals"),
        platforms=("steam", "xbl", "psn"),
    ),
    TrackerGame(
        name="Rainbow Six Siege",
        api_ids=("rainbow-six-siege", "r6siege"),
        platforms=("uplay", "xbl", "psn"),
    ),
    TrackerGame(
        name="Roblox",
        api_ids=("roblox",),
        platforms=("roblox",),
    ),
    TrackerGame(
        name="Battlefield 6",
        api_ids=("battlefield-6", "battlefield6"),
        platforms=("origin", "xbl", "psn"),
    ),
    TrackerGame(
        name="Apex Legends",
        api_ids=("apex", "apex-legends"),
        platforms=("origin", "steam", "xbl", "psn"),
    ),
    TrackerGame(
        name="Rocket League",
        api_ids=("rocket-league", "rocketleague"),
        platforms=("epic", "steam", "xbl", "psn"),
    ),
    TrackerGame(
        name="Splitgate",
        api_ids=("splitgate",),
        platforms=("steam", "xbl", "psn"),
    ),
    TrackerGame(
        name="Counter-Strike 2",
        api_ids=("counter-strike-2", "cs2", "csgo"),
        platforms=("steam",),
    ),
    TrackerGame(
        name="Halo Infinite",
        api_ids=("halo-infinite", "halo"),
        platforms=("xbl", "steam"),
    ),
    TrackerGame(
        name="Off The Grid",
        api_ids=("off-the-grid", "offthegrid"),
        platforms=("epic", "xbl", "psn"),
    ),
    TrackerGame(
        name="SMITE 2",
        api_ids=("smite-2", "smite2"),
        platforms=("steam", "xbl", "psn"),
    ),
    TrackerGame(
        name="Destiny 2",
        api_ids=("destiny-2", "destiny2"),
        platforms=("steam", "xbl", "psn"),
    ),
    TrackerGame(
        name="Teamfight Tactics",
        api_ids=("teamfight-tactics", "tft"),
        platforms=("riot",),
    ),
    TrackerGame(
        name="Battlefield 1",
        api_ids=("battlefield-1", "bf1"),
        platforms=("origin", "xbl", "psn"),
    ),
    TrackerGame(
        name="Battlefield 2042",
        api_ids=("battlefield-2042", "bf2042"),
        platforms=("origin", "xbl", "psn"),
    ),
    TrackerGame(
        name="Battlefield V",
        api_ids=("battlefield-v", "battlefield-5", "bfv"),
        platforms=("origin", "xbl", "psn"),
    ),
    TrackerGame(
        name="Overwatch",
        api_ids=("overwatch", "overwatch2", "overwatch-2"),
        platforms=("battlenet", "xbl", "psn"),
    ),
    TrackerGame(
        name="PUBG: BATTLEGROUNDS",
        api_ids=("pubg", "pubg-battlegrounds"),
        platforms=("steam", "xbl", "psn"),
    ),
    TrackerGame(
        name="Bloodhunt",
        api_ids=("bloodhunt",),
        platforms=("steam", "psn"),
    ),
    TrackerGame(
        name="Brawlhalla",
        api_ids=("brawlhalla",),
        platforms=("steam", "xbl", "psn"),
    ),
    TrackerGame(
        name="Call of Duty: Warzone",
        api_ids=("warzone", "call-of-duty-warzone", "cod-warzone"),
        platforms=("atvi", "battle", "uno", "xbl", "psn"),
    ),
    TrackerGame(
        name="For Honor",
        api_ids=("for-honor", "forhonor"),
        platforms=("uplay", "xbl", "psn"),
    ),
    TrackerGame(
        name="Rainbow Six Mobile",
        api_ids=("rainbow-six-mobile", "r6mobile"),
        platforms=("uplay",),
    ),
    TrackerGame(
        name="The Division 2",
        api_ids=("division-2", "the-division-2"),
        platforms=("uplay", "xbl", "psn"),
    ),
    TrackerGame(
        name="2XKO",
        api_ids=("2xko",),
        platforms=("riot",),
        coming_soon=True,
    ),
    TrackerGame(
        name="Marathon",
        api_ids=("marathon",),
        platforms=("steam",),
        coming_soon=True,
    ),
)

TRACKER_GAME_NAMES: tuple[str, ...] = tuple(game.name for game in TRACKER_GAMES)
TRACKER_GAMES_BY_NAME: dict[str, TrackerGame] = {
    game.name.casefold(): game for game in TRACKER_GAMES
}


class TrackerApiError(RuntimeError):
    def __init__(self, message: str, code: str = "unknown") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class TrackerProfileStats:
    game: str
    game_name: str
    account_name: str
    platform: str
    platform_user: str
    stats: dict[str, str]


class TrackerService:
    def __init__(self, api_key: str, timeout_seconds: int = 10) -> None:
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.base_url = "https://public-api.tracker.gg/v2"

    def fetch_profile_stats(self, game_name: str, account_name: str) -> TrackerProfileStats:
        game_key = game_name.strip().casefold()
        game = TRACKER_GAMES_BY_NAME.get(game_key)
        if game is None:
            raise TrackerApiError("Unsupported game.")
        if game.coming_soon:
            raise TrackerApiError(
                f"{game.name} is listed as coming soon and player stats are not available yet."
            )

        query = account_name.strip()
        if not query:
            raise TrackerApiError("Account name is required.")

        encoded_name = quote(query, safe="")
        last_not_found: str | None = None
        saw_auth_error = False
        saw_bad_request = False
        for api_id in game.api_ids:
            for platform in game.platforms:
                url = (
                    f"{self.base_url}/{api_id}/standard/profile/{platform}/{encoded_name}"
                )
                try:
                    payload = self._get_json(url)
                except TrackerApiError as exc:
                    if exc.code == "not_found":
                        last_not_found = str(exc)
                        continue
                    if exc.code in {"unauthorized", "forbidden"}:
                        # Keep probing other aliases/platforms before failing.
                        saw_auth_error = True
                        continue
                    if exc.code == "bad_request":
                        # Can happen for an invalid slug/platform combination.
                        saw_bad_request = True
                        continue
                    raise

                data = payload.get("data", {})
                platform_info = data.get("platformInfo", {})
                stats = self._extract_stats(data.get("segments", []))
                if not stats:
                    raise TrackerApiError("No stats were returned for this profile.")

                return TrackerProfileStats(
                    game=api_id,
                    game_name=game.name,
                    account_name=query,
                    platform=platform_info.get("platformSlug") or platform,
                    platform_user=platform_info.get("platformUserHandle")
                    or platform_info.get("platformUserIdentifier")
                    or query,
                    stats=stats,
                )

        if last_not_found:
            raise TrackerApiError(last_not_found)
        if saw_auth_error:
            raise TrackerApiError(
                "Tracker API denied this game/profile lookup. The key is valid, but this "
                "title/route may not be enabled for it.",
                code="forbidden",
            )
        if saw_bad_request:
            raise TrackerApiError(
                "Tracker API rejected the lookup route for this title/profile.",
                code="bad_request",
            )
        raise TrackerApiError(
            "Profile was not found on supported platforms for this game."
        )

    def _get_json(self, url: str) -> dict[str, Any]:
        request = Request(
            url,
            headers={
                "TRN-Api-Key": self.api_key,
                "Accept": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            message = self._extract_error_message(exc)
            if exc.code == 404:
                raise TrackerApiError(
                    "Profile not found for that game/platform.",
                    code="not_found",
                ) from exc
            if exc.code == 401:
                raise TrackerApiError(
                    "Tracker API key is invalid.",
                    code="unauthorized",
                ) from exc
            if exc.code == 403:
                raise TrackerApiError(
                    "Tracker API request is not authorized for this title/route.",
                    code="forbidden",
                ) from exc
            if exc.code == 400:
                raise TrackerApiError(
                    f"Tracker API rejected the lookup: {message}",
                    code="bad_request",
                ) from exc
            if exc.code == 429:
                raise TrackerApiError(
                    "Tracker API rate limit reached. Try again in a moment.",
                    code="rate_limited",
                ) from exc
            raise TrackerApiError(
                f"Tracker API request failed ({exc.code}): {message}",
                code="http_error",
            ) from exc
        except URLError as exc:
            raise TrackerApiError("Could not reach Tracker API.", code="network") from exc

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise TrackerApiError("Tracker API returned invalid JSON.", code="json") from exc
        if not isinstance(data, dict):
            raise TrackerApiError("Unexpected Tracker API response shape.", code="shape")
        return data

    def _extract_error_message(self, exc: HTTPError) -> str:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
            if isinstance(payload, dict):
                msg = payload.get("message")
                if isinstance(msg, str) and msg.strip():
                    return msg.strip()
        except Exception:
            pass
        return exc.reason if isinstance(exc.reason, str) else "Unknown error"

    def _extract_stats(self, segments: Any) -> dict[str, str]:
        if not isinstance(segments, list):
            return {}

        target: dict[str, Any] | None = None
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            if segment.get("type") == "overview":
                target = segment
                break

        if target is None:
            for segment in segments:
                if isinstance(segment, dict) and isinstance(segment.get("stats"), dict):
                    target = segment
                    break

        if target is None:
            return {}

        raw_stats = target.get("stats")
        if not isinstance(raw_stats, dict):
            return {}

        output: dict[str, str] = {}
        for key, value in raw_stats.items():
            if not isinstance(value, dict):
                continue
            title = value.get("displayName") or key
            display_value = value.get("displayValue")
            if display_value is None:
                display_value = value.get("value")
            if display_value is None:
                continue
            output[str(title)] = str(display_value)
        return output
