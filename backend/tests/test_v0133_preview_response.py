import pytest
from fastapi import HTTPException

from open_webui.utils.terminal_preview_response import rewrite_preview_body, rewrite_preview_location


UPSTREAM_BASE = 'https://terminal.example.test/p/policy'
UPSTREAM_PREFIX = '/p/policy/browser/view/session/upstream-capability'
UPSTREAM_ROOT = 'https://terminal.example.test' + UPSTREAM_PREFIX
UPSTREAM_URL = UPSTREAM_ROOT + '/frame/http/localhost%3A8000/nested/page'
PREVIEW_PREFIX = '/p/preview-capability'


@pytest.mark.parametrize('base', [UPSTREAM_URL, UPSTREAM_URL.replace('https://', 'tether://', 1)])
def test_location_keeps_relative_port_navigation_inside_preview(base):
    result = rewrite_preview_location(base, '../asset?x=1#section', UPSTREAM_PREFIX, PREVIEW_PREFIX)
    assert result == PREVIEW_PREFIX + '/frame/http/localhost%3A8000/asset?x=1#section'


def test_location_removes_upstream_capability_from_absolute_redirect():
    result = rewrite_preview_location(UPSTREAM_URL, UPSTREAM_ROOT + '/frame/http/site/', UPSTREAM_ROOT, PREVIEW_PREFIX)
    assert result == PREVIEW_PREFIX + '/frame/http/site/'


@pytest.mark.parametrize(
    'location',
    [
        'https://other.example.test' + UPSTREAM_PREFIX + '/runtime.js',
        'https://terminal.example.test:8443' + UPSTREAM_PREFIX + '/runtime.js',
        'http://terminal.example.test' + UPSTREAM_PREFIX + '/runtime.js',
        '//terminal.example.test@other.example.test' + UPSTREAM_PREFIX,
        '/api/v1/admin',
        UPSTREAM_PREFIX + '-different/runtime.js',
        UPSTREAM_PREFIX + '/%252e%252e%252foutside',
        UPSTREAM_PREFIX + '/%5c%5coutside',
        UPSTREAM_PREFIX + '/runtime.js?refer=/browser/view/session/upstream-capability',
        UPSTREAM_PREFIX + '/runtime.js?refer=%2575pstream-capability',
    ],
)
def test_location_refuses_escapes_and_embedded_upstream_capabilities(location):
    with pytest.raises(HTTPException) as error:
        rewrite_preview_location(UPSTREAM_URL, location, UPSTREAM_PREFIX, PREVIEW_PREFIX)
    assert error.value.status_code == 502
    assert 'upstream-capability' not in error.value.detail


def test_text_resources_replace_absolute_and_root_relative_capability_paths():
    body = (
        f'<a href="{UPSTREAM_ROOT}/frame/https/site/">Link</a>'
        f'<script src={UPSTREAM_PREFIX}/runtime.js></script>'
        '<style>div{background:url(/browser/view/session/upstream-capability/frame/http/site/icon)}</style>'
        '<img src="../relative.png">'
    ).encode()
    result = rewrite_preview_body(body, 'text/html; charset=utf-8', UPSTREAM_BASE, UPSTREAM_PREFIX, PREVIEW_PREFIX)
    assert result.count(PREVIEW_PREFIX.encode()) == 3
    assert b'upstream-capability' not in result
    assert b'../relative.png' in result


def test_text_resources_replace_slash_escaped_json_paths():
    escaped = UPSTREAM_PREFIX.replace('/', r'\/')
    body = ('{"src":"' + escaped + r'\/runtime.js"}').encode()
    result = rewrite_preview_body(body, 'application/json', UPSTREAM_BASE, UPSTREAM_PREFIX, PREVIEW_PREFIX)
    assert result == b'{"src":"\\/p\\/preview-capability\\/runtime.js"}'


@pytest.mark.parametrize(
    'uri',
    [
        'https://other.example.test' + UPSTREAM_PREFIX,
        'https://other.example.test/?next=' + UPSTREAM_PREFIX,
        'https://other.example.test/?src=' + UPSTREAM_PREFIX,
    ],
)
def test_text_resources_cannot_send_the_preview_ticket_to_another_authority(uri):
    with pytest.raises(HTTPException) as error:
        rewrite_preview_body(
            f'<a href="{uri}">Link</a>'.encode(), 'text/html', UPSTREAM_BASE, UPSTREAM_PREFIX, PREVIEW_PREFIX
        )
    assert error.value.status_code == 502


def test_binary_resources_keep_exact_bytes():
    body = b'\x00\xff' + UPSTREAM_PREFIX.encode()
    assert (
        rewrite_preview_body(body, 'application/octet-stream', UPSTREAM_BASE, UPSTREAM_PREFIX, PREVIEW_PREFIX) == body
    )
