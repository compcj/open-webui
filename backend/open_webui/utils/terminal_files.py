"""Inert terminal file delivery policy, independent of application/database imports."""

import hashlib
import io
import posixpath
import warnings
from urllib.parse import quote, urlencode

from itsdangerous import BadData, URLSafeSerializer
from lxml import etree
from PIL import Image

FILE_REFERENCE_SALT = 'open-webui-terminal-file-v1'
IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg'}
# Images must be validated before response headers are sent. Downloads have no
# buffering limit and remain streamed; unusually large images can be downloaded.
MAX_IMAGE_BYTES = 32 * 1024 * 1024
FILE_CSP = "sandbox; default-src 'none'; script-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
SVG_CSP = FILE_CSP + "; style-src 'unsafe-inline'; img-src data:; font-src data:"
TERMINAL_FILE_DELIVERY_PROMPT = (
    'To deliver a generated file, call display_file first. After it succeeds, use the returned '
    'download_url in a Markdown link: [Download filename](download_url). '
    'For an image, including SVG, use the returned image_url in Markdown: ![description](image_url), '
    'and include a download link when delivering the original file. Copy these URLs exactly; '
    'never construct file URLs, use local paths as links, or insert SVG/HTML markup. '
    'These delivery instructions replace any instruction to omit Markdown after display_file. '
    'If no delivery URL is returned, use the existing file viewer instead of inventing a link.'
)


def valid_file_path(path) -> bool:
    return (
        isinstance(path, str)
        and path.startswith('/')
        and not path.startswith('//')
        and path != '/'
        and '\\' not in path
        and not any(ord(char) < 32 or ord(char) == 127 for char in path)
        and posixpath.normpath(path) == path
    )


def _serializer(secret: str) -> URLSafeSerializer:
    if not secret:
        raise ValueError('File signing is unavailable')
    return URLSafeSerializer(secret, salt=FILE_REFERENCE_SALT, signer_kwargs={'digest_method': hashlib.sha256})


def verify_file_reference(reference: str, secret: str) -> dict:
    try:
        if not isinstance(reference, str) or len(reference) > 32768:
            raise ValueError('Invalid file reference')
        data = _serializer(secret).loads(reference)
        if (
            not isinstance(data, dict)
            or data.get('v') != 1
            or not valid_file_path(data.get('path'))
            or not all(isinstance(data.get(key), str) and data[key] for key in ('owner_id', 'server_id'))
            or not all(
                data.get(key) is None or isinstance(data[key], str)
                for key in ('chat_id', 'automation_id', 'context_id')
            )
        ):
            raise ValueError('Invalid file reference')
        return data
    except (BadData, TypeError, KeyError) as error:
        raise ValueError('Invalid file reference') from error


def add_file_delivery_links(
    result,
    *,
    server_id: str,
    owner_id: str,
    metadata: dict,
    secret: str,
    context_id: str | None = None,
):
    """Enrich a successful display_file result using trusted caller metadata only."""
    if not isinstance(result, dict):
        return result
    result = {key: value for key, value in result.items() if key not in {'download_url', 'image_url', 'owner_id'}}
    path = result.get('full_path') or result.get('path')
    if result.get('exists') is not True or result.get('error') or not valid_file_path(path) or not secret:
        return result
    payload = {
        'v': 1,
        'owner_id': owner_id,
        'server_id': server_id,
        'path': path,
        'chat_id': metadata.get('chat_id'),
        'automation_id': metadata.get('automation_id'),
        'context_id': context_id,
    }
    reference = _serializer(secret).dumps(payload)
    base = f'/api/v1/terminals/{quote(server_id, safe="")}/files'
    query = urlencode({'ref': reference})
    result.update(owner_id=owner_id, download_url=f'{base}/download?{query}')
    if posixpath.splitext(path)[1].lower() in IMAGE_EXTENSIONS:
        result['image_url'] = f'{base}/image?{query}'
    return result


def file_response_headers(path: str, image_type: str | None = None) -> dict[str, str]:
    name = posixpath.basename(path.replace('\\', '/'))
    name = ''.join(char for char in name if ord(char) >= 32 and ord(char) != 127) or 'file'
    disposition = 'inline' if image_type else 'attachment'
    return {
        'Content-Type': image_type or 'application/octet-stream',
        'Content-Disposition': f"{disposition}; filename*=UTF-8''{quote(name, safe='')}",
        'X-Content-Type-Options': 'nosniff',
        'Content-Security-Policy': SVG_CSP if image_type == 'image/svg+xml' else FILE_CSP,
        'Cache-Control': 'private, no-store',
        'Cross-Origin-Resource-Policy': 'same-origin',
        'Referrer-Policy': 'no-referrer',
        'X-Frame-Options': 'DENY',
    }


def detect_image_type(data: bytes) -> str | None:
    if not data or len(data) > MAX_IMAGE_BYTES:
        return None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                mime = {'PNG': 'image/png', 'JPEG': 'image/jpeg', 'GIF': 'image/gif', 'WEBP': 'image/webp'}.get(
                    image.format
                )
                if mime:
                    image.verify()
                    return mime
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        pass
    try:
        parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True, huge_tree=False)
        root = etree.fromstring(data, parser=parser)
        tree = root.getroottree()
        if root.tag != '{http://www.w3.org/2000/svg}svg' or tree.docinfo.doctype:
            return None
        # No XSLT processing instructions, DTD/entity expansion or server-side
        # sanitization. Browser image mode and CSP isolate the original SVG.
        if tree.xpath('//processing-instruction()'):
            return None
        return 'image/svg+xml'
    except (etree.XMLSyntaxError, ValueError):
        return None
