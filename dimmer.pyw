"""
NightDimmer – overlay click-through per ridurre la luminosità e il colore del monitor.
VERSIONE: No-Widget (Funzionalità Pin rimossa)

Dipendenze:
    pip install -r requirements.txt

Hotkey globali:
    Ctrl+Alt+↑ / ↓   →  oscuramento su/giù
    Ctrl+Alt+D        →  toggle overlay on/off
"""

import sys
import time
import threading
import ctypes
import tkinter as tk
from datetime import date, datetime, timedelta
from pathlib import Path

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        print("Errore: installa tomli con 'pip install tomli' (Python < 3.11)")
        sys.exit(1)

try:
    import pystray
    from pystray import MenuItem as Item
    from PIL import Image, ImageDraw
    import keyboard
    from astral import LocationInfo
    from astral.sun import sun
except ImportError as e:
    print(f"Dipendenza mancante: {e}")
    print("Installa tutto con: pip install pystray pillow astral keyboard")
    sys.exit(1)

# ── Win32 ────────────────────────────────────────────────────────────────────
user32  = ctypes.windll.user32
dwmapi  = ctypes.windll.dwmapi
shcore  = ctypes.windll.shcore

GWL_EXSTYLE        = -20
WS_EX_TRANSPARENT  = 0x00000020
WS_EX_LAYERED      = 0x00080000
WS_EX_NOACTIVATE   = 0x08000000
WS_EX_TOOLWINDOW   = 0x00000080
WS_EX_APPWINDOW    = 0x00040000
HWND_TOPMOST       = -1
SWP_NOMOVE         = 0x0002
SWP_NOSIZE         = 0x0001
SWP_NOACTIVATE     = 0x0010
SWP_SHOWWINDOW     = 0x0040
SWP_FRAMECHANGED   = 0x0020

# Screenshot exclusion (Win10 2004+)
WDA_EXCLUDEFROMCAPTURE = 0x00000011

# DWM backdrop (Win11)
DWMWA_SYSTEMBACKDROP_TYPE      = 38
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMSBT_TRANSIENT               = 3
DWMWCP_ROUND                   = 2

# Colori UI (Catppuccin Mocha)
BG_COLOR  = "#1e1e2e"
SEP_COLOR = "#414155"
FG_COLOR  = "#cdd6f4"
FG_DIM    = "#a6adc8"
FG_HINT   = "#585b70"
BTN_BG    = "#2a2a3e"
BTN_ACT   = "#45475a"
ENT_BG    = "#313244"
ACCENT    = "#89b4fa"
RED_ACC   = "#f38ba8"
GREEN_ACC = "#a6e3a1"

MAX_DARKNESS = 0.80


# ── DPI (calcolato una sola volta) ───────────────────────────────────────────
def _get_dpi_scale() -> float:
    try:
        shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    try:
        return user32.GetDpiForSystem() / 96.0
    except Exception:
        return 1.0

_DPI = _get_dpi_scale()


# ── Win32 helpers ─────────────────────────────────────────────────────────────
def _hwnd(win: tk.BaseWidget) -> int:
    win.update_idletasks()
    return user32.GetParent(win.winfo_id())


def _apply_acrylic(hwnd: int) -> bool:
    try:
        val = ctypes.c_int(DWMSBT_TRANSIENT)
        hr  = dwmapi.DwmSetWindowAttribute(
            hwnd, ctypes.c_uint(DWMWA_SYSTEMBACKDROP_TYPE),
            ctypes.byref(val), ctypes.sizeof(val))
        return hr == 0
    except Exception:
        return False


def _apply_round_corners(hwnd: int):
    try:
        val = ctypes.c_int(DWMWCP_ROUND)
        dwmapi.DwmSetWindowAttribute(
            hwnd, ctypes.c_uint(DWMWA_WINDOW_CORNER_PREFERENCE),
            ctypes.byref(val), ctypes.sizeof(val))
    except Exception:
        pass


def _apply_rounded_region(win: tk.BaseWidget, radius: int = 12):
    try:
        hwnd = _hwnd(win)
        w, h = win.winfo_width(), win.winfo_height()
        if w < 2 or h < 2:
            return
        rgn = ctypes.windll.gdi32.CreateRoundRectRgn(
            0, 0, w + 1, h + 1, radius * 2, radius * 2)
        user32.SetWindowRgn(hwnd, rgn, True)
    except Exception:
        pass


def _setup_glass_window(win: tk.Toplevel, W: int, H: int, x: int, y: int,
                         alpha: float = 0.88):
    """Imposta dimensioni, posizione, effetto vetro e bordi arrotondati."""
    win.overrideredirect(True)
    win.attributes("-topmost", True)
    win.configure(bg=BG_COLOR)
    win.geometry(f"{W}x{H}+{x}+{y}")
    win.update_idletasks()
    hwnd = _hwnd(win)
    ok   = _apply_acrylic(hwnd)
    win.attributes("-alpha", 0.96 if ok else alpha)
    _apply_round_corners(hwnd)
    if not ok:
        win.after(50, lambda: _apply_rounded_region(win, 12))


# ── Tooltip ───────────────────────────────────────────────────────────────────
class Tooltip:
    def __init__(self, widget: tk.Widget, text: str):
        self._widget = widget
        self._text   = text
        self._tip    = None
        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._hide)

    def _show(self, event=None):
        if self._tip:
            return
        x = self._widget.winfo_rootx() + self._widget.winfo_width() // 2
        y = self._widget.winfo_rooty() - 28
        tip = tk.Toplevel(self._widget)
        self._tip = tip
        tip.overrideredirect(True)
        tip.attributes("-topmost", True)
        tip.attributes("-alpha", 0.92)
        tk.Label(tip, text=self._text, bg=ENT_BG, fg=FG_COLOR,
                 font=("Segoe UI", 8), padx=7, pady=3,
                 relief="flat").pack()
        tip.update_idletasks()
        tw = tip.winfo_width()
        tip.geometry(f"+{x - tw // 2}+{y}")

    def _hide(self, event=None):
        if self._tip:
            self._tip.destroy()
            self._tip = None


# ══════════════════════════════════════════════════════════════════════════════
# Configurazione
# ══════════════════════════════════════════════════════════════════════════════

CONFIG_PATH = Path(__file__).resolve().parent / "config.toml"

