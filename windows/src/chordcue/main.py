"""Desktop entry point; configure WebEngine before importing UI components."""

import argparse
import os
from pathlib import Path
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="ChordCue Windows 桌面版")
    parser.add_argument("project", nargs="?", type=Path)
    parser.add_argument("--smoke-test", type=Path, metavar="OUTPUT_DIRECTORY")
    parser.add_argument("--smoke-score", type=Path, metavar="SCORE_FILE")
    args = parser.parse_args()
    # Desktop audio must keep its lookahead timer alive when the chart tab is selected.
    flag = "--disable-background-timer-throttling"
    current = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    if flag not in current.split():
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = f"{current} {flag}".strip()

    from PySide6.QtCore import QStandardPaths, Qt
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication
    from chordcue import __version__
    from chordcue.paths import resource_path

    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QApplication(sys.argv[:1])
    app.setOrganizationName("ChordCue")
    app.setApplicationName("ChordCue")
    app.setApplicationVersion(__version__)
    if args.smoke_test is not None:
        QStandardPaths.setTestModeEnabled(True)
    app.setWindowIcon(QIcon(str(resource_path("ChordCue.svg"))))
    from chordcue.ui import MainWindow

    if args.smoke_test is not None:
        from chordcue.settings import Settings
        window = MainWindow(settings=Settings(args.smoke_test / "preferences.ini"))
    else:
        window = MainWindow()
    window.show()
    if args.project is not None:
        if args.project.suffix.lower() == ".json":
            window.open_project_path(args.project)
        else:
            window.import_score_path(args.project)
    if args.smoke_test is not None:
        from chordcue.diagnostics import run_smoke
        run_smoke(window, args.smoke_test, app, score_path=args.smoke_score)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
