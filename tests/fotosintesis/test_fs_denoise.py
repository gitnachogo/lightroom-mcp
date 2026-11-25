"""Ejecuta el Lua real de HandlerFSDenoise con un SDK simulado (Lupa 2.6, Lua 5.1).

python -m unittest tests/fotosintesis/test_fs_denoise.py  (LUPA_PATH apunta a una carpeta con lupa si no esta instalada)
"""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / 'plugin' / 'LightroomMCP.lrplugin'
if os.environ.get('LUPA_PATH'):
    sys.path.insert(0, os.environ['LUPA_PATH'])
try:
    from lupa.lua51 import LuaError, LuaRuntime
except ImportError:
    LuaRuntime = None

MOCK = r'''
local catalog = { writes = 0, reads = 0, inWrite = false, updated = {} }
function catalog:withReadAccessDo(f) self.reads = self.reads + 1; f() end
function catalog:withWriteAccessDo(name, f)
    self.writes = self.writes + 1; self.inWrite = true; f(); self.inWrite = false
end
local function photo(id)
    local p = {
        localIdentifier = id,
        getDevelopSettings = function() return SETTINGS[id] end,
        getRawMetadata = function(_, key) assert(key == 'dimensions', key); return { width = 7008, height = 4672 } end,
    }
    if not SIN_IA then
        p.updateAISettings = function()
            assert(catalog.inWrite, 'updateAISettings outside write access')
            if id == 3 then error('boom') end
            catalog.updated[#catalog.updated + 1] = id
        end
    end
    return p
end
function import(name)
    if name == 'LrTasks' then return { pcall = pcall } end
    assert(name == 'LrApplication', name)
    return { activeCatalog = function() return catalog end }
end
package.preload.PhotoLookup = function() return { resolveMany = function(_, ids)
    local out = {}
    for i, id in ipairs(ids) do
        local n = tonumber(id)
        out[i] = { id = id, photo = SETTINGS[n] and photo(n) or nil }
    end
    return out
end } end
world = catalog
'''

PLANTILLA = {'Name': 'Enhance', 'CompressedSettings': 'BACB0284', 'DstBoundsLeft': 0, 'DstBoundsRight': 6048,
             'DstBoundsTop': 0, 'DstBoundsBottom': 4024}
CALCULADO = {**PLANTILLA, 'DstBoundsRight': 7008, 'DstBoundsBottom': 4672,
             'Images': [{'ColorVariations': [{'ImageGroup': [{'GroupDigest': 'B50E003F'}]}]}]}
SIN_DIGEST = {**CALCULADO, 'Images': [{'ColorVariations': [{}]}]}


@unittest.skipUnless(LuaRuntime, 'Requiere lupa==2.6 (LUPA_PATH)')
class EstadoDenoise(unittest.TestCase):
    def setUp(self, sin_ia=False):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        g = self.lua.globals()
        g.SIN_IA = sin_ia
        g.SETTINGS = self.t({
            1: {'Exposure2012': 0.3},
            2: {'FilterList': {'Filters': [PLANTILLA]}},
            3: {'FilterList': {'Filters': [{'Name': 'Otro'}, CALCULADO]}},
            4: {'FilterList': {'Filters': [SIN_DIGEST]}},
            5: {'FilterList': {'Filters': [PLANTILLA, CALCULADO]}},
            6: {'FilterList': 'raro'},
        })
        self.lua.execute(MOCK)
        self.h = self.lua.execute((PLUGIN / 'HandlerFSDenoise.lua').read_text(encoding='utf-8'))
        self.world = g.world

    def t(self, v):
        if isinstance(v, dict):
            return self.lua.table_from({k: self.t(x) for k, x in v.items()})
        if isinstance(v, list):
            return self.lua.table_from([self.t(x) for x in v])
        return v

    def estados(self, ids):
        r = self.h.estado(self.t({'photo_ids': ids}))
        return r, {f['id']: f for f in r['photos'].values()}

    def test_clasifica_sin_escribir(self):
        r, f = self.estados([1, 2, 3, 4, 5, 6, 99])
        self.assertEqual({k: v['estado'] for k, v in f.items()},
                         {1: 'sin_denoise', 2: 'plantilla', 3: 'calculado', 4: 'desconocido',
                          5: 'desconocido', 6: 'desconocido'})
        self.assertEqual((f[2]['ancho_destino'], f[2]['alto_destino'], f[2]['ancho_foto'], f[2]['ajustes']),
                         (6048, 4024, 7008, 'BACB0284'))
        self.assertEqual((f[3]['ancho_destino'], f[3]['filtros'][1]['imagenes'], f[3]['filtros'][1]['group_digest']),
                         (7008, 1, True))
        self.assertEqual(list(r['missing'].values()), ['99'])
        self.assertEqual((self.world['writes'], self.world['reads']), (0, 1))

    def test_limites(self):
        for ids in ([], list(range(1, 102))):
            with self.subTest(n=len(ids)), self.assertRaises(LuaError):
                self.h.estado(self.t({'photo_ids': ids}))
        with self.assertRaises(LuaError):
            self.h.actualizarIA(self.t({'photo_ids': list(range(1, 22))}))

    def test_actualizar_ia(self):
        r = self.h.actualizarIA(self.t({'photo_ids': [2, 3, 99]}))
        self.assertFalse(r['success'])
        self.assertEqual(r['lanzadas'], 1)
        self.assertEqual(r['fallidas'][1]['id'], '3')
        self.assertEqual(list(r['missing'].values()), ['99'])
        self.assertEqual(list(self.world['updated'].values()), [2])
        self.assertEqual(self.world['writes'], 1)  # una sola transaccion

    def test_sin_update_ai(self):
        self.setUp(sin_ia=True)
        r = self.h.actualizarIA(self.t({'photo_ids': [2]}))
        self.assertEqual((r['success'], r['soportado'], self.world['writes']), (False, False, 0))


if __name__ == '__main__':
    unittest.main()
