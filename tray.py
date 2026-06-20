#!/usr/bin/python
"""Minimal system-tray widget for the SoundPeats BLE daemon.

Shows battery and switches noise modes by talking to the same
tn.aziz.soundpeats.BLEService D-Bus service over dbus-send.
"""
import re
import subprocess

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

DEST = "tn.aziz.soundpeats.BLEService"
PATH = "/tn/aziz/soundpeats/BLEService"

MODES = ("ANC", "PASSTHROUGH", "NORMAL")
MODE_LABELS = {"ANC": "ANC", "PASSTHROUGH": "Прозрачность", "NORMAL": "Обычный"}


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
        for mode in MODES:
            menu.addAction(MODE_LABELS[mode], lambda _=False, m=mode: self.set_mode(m))
        menu.addSeparator()
        menu.addAction("Обновить", self.refresh)
        menu.addAction("Выход", QApplication.instance().quit)
        # Refresh battery whenever the menu opens; Plasma shows the menu itself
        # (manually popping it up fails on Wayland).
        menu.aboutToShow.connect(self.refresh)
        self.setContextMenu(menu)
        # Left-click (Activate) cycles the noise mode; right-click is the menu.
        self.mode_index = 0
        self.activated.connect(self.on_activated)
        self.setToolTip("SoundPeats")
        self.setVisible(True)

        timer = QTimer(self)
        timer.timeout.connect(self.refresh)
        timer.start(60_000)
        self.refresh()

    def on_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.mode_index = (self.mode_index + 1) % len(MODES)
            self.set_mode(MODES[self.mode_index])

    def set_mode(self, mode):
        if mode in MODES:
            self.mode_index = MODES.index(mode)
        call("SetNoiseMode", f"string:{mode}")
        self.showMessage("SoundPeats", MODE_LABELS.get(mode, mode),
                         QSystemTrayIcon.MessageIcon.Information, 2000)
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
