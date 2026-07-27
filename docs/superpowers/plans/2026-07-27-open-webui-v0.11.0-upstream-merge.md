# Open WebUI v0.11.0 Upstream Merge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge upstream Open WebUI `v0.11.0` into `compcj/open-webui:main` while preserving and adapting the fork's Search1API provider.

**Architecture:** Build and test a candidate source tree from the signed upstream tag, apply Search1API as a small provider/config/router/UI extension, then create a normal two-parent merge commit whose parents are the guarded fork head and upstream tag. Because full Git transport repeatedly times out, use the GitHub Git Data API for the final tree and commit while retaining normal merge topology.

**Tech Stack:** GitHub REST/Git Data API via `gh`, Python 3.11/pytest, Svelte 5/TypeScript, npm, PowerShell.

## Global Constraints

- Upstream source and second parent must be `f9590b8017199e56d5e953657e6498e3cef1d246` (`v0.11.0`).
- Preserve the Search1API environment variable, persistent configuration, provider, router branch, admin UI, and automated tests.
- Do not rebase, force-push, or overwrite a remote `main` head that changed after the guard check.
- The final `main` update must be a non-force fast-forward to a two-parent merge commit.
- Do not replace whole v0.11.0 files with v0.10.2 copies; make minimal Search1API insertions into v0.11.0.
- Do not update `main` unless targeted tests, Python checks, frontend checks, conflict-marker scans, and commit-topology checks pass.

---

### Task 1: Materialize and Guard the v0.11.0 Candidate

**Files:**
- Create locally: `candidate-v0.11.0/` from the upstream release archive
- Verify: `candidate-v0.11.0/package.json`

**Interfaces:**
- Consumes: upstream commit `f9590b8017199e56d5e953657e6498e3cef1d246`
- Produces: a writable v0.11.0 source tree and the exact guarded fork-head SHA

- [ ] **Step 1: Re-read and record the fork head**

```powershell
$expectedForkHead = gh api repos/compcj/open-webui/git/ref/heads/main --jq '.object.sha'
$expectedForkHead
```

Expected: the SHA of the plan commit created immediately before execution. If it differs from that known SHA, stop and inspect the new commits before continuing.

- [ ] **Step 2: Download and extract the immutable upstream archive**

```powershell
$archivePath = 'D:\project\openwebui\open-webui-v0.11.0.tar.gz'
$extractRoot = 'D:\project\openwebui\candidate-v0.11.0-extract'
$candidateRoot = 'D:\project\openwebui\candidate-v0.11.0'
Invoke-WebRequest `
  -Uri 'https://api.github.com/repos/open-webui/open-webui/tarball/f9590b8017199e56d5e953657e6498e3cef1d246' `
  -Headers @{ 'User-Agent' = 'Codex-Open-WebUI-Merge' } `
  -OutFile $archivePath
New-Item -ItemType Directory -Path $extractRoot
tar -xzf $archivePath -C $extractRoot
$archiveRoot = Get-ChildItem -Directory -LiteralPath $extractRoot | Select-Object -First 1
Move-Item -LiteralPath $archiveRoot.FullName -Destination $candidateRoot
```

Expected: `candidate-v0.11.0/package.json` exists.

- [ ] **Step 3: Verify the source identity**

```powershell
$package = Get-Content -Raw 'D:\project\openwebui\candidate-v0.11.0\package.json' | ConvertFrom-Json
if ($package.version -ne '0.11.0') { throw "Unexpected version: $($package.version)" }
gh api repos/open-webui/open-webui/git/commits/f9590b8017199e56d5e953657e6498e3cef1d246 --jq '.sha'
```

Expected: version `0.11.0` and SHA `f9590b8017199e56d5e953657e6498e3cef1d246`.

- [ ] **Step 4: Install the minimal Python test tools**

```powershell
python -m pip install pytest pytest-asyncio ruff
```

Expected: pytest, pytest-asyncio, and Ruff install successfully. Application dependencies are not required for the stubbed Search1API unit and contract tests.

---

### Task 2: Port the Search1API Provider with Unit Tests

**Files:**
- Create: `backend/open_webui/retrieval/web/search1api.py`
- Create: `backend/tests/test_search1api.py`

**Interfaces:**
- Consumes: `SearchResult`, `get_filtered_results`, and `get_session()` from v0.11.0
- Produces: `async search_search1api(api_key: str, query: str, count: int, filter_list: list[str | None] | None = None) -> list[SearchResult]`

- [ ] **Step 1: Write the provider tests**

Create `backend/tests/test_search1api.py` with:

```python
import importlib.util
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from types import ModuleType
from urllib.parse import urlparse

