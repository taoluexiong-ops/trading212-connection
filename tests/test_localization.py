import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
RESOURCES=ROOT/'app/Trading212 Connection.app/Contents/Resources/Localization'

class LocalizationTests(unittest.TestCase):
    def test_catalogs_are_complete_and_preserve_substitutions(self):
        manifest=json.loads((RESOURCES/'languages.json').read_text())
        base=json.loads((RESOURCES/'zh-Hans.json').read_text())
        self.assertEqual(manifest['defaultLanguage'],'zh-Hans')
        self.assertEqual(len({x['id'] for x in manifest['languages']}),len(manifest['languages']))
        for language in manifest['languages']:
            translated=json.loads((RESOURCES/language['file']).read_text())
            self.assertEqual(set(base),set(translated))
            for key,value in translated.items():
                self.assertTrue(value)
                self.assertEqual(sorted(re.findall(r'\{\d+\}',base[key])),sorted(re.findall(r'\{\d+\}',value)),key)
                if language['id']=='en':self.assertIsNone(re.search(r'[\u4e00-\u9fff]',value),key)

    def test_all_native_lookup_keys_exist(self):
        source=(ROOT/'Manager.swift').read_text()
        base=json.loads((RESOURCES/'zh-Hans.json').read_text())
        for literal in re.findall(r'L\(("(?:\\.|[^"\\])*")',source):
            self.assertIn(json.loads(literal),base)

    def test_native_runtime_persistence_errors_and_extension(self):
        with tempfile.TemporaryDirectory() as temporary:
            binary=Path(temporary)/'localization-checks'
            subprocess.run(['swiftc','-module-cache-path',str(ROOT/'.build/swift-cache'),str(ROOT/'Localization.swift'),str(Path(__file__).with_name('LocalizationChecks.swift')),'-o',str(binary)],check=True,capture_output=True,text=True)
            result=subprocess.run([str(binary),str(RESOURCES),temporary],check=True,capture_output=True,text=True)
            self.assertIn('26 localization runtime checks passed',result.stdout)
