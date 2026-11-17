#!/usr/bin/env python3
"""Ferospark Equalizer — a friendlier front-end for the EasyEffects output equalizer.

All audio processing is still done by EasyEffects. This app only reads and
writes EasyEffects' GSettings keys and preset files.
"""
import json
import math
import os
import subprocess
import sys
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
import cairo
import numpy as np
from gi.repository import GLib

GLib.set_prgname("io.github.matewinslet.FerosparkEqualizer")  # window class, so the dock shows our icon
GLib.set_application_name("Ferospark Equalizer")
from gi.repository import Adw, Gdk, Gio, Gtk, Pango  # noqa: E402

EQ_PATH = "/com/github/wwmm/easyeffects/streamoutputs/equalizer/0/"
PRESET_DIR = os.path.expanduser("~/.config/easyeffects/output")  # replaced by the backend
AUTOSTART = os.path.expanduser("~/.config/autostart/io.github.matewinslet.FerosparkEqualizer-restore.desktop")
OLD_AUTOSTART = os.path.expanduser("~/.config/autostart/easyeffects-service.desktop")
STATE_FILE = os.path.expanduser("~/.config/ferospark-equalizer/state.json")
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DONATE_QR = os.path.join(DATA_DIR, "donate_qr.png")
BINANCE_PAY_ID = "User-ec639"
BUNDLED_PRESETS = os.path.join(DATA_DIR, "presets")
PRESET_PREFIX = "Ferospark – "
MAX_BANDS = 32
FS = 48000.0

# (EasyEffects type, label shown to the user)
TYPES = [
    ("Bell", "Peak"),
    ("Lo-shelf", "Low shelf"),
    ("Hi-shelf", "High shelf"),
    ("Lo-pass", "Low pass"),
    ("Hi-pass", "High pass"),
    ("Notch", "Notch"),
    ("Bandpass", "Band pass"),
]
TYPE_IDS = [t for t, _ in TYPES]
NO_GAIN = {"Lo-pass", "Hi-pass", "Notch", "Bandpass"}

COLORS = ["#62a0ea", "#57e389", "#f8e45c", "#ffa348", "#f66151",
          "#dc8add", "#5bc8af", "#cdab8f", "#99c1f1", "#8ff0a4"]

F_MIN, F_MAX = 20.0, 20000.0
DB_RANGE = 20.0

CSS = """
.donate-btn { color: #f66151; }
.donate-qr { background: #ffffff; border-radius: 12px; padding: 12px; }
.donate-id { font-size: 22px; font-weight: 700; color: #f8c45c; }
window.eqpanel { background: linear-gradient(180deg, #1c1f26 0%, #15171c 100%); }
.power {
  min-width: 64px; min-height: 64px; border-radius: 999px; padding: 0;
  background: #2a2e37; color: #5e6470; border: 2px solid #3a3f4b;
  transition: all 200ms ease;
}
.power:hover { background: #323743; }
.power.on {
  background: radial-gradient(circle, #2ec27e 0%, #1f9d62 100%);
  color: white; border-color: #57e389;
  box-shadow: 0 0 18px rgba(46,194,126,0.55);
}
.power image { -gtk-icon-size: 30px; }
.state-label { font-weight: 800; font-size: 13px; letter-spacing: 2px; }
.state-on { color: #57e389; }
.state-off { color: #77767b; }
.graph-card {
  background: #111318; border-radius: 16px; border: 1px solid #2a2e37;
}
.section-title { font-weight: 700; font-size: 12px; letter-spacing: 1px; color: #9a9da5; }
.band-row { padding: 6px 10px; }
.band-row.selected { background: rgba(98,160,234,0.14); }
.band-dot { min-width: 26px; min-height: 26px; border-radius: 999px;
            font-weight: 800; font-size: 11px; color: #111; }
.preamp-value { font-feature-settings: "tnum"; font-weight: 700; min-width: 70px; }
.hint { color: #7d8089; font-size: 11px; }
.clip-light { min-width: 130px; font-weight: 800; letter-spacing: 1px; font-size: 12px;
              background: #23272f; color: #6b7080; border-radius: 8px; }
.clip-light.clipping { background: #5a1f22; color: #ff8a80; }
.clip-light.clipping.flash { background: #e01b24; color: white;
                             box-shadow: 0 0 16px rgba(224,27,36,0.7); }
.strip-card { background: #1c1f26; border: 1px solid #2a2e37; border-radius: 14px; padding: 10px 8px; }
.band-col { padding: 4px 2px 6px; border-radius: 8px; }
.band-col.selected { background: rgba(255,255,255,0.08);
                     box-shadow: inset 0 0 0 1px rgba(255,255,255,0.18); }
.band-col.dim { opacity: 0.35; }
.band-value { font-feature-settings: "tnum"; font-weight: 700; font-size: 12px; }
.band-freq { color: #9a9da5; font-size: 11px; font-feature-settings: "tnum"; }
.nogain-label { color: #ffbe6f; font-size: 11px; font-weight: 600; }
.inspector { background: #1c1f26; border: 1px solid #2a2e37; border-radius: 14px; padding: 8px 14px; }
.inspector-title { font-weight: 700; }
.strip-card scale highlight { background: none; }
.strip-card scale.vertical slider { border-radius: 3px; min-width: 26px; min-height: 12px;
                                    margin: -4px -11px; }
entry.cell { min-height: 26px; min-width: 0; padding: 0 3px; font-size: 12px;
             font-feature-settings: "tnum"; }
.filter-btn > button { min-height: 26px; min-width: 0; padding: 0 8px; }
.row-label { color: #9a9da5; font-size: 11px; font-weight: 600; }
.preamp-entry { min-width: 0; font-feature-settings: "tnum"; font-weight: 700; }
""" + "".join(f".band-c{i} {{ background: {c}; }}\n"
              f".bandscale-c{i} slider {{ background-color: {c}; background-image: none; }}\n"
              for i, c in enumerate(COLORS))


# ---------------------------------------------------------------- filter math
def biquad(btype, f, gain, q):
    """RBJ cookbook coefficients (same designs Equalizer APO uses)."""
    f = min(max(f, 1.0), FS / 2 - 1)
    q = max(q, 0.01)
    w0 = 2 * math.pi * f / FS
    c, s = math.cos(w0), math.sin(w0)
    alpha = s / (2 * q)
    A = 10 ** (gain / 40)
    if btype == "Bell":
        b = [1 + alpha * A, -2 * c, 1 - alpha * A]
        a = [1 + alpha / A, -2 * c, 1 - alpha / A]
    elif btype == "Lo-shelf":
        k = 2 * math.sqrt(A) * alpha
        b = [A * ((A + 1) - (A - 1) * c + k), 2 * A * ((A - 1) - (A + 1) * c),
             A * ((A + 1) - (A - 1) * c - k)]
        a = [(A + 1) + (A - 1) * c + k, -2 * ((A - 1) + (A + 1) * c),
             (A + 1) + (A - 1) * c - k]
    elif btype == "Hi-shelf":
        k = 2 * math.sqrt(A) * alpha
        b = [A * ((A + 1) + (A - 1) * c + k), -2 * A * ((A - 1) + (A + 1) * c),
             A * ((A + 1) + (A - 1) * c - k)]
        a = [(A + 1) - (A - 1) * c + k, 2 * ((A - 1) - (A + 1) * c),
             (A + 1) - (A - 1) * c - k]
    elif btype == "Lo-pass":
        b = [(1 - c) / 2, 1 - c, (1 - c) / 2]
        a = [1 + alpha, -2 * c, 1 - alpha]
    elif btype == "Hi-pass":
        b = [(1 + c) / 2, -(1 + c), (1 + c) / 2]
        a = [1 + alpha, -2 * c, 1 - alpha]
    elif btype == "Notch":
        b = [1, -2 * c, 1]
        a = [1 + alpha, -2 * c, 1 - alpha]
    elif btype == "Bandpass":
        b = [alpha, 0, -alpha]
        a = [1 + alpha, -2 * c, 1 - alpha]
    else:
        return None
    return b, a


FREQS = np.logspace(math.log10(F_MIN), math.log10(F_MAX), 400)
_Z1 = np.exp(-1j * 2 * np.pi * FREQS / FS)
_Z2 = _Z1 * _Z1


def band_response(band):
    co = biquad(band["type"], band["frequency"], band["gain"], band["q"])
    if co is None:
        return np.zeros_like(FREQS)
    b, a = co
    h = (b[0] + b[1] * _Z1 + b[2] * _Z2) / (a[0] + a[1] * _Z1 + a[2] * _Z2)
    return 20 * np.log10(np.maximum(np.abs(h), 1e-9))


# ------------------------------------------------------------ EasyEffects I/O
# Two ways to talk to EasyEffects:
#   EasyEffects 7 (GTK): its GSettings keys.
#   EasyEffects 8 (Qt):  its local socket "EasyEffectsServer" (8.2.8+ can set and
#                        read every equalizer value through it).
EE_APP_ID = "com.github.wwmm.easyeffects"
RUNTIME_DIR = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
XDG_CONFIG = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
XDG_DATA = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
FLATPAK_DATA = os.path.expanduser(f"~/.var/app/{EE_APP_ID}")


def flatpak_has_ee():
    return any(os.path.isdir(os.path.join(d, EE_APP_ID)) for d in (
        os.path.expanduser("~/.local/share/flatpak/app"), "/var/lib/flatpak/app"))


