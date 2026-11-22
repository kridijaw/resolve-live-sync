#!/usr/bin/env python3
"""Sync a recording session to its master audio, make edit copies, build the Resolve setup script."""
import argparse
import json
import math
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy import fft
from scipy.io import wavfile
from scipy.signal import butter, sosfilt

TOOLS = Path(__file__).resolve().parent
RECORDINGS = TOOLS.parent / "Aufnahmen"
SESSIONS = TOOLS / "sessions"
OUT = TOOLS / "out"
RESOLVE_SCRIPT = Path.home() / ("Library/Application Support/Blackmagic Design/DaVinci Resolve/"
                                "Fusion/Scripts/Utility/Live Sync Setup.py")

FPS = 30
RATE = 8000
CHUNK = 8
AGREE = 0.1
NEARBY = 0.3
CLOCK_LIMIT = 2.0
SPLIT_GAP = 0.1
LONG_TAKE = 600
MARGIN = 120
MIN_FREE_GB = 30
BAND = butter(4, [200, 3000], btype="band", fs=RATE, output="sos")


def probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                       capture_output=True, text=True, check=True)
    info = json.loads(r.stdout)
    video = next((s for s in info["streams"] if s["codec_type"] == "video"), {})
    return float(info["format"]["duration"]), video


def load_audio(path):
    cache = OUT / "audio" / path.relative_to(RECORDINGS).with_suffix(".wav")
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        part = cache.with_suffix(".part.wav")
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(path), "-vn", "-ac", "1",
                        "-ar", str(RATE), "-c:a", "pcm_s16le", str(part)], check=True)
        part.rename(cache)
    return sosfilt(BAND, wavfile.read(cache)[1].astype(np.float32)).astype(np.float32)


class Reference:
    """Audio that chunks can be located in by normalized cross-correlation."""

    def __init__(self, samples):
        self.x = samples
        self.seconds = len(samples) / RATE
        self.n = fft.next_fast_len(len(samples) + CHUNK * RATE)
        self.spectrum = None
        self.energy = np.concatenate([[0], np.cumsum(samples.astype(np.float64) ** 2)])
        self.silence = 1e-4 * self.energy[-1] / len(samples)

    def _best(self, corr, start, size):
        e = self.energy[start + size:start + size + len(corr)] - self.energy[start:start + len(corr)]
        ncc = np.where(e > self.silence * size, corr / np.sqrt(np.maximum(e, 1e-9)), 0)
        i = int(np.argmax(ncc))
        return (start + i) / RATE, float(ncc[i])

    def find(self, chunk, around=None):
        size = len(chunk)
        if size > len(self.x):
            return None
        c = chunk - chunk.mean()
        c = c / (np.linalg.norm(c) + 1e-9)
        if around is None:
            if self.spectrum is None:
                self.spectrum = fft.rfft(self.x, self.n)
            corr = fft.irfft(self.spectrum * np.conj(fft.rfft(c, self.n)), self.n)[:len(self.x) - size + 1]
            return self._best(corr, 0, size)
        lo = int((around - NEARBY) * RATE)
        hi = int((around + NEARBY) * RATE) + size
        if lo < 0 or hi > len(self.x):
            return None
        n = fft.next_fast_len(hi - lo + size)
        corr = fft.irfft(fft.rfft(self.x[lo:hi], n) * np.conj(fft.rfft(c, n)), n)[:hi - lo - size + 1]
        return self._best(corr, lo, size)