_DEFAULT_CONFIG = """\
[location]
latitude  = 45.0703
longitude = 7.6869

[schedule]
activation_mode           = "sunset"
activate_offset_minutes   = 60
deactivate_offset_minutes = 30
fixed_on_time  = "21:00"
fixed_off_time = "07:00"

[overlay]
default_intensity = 0.45
default_redness   = 0.35
step              = 0.05

[state]
last_darkness = 0.45
last_redness  = 0.35
"""


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(_DEFAULT_CONFIG, encoding="utf-8")
    with open(CONFIG_PATH, "rb") as f:
        return tomllib.load(f)


def save_config(cfg: dict):
    """Serializza il dict in TOML mantenendo i tipi corretti."""
    def _val(v):
        if isinstance(v, bool):  return "true" if v else "false"
        if isinstance(v, float): return f"{v:.4f}"
        if isinstance(v, int):   return str(v)
        if isinstance(v, str):   return f'"{v}"'
        return str(v)

    lines = []
    for section, values in cfg.items():
        lines.append(f"\n[{section}]")
        for k, v in values.items():
            lines.append(f"{k} = {_val(v)}")
    CONFIG_PATH.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


# ── Avvio automatico ──────────────────────────────────────────────────────────
_REG_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_APP_NAME    = "NightDimmer"


def _get_startup_command() -> str:
    pythonw = Path(sys.executable).parent / "pythonw.exe"
    return f'"{pythonw}" "{Path(__file__).resolve()}"'


def is_autostart_enabled() -> bool:
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_RUN_KEY)
        winreg.QueryValueEx(key, _APP_NAME)
        winreg.CloseKey(key)
        return True
    except Exception:
        return False


def set_autostart(enabled: bool):
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_RUN_KEY, 0,
                             winreg.KEY_SET_VALUE)
        if enabled:
            winreg.SetValueEx(key, _APP_NAME, 0, winreg.REG_SZ,
                              _get_startup_command())
        else:
            try:
                winreg.DeleteValue(key, _APP_NAME)
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
    except Exception as e:
        print(f"[Autostart] Errore: {e}")


# ── Variabili globali dal config ──────────────────────────────────────────────
# Evento usato per svegliare lo scheduler dopo un cambio di config
_sched_reset_event = threading.Event()


def _reload_globals():
    global LAT, LON, ACT_OFFSET, DEA_OFFSET
    global DEFAULT_DARKNESS, DEFAULT_REDNESS, STEP, ACTIVATION_MODE
    global FIXED_ON, FIXED_OFF

    cfg             = load_config()
    LAT             = cfg["location"]["latitude"]
    LON             = cfg["location"]["longitude"]
    ACT_OFFSET      = cfg["schedule"]["activate_offset_minutes"]
    DEA_OFFSET      = cfg["schedule"]["deactivate_offset_minutes"]
    DEFAULT_DARKNESS = float(cfg["overlay"]["default_intensity"])
    DEFAULT_REDNESS  = float(cfg["overlay"].get("default_redness", 0.0))
    STEP            = float(cfg["overlay"]["step"])
    ACTIVATION_MODE = cfg["schedule"].get("activation_mode", "sunset")
    FIXED_ON        = cfg["schedule"].get("fixed_on_time", "21:00")
    FIXED_OFF       = cfg["schedule"].get("fixed_off_time", "07:00")

    # Sveglia lo scheduler so che ricalcoli subito
    _sched_reset_event.set()


_reload_globals()


# ══════════════════════════════════════════════════════════════════════════════
# Formula rosso/oscuramento
# ══════════════════════════════════════════════════════════════════════════════

def max_redness_for(darkness: float) -> float:
    d = darkness * 100
    if d <= 80:
        return (50 - d * 0.25) / 100
    return max(0.0, (110 - d) / 100)


def effective_redness(slider_redness: float, darkness: float) -> float:
    return slider_redness * max_redness_for(darkness)


# ══════════════════════════════════════════════════════════════════════════════
# Calcolo tramonto / alba
# ══════════════════════════════════════════════════════════════════════════════

def get_sun_times(for_date: date):
    local_tz = datetime.now().astimezone().tzinfo
    loc = LocationInfo(latitude=LAT, longitude=LON)
    s   = sun(loc.observer, date=for_date, tzinfo=local_tz)
    return (s["sunset"]  + timedelta(minutes=ACT_OFFSET),
            s["sunrise"] + timedelta(minutes=DEA_OFFSET))


def get_fixed_times(for_date: date):
    local_tz = datetime.now().astimezone().tzinfo
    on_h,  on_m  = map(int, FIXED_ON.split(":"))
    off_h, off_m = map(int, FIXED_OFF.split(":"))
    act = datetime(for_date.year, for_date.month, for_date.day,
                   on_h,  on_m,  tzinfo=local_tz)
    dea = datetime(for_date.year, for_date.month, for_date.day,
                   off_h, off_m, tzinfo=local_tz)
    if dea <= act:
        dea += timedelta(days=1)
    return act, dea


# ══════════════════════════════════════════════════════════════════════════════
# Overlay click-through
# ══════════════════════════════════════════════════════════════════════════════

def _overlay_color(eff_redness: float) -> str:
    return f"#{int(eff_redness * 180):02x}0000"


def _overlay_alpha(darkness: float, eff_redness: float) -> float:
    return min(1.0, darkness + eff_redness * 0.55)


