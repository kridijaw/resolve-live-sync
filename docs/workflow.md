# From recording to Resolve

Needs `ffmpeg` (with VideoToolbox) and Python 3.

## Setup

```sh
python3 -m venv tools/.venv
tools/.venv/bin/pip install -r tools/requirements.txt
```

## Session folder

```
Aufnahmen/2026-05-01 Park Session/
  Master/    recorder WAV files
  Pixel5/    one folder per camera
  Pixel7/
```

Keep the original `PXL_...` file names. They hold the recording time, which places clips that have no usable audio match.

## Steps

```sh
tools/.venv/bin/python tools/live_sync.py sync "2026-05-01 Park Session"
tools/.venv/bin/python tools/live_sync.py encode "2026-05-01 Park Session"
tools/.venv/bin/python tools/live_sync.py resolve
```

1. `sync` matches every clip to the master and writes `tools/sessions/<session>.json`. Master files that continue each other without a gap are joined.
2. `encode` writes constant-frame-rate edit copies to `Edit/`. Existing copies are kept. It stops before free disk space drops below 30 GB.
3. `resolve` installs `Live Sync Setup`. Run it in Resolve under **Workspace > Scripts**. It creates missing projects and fills empty timelines, and leaves filled timelines alone. It logs to **Workspace > Console** and writes check stills to `tools/out/resolve/`. Sessions with missing edit copies are left out.

## Session file

Check it between `sync` and `encode`.

- `placed_by`: `audio` (matched to a master), `audio near clock` (matched close to the recording time, also against other takes) or `clock` (recording time only).
- `in`, `out`: source range in seconds. Takes over 10 minutes are trimmed to 2 minutes around the master. `frame` is the timeline position of `in` at 30 fps, so move both together.
- `cameras`: video tracks from bottom to top.

Audio tracks are the master, then one per camera in reverse name order (Pixel 7 before Pixel 5). They hold the full sound of every clip, cut at the edges of the gaps that no track above covers.

## Why edit copies

The phones record a variable frame rate. Resolve assumes a constant one, so a one-hour take drifts by seconds against the master.