def match(samples, ref, expected=None):
    """Where the clip starts inside ref: (seconds, votes, chunks, ncc), or None without enough agreement."""
    size = min(CHUNK * RATE, len(samples))
    if expected is None and len(samples) < 2 * size:
        return None
    count = min(24 if len(samples) > LONG_TAKE * RATE else 8, max(1, len(samples) // size))
    starts = np.linspace(0, len(samples) - size, count).astype(int)
    hits = []
    for s in starts:
        found = ref.find(samples[s:s + size], None if expected is None else expected + s / RATE)
        if found:
            hits.append((found[0] - s / RATE, found[1]))
    if not hits:
        return None
    groups = [[h for h in hits if abs(h[0] - center) < AGREE] for center, _ in hits]
    best = max(groups, key=lambda g: (len(g), np.mean([h[1] for h in g])))
    ncc = float(np.mean([h[1] for h in best]))
    votes = len(best)
    if expected is None:
        ok = (votes >= 3 and ncc >= 0.25) or (votes >= 2 and votes == len(hits))
    else:
        ok = votes >= max(1, math.ceil(len(hits) / 2)) and ncc >= 0.3
    return (float(np.median([h[0] for h in best])), votes, len(hits), ncc) if ok else None


def clock(path):
    m = re.match(r"PXL_(\d{8})_(\d{6})(\d{3})", path.name)
    if not m:
        return None
    t = datetime.strptime(m[1] + m[2], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    return t.timestamp() + int(m[3]) / 1000


def media(folder, *suffixes):
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in suffixes and not p.name.startswith("."))


def place(session):
    """Put masters and clips of a session on one time axis (seconds, first master near 0)."""
    masters = [{"path": p, "seconds": probe(p)[0], "ref": Reference(load_audio(p))}
               for p in media(session / "Master", ".wav")]
    if not masters:
        sys.exit(f"No WAV files in {session / 'Master'}")
    clips = []
    for cam in sorted(d for d in session.iterdir() if d.is_dir() and d.name not in ("Master", "Edit")):
        for p in media(cam, ".mp4", ".mov"):
            seconds, _ = probe(p)
            clips.append({"path": p, "camera": cam.name, "seconds": seconds, "wall": clock(p),
                          "audio": load_audio(p), "pos": None, "by": None})
    if not clips:
        sys.exit(f"No camera folders with video in {session}")

    for c in clips:
        c["hits"] = {i: h for i, m in enumerate(masters) if (h := match(c["audio"], m["ref"]))}
        print(f"  {c['camera']}/{c['path'].name}: "
              + (", ".join(f"{masters[i]['path'].name} at {h[0]:.3f}s ({h[1]}/{h[2]} chunks)"
                           for i, h in c["hits"].items()) or "no direct match"), flush=True)

    weight = [sum(c["hits"][i][1] for c in clips if i in c["hits"]) for i in range(len(masters))]
    pos = {int(np.argmax(weight)): 0.0}

    def deltas():
        out = {}
        for cam in {c["camera"] for c in clips}:
            d = [c["pos"] - c["wall"] for c in clips if c["camera"] == cam and c["by"] == "audio" and c["wall"]]
            if d:
                out[cam] = float(np.median(d))
        return out

    changed = True
    while changed:
        changed = False
        for c in clips:
            known = [i for i in c["hits"] if i in pos]
            if c["pos"] is None and known:
                i = max(known, key=lambda k: c["hits"][k][1])
                c.update(pos=pos[i] + c["hits"][i][0], by="audio")
                changed = True
        todo = [i for i in range(len(masters)) if i not in pos]
        direct = [(c["hits"][i][1], i, c["pos"] - c["hits"][i][0])
                  for c in clips if c["pos"] is not None for i in todo if i in c["hits"]]
        if direct:
            _, i, p = max(direct)
            pos[i] = p
            changed = True
            continue
        known = deltas()
        for i in todo:
            est = [c["wall"] + known[c["camera"]] - c["hits"][i][0]
                   for c in clips if i in c["hits"] and c["wall"] and c["camera"] in known]
            if est:
                pos[i] = float(np.median(est))
                changed = True
                break
    for i, m in enumerate(masters):
        if i not in pos:
            sys.exit(f"{m['path'].name} matches no clip, so it cannot be placed")
        m["pos"] = pos[i]
    masters.sort(key=lambda m: m["pos"])
    for a, b in zip(masters, masters[1:]):
        gap = b["pos"] - (a["pos"] + a["seconds"])
        if abs(gap) < SPLIT_GAP:
            shift = -gap
            b.update(pos=b["pos"] + shift, continues=True)

    known = deltas()
    for c in clips:
        if c["by"] == "audio" and c["wall"] and abs(c["pos"] - c["wall"] - known[c["camera"]]) > CLOCK_LIMIT:
            print(f"  rejected {c['path'].name}: audio match contradicts the camera clock")
            c.update(pos=None, by=None)
    for c in clips:
        if c["pos"] is not None or not c["wall"] or c["camera"] not in known:
            continue
        guess = c["wall"] + known[c["camera"]]
        others = [(o["pos"], o["audio"]) for o in clips if o["by"] == "audio" and o["seconds"] > LONG_TAKE
                  and o["pos"] < guess + c["seconds"] and guess < o["pos"] + o["seconds"]]
        refs = [(m["pos"], m["ref"]) for m in masters] + [(p, Reference(a)) for p, a in others]
        found = [(h[3], p + h[0]) for p, r in refs if (h := match(c["audio"], r, guess - p))]
        if found:
            c.update(pos=max(found)[1], by="audio near clock")
        else:
            c.update(pos=guess, by="clock")
    return masters, clips


def uncovered(start, end, spans):
    out = [(start, end)]
    for a, b in spans:
        out = [piece for s, e in out for piece in ((s, min(e, a)), (max(s, b), e)) if piece[1] - piece[0] >= FPS]
    return out


def sync(session):
    target = SESSIONS / f"{session.name}.json"
    if target.exists():
        sys.exit(f"{target} exists. Delete it to sync again.")
    masters, clips = place(session)
    begin = min(m["pos"] for m in masters)
    end = max(m["pos"] + m["seconds"] for m in masters)
    kept = []
    for c in clips:
        if c["pos"] is None:
            print(f"  skipped {c['path'].name}: no audio match and no usable clock")
            continue
        c["in"], c["out"] = 0.0, c["seconds"]
        if c["seconds"] > LONG_TAKE:
            c["in"] = max(0.0, begin - MARGIN - c["pos"])
            c["out"] = min(c["seconds"], end + MARGIN - c["pos"])
        if c["out"] <= c["in"]:
            print(f"  skipped {c['path'].name}: long take entirely outside the master")
            continue
        kept.append(c)
    first = min([begin] + [c["pos"] + c["in"] for c in kept])
    origin = begin - math.ceil((begin - first) * FPS) / FPS
    frame = lambda t: round((t - origin) * FPS)

    tag = session.name[2:10].replace("-", "")
    groups = []
    for m in masters:
        if m.get("continues"):
            groups[-1].append(m)
        else:
            groups.append([m])
    rel = lambda p: str(p.relative_to(session))
    data = {"session": session.name, "timeline": "Sync", "width": 1080, "height": 1920, "masters": [], "clips": []}
    spans = []
    for n, g in enumerate(groups, 1):
        edit = rel(g[0]["path"]) if len(g) == 1 else f"Edit/{tag}_master_{n}_joined.wav"
        seconds = sum(m["seconds"] for m in g)
        start = frame(g[0]["pos"])
        spans.append((start, start + round(seconds * FPS)))
        data["masters"].append({"sources": [rel(m["path"]) for m in g], "edit": edit, "frame": start,
                                "seconds": round(seconds, 4)})
    longest = lambda cam: max(c["seconds"] for c in kept if c["camera"] == cam)
    data["cameras"] = sorted({c["camera"] for c in kept}, key=longest, reverse=True)
    for c in sorted(kept, key=lambda c: c["pos"]):
        start = frame(c["pos"] + c["in"])
        length = int((c["out"] - c["in"]) * FPS)
        stem = c["path"].name.split(".")[0]
        data["clips"].append({
            "camera": c["camera"], "source": rel(c["path"]), "edit": f"Edit/{c['camera']}/{stem}_edit.mov",
            "in": round(c["in"], 4), "out": round(c["out"], 4), "frame": start, "placed_by": c["by"],
            "phone_audio": [list(s) for s in uncovered(start, start + length, spans)]
            if c["camera"] == data["cameras"][-1] else []})
    data["stills"] = [(a + b) // 2 for a, b in spans]
    SESSIONS.mkdir(exist_ok=True)
    target.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    print(f"Wrote {target}")


def video_args(v):
    if v.get("color_transfer") in ("arib-std-b67", "smpte2084"):
        trc = v["color_transfer"]
        return ["-vf", f"fps={FPS},format=p010le", "-profile:v", "main10", "-color_range", "tv",
                "-colorspace", "bt2020nc", "-color_primaries", "bt2020", "-color_trc", trc]
    rng = "pc" if v.get("color_range") == "pc" else "tv"
    matrix = "bt709" if v.get("color_space") == "bt709" else "bt601"
    return ["-vf", f"fps={FPS},scale=in_range={rng}:out_range=tv:in_color_matrix={matrix}:out_color_matrix=bt709,"
            "format=yuv420p,setparams=range=tv:colorspace=bt709:color_primaries=bt709:color_trc=bt709",
            "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"]


def encode(session):
    data = json.loads((SESSIONS / f"{session.name}.json").read_text())
    for m in data["masters"]:
        out = session / m["edit"]
        if len(m["sources"]) > 1 and not out.exists():
            out.parent.mkdir(parents=True, exist_ok=True)
            OUT.mkdir(exist_ok=True)
            listing = OUT / "concat.txt"
            quoted = [str(session / s).replace("'", r"'\''") for s in m["sources"]]
            listing.write_text("".join(f"file '{q}'\n" for q in quoted))
            part = out.with_suffix(".part.wav")
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "concat", "-safe", "0",
                            "-i", str(listing), "-c", "copy", "-rf64", "auto", str(part)], check=True)
            part.rename(out)
    for c in data["clips"]:
        out = session / c["edit"]
        if out.exists():
            continue
        _, v = probe(session / c["source"])
        seconds = c["out"] - c["in"]
        mbit = 40 if v["width"] * v["height"] > 1920 * 1080 else 20
        free = shutil.disk_usage(session).free / 1e9 - seconds * mbit / 8000
        if free < MIN_FREE_GB:
            sys.exit(f"Stopped before {c['source']}: it would leave {free:.0f} GB free (minimum {MIN_FREE_GB})")
        out.parent.mkdir(parents=True, exist_ok=True)
        part = out.with_suffix(".part.mov")
        print(f"  {c['source']} ({seconds / 60:.1f} min)", flush=True)
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-hwaccel", "videotoolbox", "-ss", str(c["in"]),
                        "-i", str(session / c["source"]), "-t", f"{seconds:.4f}", "-map", "0:v:0", "-map", "0:a:0",
                        *video_args(v), "-c:v", "hevc_videotoolbox", "-b:v", f"{mbit}M", "-tag:v", "hvc1",
                        "-c:a", "pcm_s16le", str(part)], check=True)
        part.rename(out)