def read_kconfig(path):
    """Tiny reader for KDE config files: {"Group": {"key": "value"}}."""
    groups, cur = {}, None
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.rstrip("\n")
                if line.startswith("["):
                    cur = groups.setdefault(line.strip("[]").replace("][", "/"), {})
                elif cur is not None and "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    cur[k.split("[")[0].strip()] = v
    except OSError:
        pass
    return groups


class EEBase:
    version = 0

    def eq_section(self):
        raise NotImplementedError

    def _after_save(self, name):
        pass

    def save_preset(self, name):
        """Write the current EQ into a preset, keeping any other effects it has."""
        os.makedirs(self.preset_dir, exist_ok=True)
        path = os.path.join(self.preset_dir, name + ".json")
        base_path = path if os.path.exists(path) else os.path.join(
            self.preset_dir, self.current_preset() + ".json")
        data = {"output": {"blocklist": [], "plugins_order": []}}
        try:
            with open(base_path) as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            pass
        out = data.setdefault("output", {})
        order = out.setdefault("plugins_order", [])
        if "equalizer#0" not in order:
            order.append("equalizer#0")
        out.setdefault("blocklist", [])
        out["equalizer#0"] = self.eq_section()
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(data, fh, indent=4)
        os.replace(tmp, path)
        self._after_save(name)

    def problem(self):
        """A reason the EQ can't be controlled right now, or None."""
        return None


class EE7(EEBase):
    """EasyEffects 7.x (GTK version): everything lives in GSettings."""
    version = 7
    preset_dir = os.path.join(XDG_CONFIG, "easyeffects", "output")
    config_dir = os.path.join(XDG_CONFIG, "easyeffects")

    def __init__(self):
        self.main = Gio.Settings.new(EE_APP_ID)
        self.outputs = Gio.Settings.new(EE_APP_ID + ".streamoutputs")
        self.eq = Gio.Settings.new_with_path(EE_APP_ID + ".equalizer", EQ_PATH)
        self.left = Gio.Settings.new_with_path(EE_APP_ID + ".equalizer.channel", EQ_PATH + "leftchannel/")
        self.right = Gio.Settings.new_with_path(EE_APP_ID + ".equalizer.channel", EQ_PATH + "rightchannel/")

    # --- the EasyEffects process
    def running(self):
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            res = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
                                "org.freedesktop.DBus", "NameHasOwner",
                                GLib.Variant("(s)", (EE_APP_ID,)), GLib.VariantType("(b)"),
                                Gio.DBusCallFlags.NONE, -1, None)
            return res.unpack()[0]
        except GLib.Error:
            return False

    def start(self):
        subprocess.Popen(["easyeffects", "--gapplication-service"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)

    def open_gui(self):
        subprocess.Popen(["easyeffects"], start_new_session=True)

    def watch(self, cb):
        for s in (self.eq, self.left, self.main, self.outputs):
            s.connect("changed", cb)

    # --- reading
    def num_bands(self):
        return self.eq.get_int("num-bands")

    def bands(self):
        out = []
        for i in range(self.num_bands()):
            p = f"band{i}-"
            out.append({
                "type": self.left.get_string(p + "type"),
                "frequency": self.left.get_double(p + "frequency"),
                "gain": self.left.get_double(p + "gain"),
                "q": self.left.get_double(p + "q"),
            })
        return out

    def preamp(self):
        return self.eq.get_double("input-gain")

    def powered(self):
        return not self.main.get_boolean("bypass") and not self.eq.get_boolean("bypass")

    def eq_in_chain(self):
        return "equalizer#0" in self.outputs.get_strv("plugins")

    def band_flags(self):
        n = self.num_bands()
        muted = {i for i in range(n) if self.left.get_boolean(f"band{i}-mute")}
        soloed = {i for i in range(n) if self.left.get_boolean(f"band{i}-solo")}
        return muted, soloed

    def output_device(self):
        return (self.outputs.get_string("output-device"),
                self.outputs.get_boolean("use-default-output-device"))

    # --- writing
    def write_band(self, i, band, full=False):
        p = f"band{i}-"
        for ch in (self.left, self.right):
            ch.set_string(p + "type", band["type"])
            ch.set_double(p + "frequency", float(band["frequency"]))
            ch.set_double(p + "gain", float(band["gain"]))
            ch.set_double(p + "q", float(band["q"]))
            if full:
                ch.set_string(p + "mode", "APO (DR)")
                ch.set_string(p + "slope", "x1")
                ch.set_boolean(p + "mute", False)
                ch.set_boolean(p + "solo", False)

    def set_band_flag(self, i, key, on):
        for ch in (self.left, self.right):
            ch.set_boolean(f"band{i}-{key}", bool(on))

    def set_num_bands(self, n):
        self.eq.set_int("num-bands", n)

    def write_all(self, bands):
        for i, b in enumerate(bands):
            self.write_band(i, b, full=True)
        self.set_num_bands(len(bands))

    def set_preamp(self, v):
        self.eq.set_double("input-gain", float(v))

    def set_power(self, on):
        """Switch only the equalizer; other EasyEffects effects keep running."""
        self.eq.set_boolean("bypass", not on)
        if on:
            self.main.set_boolean("bypass", False)  # a global bypass would silence it

    def clear_global_bypass(self):
        self.main.set_boolean("bypass", False)

    def add_eq_to_chain(self):
        plugins = self.outputs.get_strv("plugins")
        if "equalizer#0" not in plugins:
            self.outputs.set_strv("plugins", plugins + ["equalizer#0"])

    # --- presets
    def current_preset(self):
        return self.main.get_string("last-used-output-preset")

    def forget_current_preset(self):
        self.main.set_string("last-used-output-preset", "")

    def load_preset(self, name):
        subprocess.Popen(["easyeffects", "-l", name], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _after_save(self, name):
        self.main.set_string("last-used-output-preset", name)

    def eq_section(self):
        sec = {}
        for key in ("balance", "input-gain", "output-gain", "pitch-left", "pitch-right"):
            sec[key] = self.eq.get_double(key)
        sec["bypass"] = False  # on/off is the power button's job, not the preset's
        sec["split-channels"] = self.eq.get_boolean("split-channels")
        sec["mode"] = self.eq.get_string("mode")
        n = self.num_bands()
        sec["num-bands"] = n
        for name, ch in (("left", self.left), ("right", self.right)):
            chan = {}
            for i in range(n):
                p = f"band{i}-"
                chan[f"band{i}"] = {
                    "frequency": ch.get_double(p + "frequency"),
                    "gain": ch.get_double(p + "gain"),
                    "mode": ch.get_string(p + "mode"),
                    "mute": False,  # solo/mute are for listening only, never saved
                    "q": ch.get_double(p + "q"),
                    "slope": ch.get_string(p + "slope"),
                    "solo": False,
                    "type": ch.get_string(p + "type"),
                    "width": ch.get_double(p + "width"),
                }
            sec[name] = chan
        return sec


# EasyEffects 8 stores choices as list positions; these are its label lists.
EE8_BAND_TYPES = ["Off", "Bell", "Hi-pass", "Hi-shelf", "Lo-pass", "Lo-shelf", "Notch",
                  "Resonance", "Allpass", "Bandpass", "Ladder-pass", "Ladder-rej"]
EE8_BAND_MODES = ["RLC (BT)", "RLC (MT)", "BWC (BT)", "BWC (MT)", "LRX (BT)", "LRX (MT)", "APO (DR)"]
EE8_SLOPES = ["x1", "x2", "x3", "x4"]
EE8_EQ_MODES = ["IIR", "FIR", "FFT", "SPM", "MP FIR", "MP FFT", "MP SPM"]


class EE8(EEBase):
    """EasyEffects 8.x (Qt version), native package or Flatpak, through its local socket."""
    version = 8
    EQ = "output:equalizer:0"

    def __init__(self, flatpak=False):
        self.flatpak = flatpak
        if flatpak:
            self.cmd = ["flatpak", "run", EE_APP_ID]
            self.config_dir = os.path.join(FLATPAK_DATA, "config", "easyeffects")
            self.preset_dir = os.path.join(FLATPAK_DATA, "data", "easyeffects", "output")
            self.sock_path = os.path.join(RUNTIME_DIR, ".flatpak", EE_APP_ID, "tmp", "EasyEffectsServer")
        else:
            self.cmd = ["easyeffects"]
            self.config_dir = os.path.join(XDG_CONFIG, "easyeffects")
            self.preset_dir = os.path.join(XDG_DATA, "easyeffects", "output")
            self.sock_path = os.path.join(RUNTIME_DIR, "EasyEffectsServer")
        self._monitor = None
        self._sock = None

    # --- socket (one connection kept open, so commands arrive in order)
    def _connect(self):
        import socket
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(1.0)
        for attempt in range(25):  # queue full right now: wait a moment
            try:
                s.connect(self.sock_path)
                return s
            except BlockingIOError:
                time.sleep(0.01)
            except OSError:
                s.close()
                raise
        s.close()
        raise OSError("EasyEffects socket busy")

    def _drop(self):
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def _talk(self, lines, replies=0, raw=False):
        """Send command lines; return `replies` answer lines (None on failure)."""
        for attempt in (0, 1):  # second try with a fresh connection (EasyEffects restarted)
            try:
                if self._sock is None:
                    self._sock = self._connect()
                s = self._sock
                if lines:
                    s.sendall("".join(lines).encode())
                if not replies:
                    return []
                buf = b""
                while True:
                    if raw and buf:
                        return [buf.decode(errors="replace")]
                    if not raw and buf.count(b"\n") >= replies:
                        break
                    chunk = s.recv(65536)
                    if not chunk:
                        raise ConnectionResetError
                    buf += chunk
                return buf.decode(errors="replace").split("\n")[:replies]
            except TimeoutError:
                self._drop()  # no answer (EasyEffects too old?); late replies would confuse us
                return None
            except OSError:
                self._drop()
                if attempt:
                    return None
        return None

    def _get(self, props):
        """props: [(channel or None, name)] on equalizer #0 -> list of strings."""
        lines = [f"get_property:{self.EQ}:{ch + ':' if ch else ''}{name}\n" for ch, name in props]
        return self._talk(lines, len(lines))

    def _set(self, props):
        """props: [(channel or None, name, value)]."""
        def fmt(v):
            if isinstance(v, bool):
                return "true" if v else "false"
            return repr(float(v)) if isinstance(v, float) else str(v)
        self._talk([f"set_property:{self.EQ}:{ch + ':' if ch else ''}{name}:{fmt(v)}\n"
                    for ch, name, v in props])

    @staticmethod
    def _num(txt, default=0.0):
        try:
            return float(txt)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _label(labels, txt, default):
        try:
            return labels[int(txt)]
        except (TypeError, ValueError, IndexError):
            return default

    # --- the EasyEffects process
    def running(self):
        try:
            self._connect().close()
            return True
        except OSError:
            return False

    def problem(self):
        if not self.running():
            return None
        ans = self._get([("left", "band0Gain")])
        if ans is None:
            return "This EasyEffects 8 is too old. Please update it to 8.2.8 or newer"
        return None

    def start(self):
        subprocess.Popen(self.cmd + ["--service-mode", "--hide-window"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)

    def open_gui(self):
        subprocess.Popen(self.cmd, start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def watch(self, cb):
        # EasyEffects 8 writes its settings to files in config_dir/db; watch them
        # instead of polling the socket.
        db = os.path.join(self.config_dir, "db")
        os.makedirs(db, exist_ok=True)
        self._monitor = Gio.File.new_for_path(db).monitor_directory(Gio.FileMonitorFlags.NONE, None)
        self._monitor.connect("changed", lambda *_: cb())

    def _rc(self):
        return read_kconfig(os.path.join(self.config_dir, "db", "easyeffectsrc"))

    # --- reading
    def num_bands(self):
        ans = self._get([(None, "numBands")])
        return int(self._num(ans[0], 0)) if ans else 0

    def bands(self):
        n = self.num_bands()
        props = [("left", f"band{i}{k}") for i in range(n) for k in ("Type", "Frequency", "Gain", "Q")]
        ans = self._get(props) if n else []
        if ans is None:
            return []
        out = []
        for i in range(n):
            t, f, g, q = ans[i * 4:i * 4 + 4]
            out.append({"type": self._label(EE8_BAND_TYPES, t, "Bell"),
                        "frequency": self._num(f, 1000.0), "gain": self._num(g), "q": self._num(q, 1.0)})
        return out

    def preamp(self):
        ans = self._get([(None, "inputGain")])
        return self._num(ans[0]) if ans else 0.0

    def powered(self):
        g = self._talk(["get_global_bypass\n"], 1, raw=True)
        eq = self._get([(None, "bypass")])
        global_off = bool(g) and g[0].strip().startswith("1")
        eq_off = bool(eq) and eq[0].strip() == "true"
        return not global_off and not eq_off

    def eq_in_chain(self):
        plugins = self._rc().get("StreamOutputs", {}).get("plugins")
        if plugins is not None:
            return "equalizer#0" in plugins.replace("\\,", "\0").split(",")
        ans = self._get([(None, "bypass")])
        return bool(ans) and not ans[0].startswith("error")

    def band_flags(self):
        n = self.num_bands()
        ans = self._get([("left", f"band{i}{k}") for i in range(n) for k in ("Mute", "Solo")]) if n else []
        if not ans:
            return set(), set()
        muted = {i for i in range(n) if ans[i * 2] == "true"}
        soloed = {i for i in range(n) if ans[i * 2 + 1] == "true"}
        return muted, soloed

    def output_device(self):
        so = self._rc().get("StreamOutputs", {})
        return so.get("outputDevice", ""), so.get("useDefaultOutputDevice", "true") != "false"

    # --- writing
    @staticmethod
    def _band_props(i, band, full=False):
        t = EE8_BAND_TYPES.index(band["type"]) if band["type"] in EE8_BAND_TYPES else 1
        props = []
        for ch in ("left", "right"):
            props += [(ch, f"band{i}Type", t), (ch, f"band{i}Frequency", float(band["frequency"])),
                      (ch, f"band{i}Gain", float(band["gain"])), (ch, f"band{i}Q", float(band["q"]))]
            if full:
                props += [(ch, f"band{i}Mode", EE8_BAND_MODES.index("APO (DR)")),
                          (ch, f"band{i}Slope", 0), (ch, f"band{i}Mute", False),
                          (ch, f"band{i}Solo", False)]
        return props

    def write_band(self, i, band, full=False):
        self._set(self._band_props(i, band, full))

    def set_band_flag(self, i, key, on):
        k = key.capitalize()
        self._set([(ch, f"band{i}{k}", bool(on)) for ch in ("left", "right")])

    def set_num_bands(self, n):
        self._set([(None, "numBands", int(n))])

    def write_all(self, bands):
        props = [p for i, b in enumerate(bands) for p in self._band_props(i, b, full=True)]
        self._set(props + [(None, "numBands", len(bands))])  # one connection

    def set_preamp(self, v):
        self._set([(None, "inputGain", float(v))])

    def set_power(self, on):
        """Switch only the equalizer; other EasyEffects effects keep running."""
        self._set([(None, "bypass", not on)])
        if on:
            self._talk(["global_bypass:0\n"])  # a global bypass would silence it

    def clear_global_bypass(self):
        self._talk(["global_bypass:0\n"])

    def add_eq_to_chain(self):
        # The socket can't change the effects list, so load a preset that has an
        # equalizer (our own flat one).
        name = PRESET_PREFIX + "Flat"
        dst = os.path.join(self.preset_dir, name + ".json")
        if not os.path.exists(dst):
            os.makedirs(self.preset_dir, exist_ok=True)
            with open(os.path.join(BUNDLED_PRESETS, name + ".json"), "rb") as src, open(dst, "wb") as out:
                out.write(src.read())
        self.load_preset(name)

    # --- presets
    def current_preset(self):
        ans = self._talk(["get_last_loaded_preset:output\n"], 1)
        return ans[0].strip() if ans else ""

    def forget_current_preset(self):
        pass  # EasyEffects 8 has no way to clear it from outside

    def load_preset(self, name):
        self._talk([f"load_preset:output:{name[:100]}\n"])

    def _after_save(self, name):
        if name != self.current_preset():
            self.load_preset(name)  # same sound; makes it the current preset

    def eq_section(self):
        top = ["balance", "inputGain", "outputGain", "pitchLeft", "pitchRight",
               "bypass", "splitChannels", "mode", "numBands"]
        a = self._get([(None, k) for k in top]) or [""] * len(top)
        v = dict(zip(top, a))
        n = int(self._num(v["numBands"], 0))
        sec = {"balance": self._num(v["balance"]), "input-gain": self._num(v["inputGain"]),
               "output-gain": self._num(v["outputGain"]), "pitch-left": self._num(v["pitchLeft"]),
               "pitch-right": self._num(v["pitchRight"]), "bypass": False,
               "split-channels": v["splitChannels"] == "true",
               "mode": self._label(EE8_EQ_MODES, v["mode"], "IIR"), "num-bands": n}
        keys = ("Frequency", "Gain", "Mode", "Q", "Slope", "Type", "Width")
        for ch in ("left", "right"):
            a = self._get([(ch, f"band{i}{k}") for i in range(n) for k in keys]) or [""] * (n * len(keys))
            chan = {}
            for i in range(n):
                f, g, m, q, sl, t, w = a[i * 7:i * 7 + 7]
                chan[f"band{i}"] = {
                    "frequency": self._num(f, 1000.0), "gain": self._num(g),
                    "mode": self._label(EE8_BAND_MODES, m, "APO (DR)"), "mute": False,
                    "q": self._num(q, 1.0), "slope": self._label(EE8_SLOPES, sl, "x1"),
                    "solo": False, "type": self._label(EE8_BAND_TYPES, t, "Bell"),
                    "width": self._num(w, 4.0)}
            sec[ch] = chan
        return sec


def detect_backend():
    """EasyEffects 7 if its settings schema is installed, otherwise EasyEffects 8
    (native first, then Flatpak). None when EasyEffects isn't installed at all."""
    import shutil
    src = Gio.SettingsSchemaSource.get_default()
    has7 = src is not None and src.lookup(EE_APP_ID, True) is not None
    if has7 and shutil.which("easyeffects"):
        return EE7()
    native, flat = shutil.which("easyeffects"), flatpak_has_ee()
    if native and flat:  # both installed: the one running, else the Flatpak (usually newer)
        nb = EE8(flatpak=False)
        return nb if nb.running() else EE8(flatpak=True)
    if native:
        return EE8(flatpak=False)
    if flat:
        return EE8(flatpak=True)
    if has7:
        return EE7()
    return None


BACKEND = detect_backend()
if BACKEND is not None:
    PRESET_DIR = BACKEND.preset_dir


def ee_running():
    return BACKEND is not None and BACKEND.running()


def start_ee_service():
    if BACKEND is not None:
        BACKEND.start()


# The power button switches only the equalizer. Its on/off state is kept in our
# own file and re-applied after EasyEffects starts or a preset is loaded.
# POWER_MODE 2 = equalizer only; earlier versions switched all of EasyEffects off.
POWER_MODE = 2


def _state():
    try:
        with open(STATE_FILE) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def saved_power():
    return bool(_state().get("power", True))


def save_power(on):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump({"power": bool(on), "mode": POWER_MODE}, fh)
    os.replace(tmp, STATE_FILE)


def enforce_power(done=None, timeout=15):
    """Wait for EasyEffects to come up, then keep re-applying the saved power
    state for a few seconds (its startup reset can land after our first write)."""
    ee = BACKEND
    if ee is None:
        if done:
            done()
        return
    ticks = {"n": 0, "up": 0}

    def tick():
        ticks["n"] += 1
        if ee_running():
            ticks["up"] += 1
            want = saved_power()
            if ticks["up"] == 1:
                migrate_power_mode()
            if ee.powered() != want:
                ee.set_power(want)
        if ticks["up"] >= 8 or ticks["n"] >= timeout * 2:
            if done:
                done()
            return False
        return True

    GLib.timeout_add(500, tick)


def migrate_power_mode():
    """Older versions switched ALL of EasyEffects off (global bypass). Now only the
    equalizer is switched; undo the old global bypass once if we had set it."""
    st = _state()
    if st.get("mode") == POWER_MODE or not ee_running():
        return
    if not st.get("power", True):
        BACKEND.clear_global_bypass()
        BACKEND.set_power(False)
    save_power(st.get("power", True))


def install_autostart():
    """Login entry that restores the saved on/off state (replaces the old
    plain EasyEffects autostart, which always came up ON)."""
    os.makedirs(os.path.dirname(AUTOSTART), exist_ok=True)
    with open(AUTOSTART, "w") as fh:
        fh.write("[Desktop Entry]\nName=Ferospark Equalizer (restore state)\n"
                 "Comment=Starts EasyEffects and restores the equalizer on/off state\n"
                 f"Exec=python3 {os.path.abspath(__file__)} --restore\n"
                 "Icon=io.github.matewinslet.FerosparkEqualizer\nNoDisplay=true\n"
                 "StartupNotify=false\nTerminal=false\nType=Application\n"
                 "X-GNOME-Autostart-enabled=true\n")
    if os.path.exists(OLD_AUTOSTART):
        os.remove(OLD_AUTOSTART)


def restore():
    """Run at login: start EasyEffects (its other effects may be in use too) and
    put the equalizer back on or off as the user left it."""
    if BACKEND is None:
        return 0
    if not ee_running():
        start_ee_service()
    loop = GLib.MainLoop()
    enforce_power(loop.quit)
    loop.run()
    return 0


def install_bundled_presets():
    """Copy the presets shipped with the app into EasyEffects' preset folder.
    A preset the user already has (maybe edited) is never overwritten, and one
    the user deleted is not brought back."""
    marker = os.path.join(os.path.dirname(STATE_FILE), "presets-installed.json")
    try:
        with open(marker) as fh:
            done = set(json.load(fh))
    except (OSError, ValueError):
        done = set()
    try:
        shipped = sorted(f for f in os.listdir(BUNDLED_PRESETS) if f.endswith(".json"))
    except OSError:
        return
    if not set(shipped) - done:
        return
    os.makedirs(PRESET_DIR, exist_ok=True)
    for f in shipped:
        dst = os.path.join(PRESET_DIR, f)
        if f not in done and not os.path.exists(dst):
            with open(os.path.join(BUNDLED_PRESETS, f), "rb") as src, open(dst, "wb") as out:
                out.write(src.read())
        done.add(f)
    os.makedirs(os.path.dirname(marker), exist_ok=True)
    with open(marker, "w") as fh:
        json.dump(sorted(done), fh)


def clean_name(text):
    """Preset name that is safe as a file name: no folders, no hidden files."""
    name = "".join(c for c in text if c.isprintable()).strip().replace("/", "-")
    return name.lstrip(".").strip()


def list_presets():
    try:
        names = [f[:-5] for f in os.listdir(PRESET_DIR) if f.endswith(".json")]
    except FileNotFoundError:
        names = []
    return sorted(names, key=str.lower)


# ----------------------------------------------------------------- the graph
class ResponseGraph(Gtk.DrawingArea):
    PAD_L, PAD_R, PAD_T, PAD_B = 44, 16, 16, 28

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.set_vexpand(True)
        self.set_content_height(225)
        self.set_draw_func(self.draw)
        self.hover = None
        self.drag = None

        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self.on_drag_begin)
        drag.connect("drag-update", self.on_drag_update)
        drag.connect("drag-end", self.on_drag_end)
        self.add_controller(drag)

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self.on_motion)
        motion.connect("leave", lambda *_: self.set_hover(None))
        self.add_controller(motion)

        scroll = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect("scroll", self.on_scroll)
        self.add_controller(scroll)

    # coordinate helpers
    def _plot(self):
        w, h = self.get_width(), self.get_height()
        return self.PAD_L, self.PAD_T, w - self.PAD_L - self.PAD_R, h - self.PAD_T - self.PAD_B

    def f_to_x(self, f):
        x0, _, pw, _ = self._plot()
        t = (math.log10(f) - math.log10(F_MIN)) / (math.log10(F_MAX) - math.log10(F_MIN))
        return x0 + t * pw

    def x_to_f(self, x):
        x0, _, pw, _ = self._plot()
        t = (x - x0) / pw
        return 10 ** (math.log10(F_MIN) + t * (math.log10(F_MAX) - math.log10(F_MIN)))

    def db_to_y(self, db):
        _, y0, _, ph = self._plot()
        return y0 + (DB_RANGE - db) / (2 * DB_RANGE) * ph

    def y_to_db(self, y):
        _, y0, _, ph = self._plot()
        return DB_RANGE - (y - y0) / ph * 2 * DB_RANGE

    def node_pos(self, band):
        db = 0 if band["type"] in NO_GAIN else band["gain"]
        return self.f_to_x(band["frequency"]), self.db_to_y(db)

    def hit(self, x, y):
        best, bd = None, 16 ** 2
        for i, b in enumerate(self.win.bands):
            nx, ny = self.node_pos(b)
            d = (nx - x) ** 2 + (ny - y) ** 2
            if d < bd:
                best, bd = i, d
        return best

    # interaction
    def set_hover(self, i):
        if i != self.hover:
            self.hover = i
            self.queue_draw()

    def on_motion(self, _c, x, y):
        if self.drag is None:
            self.set_hover(self.hit(x, y))

    def on_drag_begin(self, g, x, y):
        i = self.hit(x, y)
        if i is None:
            g.set_state(Gtk.EventSequenceState.DENIED)
            return
        self.drag = (i, x, y)
        self.win.select_band(i)

    def on_drag_update(self, _g, dx, dy):
        if self.drag is None:
            return
        i, sx, sy = self.drag
        band = dict(self.win.bands[i])
        band["frequency"] = round(min(max(self.x_to_f(sx + dx), F_MIN), F_MAX))
        if band["type"] not in NO_GAIN:
            band["gain"] = round(min(max(self.y_to_db(sy + dy), -DB_RANGE), DB_RANGE), 1)
        self.win.update_band(i, band)

    def on_drag_end(self, *_):
        self.drag = None

    def on_scroll(self, _c, _dx, dy):
        i = self.hover
        if i is None:
            return False
        band = dict(self.win.bands[i])
        band["q"] = round(min(max(band["q"] * (0.9 if dy > 0 else 1.1), 0.1), 20), 2)
        self.win.update_band(i, band)
        return True

    # drawing
    def draw(self, _area, cr, w, h):
        x0, y0, pw, ph = self._plot()
        cr.select_font_face("Sans")
        cr.set_font_size(10)

        # grid
        cr.set_line_width(1)
        for db in range(-int(DB_RANGE), int(DB_RANGE) + 1, 5):
            y = round(self.db_to_y(db)) + 0.5
            cr.set_source_rgba(1, 1, 1, 0.16 if db == 0 else 0.05)
            cr.move_to(x0, y)
            cr.line_to(x0 + pw, y)
            cr.stroke()
            cr.set_source_rgba(1, 1, 1, 0.4)
            label = f"{db:+d}" if db else "0 dB"
            ext = cr.text_extents(label)
            cr.move_to(x0 - ext.width - 8, y + 3.5)
            cr.show_text(label)
        for f, lab in ((20, "20"), (50, "50"), (100, "100"), (200, "200"), (500, "500"),
                       (1000, "1k"), (2000, "2k"), (5000, "5k"), (10000, "10k"), (20000, "20k")):
            x = round(self.f_to_x(f)) + 0.5
            cr.set_source_rgba(1, 1, 1, 0.05)
            cr.move_to(x, y0)
            cr.line_to(x, y0 + ph)
            cr.stroke()
            cr.set_source_rgba(1, 1, 1, 0.4)
            ext = cr.text_extents(lab)
            cr.move_to(min(max(x - ext.width / 2, x0), x0 + pw - ext.width), y0 + ph + 17)
            cr.show_text(lab)

        bands = self.win.bands
        on = self.win.power_on
        responses = [band_response(b) if self.win.audible(i) else np.zeros_like(FREQS)
                     for i, b in enumerate(bands)]
        total = sum(responses, np.zeros_like(FREQS)) + self.win.preamp

        cr.save()
        cr.rectangle(x0, y0, pw, ph)
        cr.clip()

        # selected band's own curve
        sel = self.win.selected
        if sel is not None and sel < len(responses):
            rgba = Gdk.RGBA()
            rgba.parse(COLORS[sel % len(COLORS)])
            cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, 0.55)
            cr.set_line_width(1.5)
            cr.set_dash([4, 4])
            for k, (f, v) in enumerate(zip(FREQS, responses[sel])):
                (cr.move_to if k == 0 else cr.line_to)(self.f_to_x(f), self.db_to_y(v))
            cr.stroke()
            cr.set_dash([])

        # total curve with gradient fill
        pts = [(self.f_to_x(f), self.db_to_y(v)) for f, v in zip(FREQS, total)]
        zero = self.db_to_y(0)

        cr.move_to(pts[0][0], zero)
        for x, y in pts:
            cr.line_to(x, y)
        cr.line_to(pts[-1][0], zero)
        cr.close_path()
        grad = cairo.LinearGradient(0, y0, 0, y0 + ph)
        if on:
            grad.add_color_stop_rgba(0, 0.18, 0.76, 0.49, 0.35)
            grad.add_color_stop_rgba(0.5, 0.18, 0.76, 0.49, 0.05)
            grad.add_color_stop_rgba(1, 0.38, 0.63, 0.92, 0.30)
        else:
            grad.add_color_stop_rgba(0, 0.5, 0.5, 0.5, 0.15)
            grad.add_color_stop_rgba(1, 0.5, 0.5, 0.5, 0.15)
        cr.set_source(grad)
        cr.fill()

        cr.set_line_width(2.5)
        cr.set_source_rgba(0.34, 0.89, 0.54, 1) if on else cr.set_source_rgba(0.55, 0.55, 0.58, 1)
        for k, (x, y) in enumerate(pts):
            (cr.move_to if k == 0 else cr.line_to)(x, y)
        cr.stroke()
        cr.restore()

        # nodes
        for i, b in enumerate(bands):
            nx, ny = self.node_pos(b)
            rgba = Gdk.RGBA()
            rgba.parse(COLORS[i % len(COLORS)])
            big = i == self.hover or i == sel
            r = 10 if big else 8
            if big:
                cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, 0.25)
                cr.arc(nx, ny, r + 6, 0, 2 * math.pi)
                cr.fill()
            cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, 1 if on else 0.5)
            cr.arc(nx, ny, r, 0, 2 * math.pi)
            cr.fill()
            cr.set_source_rgba(0.07, 0.07, 0.08, 1)
            cr.set_font_size(9 if i >= 9 else 10)
            t = str(i + 1)
            ext = cr.text_extents(t)
            cr.move_to(nx - ext.width / 2 - ext.x_bearing, ny + ext.height / 2)
            cr.show_text(t)

        # info readout
        info_i = self.hover if self.hover is not None else sel
        if info_i is not None and info_i < len(bands):
            b = bands[info_i]
            label = dict(TYPES).get(b["type"], b["type"])
            txt = f"Band {info_i + 1}  ·  {label}  ·  {fmt_freq(b['frequency'])}"
            if b["type"] not in NO_GAIN:
                txt += f"  ·  {b['gain']:+.1f} dB"
            txt += f"  ·  Q {b['q']:.2f}"
        else:
            txt = f"Peak level {max(total):+.1f} dB  ·  drag a dot to move it, scroll on it to change Q"
        cr.set_font_size(11)
        cr.set_source_rgba(1, 1, 1, 0.75)
        cr.move_to(x0 + 10, y0 + 16)
        cr.show_text(txt)


