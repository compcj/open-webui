"""Bounded source transport for direct knowledge imports; never a model text reader."""

import asyncio
import codecs
import io
import mimetypes
import posixpath
import re
import tempfile
import unicodedata
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit, urlunsplit


class ImportSourceError(ValueError):
    """A safe, user-facing import error, without remote exception text."""


async def settle(awaitable, on_cancel=None):
    """Finish started blocking work before its files or database state are cleaned up."""
    task = asyncio.ensure_future(awaitable)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        if on_cancel is not None:
            on_cancel()
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        # Retrieve a failure to avoid an unobserved task exception, then preserve cancellation.
        if not task.cancelled():
            task.exception()
        raise


def terminal_path(path):
    if (
        not isinstance(path, str)
        or not path.startswith('/')
        or path.startswith('//')
        or path.endswith('/')
        or posixpath.normpath(path) != path
        or '\\' in path
        or any(unicodedata.category(char).startswith('C') for char in path)
    ):
        raise ImportSourceError('A canonical absolute POSIX file path is required')
    return path


def check_size(size, max_bytes):
    if max_bytes and size > max_bytes:
        raise ImportSourceError('Source exceeds the configured file size limit')


async def spool_response(response, max_bytes):
    """Limit declared and actual decoded bytes; close a failed spool immediately."""
    response.raise_for_status()
    if response.status != 200:
        raise ImportSourceError('Source did not return a complete file')
    try:
        declared = int(response.headers.get('Content-Length', ''))
    except (TypeError, ValueError):
        declared = 0
    check_size(declared, max_bytes)
    spool = tempfile.SpooledTemporaryFile(max_size=1024 * 1024, mode='w+b')
    size = 0
    try:
        async for chunk in response.content.iter_chunked(64 * 1024):
            size += len(chunk)
            check_size(size, max_bytes)
            spool.write(chunk)
        spool.seek(0)
        return spool, size
    except BaseException:
        spool.close()
        raise


def response_filename(response, url):
    disposition = response.headers.get('Content-Disposition', '')
    extended = re.search(r"filename\*=UTF-8''([^;]+)", disposition, re.IGNORECASE)
    plain = re.search(r'filename="?([^";]+)"?', disposition, re.IGNORECASE)
    name = unquote(extended.group(1)) if extended else plain.group(1) if plain else ''
    return posixpath.basename(name or unquote(urlsplit(url).path)) or 'download'


def _parse_downloaded_html(loader, spool, url, charset):
    """Use the deployed basic parser on the bounded download, preserving HTTP charset."""
    try:
        codecs.lookup(charset)
    except LookupError:
        charset = 'utf-8'
    text = io.TextIOWrapper(spool, encoding=charset)
    try:
        return loader._document_from_html(text.read(), url)
    finally:
        text.detach()
        spool.seek(0)


