#!/usr/bin/python
"""Minimal system-tray widget for the SoundPeats BLE daemon.

Shows battery and switches noise modes by talking to the same
tn.aziz.soundpeats.BLEService D-Bus service over dbus-send.
"""
import re
import subprocess

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QCursor, QIcon
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

DEST = "tn.aziz.soundpeats.BLEService"
PATH = "/tn/aziz/soundpeats/BLEService"


def call(method, *args):
    cmd = [
        "dbus-send", "--session", f"--dest={DEST}", "--print-reply",
        PATH, f"{DEST}.{method}", *args,
    ]
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return ""


def is_connected():
    return "boolean true" in call("IsConnected")


def battery():
    out = call("GetBatteryLevel")

    def field(name):
        m = re.search(rf'string "{name}"\s+variant\s+\S+\s+(\S+)', out)
        return m.group(1) if m else None

    return (
        field("left"),
        field("right"),
        field("charging_left") == "true",
        field("charging_right") == "true",
    )


class Tray(QSystemTrayIcon):
    def __init__(self):
        super().__init__(QIcon.fromTheme("audio-headphones"))
        menu = QMenu()
        self.status = menu.addAction("…")
        self.status.setEnabled(False)
        menu.addSeparator()
        menu.addAction("ANC", lambda: self.set_mode("ANC"))
        menu.addAction("Прозрачность", lambda: self.set_mode("PASSTHROUGH"))
        menu.addAction("Обычный", lambda: self.set_mode("NORMAL"))
        menu.addSeparator()
        menu.addAction("Обновить", self.refresh)
        menu.addAction("Выход", QApplication.instance().quit)
        self.setContextMenu(menu)
        self.activated.connect(self.on_activated)
        self.setToolTip("SoundPeats")
        self.setVisible(True)

        timer = QTimer(self)
        timer.timeout.connect(self.refresh)
        timer.start(60_000)
        self.refresh()

    def on_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.refresh()
            self.contextMenu().popup(QCursor.pos())

    def set_mode(self, mode):
        call("SetNoiseMode", f"string:{mode}")
        self.refresh()

    def refresh(self):
        if not is_connected():
            self.status.setText("Отключено")
            self.setToolTip("SoundPeats: отключено")
            return
        left, right, lc, rc = battery()
        text = f"L {left}%{'⚡' if lc else ''}   R {right}%{'⚡' if rc else ''}"
        self.status.setText(text)
        self.setToolTip(f"SoundPeats   {text}")


def main():
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    Tray()
    app.exec()


if __name__ == "__main__":
    main()
