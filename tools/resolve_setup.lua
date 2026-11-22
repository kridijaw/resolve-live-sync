-- Runs inside Resolve (Workspace > Scripts). Installed by live_sync.py, which fills in the config.
local CONFIG = __CONFIG__

local resolve = resolve or (...) or (bmd and bmd.scriptapp and bmd.scriptapp("Resolve"))
local LOG = assert(io.open(CONFIG.out .. "/log.txt", "w"))

local function log(...)
    local parts = {}
    for i = 1, select("#", ...) do
        parts[i] = tostring((select(i, ...)))
    end
    LOG:write(table.concat(parts, " "), "\n")
    LOG:flush()
end

local function exists(path)
    local f = io.open(path, "rb")
    if f then
        f:close()
    end
    return f ~= nil
end

local function contains(list, value)
    for _, v in pairs(list or {}) do
        if v == value then
            return true
        end
    end
    return false
end

local function append(pool, info)
    local res = pool:AppendToTimeline({ info })
    return type(res) == "table" and res[1] or nil
end

local function build(pm, spec)
    if contains(pm:GetProjectListInCurrentFolder(), spec.name) then
        log("SKIP project exists:", spec.name)
        return
    end
    local paths, seen, missing = {}, {}, {}
    for _, it in ipairs(spec.items) do
        if not seen[it.path] then
            seen[it.path] = true
            paths[#paths + 1] = it.path
            if not exists(it.path) then
                missing[#missing + 1] = it.path
            end
        end
    end
    if #missing > 0 then
        log("FAIL missing files, project not created:", table.concat(missing, " | "))
        return
    end
    local proj = pm:CreateProject(spec.name)
    if not proj then
        log("FAIL CreateProject", spec.name)
        return
    end
    log("settings base:", proj:SetSettings({
        timelineFrameRate = "30",
        timelineResolutionWidth = tostring(spec.width),
        timelineResolutionHeight = tostring(spec.height),
        timelineOutputResMatchTimelineRes = "1",
        timelineInputResMismatchBehavior = spec.mismatch,
        colorScienceMode = "davinciYRGBColorManagedv2",
    }))
    log("settings rcm:", proj:SetSettings({ isAutoColorManage = "1", rcmPresetMode = "SDR" }))
    local got = proj:GetSettings() or {}
    for _, k in ipairs({ "timelineFrameRate", "timelineResolutionWidth", "timelineResolutionHeight",
        "timelineInputResMismatchBehavior", "colorScienceMode", "isAutoColorManage", "rcmPresetMode",
        "colorSpaceTimeline", "colorSpaceOutput" }) do
        log("  " .. k, got[k])
    end

    local pool = proj:GetMediaPool()
    local clipInfos = {}
    for i, p in ipairs(paths) do
        clipInfos[i] = { FilePath = p }
    end
    local byPath, count = {}, 0
    for _, mpi in pairs(pool:ImportMedia(clipInfos) or {}) do
        byPath[mpi:GetClipProperty("File Path")] = mpi
        count = count + 1
    end
    log("imported", count, "of", #paths)
    for _, p in ipairs(paths) do
        if not byPath[p] then
            log("  not imported:", p)
        end
    end

    local tl = pool:CreateEmptyTimeline(spec.timeline)
    if not tl then
        log("FAIL CreateEmptyTimeline", spec.timeline)
        return
    end
    proj:SetCurrentTimeline(tl)
    while tl:GetTrackCount("video") < #spec.video_tracks do
        if not tl:AddTrack("video") then
            error("could not add a video track")
        end
    end
    while tl:GetTrackCount("audio") < #spec.audio_tracks do
        if not tl:AddTrack("audio", "stereo") then
            error("could not add an audio track")
        end
    end
    for i, n in ipairs(spec.video_tracks) do
        tl:SetTrackName("video", i, n)
    end
    for i, n in ipairs(spec.audio_tracks) do
        tl:SetTrackName("audio", i, n)
    end
    local start = tl:GetStartFrame()
    log("timeline start frame", start)

    table.sort(spec.items, function(a, b)
        if a.type ~= b.type then
            return a.type < b.type
        end
        if a.track ~= b.track then
            return a.track < b.track
        end
        return a.frame < b.frame
    end)
    local bad = 0
    for _, it in ipairs(spec.items) do
        local mpi = byPath[it.path]
        if mpi then
            local info = { mediaPoolItem = mpi, mediaType = it.type, trackIndex = it.track,
                recordFrame = start + it.frame }
            if it.src then
                info.startFrame, info.endFrame = it.src[1], it.src[2]
            end
            local item = append(pool, info)
            if not item and not it.src then
                info.startFrame = 0
                info.endFrame = (tonumber(mpi:GetClipProperty("Frames")) or 0) - 1
                item = append(pool, info)
            end
            if not item then
                bad = bad + 1
                log("FAIL append", it.path, "type", it.type, "track", it.track, "frame", it.frame)
            elseif item:GetStart() ~= start + it.frame then
                bad = bad + 1
                log("MISPLACED", it.path, "wanted", start + it.frame, "got", item:GetStart())
            end
        end
    end
    log("appended with", bad, "problems")
    for _, kind in ipairs({ "video", "audio" }) do
        for i = 1, tl:GetTrackCount(kind) do
            local items = tl:GetItemListInTrack(kind, i) or {}
            log("  " .. kind, i, tl:GetTrackName(kind, i), #items, "items")
            for n = 1, math.min(3, #items) do
                log("    ", items[n]:GetName(), items[n]:GetStart() - start, items[n]:GetDuration())
            end
        end
    end
    for _, off in ipairs(spec.stills) do
        local file = string.format("%s/%s_%s_%d.jpg", CONFIG.out, spec.name:sub(1, 10),
            spec.timeline:gsub(" ", "_"), off)
        local r = tl:ExportFrame(off, { outputType = "file", filePath = file, maxDimension = 1280 }) or {}
        log("still", off, r.success, r.error)
    end
    log("save:", pm:SaveProject())
end

local ok, err = xpcall(function()
    local pm = resolve:GetProjectManager()
    log("Resolve", resolve:GetVersionString())
    local cur = pm:GetCurrentProject()
    log("current project:", cur and cur:GetName(), "saved:", pm:SaveProject())
    for _, spec in ipairs(CONFIG.projects) do
        log("==", spec.name)
        build(pm, spec)
    end
    log("DONE")
end, debug.traceback)
if not ok then
    log(err)
end
LOG:close()
print(ok and "Live Sync Setup finished" or err)