def fmt_freq(f):
    return f"{f / 1000:.2f} kHz".replace(".00 ", " ") if f >= 1000 else f"{f:.0f} Hz"


# ------------------------------------------------------------- band widgets
def fmt_short(f):
    if f >= 1000:
        return f"{f / 1000:.1f}".rstrip("0").rstrip(".") + "k"
    return f"{f:.0f}"


# Filter-shape icons, drawn in a 20x18 box.
TYPE_PATHS = {
    "Bell": [("M", 2, 12), ("C", 7, 12, 7, 4, 10, 4), ("C", 13, 4, 13, 12, 18, 12)],
    "Lo-shelf": [("M", 2, 6), ("L", 7, 6), ("C", 10, 6, 10, 12, 13, 12), ("L", 18, 12)],
    "Hi-shelf": [("M", 2, 12), ("L", 7, 12), ("C", 10, 12, 10, 6, 13, 6), ("L", 18, 6)],
    "Lo-pass": [("M", 2, 7), ("L", 10, 7), ("C", 14, 7, 15, 10, 17, 15)],
    "Hi-pass": [("M", 3, 15), ("C", 5, 10, 6, 7, 10, 7), ("L", 18, 7)],
    "Notch": [("M", 2, 6), ("L", 7, 6), ("C", 9, 6, 9, 15, 10, 15),
              ("C", 11, 15, 11, 6, 13, 6), ("L", 18, 6)],
    "Bandpass": [("M", 2, 15), ("C", 6, 15, 7, 6, 10, 6), ("C", 13, 6, 14, 15, 18, 15)],
}


