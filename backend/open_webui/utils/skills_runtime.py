"""Runtime support for OpenClaw-compatible skills.

Skill metadata may carry an ``openclaw`` bag (see ``SkillMeta.openclaw``) with
frontmatter gating requirements, bundled files, config, and env vars. This
module probes the configured Open Terminal server, evaluates the gating rules,
syncs bundled files to the terminal working directory, and builds install
commands for missing dependencies.

Only stdlib imports live at module level so the pure helpers can be unit
tested without importing the application (and its database side effects).
All ``open_webui`` imports are deferred into the async functions.
"""

import base64
import hashlib
import json
import logging
import posixpath
import re
import shlex

log = logging.getLogger(__name__)

_NAME_RE = re.compile(r'[A-Za-z0-9_.-]+')
_ENV_KEY_RE = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')

_OS_LINE_RE = re.compile(r'\bOS[ \t]+([A-Za-z0-9_.-]+)')
_BIN_LINE_RE = re.compile(r'\bBIN[ \t]+([A-Za-z0-9_.-]+)[ \t]+([01])\b')
_ENV_LINE_RE = re.compile(r'\bENV[ \t]+([A-Za-z0-9_.-]+)[ \t]+([01])\b')

_PROBE_OS_VALUES = ('linux', 'darwin')


class _DictUser:
    """Duck-typed user exposing .id/.role over a dict-shaped user."""

    def __init__(self, data: dict):
        self.id = data.get('id')
        self.role = data.get('role', 'user')


def _normalize_user(user):
    if user is None:
        return None
    if isinstance(user, dict):
        return _DictUser(user)
    return user


def _get(skill, key, default=None):
    if isinstance(skill, dict):
        return skill.get(key, default)
    return getattr(skill, key, default)


