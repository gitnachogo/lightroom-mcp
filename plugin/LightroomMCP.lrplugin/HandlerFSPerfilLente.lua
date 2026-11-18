-- Fotosintesis (prototipo): perfil de lente por foto con escala de vineta propia.
--
-- Lightroom solo conserva una escala de vineta propia con LensProfileSetup = "Custom" y el perfil
-- nombrado (Name + Filename + Digest); con "LensDefaults"/"Auto" manda el valor por defecto de la lente.
-- Este handler lee el perfil que guarda cada foto y fija Custom + perfil + escala en una sola
-- transaccion, validando todo antes de escribir. No abre Revelar ni cambia de modulo.
local LrApplication = import 'LrApplication'
local PhotoLookup = require 'PhotoLookup'
local Handler = {}

local MAX_IDS = 100
local CAMPOS = {
    'LensProfileEnable', 'LensProfileSetup', 'LensProfileName', 'LensProfileFilename', 'LensProfileDigest',
    'LensProfileIsEmbedded', 'LensProfileDistortionScale', 'LensProfileVignettingScale', 'AutoLateralCA',
}

local function leerUna(photo)
    local settings = photo:getDevelopSettings()
    local out = { id = photo.localIdentifier }
    out.lente = photo:getFormattedMetadata('lens')
    out.camara = photo:getFormattedMetadata('cameraModel')
    if type(settings) == 'table' then
        for _, k in ipairs(CAMPOS) do out[k] = settings[k] end
    end
    return out
end

local function requireIds(ids)
    if type(ids) ~= 'table' or #ids == 0 or #ids > MAX_IDS then
        error('photo_ids must contain 1 to 100 photo IDs')
    end
end

function Handler.leer(args)
    if type(args) ~= 'table' then error('args must be an object') end
    requireIds(args.photo_ids)
    local catalog = LrApplication.activeCatalog()
    local resolved = PhotoLookup.resolveMany(catalog, args.photo_ids)
    local photos, missing = {}, {}
    catalog:withReadAccessDo(function()
        for _, entry in ipairs(resolved) do
            if entry.photo then photos[#photos + 1] = leerUna(entry.photo)
            else missing[#missing + 1] = tostring(entry.id) end
        end
    end)
    return { success = true, photos = photos, missing = missing }
end

local function texto(v) return type(v) == 'string' and v ~= '' and #v <= 512 end

local function escala(v, campo, i)
    if type(v) ~= 'number' or v ~= math.floor(v) or v < 0 or v > 200 then
        error(string.format('items[%d].%s must be an integer 0-200', i, campo))
    end
    return v
end

-- Valida un item y devuelve la tabla exacta que se aplicara.
local function ajustesDe(item, i)
    if type(item) ~= 'table' then error(string.format('items[%d] must be an object', i)) end
    local modo = item.modo or 'custom'
    -- Medido en vivo (18/11/2026): solo resuelve el perfil tras un render y si la foto no lleva ya
    -- LensProfileName; con nombre, Lightroom vuelve a Custom con ese nombre.
    if modo == 'lens_defaults' then
        return { LensProfileEnable = 1, LensProfileSetup = 'LensDefaults' }
    end
    if modo ~= 'custom' then error(string.format('items[%d].modo must be custom or lens_defaults', i)) end
    if not texto(item.name) or not texto(item.filename) then
        error(string.format('items[%d] needs name and filename of the lens profile', i))
    end
    if item.digest ~= nil and not (type(item.digest) == 'string' and item.digest:match('^%x+$')) then
        error(string.format('items[%d].digest must be a hex string', i))
    end
    local s = {
        LensProfileEnable = 1,
        LensProfileSetup = 'Custom',
        LensProfileName = item.name,
        LensProfileFilename = item.filename,
        LensProfileIsEmbedded = false,
        LensProfileDistortionScale = escala(item.distortion_scale == nil and 100 or item.distortion_scale,
            'distortion_scale', i),
        LensProfileVignettingScale = escala(item.vignetting_scale, 'vignetting_scale', i),
    }
    if item.digest ~= nil then s.LensProfileDigest = item.digest end
    return s
end

-- items: [{photo_id, modo?="custom"|"lens_defaults", name, filename, digest?, vignetting_scale,
--          distortion_scale?=100}]. Todo o nada: si un item no valida o un id no existe, no escribe.
function Handler.fijar(args)
    if type(args) ~= 'table' or type(args.items) ~= 'table' or #args.items == 0 or #args.items > MAX_IDS then
        error('items must contain 1 to 100 entries')
    end
    local ids, vistos, ajustes = {}, {}, {}
    for i, item in ipairs(args.items) do
        ajustes[i] = ajustesDe(item, i)
        local id = tostring(item.photo_id)
        if item.photo_id == nil or vistos[id] then error(string.format('items[%d].photo_id missing or repeated', i)) end
        vistos[id] = true
        ids[i] = item.photo_id
    end
    local catalog = LrApplication.activeCatalog()
    local resolved = PhotoLookup.resolveMany(catalog, ids)
    local missing = {}
    for _, entry in ipairs(resolved) do
        if not entry.photo then missing[#missing + 1] = tostring(entry.id) end
    end
    if #missing > 0 then return { success = false, applied = 0, missing = missing, photos = {} } end
    catalog:withWriteAccessDo('Fotosintesis: perfil de lente', function()
        for i, entry in ipairs(resolved) do
            entry.photo:applyDevelopSettings(ajustes[i], 'Fotosintesis: perfil de lente')
        end
    end)
    local photos = {}
    catalog:withReadAccessDo(function()
        for _, entry in ipairs(resolved) do photos[#photos + 1] = leerUna(entry.photo) end
    end)
    return { success = true, applied = #resolved, missing = missing, photos = photos }
end

return Handler
