"""Keep upstream browser capabilities out of independent preview responses."""

import re
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

from fastapi import HTTPException


TEXT_RESOURCE_TYPES = {'application/javascript', 'application/json', 'application/xml', 'image/svg+xml'}
URL_START = rb"""(^|[\s"'(]|(?i:\s(?:href|src|action|poster)\s*=\s*))"""
URL_END = rb"""(?=$|[\s/?#\\'")\]},;>])"""


def decode_url_component(value, error):
    for _ in range(8):
        decoded = unquote(value)
        if decoded == value:
            return value
        value = decoded
    if unquote(value) != value:
        raise error
    return value


def rewrite_preview_location(upstream_url, location, upstream_prefix, preview_prefix):
    """Resolve a redirect at the terminal, then retain only its scoped suffix."""
    error = HTTPException(502, 'Unscoped browser redirect refused')
    if not location or any(char == '\\' or ord(char) <= 32 or ord(char) == 127 for char in location):
        raise error
    try:
        source = urlsplit(upstream_url)
        requested = urlsplit(location)
        if requested.scheme and requested.scheme != source.scheme:
            raise error
        # urljoin does not recognize the internal Tether scheme as hierarchical.
        scheme = 'http' if source.scheme == 'tether' else source.scheme
        base = source._replace(scheme=scheme).geturl()
        destination = requested._replace(scheme=scheme).geturl() if requested.scheme else location
        target = urlsplit(urljoin(base, destination))
        default_port = {'http': 80, 'https': 443}.get(source.scheme)
        if (
            target.scheme != scheme
            or target.hostname != source.hostname
            or (target.port or default_port) != (source.port or default_port)
            or target.username is not None
            or target.password is not None
        ):
            raise error
        root = urlsplit(upstream_prefix).path.rstrip('/')
    except ValueError:
        raise error from None

    path = target.path
    if not root or (path != root and not path.startswith(root + '/')):
        raise error
    decoded = decode_url_component(path, error)
    if any(char == '\\' or ord(char) < 32 or ord(char) == 127 for char in decoded) or {'.', '..'}.intersection(
        decoded.split('/')
    ):
        raise error
    token = unquote(root.rsplit('/', 1)[-1])
    if any(token in decode_url_component(component, error) for component in (target.query, target.fragment)):
        raise error
    path = preview_prefix.rstrip('/') + path[len(root) :]
    return urlunsplit(('', '', path, target.query, target.fragment))


def rewrite_preview_body(body, content_type, terminal_base, upstream_prefix, preview_prefix):
    """Rewrite textual URLs and reject any upstream capability that remains."""
    mime = content_type.partition(';')[0].strip().lower()
    if mime not in TEXT_RESOURCE_TYPES and not mime.startswith('text/') and not mime.endswith(('+json', '+xml')):
        return body

    root = urlsplit(upstream_prefix).path.rstrip('/')
    browser_root = root[root.index('/browser/view/') :]
    terminal = urlsplit(terminal_base)
    origin = f'{terminal.scheme}://{terminal.netloc}'
    variants = {
        root,
        root.lstrip('/'),
        browser_root,
        browser_root.lstrip('/'),
        origin + root,
        '//' + terminal.netloc + root,
        terminal_base.rstrip('/') + browser_root,
    }
    replacement = preview_prefix.rstrip('/')
    replacements = {value.encode(): replacement.encode() for value in variants}
    replacements.update(
        {value.replace('/', r'\/').encode(): replacement.replace('/', r'\/').encode() for value in variants}
    )
    alternatives = b'|'.join(re.escape(value) for value in sorted(replacements, key=len, reverse=True))
    pattern = re.compile(URL_START + b'(' + alternatives + b')' + URL_END)
    rewritten = pattern.sub(lambda match: match[1] + replacements[match[2]], body)

    token = root.rsplit('/', 1)[-1]
    if token.encode() in rewritten or unquote(token).encode() in rewritten:
        raise HTTPException(502, 'Unscoped browser capability refused')
    return rewritten
