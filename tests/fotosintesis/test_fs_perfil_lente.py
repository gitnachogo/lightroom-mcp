"""Ejecuta el Lua real de HandlerFSPerfilLente con un SDK simulado (Lupa 2.6, Lua 5.1).

python -m unittest tests/fotosintesis/test_fs_perfil_lente.py  (LUPA_PATH apunta a una carpeta con lupa si no esta instalada)
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

TAMRON = {'LensProfileEnable': 1, 'LensProfileSetup': 'Custom',
          'LensProfileName': 'Adobe (TAMRON 28-75mm F/2.8 Di III RXD A036, SONY)',
          'LensProfileFilename': 'SONY (TAMRON 28-75mm F_2.8 Di III RXD A036) - RAW.lcp',
          'LensProfileDigest': '6A91051894479F44BCA30295C4893668', 'LensProfileIsEmbedded': False,
          'LensProfileDistortionScale': 100, 'LensProfileVignettingScale': 8, 'AutoLateralCA': 1, 'Exposure2012': 0.3}
FE35 = {'name': 'Adobe (Sony FE 35mm F1.8)', 'filename': 'SONY (Sony FE 35mm F1.8) - RAW.lcp',
        'digest': '469B3440073BC214AAA2554FBD16B3AE'}

MOCK = r'''
local catalog = { writes = 0, reads = 0, inWrite = false, applied = {} }
function catalog:withReadAccessDo(f) self.reads = self.reads + 1; f() end
function catalog:withWriteAccessDo(name, f)
    self.writes = self.writes + 1; self.inWrite = true; f(); self.inWrite = false
end
local function photo(id)
    return {
        localIdentifier = id,
        getDevelopSettings = function() return SETTINGS[id] end,
        getFormattedMetadata = function(_, key)
            if key == 'lens' then return LENSES[id] end
            return key == 'cameraMake' and 'SONY' or 'ILCE-7M4'
        end,
        applyDevelopSettings = function(_, s, history)
            assert(catalog.inWrite, 'applyDevelopSettings outside write access')
            catalog.applied[#catalog.applied + 1] = { id = id, history = history }
            for k, v in pairs(s) do SETTINGS[id][k] = v end
        end,
    }
end
function import(name)
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


@unittest.skipUnless(LuaRuntime, 'Requiere lupa==2.6 (LUPA_PATH)')
class PerfilLente(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        g = self.lua.globals()
        g.SETTINGS = self.lua.table_from({1: self.lua.table_from(TAMRON), 2: self.lua.table_from(TAMRON)})
        g.LENSES = self.lua.table_from({1: 'FE 35mm F1.8', 2: 'E 28-75mm F2.8-2.8'})
        self.lua.execute(MOCK)
        self.h = self.lua.execute((PLUGIN / 'HandlerFSPerfilLente.lua').read_text(encoding='utf-8'))
        self.world = g.world

    def t(self, v):
        if isinstance(v, dict):
            return self.lua.table_from({k: self.t(x) for k, x in v.items()})
        if isinstance(v, list):
            return self.lua.table_from([self.t(x) for x in v])
        return v

    def fijar(self, items):
        return self.h.fijar(self.t({'items': items}))

    def ajustes(self, pid):
        return dict(self.lua.globals().SETTINGS[pid].items())

    def test_leer_solo_lectura(self):
        r = self.h.leer(self.t({'photo_ids': [1, 99]}))
        f = r['photos'][1]
        self.assertEqual((f['id'], f['lente'], f['marca'], f['camara'], f['LensProfileSetup'], f['LensProfileVignettingScale']),
                         (1, 'FE 35mm F1.8', 'SONY', 'ILCE-7M4', 'Custom', 8))
        self.assertIsNone(f['Exposure2012'])  # solo devuelve campos de lente
        self.assertEqual(list(r['missing'].values()), ['99'])
        self.assertEqual(self.world['writes'], 0)

    def test_fijar_custom_con_escala(self):
        r = self.fijar([dict(photo_id=1, vignetting_scale=8, **FE35), dict(photo_id=2, vignetting_scale=50,
                        name=TAMRON['LensProfileName'], filename=TAMRON['LensProfileFilename'])])
        self.assertTrue(r['success'])
        self.assertEqual(r['applied'], 2)
        self.assertEqual(self.world['writes'], 1)  # una sola transaccion
        a = self.ajustes(1)
        self.assertEqual((a['LensProfileSetup'], a['LensProfileName'], a['LensProfileDigest'],
                          a['LensProfileVignettingScale'], a['LensProfileDistortionScale']),
                         ('Custom', FE35['name'], FE35['digest'], 8, 100))
        self.assertEqual((a['AutoLateralCA'], a['Exposure2012']), (1, 0.3))  # no toca nada mas
        b = self.ajustes(2)
        self.assertEqual(b['LensProfileVignettingScale'], 50)
        self.assertEqual(b['LensProfileDigest'], TAMRON['LensProfileDigest'])  # sin digest: no se envia
        self.assertEqual(r['photos'][1]['LensProfileName'], FE35['name'])  # relectura

    def test_lens_defaults(self):
        self.fijar([{'photo_id': 1, 'modo': 'lens_defaults'}])
        a = self.ajustes(1)
        self.assertEqual((a['LensProfileSetup'], a['LensProfileEnable']), ('LensDefaults', 1))

    def test_todo_o_nada(self):
        malos = [
            [dict(photo_id=1, vignetting_scale=8, name=FE35['name'])],                   # sin archivo
            [dict(photo_id=1, vignetting_scale=8.5, **FE35)],                            # escala no entera
            [dict(photo_id=1, vignetting_scale=201, **FE35)],                            # fuera de rango
            [dict(photo_id=1, **FE35)],                                                  # sin escala
            [dict(photo_id=1, vignetting_scale=8, modo='auto', **FE35)],                 # modo desconocido
            [dict(photo_id=1, vignetting_scale=8, **{**FE35, 'digest': 'zz'})],          # digest no hex
            [dict(photo_id=1, vignetting_scale=8, **FE35), dict(photo_id=1, vignetting_scale=8, **FE35)],
            [dict(photo_id=2, vignetting_scale=8, **FE35), dict(vignetting_scale=8, **FE35)],  # sin id
            [],
        ]
        for items in malos:
            with self.subTest(items=items), self.assertRaises(LuaError):
                self.fijar(items)
        r = self.fijar([dict(photo_id=1, vignetting_scale=8, **FE35), dict(photo_id=99, vignetting_scale=8, **FE35)])
        self.assertFalse(r['success'])
        self.assertEqual(list(r['missing'].values()), ['99'])
        self.assertEqual(self.world['writes'], 0)
        self.assertEqual(self.ajustes(1)['LensProfileName'], TAMRON['LensProfileName'])


if __name__ == '__main__':
    unittest.main()
