"""
Aim Lock — PyQt5 multi-tab frameless app.

Tabs:
  1. AIM LOCK  — pixel-based aim assist with full axis control
  2. NO RECOIL — recoil compensation + autofire macro

Install:
    pip install PyQt5 mss numpy pynput

Run:
    python aim_lock.py
"""

import sys
import ctypes
import math
import json
import os
import subprocess
import threading
import time

try:
    from PyQt5.QtWidgets import (
        QApplication, QWidget, QLabel, QPushButton, QSlider,
        QHBoxLayout, QVBoxLayout, QFrame, QGraphicsDropShadowEffect,
        QColorDialog, QSizePolicy, QLineEdit, QCheckBox, QComboBox,
        QTabWidget, QScrollArea, QInputDialog,
    )
    from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QObject, QPoint, QThread
    from PyQt5.QtGui import (
        QPainter, QColor, QPen, QBrush, QFont,
        QLinearGradient, QPalette,
    )
except ImportError:
    print("Run: pip install PyQt5"); input(); sys.exit(1)

try:
    import mss
    import numpy as np
    from pynput import mouse as pynput_mouse
    from pynput import keyboard as pynput_keyboard
except ImportError:
    print("Run: pip install mss numpy pynput"); input(); sys.exit(1)

# ─── Win32 constants ─────────────────────────────────────────────────────────
MOUSEEVENTF_MOVE      = 0x0001
MOUSEEVENTF_LEFTDOWN  = 0x0002
MOUSEEVENTF_LEFTUP    = 0x0004
VK_LBUTTON            = 0x01

# ─── Config files ─────────────────────────────────────────────────────────────
BASE_DIR        = os.path.dirname(os.path.abspath(__file__))
AIM_CONFIG      = os.path.join(BASE_DIR, "aim_config.json")
RECOIL_DIR      = os.path.join(BASE_DIR, "recoil_configs")
os.makedirs(RECOIL_DIR, exist_ok=True)

AIM_DEFAULTS = {
    "em_color":      "#970000",
    "col_vn":        61,
    "fov_x":         64,
    "fov_y":         7,
    "anti_shake_x":  8,
    "anti_shake_y":  8,
    "mouse_mult_x":  2.2,
    "mouse_mult_y":  1.0,
    "snap_power_x":  0.50,
    "snap_power_y":  0.50,
    "max_step_x":    40,
    "max_step_y":    20,
    "loop_count":    10,
}

RECOIL_DEFAULTS = {
    "recoil_y_pos":       10,
    "recoil_y_neg":       10,
    "lock_sliders":       False,
    "delay":              5,
    "no_recoil_enabled":  False,
    "click_interval":     200,
    "autofire_enabled":   False,
    "selected_process":   "",
}

# ─── Palette ──────────────────────────────────────────────────────────────────
C_BG     = "#0d0d12"
C_PANEL  = "#13131b"
C_CARD   = "#1a1a26"
C_BORDER = "#252535"
C_ACCENT = "#e8192c"
C_ACCENT2= "#ff4d5e"
C_TEXT   = "#e2e2f0"
C_MUTED  = "#5a5a7a"
C_GREEN  = "#22c55e"
C_YELLOW = "#facc15"
C_BLUE   = "#3b82f6"
C_ORANGE = "#f97316"


# ─── Helpers ──────────────────────────────────────────────────────────────────
def hex_to_rgb(h: str):
    h = h.lstrip("#")
    if len(h) != 6:
        return (0x97, 0x00, 0x00)
    return int(h[:2], 16), int(h[2:4], 16), int(h[4:], 16)


def lbutton_down() -> bool:
    return bool(ctypes.windll.user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000)


def get_process_list():
    try:
        r = subprocess.run(
            ["tasklist", "/fo", "csv", "/nh"],
            capture_output=True, text=True, timeout=5,
        )
        procs = set()
        for line in r.stdout.strip().splitlines():
            if line:
                name = line.split(",")[0].strip('"')
                if name:
                    procs.add(name)
        return sorted(procs)
    except Exception:
        return []