class DimmerOverlay:
    def __init__(self):
        cfg = load_config()
        saved = cfg.get("state", {})
        self._slider_darkness = min(
            float(saved.get("last_darkness", DEFAULT_DARKNESS)), MAX_DARKNESS)
        self._slider_redness = float(saved.get("last_redness", DEFAULT_REDNESS))
        self.visible = False
        self.paused  = False
        self.root    = None
        self._lock   = threading.Lock()

    # ── Proprietà ────────────────────────────────────────────────────────────
    @property
    def slider_darkness(self) -> float: return self._slider_darkness

    @property
    def slider_redness(self) -> float: return self._slider_redness

    @property
    def eff_redness(self) -> float:
        return effective_redness(self._slider_redness, self._slider_darkness)

    # ── Costruzione finestra (thread dedicato) ────────────────────────────────
    def _build_window(self):
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"{sw}x{sh}+0+0")
        self.root.config(bg="black")
        self.canvas = tk.Canvas(self.root, bg="black", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self._rect = self.canvas.create_rectangle(
            0, 0, sw, sh, fill="black", outline="")
        self._apply_visual()
        self._set_clickthrough()
        self.root.withdraw()
        self.root.mainloop()

    def _apply_visual(self):
        er = self.eff_redness
        self.canvas.itemconfig(self._rect, fill=_overlay_color(er))
        alpha = max(0.01, min(1.0, _overlay_alpha(self._slider_darkness, er)))
        self.root.attributes("-alpha", alpha)

    def _set_clickthrough(self):
        self.root.update_idletasks()
        hwnd  = user32.GetParent(self.root.winfo_id())
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE,
                              style | WS_EX_LAYERED | WS_EX_TRANSPARENT)
        try:
            user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
        except Exception:
            pass

    # ── API pubblica ──────────────────────────────────────────────────────────
    def start(self):
        t = threading.Thread(target=self._build_window, daemon=True)
        t.start()
        while self.root is None:
            time.sleep(0.05)

    def show(self):
        with self._lock:
            self.visible = True
            self.paused  = False
            self.root.after(0, self._show)

    def hide(self):
        with self._lock:
            self.visible = False
            self.root.after(0, self.root.withdraw)

    def toggle(self):
        if self.visible:
            self.hide()
        else:
            self.show()

    def pause(self):
        with self._lock:
            self.paused = True
            self.root.after(0, self.root.withdraw)

    def resume(self):
        with self._lock:
            self.paused = False
            if self.visible:
                self.root.after(0, self._show)

    def toggle_pause(self):
        if self.paused:
            self.resume()
        else:
            self.pause()

    def set_darkness(self, value: float):
        with self._lock:
            self._slider_darkness = max(0.0, min(MAX_DARKNESS, value))
            if self.visible and not self.paused:
                self.root.after(0, self._apply_visual)

    def set_redness(self, value: float):
        with self._lock:
            self._slider_redness = max(0.0, min(1.0, value))
            if self.visible and not self.paused:
                self.root.after(0, self._apply_visual)

    def adjust_darkness(self, delta: float):
        self.set_darkness(self._slider_darkness + delta)

    def save_state(self):
        cfg = load_config()
        cfg.setdefault("state", {})
        cfg["state"]["last_darkness"] = round(self._slider_darkness, 4)
        cfg["state"]["last_redness"]  = round(self._slider_redness, 4)
        save_config(cfg)

    def _show(self):
        self._apply_visual()
        self.root.deiconify()
        self._set_clickthrough()


# ══════════════════════════════════════════════════════════════════════════════
# Slider custom click-anywhere
# ══════════════════════════════════════════════════════════════════════════════

class ClickSlider(tk.Canvas):
    def __init__(self, parent, from_=0.0, to=1.0, initial=0.0,
                 fill_color=ACCENT, track_color=BTN_ACT, thumb_color=FG_COLOR,
                 on_change=None, dpi_scale=1.0, **kwargs):
        self.from_     = from_
        self.to        = to
        self._value    = float(initial)
        self.on_change = on_change
        self._track_h  = max(5, int(6  * dpi_scale))
        self._thumb_r  = max(9, int(11 * dpi_scale))
        self._pad      = self._thumb_r + 3
        super().__init__(parent, height=self._thumb_r * 2 + 6,
                         bg=parent.cget("bg"), highlightthickness=0, **kwargs)
        self._fill_color  = fill_color
        self._track_color = track_color
        self._thumb_color = thumb_color
        self._dragging    = False
        self.bind("<Configure>",       lambda e: self._draw())
        self.bind("<ButtonPress-1>",   self._on_press)
        self.bind("<B1-Motion>",       self._on_drag)
        self.bind("<ButtonRelease-1>", lambda e: setattr(self, "_dragging", False))
        self.after(10, self._draw)

    def _draw(self):
        self.delete("all")
        w = self.winfo_width()
        if w <= 1:
            return
        cy  = self.winfo_height() // 2
        pad = self._pad
        tw  = w - pad * 2
        ty1 = cy - self._track_h // 2
        ty2 = cy + self._track_h // 2
        frac = max(0.0, min(1.0, (self._value - self.from_)
                            / max(1e-9, self.to - self.from_)))
        fx = pad + frac * tw
        self.create_rectangle(pad, ty1, pad + tw, ty2,
                              fill=self._track_color, outline="")
        if fx > pad + 1:
            self.create_rectangle(pad, ty1, fx, ty2,
                                  fill=self._fill_color, outline="")
        r = self._thumb_r
        self.create_oval(fx - r, cy - r, fx + r, cy + r,
                         fill=self._thumb_color, outline="")

    def _x_to_value(self, x):
        w    = self.winfo_width()
        frac = (x - self._pad) / max(1, w - self._pad * 2)
        return self.from_ + max(0.0, min(1.0, frac)) * (self.to - self.from_)

    def _on_press(self, e):
        self._dragging = True
        self._set(self._x_to_value(e.x))

    def _on_drag(self, e):
        if self._dragging:
            self._set(self._x_to_value(e.x))

    def _set(self, value):
        self._value = max(self.from_, min(self.to, value))
        self._draw()
        if self.on_change:
            self.on_change(self._value)

    def get(self) -> float:
        return self._value

    def set(self, v: float, silent: bool = False):
        """
        Aggiorna il valore dello slider.
        Con silent=True non chiama on_change (evita loop quando il valore
        viene impostato programmaticamente da un'altra widget sincronizzata).
        """
        self._value = max(self.from_, min(self.to, float(v)))
        self._draw()
        if not silent and self.on_change:
            self.on_change(self._value)


# ══════════════════════════════════════════════════════════════════════════════
# Contenuto pannello condiviso (ControlPanel)
# ══════════════════════════════════════════════════════════════════════════════