import pytest


@dataclass
class StubSearchResult:
    link: str
    title: str | None
    snippet: str | None

    def model_dump(self):
        return asdict(self)


def filter_results(results, filter_list):
    allowed = [entry for entry in filter_list if entry and not entry.startswith('!')]
    blocked = [entry[1:] for entry in filter_list if entry and entry.startswith('!')]

    def matches(host, domain):
        return host == domain or host.endswith(f'.{domain}')

    return [
        result
        for result in results
        if (not allowed or any(matches(urlparse(result['link']).hostname or '', domain) for domain in allowed))
        and not any(matches(urlparse(result['link']).hostname or '', domain) for domain in blocked)
    ]


class FakeResponse:
    def __init__(self, payload=None, error=None):
        self.payload = payload or {}
        self.error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def raise_for_status(self):
        if self.error:
            raise self.error

    async def json(self):
        return self.payload


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def load_module(response):
    session = FakeSession(response)

    async def fake_get_session():
        return session

    main_module = ModuleType('open_webui.retrieval.web.main')
    main_module.SearchResult = StubSearchResult
    main_module.get_filtered_results = filter_results
    pool_module = ModuleType('open_webui.utils.session_pool')
    pool_module.get_session = fake_get_session
    module_path = Path(__file__).parents[1] / 'open_webui' / 'retrieval' / 'web' / 'search1api.py'
    spec = importlib.util.spec_from_file_location('search1api_under_test', module_path)
    module = importlib.util.module_from_spec(spec)
    old_main = sys.modules.get('open_webui.retrieval.web.main')
    old_pool = sys.modules.get('open_webui.utils.session_pool')
    sys.modules['open_webui.retrieval.web.main'] = main_module
    sys.modules['open_webui.utils.session_pool'] = pool_module
    try:
        spec.loader.exec_module(module)
    finally:
        if old_main is None:
            sys.modules.pop('open_webui.retrieval.web.main', None)
        else:
            sys.modules['open_webui.retrieval.web.main'] = old_main
        if old_pool is None:
            sys.modules.pop('open_webui.utils.session_pool', None)
        else:
            sys.modules['open_webui.utils.session_pool'] = old_pool
    return module, session


@pytest.mark.asyncio
async def test_request_and_result_normalisation():
    module, session = load_module(
        FakeResponse(
            {
                'results': [
                    {'title': 'First', 'link': 'https://first.example/a', 'snippet': 'A'},
                    {'title': 'Second', 'link': 'https://second.example/b', 'content': 'B'},
                    {'title': 'Third', 'link': 'https://third.example/c', 'snippet': 'C'},
                ]
            }
        )
    )
    results = await module.search_search1api('test-key', 'open webui', 2)
    assert session.calls == [
        (
            'https://api.search1api.com/search',
            {
                'headers': {'Authorization': 'Bearer test-key', 'Content-Type': 'application/json'},
                'json': {'query': 'open webui', 'max_results': 2},
            },
        )
    ]
    assert [result.model_dump() for result in results] == [
        {'link': 'https://first.example/a', 'title': 'First', 'snippet': 'A'},
        {'link': 'https://second.example/b', 'title': 'Second', 'snippet': 'B'},
    ]


@pytest.mark.asyncio
async def test_allow_and_block_domain_filters():
    module, _ = load_module(
        FakeResponse(
            {
                'results': [
                    {'title': 'Allowed', 'link': 'https://allowed.example/a', 'snippet': 'A'},
                    {'title': 'Subdomain', 'link': 'https://sub.allowed.example/b', 'snippet': 'B'},
                    {'title': 'Blocked', 'link': 'https://blocked.allowed.example/c', 'snippet': 'C'},
                    {'title': 'Outside', 'link': 'https://outside.example/d', 'snippet': 'D'},
                ]
            }
        )
    )
    results = await module.search_search1api(
        'test-key', 'filtered query', 10, ['allowed.example', '!blocked.allowed.example']
    )
    assert [result.title for result in results] == ['Allowed', 'Subdomain']


@pytest.mark.asyncio
async def test_empty_results():
    module, _ = load_module(FakeResponse({'results': []}))
    assert await module.search_search1api('test-key', 'none', 3) == []


@pytest.mark.asyncio
async def test_http_error_is_propagated():
    module, _ = load_module(FakeResponse(error=RuntimeError('upstream failed')))
    with pytest.raises(RuntimeError, match='upstream failed'):
        await module.search_search1api('test-key', 'query', 3)
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```powershell
python -m pytest backend/tests/test_search1api.py -q
```