class TypeIcon(Gtk.DrawingArea):
    def __init__(self, btype, w=20, h=18, color=None):
        super().__init__()
        self.btype = btype
        self.color = color
        self.set_content_width(w)
        self.set_content_height(h)
        self.set_draw_func(self.draw)

    def set_type(self, btype):
        if btype != self.btype:
            self.btype = btype
            self.queue_draw()

    def draw(self, _a, cr, w, h):
        if self.color:
            c = Gdk.RGBA()
            c.parse(self.color)
        else:
            c = self.get_color()
        cr.set_source_rgba(c.red, c.green, c.blue, c.alpha)
        cr.scale(w / 20, h / 18)
        cr.set_line_width(1.6 * 20 / w if w > 20 else 1.6)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        for op in TYPE_PATHS.get(self.btype, TYPE_PATHS["Bell"]):
            {"M": cr.move_to, "L": cr.line_to, "C": cr.curve_to}[op[0]](*op[1:])
        cr.stroke()


def parse_num(txt):
    """'4.85k', '4850 Hz', '−3 dB' -> float (ValueError if not a number)."""
    t = txt.strip().lower().replace("−", "-").replace("hz", "").replace("db", "").strip()
    mult = 1.0
    if t.endswith("k"):
        mult, t = 1000.0, t[:-1]
    return float(t) * mult


