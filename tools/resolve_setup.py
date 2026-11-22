# Runs inside Resolve (Workspace > Scripts). Installed by live_sync.py, which fills in the config.
import json, os, traceback

CONFIG = json.loads(__CONFIG__)
OUT = CONFIG["out"]
os.makedirs(OUT, exist_ok=True)
LOG = open(os.path.join(OUT, "log.txt"), "w")

def log(*a):
    LOG.write(" ".join(str(x) for x in a) + "\n")
    LOG.flush()

def build(pm, spec):
    if spec["name"] in (pm.GetProjectListInCurrentFolder() or []):
        log("SKIP project exists:", spec["name"])
        return
    paths = sorted({it["path"] for it in spec["items"]})
    missing = [p for p in paths if not os.path.exists(p)]
    if missing:
        log("FAIL missing files, project not created:", missing)
        return
    proj = pm.CreateProject(spec["name"])
    if not proj:
        log("FAIL CreateProject", spec["name"])
        return
    settings = {
        "timelineFrameRate": "30",
        "timelineResolutionWidth": str(spec["width"]),
        "timelineResolutionHeight": str(spec["height"]),
        "timelineOutputResMatchTimelineRes": "1",
        "timelineInputResMismatchBehavior": spec["mismatch"],
        "colorScienceMode": "davinciYRGBColorManagedv2",
    }
    log("settings base:", proj.SetSettings(settings))
    log("settings rcm:", proj.SetSettings({"isAutoColorManage": "1", "rcmPresetMode": "SDR"}))
    got = proj.GetSettings()
    log("settings now:", {k: got.get(k) for k in list(settings) + ["isAutoColorManage", "rcmPresetMode", "colorSpaceTimeline", "colorSpaceOutput"]})

    mp = proj.GetMediaPool()
    imported = mp.ImportMedia([{"FilePath": p} for p in paths]) or []
    by_path = {}
    for mpi in imported:
        by_path[mpi.GetClipProperty("File Path")] = mpi
    log("imported", len(by_path), "of", len(paths))
    for p in paths:
        if p not in by_path:
            log("  not imported:", p)

    tl = mp.CreateEmptyTimeline(spec["timeline"])
    proj.SetCurrentTimeline(tl)
    while tl.GetTrackCount("video") < len(spec["video_tracks"]):
        tl.AddTrack("video")
    while tl.GetTrackCount("audio") < len(spec["audio_tracks"]):
        tl.AddTrack("audio", "stereo")
    for i, n in enumerate(spec["video_tracks"], 1):
        tl.SetTrackName("video", i, n)
    for i, n in enumerate(spec["audio_tracks"], 1):
        tl.SetTrackName("audio", i, n)
    start = tl.GetStartFrame()
    log("timeline start frame", start)

    bad = 0
    for it in sorted(spec["items"], key=lambda x: (x["type"], x["track"], x["frame"])):
        mpi = by_path.get(it["path"])
        if not mpi:
            continue
        info = {"mediaPoolItem": mpi, "mediaType": it["type"], "trackIndex": it["track"], "recordFrame": start + it["frame"]}
        if it.get("src"):
            info.update(startFrame=it["src"][0], endFrame=it["src"][1])
        res = mp.AppendToTimeline([info])
        if not res and not it.get("src"):
            frames = int(mpi.GetClipProperty("Frames") or 0)
            info.update(startFrame=0, endFrame=frames - 1)
            res = mp.AppendToTimeline([info])
        if not res:
            bad += 1
            log("FAIL append", os.path.basename(it["path"]), it)
            continue
        item = res[0]
        if item.GetStart() != start + it["frame"]:
            bad += 1
            log("MISPLACED", os.path.basename(it["path"]), "wanted", start + it["frame"], "got", item.GetStart())
        if it.get("props"):
            log("props", os.path.basename(it["path"]), item.SetProperties(it["props"]))
    log("appended with", bad, "problems")
    for kind in ("video", "audio"):
        for i in range(1, tl.GetTrackCount(kind) + 1):
            items = tl.GetItemListInTrack(kind, i) or []
            log(f"  {kind} {i} {tl.GetTrackName(kind, i)}: {len(items)} items",
                [(x.GetName(), x.GetStart() - start, x.GetDuration()) for x in items[:3]])
    for off in spec["stills"]:
        r = tl.ExportFrame(off, {"outputType": "file", "filePath": os.path.join(OUT, f"{spec['name'][:10]}_{spec['timeline'].replace(' ', '_')}_{off}.jpg"), "maxDimension": 1280})
        log("still", off, r.get("success"), r.get("error"))
    log("save:", pm.SaveProject())

try:
    pm = resolve.GetProjectManager()
    log("Resolve", resolve.GetVersionString())
    cur = pm.GetCurrentProject()
    log("current project:", cur.GetName() if cur else None, "saved:", pm.SaveProject())
    for spec in CONFIG["projects"]:
        log("==", spec["name"])
        build(pm, spec)
    log("DONE")
except Exception:
    log(traceback.format_exc())
