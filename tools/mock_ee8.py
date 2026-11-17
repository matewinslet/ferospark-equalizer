#!/usr/bin/env python3
"""Stand-in for EasyEffects 8.3's local socket server (src/local_server.cpp),
for testing Ferospark Equalizer without running the real EasyEffects 8.
Regexes, reply formats and value types copied from the 8.3.2 source.
Usage: mock_ee8.py SOCKET_PATH CONFIG_DIR PRESET_DIR"""
import json
import os
import re
import socket
import sys
import threading

SOCK, CONF, PRESETS = sys.argv[1:4]
TYPES = ["Off", "Bell", "Hi-pass", "Hi-shelf", "Lo-pass", "Lo-shelf", "Notch",
         "Resonance", "Allpass", "Bandpass", "Ladder-pass", "Ladder-rej"]
MODES = ["RLC (BT)", "RLC (MT)", "BWC (BT)", "BWC (MT)", "LRX (BT)", "LRX (MT)", "APO (DR)"]
SLOPES = ["x1", "x2", "x3", "x4"]
EQMODES = ["IIR", "FIR", "FFT", "SPM", "MP FIR", "MP FFT", "MP SPM"]

lock = threading.Lock()
eq = {"bypass": False, "inputGain": 0.0, "outputGain": 0.0, "numBands": 32, "mode": 0,
      "splitChannels": False, "balance": 0.0, "pitchLeft": 0.0, "pitchRight": 0.0, "decramp": 0}
chans = {}
for ch in ("left", "right"):
    d = {}
    for i in range(32):
        d.update({f"band{i}Gain": 0.0, f"band{i}Frequency": 1000.0, f"band{i}Q": 4.36,
                  f"band{i}Width": 4.0, f"band{i}Type": 1, f"band{i}Mode": 0, f"band{i}Slope": 0,
                  f"band{i}Solo": False, f"band{i}Mute": False})
    chans[ch] = d
state = {"bypass": False, "last": "", "plugins": ["equalizer#0"]}
log = open(os.path.join(os.path.dirname(SOCK), "mock.log"), "a", buffering=1)


def to_str(v):  # QVariant::toString
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return f"{v:g}" if v != int(v) or abs(v) >= 1e16 else str(int(v))
    return str(v)


def write_rc():
    os.makedirs(os.path.join(CONF, "db"), exist_ok=True)
    with open(os.path.join(CONF, "db", "easyeffectsrc"), "w") as fh:
        fh.write("[EffectsPipelines]\nbypass=%s\n\n[Presets]\nlastLoadedOutputPreset=%s\n\n"
                 "[StreamOutputs]\noutputDevice=\nplugins=%s\nuseDefaultOutputDevice=true\n"
                 % (to_str(state["bypass"]), state["last"], ",".join(state["plugins"])))
    with open(os.path.join(CONF, "db", "equalizerrc"), "w") as fh:
        fh.write("[StreamOutputs][Equalizer#0]\n" + "".join(f"{k}={to_str(v)}\n" for k, v in eq.items()))


def db_for(plugin, inst, ch):
    if plugin != "equalizer" or inst != "0":
        return None
    return chans[ch] if ch else eq


def set_value(db, prop, value):
    if prop not in db:
        return
    cur = db[prop]
    if isinstance(cur, bool):
        db[prop] = value in ("true", "1", "on")
    elif isinstance(cur, int):
        try:
            db[prop] = int(value)
        except ValueError:
            pass
    else:
        try:
            db[prop] = float(value)
        except ValueError:
            pass


def load_preset(name):
    path = os.path.join(PRESETS, name + ".json")
    try:
        data = json.load(open(path))["output"]
    except (OSError, ValueError, KeyError):
        return
    state["plugins"] = list(data.get("plugins_order", []))
    state["last"] = name
    e = data.get("equalizer#0")
    if not e:
        return
    eq.update({"inputGain": e["input-gain"], "outputGain": e["output-gain"], "numBands": e["num-bands"],
               "mode": EQMODES.index(e["mode"]), "splitChannels": e["split-channels"],
               "balance": e["balance"], "bypass": e["bypass"]})
    for ch in ("left", "right"):
        for i in range(e["num-bands"]):
            b = e[ch][f"band{i}"]
            chans[ch].update({f"band{i}Gain": b["gain"], f"band{i}Frequency": b["frequency"],
                              f"band{i}Q": b["q"], f"band{i}Width": b["width"],
                              f"band{i}Type": TYPES.index(b["type"]), f"band{i}Mode": MODES.index(b["mode"]),
                              f"band{i}Slope": SLOPES.index(b["slope"]), f"band{i}Solo": b["solo"],
                              f"band{i}Mute": b["mute"]})


SET = re.compile(r"^set_property:(input|output):([^:]+):([0-9]+):(?:(left|right):)?([^:]+):([^\n]+)")
GET = re.compile(r"^get_property:(input|output):([^:]+):([0-9]+):(?:(left|right):)?([^\n]+)")


def handle(conn):
    f = conn.makefile("rb")
    for raw in f:
        line = raw.decode()
        log.write(line if line.endswith("\n") else line + "\n")
        with lock:
            changed = False
            if line.startswith("global_bypass"):
                m = re.search(r"^global_bypass:([01])\n$", line)
                if m:
                    state["bypass"] = m.group(1) != "0"
                    changed = True
            elif line == "get_global_bypass\n":
                conn.sendall(b"1" if state["bypass"] else b"2")
            elif line.startswith("load_preset"):
                m = re.search(r"^load_preset:(input|output):([^\n]{1,100})\n$", line)
                if m:
                    load_preset(m.group(2))
                    changed = True
            elif line.startswith("set_property"):
                m = SET.search(line)
                if m:
                    db = db_for(m.group(2), m.group(3), m.group(4))
                    if db is not None:
                        set_value(db, m.group(5), m.group(6))
                        changed = True
            elif line.startswith("get_property"):
                m = GET.search(line)
                if m:
                    db = db_for(m.group(2), m.group(3), m.group(4))
                    if db is None:
                        out = "error_plugin_not_found"
                    elif m.group(5) not in db:
                        out = "error_property_not_found"
                    else:
                        out = to_str(db[m.group(5)])
                    conn.sendall((out + "\n").encode())
            elif line.startswith("get_last_loaded_preset"):
                if re.search(r"^get_last_loaded_preset:(input|output)\n$", line):
                    conn.sendall((state["last"] + "\n").encode())
            if changed:
                write_rc()
    conn.close()


if os.path.exists(SOCK):
    os.remove(SOCK)
load_preset(sys.argv[4]) if len(sys.argv) > 4 else None
write_rc()
srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
srv.bind(SOCK)
srv.listen(8)
print("mock EasyEffects 8 listening on", SOCK, flush=True)
while True:
    c, _ = srv.accept()
    threading.Thread(target=handle, args=(c,), daemon=True).start()
