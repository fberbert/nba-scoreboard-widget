"""ESPN NBA scoreboard: fetch URL, normalization and display rules.

The NBA CDN live scoreboard (used by nba-bet-agent) answers 403 to this machine
without NBA_PROXY; ESPN's public scoreboard is reached directly and already
carries the current slate, clock, records and scores. Pure functions only; the
widget does the HTTP request.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
REGULATION_PERIODS = 4

LIVE_REFRESH_MS = 15_000
MIN_WAIT_MS = 30_000
MAX_WAIT_MS = 15 * 60_000
ERROR_RETRY_MS = 60_000

# ESPN codes that differ from the NBA tricodes used by the logo files.
ESPN_TO_NBA = {
    "GS": "GSW",
    "NO": "NOP",
    "NY": "NYK",
    "SA": "SAS",
    "UTAH": "UTA",
    "WSH": "WAS",
}

# ESPN status names that override the pre/in/post state.
_SPECIAL_STATES = {
    "STATUS_POSTPONED": "postponed",
    "STATUS_CANCELED": "cancelled",
    "STATUS_SUSPENDED": "suspended",
}
_STATE_ORDER = {"live": 0, "scheduled": 1, "final": 2}

OTHER_STATE_TEXT = {
    "postponed": "Adiado",
    "cancelled": "Cancelado",
    "suspended": "Suspenso",
}


@dataclass(frozen=True)
class Team:
    tricode: str
    name: str
    score: int | None
    record: str | None


@dataclass(frozen=True)
class Game:
    game_id: str
    state: str  # scheduled, live, final, postponed, cancelled, suspended
    status_name: str
    tipoff_utc: datetime | None
    period: int
    clock: str
    away: Team
    home: Team

    @property
    def scored(self) -> bool:
        return self.state in ("live", "final", "suspended")

    def leader(self) -> str | None:
        """'away' or 'home' when scored and not tied."""
        if not self.scored or self.away.score is None or self.home.score is None:
            return None
        if self.away.score == self.home.score:
            return None
        return "home" if self.home.score > self.away.score else "away"


@dataclass(frozen=True)
class Scoreboard:
    day: str | None  # slate date, YYYY-MM-DD (US Eastern)
    games: tuple[Game, ...]


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _state(status_type: Mapping[str, Any]) -> str:
    special = _SPECIAL_STATES.get(str(status_type.get("name") or ""))
    if special:
        return special
    phase = status_type.get("state")
    if phase == "in":
        return "live"
    if phase == "post":
        return "final"
    return "scheduled"


def _team(raw: Mapping[str, Any], scored: bool) -> Team:
    team = raw.get("team") if isinstance(raw.get("team"), Mapping) else {}
    code = str(team.get("abbreviation") or "?").upper()
    records = raw.get("records")
    record = None
    if isinstance(records, list) and records and isinstance(records[0], Mapping):
        record = records[0].get("summary") or None
    return Team(
        tricode=ESPN_TO_NBA.get(code, code),
        name=str(team.get("shortDisplayName") or team.get("name") or code),
        # ESPN reports "0" for games that have not started.
        score=_int(raw.get("score")) if scored else None,
        record=record,
    )


def parse_game(event: Mapping[str, Any]) -> Game | None:
    competitions = event.get("competitions")
    if not isinstance(competitions, list) or not competitions or not isinstance(competitions[0], Mapping):
        return None
    competitors = competitions[0].get("competitors")
    if not isinstance(competitors, list):
        return None
    sides = {c.get("homeAway"): c for c in competitors if isinstance(c, Mapping)}
    if "home" not in sides or "away" not in sides:
        return None

    status = event.get("status") if isinstance(event.get("status"), Mapping) else {}
    status_type = status.get("type") if isinstance(status.get("type"), Mapping) else {}
    state = _state(status_type)
    scored = state in ("live", "final", "suspended")
    return Game(
        game_id=str(event.get("id") or ""),
        state=state,
        status_name=str(status_type.get("name") or ""),
        tipoff_utc=_parse_time(event.get("date")),
        period=_int(status.get("period")) or 0,
        clock=str(status.get("displayClock") or ""),
        away=_team(sides["away"], scored),
        home=_team(sides["home"], scored),
    )


def _sort_key(game: Game) -> tuple[int, float, str]:
    tipoff = game.tipoff_utc.timestamp() if game.tipoff_utc else float("inf")
    return (_STATE_ORDER.get(game.state, 3), tipoff, game.game_id)


def parse_scoreboard(payload: Any) -> Scoreboard:
    """Games of the payload, live first, then scheduled, finished and the rest; by tipoff."""
    if not isinstance(payload, Mapping) or not isinstance(payload.get("events"), list):
        raise ValueError("Placar da ESPN sem lista de jogos.")
    games = [parse_game(e) for e in payload["events"] if isinstance(e, Mapping)]
    day = payload.get("day") if isinstance(payload.get("day"), Mapping) else {}
    return Scoreboard(
        day=day.get("date") or None,
        games=tuple(sorted((g for g in games if g is not None), key=_sort_key)),
    )


def _overtime_label(ot: int) -> str:
    return "Prorr." if ot == 1 else f"{ot}ª prorr."


def status_text(game: Game, tz: Any = None) -> str:
    """Short pt-BR status line. ``tz`` is the display zone (default: system local)."""
    if game.state == "scheduled":
        if game.tipoff_utc is None:
            return "A definir"
        return game.tipoff_utc.astimezone(tz).strftime("%H:%M")
    if game.state == "final":
        ot = max(0, game.period - REGULATION_PERIODS)
        if ot == 0:
            return "Encerrado"
        return "Encerrado · prorr." if ot == 1 else f"Encerrado · {ot} prorr."
    if game.state != "live":
        return OTHER_STATE_TEXT.get(game.state, game.state)

    if game.status_name == "STATUS_HALFTIME":
        return "Intervalo"
    ot = game.period - REGULATION_PERIODS
    if game.status_name == "STATUS_END_PERIOD":
        return f"Fim da {_overtime_label(ot).lower()}" if ot > 0 else f"Fim do {game.period}º"
    if game.period <= 0:
        return "Em andamento"
    label = _overtime_label(ot) if ot > 0 else f"{game.period}º"
    return f"{label} · {game.clock}" if game.clock else label


def next_refresh_ms(board: Scoreboard, now: datetime) -> int:
    """15 s while a game is live; else wait for the next tipoff (30 s to 15 min)."""
    if any(game.state == "live" for game in board.games):
        return LIVE_REFRESH_MS
    wait = MAX_WAIT_MS
    for game in board.games:
        if game.state != "scheduled" or game.tipoff_utc is None:
            continue
        until = int((game.tipoff_utc - now).total_seconds() * 1000)
        wait = min(wait, max(until, MIN_WAIT_MS))
    return wait