async def fetch_url_source(request, url, max_bytes):
    from open_webui.env import AIOHTTP_CLIENT_ALLOW_REDIRECTS, AIOHTTP_CLIENT_SESSION_SSL, USER_AGENT
    from open_webui.models.config import Config
    from open_webui.retrieval.web.utils import SafeWebBaseLoader, get_ssrf_safe_session, get_web_loader, validate_url

    if not isinstance(url, str) or not url or urlsplit(url).username or urlsplit(url).password:
        raise ImportSourceError('A HTTP(S) URL without embedded credentials is required')
    spool = None
    try:
        await settle(asyncio.to_thread(validate_url, url))
        async with get_ssrf_safe_session(store_cookies=False) as session:
            async with session.get(
                url,
                headers={'User-Agent': USER_AGENT} if USER_AGENT else {},
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
                allow_redirects=AIOHTTP_CLIENT_ALLOW_REDIRECTS,
            ) as response:
                spool, size = await spool_response(response, max_bytes)
                content_type = response.headers.get('Content-Type', '').split(';')[0].strip().lower()
                charset = getattr(response, 'charset', None) or 'utf-8'
                filename = response_filename(response, str(response.url))
                attachment = response.headers.get('Content-Disposition', '').lower().startswith('attachment')
                sample = spool.read(4096).lstrip().lower()
                spool.seek(0)
                web = not attachment and (
                    content_type in {'text/html', 'application/xhtml+xml'}
                    or (
                        content_type in {'', 'application/octet-stream', 'binary/octet-stream'}
                        and (
                            sample.startswith((b'<!doctype html', b'<html', b'<head', b'<body', b'<?xml'))
                            or b'<html' in sample[:1024]
                        )
                    )
                )
        if web:
            # Use the deployed web loader, including its main-text extraction and SSRF policy.
            from open_webui.routers.retrieval import RETRIEVAL_CONFIG_KEYS

            config = await Config.get_many(*RETRIEVAL_CONFIG_KEYS.values())
            loader = await settle(asyncio.to_thread(get_web_loader, [url], config))
            try:
                if isinstance(loader, SafeWebBaseLoader):
                    docs = [await settle(asyncio.to_thread(_parse_downloaded_html, loader, spool, url, charset))]
                else:
                    # Browser/external engines own their network/resource controls;
                    # their complete extracted UTF-8 output is bounded below.
                    docs = await settle(loader.aload())
            finally:
                if getattr(loader, 'session', None) is not None:
                    await settle(asyncio.to_thread(loader.session.close))
            text = '\n\n'.join(doc.page_content for doc in docs)
            if not text.strip():
                raise ImportSourceError('Source did not produce non-blank extracted text')
            encoded = text.encode('utf-8')
            check_size(len(encoded), max_bytes)
            spool.seek(0)
            spool.truncate()
            spool.write(encoded)
            spool.seek(0)
            size = len(encoded)
            parsed = urlsplit(url)
            safe_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, '', ''))
            filename = re.sub(r'[^a-zA-Z0-9]', '_', safe_url).lower()[:50] + '.txt'
            content_type = 'text/plain'
        else:
            extension_unspecified = not posixpath.splitext(filename)[1] and content_type in {
                '',
                'application/octet-stream',
                'binary/octet-stream',
            }
            content_type = content_type or mimetypes.guess_type(filename)[0] or 'application/octet-stream'
            if not posixpath.splitext(filename)[1]:
                filename += mimetypes.guess_extension(content_type) or '.bin'
        return SimpleNamespace(
            file=spool,
            size=size,
            filename=filename,
            content_type=content_type,
            source_type='url',
            extension_unspecified=False if web else extension_unspecified,
        )
    except BaseException:
        if spool is not None:
            spool.close()
        raise


async def terminal_access(request, user, metadata):
    from open_webui.routers.terminals import _http_terminal_context

    # This resolver accepts only administrator-stored connection IDs and rechecks
    # connection, actor, saved-chat access and chat/automation context availability.
    return await _http_terminal_context(metadata.get('terminal_id'), request, user, metadata, require_context=True)


async def fetch_terminal_source(request, user, metadata, path, max_bytes, binding=None):
    import aiohttp
    from urllib.parse import quote
    from open_webui.env import AIOHTTP_CLIENT_SESSION_TOOL_SERVER_SSL, AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER_DATA

    current = await terminal_access(request, user, metadata)
    if binding is not None and current != binding:
        raise ImportSourceError('Selected terminal connection or context changed; start a new import')
    base_url, headers, cookies, _ = binding if binding is not None else current
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER_DATA), trust_env=True
    ) as session:
        async with session.get(
            f'{base_url}/files/view?path={quote(terminal_path(path), safe="")}',
            headers=headers,
            cookies=cookies,
            ssl=AIOHTTP_CLIENT_SESSION_TOOL_SERVER_SSL,
            allow_redirects=False,
        ) as response:
            spool, size = await spool_response(response, max_bytes)
            filename = posixpath.basename(path)
            content_type = response.headers.get('Content-Type', '').split(';')[0].strip().lower()
            return SimpleNamespace(
                file=spool,
                size=size,
                filename=filename,
                content_type=content_type or mimetypes.guess_type(filename)[0] or 'application/octet-stream',
                source_type='terminal',
            )
