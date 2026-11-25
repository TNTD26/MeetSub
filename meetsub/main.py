from __future__ import annotations

import hashlib
import logging
import os
import sys
import traceback

from PySide6.QtCore import QByteArray, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox

from . import __display_name__, __version__
from .app import MeetSubApp
from .logging_setup import setup_logging
from .paths import data_dir
from .ui.theme import app_icon, apply_theme

log = logging.getLogger(__name__)


def _set_windows_identity() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MeetSub.Caption.1")
    except Exception:
        pass


def _server_name() -> str:
    digest = hashlib.sha1(str(data_dir()).encode("utf-8")).hexdigest()[:10]
    return f"MeetSub.SingleInstance.{digest}"


def _notify_existing_instance(name: str) -> bool:
    socket = QLocalSocket()
    socket.connectToServer(name)
    if not socket.waitForConnected(500):
        return False
    socket.write(QByteArray(b"show\n"))
    socket.flush()
    socket.waitForBytesWritten(800)
    socket.disconnectFromServer()
    return True


def _install_excepthook() -> None:
    def hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        log.critical(
            "Unhandled exception:\n%s",
            "".join(traceback.format_exception(exc_type, exc_value, exc_tb)),
        )
        try:
            QMessageBox.critical(
                None,
                "会译字幕遇到错误",
                f"应用遇到未处理的错误：\n{exc_value}\n\n详细信息已写入本机日志。",
            )
        except Exception:
            pass

    sys.excepthook = hook


def main() -> int:
    setup_logging(console=not getattr(sys, "frozen", False))
    _set_windows_identity()
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("MeetSub")
    app.setApplicationDisplayName(__display_name__)
    app.setOrganizationName("MeetSub")
    app.setApplicationVersion(__version__)
    app.setWindowIcon(app_icon())
    app.setQuitOnLastWindowClosed(False)
    apply_theme(app)
    _install_excepthook()

    name = _server_name()
    if _notify_existing_instance(name):
        log.info("Another MeetSub instance is running; asked it to show.")
        return 0
    server = QLocalServer()
    QLocalServer.removeServer(name)
    if not server.listen(name):
        log.warning("Could not start single-instance server: %s", server.errorString())
    controller = MeetSubApp(app)

    def on_new_connection() -> None:
        while server.hasPendingConnections():
            connection = server.nextPendingConnection()
            if connection is None:
                continue
            connection.setParent(server)

            def handle(c=connection) -> None:
                c.readAll()
                controller.show_control()
                c.disconnectFromServer()

            connection.readyRead.connect(handle)
            QTimer.singleShot(150, lambda c=connection: handle(c))

    server.newConnection.connect(on_new_connection)
    # Keep the server and controller alive for the lifetime of the event loop.
    app._meetsub_server = server  # type: ignore[attr-defined]
    app._meetsub_controller = controller  # type: ignore[attr-defined]
    exit_code = app.exec()
    try:
        server.close()
    except Exception:
        pass
    return int(exit_code)


if __name__ == "__main__":
    sys.exit(main())
