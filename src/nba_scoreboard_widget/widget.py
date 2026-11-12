"""Floating Qt widget with the live NBA scoreboard of the night."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Optional

from PyQt6 import QtCore, QtGui, QtNetwork, QtSvg, QtWidgets

from .config import AppConfig, clamp_columns, clamp_scale, save_config
from .espn import (
    ERROR_RETRY_MS,
    SCOREBOARD_URL,
    Game,
    Scoreboard,
    Team,
    next_refresh_ms,
    parse_scoreboard,
    status_text,
)

LOGO_DIR = Path(__file__).resolve().parent / "assets" / "logos"
REQUEST_TIMEOUT_MS = 15_000
SCORE_FLASH_MS = 4_000
USER_AGENT = b"Mozilla/5.0 (X11; Linux x86_64) nba-scoreboard-widget/0.1"

BRIGHT = "#f5f5f5"
SOFT = "#bfbfbf"
DIM = "#7a7a7a"
TITLE = "#d9d9d9"
GREEN = "#38b000"
RED = "#ff4d4f"
WARNING = "#ffb020"
CARD_STYLE = "background-color: rgba(15, 15, 15, 0.53); border: none;"


@lru_cache(maxsize=128)
def team_logo(tricode: str, size: int, dpr: float) -> QtGui.QPixmap | None:
    """Team SVG rendered to a square pixmap; None for an unknown team."""
    path = LOGO_DIR / f"{tricode}.svg"
    if not path.exists():
        return None
    renderer = QtSvg.QSvgRenderer(str(path))
    if not renderer.isValid():
        return None
    renderer.setAspectRatioMode(QtCore.Qt.AspectRatioMode.KeepAspectRatio)
    pixels = max(1, round(size * dpr))
    pixmap = QtGui.QPixmap(pixels, pixels)
    pixmap.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    renderer.render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(dpr)
    return pixmap


def _set_font(label: QtWidgets.QLabel, points: float, bold: bool) -> None:
    font = label.font()
    font.setPointSizeF(points)
    font.setBold(bold)
    label.setFont(font)


def _set_color(label: QtWidgets.QLabel, color: str) -> None:
    label.setStyleSheet(f"color: {color}; background: transparent;")


class TeamRow(QtWidgets.QWidget):
    """Logo, tricode, record and score of one team; a score that goes up flashes green."""

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self._logo = QtWidgets.QLabel()
        self._code = QtWidgets.QLabel()
        self._record = QtWidgets.QLabel()
        self._score = QtWidgets.QLabel()
        self._score.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        _set_color(self._record, DIM)
        self._tricode: str | None = None
        self._last_score: int | None = None
        self._score_color = BRIGHT
        self._logo_size = 22
        self._flash_timer = QtCore.QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.setInterval(SCORE_FLASH_MS)
        self._flash_timer.timeout.connect(lambda: _set_color(self._score, self._score_color))

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._logo)
        layout.addWidget(self._code)
        layout.addWidget(self._record)
        layout.addStretch(1)
        layout.addWidget(self._score)

    def apply_scale(self, scale: float) -> None:
        _set_font(self._code, 12 * scale, True)
        _set_font(self._record, 8.5 * scale, False)
        _set_font(self._score, 15 * scale, True)
        self._logo_size = round(22 * scale)
        self._logo.setFixedSize(self._logo_size, self._logo_size)
        self._score.setMinimumWidth(round(38 * scale))
        layout = self.layout()
        if layout is not None:
            layout.setSpacing(round(6 * scale))
        self._render_logo()

    def set_team(self, team: Team, color: str) -> None:
        if team.tricode != self._tricode:
            self._tricode = team.tricode
            self._last_score = None
            self._render_logo()
        self._code.setText(team.tricode)
        self._code.setToolTip(team.name)
        _set_color(self._code, color)
        self._record.setText(team.record or "")
        self._score.setText("" if team.score is None else str(team.score))
        self._score_color = color
        scored_up = self._last_score is not None and team.score is not None and team.score > self._last_score
        self._last_score = team.score
        if scored_up:
            _set_color(self._score, GREEN)
            self._flash_timer.start()
        elif not self._flash_timer.isActive():
            _set_color(self._score, color)

    def _render_logo(self) -> None:
        pixmap = team_logo(self._tricode, self._logo_size, self.devicePixelRatioF()) if self._tricode else None
        if pixmap is None:
            self._logo.clear()
        else:
            self._logo.setPixmap(pixmap)


class GameCard(QtWidgets.QFrame):
    """Status line plus away and home rows of one game."""

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("gameCard")
        self.setStyleSheet(f"QFrame#gameCard {{ {CARD_STYLE} }}")
        self._dot = QtWidgets.QLabel("●")
        _set_color(self._dot, RED)
        self._status = QtWidgets.QLabel()
        self._away = TeamRow(self)
        self._home = TeamRow(self)

        status_row = QtWidgets.QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(4)
        status_row.addWidget(self._dot)
        status_row.addWidget(self._status)
        status_row.addStretch(1)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(status_row)
        layout.addWidget(self._away)
        layout.addWidget(self._home)

    def apply_scale(self, scale: float) -> None:
        _set_font(self._dot, 8 * scale, False)
        _set_font(self._status, 9 * scale, True)
        self._away.apply_scale(scale)
        self._home.apply_scale(scale)
        layout = self.layout()
        if layout is not None:
            h, v = round(10 * scale), round(8 * scale)
            layout.setContentsMargins(h, v, h, v)
            layout.setSpacing(round(4 * scale))
        self.setMinimumWidth(round(170 * scale))

    def set_game(self, game: Game) -> None:
        live = game.state == "live"
        self._dot.setVisible(live)
        self._status.setText(status_text(game))
        _set_color(self._status, RED if live else (WARNING if game.state not in ("scheduled", "final") else TITLE))
        leader = game.leader()
        for side, row in (("away", self._away), ("home", self._home)):
            row.set_team(getattr(game, side), self._team_color(game, side, leader))

    @staticmethod
    def _team_color(game: Game, side: str, leader: str | None) -> str:
        if leader is None or leader == side:
            return BRIGHT
        return DIM if game.state == "final" else SOFT


class ScoreboardWidget(QtWidgets.QWidget):
    """A frameless, draggable widget that stays on top of the desktop."""

    def __init__(self, config: AppConfig, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self._config = config
        self._drag_offset: Optional[QtCore.QPoint] = None
        self._cards: dict[str, GameCard] = {}
        self._order: list[str] = []
        self._reply: QtNetwork.QNetworkReply | None = None

        self._title = QtWidgets.QLabel("NBA")
        self._meta = QtWidgets.QLabel("Carregando…")
        self._meta.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        _set_color(self._title, BRIGHT)
        _set_color(self._meta, TITLE)
        header = QtWidgets.QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(self._title)
        header.addStretch(1)
        header.addWidget(self._meta)

        self._empty = QtWidgets.QLabel("")
        self._empty.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        _set_color(self._empty, TITLE)
        self._empty.hide()

        self._grid = QtWidgets.QGridLayout()
        self._grid.setContentsMargins(0, 0, 0, 0)

        self._root = QtWidgets.QVBoxLayout(self)
        self._root.setSizeConstraint(QtWidgets.QLayout.SizeConstraint.SetFixedSize)
        self._root.addLayout(header)
        self._root.addWidget(self._empty)
        self._root.addLayout(self._grid)

        self.setWindowTitle("NBA Scoreboard")
        self.setWindowFlags(
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.Window
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self._shortcuts = [
            self._make_shortcut(key, handler)
            for key, handler in (
                ("Q", self._quit),
                ("R", self._refresh_now),
                ("+", lambda: self._change_scale(0.1)),
                ("=", lambda: self._change_scale(0.1)),
                ("-", lambda: self._change_scale(-0.1)),
                ("_", lambda: self._change_scale(-0.1)),
                ("]", lambda: self._change_columns(1)),
                ("[", lambda: self._change_columns(-1)),
            )
        ]

        self._network = QtNetwork.QNetworkAccessManager(self)
        self._refresh_timer = QtCore.QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self._fetch)

        self._apply_scale()
        QtCore.QTimer.singleShot(0, self._place_initially)
        QtCore.QTimer.singleShot(0, self._fetch)

    # Mouse: drag to move, position saved on release.
    def mousePressEvent(self, a0: QtGui.QMouseEvent | None) -> None:  # noqa: N802
        if a0 and a0.button() == QtCore.Qt.MouseButton.LeftButton:
            self._drag_offset = a0.globalPosition().toPoint() - self.frameGeometry().topLeft()
            a0.accept()
        super().mousePressEvent(a0)

    def mouseMoveEvent(self, a0: QtGui.QMouseEvent | None) -> None:  # noqa: N802
        if a0 and a0.buttons() & QtCore.Qt.MouseButton.LeftButton and self._drag_offset is not None:
            self.move(a0.globalPosition().toPoint() - self._drag_offset)
            a0.accept()
        super().mouseMoveEvent(a0)

    def mouseReleaseEvent(self, a0: QtGui.QMouseEvent | None) -> None:  # noqa: N802
        if a0 and a0.button() == QtCore.Qt.MouseButton.LeftButton and self._drag_offset is not None:
            self._drag_offset = None
            pos = self.pos()
            self._update_config(position=(pos.x(), pos.y()))
        super().mouseReleaseEvent(a0)

    def closeEvent(self, a0: QtGui.QCloseEvent | None) -> None:  # noqa: N802
        self._refresh_timer.stop()
        if self._reply is not None:
            self._reply.abort()
        super().closeEvent(a0)

    # Data
    def _refresh_now(self) -> None:
        self._refresh_timer.stop()
        self._fetch()

    def _fetch(self) -> None:
        if self._reply is not None:
            return
        request = QtNetwork.QNetworkRequest(QtCore.QUrl(SCOREBOARD_URL))
        request.setRawHeader(b"User-Agent", USER_AGENT)
        request.setTransferTimeout(REQUEST_TIMEOUT_MS)
        self._reply = self._network.get(request)
        self._reply.finished.connect(self._handle_reply)

    def _handle_reply(self) -> None:
        reply, self._reply = self._reply, None
        if reply is None:
            return
        try:
            if reply.error() != QtNetwork.QNetworkReply.NetworkError.NoError:
                raise ValueError(reply.errorString())
            board = parse_scoreboard(json.loads(bytes(reply.readAll().data())))
        except ValueError as exc:
            self._show_error(str(exc))
            self._refresh_timer.start(ERROR_RETRY_MS)
            return
        finally:
            reply.deleteLater()
        self._show_board(board)
        self._refresh_timer.start(next_refresh_ms(board, datetime.now(timezone.utc)))

    def _show_board(self, board: Scoreboard) -> None:
        self._title.setText(f"NBA · {self._format_day(board.day)}" if board.day else "NBA")
        self._meta.setText(datetime.now().strftime("%H:%M"))
        self._meta.setToolTip("Última atualização (ESPN)")
        _set_color(self._meta, TITLE)

        seen = set()
        for game in board.games:
            seen.add(game.game_id)
            card = self._cards.get(game.game_id)
            if card is None:
                card = GameCard(self)
                card.apply_scale(self._config.scale)
                self._cards[game.game_id] = card
            card.set_game(game)
        for game_id in [g for g in self._cards if g not in seen]:
            self._cards.pop(game_id).deleteLater()

        order = [game.game_id for game in board.games]
        if order != self._order:
            self._order = order
            self._layout_cards()
        self._empty.setText("Nenhum jogo hoje")
        self._empty.setVisible(not order)

    def _show_error(self, message: str) -> None:
        self._meta.setText("sem conexão")
        self._meta.setToolTip(message)
        _set_color(self._meta, WARNING)
        if not self._order:
            self._empty.setText("Placar indisponível")
            self._empty.show()

    # Layout
    def _layout_cards(self) -> None:
        while self._grid.count():
            self._grid.takeAt(0)
        columns = self._config.columns
        for index, game_id in enumerate(self._order):
            self._grid.addWidget(self._cards[game_id], index // columns, index % columns)

    def _apply_scale(self) -> None:
        scale = self._config.scale
        margin, spacing = round(12 * scale), round(8 * scale)
        self._root.setContentsMargins(margin, margin, margin, margin)
        self._root.setSpacing(spacing)
        self._grid.setSpacing(spacing)
        self.setStyleSheet(f"ScoreboardWidget {{ background-color: rgb(60, 60, 60); border-radius: {round(12 * scale)}px; }}")
        _set_font(self._title, 11 * scale, True)
        _set_font(self._meta, 9 * scale, False)
        _set_font(self._empty, 11 * scale, False)
        for card in self._cards.values():
            card.apply_scale(scale)

    def paintEvent(self, a0: QtGui.QPaintEvent | None) -> None:  # noqa: N802
        # Lets the stylesheet paint the rounded background of a plain QWidget subclass.
        option = QtWidgets.QStyleOption()
        option.initFrom(self)
        painter = QtGui.QPainter(self)
        self.style().drawPrimitive(QtWidgets.QStyle.PrimitiveElement.PE_Widget, option, painter, self)
        painter.end()

    def _change_scale(self, delta: float) -> None:
        scale = clamp_scale(self._config.scale + delta)
        if scale != self._config.scale:
            self._update_config(scale=scale)
            self._apply_scale()

    def _change_columns(self, delta: int) -> None:
        columns = clamp_columns(self._config.columns + delta)
        if columns != self._config.columns:
            self._update_config(columns=columns)
            self._layout_cards()

    def _update_config(self, **changes: object) -> None:
        self._config = replace(self._config, **changes)
        save_config(self._config)

    def _place_initially(self) -> None:
        saved = self._config.position
        if saved is not None:
            point = QtCore.QPoint(*saved)
            if QtGui.QGuiApplication.screenAt(point) is not None:
                self.move(point)
                return
        screen = QtGui.QGuiApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            self.move(available.left() + 20, available.top() + 20)

    def _make_shortcut(self, key: str, handler: object) -> QtGui.QShortcut:
        shortcut = QtGui.QShortcut(QtGui.QKeySequence(key), self)
        shortcut.setContext(QtCore.Qt.ShortcutContext.ApplicationShortcut)
        shortcut.activated.connect(handler)  # type: ignore[arg-type]
        return shortcut

    @staticmethod
    def _quit() -> None:
        QtCore.QCoreApplication.quit()

    @staticmethod
    def _format_day(day: str) -> str:
        try:
            return datetime.strptime(day, "%Y-%m-%d").strftime("%d/%m")
        except ValueError:
            return day