def _build_panel_content(frame: tk.Frame, overlay: "DimmerOverlay",
                          dpi: float, on_settings, on_pause, on_quit) -> dict:
    font_sz     = max(9,  int(9  * dpi))
    font_szb    = max(11, int(11 * dpi))
    font_xs     = max(7,  int(7  * dpi))
    btn_pady    = max(4,  int(5  * dpi))
    btn_icon_sz = max(11, int(12 * dpi))

    refs = {}

    # ── Titolo + bottoni icona ────────────────────────────────────────────────
    title_row = tk.Frame(frame, bg=BG_COLOR)
    title_row.pack(fill="x", pady=(0, 10))

    tk.Label(title_row, text="🌙  NightDimmer",
             bg=BG_COLOR, fg=FG_COLOR,
             font=("Segoe UI", font_szb, "bold")).pack(side="left")

    refs["settings_btn"] = tk.Button(
        title_row, text="⚙",
        bg=BG_COLOR, fg=FG_DIM, relief="flat",
        activebackground=BTN_ACT, activeforeground=FG_COLOR,
        font=("Segoe UI", btn_icon_sz), padx=5, pady=2,
        cursor="hand2", bd=0, command=on_settings)
    refs["settings_btn"].pack(side="right", padx=(3, 0))
    Tooltip(refs["settings_btn"], "Impostazioni")

    # ── Slider oscuramento ────────────────────────────────────────────────────
    row1 = tk.Frame(frame, bg=BG_COLOR)
    row1.pack(fill="x", pady=(0, 2))
    tk.Label(row1, text="Oscuramento", bg=BG_COLOR, fg=FG_COLOR,
             font=("Segoe UI", font_sz)).pack(side="left")
    refs["dark_pct"] = tk.Label(
        row1, text=f"{int(overlay.slider_darkness / MAX_DARKNESS * 100)}%",
        bg=BG_COLOR, fg=FG_DIM, font=("Segoe UI", font_sz))
    refs["dark_pct"].pack(side="right")
    refs["dark_slider"] = ClickSlider(
        frame, from_=0.0, to=1.0,
        initial=overlay.slider_darkness / MAX_DARKNESS,
        fill_color=ACCENT, track_color=BTN_ACT, thumb_color=FG_COLOR,
        dpi_scale=dpi)
    refs["dark_slider"].pack(fill="x", pady=(0, 10))

    # ── Slider tinta rossa ────────────────────────────────────────────────────
    row2 = tk.Frame(frame, bg=BG_COLOR)
    row2.pack(fill="x", pady=(0, 2))
    tk.Label(row2, text="Tinta rossa", bg=BG_COLOR, fg=FG_COLOR,
             font=("Segoe UI", font_sz)).pack(side="left")
    refs["red_pct"] = tk.Label(
        row2, text=f"{int(overlay.slider_redness * 100)}%",
        bg=BG_COLOR, fg=FG_DIM, font=("Segoe UI", font_sz))
    refs["red_pct"].pack(side="right")
    refs["red_slider"] = ClickSlider(
        frame, from_=0.0, to=1.0,
        initial=overlay.slider_redness,
        fill_color=RED_ACC, track_color=BTN_ACT, thumb_color=FG_COLOR,
        dpi_scale=dpi)
    refs["red_slider"].pack(fill="x", pady=(0, 12))

    # ── Bottoni azione ────────────────────────────────────────────────────────
    btn_row = tk.Frame(frame, bg=BG_COLOR)
    btn_row.pack(fill="x")

    refs["pause_btn"] = tk.Button(
        btn_row,
        text="▶  Riprendi" if overlay.paused else "⏸  Pausa",
        bg=BTN_BG, fg=FG_COLOR, relief="flat",
        activebackground=BTN_ACT, activeforeground=FG_COLOR,
        font=("Segoe UI", font_sz), padx=10, pady=btn_pady,
        cursor="hand2", bd=0, command=on_pause)
    refs["pause_btn"].pack(side="left")

    refs["quit_btn"] = tk.Button(
        btn_row, text="✕  Esci",
        bg=BTN_BG, fg=RED_ACC, relief="flat",
        activebackground=BTN_ACT, activeforeground=RED_ACC,
        font=("Segoe UI", font_sz), padx=10, pady=btn_pady,
        cursor="hand2", bd=0, command=on_quit)
    refs["quit_btn"].pack(side="right")

    # ── Hint shortcut ─────────────────────────────────────────────────────────
    tk.Label(frame,
             text="Ctrl+Alt+↑↓  oscuramento    Ctrl+Alt+D  on/off",
             bg=BG_COLOR, fg=FG_HINT,
             font=("Segoe UI", font_xs)).pack(anchor="center", pady=(8, 0))

    return refs


def _wire_sliders(refs: dict, overlay: "DimmerOverlay"):
    def on_dark(val):
        real = val * MAX_DARKNESS
        refs["dark_pct"].config(text=f"{int(val * 100)}%")
        overlay.set_darkness(real)
        if (real > 0 or overlay.slider_redness > 0) and not overlay.visible:
            overlay.show()

    def on_red(val):
        refs["red_pct"].config(text=f"{int(val * 100)}%")
        overlay.set_redness(val)
        if (val > 0 or overlay.slider_darkness > 0) and not overlay.visible:
            overlay.show()

    refs["dark_slider"].on_change = on_dark
    refs["red_slider"].on_change  = on_red


# ══════════════════════════════════════════════════════════════════════════════
# Schermata impostazioni
# ══════════════════════════════════════════════════════════════════════════════

