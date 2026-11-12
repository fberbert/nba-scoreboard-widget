from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from nba_scoreboard_widget.espn import (
    LIVE_REFRESH_MS,
    MAX_WAIT_MS,
    MIN_WAIT_MS,
    next_refresh_ms,
    parse_scoreboard,
    status_text,
)

BRT = timezone(timedelta(hours=-3))
NOW = datetime(2026, 11, 12, 1, 0, tzinfo=timezone.utc)


def event(
    game_id: str,
    name: str,
    state: str,
    *,
    period: int = 0,
    clock: str = "0.0",
    date: str = "2026-11-12T00:00Z",
    away: tuple[str, str] = ("GS", "0"),
    home: tuple[str, str] = ("HOU", "0"),
) -> dict:
    def side(home_away: str, code: str, score: str) -> dict:
        return {
            "homeAway": home_away,
            "score": score,
            "team": {"abbreviation": code, "shortDisplayName": code.title()},
            "records": [{"summary": "8-3"}],
        }

    return {
        "id": game_id,
        "date": date,
        "status": {"period": period, "displayClock": clock, "type": {"name": name, "state": state}},
        "competitions": [{"competitors": [side("home", *home), side("away", *away)]}],
    }


def board(*events: dict) -> object:
    return parse_scoreboard({"day": {"date": "2026-11-11"}, "events": list(events)})


class ParseTests(unittest.TestCase):
    def test_maps_espn_codes_to_nba_tricodes_and_hides_pre_game_zero(self) -> None:
        game = board(event("1", "STATUS_SCHEDULED", "pre", away=("GS", "0"), home=("UTAH", "0"))).games[0]
        self.assertEqual((game.away.tricode, game.home.tricode), ("GSW", "UTA"))
        self.assertIsNone(game.away.score)
        self.assertEqual(game.home.record, "8-3")

    def test_orders_live_then_scheduled_then_final_by_tipoff(self) -> None:
        result = board(
            event("final", "STATUS_FINAL", "post", period=4),
            event("late", "STATUS_SCHEDULED", "pre", date="2026-11-12T03:00Z"),
            event("early", "STATUS_SCHEDULED", "pre", date="2026-11-12T02:30Z"),
            event("live", "STATUS_IN_PROGRESS", "in", period=2),
            event("ppd", "STATUS_POSTPONED", "pre"),
        )
        self.assertEqual([g.game_id for g in result.games], ["live", "early", "late", "final", "ppd"])
        self.assertEqual(result.games[-1].state, "postponed")

    def test_skips_events_without_both_sides(self) -> None:
        broken = event("1", "STATUS_SCHEDULED", "pre")
        broken["competitions"][0]["competitors"].pop()
        self.assertEqual(board(broken).games, ())

    def test_rejects_payload_without_events(self) -> None:
        with self.assertRaises(ValueError):
            parse_scoreboard({"day": {}})

    def test_leader_none_when_tied(self) -> None:
        game = board(event("1", "STATUS_IN_PROGRESS", "in", period=1, away=("GS", "5"), home=("HOU", "5"))).games[0]
        self.assertIsNone(game.leader())


class StatusTextTests(unittest.TestCase):
    def status(self, name: str, state: str, **kwargs: object) -> str:
        return status_text(board(event("1", name, state, **kwargs)).games[0], BRT)

    def test_scheduled_shows_local_tipoff(self) -> None:
        self.assertEqual(self.status("STATUS_SCHEDULED", "pre", date="2026-11-12T02:30Z"), "23:30")

    def test_live_quarter_and_clock(self) -> None:
        self.assertEqual(self.status("STATUS_IN_PROGRESS", "in", period=3, clock="7:14"), "3º · 7:14")

    def test_halftime_and_end_of_period(self) -> None:
        self.assertEqual(self.status("STATUS_HALFTIME", "in", period=2), "Intervalo")
        self.assertEqual(self.status("STATUS_END_PERIOD", "in", period=3), "Fim do 3º")
        self.assertEqual(self.status("STATUS_END_PERIOD", "in", period=5), "Fim da prorr.")

    def test_overtimes(self) -> None:
        self.assertEqual(self.status("STATUS_IN_PROGRESS", "in", period=5, clock="2:10"), "Prorr. · 2:10")
        self.assertEqual(self.status("STATUS_IN_PROGRESS", "in", period=6, clock="0:45"), "2ª prorr. · 0:45")

    def test_final_counts_overtimes(self) -> None:
        self.assertEqual(self.status("STATUS_FINAL", "post", period=4), "Encerrado")
        self.assertEqual(self.status("STATUS_FINAL", "post", period=5), "Encerrado · prorr.")
        self.assertEqual(self.status("STATUS_FINAL", "post", period=7), "Encerrado · 3 prorr.")

    def test_postponed(self) -> None:
        self.assertEqual(self.status("STATUS_POSTPONED", "pre"), "Adiado")


class RefreshTests(unittest.TestCase):
    def test_live_game_polls_fast(self) -> None:
        result = board(event("1", "STATUS_IN_PROGRESS", "in", period=1), event("2", "STATUS_SCHEDULED", "pre", date="2026-11-12T05:00Z"))
        self.assertEqual(next_refresh_ms(result, NOW), LIVE_REFRESH_MS)

    def test_waits_for_next_tipoff_within_bounds(self) -> None:
        soon = board(event("1", "STATUS_SCHEDULED", "pre", date="2026-11-12T01:05Z"))
        self.assertEqual(next_refresh_ms(soon, NOW), 5 * 60_000)
        late = board(event("1", "STATUS_SCHEDULED", "pre", date="2026-11-12T05:00Z"))
        self.assertEqual(next_refresh_ms(late, NOW), MAX_WAIT_MS)
        overdue = board(event("1", "STATUS_SCHEDULED", "pre", date="2026-11-12T00:55Z"))
        self.assertEqual(next_refresh_ms(overdue, NOW), MIN_WAIT_MS)

    def test_finished_or_empty_slate_polls_slowly(self) -> None:
        self.assertEqual(next_refresh_ms(board(event("1", "STATUS_FINAL", "post", period=4)), NOW), MAX_WAIT_MS)
        self.assertEqual(next_refresh_ms(board(), NOW), MAX_WAIT_MS)


if __name__ == "__main__":
    unittest.main()