DOT_H, SLIDER_H, CELL_H, COL_GAP = 26, 140, 28, 4


class BandColumn(Gtk.Box):
    """One band in the strip, Peace style: slider, then Gain / Freq / Q / Filter."""

    def __init__(self, win, index):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=COL_GAP)
        self.win = win
        self.index = index
        self._updating = False
        self.add_css_class("band-col")
        self.set_size_request(56, -1)
        self.set_hexpand(True)

        dot = Gtk.Label(label=str(index + 1), halign=Gtk.Align.CENTER)
        dot.set_size_request(-1, DOT_H)
        dot.add_css_class("band-dot")
        dot.add_css_class(f"band-c{index % len(COLORS)}")
        self.append(dot)

        self.freq_e = self._cell("Frequency in Hz (4850 or 4.85k) — press Enter", self.on_freq)

        # no marks: GTK draws a pointed knob when a scale has marks
        self.scale = Gtk.Scale.new_with_range(Gtk.Orientation.VERTICAL, -DB_RANGE, DB_RANGE, 0.1)
        self.scale.set_inverted(True)
        self.scale.set_draw_value(False)
        self.scale.set_size_request(-1, SLIDER_H)
        self.scale.set_halign(Gtk.Align.CENTER)
        self.scale.add_css_class(f"bandscale-c{index % len(COLORS)}")
        # display only: follows the Gain cell, can't be dragged or keyed
        self.scale.set_can_target(False)
        self.scale.set_can_focus(False)
        self.append(self.scale)

        self.gain_e = self._cell("Gain in dB — type a value and press Enter", self.on_gain)
        self.q_e = self._cell("Q (quality): higher = narrower — press Enter", self.on_q)

        self.filter_icon = TypeIcon("Bell")
        self.filter_btn = Gtk.MenuButton(halign=Gtk.Align.CENTER)
        self.filter_btn.set_child(self.filter_icon)
        self.filter_btn.set_size_request(-1, CELL_H)
        self.filter_btn.add_css_class("filter-btn")
        pop = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        for tid, lab in TYPES:
            b = Gtk.Button()
            b.add_css_class("flat")
            row = Gtk.Box(spacing=10)
            row.append(TypeIcon(tid))
            row.append(Gtk.Label(label=lab, xalign=0))
            b.set_child(row)
            b.connect("clicked", self.on_filter, tid, pop)
            box.append(b)
        pop.set_child(box)
        self.filter_btn.set_popover(pop)
        self.append(self.filter_btn)

        # capture phase: select the band without stealing clicks from its controls
        click = Gtk.GestureClick()
        click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        click.connect("pressed", lambda *_: self.win.select_band(self.index))
        self.add_controller(click)

    def _cell(self, tip, commit):
        e = Gtk.Entry(width_chars=3, max_width_chars=6, xalign=0.5, tooltip_text=tip)
        e.set_size_request(-1, CELL_H)
        e.add_css_class("cell")
        e.connect("activate", lambda *_: commit())
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", lambda *_: commit())
        e.add_controller(focus)
        self.append(e)
        return e

    @staticmethod
    def _set(entry, text):
        if entry.get_text() != text:
            entry.set_text(text)

    def load(self, band):
        self._updating = True
        try:
            nog = band["type"] in NO_GAIN
            self.scale.set_sensitive(not nog)
            self.gain_e.set_sensitive(not nog)
            g = 0.0 if nog else band["gain"]
            if abs(self.scale.get_value() - g) > 1e-6:
                self.scale.set_value(g)
            self._set(self.gain_e, "—" if nog else
                      ("0.0" if abs(g) < 0.05 else f"{g:+.1f}"))
            self._set(self.freq_e, f"{band['frequency']:.0f}")
            self._set(self.q_e, f"{band['q']:.2f}")
            self.filter_icon.set_type(band["type"])
            name = dict(TYPES).get(band["type"], band["type"])
            self.filter_btn.set_tooltip_text(f"Filter: {name} — click to change")
            self.set_tooltip_text(f"Band {self.index + 1} · {name} · "
                                  f"{fmt_freq(band['frequency'])} · Q {band['q']:.2f}")
        finally:
            self._updating = False

    def set_dim(self, dim):
        (self.add_css_class if dim else self.remove_css_class)("dim")

    def _band(self):
        return dict(self.win.bands[self.index]) if self.index < len(self.win.bands) else None

    def _apply(self, key, entry, lo, hi, digits):
        band = self._band()
        if band is None or self._updating:
            return
        try:
            v = round(min(max(parse_num(entry.get_text()), lo), hi), digits)
        except ValueError:
            self.load(band)  # not a number: put the real value back
            return
        if abs(v - band[key]) < 1e-9:
            self.load(band)  # normalise the text
            return
        band[key] = v
        self.win.update_band(self.index, band)

    def on_gain(self):
        band = self._band()
        if band and band["type"] not in NO_GAIN:
            self._apply("gain", self.gain_e, -30, 30, 1)

    def on_freq(self):
        self._apply("frequency", self.freq_e, 10, 22000, 0)

    def on_q(self):
        self._apply("q", self.q_e, 0.1, 20, 2)

    def on_filter(self, _b, tid, pop):
        pop.popdown()
        band = self._band()
        if band is None or band["type"] == tid:
            return
        band["type"] = tid
        self.win.select_band(self.index)
        self.win.update_band(self.index, band)


class BandInspector(Gtk.Box):
    """Actions for the selected band, shown in the BANDS header: Solo, Mute, Remove."""

    def __init__(self, win):
        super().__init__(spacing=6, valign=Gtk.Align.CENTER)
        self.win = win
        self.index = None
        self._updating = False
        self._dot_class = None
        self.add_css_class("band-actions")

        self.dot = Gtk.Label(valign=Gtk.Align.CENTER)
        self.dot.add_css_class("band-dot")
        self.append(self.dot)
        self.title = Gtk.Label(xalign=0)
        self.title.add_css_class("inspector-title")
        self.title.set_margin_end(4)
        self.append(self.title)

        self.solo = Gtk.ToggleButton(label="Solo",
                                     tooltip_text="Hear only this band (for listening, never saved)")
        self.solo.connect("toggled", self.on_flag, "solo")
        self.append(self.solo)
        self.mute = Gtk.ToggleButton(label="Mute",
                                     tooltip_text="Switch this band off for a moment (never saved)")
        self.mute.connect("toggled", self.on_flag, "mute")
        self.append(self.mute)

        self.rm = Gtk.Button(icon_name="user-trash-symbolic",
                             tooltip_text="Remove this band (only this band, not the preset)")
        self.rm.add_css_class("flat")
        self.rm.connect("clicked", lambda *_: self.index is not None and self.win.remove_band(self.index))
        self.append(self.rm)

    def load(self, i):
        self.index = i
        none = i is None or i >= len(self.win.bands)
        self.dot.set_visible(not none)
        for w in (self.solo, self.mute, self.rm):
            w.set_sensitive(not none)
        if none:
            self.index = None
            self.title.set_label("Click a band to select it")
            self.title.add_css_class("dim-label")
            self._updating = True
            self.solo.set_active(False)
            self.mute.set_active(False)
            self._updating = False
            return
        self.title.remove_css_class("dim-label")
        self._updating = True
        try:
            cls = f"band-c{i % len(COLORS)}"
            if self._dot_class != cls:
                if self._dot_class:
                    self.dot.remove_css_class(self._dot_class)
                self.dot.add_css_class(cls)
                self._dot_class = cls
            self.dot.set_label(str(i + 1))
            self.title.set_label(f"Band {i + 1}")
            self.solo.set_active(i in self.win.soloed)
            self.mute.set_active(i in self.win.muted)
        finally:
            self._updating = False

    def on_flag(self, btn, key):
        if self._updating or self.index is None:
            return
        self.win.set_flag(self.index, key, btn.get_active())


# ------------------------------------------------------------ clip detection
def output_sink(ee):
    """The real device EasyEffects plays into (where clipping would happen)."""
    dev, use_default = ee.output_device()
    if use_default:
        try:
            d = subprocess.run(["pactl", "get-default-sink"], capture_output=True,
                               text=True, timeout=2).stdout.strip()
            if d and d != "easyeffects_sink":
                dev = d
        except (OSError, subprocess.SubprocessError):
            pass
    return dev


