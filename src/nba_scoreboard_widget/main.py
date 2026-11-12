"""Application entry point."""

from __future__ import annotations

import sys

from PyQt6 import QtWidgets

from .config import load_config
from .widget import ScoreboardWidget


def main() -> None:
    config = load_config()
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("nba-scoreboard-widget")
    widget = ScoreboardWidget(config=config)
    widget.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
