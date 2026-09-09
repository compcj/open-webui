import ast
from pathlib import Path

BUILTIN = Path(__file__).parents[1] / 'open_webui' / 'tools' / 'builtin.py'


def _docstring(func_name: str) -> str:
    tree = ast.parse(BUILTIN.read_text(encoding='utf-8'))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            return ast.get_docstring(node) or ''
    raise AssertionError(f'{func_name} not found in {BUILTIN}')


def _description_portion(doc: str) -> str:
    # open_webui.utils.tools.parse_description stops at the first :param/:return line;
    # only text before it reaches the model as the tool description.
    for marker in (':param', ':return'):
        idx = doc.find(marker)
        if idx != -1:
            doc = doc[:idx]
    return doc


def test_search_web_description_requires_fetch_url_for_full_content():
    # search_web returns only title/link/snippet; the description must tell the model
    # that full page content requires a follow-up fetch_url call, so models do not
    # answer "result body is empty" instead of fetching.
    description = _description_portion(_docstring('search_web'))
    assert 'never the full page' in description
    assert 'fetch_url' in description


def test_fetch_url_description_chains_after_search_web():
    description = _description_portion(_docstring('fetch_url'))
    assert 'search_web' in description
    assert 'snippet' in description