class SettingsPanel:
    _instance = None

    def __init__(self, overlay: "DimmerOverlay", panel_root: tk.Tk,
                 parent_win: tk.Toplevel = None):
        # Se esiste già, portala in primo piano invece di aprirne un'altra
        if SettingsPanel._instance is not None:
            try:
                SettingsPanel._instance._win.lift()
                SettingsPanel._instance._win.focus_force()
            except Exception:
                pass
            return
        SettingsPanel._instance = self
        self.overlay    = overlay
        self.panel_root = panel_root
        self._parent_win = parent_win   # finestra chiamante, per il posizionamento
        self._build()

    def _build(self):
        dpi = _DPI
        win = tk.Toplevel(self.panel_root)
        self._win = win
        win.overrideredirect(True)
        win.attributes("-topmost", True)

        font_sz  = max(9,  int(9  * dpi))
        font_szb = max(10, int(10 * dpi))
        pad      = max(14, int(14 * dpi))
        self._font_sz  = font_sz
        self._font_szb = font_szb
        self._pad      = pad

        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        W = max(420, int(420 * dpi))
        H = min(max(500, int(500 * dpi)), int(sh * 0.85))

        # Posiziona sopra la finestra chiamante; fallback al centro schermo
        if self._parent_win is not None:
            try:
                self._parent_win.update_idletasks()
                px = self._parent_win.winfo_x()
                py = self._parent_win.winfo_y()
                pw = self._parent_win.winfo_width()
                # Allinea il bordo sinistro delle impostazioni a quello del pannello
                # e le posiziona immediatamente sopra
                x = px + (pw - W) // 2   # centra orizzontalmente sul pannello
                y = max(0, py - H - 8)   # 8px di gap sopra
                # Clamp: non uscire dai bordi dello schermo
                x = max(0, min(x, sw - W))
                y = max(0, min(y, sh - H))
            except Exception:
                x = (sw - W) // 2
                y = (sh - H) // 2
        else:
            x = (sw - W) // 2
            y = (sh - H) // 2

        _setup_glass_window(win, W, H, x, y, alpha=0.95)

        # Struttura: bordo → wrapper → [titlebar | scrollarea | pulsanti fissi]
        outer = tk.Frame(win, bg=SEP_COLOR, padx=1, pady=1)
        outer.pack(fill="both", expand=True)
        wrapper = tk.Frame(outer, bg=BG_COLOR)
        wrapper.pack(fill="both", expand=True)

        # Titlebar con drag
        self._tbar = tk.Frame(wrapper, bg=BG_COLOR, padx=pad, pady=10)
        self._tbar.pack(fill="x")
        self._tbar.bind("<ButtonPress-1>", self._drag_start)
        self._tbar.bind("<B1-Motion>",     self._drag_move)

        lbl_title = tk.Label(self._tbar, text="⚙  Impostazioni",
                              bg=BG_COLOR, fg=FG_COLOR,
                              font=("Segoe UI", font_szb, "bold"))
        lbl_title.pack(side="left")
        lbl_title.bind("<ButtonPress-1>", self._drag_start)
        lbl_title.bind("<B1-Motion>",     self._drag_move)

        tk.Button(self._tbar, text="✕", bg=BG_COLOR, fg=FG_DIM,
                  relief="flat", activebackground=BTN_ACT, activeforeground=RED_ACC,
                  font=("Segoe UI", font_sz), padx=5, pady=1,
                  cursor="hand2", bd=0, command=self._close).pack(side="right")

        tk.Frame(wrapper, bg=SEP_COLOR, height=1).pack(fill="x")

        # ── Pulsanti fissi in FONDO — devono essere dichiarati PRIMA
        #    dell'area scrollabile, altrimenti canvas expand=True li soffoca
        tk.Frame(wrapper, bg=SEP_COLOR, height=1).pack(side="bottom", fill="x")
        btn_area = tk.Frame(wrapper, bg=BG_COLOR, padx=pad, pady=10)
        btn_area.pack(side="bottom", fill="x")

        # Area scrollabile (prende tutto lo spazio rimasto)
        canvas_outer = tk.Frame(wrapper, bg=BG_COLOR)
        canvas_outer.pack(fill="both", expand=True)

        self._canvas = tk.Canvas(canvas_outer, bg=BG_COLOR, highlightthickness=0)
        sb = tk.Scrollbar(canvas_outer, orient="vertical",
                          command=self._canvas.yview,
                          bg=BG_COLOR, troughcolor=BG_COLOR,
                          activebackground=BTN_ACT, relief="flat", bd=0,
                          width=6)
        self._canvas.configure(yscrollcommand=sb.set)
        self._canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self._scroll_frame = tk.Frame(self._canvas, bg=BG_COLOR)
        self._cwin = self._canvas.create_window(
            (0, 0), window=self._scroll_frame, anchor="nw")

        self._scroll_frame.bind(
            "<Configure>",
            lambda e: self._canvas.configure(
                scrollregion=self._canvas.bbox("all")))
        self._canvas.bind(
            "<Configure>",
            lambda e: self._canvas.itemconfig(self._cwin, width=e.width))
        win.bind(
            "<MouseWheel>",
            lambda e: self._canvas.yview_scroll(
                int(-1 * (e.delta / 120)), "units"))

        main = tk.Frame(self._scroll_frame, bg=BG_COLOR, padx=pad, pady=pad)
        main.pack(fill="x", expand=True)
        self._main = main

        # ── Contenuto ────────────────────────────────────────────────────────
        cfg = load_config()

        def sep(parent=main):
            tk.Frame(parent, bg=SEP_COLOR, height=1).pack(fill="x", pady=(2, 6))

        def section(text, parent=main):
            tk.Label(parent, text=text, bg=BG_COLOR, fg=ACCENT,
                     font=("Segoe UI", font_szb, "bold")).pack(
                         anchor="w", pady=(10, 2))
            sep(parent)

        def entry_row(label, default_val, parent=main, width=10):
            row = tk.Frame(parent, bg=BG_COLOR)
            row.pack(fill="x", pady=3)
            tk.Label(row, text=label, bg=BG_COLOR, fg=FG_COLOR,
                     font=("Segoe UI", font_sz), width=28,
                     anchor="w").pack(side="left")
            var = tk.StringVar(value=str(default_val))
            tk.Entry(row, textvariable=var, bg=ENT_BG, fg=FG_COLOR,
                     insertbackground=FG_COLOR, relief="flat",
                     font=("Segoe UI", font_sz),
                     width=width).pack(side="left", padx=(6, 0))
            return var

        def slider_row(label, initial_val, fill_color=ACCENT, parent=main):
            """
            Slider + label percentuale + entry numerica sincronizzati.
            Usa un flag _updating per evitare loop di aggiornamento reciproco.
            """
            lbl_row = tk.Frame(parent, bg=BG_COLOR)
            lbl_row.pack(fill="x", pady=(6, 1))
            tk.Label(lbl_row, text=label, bg=BG_COLOR, fg=FG_COLOR,
                     font=("Segoe UI", font_sz)).pack(side="left")

            pct_lbl = tk.Label(lbl_row, text=f"{int(initial_val * 100)}%",
                                bg=BG_COLOR, fg=FG_DIM,
                                font=("Segoe UI", font_sz))
            pct_lbl.pack(side="right")

            var = tk.StringVar(value=f"{initial_val:.2f}")
            tk.Entry(lbl_row, textvariable=var, bg=ENT_BG, fg=FG_COLOR,
                     insertbackground=FG_COLOR, relief="flat",
                     font=("Segoe UI", font_sz),
                     width=6).pack(side="right", padx=(0, 8))

            sl = ClickSlider(parent, from_=0.0, to=1.0, initial=initial_val,
                              fill_color=fill_color, track_color=BTN_ACT,
                              thumb_color=FG_COLOR, dpi_scale=dpi)
            sl.pack(fill="x", pady=(0, 4))

            # Flag anti-loop: evita che slider → entry → slider → ...
            _updating = [False]

            def _on_sl(v):
                if _updating[0]:
                    return
                _updating[0] = True
                pct_lbl.config(text=f"{int(v * 100)}%")
                var.set(f"{v:.2f}")
                _updating[0] = False

            def _on_entry(*_):
                if _updating[0]:
                    return
                try:
                    v = max(0.0, min(1.0, float(var.get())))
                    _updating[0] = True
                    sl.set(v, silent=True)       # silent: non richiamare on_sl
                    pct_lbl.config(text=f"{int(v * 100)}%")
                    _updating[0] = False
                except ValueError:
                    pass

            sl.on_change = _on_sl
            var.trace_add("write", _on_entry)
            return var, sl

        # ── Sezione: Posizione ────────────────────────────────────────────────
        section("📍 Posizione")
        self._lat = entry_row("Latitudine",  cfg["location"]["latitude"])
        self._lon = entry_row("Longitudine", cfg["location"]["longitude"])

        # ── Sezione: Attivazione ──────────────────────────────────────────────
        section("⏰ Attivazione")

        mode_row = tk.Frame(main, bg=BG_COLOR)
        mode_row.pack(fill="x", pady=3)
        tk.Label(mode_row, text="Modalità", bg=BG_COLOR, fg=FG_COLOR,
                 font=("Segoe UI", font_sz), width=28,
                 anchor="w").pack(side="left")

        self._mode = tk.StringVar(
            value=cfg["schedule"].get("activation_mode", "sunset"))
        mode_labels = {
            "sunset": "Tramonto",
            "fixed":  "Ora fissa",
            "manual": "Manuale",
        }

        om = tk.OptionMenu(mode_row, self._mode, *mode_labels.keys())
        om.config(bg=ENT_BG, fg=FG_COLOR, activebackground=BTN_ACT,
                   activeforeground=FG_COLOR, relief="flat",
                   font=("Segoe UI", font_sz), highlightthickness=0,
                   bd=0, padx=8, pady=3)
        om["menu"].config(bg=ENT_BG, fg=FG_COLOR, activebackground=BTN_ACT,
                           activeforeground=FG_COLOR,
                           font=("Segoe UI", font_sz),
                           relief="flat", bd=0)
        om.pack(side="left")

        def _sync_om_text(*_):
            om.config(text=mode_labels.get(self._mode.get(), self._mode.get()))

        _sync_om_text()

        # I valori delle varie modalità (sempre presenti in memoria)
        self._act_off   = tk.StringVar(
            value=str(cfg["schedule"]["activate_offset_minutes"]))
        self._deact_off = tk.StringVar(
            value=str(cfg["schedule"]["deactivate_offset_minutes"]))
        self._fixed_on  = tk.StringVar(
            value=cfg["schedule"].get("fixed_on_time",  "21:00"))
        self._fixed_off = tk.StringVar(
            value=cfg["schedule"].get("fixed_off_time", "07:00"))

        # Frame dinamico sotto il dropdown
        self._mode_frame = tk.Frame(main, bg=BG_COLOR)
        self._mode_frame.pack(fill="x")

        # ── Sezione: Valori predefiniti ───────────────────────────────────────
        section("🌙 Valori predefiniti")

        use_row = tk.Frame(main, bg=BG_COLOR)
        use_row.pack(fill="x", pady=(0, 6))
        tk.Button(use_row, text="↩  Usa valori attuali come default",
                  bg=BTN_BG, fg=FG_DIM, relief="flat",
                  activebackground=BTN_ACT, activeforeground=FG_COLOR,
                  font=("Segoe UI", font_sz), padx=10, pady=3,
                  cursor="hand2", bd=0,
                  command=self._use_current_values).pack(side="left")

        self._def_dark_var, self._def_dark_sl = slider_row(
            "Oscuramento default",
            cfg["overlay"]["default_intensity"] / MAX_DARKNESS,
            ACCENT)
        self._def_red_var, self._def_red_sl = slider_row(
            "Tinta rossa default",
            cfg["overlay"].get("default_redness", 0.0),
            RED_ACC)
        # step: entry numerica semplice, nessuno slider
        self._step = entry_row(
            "Passo tasti rapidi (0–1)", cfg["overlay"]["step"], width=6)

        # ── Sezione: Avvio automatico ─────────────────────────────────────────
        # Il frame è creato qui (con BooleanVar) ma viene inserito nel layout
        # da _update_mode_ui, all'interno della sezione Attivazione.
        self._autostart = tk.BooleanVar(value=is_autostart_enabled())

        # Ora che tutti i frame esistono, applica la modalità corrente e
        # registra il trace
        self._update_mode_ui()

        def _on_mode_change(*_):
            _sync_om_text()
            self._update_mode_ui()

        self._mode.trace_add("write", _on_mode_change)

        # ── Pulsanti Salva / Annulla (allineati a destra) ────────────────────
        tk.Button(btn_area, text="Annulla",
                  bg=BTN_BG, fg=FG_DIM, relief="flat",
                  activebackground=BTN_ACT, activeforeground=FG_COLOR,
                  font=("Segoe UI", font_sz), padx=14, pady=5,
                  cursor="hand2", bd=0,
                  command=self._close).pack(side="right", padx=(8, 0))
        tk.Button(btn_area, text="✔  Salva",
                  bg="#2d4a38", fg=GREEN_ACC, relief="flat",
                  activebackground="#3a5a45", activeforeground=GREEN_ACC,
                  font=("Segoe UI", font_sz, "bold"), padx=14, pady=5,
                  cursor="hand2", bd=0, command=self._save).pack(side="right")

    # ── Drag ──────────────────────────────────────────────────────────────────
    def _drag_start(self, e):
        self._dx = e.x_root - self._win.winfo_x()
        self._dy = e.y_root - self._win.winfo_y()

    def _drag_move(self, e):
        self._win.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    # ── Aggiornamento UI modalità ─────────────────────────────────────────────
    def _update_mode_ui(self):
        for w in self._mode_frame.winfo_children():
            w.destroy()
        mode = self._mode.get()
        fz   = self._font_sz

        if mode == "sunset":
            self._add_entry_in(self._mode_frame,
                                "Offset attivazione (min)",
                                self._act_off, fz)
            self._add_entry_in(self._mode_frame,
                                "Offset disattivazione (min)",
                                self._deact_off, fz)
            self._add_autostart_check(self._mode_frame, fz)
        elif mode == "fixed":
            self._add_entry_in(self._mode_frame,
                                "Ora accensione (HH:MM)",
                                self._fixed_on, fz)
            self._add_entry_in(self._mode_frame,
                                "Ora spegnimento (HH:MM)",
                                self._fixed_off, fz)
            self._add_autostart_check(self._mode_frame, fz)
        else:  # manual
            tk.Label(self._mode_frame,
                     text="ℹ  In modalità manuale l'overlay si attiva solo\n"
                          "   quando lo abiliti tu (icona tray o Ctrl+Alt+D).",
                     bg=BG_COLOR, fg=FG_DIM,
                     font=("Segoe UI", fz),
                     justify="left").pack(anchor="w", pady=6)

    def _add_entry_in(self, parent, label, var, font_sz):
        row = tk.Frame(parent, bg=BG_COLOR)
        row.pack(fill="x", pady=3)
        tk.Label(row, text=label, bg=BG_COLOR, fg=FG_COLOR,
                 font=("Segoe UI", font_sz), width=28,
                 anchor="w").pack(side="left")
        tk.Entry(row, textvariable=var, bg=ENT_BG, fg=FG_COLOR,
                 insertbackground=FG_COLOR, relief="flat",
                 font=("Segoe UI", font_sz),
                 width=10).pack(side="left", padx=(6, 0))

    def _add_autostart_check(self, parent, font_sz):
        """Checkbox avvio automatico, mostrata inline nella sezione Attivazione."""
        tk.Checkbutton(parent,
                       text="🚀  Avvia NightDimmer con Windows",
                       variable=self._autostart,
                       bg=BG_COLOR, fg=FG_DIM, selectcolor=ENT_BG,
                       activebackground=BG_COLOR, activeforeground=FG_COLOR,
                       font=("Segoe UI", font_sz)).pack(anchor="w", pady=(8, 2))

    def _use_current_values(self):
        d = self.overlay.slider_darkness / MAX_DARKNESS   # 0-1 display scale
        r = self.overlay.slider_redness
        self._def_dark_sl.set(d, silent=True)
        self._def_dark_var.set(f"{d:.2f}")
        self._def_red_sl.set(r, silent=True)
        self._def_red_var.set(f"{r:.2f}")

    def _save(self):
        try:
            cfg = load_config()

            # Posizione
            cfg["location"]["latitude"]  = float(self._lat.get())
            cfg["location"]["longitude"] = float(self._lon.get())

            # Modalità e parametri schedule
            mode = self._mode.get()
            cfg["schedule"]["activation_mode"] = mode
            if mode == "sunset":
                cfg["schedule"]["activate_offset_minutes"] = \
                    int(self._act_off.get())
                cfg["schedule"]["deactivate_offset_minutes"] = \
                    int(self._deact_off.get())
            elif mode == "fixed":
                cfg["schedule"]["fixed_on_time"] = \
                    self._fixed_on.get().strip()
                cfg["schedule"]["fixed_off_time"] = \
                    self._fixed_off.get().strip()

            # Autostart
            if mode == "manual":
                set_autostart(False)
            else:
                set_autostart(self._autostart.get())

            # Slider oscuramento va 0-1 dove 1 = 100% display = MAX_DARKNESS reale
            cfg["overlay"]["default_intensity"] = round(
                self._def_dark_sl.get() * MAX_DARKNESS, 4)
            cfg["overlay"]["default_redness"]   = round(self._def_red_sl.get(), 4)

            # Step: solo entry numerica, nessuno slider
            cfg["overlay"]["step"] = float(self._step.get())

            save_config(cfg)
            _reload_globals()
            self._close()

        except ValueError as e:
            _show_dark_error(self._win, f"Valore non valido:\n{e}")
        except Exception as e:
            _show_dark_error(self._win, f"Errore durante il salvataggio:\n{e}")

    def _close(self):
        SettingsPanel._instance = None
        try:
            self._win.destroy()
        except Exception:
            pass