class ClipMonitor:
    """Reads the float signal going to the output device and reports levels.

    PipeWire keeps samples as floats up to the device, so anything above 1.0
    (0 dBFS) is audio that will be clipped when it's converted for the DAC.
    """
    RATE, BLOCK = 48000, 1200  # 25 ms blocks

    def __init__(self, callback):
        self.callback = callback
        self.proc = None
        self.target = None
        self.stopped = False

    def start(self, target):
        self.stop_proc()
        self.target = target
        self.stopped = False
        if not target:
            return
        props = ('{ stream.capture.sink=true application.name="EQ clip meter" '
                 'media.name="EQ clip meter" node.dont-reconnect=true }')
        try:
            self.proc = subprocess.Popen(
                ["pw-cat", "--record", "--target", target, "-P", props,
                 "--format", "f32", "--rate", str(self.RATE), "--channels", "2",
                 "--latency", str(self.BLOCK), "-"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except OSError:
            self.proc = None
            return
        import threading
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()

    def _read(self, proc):
        nbytes = self.BLOCK * 2 * 4
        while True:
            buf = proc.stdout.read(nbytes)
            if not buf or len(buf) < 8:
                break
            a = np.frombuffer(buf[: len(buf) // 8 * 8], dtype="<f4").reshape(-1, 2)
            a = np.abs(a)
            peaks = a.max(axis=0)
            over = int((a > 1.0).any(axis=1).sum())
            GLib.idle_add(self.callback, float(peaks[0]), float(peaks[1]), over)
        # restart if the stream died on its own (device change, PipeWire restart)
        if not self.stopped and proc is self.proc:
            GLib.timeout_add(2000, lambda: (self.start(self.target), False)[1]
                             if proc is self.proc and not self.stopped else False)

    def stop_proc(self):
        if self.proc:
            p, self.proc = self.proc, None
            p.terminate()
            try:
                p.wait(timeout=1)
            except subprocess.TimeoutExpired:
                p.kill()

    def stop(self):
        self.stopped = True
        self.stop_proc()


def to_db(v):
    return 20 * math.log10(v) if v > 1e-6 else -120.0


class LevelMeter(Gtk.DrawingArea):
    FLOOR = -48.0

    def __init__(self):
        super().__init__()
        self.set_content_height(26)
        self.set_hexpand(True)
        self.level = [self.FLOOR, self.FLOOR]
        self.hold = [self.FLOOR, self.FLOOR]
        self.set_draw_func(self.draw)

    def push(self, l_db, r_db):
        for i, v in enumerate((l_db, r_db)):
            # fast attack, smooth fall
            self.level[i] = v if v > self.level[i] else max(v, self.level[i] - 1.2)
            self.hold[i] = v if v > self.hold[i] else max(self.hold[i] - 0.25, self.FLOOR)
        self.queue_draw()

    def x_for(self, db, w):
        t = (min(max(db, self.FLOOR), 6) - self.FLOOR) / (6 - self.FLOOR)
        return t * w

    def draw(self, _a, cr, w, h):
        bar_h = (h - 4) / 2
        zero_x = self.x_for(0, w)
        for i in range(2):
            y = i * (bar_h + 4)
            cr.set_source_rgba(1, 1, 1, 0.06)
            cr.rectangle(0, y, w, bar_h)
            cr.fill()
            lx = self.x_for(self.level[i], w)
            grad = cairo.LinearGradient(0, 0, w, 0)
            grad.add_color_stop_rgb(0, 0.18, 0.76, 0.49)
            grad.add_color_stop_rgb(self.x_for(-12, w) / w, 0.34, 0.89, 0.54)
            grad.add_color_stop_rgb(self.x_for(-3, w) / w, 0.97, 0.89, 0.36)
            grad.add_color_stop_rgb(zero_x / w, 1.0, 0.64, 0.28)
            grad.add_color_stop_rgb(min(zero_x / w + 0.001, 1), 0.96, 0.25, 0.25)
            grad.add_color_stop_rgb(1, 0.96, 0.25, 0.25)
            cr.set_source(grad)
            cr.rectangle(0, y, lx, bar_h)
            cr.fill()
            hx = self.x_for(self.hold[i], w)
            if self.hold[i] > self.FLOOR:
                cr.set_source_rgba(1, 1, 1, 0.8)
                cr.rectangle(hx - 1, y, 2, bar_h)
                cr.fill()
        # 0 dB line
        cr.set_source_rgba(1, 1, 1, 0.5)
        cr.rectangle(zero_x - 0.5, 0, 1, h)
        cr.fill()


# ----------------------------------------------------------------- the window
class EQWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Ferospark Equalizer")
        self.add_css_class("eqpanel")
        self.set_default_size(1180, 840)
        self.ee = BACKEND
        self.bands = self.ee.bands()
        self.preamp = self.ee.preamp()
        self.power_on = self.ee.powered()
        self.selected = None
        self.cols = []
        self.muted, self.soloed = set(), set()
        self._pending = {}
        self._flush_id = 0
        self._refresh_id = 0
        self._preset_guard = False

        self.toasts = Adw.ToastOverlay()
        view = Adw.ToolbarView()
        self.toasts.set_child(view)
        self.set_content(self.toasts)

        # header
        header = Adw.HeaderBar()
        self.preset_list = Gtk.StringList()
        self.preset_dd = Gtk.DropDown(model=self.preset_list)
        self.preset_dd.set_size_request(240, -1)
        self.preset_dd.set_tooltip_text("Choose a preset")
        self.preset_dd.connect("notify::selected", self.on_preset_selected)
        header.pack_start(self.preset_dd)

        save = Gtk.Button(label="Save")
        save.add_css_class("suggested-action")
        save.set_tooltip_text("Save changes into the current preset")
        save.connect("clicked", self.on_save)
        header.pack_start(save)

        new = Gtk.Button()
        new.set_child(Adw.ButtonContent(icon_name="document-new-symbolic", label="New"))
        new.set_tooltip_text("Create a new EQ preset")
        new.connect("clicked", lambda *_: self.on_new_preset())
        header.pack_start(new)

        menu = Gio.Menu()
        menu.append("New preset…", "win.new-preset")
        menu.append("Save as new preset…", "win.save-as")
        menu.append("Delete this preset…", "win.delete-preset")
        menu.append("Flatten all bands", "win.flatten")
        menu.append("Auto pre-amp (prevent clipping)", "win.auto-preamp")
        menu.append("Open EasyEffects (advanced)", "win.open-ee")
        menu.append("Donate…", "win.donate")
        mb = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu)
        header.pack_end(mb)

        donate = Gtk.Button(icon_name="emblem-favorite-symbolic")
        donate.add_css_class("donate-btn")
        donate.set_tooltip_text("Donate — support development")
        donate.connect("clicked", lambda *_: self.on_donate())
        header.pack_end(donate)
        view.add_top_bar(header)

        self._action("save-as", lambda *_: self.on_save_as())
        self._action("new-preset", lambda *_: self.on_new_preset())
        self._action("donate", lambda *_: self.on_donate())
        self._action("delete-preset", lambda *_: self.on_delete_preset())
        self._action("flatten", lambda *_: self.on_flatten())
        self._action("auto-preamp", lambda *_: self.on_auto_preamp())
        self._action("open-ee", lambda *_: self.ee.open_gui())

        # body
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.scroller = scroller
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        body.set_margin_top(18)
        body.set_margin_bottom(24)
        body.set_margin_start(24)
        body.set_margin_end(24)
        # use the whole window width (a narrower clamp cut off the last band)
        clamp = Adw.Clamp(maximum_size=1800, tightening_threshold=1800, child=body)
        scroller.set_child(clamp)
        view.set_content(scroller)

        self.banner = Adw.Banner()
        self.banner.connect("button-clicked", self.on_banner)
        body.append(self.banner)

        # top strip: power + output meter
        top = Gtk.Box(spacing=20)
        self.power = Gtk.Button(icon_name="system-shutdown-symbolic")
        self.power.add_css_class("power")
        self.power.set_valign(Gtk.Align.CENTER)
        self.power.connect("clicked", self.on_power)
        pbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        pbox.append(self.power)
        self.state_label = Gtk.Label()
        self.state_label.add_css_class("state-label")
        pbox.append(self.state_label)
        top.append(pbox)

        # clip detector
        mcol = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
        mcol.set_valign(Gtk.Align.CENTER)
        mhead = Gtk.Box(spacing=8)
        t = Gtk.Label(label="OUTPUT LEVEL", xalign=0)
        t.add_css_class("section-title")
        mhead.append(t)
        self.level_label = Gtk.Label(xalign=0, hexpand=True)
        self.level_label.add_css_class("hint")
        mhead.append(self.level_label)
        mcol.append(mhead)
        self.meter = LevelMeter()
        mcol.append(self.meter)
        top.append(mcol)

        self.clip_btn = Gtk.Button()
        self.clip_btn.add_css_class("clip-light")
        self.clip_btn.set_valign(Gtk.Align.CENTER)
        self.clip_btn.set_tooltip_text("Lights red when the sound goes over 0 dB (clipping). Click to reset.")
        self.clip_btn.connect("clicked", lambda *_: self.reset_clip())
        top.append(self.clip_btn)

        self.fix_btn = Gtk.Button(label="Fix")
        self.fix_btn.add_css_class("destructive-action")
        self.fix_btn.set_valign(Gtk.Align.CENTER)
        self.fix_btn.connect("clicked", lambda *_: self.fix_clip())
        top.append(self.fix_btn)
        body.append(top)

        self.clip_count = 0
        self.max_peak = 0.0
        self.last_clip = 0
        self.last_level_text = 0
        self.monitor = ClipMonitor(self.on_levels)
        self.monitor.start(output_sink(self.ee))
        self.connect("close-request", self.on_close)
        self.reset_clip()

        # graph
        card = Gtk.Frame()
        card.add_css_class("graph-card")
        self.graph = ResponseGraph(self)
        card.set_child(self.graph)
        body.append(card)

        # bands header
        bh = Gtk.Box(spacing=12)
        t = Gtk.Label(label="BANDS", xalign=0)
        t.add_css_class("section-title")
        bh.append(t)
        self.clip_label = Gtk.Label(xalign=0, hexpand=True)
        self.clip_label.add_css_class("hint")
        self.clip_label.set_ellipsize(Pango.EllipsizeMode.END)  # header width never depends on text
        bh.append(self.clip_label)
        self.inspector = BandInspector(self)  # Solo / Mute / Remove for the selected band
        bh.append(self.inspector)
        bh.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        self.add_btn = Gtk.Button()
        self.add_btn.set_child(Adw.ButtonContent(icon_name="list-add-symbolic", label="Add band"))
        self.add_btn.connect("clicked", lambda *_: self.add_band())
        bh.append(self.add_btn)
        body.append(bh)

        # band strip: pre-amp column + one gain slider per band
        strip = Gtk.Box(spacing=8)
        strip.add_css_class("strip-card")
        pcol = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        pcol.set_size_request(74, -1)
        t = Gtk.Label(label="PRE-AMP")
        t.add_css_class("section-title")
        pcol.append(t)
        self.preamp_scale = Gtk.Scale.new_with_range(Gtk.Orientation.VERTICAL, -30, 10, 0.1)
        self.preamp_scale.set_inverted(True)
        self.preamp_scale.set_draw_value(False)
        self.preamp_scale.set_size_request(-1, 118)
        self.preamp_scale.set_halign(Gtk.Align.CENTER)
        self.preamp_scale.set_tooltip_text("Pre-amp: lowers everything before the EQ so boosts don't clip")
        self.preamp_scale.connect("value-changed", self.on_preamp_scale)
        self.no_wheel(self.preamp_scale)
        pcol.append(self.preamp_scale)
        self.preamp_entry = Gtk.Entry(width_chars=5, max_width_chars=6, xalign=0.5)
        self.preamp_entry.add_css_class("preamp-entry")
        self.preamp_entry.set_tooltip_text("Type a pre-amp value in dB and press Enter")
        self.preamp_entry.connect("activate", self.on_preamp_entry)
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", lambda *_: self.on_preamp_entry(self.preamp_entry))
        self.preamp_entry.add_controller(focus)
        pcol.append(self.preamp_entry)
        autob = Gtk.Button(label="Auto")
        autob.set_tooltip_text("Lower the pre-amp just enough to prevent clipping")
        autob.connect("clicked", lambda *_: self.on_auto_preamp())
        pcol.append(autob)
        strip.append(pcol)
        strip.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=COL_GAP)
        labels.set_margin_top(4)  # matches .band-col top padding
        def gap(h):
            g = Gtk.Box()
            g.set_size_request(-1, h)
            labels.append(g)

        def label(name):
            lab = Gtk.Label(label=name, xalign=1)
            lab.set_size_request(-1, CELL_H)
            lab.add_css_class("row-label")
            labels.append(lab)

        gap(DOT_H)
        label("Freq")
        gap(SLIDER_H)
        for name in ("Gain", "Q", "Filter"):
            label(name)
        strip.append(labels)
        self.cols_box = Gtk.Box(spacing=2)
        sw = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                                vscrollbar_policy=Gtk.PolicyType.NEVER, hexpand=True)
        sw.set_child(self.cols_box)
        strip.append(sw)
        body.append(strip)


        # external changes (preset loaded elsewhere, EasyEffects UI, etc.)
        self.ee.watch(self.on_settings_changed)

        # pick up presets added or removed on disk
        self.preset_monitor = Gio.File.new_for_path(PRESET_DIR).monitor_directory(
            Gio.FileMonitorFlags.NONE, None)
        self.preset_monitor.connect(
            "changed", lambda *_: GLib.timeout_add(300, lambda: (self.refresh_presets(), False)[1]))

        if self.ee.version == 8 and not self.ee.running():
            tries = {"n": 0}

            def wait_ee():
                tries["n"] += 1
                if self.ee.running():
                    self.clear_flags()
                    self._refresh()
                    self.refresh_presets()
                    return False
                return tries["n"] < 20

            GLib.timeout_add(500, wait_ee)

        self.clear_flags()  # stale solo/mute from an earlier session
        self.rebuild_rows()
        self.refresh_presets()
        self.refresh_power()
        self.refresh_preamp()
        self.refresh_banner()

    def _action(self, name, cb):
        a = Gio.SimpleAction.new(name, None)
        a.connect("activate", cb)
        self.add_action(a)

    def no_wheel(self, widget):
        """Mouse wheel scrolls the page instead of silently changing a value."""
        wheel = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        wheel.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        wheel.connect("scroll", lambda _c, _dx, dy: self.scroll_page(dy))
        widget.add_controller(wheel)

    def on_close(self, *_):
        self.monitor.stop()
        self.clear_flags()
        Gio.Settings.sync()
        return False

    # --- solo / mute (listening aids, never saved into presets)
    def audible(self, i):
        if self.soloed:
            return i in self.soloed
        return i not in self.muted

    def set_flag(self, i, key, on):
        s = self.muted if key == "mute" else self.soloed
        (s.add if on else s.discard)(i)
        self.ee.set_band_flag(i, key, on)
        self.refresh_dims()
        self.graph.queue_draw()

    def clear_flags(self):
        muted, soloed = self.ee.band_flags()
        for i in muted:
            self.ee.set_band_flag(i, "mute", False)
        for i in soloed:
            self.ee.set_band_flag(i, "solo", False)
        self.muted, self.soloed = set(), set()

    def refresh_dims(self):
        for k, c in enumerate(self.cols):
            c.set_dim(not self.audible(k))

    def toast(self, msg):
        self.toasts.add_toast(Adw.Toast(title=msg, timeout=2))

    # --- model changes from the UI
    def update_band(self, i, band, source=None):
        self.bands[i] = band
        self._pending[i] = band
        if not self._flush_id:
            self._flush_id = GLib.timeout_add(35, self._flush)
        if i < len(self.cols) and source is not self.cols[i]:
            self.cols[i].load(band)
        if i == self.selected and source is not self.inspector:
            self.inspector.load(i)
        self.graph.queue_draw()
        self.refresh_clip()

    def _flush(self):
        pending, self._pending = self._pending, {}
        for i, b in pending.items():
            if i < self.ee.num_bands():
                self.ee.write_band(i, b)
        self._flush_id = 0
        return False

    def add_band(self):
        if len(self.bands) >= MAX_BANDS:
            self.toast(f"EasyEffects supports up to {MAX_BANDS} bands")
            return
        self._flush()
        new = {"type": "Bell", "frequency": 1000.0, "gain": 0.0, "q": 1.0}
        self.bands.append(new)
        self.ee.write_band(len(self.bands) - 1, new, full=True)
        self.ee.set_num_bands(len(self.bands))
        self.rebuild_rows()
        self.select_band(len(self.bands) - 1)

    def remove_band(self, i):
        if len(self.bands) <= 1:
            self.toast("Keep at least one band — use Flatten or the power button instead")
            return
        self._flush()
        del self.bands[i]
        self.ee.write_all(self.bands)  # also clears every solo/mute
        self.muted, self.soloed = set(), set()
        self.selected = None
        self.rebuild_rows()

    def select_band(self, i):
        if i == self.selected and self.inspector.index == i:
            return
        self.selected = i
        for k, c in enumerate(self.cols):
            (c.add_css_class if k == i else c.remove_css_class)("selected")
        self.inspector.load(i)
        self.graph.queue_draw()

    # --- clip detector
    def on_levels(self, l, r, over):
        peak = max(l, r)
        self.meter.push(to_db(l), to_db(r))
        self.max_peak = max(self.max_peak, peak)
        now = GLib.get_monotonic_time()
        if over:
            self.clip_count += over
            self.last_clip = now
            self.update_clip_light()
        elif self.clip_count and now - self.last_clip > 1_500_000:
            self.clip_btn.remove_css_class("flash")
        if now - self.last_level_text > 250_000:
            self.last_level_text = now
            self.level_label.set_label(
                ("silent" if peak < 1e-4 else f"now {to_db(peak):.1f} dB")
                + f"  ·  loudest since reset {to_db(self.max_peak):+.1f} dB"
                if self.max_peak > 1e-4 else "silent — play something to measure")
        return False

    def update_clip_light(self):
        if self.clip_count:
            self.clip_btn.set_label(f"CLIP  ×{self.clip_count:,}")
            self.clip_btn.add_css_class("clipping")
            self.clip_btn.add_css_class("flash")
            over_db = to_db(self.max_peak)
            self.fix_btn.set_tooltip_text(f"Lower the pre-amp by {over_db + 0.2:.1f} dB")
            self.fix_btn.set_visible(True)
        else:
            self.clip_btn.set_label("NO CLIPPING")
            self.clip_btn.remove_css_class("clipping")
            self.clip_btn.remove_css_class("flash")
            self.fix_btn.set_visible(False)

    def reset_clip(self):
        self.clip_count = 0
        self.max_peak = 0.0
        self.update_clip_light()

    def fix_clip(self):
        drop = round(to_db(self.max_peak) + 0.2, 1)
        if drop > 0:
            self.preamp_scale.set_value(max(self.preamp - drop, -30))
            self.toast(f"Pre-amp lowered by {drop:.1f} dB — press Save to keep it")
        self.reset_clip()

    def scroll_page(self, dy):
        adj = self.scroller.get_vadjustment()
        adj.set_value(adj.get_value() + dy * 60)
        return True

    def on_preamp_scale(self, scale):
        self.set_preamp_value(round(scale.get_value(), 1))

    def on_preamp_entry(self, entry):
        txt = entry.get_text().replace("dB", "").replace("−", "-").strip()
        try:
            v = round(max(min(float(txt), 10.0), -30.0), 1)
        except ValueError:
            self.refresh_preamp()
            return
        self.preamp_scale.set_value(v)
        self.set_preamp_value(v)

    def set_preamp_value(self, v):
        if abs(v - self.preamp) >= 1e-6:
            self.preamp = v
            self.ee.set_preamp(v)
            self.graph.queue_draw()
            self.refresh_clip()
        self.preamp_entry.set_text(f"{v:+.1f}")

    def peak_without_preamp(self):
        return float(max(sum((band_response(b) for b in self.bands), np.zeros_like(FREQS))))

    def on_auto_preamp(self):
        v = round(-max(self.peak_without_preamp(), 0.0) - 0.1, 1)
        self.preamp_scale.set_value(v)
        self.toast(f"Pre-amp set to {v:+.1f} dB")

    def on_flatten(self):
        for i, b in enumerate(self.bands):
            if b["type"] not in NO_GAIN:
                self.update_band(i, dict(b, gain=0.0))
        self.toast("All gains set to 0 dB (not saved yet)")

    def on_power(self, *_):
        self.power_on = not self.power_on
        self.ee.set_power(self.power_on)
        save_power(self.power_on)
        self.refresh_power()

    # --- presets
    def refresh_presets(self):
        self._preset_guard = True
        try:
            names = list_presets()
            self.preset_list.splice(0, self.preset_list.get_n_items(), names)
            cur = self.ee.current_preset()
            self.preset_dd.set_selected(names.index(cur) if cur in names else Gtk.INVALID_LIST_POSITION)
        finally:
            self._preset_guard = False

    def on_preset_selected(self, dd, _p):
        if self._preset_guard:
            return
        item = dd.get_selected_item()
        if item is None:
            return
        name = item.get_string()
        if name == self.ee.current_preset():
            return
        self.ee.load_preset(name)
        self.toast(f"Loaded “{name}”")
        GLib.timeout_add(800, self._reapply_power)

    def _reapply_power(self):
        want = saved_power()  # not self.power_on: a refresh may already show the preset's state
        if self.ee.powered() != want:
            self.ee.set_power(want)
        self.power_on = want
        self.refresh_power()
        return False

    def on_save(self, *_):
        name = self.ee.current_preset()
        if not name:
            self.on_save_as()
            return
        self._flush()
        self.ee.save_preset(name)
        self.toast(f"Saved “{name}”")

    def on_new_preset(self):
        dlg = Adw.AlertDialog.new("New preset", "Give it a name and choose how to start.")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        entry = Gtk.Entry(placeholder_text="Preset name", activates_default=True)
        box.append(entry)
        flat = Gtk.CheckButton(label="Start flat: 10 bands at 0 dB (31 Hz to 16 kHz)")
        copy = Gtk.CheckButton(label="Start from a copy of the current sound")
        copy.set_group(flat)
        flat.set_active(True)
        box.append(flat)
        box.append(copy)
        warn = Gtk.Label(xalign=0)
        warn.add_css_class("hint")
        box.append(warn)
        dlg.set_extra_child(box)
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("create", "Create")
        dlg.set_response_appearance("create", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("create")
        dlg.set_close_response("cancel")
        dlg.set_response_enabled("create", False)

        def check(*_):
            name = clean_name(entry.get_text())
            taken = name.lower() in (n.lower() for n in list_presets())
            warn.set_label("A preset with that name already exists" if taken else "")
            dlg.set_response_enabled("create", bool(name) and not taken)

        entry.connect("changed", check)

        def done(_d, resp):
            name = clean_name(entry.get_text())
            if resp != "create" or not name:
                return
            self._flush()
            if flat.get_active():
                bands = [{"type": "Bell", "frequency": float(f), "gain": 0.0, "q": 1.41}
                         for f in (31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)]
                self.clear_flags()
                self.ee.write_all(bands)
                self.ee.set_preamp(0.0)
                self.bands = bands
                self.preamp = 0.0
                self.selected = None
                self.rebuild_rows()
                self.refresh_preamp()
                self.graph.queue_draw()
            self.ee.save_preset(name)
            self.refresh_presets()
            self.toast(f"Created “{name}”")

        dlg.connect("response", done)
        dlg.present(self)
        entry.grab_focus()

    def on_donate(self):
        dlg = Adw.Dialog(title="Support Development", content_width=400)
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar(show_title=False))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_start(32)
        box.set_margin_end(32)
        box.set_margin_bottom(28)
        title = Gtk.Label(label="Support Development")
        title.add_css_class("title-2")
        box.append(title)
        sub = Gtk.Label(label="Ferospark Equalizer is free. If it helps you, "
                              "you can say thanks with Binance Pay.",
                        wrap=True, justify=Gtk.Justification.CENTER)
        sub.add_css_class("dim-label")
        sub.set_margin_bottom(12)
        box.append(sub)
        if os.path.exists(DONATE_QR):
            pic = Gtk.Picture.new_for_filename(DONATE_QR)
            pic.set_can_shrink(True)
            pic.set_size_request(260, 260)
            pic.set_halign(Gtk.Align.CENTER)
            pic.add_css_class("donate-qr")
            box.append(pic)
        cap = Gtk.Label(label="Binance Pay ID")
        cap.add_css_class("dim-label")
        cap.set_margin_top(12)
        box.append(cap)
        pid = Gtk.Label(label=BINANCE_PAY_ID)
        pid.add_css_class("donate-id")
        box.append(pid)
        copy = Gtk.Button(label="Copy ID", halign=Gtk.Align.CENTER)
        copy.add_css_class("pill")
        copy.set_margin_top(10)

        def do_copy(*_):
            self.get_clipboard().set(BINANCE_PAY_ID)
            copy.set_label("Copied ✓")

        copy.connect("clicked", do_copy)
        box.append(copy)
        tv.set_content(box)
        dlg.set_child(tv)
        dlg.present(self)

    def on_save_as(self):
        dlg = Adw.AlertDialog.new("Save as new preset", None)
        entry = Gtk.Entry(placeholder_text="Preset name", activates_default=True)
        dlg.set_extra_child(entry)
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("save", "Save")
        dlg.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("save")

        def done(d, resp):
            name = clean_name(entry.get_text())
            if resp != "save" or not name:
                return
            self._flush()
            self.ee.save_preset(name)
            self.refresh_presets()
            self.toast(f"Saved “{name}”")

        dlg.connect("response", done)
        dlg.present(self)

    def on_delete_preset(self):
        item = self.preset_dd.get_selected_item()
        if item is None:
            self.toast("Pick a preset first")
            return
        name = item.get_string()
        dlg = Adw.AlertDialog.new(f"Delete “{name}”?",
                                  "It will be moved to the Trash, so you can restore it from there. "
                                  "The sound you hear right now won't change.")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("delete", "Delete")
        dlg.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_default_response("cancel")
        dlg.set_close_response("cancel")

        def done(_d, resp):
            if resp != "delete":
                return
            f = Gio.File.new_for_path(os.path.join(PRESET_DIR, name + ".json"))
            try:
                f.trash(None)
            except GLib.Error as e:
                self.toast(f"Couldn't delete: {e.message}")
                return
            if self.ee.current_preset() == name:
                self.ee.forget_current_preset()
            self.refresh_presets()
            self.toast(f"Deleted “{name}” (it's in the Trash)")

        dlg.connect("response", done)
        dlg.present(self)

    def on_banner(self, *_):
        if not ee_running():
            start_ee_service()
            enforce_power()
            GLib.timeout_add(1500, lambda: (self.refresh_banner(), False)[1])
        elif not self.ee.eq_in_chain():
            if self.ee.version == 8:
                self.ask_add_eq_ee8()
            else:
                self.ee.add_eq_to_chain()

    def ask_add_eq_ee8(self):
        dlg = Adw.AlertDialog.new(
            "Add the equalizer in EasyEffects",
            "The current EasyEffects setup has no equalizer, and EasyEffects 8 doesn't let other "
            "apps add one. Open EasyEffects, go to Output, press the button to add an effect and "
            "choose Equalizer. Your other effects stay as they are.\n\n"
            "Or start over with a flat equalizer preset instead. That replaces the current "
            "EasyEffects effects.")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("flat", "Use a flat preset")
        dlg.add_response("open", "Open EasyEffects")
        dlg.set_response_appearance("open", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("open")
        dlg.set_close_response("cancel")

        def done(_d, resp):
            if resp == "open":
                self.ee.open_gui()
            elif resp == "flat":
                self.ee.add_eq_to_chain()
                GLib.timeout_add(800, self._reapply_power)

        dlg.connect("response", done)
        dlg.present(self)

    # --- syncing from EasyEffects
    def on_settings_changed(self, *_):
        if not self._refresh_id:
            self._refresh_id = GLib.timeout_add(120, self._refresh)

    def _refresh(self):
        self._refresh_id = 0
        if self._pending:
            return False
        bands = self.ee.bands()
        muted, soloed = self.ee.band_flags()
        if len(bands) != len(self.cols):
            self.bands = bands
            self.muted, self.soloed = muted, soloed
            self.rebuild_rows()
        else:
            for i, b in enumerate(bands):
                if b != self.bands[i]:
                    self.bands[i] = b
                    self.cols[i].load(b)
            if (muted, soloed) != (self.muted, self.soloed):
                self.muted, self.soloed = muted, soloed
                self.refresh_dims()
            if self.selected is not None:
                self.inspector.load(self.selected)
        self.preamp = self.ee.preamp()
        self.power_on = self.ee.powered()
        sink = output_sink(self.ee)
        if sink != self.monitor.target:
            self.monitor.start(sink)
        self.refresh_preamp()
        self.refresh_power()
        self.refresh_banner()
        cur = self.ee.current_preset()
        item = self.preset_dd.get_selected_item()
        if item is None or item.get_string() != cur:
            self.refresh_presets()
        self.graph.queue_draw()
        return False

    def rebuild_rows(self):
        child = self.cols_box.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.cols_box.remove(child)
            child = nxt
        self.cols = []
        for i, b in enumerate(self.bands):
            col = BandColumn(self, i)
            col.load(b)
            self.cols.append(col)
            self.cols_box.append(col)
        self.add_btn.set_sensitive(len(self.bands) < MAX_BANDS)
        sel = self.selected
        if sel is not None and sel >= len(self.bands):
            sel = None
        self.selected = -1  # force a full refresh
        self.select_band(sel)
        self.refresh_dims()
        self.refresh_clip()

    def refresh_power(self):
        if self.power_on:
            self.power.add_css_class("on")
            self.state_label.set_label("ON")
            self.state_label.add_css_class("state-on")
            self.state_label.remove_css_class("state-off")
            self.power.set_tooltip_text("Equalizer is ON — click to turn it off")
        else:
            self.power.remove_css_class("on")
            self.state_label.set_label("OFF")
            self.state_label.add_css_class("state-off")
            self.state_label.remove_css_class("state-on")
            self.power.set_tooltip_text("Equalizer is OFF — click to turn it on")
        self.graph.queue_draw()

    def refresh_preamp(self):
        if abs(self.preamp_scale.get_value() - self.preamp) > 1e-6:
            self.preamp_scale.set_value(self.preamp)
        if not self.preamp_entry.get_state_flags() & Gtk.StateFlags.FOCUS_WITHIN:
            self.preamp_entry.set_text(f"{self.preamp:+.1f}")
        self.refresh_clip()

    def refresh_clip(self):
        peak = self.peak_without_preamp() + self.preamp
        if peak > 0.05:
            self.clip_label.set_label(f"⚠  Loudest point is {peak:+.1f} dB — may distort. Press Auto to fix.")
        else:
            self.clip_label.set_label(f"Loudest point {peak:+.1f} dB — no clipping")

    def refresh_banner(self):
        problem = self.ee.problem()
        if problem:
            self.banner.set_title(problem)
            self.banner.set_button_label(None)
            self.banner.set_revealed(True)
        elif not ee_running():
            self.banner.set_title("EasyEffects isn't running, so the EQ isn't active")
            self.banner.set_button_label("Start it")
            self.banner.set_revealed(True)
        elif not self.ee.eq_in_chain():
            self.banner.set_title("This preset has no equalizer effect")
            self.banner.set_button_label("Add equalizer")
            self.banner.set_revealed(True)
        else:
            self.banner.set_revealed(False)


class EQApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="io.github.matewinslet.FerosparkEqualizer",
                         flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_startup(self):
        Adw.Application.do_startup(self)
        Gtk.Window.set_default_icon_name("io.github.matewinslet.FerosparkEqualizer")
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        prov = Gtk.CssProvider()
        prov.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        if BACKEND is None:
            return
        install_autostart()
        install_bundled_presets()
        migrate_power_mode()
        if not ee_running():
            start_ee_service()
            enforce_power()

    def do_activate(self):
        win = self.get_active_window()
        if win is None:
            win = EQWindow(self) if BACKEND is not None else self.missing_ee_window()
        win.present()

    def missing_ee_window(self):
        win = Adw.ApplicationWindow(application=self, title="Ferospark Equalizer")
        win.set_default_size(560, 460)
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar())
        page = Adw.StatusPage(
            icon_name="audio-speakers-symbolic", title="EasyEffects is needed",
            description="Ferospark Equalizer controls the equalizer inside EasyEffects, "
                        "which does the actual sound processing. Install EasyEffects first "
                        "(for example: sudo apt install easyeffects), then open this app again.")
        btn = Gtk.Button(label="Open install instructions", halign=Gtk.Align.CENTER)
        btn.add_css_class("pill")
        btn.add_css_class("suggested-action")
        btn.connect("clicked", lambda *_: Gtk.UriLauncher.new(
            "https://github.com/matewinslet/ferospark-equalizer#before-you-start-easyeffects-is-required"
        ).launch(win, None, None, None))
        page.set_child(btn)
        tv.set_content(page)
        win.set_content(tv)
        return win


if __name__ == "__main__":
    if "--restore" in sys.argv:
        sys.exit(restore())
    sys.exit(EQApp().run(sys.argv))
