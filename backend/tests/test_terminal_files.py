"""Delivery policy tests without importing application/config/database state."""

import importlib.util
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

MODULE = Path(__file__).parents[1] / 'open_webui/utils/terminal_files.py'


@pytest.fixture
def policy():
    assert MODULE.exists(), 'Terminal file delivery policy is not implemented'
    spec = importlib.util.spec_from_file_location('terminal_files_policy', MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def issue(policy, **changes):
    return policy.add_file_delivery_links(
        {'exists': True, 'path': '/workspace/报告.svg', **changes},
        server_id='terminal-1',
        owner_id='user-1',
        metadata={'chat_id': 'chat-1'},
        secret='synthetic-signing-secret',
    )


def test_links_bind_owner_terminal_path_and_context(policy):
    result = issue(policy)
    ref = parse_qs(urlsplit(result['download_url']).query)['ref'][0]
    payload = policy.verify_file_reference(ref, 'synthetic-signing-secret')
    assert payload['owner_id'] == result['owner_id'] == 'user-1'
    assert payload['server_id'] == 'terminal-1'
    assert payload['path'] == '/workspace/报告.svg'
    assert payload['chat_id'] == 'chat-1'
    assert '/files/image?' in result['image_url']
    assert 'synthetic-signing-secret' not in result['download_url']


def test_signature_tamper_and_key_rotation_rejected(policy):
    ref = parse_qs(urlsplit(issue(policy)['download_url']).query)['ref'][0]
    for value, secret in [(ref + 'x', 'synthetic-signing-secret'), (ref, 'rotated-secret')]:
        with pytest.raises(ValueError):
            policy.verify_file_reference(value, secret)


@pytest.mark.parametrize(
    'result',
    [
        {'error': 'failed'},
        {'exists': False, 'path': '/file.png'},
        {'exists': True, 'path': '../file.png'},
        {'exists': True, 'path': '/a/../file.png'},
        {'exists': True, 'path': 'https://other.test/x'},
        {'exists': True, 'path': '/a\x00.png'},
    ],
)
def test_no_links_for_errors_or_unsafe_paths(policy, result):
    enriched = policy.add_file_delivery_links(
        result,
        server_id='t',
        owner_id='u',
        metadata={},
        secret='secret',
    )
    assert 'download_url' not in enriched


def test_nonimage_only_has_download_and_preserves_tool_fields(policy):
    result = issue(policy, path='/report.html', page=3)
    assert 'download_url' in result
    assert 'image_url' not in result
    assert result['page'] == 3


def test_headers_force_inert_download_and_encode_filename(policy):
    headers = policy.file_response_headers('/tmp/报告\r\n".html')
    assert headers['Content-Type'] == 'application/octet-stream'
    assert headers['Content-Disposition'].startswith('attachment;')
    assert "filename*=UTF-8''" in headers['Content-Disposition']
    assert '\r' not in headers['Content-Disposition'] and '\n' not in headers['Content-Disposition']
    assert headers['X-Content-Type-Options'] == 'nosniff'
    assert 'sandbox' in headers['Content-Security-Policy']
    assert 'no-store' in headers['Cache-Control']


def test_svg_image_headers_isolate_direct_navigation(policy):
    headers = policy.file_response_headers('/plot.svg', 'image/svg+xml')
    assert headers['Content-Type'] == 'image/svg+xml'
    assert headers['Content-Disposition'].startswith('inline;')
    csp = headers['Content-Security-Policy']
    assert "script-src 'none'" in csp
    assert "style-src 'unsafe-inline'" in csp
    assert 'allow-scripts' not in csp and 'allow-same-origin' not in csp


@pytest.mark.parametrize(
    'svg',
    [
        b'<svg xmlns="http://www.w3.org/2000/svg"><style>path{fill:red}</style><path d="M0 0"/></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"><script>alert(1)</script></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><foreignObject/><image href="https://external.test/x"/></svg>',
    ],
)
def test_svg_validation_preserves_bytes_for_img_isolation(policy, svg):
    assert policy.detect_image_type(svg) == 'image/svg+xml'


@pytest.mark.parametrize(
    'data',
    [
        b'<html><script>alert(1)</script></html>',
        b'<svg xmlns="urn:wrong"/>',
        b'<svg',
        b'<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/passwd">]><svg xmlns="http://www.w3.org/2000/svg">&x;</svg>',
        b'<?xml-stylesheet href="https://external.test/x"?><svg xmlns="http://www.w3.org/2000/svg"/>',
    ],
)
def test_invalid_or_entity_svg_rejected(policy, data):
    assert policy.detect_image_type(data) is None


def test_real_raster_validation_rejects_truncated_magic(policy):
    import io
    from PIL import Image

    for fmt, mime in [('PNG', 'image/png'), ('JPEG', 'image/jpeg'), ('GIF', 'image/gif'), ('WEBP', 'image/webp')]:
        stream = io.BytesIO()
        Image.new('RGB', (2, 2), 'red').save(stream, format=fmt)
        assert policy.detect_image_type(stream.getvalue()) == mime
    assert policy.detect_image_type(b'\x89PNG\r\n\x1a\n<html>') is None
