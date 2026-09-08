import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

MODULE_PATH = Path(__file__).resolve().parents[1] / 'open_webui' / 'utils' / 'skills_runtime.py'


def load_module():
    spec = importlib.util.spec_from_file_location('skills_runtime_under_test', MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sr = load_module()


def test_module_imports_without_open_webui_side_effects():
    before = {name for name in sys.modules if name == 'open_webui' or name.startswith('open_webui.')}
    load_module()
    after = {name for name in sys.modules if name == 'open_webui' or name.startswith('open_webui.')}
    assert after == before


def make_req(**overrides):
    req = {
        'bins': [],
        'any_bins': [],
        'env': [],
        'config': [],
        'os': [],
        'always': False,
        'install': [],
        'emoji': None,
    }
    req.update(overrides)
    return req


####################
# Meta accessors
####################


def test_openclaw_meta_from_dict_skill():
    assert sr.openclaw_meta({'meta': {'openclaw': {'files': {}}}}) == {'files': {}}
    assert sr.openclaw_meta({'meta': {}}) == {}
    assert sr.openclaw_meta({'meta': {'openclaw': 'not-a-dict'}}) == {}
    assert sr.openclaw_meta({}) == {}


def test_openclaw_meta_from_object_skill():
    skill = SimpleNamespace(meta=SimpleNamespace(openclaw={'env': {'A': '1'}}))
    assert sr.openclaw_meta(skill) == {'env': {'A': '1'}}
    assert sr.openclaw_meta(SimpleNamespace(meta=None)) == {}
    assert sr.openclaw_meta(SimpleNamespace()) == {}


def test_oc_bag_accessors():
    oc = {
        'frontmatter': {'license': 'MIT'},
        'config': {'a': 1},
        'env': {'E': 'v'},
        'files': {'run.sh': {'content': 'x'}},
    }
    assert sr.openclaw_frontmatter(oc) == {'license': 'MIT'}
    assert sr.skill_config(oc) == {'a': 1}
    assert sr.skill_env(oc) == {'E': 'v'}
    assert sr.skill_files(oc) == {'run.sh': {'content': 'x'}}
    assert sr.openclaw_frontmatter({}) == {}
    assert sr.skill_config(None) == {}
    assert sr.skill_env({'env': 'nope'}) == {}


####################
# gating_requirements
####################


def test_gating_requirements_full_normalization():
    frontmatter = {
        'license': 'MIT',
        'metadata': {
            'openclaw': {
                'requires': {
                    'bins': ['gemini'],
                    'anyBins': 'fd',
                    'env': ['GEMINI_API_KEY'],
                    'config': ['a.b'],
                },
                'os': ['linux'],
                'always': True,
                'emoji': '♊️',
                'install': [{'kind': 'brew', 'formula': 'gemini-cli'}, 'junk', None],
            }
        },
    }
    req = sr.gating_requirements(frontmatter)
    assert req == {
        'bins': ['gemini'],
        'any_bins': ['fd'],
        'env': ['GEMINI_API_KEY'],
        'config': ['a.b'],
        'os': ['linux'],
        'always': True,
        'install': [{'kind': 'brew', 'formula': 'gemini-cli'}],
        'emoji': '♊️',
    }


def test_gating_requirements_defaults():
    assert sr.gating_requirements({}) == make_req()
    assert sr.gating_requirements({'metadata': {'openclaw': None}}) == make_req()
    assert sr.gating_requirements({'metadata': 'junk'}) == make_req()
    assert sr.gating_requirements({'metadata': {'openclaw': {'requires': 'junk'}}}) == make_req()


####################
# Probe script build + parse
####################


def test_build_probe_script_and_parse_round_trip():
    req = make_req(bins=['gemini', 'gemini', 'git'], any_bins=['fd'], env=['GEMINI_API_KEY'])
    script = sr.build_probe_script(req)
    assert script.count('command -v gemini') == 1  # deduplicated
    assert 'command -v git' in script
    assert 'command -v fd' in script
    assert 'printenv GEMINI_API_KEY' in script
    assert script.startswith("echo OS $(uname -s | tr '[:upper:]' '[:lower:]')")
    assert 'echo BIN gemini 1' in script and 'echo BIN gemini 0' in script
    assert 'echo ENV GEMINI_API_KEY 1' in script

    output = 'OS Linux\nBIN gemini 1\nBIN git 0\nBIN fd 1\nENV GEMINI_API_KEY 0\n'
    probe = sr.parse_probe_output(output, req)
    assert probe == {
        'os': 'linux',
        'bins': {'gemini': True, 'git': False, 'fd': True},
        'env': {'GEMINI_API_KEY': False},
    }


def test_build_probe_script_skips_invalid_names():
    req = make_req(bins=['ok-bin', 'bad;rm', ''], env=['GOOD_ENV', 'bad name'])
    script = sr.build_probe_script(req)
    assert 'ok-bin' in script
    assert 'GOOD_ENV' in script
    assert 'bad;rm' not in script
    assert 'bad name' not in script


def test_parse_probe_output_tolerates_wrapping():
    req = make_req(bins=['gemini'], env=['TOKEN'])
    output = '{"stdout": "OS darwin\\nBIN gemini 0\\nENV TOKEN 1\\n", "exit_code": 0}'
    probe = sr.parse_probe_output(output, req)
    assert probe == {'os': 'darwin', 'bins': {'gemini': False}, 'env': {'TOKEN': True}}


def test_parse_probe_output_ignores_undeclared_names():
    req = make_req(bins=['declared'])
    probe = sr.parse_probe_output('BIN other 1\nBIN declared 1', req)
    assert probe['bins'] == {'declared': True}


####################
# evaluate_gating
####################


def test_evaluate_gating_passes_when_all_met():
    req = make_req(bins=['git'], env=['E'], config=['a.b'])
    probe = {'os': 'linux', 'bins': {'git': True}, 'env': {'E': True}}
    assert sr.evaluate_gating(req, probe, {'a': {'b': 1}}) == []


def test_evaluate_gating_missing_bin():
    req = make_req(bins=['git', 'fd'])
    probe = {'os': 'linux', 'bins': {'git': True, 'fd': False}, 'env': {}}
    assert sr.evaluate_gating(req, probe, {}) == ['bin:fd']


def test_evaluate_gating_any_bins():
    req = make_req(any_bins=['fd', 'find'])
    satisfied = {'os': 'linux', 'bins': {'fd': False, 'find': True}, 'env': {}}
    assert sr.evaluate_gating(req, satisfied, {}) == []
    missing = {'os': 'linux', 'bins': {'fd': False, 'find': False}, 'env': {}}
    assert sr.evaluate_gating(req, missing, {}) == ['anyBin:fd|find']


def test_evaluate_gating_missing_env():
    req = make_req(env=['A', 'B'])
    probe = {'os': 'linux', 'bins': {}, 'env': {'A': True, 'B': False}}
    assert sr.evaluate_gating(req, probe, {}) == ['env:B']


def test_evaluate_gating_config_dot_paths():
    req = make_req(config=['a.b', 'a.c', 'missing'])
    probe = {'os': 'linux', 'bins': {}, 'env': {}}
    assert sr.evaluate_gating(req, probe, {'a': {'b': 1, 'c': 0}}) == ['config:a.c', 'config:missing']
    assert sr.evaluate_gating(req, probe, 'not-a-dict') == ['config:a.b', 'config:a.c', 'config:missing']


def test_evaluate_gating_os_match_and_mismatch():
    req = make_req(os=['linux', 'darwin'])
    assert sr.evaluate_gating(req, {'os': 'linux', 'bins': {}, 'env': {}}, {}) == []
    assert sr.evaluate_gating(req, {'os': 'freebsd', 'bins': {}, 'env': {}}, {}) == ['os:other']


def test_evaluate_gating_win32_requirement_never_matches():
    req = make_req(os=['win32'])
    assert sr.evaluate_gating(req, {'os': 'linux', 'bins': {}, 'env': {}}, {}) == ['os:linux']


def test_evaluate_gating_always_exempts_requires_when_os_ok():
    req = make_req(os=['linux'], always=True, bins=['missing'], env=['NOPE'], config=['x.y'])
    probe = {'os': 'linux', 'bins': {}, 'env': {}}
    assert sr.evaluate_gating(req, probe, {}) == []


def test_evaluate_gating_always_exempts_when_os_list_empty():
    req = make_req(always=True, bins=['missing'])
    probe = {'os': 'linux', 'bins': {}, 'env': {}}
    assert sr.evaluate_gating(req, probe, {}) == []


def test_evaluate_gating_always_does_not_exempt_when_os_fails():
    req = make_req(os=['linux'], always=True, bins=['missing'], config=['x'])
    probe = {'os': 'darwin', 'bins': {'missing': False}, 'env': {}}
    assert sr.evaluate_gating(req, probe, {}) == ['os:darwin', 'bin:missing', 'config:x']


def test_evaluate_gating_without_probe_only_checks_config():
    req = make_req(os=['linux'], bins=['git'], env=['E'], config=['a.b'])
    assert sr.evaluate_gating(req, None, {'a': {'b': 'yes'}}) == []
    assert sr.evaluate_gating(req, None, {'a': {}}) == ['config:a.b']


####################
# Files / env helpers
####################


def test_substitute_basedir():
    assert (
        sr.substitute_basedir('run {baseDir}/scripts/run.sh from {baseDir}', '/tmp/s')
        == 'run /tmp/s/scripts/run.sh from /tmp/s'
    )
    assert sr.substitute_basedir('no placeholder', '/tmp/s') == 'no placeholder'


def test_sanitize_skill_file_path():
    assert sr.sanitize_skill_file_path('scripts/run.sh') == 'scripts/run.sh'
    assert sr.sanitize_skill_file_path('./run.sh') == 'run.sh'
    assert sr.sanitize_skill_file_path('a//b.txt') == 'a/b.txt'
    assert sr.sanitize_skill_file_path('../x') is None
    assert sr.sanitize_skill_file_path('a/../b') is None
    assert sr.sanitize_skill_file_path('/abs/path') is None
    assert sr.sanitize_skill_file_path('a\\b') is None
    assert sr.sanitize_skill_file_path('') is None
    assert sr.sanitize_skill_file_path('   ') is None
    assert sr.sanitize_skill_file_path(None) is None


def test_files_fingerprint_is_order_independent():
    files_a = {'b.sh': {'content': '2', 'encoding': 'utf-8'}, 'a.sh': {'content': '1', 'encoding': 'utf-8'}}
    files_b = {'a.sh': {'encoding': 'utf-8', 'content': '1'}, 'b.sh': {'encoding': 'utf-8', 'content': '2'}}
    assert sr.files_fingerprint(files_a) == sr.files_fingerprint(files_b)


def test_files_fingerprint_includes_env():
    files = {'a.sh': {'content': '1'}}
    assert sr.files_fingerprint(files) == sr.files_fingerprint(files, {})
    assert sr.files_fingerprint(files, {'A': '1'}) != sr.files_fingerprint(files, {'A': '2'})
    assert sr.files_fingerprint(files, {'A': '1'}) != sr.files_fingerprint(files)


def test_build_env_file():
    env = {'GOOD_KEY': 'v1', 'bad key': 'x', '1BAD': 'y', 'MULTI': 'a\nb\rc'}
    assert sr.build_env_file(env) == 'GOOD_KEY=v1\nMULTI=a b c\n'
    assert sr.build_env_file({}) == ''
    assert sr.build_env_file({'EMPTY': None}) == 'EMPTY=\n'


def test_build_env_guidance():
    guidance = sr.build_env_guidance('/tmp/openwebui-skills/s1')
    assert 'set -a; . /tmp/openwebui-skills/s1/env; set +a' in guidance


####################
# build_install_command
####################


def test_build_install_command_brew():
    assert sr.build_install_command({'kind': 'brew', 'formula': 'gemini-cli'}, 's1') == 'brew install gemini-cli'
    assert sr.build_install_command({'kind': 'brew'}, 's1') is None


def test_build_install_command_node_managers():
    spec = {'kind': 'node', 'package': '@google/gemini-cli'}
    assert sr.build_install_command(spec, 's1') == 'npm i -g @google/gemini-cli'
    assert sr.build_install_command(spec, 's1', node_manager='npm') == 'npm i -g @google/gemini-cli'
    assert sr.build_install_command(spec, 's1', node_manager='pnpm') == 'pnpm add -g @google/gemini-cli'
    assert sr.build_install_command(spec, 's1', node_manager='yarn') == 'yarn add -g @google/gemini-cli'
    assert sr.build_install_command(spec, 's1', node_manager='bun') == 'bun add -g @google/gemini-cli'


def test_build_install_command_go():
    assert (
        sr.build_install_command({'kind': 'go', 'module': 'example.com/mod'}, 's1')
        == 'go install example.com/mod@latest'
    )
    assert (
        sr.build_install_command({'kind': 'go', 'module': 'example.com/mod@latest'}, 's1')
        == 'go install example.com/mod@latest'
    )
    assert (
        sr.build_install_command({'kind': 'go', 'module': 'example.com/mod', 'version': 'v1.2.3'}, 's1')
        == 'go install example.com/mod@v1.2.3'
    )
    assert sr.build_install_command({'kind': 'go'}, 's1') is None


def test_build_install_command_uv():
    assert sr.build_install_command({'kind': 'uv', 'package': 'ruff'}, 's1') == 'uv tool install ruff'


def test_build_install_command_download_zip():
    cmd = sr.build_install_command({'kind': 'download', 'url': 'https://x.test/y.zip'}, 's1')
    assert cmd.startswith('curl -fSL https://x.test/y.zip -o /tmp/openwebui-skill-s1-download')
    assert 'mkdir -p $HOME/.openwebui/tools/s1' in cmd
    assert 'unzip -o /tmp/openwebui-skill-s1-download -d $HOME/.openwebui/tools/s1' in cmd
    assert 'python3 -c' in cmd


def test_build_install_command_download_tar_with_sha256():
    spec = {'kind': 'download', 'url': 'https://x.test/y.tar.gz', 'sha256': 'ab12', 'targetDir': '~/tools/s1'}
    cmd = sr.build_install_command(spec, 's1')
    assert "echo 'ab12  /tmp/openwebui-skill-s1-download' | sha256sum -c -" in cmd
    assert 'tar -xzf /tmp/openwebui-skill-s1-download -C $HOME/tools/s1' in cmd


def test_build_install_command_download_explicit_archive():
    spec = {'kind': 'download', 'url': 'https://x.test/download', 'archive': 'tar.bz2'}
    cmd = sr.build_install_command(spec, 's1')
    assert 'tar -xjf' in cmd
    assert sr.build_install_command({'kind': 'download', 'url': 'https://x.test/f', 'archive': 'rar'}, 's1') is None
    assert sr.build_install_command({'kind': 'download'}, 's1') is None


def test_build_install_command_quotes_interpolations():
    assert sr.build_install_command({'kind': 'node', 'package': 'a b'}, 's1') == "npm i -g 'a b'"
    assert sr.build_install_command({'kind': 'brew', 'formula': 'f;rm -rf /'}, 's1') == "brew install 'f;rm -rf /'"


def test_build_install_command_unknown_kind():
    assert sr.build_install_command({'kind': 'apt', 'package': 'x'}, 's1') is None
    assert sr.build_install_command(None, 's1') is None
