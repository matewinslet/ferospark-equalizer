#!/usr/bin/env python3
"""Regenerate data/presets/*.json. The pre-amp of each preset is set from the
real response curve so it never clips. Run from the repo root."""
import importlib.util
import json
import os

spec = importlib.util.spec_from_file_location("fe", "ferospark-equalizer.py")
fe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fe)

B = lambda t, f, g, q: {"type": t, "frequency": float(f), "gain": float(g), "q": float(q)}
PRESETS = {
    "Flat": [B("Bell", f, 0, 1.41) for f in (31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)],
    "Bass Boost": [B("Lo-shelf", 105, 6, 0.7), B("Bell", 55, 1.5, 1.0), B("Bell", 300, -1, 1.0)],
    "Bass Reducer": [B("Lo-shelf", 120, -6, 0.7), B("Bell", 250, -1.5, 1.0)],
    "Treble Boost": [B("Hi-shelf", 6000, 5, 0.7), B("Bell", 3000, 1, 1.0)],
    "Vocal Clarity": [B("Hi-pass", 80, 0, 0.71), B("Bell", 300, -2.5, 1.0),
                      B("Bell", 2500, 3, 1.0), B("Bell", 5000, 1.5, 1.4)],
    "V-Shape": [B("Lo-shelf", 100, 5, 0.7), B("Bell", 1000, -2, 0.8), B("Hi-shelf", 8000, 4, 0.7)],
    "Speech & Podcasts": [B("Hi-pass", 100, 0, 0.71), B("Bell", 200, -2, 1.0),
                          B("Bell", 3000, 4, 1.0), B("Hi-shelf", 10000, -2, 0.7)],
    "Late Night": [B("Lo-shelf", 120, -4, 0.7), B("Bell", 2500, 1.5, 1.0), B("Hi-shelf", 8000, -3, 0.7)],
}


def channel(bands):
    return {f"band{i}": {"frequency": b["frequency"], "gain": b["gain"], "mode": "APO (DR)",
                         "mute": False, "q": b["q"], "slope": "x1", "solo": False,
                         "type": b["type"], "width": 4.0} for i, b in enumerate(bands)}


os.makedirs("data/presets", exist_ok=True)
for name, bands in PRESETS.items():
    peak = float(max(sum((fe.band_response(b) for b in bands), fe.np.zeros_like(fe.FREQS))))
    pre = round(-max(peak, 0.0) - 0.1, 1) if peak > 0.05 else 0.0
    eq = {"balance": 0.0, "bypass": False, "input-gain": pre, "output-gain": 0.0, "mode": "IIR",
          "num-bands": len(bands), "pitch-left": 0.0, "pitch-right": 0.0, "split-channels": False,
          "left": channel(bands), "right": channel(bands)}
    data = {"output": {"blocklist": [], "plugins_order": ["equalizer#0"], "equalizer#0": eq}}
    with open(f"data/presets/{fe.PRESET_PREFIX}{name}.json", "w") as fh:
        json.dump(data, fh, indent=4)
    print(f"{name:20s} peak {peak:+.1f} dB  pre-amp {pre:+.1f}")
