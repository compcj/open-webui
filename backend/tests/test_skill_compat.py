from copy import deepcopy
import importlib.util
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]


def helper():
    path = ROOT / 'open_webui/utils/skill_compat.py'
    spec = importlib.util.spec_from_file_location('compat_under_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def legacy():
    return {
        'name': 'Demo',
        'description': 'desc',
        'content': 'Old body\n\n',
        'meta': {
            'unknown': {'keep': True},
            'openclaw': {
                'frontmatter': {'license': 'MIT', 'metadata': {'openclaw': {'requires': {'bins': ['uv']}}}},
                'files': {
                    'run.sh': {'content': 'echo ok\n', 'encoding': 'utf-8'},
                    'asset.bin': {'content': 'AP8=', 'encoding': 'base64'},
                },
                'source': {'type': 'zip', 'extra': 1},
                'config': {'flag': True},
                'env': {'DEMO': 'value'},
                'future': [1],
            },
        },
        'data': {'files': [{'path': 'SKILL.md', 'content': 'Old body\n\n'}]},
    }


def test_legacy_conversion_preserves_metadata_and_bytes_without_mutation():
    value = legacy()
    before = deepcopy(value)
    converted = helper().canonical_snapshot('demo', value)
    assert value == before
    assert converted['meta']['unknown'] == {'keep': True}
    oc = converted['meta']['openclaw']
    assert 'files' not in oc and 'frontmatter' not in oc
    assert oc['source']['extra'] == 1 and oc['future'] == [1]
    files = {f['path']: f for f in converted['data']['files']}
    assert files['run.sh'] == {'path': 'run.sh', 'content': 'echo ok\n'}
    assert files['asset.bin']['content'] == 'AP8='
    assert 'license: MIT' in files['SKILL.md']['content']
    assert files['SKILL.md']['content'].endswith('Old body\n')
    assert helper().canonical_snapshot('demo', converted) == converted


def test_canonical_collision_aborts_with_skill_and_path():
    value = legacy()
    value['data']['files'].append({'path': 'run.sh', 'content': 'different'})
    with pytest.raises(ValueError, match='demo.*run.sh'):
        helper().canonical_snapshot('demo', value)


def test_identical_duplicate_is_deduplicated():
    value = legacy()
    value['data']['files'].append({'path': 'run.sh', 'content': 'echo ok\n'})
    assert len([f for f in helper().canonical_snapshot('demo', value)['data']['files'] if f['path'] == 'run.sh']) == 1


def test_invalid_legacy_data_aborts_without_silently_skipping():
    for path, info in [('../oops', {'content': 'x'}), ('bad.bin', {'encoding': 'base64', 'content': '??'})]:
        value = legacy()
        value['meta']['openclaw']['files'] = {path: info}
        with pytest.raises(ValueError, match='demo'):
            helper().canonical_snapshot('demo', value)


def test_valid_new_root_never_overwritten_by_legacy_metadata():
    value = legacy()
    value['data']['files'][0]['content'] = '---\nname: new\nlicense: Apache\n---\nnew body'
    with pytest.raises(ValueError, match='demo.*SKILL.md'):
        helper().canonical_snapshot('demo', value)


def test_existing_canonical_root_with_mirrored_content_is_never_double_wrapped():
    value = legacy()
    root = '---\nname: demo\ndescription: desc\nlicense: MIT\nmetadata:\n  openclaw:\n    requires:\n      bins: [uv]\n---\nbody\n'
    value['content'] = root
    value['data']['files'][0]['content'] = root
    assert helper().canonical_snapshot('demo', value)['content'] == root
    value['meta']['openclaw']['frontmatter']['license'] = 'different'
    with pytest.raises(ValueError, match='demo.*SKILL.md'):
        helper().canonical_snapshot('demo', value)