def _as_str_list(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


####################
# Meta accessors
####################


def openclaw_meta(skill) -> dict:
    """Return the ``meta.openclaw`` dict of a skill (model or dict), or {}."""
    meta = _get(skill, 'meta')
    if meta is None:
        return {}
    oc = meta.get('openclaw') if isinstance(meta, dict) else getattr(meta, 'openclaw', None)
    return oc if isinstance(oc, dict) else {}


def openclaw_frontmatter(oc: dict) -> dict:
    frontmatter = (oc or {}).get('frontmatter')
    return frontmatter if isinstance(frontmatter, dict) else {}


def skill_config(oc: dict) -> dict:
    config = (oc or {}).get('config')
    return config if isinstance(config, dict) else {}


def skill_env(oc: dict) -> dict:
    env = (oc or {}).get('env')
    return env if isinstance(env, dict) else {}


def skill_files(oc: dict) -> dict:
    files = (oc or {}).get('files')
    return files if isinstance(files, dict) else {}


####################
# Gating
####################


def gating_requirements(frontmatter: dict) -> dict:
    """Normalize ``frontmatter.metadata.openclaw`` gating declarations."""
    oc = (frontmatter or {}).get('metadata')
    oc = oc.get('openclaw') if isinstance(oc, dict) else None
    if not isinstance(oc, dict):
        oc = {}
    requires = oc.get('requires')
    if not isinstance(requires, dict):
        requires = {}
    install = oc.get('install')
    if not isinstance(install, list):
        install = []
    emoji = oc.get('emoji')
    return {
        'bins': _as_str_list(requires.get('bins')),
        'any_bins': _as_str_list(requires.get('anyBins')),
        'env': _as_str_list(requires.get('env')),
        'config': _as_str_list(requires.get('config')),
        'os': _as_str_list(oc.get('os')),
        'always': bool(oc.get('always')),
        'install': [spec for spec in install if isinstance(spec, dict)],
        'emoji': emoji if isinstance(emoji, str) else None,
    }


def has_gating(req: dict) -> bool:
    return bool(
        req.get('bins')
        or req.get('any_bins')
        or req.get('env')
        or req.get('config')
        or req.get('os')
        or req.get('always')
    )


def build_probe_script(req: dict) -> str:
    """Build a POSIX sh one-liner reporting OS, binary, and env availability."""
    parts = ["echo OS $(uname -s | tr '[:upper:]' '[:lower:]')"]
    seen = set()
    for name in [*(req.get('bins') or []), *(req.get('any_bins') or [])]:
        if name in seen or not isinstance(name, str) or not _NAME_RE.fullmatch(name):
            continue
        seen.add(name)
        quoted = shlex.quote(name)
        parts.append(f'if command -v {quoted} >/dev/null 2>&1; then echo BIN {name} 1; else echo BIN {name} 0; fi')
    for name in req.get('env') or []:
        if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
            continue
        quoted = shlex.quote(name)
        parts.append(f'if printenv {quoted} >/dev/null 2>&1; then echo ENV {name} 1; else echo ENV {name} 0; fi')
    return '; '.join(parts)


def parse_probe_output(output: str, req: dict) -> dict:
    """Parse probe output, tolerating surrounding JSON/text wrapping."""
    probe = {'os': None, 'bins': {}, 'env': {}}
    if not output:
        return probe
    # Tool results may arrive JSON-wrapped; normalize escaped whitespace so
    # marker lines stay detectable in both raw and encoded forms.
    text = output.replace('\\n', '\n').replace('\\r', '\n').replace('\\t', ' ')
    bin_names = {
        name
        for name in [*(req.get('bins') or []), *(req.get('any_bins') or [])]
        if isinstance(name, str) and _NAME_RE.fullmatch(name)
    }
    env_names = {name for name in req.get('env') or [] if isinstance(name, str) and _NAME_RE.fullmatch(name)}
    for match in _OS_LINE_RE.finditer(text):
        probe['os'] = match.group(1).lower()
    for match in _BIN_LINE_RE.finditer(text):
        if match.group(1) in bin_names:
            probe['bins'][match.group(1)] = match.group(2) == '1'
    for match in _ENV_LINE_RE.finditer(text):
        if match.group(1) in env_names:
            probe['env'][match.group(1)] = match.group(2) == '1'
    return probe


def _config_path_truthy(config: dict, path: str) -> bool:
    current = config
    for part in str(path).split('.'):
        if not isinstance(current, dict):
            return False
        current = current.get(part)
    return bool(current)


def evaluate_gating(req: dict, probe: dict | None, config: dict) -> list[str]:
    """Return unmet-requirement reasons; an empty list means the skill may run."""
    reasons = []

    os_ok = True
    os_list = req.get('os') or []
    if os_list and probe is not None:
        probed_os = probe.get('os')
        if probed_os not in _PROBE_OS_VALUES:
            probed_os = 'other' if probed_os else 'unknown'
        if probed_os not in os_list:
            reasons.append(f'os:{probed_os}')
            os_ok = False

    # `always` exempts requires.* only when the os constraint holds (or is absent).
    if req.get('always') and os_ok:
        return reasons

    if probe is not None:
        bins_available = probe.get('bins') or {}
        for name in req.get('bins') or []:
            if not bins_available.get(name):
                reasons.append(f'bin:{name}')
        any_bins = req.get('any_bins') or []
        if any_bins and not any(bins_available.get(name) for name in any_bins):
            reasons.append(f'anyBin:{"|".join(any_bins)}')
        env_available = probe.get('env') or {}
        for name in req.get('env') or []:
            if not env_available.get(name):
                reasons.append(f'env:{name}')

    config_bag = config if isinstance(config, dict) else {}
    for path in req.get('config') or []:
        if not _config_path_truthy(config_bag, path):
            reasons.append(f'config:{path}')

    return reasons


####################
# Files and env helpers
####################


def substitute_basedir(content: str, base_dir: str) -> str:
    if not isinstance(content, str):
        return content
    return content.replace('{baseDir}', base_dir)


def files_fingerprint(files: dict, env: dict | None = None) -> str:
    """Order-independent sha256 over bundled files (and env when given)."""
    canonical = json.dumps(
        {'files': files or {}, 'env': env or {}},
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def sanitize_skill_file_path(path: str) -> str | None:
    """Return a safe relative path, or None for absolute/traversing paths."""
    if not isinstance(path, str):
        return None
    path = path.strip()
    if not path or path.startswith('/') or '\\' in path or re.match(r'^[A-Za-z]:', path):
        return None
    parts = []
    for part in path.split('/'):
        if part in ('', '.'):
            continue
        if part == '..':
            return None
        parts.append(part)
    if not parts:
        return None
    return '/'.join(parts)


def build_env_file(env: dict) -> str:
    lines = []
    for key, value in (env or {}).items():
        if not isinstance(key, str) or not _ENV_KEY_RE.fullmatch(key):
            continue
        text = '' if value is None else str(value)
        text = text.replace('\r', ' ').replace('\n', ' ')
        lines.append(f'{key}={text}')
    return '\n'.join(lines) + ('\n' if lines else '')


def build_env_guidance(base_dir: str) -> str:
    return (
        f'Before running scripts from this skill, load its environment variables with '
        f'`set -a; . {base_dir}/env; set +a`.'
    )


####################
# Install commands
####################


def _quote_target_dir(path: str) -> str:
    if path == '$HOME':
        return path
    if path.startswith('$HOME/'):
        return '$HOME/' + shlex.quote(path[len('$HOME/') :])
    return shlex.quote(path)


def build_install_command(spec: dict, skill_id: str, node_manager: str = 'npm') -> str | None:
    """Build a shell command for one install spec, or None if unsupported."""
    if not isinstance(spec, dict):
        return None
    kind = spec.get('kind')
    safe_skill_id = re.sub(r'[^A-Za-z0-9_.-]', '_', str(skill_id))

    if kind == 'brew':
        formula = spec.get('formula')
        if not formula:
            return None
        return f'brew install {shlex.quote(str(formula))}'

    if kind == 'node':
        package = spec.get('package')
        if not package:
            return None
        install_args = 'i -g' if node_manager == 'npm' else 'add -g'
        return f'{node_manager} {install_args} {shlex.quote(str(package))}'

    if kind == 'go':
        module = spec.get('module')
        if not module:
            return None
        module_text = str(module)
        version = spec.get('version')
        if version:
            module_text = f'{module_text.split("@")[0]}@{version}'
        elif '@' not in module_text:
            module_text = f'{module_text}@latest'
        return f'go install {shlex.quote(module_text)}'

    if kind == 'uv':
        package = spec.get('package')
        if not package:
            return None
        return f'uv tool install {shlex.quote(str(package))}'

    if kind == 'download':
        url = spec.get('url')
        if not url:
            return None
        target_dir = str(spec.get('targetDir') or '').strip()
        if not target_dir:
            target_dir = f'$HOME/.openwebui/tools/{safe_skill_id}'
        elif target_dir.startswith('~/'):
            target_dir = f'$HOME/{target_dir[2:]}'
        archive = spec.get('archive')
        if not archive:
            tail = str(url).split('?', 1)[0].rstrip('/').rsplit('/', 1)[-1].lower()
            if tail.endswith('.zip'):
                archive = 'zip'
            elif tail.endswith(('.tar.gz', '.tgz')):
                archive = 'tar.gz'
            elif tail.endswith(('.tar.bz2', '.tbz2')):
                archive = 'tar.bz2'
        tmpfile = f'/tmp/openwebui-skill-{safe_skill_id}-download'
        commands = [f'curl -fSL {shlex.quote(str(url))} -o {shlex.quote(tmpfile)}']
        sha256 = spec.get('sha256')
        if sha256:
            commands.append(f'echo {shlex.quote(f"{sha256}  {tmpfile}")} | sha256sum -c -')
        quoted_target = _quote_target_dir(target_dir)
        if archive == 'zip':
            python_extract = shlex.quote('import zipfile,sys;zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])')
            commands.append(
                f'mkdir -p {quoted_target} && '
                f'(unzip -o {shlex.quote(tmpfile)} -d {quoted_target} || '
                f'python3 -c {python_extract} {shlex.quote(tmpfile)} {quoted_target})'
            )
        elif archive in ('tar.gz', 'tar.bz2', 'tar.xz'):
            tar_flags = {'tar.gz': '-xzf', 'tar.bz2': '-xjf', 'tar.xz': '-xJf'}[archive]
            commands.append(f'mkdir -p {quoted_target} && tar {tar_flags} {shlex.quote(tmpfile)} -C {quoted_target}')
        elif archive:
            return None
        return ' && '.join(commands)

    return None


####################
# Terminal operations (lazy open_webui imports)
####################


def _find_operation(specs, exact_names=(), name_tokens=()):
    """Find an operation spec by exact name first, then by name substring."""
    specs = [spec for spec in (specs or []) if isinstance(spec, dict) and isinstance(spec.get('name'), str)]
    for wanted in exact_names:
        for spec in specs:
            if spec['name'] == wanted:
                return spec
    for spec in specs:
        if any(token in spec['name'] for token in name_tokens):
            return spec
    return None


def _find_run_command_spec(specs):
    return _find_operation(specs, exact_names=('run_command',), name_tokens=('run', 'exec'))


def _resolve_write_file_params(spec) -> tuple[str, str] | None:
    """Map write_file parameter names from the spec, or None to fall back."""
    params = (spec or {}).get('parameters')
    props = params.get('properties') if isinstance(params, dict) else None
    if not isinstance(props, dict):
        return None
    names = set(props.keys())
    path_param = next((n for n in ('path', 'file_path', 'filePath', 'filename') if n in names), None)
    content_param = next((n for n in ('content', 'text', 'data') if n in names), None)
    if not path_param or not content_param:
        return None
    return path_param, content_param


async def _terminal_request_context(request, user, terminal_id, metadata):
    from open_webui.models.config import Config
    from open_webui.utils.tools import build_terminal_request_context

    connections = await Config.get('terminal_server.connections', []) or []
    connection = next((c for c in connections if c.get('id') == terminal_id), None)
    if connection is None:
        raise RuntimeError(f"Terminal server '{terminal_id}' not found")
    return await build_terminal_request_context(request, connection, _normalize_user(user), metadata or {})


def _stringify_tool_result(result) -> str:
    if isinstance(result, str):
        return result
    return json.dumps(result, ensure_ascii=False)


async def probe_terminal_environment(request, user, terminal_id, metadata, req) -> dict | None:
    """Run the probe script on the terminal; None when probing is impossible."""
    try:
        from open_webui.utils.tools import execute_tool_server

        server_data, headers, cookies = await _terminal_request_context(request, user, terminal_id, metadata)
        run_spec = _find_run_command_spec(server_data.get('specs'))
        if run_spec is None:
            log.debug('No run_command operation on terminal %s; skipping skill probe', terminal_id)
            return None
        result, _ = await execute_tool_server(
            url=server_data['url'],
            headers=headers,
            cookies=cookies,
            name=run_spec['name'],
            params={'command': build_probe_script(req)},
            server_data=server_data,
        )
        if isinstance(result, dict) and result.get('error'):
            log.debug('Skill probe failed on terminal %s: %s', terminal_id, result.get('error'))
            return None
        return parse_probe_output(_stringify_tool_result(result), req)
    except Exception as e:
        log.warning('Failed to probe terminal environment for skill gating: %s', e)
        return None


async def sync_skill_files(request, user, terminal_id, metadata, skill_id, files, env) -> str | None:
    """Sync bundled skill files to the terminal; returns the base dir or None."""
    try:
        from open_webui.utils.tools import execute_tool_server, get_terminal_cwd

        metadata = metadata or {}
        server_data, headers, cookies = await _terminal_request_context(request, user, terminal_id, metadata)
        specs = server_data.get('specs')
        run_spec = _find_run_command_spec(specs)
        if run_spec is None:
            return None
        write_spec = _find_operation(specs, exact_names=('write_file',))
        write_params = _resolve_write_file_params(write_spec) if write_spec else None

        cache = getattr(request.app.state, 'SKILL_TERMINAL_SYNC', None)
        if cache is None:
            cache = {}
            request.app.state.SKILL_TERMINAL_SYNC = cache
        fingerprint = files_fingerprint(files, env)
        cache_key = (terminal_id, metadata.get('chat_id') or metadata.get('session_id'), skill_id)
        cached = cache.get(cache_key)
        if cached and cached.get('fingerprint') == fingerprint:
            return cached.get('base_dir')

        cwd = await get_terminal_cwd(server_data['url'], headers, cookies)
        safe_skill_id = re.sub(r'[^A-Za-z0-9_.-]', '_', str(skill_id))
        if cwd:
            base_dir = f'{cwd.rstrip("/")}/.openwebui/skills/{safe_skill_id}'
        else:
            base_dir = f'/tmp/openwebui-skills/{safe_skill_id}'

        async def run_command(command: str):
            result, _ = await execute_tool_server(
                url=server_data['url'],
                headers=headers,
                cookies=cookies,
                name=run_spec['name'],
                params={'command': command},
                server_data=server_data,
            )
            if isinstance(result, dict) and result.get('error'):
                raise RuntimeError(result['error'])
            return result

        entries = []
        for path, info in (files or {}).items():
            rel_path = sanitize_skill_file_path(path)
            if rel_path is None:
                log.warning('Skipping unsafe file path %r for skill %s', path, skill_id)
                continue
            if not isinstance(info, dict) or info.get('content') is None:
                continue
            encoding = info.get('encoding', 'utf-8')
            entries.append((rel_path, str(info['content']), encoding if encoding in ('utf-8', 'base64') else 'utf-8'))

        env_text = build_env_file(env or {})
        if env_text:
            entries.append(('env', env_text, 'utf-8'))

        if not entries:
            return None

        dirs = sorted({posixpath.dirname(rel) for rel, _, _ in entries} - {''})
        mkdir_targets = ' '.join([shlex.quote(base_dir), *[shlex.quote(f'{base_dir}/{d}') for d in dirs]])
        await run_command(f'mkdir -p {mkdir_targets}')

        for rel_path, content, encoding in entries:
            target = f'{base_dir}/{rel_path}'
            if encoding == 'utf-8' and write_params is not None:
                path_param, content_param = write_params
                result, _ = await execute_tool_server(
                    url=server_data['url'],
                    headers=headers,
                    cookies=cookies,
                    name=write_spec['name'],
                    params={path_param: target, content_param: content},
                    server_data=server_data,
                )
                if not (isinstance(result, dict) and result.get('error')):
                    continue
                log.debug('write_file failed for %s; falling back to run_command', target)
            encoded = base64.b64encode(content.encode('utf-8')).decode('ascii') if encoding == 'utf-8' else content
            await run_command(
                f'mkdir -p {shlex.quote(posixpath.dirname(target))} && '
                f'printf %s {shlex.quote(encoded)} | base64 -d > {shlex.quote(target)}'
            )

        cache[cache_key] = {'fingerprint': fingerprint, 'base_dir': base_dir}
        return base_dir
    except Exception as e:
        log.warning('Failed to sync files for skill %s: %s', skill_id, e)
        return None


async def prepare_skills_for_terminal(request, user, metadata, terminal_id, skills) -> dict[str, dict]:
    """Gate and prepare openclaw skills for a terminal-bound chat request."""
    try:
        candidates = []
        for skill in skills or []:
            oc = openclaw_meta(skill)
            if not oc:
                continue
            req = gating_requirements(openclaw_frontmatter(oc))
            candidates.append((skill, oc, req))
        if not candidates:
            return {}

        combined = {'bins': [], 'any_bins': [], 'env': [], 'config': [], 'os': []}
        for _, _, req in candidates:
            for key in combined:
                combined[key].extend(req.get(key) or [])

        probe = None
        if combined['bins'] or combined['any_bins'] or combined['env'] or combined['os']:
            probe = await probe_terminal_environment(request, user, terminal_id, metadata, combined)

        prepared = {}
        for skill, oc, req in candidates:
            skill_id = _get(skill, 'id')
            if not skill_id:
                continue
            content = _get(skill, 'content', '') or ''
            reasons = evaluate_gating(req, probe, skill_config(oc))
            if reasons:
                prepared[skill_id] = {'skip_reason': '; '.join(reasons), 'name': _get(skill, 'name')}
                continue
            files = skill_files(oc)
            env = skill_env(oc)
            if files or env:
                base_dir = await sync_skill_files(request, user, terminal_id, metadata, skill_id, files, env)
                if base_dir is None:
                    prepared[skill_id] = {'content': content}
                    continue
                content = substitute_basedir(content, base_dir)
                if env:
                    content = f'{content}\n\n{build_env_guidance(base_dir)}'
                prepared[skill_id] = {'content': content, 'base_dir': base_dir}
            elif has_gating(req):
                prepared[skill_id] = {'content': content}
        return prepared
    except Exception as e:
        log.warning('Failed to prepare openclaw skills for terminal: %s', e)
        return {}


_INSTALLER_PROBES = ('brew', 'uv', 'npm', 'pnpm', 'yarn', 'bun', 'go', 'curl')
_NODE_MANAGERS = ('npm', 'pnpm', 'yarn', 'bun')


async def run_install_specs(request, user, terminal_id, skill) -> dict:
    """Run the first usable install spec of a skill on the terminal."""
    oc = openclaw_meta(skill)
    req = gating_requirements(openclaw_frontmatter(oc))
    install_specs = req.get('install') or []
    skill_id = _get(skill, 'id') or 'skill'

    try:
        from open_webui.utils.tools import execute_tool_server

        probe = await probe_terminal_environment(
            request, user, terminal_id, {}, {'bins': list(_INSTALLER_PROBES), 'any_bins': [], 'env': []}
        )
        available = (probe or {}).get('bins') or {}
        missing = [name for name in _INSTALLER_PROBES if not available.get(name)]

        def spec_of(kind):
            return next((spec for spec in install_specs if spec.get('kind') == kind), None)

        attempt = None
        if available.get('brew') and spec_of('brew'):
            attempt = ('brew', 'brew', spec_of('brew'))
        elif available.get('uv') and spec_of('uv'):
            attempt = ('uv', 'uv', spec_of('uv'))
        else:
            node_manager = next((m for m in _NODE_MANAGERS if available.get(m)), None)
            if node_manager and spec_of('node'):
                attempt = ('node', node_manager, spec_of('node'))
            elif available.get('go') and spec_of('go'):
                attempt = ('go', 'go', spec_of('go'))
            elif available.get('curl') and spec_of('download'):
                attempt = ('download', 'curl', spec_of('download'))

        if attempt is None:
            return {'ok': False, 'missing': missing}

        kind, installer, spec = attempt
        command = build_install_command(spec, skill_id, node_manager=installer if kind == 'node' else 'npm')
        if command is None:
            return {'ok': False, 'missing': missing}

        server_data, headers, cookies = await _terminal_request_context(request, user, terminal_id, {})
        run_spec = _find_run_command_spec(server_data.get('specs'))
        if run_spec is None:
            return {'ok': False, 'missing': missing, 'error': f"Terminal server '{terminal_id}' cannot run commands"}

        result, _ = await execute_tool_server(
            url=server_data['url'],
            headers=headers,
            cookies=cookies,
            name=run_spec['name'],
            params={'command': command},
            server_data=server_data,
        )
        ok = not (isinstance(result, dict) and result.get('error'))
        return {'ok': ok, 'installer': installer, 'command': command, 'output': _stringify_tool_result(result)}
    except Exception as e:
        log.warning('Failed to run install specs for skill %s: %s', skill_id, e)
        return {'ok': False, 'error': str(e)}
