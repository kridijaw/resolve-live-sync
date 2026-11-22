-- Runs inside Resolve (Workspace > Scripts). Installed by live_sync.py.
local TOLERANCE = 5 -- frames: a phone clip's sound can end slightly before its planned length

local function itemsIn(tl, kind, i)
    return tl:GetItemListInTrack(kind, i) or {}
end

local function layerTracks(tl)
    local cameras, layers = {}, {}
    for i = 1, tl:GetTrackCount("video") do
        cameras[tl:GetTrackName("video", i)] = true
    end
    for i = 1, tl:GetTrackCount("audio") do
        local name = tl:GetTrackName("audio", i)
        if name == "Master" or cameras[name] then
            layers[#layers + 1] = { index = i, name = name }
        end
    end
    return layers
end

local function isCovered(a, b, spans)
    table.sort(spans, function(x, y) return x[1] < y[1] end)
    local at = a
    for _, s in ipairs(spans) do
        if s[2] > at and s[1] < b then
            if s[1] - at > TOLERANCE then
                return false
            end
            at = s[2]
        end
    end
    return at > a and b - at <= TOLERANCE
end

local function coveredItems(tl)
    local found, spans = {}, {}
    for n, layer in ipairs(layerTracks(tl)) do
        local own = {}
        for _, item in ipairs(itemsIn(tl, "audio", layer.index)) do
            local a = item:GetStart()
            local b = a + item:GetDuration()
            if n > 1 and isCovered(a, b, spans) then
                found[#found + 1] = item
            end
            if item:GetClipEnabled() then
                own[#own + 1] = { a, b }
            end
        end
        for _, s in ipairs(own) do
            spans[#spans + 1] = s
        end
    end
    return found
end

local function count(tl, kind)
    local n = 0
    for i = 1, tl:GetTrackCount(kind) do
        n = n + #itemsIn(tl, kind, i)
    end
    return n
end

local function freeName(proj, base)
    local taken = {}
    for i = 1, proj:GetTimelineCount() do
        taken[proj:GetTimelineByIndex(i):GetName()] = true
    end
    local name, n = base, 2
    while taken[name] do
        name, n = base .. " " .. n, n + 1
    end
    return name
end

local function main()
    local proj = resolve:GetProjectManager():GetCurrentProject()
    local tl = proj and proj:GetCurrentTimeline()
    if not tl then
        error("no timeline is open", 0)
    end
    local layers = layerTracks(tl)
    if #layers < 2 or layers[1].name ~= "Master" then
        error("needs an audio track Master above the phone tracks in " .. tl:GetName(), 0)
    end
    local total = #coveredItems(tl)
    if total == 0 then
        print("Nothing to trim in " .. tl:GetName())
        return
    end

    local name = freeName(proj, tl:GetName() .. " trimmed")
    local copy = tl:DuplicateTimeline(name)
    if not copy then
        error("could not duplicate " .. tl:GetName(), 0)
    end
    proj:SetCurrentTimeline(copy)
    if proj:GetCurrentTimeline():GetName() ~= name then
        error("could not switch to " .. name .. ", nothing was deleted", 0)
    end
    local doomed = coveredItems(copy)
    if #doomed ~= total then
        error("the copy does not match the original, nothing was deleted", 0)
    end
    local video, audio = count(copy, "video"), count(copy, "audio")
    if not copy:DeleteClips(doomed, false) then
        error("Resolve refused to delete the clips in " .. name, 0)
    end
    if count(copy, "video") ~= video or count(copy, "audio") ~= audio - total then
        error("unexpected clip count in " .. name .. ", check it before use", 0)
    end
    print("Live Sync Trim Audio removed " .. total .. " clips in " .. name)
end

local ok, err = pcall(main)
if not ok then
    print("PROBLEM " .. tostring(err))
    error(err, 0)
end
