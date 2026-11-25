-- Fotosintesis: estado de Denoise IA por foto y recalculo de IA.
--
-- Lightroom guarda Denoise como un filtro "Enhance" en FilterList. applyDevelopPreset copia el del
-- preset como plantilla: sin Images ni GroupDigest y con las dimensiones de la foto con la que se
-- creo el preset. Solo queda calculado (Images con GroupDigest, dimensiones de la foto) cuando
-- Lightroom "actualiza la configuracion de IA" de esa foto.
--
-- fs_estado_denoise: solo lectura, sin Revelar ni cambio de modulo.
-- fs_actualizar_ia: llama a photo:updateAISettings() en una transaccion; no espera al calculo.
local LrApplication = import 'LrApplication'
local LrTasks = import 'LrTasks'
local PhotoLookup = require 'PhotoLookup'
local Handler = {}

local MAX_LEER = 100
local MAX_ACTUALIZAR = 20

local function requireIds(ids, max)
    if type(ids) ~= 'table' or #ids == 0 or #ids > max then
        error(string.format('photo_ids must contain 1 to %d photo IDs', max))
    end
end

-- Busca una clave no vacia en una tabla anidada, con profundidad limitada.
local function contieneClave(t, clave, nivel)
    if type(t) ~= 'table' or nivel > 8 then return false end
    for k, v in pairs(t) do
        if k == clave and type(v) == 'string' and v ~= '' then return true end
        if type(v) == 'table' and contieneClave(v, clave, nivel + 1) then return true end
    end
    return false
end

local function numero(v) return type(v) == 'number' and v or nil end

local function filtroEnhance(filter)
    local imagenes = type(filter.Images) == 'table' and #filter.Images or 0
    local out = {
        ajustes = type(filter.CompressedSettings) == 'string' and filter.CompressedSettings or nil,
        imagenes = imagenes,
        group_digest = imagenes > 0 and contieneClave(filter.Images, 'GroupDigest', 1),
    }
    local l, r = numero(filter.DstBoundsLeft), numero(filter.DstBoundsRight)
    local t, b = numero(filter.DstBoundsTop), numero(filter.DstBoundsBottom)
    if l and r then out.ancho_destino = r - l end
    if t and b then out.alto_destino = b - t end
    if imagenes == 0 then out.estado = 'plantilla'
    elseif out.group_digest then out.estado = 'calculado'
    else out.estado = 'desconocido' end
    return out
end

local function leerUna(photo)
    local out = { id = photo.localIdentifier, estado = 'sin_denoise', filtros = {} }
    local dims = photo:getRawMetadata('dimensions')
    if type(dims) == 'table' then
        out.ancho_foto, out.alto_foto = numero(dims.width), numero(dims.height)
    end
    local settings = photo:getDevelopSettings()
    local lista = type(settings) == 'table' and settings.FilterList
    local filtros = type(lista) == 'table' and lista.Filters or nil
    -- Una foto sin filtros guarda FilterList = {} (sin Filters).
    if (lista ~= nil and type(lista) ~= 'table') or (filtros ~= nil and type(filtros) ~= 'table') then
        out.estado = 'desconocido'
        return out
    end
    for _, filter in ipairs(filtros or {}) do
        if type(filter) == 'table' and filter.Name == 'Enhance' then
            out.filtros[#out.filtros + 1] = filtroEnhance(filter)
        end
    end
    if #out.filtros == 1 then
        local f = out.filtros[1]
        out.estado = f.estado
        out.ajustes, out.ancho_destino, out.alto_destino = f.ajustes, f.ancho_destino, f.alto_destino
    elseif #out.filtros > 1 then
        out.estado = 'desconocido'
    end
    return out
end

function Handler.estado(args)
    if type(args) ~= 'table' then error('args must be an object') end
    requireIds(args.photo_ids, MAX_LEER)
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

function Handler.actualizarIA(args)
    if type(args) ~= 'table' then error('args must be an object') end
    requireIds(args.photo_ids, MAX_ACTUALIZAR)
    local catalog = LrApplication.activeCatalog()
    local resolved = PhotoLookup.resolveMany(catalog, args.photo_ids)
    local fotos, missing, fallidas = {}, {}, {}
    for _, entry in ipairs(resolved) do
        if entry.photo then fotos[#fotos + 1] = entry
        else missing[#missing + 1] = tostring(entry.id) end
    end
    if #fotos > 0 and type(fotos[1].photo.updateAISettings) ~= 'function' then
        return { success = false, soportado = false, lanzadas = 0, fallidas = fallidas, missing = missing,
                 message = 'updateAISettings needs Lightroom Classic 13.3 or later' }
    end
    local lanzadas = 0
    if #fotos > 0 then
        catalog:withWriteAccessDo('Actualizar IA (Fotosintesis)', function()
            for _, entry in ipairs(fotos) do
                local ok, err = LrTasks.pcall(function() entry.photo:updateAISettings() end)
                if ok then lanzadas = lanzadas + 1
                else fallidas[#fallidas + 1] = { id = tostring(entry.id), error = tostring(err) } end
            end
        end)
    end
    return { success = #fallidas == 0 and #missing == 0, soportado = true, lanzadas = lanzadas,
             fallidas = fallidas, missing = missing }
end

return Handler
