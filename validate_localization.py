"""Validate packaged language metadata, coverage and positional substitutions."""
import json
from pathlib import Path
import re

resources = Path(__file__).resolve().parent / 'app/Trading212 Connection.app/Contents/Resources/Localization'
manifest = json.loads((resources / 'languages.json').read_text())
entries = manifest['languages']
assert entries and len({x['id'] for x in entries}) == len(entries), 'Duplicate or empty languages'
assert manifest['defaultLanguage'] in {x['id'] for x in entries}, 'Default language is not registered'
catalogs = {}
for entry in entries:
    assert entry['id'] and entry['name'], 'Language metadata is incomplete'
    assert Path(entry['file']).name == entry['file'], 'Catalog must be a simple file name'
    catalogs[entry['id']] = json.loads((resources / entry['file']).read_text())
base = catalogs[manifest['defaultLanguage']]
for language, catalog in catalogs.items():
    assert set(catalog) == set(base), 'Incomplete keys: ' + language
    for key, value in catalog.items():
        assert isinstance(value, str) and value, 'Empty translation: ' + key
        assert sorted(re.findall(r'\{\d+\}', value)) == sorted(re.findall(r'\{\d+\}', base[key])), 'Placeholder mismatch: ' + language + ' / ' + key
print('Validated %d languages and %d keys per language.' % (len(catalogs), len(base)))