# ── Finestra errore dark ───────────────────────────────────────────────────────
def _show_dark_error(parent: tk.BaseWidget, msg: str):
    d = tk.Toplevel(parent)
    d.overrideredirect(True)
    d.attributes("-topmost", True)
    d.configure(bg=BG_COLOR)
    W, H = 320, 120
    sx, sy = d.winfo_screenwidth(), d.winfo_screenheight()
    d.geometry(f"{W}x{H}+{(sx - W) // 2}+{(sy - H) // 2}")
    _apply_round_corners(_hwnd(d))
    outer = tk.Frame(d, bg=SEP_COLOR, padx=1, pady=1)
    outer.pack(fill="both", expand=True)
    inner = tk.Frame(outer, bg=BG_COLOR, padx=16, pady=12)
    inner.pack(fill="both", expand=True)
    tk.Label(inner, text=msg, bg=BG_COLOR, fg=RED_ACC,
             font=("Segoe UI", 9), justify="left").pack(anchor="w")
    tk.Button(inner, text="OK", bg=BTN_BG, fg=FG_COLOR, relief="flat",
              activebackground=BTN_ACT, font=("Segoe UI", 9),
              padx=16, pady=4, cursor="hand2", bd=0,
              command=d.destroy).pack(anchor="e", pady=(10, 0))


