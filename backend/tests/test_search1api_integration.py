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

    search1api = router.index("    elif engine == 'search1api':")
    searchapi = router.index("    elif engine == 'searchapi':")
    assert search1api < searchapi
    assert '    elif engine ==' not in router[search1api + 1:searchapi]


def test_admin_ui_contract():
    ui = (ROOT / 'src' / 'lib' / 'components' / 'admin' / 'Settings' / 'WebSearch.svelte').read_text(
        encoding='utf-8'
    )
    assert "'search1api'," in ui
    assert "webConfig.WEB_SEARCH_ENGINE === 'search1api'" in ui
    assert 'bind:value={webConfig.SEARCH1API_API_KEY}' in ui
    assert 'variant="settings"' in ui
