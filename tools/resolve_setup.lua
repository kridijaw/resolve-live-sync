-- Runs inside Resolve (Workspace > Scripts). Installed by live_sync.py, which fills in the config.
local CONFIG = __CONFIG__

local report, problems = {}, 0

local function log(...)
    local parts = {}
    for i, v in ipairs({ ... }) do
        parts[i] = tostring(v)
    end
    report[#report + 1] = table.concat(parts, " ")
    print(report[#report])
end

local function problem(...)
    problems = problems + 1
    log("PROBLEM", ...)
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

local function collect(folder, byPath)
    for _, clip in ipairs(folder:GetClipList() or {}) do
        local path = clip:GetClipProperty("File Path")
        if path and path ~= "" then
            byPath[path] = clip
        end
    end
    for _, sub in ipairs(folder:GetSubFolderList() or {}) do
        collect(sub, byPath)
    end
end

local function findMedia(pool, paths)
    local byPath, clipInfos, missing = {}, {}, {}
    collect(pool:GetRootFolder(), byPath)
    for _, p in ipairs(paths) do
        if not byPath[p] then
            missing[#missing + 1] = p
            clipInfos[#clipInfos + 1] = { FilePath = p }
        end
    end
    log("already in media pool:", #paths - #missing, "of", #paths)
    if #missing > 0 then
        local got = pool:ImportMedia(clipInfos)
        log("ImportMedia with clip infos returned", type(got), type(got) == "table" and #got or "")
        if type(got) ~= "table" or #got == 0 then
            got = pool:ImportMedia(missing)
            log("ImportMedia with paths returned", type(got), type(got) == "table" and #got or "")
        end
        collect(pool:GetRootFolder(), byPath)
    end
    for _, p in ipairs(paths) do
        if not byPath[p] then
            problem("not in media pool:", p)
        end
    end
    return byPath
end

local function findTimeline(proj, name)
    for i = 1, proj:GetTimelineCount() do
        local tl = proj:GetTimelineByIndex(i)
        if tl and tl:GetName() == name then
            return tl
        end
    end
end

local function itemCount(tl)
    local n = 0
    for _, kind in ipairs({ "video", "audio" }) do
        for i = 1, tl:GetTrackCount(kind) do
            n = n + #(tl:GetItemListInTrack(kind, i) or {})
        end
    end
    return n
end

local function build(pm, spec)
    local paths, seen = {}, {}
    for _, it in ipairs(spec.items) do
        if not seen[it.path] then
            seen[it.path] = true
            paths[#paths + 1] = it.path
        end
    end
    local proj = pm:GetCurrentProject()
    if not proj or proj:GetName() ~= spec.name then
        if contains(pm:GetProjectListInCurrentFolder(), spec.name) then
            proj = pm:LoadProject(spec.name)
        else
            proj = pm:CreateProject(spec.name)
        end
    end
    if not proj then
        problem("could not open or create project", spec.name)
        return
    end
    local tl = findTimeline(proj, spec.timeline)
    if tl and itemCount(tl) > 0 then
        log("SKIP timeline already filled:", spec.name)
        return
    end
    if not tl then
        log("settings base:", tostring(proj:SetSettings({
            timelineFrameRate = "30",
            timelineResolutionWidth = tostring(spec.width),
            timelineResolutionHeight = tostring(spec.height),
            timelineOutputResMatchTimelineRes = "1",
            timelineInputResMismatchBehavior = spec.mismatch,
            colorScienceMode = "davinciYRGBColorManagedv2",
        })))
        log("settings rcm:", tostring(proj:SetSettings({ isAutoColorManage = "1", rcmPresetMode = "SDR" })))
    end
    local got = proj:GetSettings() or {}
    for _, k in ipairs({ "timelineFrameRate", "timelineResolutionWidth", "timelineResolutionHeight",
        "timelineInputResMismatchBehavior", "colorScienceMode", "isAutoColorManage", "rcmPresetMode",
        "colorSpaceTimeline", "colorSpaceOutput" }) do
        log("  " .. k, tostring(got[k]))
    end

    local pool = proj:GetMediaPool()
    local before = problems
    local byPath = findMedia(pool, paths)
    if problems > before then
        return
    end

    tl = tl or pool:CreateEmptyTimeline(spec.timeline)
    if not tl then
        problem("could not create timeline", spec.timeline)
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
                problem("could not place", it.path, "type", it.type, "track", it.track, "frame", it.frame)
            elseif item:GetStart() ~= start + it.frame then
                problem("misplaced", it.path, "wanted", start + it.frame, "got", item:GetStart())
            end
        end
    end
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
        log("still", off, tostring(r.success), tostring(r.error))
        if not r.success then
            problem("no still at frame", off)
        end
    end
    log("save:", tostring(pm:SaveProject()))
end

local ok, err = pcall(function()
    local pm = resolve:GetProjectManager()
    log("Resolve", resolve:GetVersionString())
    local cur = pm:GetCurrentProject()
    log("current project:", cur and cur:GetName() or "none", "saved:", tostring(pm:SaveProject()))
    for _, spec in ipairs(CONFIG.projects) do
        log("==", spec.name)
        build(pm, spec)
    end
end)
if not ok then
    problem(err)
end
log("Live Sync Setup finished with", problems, "problems")
if problems > 0 then
    error(table.concat(report, "\n"), 0)
end