Expected: FAIL because `backend/open_webui/retrieval/web/search1api.py` does not exist.

- [ ] **Step 3: Add the v0.11.0-compatible async provider**

Create `backend/open_webui/retrieval/web/search1api.py` with:

```python
from __future__ import annotations

from open_webui.retrieval.web.main import SearchResult, get_filtered_results
from open_webui.utils.session_pool import get_session


async def search_search1api(
    api_key: str,
    query: str,
    count: int,
    filter_list: list[str | None] | None = None,
) -> list[SearchResult]:
    """Search the web using Search1API and return normalized results."""
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }
    payload = {'query': query, 'max_results': count}
    session = await get_session()
    async with session.post(
        'https://api.search1api.com/search',
        headers=headers,
        json=payload,
    ) as response:
        response.raise_for_status()
        data = await response.json()

    results = data.get('results', [])
    if filter_list:
        results = get_filtered_results(results, filter_list)
    return [
        SearchResult(
            link=result.get('link', ''),
            title=result.get('title'),
            snippet=result.get('snippet') or result.get('content'),
        )
        for result in results[:count]
    ]
```

- [ ] **Step 4: Run provider tests**

```powershell
python -m pytest backend/tests/test_search1api.py -q
```

Expected: `4 passed`.

---

### Task 3: Integrate Configuration and Router Contracts

**Files:**
- Modify: `backend/open_webui/config.py:1228,2943`
- Modify: `backend/open_webui/routers/retrieval.py:103,366,729,808,1284,1435,2403`
- Create: `backend/tests/test_search1api_integration.py`

**Interfaces:**
- Consumes: `search_search1api` from Task 2
- Produces: `SEARCH1API_API_KEY` through config reads/writes and an async `search1api` dispatch branch

- [ ] **Step 1: Write integration contract tests**

Create `backend/tests/test_search1api_integration.py` with:

```python
from pathlib import Path


BACKEND = Path(__file__).parents[1] / 'open_webui'
ROOT = Path(__file__).parents[2]


def test_backend_config_contract():
    config = (BACKEND / 'config.py').read_text(encoding='utf-8')
    assert "SEARCH1API_API_KEY = os.getenv('SEARCH1API_API_KEY', '')" in config
    assert "'web.search.search1api_api_key': SEARCH1API_API_KEY" in config


def test_router_contract():
    router = (BACKEND / 'routers' / 'retrieval.py').read_text(encoding='utf-8')
    required = [
        'from open_webui.retrieval.web.search1api import search_search1api',
        "'SEARCH1API_API_KEY': 'web.search.search1api_api_key'",
        "'SEARCH1API_API_KEY': config.SEARCH1API_API_KEY",
        'SEARCH1API_API_KEY: str | None = None',
        'config.SEARCH1API_API_KEY = form_data.web.SEARCH1API_API_KEY',
        "elif engine == 'search1api':",
        'return await search_search1api(',
        "'No SEARCH1API_API_KEY found in environment variables'",
    ]
    for contract in required:
        assert contract in router


def test_admin_ui_contract():
    ui = (ROOT / 'src' / 'lib' / 'components' / 'admin' / 'Settings' / 'WebSearch.svelte').read_text(
        encoding='utf-8'
    )
    assert "'search1api'," in ui
    assert "webConfig.WEB_SEARCH_ENGINE === 'search1api'" in ui
    assert 'bind:value={webConfig.SEARCH1API_API_KEY}' in ui
    assert 'variant="settings"' in ui
```

- [ ] **Step 2: Run the tests to verify they fail**

```powershell
python -m pytest backend/tests/test_search1api_integration.py -q
```

Expected: three failures because the config, router, and v0.11.0 UI do not yet contain Search1API.

- [ ] **Step 3: Add Search1API to backend configuration**

In `backend/open_webui/config.py`, insert:

```python
SEARCHAPI_ENGINE = os.getenv('SEARCHAPI_ENGINE', '')

SEARCH1API_API_KEY = os.getenv('SEARCH1API_API_KEY', '')

SERPAPI_API_KEY = os.getenv('SERPAPI_API_KEY', '')
```

and add the persistent key next to SearchApi:

```python
'web.search.searchapi_api_key': SEARCHAPI_API_KEY,
'web.search.searchapi_engine': SEARCHAPI_ENGINE,
'web.search.search1api_api_key': SEARCH1API_API_KEY,
'web.search.serpapi_api_key': SERPAPI_API_KEY,
```

- [ ] **Step 4: Add Search1API to the retrieval router**