# ══════════════════════════════════════════════════════════════════════════════
# ControlPanel (popup autohide)
# ══════════════════════════════════════════════════════════════════════════════

class ControlPanel:
    _instance = None

    def __init__(self, overlay: "DimmerOverlay", panel_root: tk.Tk):
        if ControlPanel._instance is not None:
            try:
                ControlPanel._instance._win.lift()
                ControlPanel._instance._win.focus_force()
            except Exception:
                pass
            return
        ControlPanel._instance = self
        self.overlay    = overlay
        self.panel_root = panel_root
        self._build()

    def _build(self):
        dpi   = _DPI
        win   = tk.Toplevel(self.panel_root)   # parent esplicito
        self._win = win
        pad_x = max(16, int(16 * dpi))
        pad_y = max(14, int(14 * dpi))
        W     = max(320, int(320 * dpi))
        H     = max(260, int(260 * dpi))
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        _setup_glass_window(win, W, H, sw - W - 16, sh - H - 56, alpha=0.88)

        outer = tk.Frame(win, bg=SEP_COLOR, padx=1, pady=1)
        outer.pack(fill="both", expand=True)
        frame = tk.Frame(outer, bg=BG_COLOR, padx=pad_x, pady=pad_y)
        frame.pack(fill="both", expand=True)

        self._refs = _build_panel_content(
            frame, self.overlay, dpi,
            on_settings=self._toggle_settings,
            on_pause=self._on_pause,
            on_quit=self._on_quit)
        _wire_sliders(self._refs, self.overlay)

        win.bind("<FocusOut>", self._on_focus_out)
        win.focus_force()

    def _on_pause(self):
        self.overlay.toggle_pause()
        self._refs["pause_btn"].config(
            text="▶  Riprendi" if self.overlay.paused else "⏸  Pausa")

    def _on_quit(self):
        # 1. Salva stato e nascondi overlay
        self.overlay.save_state()
        self.overlay.hide()
        
        # 2. Ferma l'icona nella tray (se esiste)
        global _tray_icon
        if _tray_icon:
            _tray_icon.stop()

        # 3. Distruggi la finestra del pannello
        self._win.destroy()
        ControlPanel._instance = None

        # 4. KILL SWITCH: Distrugge la root nascosta di Tkinter
        # Questo è il comando fondamentale che termina il processo .pyw
        self.panel_root.quit()
        self.panel_root.destroy()
        sys.exit(0)

    def _toggle_settings(self):
        if SettingsPanel._instance is not None:
            SettingsPanel._instance._close()
        else:
            SettingsPanel(self.overlay, self.panel_root, self._win)

    def _on_focus_out(self, event):
        # Non chiudere se le impostazioni sono aperte
        if SettingsPanel._instance is not None:
            return
        self._win.after(150, self._check_focus)

    def _check_focus(self):
        if SettingsPanel._instance is not None:
            return
        try:
            if self._win.focus_get() is None:
                self._win.destroy()
                ControlPanel._instance = None
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════════════
# System tray
# ══════════════════════════════════════════════════════════════════════════════