def get_foreground_process() -> str:
    try:
        user32  = ctypes.windll.user32
        psapi   = ctypes.windll.psapi
        kernel32 = ctypes.windll.kernel32
        hwnd = user32.GetForegroundWindow()
        pid  = ctypes.c_ulong(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = kernel32.OpenProcess(0x0410, False, pid)
        if not handle:
            return ""
        buf = ctypes.create_unicode_buffer(512)
        psapi.GetModuleFileNameExW(handle, None, buf, 512)
        kernel32.CloseHandle(handle)
        return os.path.basename(buf.value)
    except Exception:
        return ""


def make_shadow(radius=20, color="#000000", opacity=190):
    fx = QGraphicsDropShadowEffect()
    fx.setBlurRadius(radius)
    c = QColor(color)
    c.setAlpha(opacity)
    fx.setColor(c)
    fx.setOffset(0, 4)
    return fx


# ─── Aim Worker ───────────────────────────────────────────────────────────────
class AimWorker(QThread):
    fps_signal = pyqtSignal(int)

    def __init__(self, cfg_fn):
        super().__init__()
        self.cfg_fn   = cfg_fn
        self._running = False

    def start_aim(self):
        self._running = True
        self.start()

    def stop_aim(self):
        self._running = False

    def run(self):
        user32 = ctypes.windll.user32
        times  = []

        with mss.mss() as sct:
            while self._running:
                t0 = time.perf_counter()

                if not lbutton_down():
                    time.sleep(0.001)
                    continue

                cfg = self.cfg_fn()
                sw = user32.GetSystemMetrics(0)
                sh = user32.GetSystemMetrics(1)
                cx, cy = sw // 2, sh // 2

                fov_x    = cfg["fov_x"]
                fov_y    = cfg["fov_y"]
                ax       = cfg["anti_shake_x"]
                ay       = cfg["anti_shake_y"]
                mult_x   = cfg["mouse_mult_x"]
                mult_y   = cfg["mouse_mult_y"]
                pow_x    = cfg["snap_power_x"]
                pow_y    = cfg["snap_power_y"]
                max_sx   = cfg["max_step_x"]
                max_sy   = cfg["max_step_y"]
                loops    = cfg["loop_count"]
                col_vn   = cfg["col_vn"]
                tr, tg, tb = hex_to_rgb(cfg["em_color"])

                near = {"left": cx-ax, "top": cy-ay, "width": ax*2, "height": ay*2}
                scan = {"left": cx-fov_x, "top": cy, "width": fov_x*2, "height": max(fov_y, 1)}

                try:
                    ns = np.array(sct.grab(near))
                    near_hit = (
                        (np.abs(ns[:,:,2].astype(np.int16) - tr) <= col_vn) &
                        (np.abs(ns[:,:,1].astype(np.int16) - tg) <= col_vn) &
                        (np.abs(ns[:,:,0].astype(np.int16) - tb) <= col_vn)
                    ).any()

                    if not near_hit:
                        for _ in range(loops):
                            if not lbutton_down() or not self._running:
                                break
                            s = np.array(sct.grab(scan))
                            mask = (
                                (np.abs(s[:,:,2].astype(np.int16) - tr) <= col_vn) &
                                (np.abs(s[:,:,1].astype(np.int16) - tg) <= col_vn) &
                                (np.abs(s[:,:,0].astype(np.int16) - tb) <= col_vn)
                            )
                            if mask.any():
                                ys, xs = np.where(mask)
                                px = int(xs[0]) + (cx - fov_x)
                                py = int(ys[0]) + cy
                                dx, dy = px - cx, py - cy
                                sx = 1 if dx >= 0 else -1
                                sy = 1 if dy >= 0 else -1
                                mx = min(int(math.floor(abs(dx) ** pow_x) * sx * mult_x), max_sx * sx)
                                my = min(int(math.floor(abs(dy) ** pow_y) * sy * mult_y), max_sy * sy)
                                user32.mouse_event(
                                    MOUSEEVENTF_MOVE,
                                    ctypes.c_int(mx),
                                    ctypes.c_int(my),
                                    0, 0,
                                )
                except Exception:
                    time.sleep(0.001)
                    continue

                elapsed = time.perf_counter() - t0
                times.append(elapsed)
                if len(times) > 30:
                    times.pop(0)
                avg = sum(times) / len(times)
                self.fps_signal.emit(int(1.0 / avg) if avg > 0 else 0)


# ─── No Recoil Worker ─────────────────────────────────────────────────────────
class NoRecoilWorker(QThread):
    def __init__(self, cfg_fn):
        super().__init__()
        self.cfg_fn   = cfg_fn
        self._running = False

    def start_work(self):
        self._running = True
        self.start()

    def stop_work(self):
        self._running = False

    def run(self):
        user32   = ctypes.windll.user32
        _held    = False
        _af_tick = 0.0

        while self._running:
            is_down = lbutton_down()
            cfg     = self.cfg_fn()
            now     = time.perf_counter()

            if is_down:
                # Autofire
                if cfg["autofire_enabled"]:
                    if now - _af_tick >= max(cfg["click_interval"], 1) / 1000.0:
                        user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
                        time.sleep(0.025)
                        user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
                        _af_tick = time.perf_counter()

                # No Recoil
                if cfg["no_recoil_enabled"]:
                    sel = cfg["selected_process"]
                    if sel:
                        active = get_foreground_process()
                        if active.lower() != sel.lower():
                            time.sleep(0.001)
                            continue

                    rpos = cfg["recoil_y_pos"]
                    rneg = -cfg["recoil_y_neg"]
                    dly  = max(cfg["delay"], 1) / 1000.0

                    user32.mouse_event(MOUSEEVENTF_MOVE, 0, ctypes.c_int(rpos), 0, 0)
                    time.sleep(dly)
                    user32.mouse_event(MOUSEEVENTF_MOVE, 0, ctypes.c_int(rneg), 0, 0)
                    time.sleep(dly)
                else:
                    time.sleep(0.001)
            else:
                _af_tick = 0.0
                time.sleep(0.001)


# ─── FOV Canvas ───────────────────────────────────────────────────────────────
class FovCanvas(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedSize(210, 160)
        self.fov_x = 64; self.fov_y = 7
        self.ax = 8;     self.ay = 8
        self.color = QColor("#970000")

    def update_values(self, fov_x, fov_y, ax, ay, color_hex):
        self.fov_x, self.fov_y = fov_x, fov_y
        self.ax,    self.ay    = ax, ay
        try:
            self.color = QColor(color_hex)
        except Exception:
            pass
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        W, H = self.width(), self.height()
        cx, cy = W // 2, H // 2
        p.fillRect(0, 0, W, H, QColor("#0a0a12"))
        pen = QPen(QColor(C_BORDER)); pen.setWidth(1)
        p.setPen(pen)
        for x in range(0, W, 20): p.drawLine(x, 0, x, H)
        for y in range(0, H, 20): p.drawLine(0, y, W, y)

        scale = min((W * 0.44) / max(self.fov_x, 1), (H * 0.44) / max(self.fov_y, 1), 5.0)
        rx = int(self.fov_x * scale); ry = int(self.fov_y * scale)
        axs = int(self.ax * scale);   ays = int(self.ay * scale)

        pen = QPen(QColor(C_ACCENT)); pen.setWidth(1); pen.setStyle(Qt.DashLine); p.setPen(pen)
        p.drawRect(cx - rx, cy, rx * 2, ry)
        pen = QPen(QColor(C_YELLOW)); pen.setWidth(1); pen.setStyle(Qt.DotLine);  p.setPen(pen)
        p.drawRect(cx - axs, cy - ays, axs * 2, ays * 2)
        pen = QPen(QColor(C_TEXT));  pen.setWidth(1); pen.setStyle(Qt.SolidLine); p.setPen(pen)
        p.drawLine(cx - 9, cy, cx + 9, cy)
        p.drawLine(cx, cy - 9, cx, cy + 9)
        p.setPen(Qt.NoPen); p.setBrush(QBrush(self.color))
        p.drawEllipse(cx - 4, cy - 4, 8, 8)
        p.setFont(QFont("Consolas", 7))
        p.setPen(QColor(C_ACCENT));  p.drawText(4, H - 12, "▪ FOV")
        p.setPen(QColor(C_YELLOW));  p.drawText(38, H - 12, "▪ Anti-Shake")
        p.end()


# ─── Shared UI widgets ────────────────────────────────────────────────────────
def styled_slider():
    s = QSlider(Qt.Horizontal)
    s.setStyleSheet(f"""
        QSlider::groove:horizontal {{ height:3px; background:{C_BORDER}; border-radius:2px; }}
        QSlider::handle:horizontal  {{ width:14px; height:14px; margin:-6px 0;
            border-radius:7px; background:{C_ACCENT}; border:2px solid {C_ACCENT2}; }}
        QSlider::handle:horizontal:hover {{ background:{C_ACCENT2}; }}
        QSlider::sub-page:horizontal {{ background:{C_ACCENT}; border-radius:2px; }}
    """)
    return s


class Card(QFrame):
    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setStyleSheet(f"QFrame#card{{background:{C_CARD};border:1px solid {C_BORDER};border-radius:10px;}}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 12)
        lay.setSpacing(8)
        lbl = QLabel(title)
        lbl.setFont(QFont("Consolas", 8, QFont.Bold))
        lbl.setStyleSheet(f"color:{C_ACCENT};letter-spacing:2px;background:transparent;border:none;")
        lay.addWidget(lbl)
        sep = QFrame(); sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"background:{C_BORDER};border:none;max-height:1px;")
        lay.addWidget(sep)
        self.body = QVBoxLayout(); self.body.setSpacing(8)
        lay.addLayout(self.body)


class SliderRow(QWidget):
    value_changed = pyqtSignal(object)

    def __init__(self, label, lo, hi, default, dec=0, color=C_ACCENT, parent=None):
        super().__init__(parent)
        self.dec = dec
        self.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(self); lay.setContentsMargins(0,0,0,0); lay.setSpacing(3)
        top = QHBoxLayout()
        lbl = QLabel(label); lbl.setFont(QFont("Consolas", 8))
        lbl.setStyleSheet(f"color:{C_MUTED};")
        top.addWidget(lbl); top.addStretch()
        self.val = QLabel(self._fmt(default))
        self.val.setFont(QFont("Consolas", 9, QFont.Bold))
        self.val.setStyleSheet(f"color:{color};"); self.val.setMinimumWidth(44)
        self.val.setAlignment(Qt.AlignRight); top.addWidget(self.val)
        lay.addLayout(top)
        self.slider = styled_slider()
        mult = 100 if dec > 0 else 1
        self.slider.setRange(int(lo*mult), int(hi*mult))
        self.slider.setValue(int(default*mult))
        self.slider.valueChanged.connect(self._changed)
        lay.addWidget(self.slider)

    def _fmt(self, v):
        return f"{v:.{self.dec}f}" if self.dec > 0 else str(int(v))

    def _changed(self, raw):
        v = raw / 100 if self.dec > 0 else raw
        self.val.setText(self._fmt(v)); self.value_changed.emit(v)

    def get_value(self):
        r = self.slider.value()
        return r / 100 if self.dec > 0 else r

    def set_value(self, v):
        self.slider.setValue(int(v * 100) if self.dec > 0 else int(v))


class ColorRow(QWidget):
    color_changed = pyqtSignal(str)

    def __init__(self, default, parent=None):
        super().__init__(parent)
        self._color = default
        self.setStyleSheet("background:transparent;")
        lay = QHBoxLayout(self); lay.setContentsMargins(0,0,0,0); lay.setSpacing(6)
        lbl = QLabel("Target Color  (EMCol)"); lbl.setFont(QFont("Consolas", 8))
        lbl.setStyleSheet(f"color:{C_MUTED};"); lay.addWidget(lbl); lay.addStretch()
        self.dot = QLabel("  "); self.dot.setFixedSize(20, 20)
        self.dot.setStyleSheet(f"background:{default};border-radius:4px;border:1px solid {C_BORDER};")
        lay.addWidget(self.dot)
        self.entry = QLineEdit(default); self.entry.setFont(QFont("Consolas", 9))
        self.entry.setFixedWidth(78); self.entry.setMaxLength(7)
        self.entry.setStyleSheet(f"QLineEdit{{background:{C_BG};color:{C_TEXT};border:1px solid {C_BORDER};border-radius:5px;padding:3px 5px;}}")
        self.entry.returnPressed.connect(self._on_entry); lay.addWidget(self.entry)
        btn = QPushButton("PICK"); btn.setFixedSize(44, 26)
        btn.setFont(QFont("Consolas", 8, QFont.Bold))
        btn.setStyleSheet(f"QPushButton{{background:{C_BORDER};color:{C_ACCENT};border:none;border-radius:5px;}}QPushButton:hover{{background:{C_ACCENT};color:white;}}")
        btn.clicked.connect(self._pick); lay.addWidget(btn)

    def _on_entry(self):
        c = self.entry.text().strip()
        if not c.startswith("#"): c = "#" + c
        if QColor(c).isValid():
            self._color = c.upper()
            self.dot.setStyleSheet(f"background:{self._color};border-radius:4px;border:1px solid {C_BORDER};")
            self.color_changed.emit(self._color)

    def _pick(self):
        c = QColorDialog.getColor(QColor(self._color), None, "Target Color")
        if c.isValid():
            self._color = c.name().upper()
            self.entry.setText(self._color)
            self.dot.setStyleSheet(f"background:{self._color};border-radius:4px;border:1px solid {C_BORDER};")
            self.color_changed.emit(self._color)

    def get_value(self): return self._color
    def set_value(self, v):
        self._color = v; self.entry.setText(v)
        self.dot.setStyleSheet(f"background:{v};border-radius:4px;border:1px solid {C_BORDER};")


def toggle_btn(on_text, off_text, on_color=C_GREEN, off_color=C_MUTED):
    btn = QPushButton(off_text)
    btn.setCheckable(True)
    btn.setFixedHeight(32)
    btn.setFont(QFont("Consolas", 9, QFont.Bold))

    def refresh(checked):
        col = on_color if checked else off_color
        txt = on_text  if checked else off_text
        btn.setText(txt)
        btn.setStyleSheet(f"""
            QPushButton{{background:{C_CARD};color:{col};border:1px solid {col};border-radius:8px;padding:0 10px;}}
            QPushButton:hover{{background:{C_BG};}}
        """)
    btn.toggled.connect(refresh)
    refresh(False)
    return btn


# ─── Aim Lock Tab ─────────────────────────────────────────────────────────────
class AimLockTab(QWidget):
    fps_updated = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background:{C_PANEL};")
        self.worker: AimWorker | None = None
        self._active = False
        self._build()
        self._load()
        self._connect()
        self._refresh_preview()

    def _build(self):
        root = QHBoxLayout(self); root.setContentsMargins(14, 14, 14, 14); root.setSpacing(12)

        # ── Left ──
        left = QVBoxLayout(); left.setSpacing(10)

        det = Card("DETECTION")
        self.color_row  = ColorRow(AIM_DEFAULTS["em_color"])
        self.col_vn_row = SliderRow("Color Variation  (ColVn)", 0, 255, AIM_DEFAULTS["col_vn"])
        self.fov_x_row  = SliderRow("Horizontal FOV X  (px)",  1, 500, AIM_DEFAULTS["fov_x"])
        self.fov_y_row  = SliderRow("Vertical FOV Y  (px)",    1, 200, AIM_DEFAULTS["fov_y"])
        for w in [self.color_row, self.col_vn_row, self.fov_x_row, self.fov_y_row]:
            det.body.addWidget(w)
        left.addWidget(det)

        trk = Card("TRACKING — AXIS CONTROL")
        self.ax_row    = SliderRow("Anti-Shake X  (px)",  1, 100, AIM_DEFAULTS["anti_shake_x"])
        self.ay_row    = SliderRow("Anti-Shake Y  (px)",  1, 100, AIM_DEFAULTS["anti_shake_y"])
        self.mx_row    = SliderRow("Speed X  (multiplier)", 0.1, 10.0, AIM_DEFAULTS["mouse_mult_x"], dec=2, color=C_BLUE)
        self.my_row    = SliderRow("Speed Y  (multiplier)", 0.1, 10.0, AIM_DEFAULTS["mouse_mult_y"], dec=2, color=C_BLUE)
        self.px_row    = SliderRow("Snap Power X  (exponent)", 0.10, 2.0, AIM_DEFAULTS["snap_power_x"], dec=2, color=C_ORANGE)
        self.py_row    = SliderRow("Snap Power Y  (exponent)", 0.10, 2.0, AIM_DEFAULTS["snap_power_y"], dec=2, color=C_ORANGE)
        self.sx_row    = SliderRow("Max Step X  (px/frame)",   1, 100, AIM_DEFAULTS["max_step_x"])
        self.sy_row    = SliderRow("Max Step Y  (px/frame)",   1, 100, AIM_DEFAULTS["max_step_y"])
        self.lc_row    = SliderRow("Loop Count", 1, 50, AIM_DEFAULTS["loop_count"])
        for w in [self.ax_row, self.ay_row, self.mx_row, self.my_row,
                  self.px_row, self.py_row, self.sx_row, self.sy_row, self.lc_row]:
            trk.body.addWidget(w)
        left.addWidget(trk)
        left.addStretch()
        root.addLayout(left, stretch=3)

        # ── Right ──
        right = QVBoxLayout(); right.setSpacing(10)

        prev = Card("FOV PREVIEW")
        self.canvas = FovCanvas()
        prev.body.addWidget(self.canvas, alignment=Qt.AlignCenter)
        right.addWidget(prev)

        self.toggle_btn = QPushButton("▶   START")
        self.toggle_btn.setFixedHeight(46)
        self.toggle_btn.setFont(QFont("Consolas", 11, QFont.Bold))
        self.toggle_btn.setStyleSheet(self._start_style())
        self.toggle_btn.clicked.connect(self._toggle)
        right.addWidget(self.toggle_btn)

        br = QHBoxLayout(); br.setSpacing(8)
        save_btn = QPushButton("SAVE"); save_btn.setFixedHeight(32)
        save_btn.setFont(QFont("Consolas", 9, QFont.Bold))
        save_btn.setStyleSheet(f"QPushButton{{background:{C_CARD};color:{C_BLUE};border:1px solid {C_BORDER};border-radius:8px;}}QPushButton:hover{{border-color:{C_BLUE};}}")
        save_btn.clicked.connect(self._save)
        rst_btn = QPushButton("RESET"); rst_btn.setFixedHeight(32)
        rst_btn.setFont(QFont("Consolas", 9, QFont.Bold))
        rst_btn.setStyleSheet(f"QPushButton{{background:{C_CARD};color:{C_MUTED};border:1px solid {C_BORDER};border-radius:8px;}}QPushButton:hover{{border-color:{C_MUTED};}}")
        rst_btn.clicked.connect(self._reset)
        br.addWidget(save_btn); br.addWidget(rst_btn)
        right.addLayout(br)

        hint = QLabel("Snap Power 0.5 = sqrt (original)  ·  1.0 = linear")
        hint.setFont(QFont("Consolas", 7)); hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet(f"color:{C_MUTED};background:transparent;")
        right.addWidget(hint)
        right.addStretch()
        root.addLayout(right, stretch=2)

    def _connect(self):
        for row in [self.color_row, self.fov_x_row, self.fov_y_row, self.ax_row, self.ay_row]:
            (row.color_changed if isinstance(row, ColorRow) else row.value_changed).connect(
                lambda _: self._refresh_preview()
            )
        if hasattr(self, 'worker') and self.worker:
            self.worker.fps_signal.connect(lambda fps: self.fps_updated.emit(f"FPS: {fps}"))

    def _refresh_preview(self):
        try:
            self.canvas.update_values(
                int(self.fov_x_row.get_value()), int(self.fov_y_row.get_value()),
                int(self.ax_row.get_value()),    int(self.ay_row.get_value()),
                self.color_row.get_value(),
            )
        except Exception: pass

    def get_cfg(self):
        return {
            "em_color":     self.color_row.get_value(),
            "col_vn":       int(self.col_vn_row.get_value()),
            "fov_x":        int(self.fov_x_row.get_value()),
            "fov_y":        int(self.fov_y_row.get_value()),
            "anti_shake_x": int(self.ax_row.get_value()),
            "anti_shake_y": int(self.ay_row.get_value()),
            "mouse_mult_x": round(self.mx_row.get_value(), 2),
            "mouse_mult_y": round(self.my_row.get_value(), 2),
            "snap_power_x": round(self.px_row.get_value(), 2),
            "snap_power_y": round(self.py_row.get_value(), 2),
            "max_step_x":   int(self.sx_row.get_value()),
            "max_step_y":   int(self.sy_row.get_value()),
            "loop_count":   int(self.lc_row.get_value()),
        }

    def _load(self):
        cfg = dict(AIM_DEFAULTS)
        if os.path.exists(AIM_CONFIG):
            try:
                with open(AIM_CONFIG) as f: cfg.update(json.load(f))
            except Exception: pass
        self.color_row.set_value(cfg["em_color"])
        self.col_vn_row.set_value(cfg["col_vn"]); self.fov_x_row.set_value(cfg["fov_x"])
        self.fov_y_row.set_value(cfg["fov_y"]);    self.ax_row.set_value(cfg["anti_shake_x"])
        self.ay_row.set_value(cfg["anti_shake_y"]); self.mx_row.set_value(cfg["mouse_mult_x"])
        self.my_row.set_value(cfg["mouse_mult_y"]); self.px_row.set_value(cfg["snap_power_x"])
        self.py_row.set_value(cfg["snap_power_y"]); self.sx_row.set_value(cfg["max_step_x"])
        self.sy_row.set_value(cfg["max_step_y"]);   self.lc_row.set_value(cfg["loop_count"])

    def _save(self):
        with open(AIM_CONFIG, "w") as f: json.dump(self.get_cfg(), f, indent=2)
        self.fps_updated.emit("SAVED ✓")
        QTimer.singleShot(1500, lambda: self.fps_updated.emit(""))

    def _reset(self):
        self._load()
        self.fps_updated.emit("RESET")
        QTimer.singleShot(1200, lambda: self.fps_updated.emit(""))

    def _toggle(self):
        if self._active: self._stop()
        else:            self._start()

    def _start(self):
        self._active = True
        self.toggle_btn.setText("⏹   STOP"); self.toggle_btn.setStyleSheet(self._stop_style())
        self.worker = AimWorker(self.get_cfg)
        self.worker.fps_signal.connect(lambda fps: self.fps_updated.emit(f"FPS: {fps}"))
        self.worker.start_aim()

    def _stop(self):
        self._active = False
        self.toggle_btn.setText("▶   START"); self.toggle_btn.setStyleSheet(self._start_style())
        self.fps_updated.emit("")
        if self.worker: self.worker.stop_aim(); self.worker = None

    def stop_worker(self):
        if self.worker: self.worker.stop_aim()

    def _start_style(self):
        return f"""QPushButton{{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 {C_ACCENT},stop:1 #c0112a);
            color:white;border:none;border-radius:10px;letter-spacing:3px;}}
            QPushButton:hover{{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 {C_ACCENT2},stop:1 {C_ACCENT});}}"""

    def _stop_style(self):
        return f"""QPushButton{{background:{C_CARD};color:{C_TEXT};border:1px solid {C_BORDER};border-radius:10px;letter-spacing:3px;}}
            QPushButton:hover{{background:{C_BORDER};}}"""


# ─── No Recoil Tab ────────────────────────────────────────────────────────────
class NoRecoilTab(QWidget):
    status_signal = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background:{C_PANEL};")
        self.worker: NoRecoilWorker | None = None
        self._active = False
        self._build()
        self._load(RECOIL_DEFAULTS)
        self._refresh_proc_list()

    def _build(self):
        root = QHBoxLayout(self); root.setContentsMargins(14, 14, 14, 14); root.setSpacing(12)

        # ── Left ──
        left = QVBoxLayout(); left.setSpacing(10)

        nr = Card("NO RECOIL")
        self.ypos_row = SliderRow("Y Recoil+  (move down)",  0, 50, RECOIL_DEFAULTS["recoil_y_pos"], color=C_ORANGE)
        self.yneg_row = SliderRow("Y Recoil−  (pull up)",    0, 50, RECOIL_DEFAULTS["recoil_y_neg"], color=C_BLUE)
        self.ypos_row.value_changed.connect(self._on_ypos)
        self.yneg_row.value_changed.connect(self._on_yneg)

        lock_row = QWidget(); lock_row.setStyleSheet("background:transparent;")
        lr = QHBoxLayout(lock_row); lr.setContentsMargins(0,0,0,0)
        self.lock_chk = QCheckBox("Link Y+ / Y−  (mirror)")
        self.lock_chk.setStyleSheet(f"color:{C_MUTED};font:8pt 'Consolas';background:transparent;")
        self.lock_chk.stateChanged.connect(self._on_lock)
        lr.addWidget(self.lock_chk); lr.addStretch()

        self.delay_row = SliderRow("Delay  (ms between moves)", 1, 100, RECOIL_DEFAULTS["delay"])

        self.nr_toggle = toggle_btn("◉  NO RECOIL ON", "◎  NO RECOIL OFF", C_GREEN, C_MUTED)
        self.nr_toggle.toggled.connect(self._on_nr_toggle)

        for w in [self.ypos_row, self.yneg_row, lock_row, self.delay_row, self.nr_toggle]:
            nr.body.addWidget(w)
        left.addWidget(nr)

        af = Card("AUTOFIRE")
        self.ci_row = SliderRow("Click Interval  (ms)", 10, 500, RECOIL_DEFAULTS["click_interval"])
        self.af_toggle = toggle_btn("◉  AUTOFIRE ON", "◎  AUTOFIRE OFF", C_ACCENT, C_MUTED)
        self.af_toggle.toggled.connect(self._on_af_toggle)
        af.body.addWidget(self.ci_row); af.body.addWidget(self.af_toggle)
        left.addWidget(af)
        left.addStretch()
        root.addLayout(left, stretch=3)

        # ── Right ──
        right = QVBoxLayout(); right.setSpacing(10)

        # Master toggle
        self.master_btn = QPushButton("▶   ACTIVATE")
        self.master_btn.setFixedHeight(46)
        self.master_btn.setFont(QFont("Consolas", 11, QFont.Bold))
        self.master_btn.setStyleSheet(self._start_style())
        self.master_btn.clicked.connect(self._toggle)
        right.addWidget(self.master_btn)

        # Process card
        proc = Card("PROCESS FILTER")
        ph = QHBoxLayout()
        self.proc_combo = QComboBox()
        self.proc_combo.addItem("— Any process —")
        self.proc_combo.setFont(QFont("Consolas", 8))
        self.proc_combo.setStyleSheet(f"""
            QComboBox{{background:{C_BG};color:{C_TEXT};border:1px solid {C_BORDER};border-radius:6px;padding:3px 6px;}}
            QComboBox::drop-down{{border:none;}}
            QComboBox QAbstractItemView{{background:{C_BG};color:{C_TEXT};border:1px solid {C_BORDER};}}
        """)
        ref_btn = QPushButton("↻"); ref_btn.setFixedSize(28, 28)
        ref_btn.setFont(QFont("Consolas", 11))
        ref_btn.setStyleSheet(f"QPushButton{{background:{C_BORDER};color:{C_TEXT};border:none;border-radius:6px;}}QPushButton:hover{{background:{C_ACCENT};color:white;}}")
        ref_btn.clicked.connect(self._refresh_proc_list)
        ph.addWidget(self.proc_combo, 1); ph.addWidget(ref_btn)
        proc.body.addLayout(ph)
        right.addWidget(proc)

        # Config card
        cfg_card = Card("SAVE / LOAD CONFIG")
        sl = QHBoxLayout()
        self.cfg_name = QLineEdit("default")
        self.cfg_name.setFont(QFont("Consolas", 8))
        self.cfg_name.setFixedHeight(28)
        self.cfg_name.setStyleSheet(f"QLineEdit{{background:{C_BG};color:{C_TEXT};border:1px solid {C_BORDER};border-radius:6px;padding:3px 6px;}}")
        sv_btn = QPushButton("SAVE"); sv_btn.setFixedHeight(28)
        sv_btn.setFont(QFont("Consolas", 8, QFont.Bold))
        sv_btn.setStyleSheet(f"QPushButton{{background:{C_CARD};color:{C_BLUE};border:1px solid {C_BORDER};border-radius:6px;}}QPushButton:hover{{border-color:{C_BLUE};}}")
        sv_btn.clicked.connect(self._save_cfg)
        sl.addWidget(self.cfg_name, 1); sl.addWidget(sv_btn)
        cfg_card.body.addLayout(sl)

        ll = QHBoxLayout()
        self.cfg_combo = QComboBox()
        self.cfg_combo.setFont(QFont("Consolas", 8))
        self.cfg_combo.setStyleSheet(f"""
            QComboBox{{background:{C_BG};color:{C_TEXT};border:1px solid {C_BORDER};border-radius:6px;padding:3px 6px;}}
            QComboBox::drop-down{{border:none;}}
            QComboBox QAbstractItemView{{background:{C_BG};color:{C_TEXT};border:1px solid {C_BORDER};}}
        """)
        ld_btn = QPushButton("LOAD"); ld_btn.setFixedHeight(28)
        ld_btn.setFont(QFont("Consolas", 8, QFont.Bold))
        ld_btn.setStyleSheet(f"QPushButton{{background:{C_CARD};color:{C_GREEN};border:1px solid {C_BORDER};border-radius:6px;}}QPushButton:hover{{border-color:{C_GREEN};}}")
        ld_btn.clicked.connect(self._load_selected)
        ll.addWidget(self.cfg_combo, 1); ll.addWidget(ld_btn)
        cfg_card.body.addLayout(ll)
        right.addWidget(cfg_card)

        hints = QLabel("F1 = toggle No Recoil  ·  +/− = adjust Y+/Y−")
        hints.setFont(QFont("Consolas", 7)); hints.setAlignment(Qt.AlignCenter)
        hints.setStyleSheet(f"color:{C_MUTED};background:transparent;")
        right.addWidget(hints)

        self.stat_lbl = QLabel("")
        self.stat_lbl.setFont(QFont("Consolas", 8, QFont.Bold))
        self.stat_lbl.setAlignment(Qt.AlignCenter)
        self.stat_lbl.setStyleSheet(f"color:{C_GREEN};background:transparent;")
        right.addWidget(self.stat_lbl)
        right.addStretch()
        root.addLayout(right, stretch=2)

        self._refresh_cfg_list()

    # ── Slot helpers ──────────────────────────────────────────────────────────
    def _on_ypos(self, v):
        if self.lock_chk.isChecked():
            self.yneg_row.set_value(v)

    def _on_yneg(self, v):
        if self.lock_chk.isChecked():
            self.ypos_row.set_value(v)

    def _on_lock(self, state):
        if state:
            self.yneg_row.set_value(self.ypos_row.get_value())

    def _on_nr_toggle(self, checked):
        if self.worker: pass

    def _on_af_toggle(self, checked):
        if self.worker: pass

    def _refresh_proc_list(self):
        self.proc_combo.clear()
        self.proc_combo.addItem("— Any process —")
        for p in get_process_list():
            self.proc_combo.addItem(p)

    def _refresh_cfg_list(self):
        self.cfg_combo.clear()
        for f in sorted(os.listdir(RECOIL_DIR)):
            if f.endswith(".json"):
                self.cfg_combo.addItem(f[:-5])

    # ── Config ────────────────────────────────────────────────────────────────
    def get_cfg(self):
        sel = self.proc_combo.currentText()
        sel = "" if sel.startswith("—") else sel
        return {
            "recoil_y_pos":      int(self.ypos_row.get_value()),
            "recoil_y_neg":      int(self.yneg_row.get_value()),
            "delay":             int(self.delay_row.get_value()),
            "no_recoil_enabled": self.nr_toggle.isChecked(),
            "click_interval":    int(self.ci_row.get_value()),
            "autofire_enabled":  self.af_toggle.isChecked(),
            "selected_process":  sel,
        }

    def _load(self, cfg):
        self.ypos_row.set_value(cfg["recoil_y_pos"])
        self.yneg_row.set_value(cfg["recoil_y_neg"])
        self.delay_row.set_value(cfg["delay"])
        self.ci_row.set_value(cfg["click_interval"])
        self.nr_toggle.setChecked(cfg["no_recoil_enabled"])
        self.af_toggle.setChecked(cfg["autofire_enabled"])

    def _save_cfg(self):
        name = self.cfg_name.text().strip() or "default"
        path = os.path.join(RECOIL_DIR, name + ".json")
        with open(path, "w") as f: json.dump(self.get_cfg(), f, indent=2)
        self._refresh_cfg_list()
        self._flash(f"Saved: {name}", C_GREEN)

    def _load_selected(self):
        name = self.cfg_combo.currentText()
        if not name: return
        path = os.path.join(RECOIL_DIR, name + ".json")
        if os.path.exists(path):
            with open(path) as f: cfg = json.load(f)
            self._load({**RECOIL_DEFAULTS, **cfg})
            self._flash(f"Loaded: {name}", C_BLUE)

    def _flash(self, msg, color):
        self.stat_lbl.setText(msg)
        self.stat_lbl.setStyleSheet(f"color:{color};background:transparent;font:bold 8pt 'Consolas';")
        QTimer.singleShot(1500, lambda: self.stat_lbl.setText(""))

    # ── F1 / +/- support (called from main window) ────────────────────────────
    def toggle_no_recoil(self):
        self.nr_toggle.setChecked(not self.nr_toggle.isChecked())

    def adjust_ypos(self, delta):
        v = max(0, min(50, int(self.ypos_row.get_value()) + delta))
        self.ypos_row.set_value(v)
        if self.lock_chk.isChecked():
            self.yneg_row.set_value(v)

    # ── Worker ────────────────────────────────────────────────────────────────
    def _toggle(self):
        if self._active: self._stop()
        else:            self._start()

    def _start(self):
        self._active = True
        self.master_btn.setText("⏹   DEACTIVATE"); self.master_btn.setStyleSheet(self._stop_style())
        self.worker = NoRecoilWorker(self.get_cfg)
        self.worker.start_work()

    def _stop(self):
        self._active = False
        self.master_btn.setText("▶   ACTIVATE"); self.master_btn.setStyleSheet(self._start_style())
        if self.worker: self.worker.stop_work(); self.worker = None

    def stop_worker(self):
        if self.worker: self.worker.stop_work()

    def _start_style(self):
        return f"""QPushButton{{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 {C_GREEN},stop:1 #16a34a);
            color:white;border:none;border-radius:10px;letter-spacing:2px;}}
            QPushButton:hover{{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #4ade80,stop:1 {C_GREEN});}}"""

    def _stop_style(self):
        return f"""QPushButton{{background:{C_CARD};color:{C_TEXT};border:1px solid {C_BORDER};border-radius:10px;letter-spacing:2px;}}
            QPushButton:hover{{background:{C_BORDER};}}"""


# ─── Main Window ──────────────────────────────────────────────────────────────
class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(820, 600)
        self._drag_pos = None
        self.kb_listener = None
        self._build_ui()
        self._setup_keyboard()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_pos = e.globalPos() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if e.buttons() == Qt.LeftButton and self._drag_pos:
            self.move(e.globalPos() - self._drag_pos)

    def mouseReleaseEvent(self, _): self._drag_pos = None

    def _build_ui(self):
        outer = QVBoxLayout(self); outer.setContentsMargins(10, 10, 10, 10)

        frame = QFrame(); frame.setObjectName("main")
        frame.setStyleSheet(f"QFrame#main{{background:{C_PANEL};border:1px solid {C_BORDER};border-radius:14px;}}")
        frame.setGraphicsEffect(make_shadow(32, "#000000", 210))

        inner = QVBoxLayout(frame); inner.setContentsMargins(0, 0, 0, 0); inner.setSpacing(0)
        inner.addWidget(self._titlebar())
        inner.addWidget(self._tabs())
        outer.addWidget(frame)

    def _titlebar(self):
        bar = QFrame(); bar.setFixedHeight(48)
        bar.setStyleSheet(f"QFrame{{background:{C_BG};border-bottom:1px solid {C_BORDER};border-radius:14px 14px 0 0;}}")
        lay = QHBoxLayout(bar); lay.setContentsMargins(16, 0, 14, 0)

        icon = QLabel("◎"); icon.setFont(QFont("Consolas", 14, QFont.Bold))
        icon.setStyleSheet(f"color:{C_ACCENT};background:transparent;"); lay.addWidget(icon)

        title = QLabel("AIM LOCK"); title.setFont(QFont("Consolas", 12, QFont.Bold))
        title.setStyleSheet(f"color:{C_TEXT};background:transparent;letter-spacing:4px;"); lay.addWidget(title)

        sub = QLabel("v3.0"); sub.setFont(QFont("Consolas", 8))
        sub.setStyleSheet(f"color:{C_MUTED};background:transparent;margin-left:6px;"); lay.addWidget(sub)
        lay.addStretch()

        self.info_lbl = QLabel(""); self.info_lbl.setFont(QFont("Consolas", 8))
        self.info_lbl.setStyleSheet(f"color:{C_MUTED};background:transparent;margin-right:10px;")
        lay.addWidget(self.info_lbl)

        min_btn = QPushButton("—"); min_btn.setFixedSize(28, 28)
        min_btn.setStyleSheet(f"QPushButton{{background:{C_CARD};color:{C_MUTED};border:1px solid {C_BORDER};border-radius:6px;font-size:12px;}}QPushButton:hover{{background:{C_BORDER};color:{C_TEXT};}}")
        min_btn.clicked.connect(self.showMinimized); lay.addWidget(min_btn)

        close_btn = QPushButton("✕"); close_btn.setFixedSize(28, 28)
        close_btn.setStyleSheet(f"QPushButton{{background:{C_CARD};color:{C_MUTED};border:1px solid {C_BORDER};border-radius:6px;font-size:10px;margin-left:4px;}}QPushButton:hover{{background:{C_ACCENT};color:white;border-color:{C_ACCENT};}}")
        close_btn.clicked.connect(self.close); lay.addWidget(close_btn)
        return bar

    def _tabs(self):
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(f"""
            QTabWidget::pane   {{ border:none; background:{C_PANEL}; }}
            QTabBar::tab       {{ background:{C_BG}; color:{C_MUTED}; font:bold 9pt 'Consolas';
                                  padding:8px 22px; border:none; letter-spacing:2px; }}
            QTabBar::tab:selected  {{ background:{C_PANEL}; color:{C_TEXT};
                                      border-bottom:2px solid {C_ACCENT}; }}
            QTabBar::tab:hover     {{ color:{C_TEXT}; background:{C_CARD}; }}
        """)

        self.aim_tab    = AimLockTab()
        self.recoil_tab = NoRecoilTab()

        self.aim_tab.fps_updated.connect(self._on_info)
        self.tabs.addTab(self.aim_tab,    "🎯  AIM LOCK")
        self.tabs.addTab(self.recoil_tab, "🔫  NO RECOIL")
        return self.tabs

    def _on_info(self, text):
        self.info_lbl.setText(text)

    def _setup_keyboard(self):
        def on_key(key):
            try:
                if key == pynput_keyboard.Key.delete:
                    self.close()
                elif key == pynput_keyboard.Key.f1:
                    self.recoil_tab.toggle_no_recoil()
                elif hasattr(key, 'char'):
                    if key.char == '+':
                        self.recoil_tab.adjust_ypos(+1)
                    elif key.char == '-':
                        self.recoil_tab.adjust_ypos(-1)
            except Exception: pass

        self.kb_listener = pynput_keyboard.Listener(on_press=on_key)
        self.kb_listener.start()

    def closeEvent(self, e):
        self.aim_tab.stop_worker()
        self.recoil_tab.stop_worker()
        if self.kb_listener: self.kb_listener.stop()
        e.accept()


# ─── Entry ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.Window,          QColor(C_BG))
    palette.setColor(QPalette.WindowText,      QColor(C_TEXT))
    palette.setColor(QPalette.Base,            QColor(C_CARD))
    palette.setColor(QPalette.Text,            QColor(C_TEXT))
    palette.setColor(QPalette.Button,          QColor(C_CARD))
    palette.setColor(QPalette.ButtonText,      QColor(C_TEXT))
    palette.setColor(QPalette.Highlight,       QColor(C_ACCENT))
    palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    app.setPalette(palette)

    win = MainWindow()
    win.show()
    sys.exit(app.exec_())