def resolve_project(data):
    session = RECORDINGS / data["session"]
    items = [{"path": str(session / m["edit"]), "type": 2, "track": 1, "frame": m["frame"]} for m in data["masters"]]
    for c in data["clips"]:
        path = str(session / c["edit"])
        items.append({"path": path, "type": 1, "track": data["cameras"].index(c["camera"]) + 1, "frame": c["frame"]})
        items += [{"path": path, "type": 2, "track": 2, "frame": a, "src": [a - c["frame"], b - c["frame"] - 1]}
                  for a, b in c["phone_audio"]]
    phone = any(c["phone_audio"] for c in data["clips"])
    return {"name": data["session"], "timeline": data["timeline"], "width": data["width"], "height": data["height"],
            "mismatch": "scaleToFit", "video_tracks": [re.sub(r"(\D)(\d)", r"\1 \2", c) for c in data["cameras"]],
            "audio_tracks": ["Master", "Phone"] if phone else ["Master"], "items": items, "stills": data["stills"]}


def resolve(_=None):
    projects = [resolve_project(json.loads(p.read_text())) for p in sorted(SESSIONS.glob("*.json"))]
    config = {"out": str(OUT / "resolve"), "projects": projects}
    script = (TOOLS / "resolve_setup.py").read_text().replace("__CONFIG__", repr(json.dumps(config)))
    RESOLVE_SCRIPT.parent.mkdir(parents=True, exist_ok=True)
    RESOLVE_SCRIPT.write_text(script)
    print(f"Wrote {RESOLVE_SCRIPT} with {len(projects)} projects")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, text in (("sync", "match clips to the master and write tools/sessions/<session>.json"),
                       ("encode", "write constant-frame-rate edit copies")):
        sub.add_parser(name, help=text).add_argument("session", help="folder name in Aufnahmen")
    sub.add_parser("resolve", help="install the Resolve setup script for all sessions")
    args = parser.parse_args()
    if args.command == "resolve":
        return resolve()
    session = RECORDINGS / args.session
    if not session.is_dir():
        sys.exit(f"No such session: {session}")
    {"sync": sync, "encode": encode}[args.command](session)


if __name__ == "__main__":
    main()
