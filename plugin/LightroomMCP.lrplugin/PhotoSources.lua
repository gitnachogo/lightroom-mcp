local PhotoSources = {}

-- The photos' own folders load far faster than All Photographs on a large
-- catalog, where switching to All Photographs and waiting for a selection to
-- stick costs tens of seconds. Returns nil when any folder cannot be resolved,
-- so callers fall back to All Photographs.
function PhotoSources.folders(catalog, photos)
    -- Imported per call: callers' specs install their own SDK mocks.
    local LrPathUtils, LrTasks = import 'LrPathUtils', import 'LrTasks'
    local ok, raw = LrTasks.pcall(function() return catalog:batchGetRawMetadata(photos, { 'path' }) end)
    if not ok or type(raw) ~= "table" then return nil end
    local folders, seen = {}, {}
    for _, photo in ipairs(photos) do
        local path = (raw[photo] or {}).path
        if type(path) ~= "string" then return nil end
        local parent = LrPathUtils.parent(path)
        if not parent then return nil end
        if not seen[parent] then
            local found, folder = LrTasks.pcall(function() return catalog:getFolderByPath(parent) end)
            if not found or not folder then return nil end
            seen[parent] = true
            folders[#folders + 1] = folder
        end
    end
    return folders
end

-- The folders, or All Photographs when any folder cannot be resolved.
function PhotoSources.foldersOrAll(catalog, photos)
    return PhotoSources.folders(catalog, photos) or { catalog.kAllPhotos }
end

return PhotoSources