Make the following minimal insertions in `backend/open_webui/routers/retrieval.py`:

```python
from open_webui.retrieval.web.search1api import search_search1api
```

```python
'SEARCH1API_API_KEY': 'web.search.search1api_api_key',
```

```python
'SEARCH1API_API_KEY': config.SEARCH1API_API_KEY,
```

```python
SEARCH1API_API_KEY: str | None = None
```

```python
config.SEARCH1API_API_KEY = form_data.web.SEARCH1API_API_KEY
```

Add this dispatch branch immediately before `searchapi`:

```python
elif engine == 'search1api':
    if config.SEARCH1API_API_KEY:
        return await search_search1api(
            config.SEARCH1API_API_KEY,
            query,
            config.WEB_SEARCH_RESULT_COUNT,
            config.WEB_SEARCH_DOMAIN_FILTER_LIST,
        )
    else:
        raise Exception('No SEARCH1API_API_KEY found in environment variables')
```

- [ ] **Step 5: Run backend integration tests**

```powershell
python -m pytest backend/tests/test_search1api.py backend/tests/test_search1api_integration.py -q
```

Expected at this point: provider and backend contract tests pass; only `test_admin_ui_contract` remains failing.

---

### Task 4: Adapt the v0.11.0 Admin UI

**Files:**
- Modify: `src/lib/components/admin/Settings/WebSearch.svelte:20-50,574`

**Interfaces:**
- Consumes: `SEARCH1API_API_KEY` returned by the retrieval configuration API
- Produces: selectable `search1api` engine and settings-styled sensitive key input

- [ ] **Step 1: Register the engine**

Insert Search1API next to SearchApi:

```svelte
		'serphouse',
		'serply',
		'search1api',
		'searchapi',
		'serpapi',
```

- [ ] **Step 2: Add the v0.11.0-styled key field**

Insert before the existing `searchapi` branch:

```svelte
					{:else if webConfig.WEB_SEARCH_ENGINE === 'search1api'}
						<div class="mb-2.5 flex w-full flex-col">
							<div>
								<div class=" self-center text-xs text-gray-600 dark:text-gray-400 mb-1">
									{$i18n.t('Search1API API Key')}
								</div>

								<SensitiveInput
									variant="settings"
									placeholder={$i18n.t('Enter Search1API API Key')}
									bind:value={webConfig.SEARCH1API_API_KEY}
								/>
							</div>
						</div>
```

- [ ] **Step 3: Run all Search1API tests**

```powershell
python -m pytest backend/tests/test_search1api.py backend/tests/test_search1api_integration.py -q
```

Expected: `7 passed`.

---

### Task 5: Validate and Publish the Guarded Merge

**Files:**
- Verify all files from Tasks 1-4
- Preserve: `docs/superpowers/specs/2026-07-27-open-webui-v0.11.0-upstream-merge-design.md`
- Preserve: `docs/superpowers/plans/2026-07-27-open-webui-v0.11.0-upstream-merge.md`

**Interfaces:**
- Consumes: tested candidate tree, guarded fork head, upstream tag SHA
- Produces: a two-parent merge commit and a non-force update of `compcj/open-webui:main`

- [ ] **Step 1: Run Python verification**

```powershell
python -m pytest backend/tests/test_search1api.py backend/tests/test_search1api_integration.py -q
python -m compileall -q backend/open_webui/config.py backend/open_webui/routers/retrieval.py backend/open_webui/retrieval/web/search1api.py
python -m ruff check backend/open_webui/config.py backend/open_webui/routers/retrieval.py backend/open_webui/retrieval/web/search1api.py backend/tests/test_search1api.py backend/tests/test_search1api_integration.py
```

Expected: all tests pass; compile and Ruff exit 0.

- [ ] **Step 2: Install frontend dependencies and verify**

```powershell
npm ci
npm run check
npm run build
```

Expected: all commands exit 0. If `npm ci` cannot access the registry, retry only after obtaining network approval; do not treat a skipped frontend check as success.

- [ ] **Step 3: Scan for merge artifacts and verify version**

```powershell
rg -n '^(<<<<<<<|=======|>>>>>>>)' .
$package = Get-Content -Raw package.json | ConvertFrom-Json
if ($package.version -ne '0.11.0') { throw "Unexpected version: $($package.version)" }
```

Expected: `rg` has no matches and version is `0.11.0`.

- [ ] **Step 4: Re-check the remote race guard**

```powershell
$actualForkHead = gh api repos/compcj/open-webui/git/ref/heads/main --jq '.object.sha'
if ($actualForkHead -ne $expectedForkHead) {
    throw "Remote main changed: expected $expectedForkHead, got $actualForkHead"
}
```