_tray_icon = None


def _make_tray_icon():
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d   = ImageDraw.Draw(img)
    d.ellipse([4,  4,  60, 60], fill=(255, 220, 80, 255))
    d.ellipse([18, 4,  64, 52], fill=(0, 0, 0, 0))
    return img


def build_tray(overlay: "DimmerOverlay", panel_root: tk.Tk):
    global _tray_icon

    def open_panel(icon, item):
        panel_root.after(0, lambda: ControlPanel(overlay, panel_root))

    def on_quit(icon, item):
        overlay.save_state()
        overlay.hide()
        icon.stop()

    menu = pystray.Menu(
        Item("⚙  Impostazioni", open_panel, default=True),
        pystray.Menu.SEPARATOR,
        Item("Esci", on_quit),
    )
    icon = pystray.Icon("NightDimmer", _make_tray_icon(), "NightDimmer", menu)
    _tray_icon = icon
    return icon


# ══════════════════════════════════════════════════════════════════════════════
# Scheduler
# ══════════════════════════════════════════════════════════════════════════════

def scheduler_loop(overlay: "DimmerOverlay"):
    """
    Calcola gli orari di attivazione/disattivazione ogni giorno e
    agisce di conseguenza. Può essere svegliato anticipatamente tramite
    _sched_reset_event per ricalcolare immediatamente dopo un cambio config.
    """
    last_date   = None
    activate_at = None
    deactivate_at = None

    while True:
        # Ricalcola se la data è cambiata o se è stato richiesto un reset
        now   = datetime.now().astimezone()
        today = now.date()

        if today != last_date or _sched_reset_event.is_set():
            _sched_reset_event.clear()
            last_date = today
            activate_at = deactivate_at = None

            if ACTIVATION_MODE == "sunset":
                activate_at, deactivate_at = get_sun_times(today)
                # Se deactivate_at è già passato, prendi quello di domani
                if deactivate_at < now:
                    _, deactivate_at = get_sun_times(today + timedelta(days=1))
                print(f"[Scheduler] {today}  "
                      f"attiva: {activate_at.strftime('%H:%M')}  "
                      f"disattiva: {deactivate_at.strftime('%H:%M')}")
            elif ACTIVATION_MODE == "fixed":
                activate_at, deactivate_at = get_fixed_times(today)
                print(f"[Scheduler] {today}  "
                      f"attiva: {activate_at.strftime('%H:%M')}  "
                      f"disattiva: {deactivate_at.strftime('%H:%M')}")

        if ACTIVATION_MODE == "manual":
            # In modalità manuale aspetta 30 s o finché non scatta l'evento
            _sched_reset_event.wait(timeout=30)
            continue

        if activate_at is not None and deactivate_at is not None:
            now = datetime.now().astimezone()
            should_be_on = activate_at <= now < deactivate_at
            if should_be_on and not overlay.visible:
                print("[Scheduler] Attivo overlay")
                overlay.show()
            elif not should_be_on and overlay.visible and not overlay.paused:
                print("[Scheduler] Disattivo overlay")
                overlay.hide()

        # Attendi 30 s o svegliati prima se il config cambia
        _sched_reset_event.wait(timeout=30)


# ══════════════════════════════════════════════════════════════════════════════
# Hotkey globali
# ══════════════════════════════════════════════════════════════════════════════

def register_hotkeys(overlay: "DimmerOverlay"):
    keyboard.add_hotkey("ctrl+alt+up",   lambda: overlay.adjust_darkness(+STEP))
    keyboard.add_hotkey("ctrl+alt+down", lambda: overlay.adjust_darkness(-STEP))
    keyboard.add_hotkey("ctrl+alt+d",    lambda: overlay.toggle())


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

def main():
    panel_root = tk.Tk()
    panel_root.withdraw()

    overlay = DimmerOverlay()
    overlay.start()

    register_hotkeys(overlay)

    sched = threading.Thread(target=scheduler_loop, args=(overlay,), daemon=True)
    sched.start()

    icon = build_tray(overlay, panel_root)

    tray_thread = threading.Thread(target=icon.run, daemon=True)
    tray_thread.start()

    panel_root.mainloop()


if __name__ == "__main__":
    main()