Expected: the two SHAs match exactly.

- [ ] **Step 5: Create blobs and the v0.11.0-based tree**

For every changed or preserved path, base64-encode its complete tested contents and create a blob:

```powershell
function New-GitHubBlob([string]$path) {
    $bytes = [System.IO.File]::ReadAllBytes($path)
    $base64 = [Convert]::ToBase64String($bytes)
    gh api --method POST repos/compcj/open-webui/git/blobs `
      -f content="$base64" -f encoding='base64' --jq '.sha'
}
```

Build the complete request using candidate files for code and the approved workspace copies for documentation:

```powershell
$upstreamSha = 'f9590b8017199e56d5e953657e6498e3cef1d246'
$upstreamTreeSha = gh api "repos/open-webui/open-webui/git/commits/$upstreamSha" --jq '.tree.sha'
$candidateRoot = 'D:\project\openwebui\candidate-v0.11.0'
$workspaceRoot = 'D:\project\openwebui'
$sources = [ordered]@{
    'backend/open_webui/config.py' = Join-Path $candidateRoot 'backend/open_webui/config.py'
    'backend/open_webui/retrieval/web/search1api.py' = Join-Path $candidateRoot 'backend/open_webui/retrieval/web/search1api.py'
    'backend/open_webui/routers/retrieval.py' = Join-Path $candidateRoot 'backend/open_webui/routers/retrieval.py'
    'backend/tests/test_search1api.py' = Join-Path $candidateRoot 'backend/tests/test_search1api.py'
    'backend/tests/test_search1api_integration.py' = Join-Path $candidateRoot 'backend/tests/test_search1api_integration.py'
    'src/lib/components/admin/Settings/WebSearch.svelte' = Join-Path $candidateRoot 'src/lib/components/admin/Settings/WebSearch.svelte'
    'docs/superpowers/specs/2026-07-27-open-webui-v0.11.0-upstream-merge-design.md' =
        Join-Path $workspaceRoot 'docs/superpowers/specs/2026-07-27-open-webui-v0.11.0-upstream-merge-design.md'
    'docs/superpowers/plans/2026-07-27-open-webui-v0.11.0-upstream-merge.md' =
        Join-Path $workspaceRoot 'docs/superpowers/plans/2026-07-27-open-webui-v0.11.0-upstream-merge.md'
}
$treeEntries = foreach ($entry in $sources.GetEnumerator()) {
    @{
        path = $entry.Key
        mode = '100644'
        type = 'blob'
        sha = New-GitHubBlob $entry.Value
    }
}
$treeRequest = @{
    base_tree = $upstreamTreeSha
    tree = @($treeEntries)
} | ConvertTo-Json -Depth 5
$treeSha = $treeRequest |
    gh api --method POST repos/compcj/open-webui/git/trees --input - --jq '.sha'
```

Expected: GitHub returns a new tree SHA.

- [ ] **Step 6: Create and inspect the two-parent candidate commit**

```powershell
$upstreamSha = 'f9590b8017199e56d5e953657e6498e3cef1d246'
$mergeSha = gh api --method POST repos/compcj/open-webui/git/commits `
  -f message='Merge upstream v0.11.0 with Search1API support' `
  -f tree="$treeSha" `
  -f parents[]="$expectedForkHead" `
  -f parents[]="$upstreamSha" `
  --jq '.sha'

gh api "repos/compcj/open-webui/git/commits/$mergeSha" `
  --jq '{sha: .sha, parents: [.parents[].sha], tree: .tree.sha}'
```

Expected: parent order is the guarded fork head first and `f9590b8...` second.

- [ ] **Step 7: Compare the candidate and update main without force**

```powershell
gh api "repos/compcj/open-webui/compare/$upstreamSha...$mergeSha" `
  --jq '{ahead_by, behind_by, files: [.files[].filename]}'

gh api --method PATCH repos/compcj/open-webui/git/refs/heads/main `
  -f sha="$mergeSha" -F force=false --jq '.object.sha'
```

Expected before update: the file list contains only the eight intended Search1API/doc paths. Expected after update: returned SHA equals `$mergeSha`.

- [ ] **Step 8: Verify the published branch**

```powershell
gh api repos/compcj/open-webui/contents/package.json?ref=main --jq '.content' |
  ForEach-Object { [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($_ -replace '\s','')) }
gh api "repos/compcj/open-webui/git/commits/$mergeSha" --jq '[.parents[].sha]'
```

Expected: package version is `0.11.0`, Search1API files are present, and the two parents remain correct.

