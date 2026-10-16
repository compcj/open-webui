"""HTTP lifecycle for native model discovery and normalized chat streams."""

import asyncio
import json

import aiohttp
from fastapi import HTTPException

from open_webui.env import AIOHTTP_CLIENT_SESSION_SSL, AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST
from open_webui.utils.headers import get_headers_and_cookies
from open_webui.utils.native_providers import normalize_models, stream_response, validate_connection
from open_webui.utils.session_pool import cleanup_response, get_session, stream_wrapper


async def discover_native_models(request, url, key, config, user=None):
    validate_connection(config)
    protocol = config['api_type']
    headers, cookies = await get_headers_and_cookies(request, url, key, config, user=user)
    session = await get_session()
    result, seen, cursor = [], set(), None
    for _ in range(1000):
        params = {'pageSize': 1000} if protocol == 'gemini' else {'limit': 1000}
        if cursor:
            params['pageToken' if protocol == 'gemini' else 'after_id'] = cursor
        response = None
        try:
            response = await session.request(
                method='GET',
                url=f'{url.rstrip("/")}/models',
                params=params,
                headers=headers,
                cookies=cookies,
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
                timeout=aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST),
            )
            try:
                data = await response.json()
            except (ValueError, aiohttp.ContentTypeError) as exc:
                raise HTTPException(status_code=502, detail='Invalid native model discovery response') from exc
            if response.status >= 400:
                retry_headers = {
                    k: v for k, v in response.headers.items() if k.lower() in ('retry-after', 'retry-after-ms')
                }
                raise HTTPException(status_code=response.status, detail=data, headers=retry_headers)
            if not isinstance(data, dict) or not isinstance(
                data.get('models' if protocol == 'gemini' else 'data'), list
            ):
                raise HTTPException(status_code=502, detail='Invalid native model discovery response')
            result.extend(normalize_models(protocol, data)['data'])
            cursor = (
                data.get('nextPageToken')
                if protocol == 'gemini'
                else data.get('last_id')
                if data.get('has_more')
                else None
            )
            if protocol == 'anthropic' and data.get('has_more') and not cursor:
                raise HTTPException(status_code=502, detail='Missing native model pagination cursor')
            if not cursor:
                return {'object': 'list', 'data': result}
            if not isinstance(cursor, str) or cursor in seen:
                raise HTTPException(status_code=502, detail='Invalid native model pagination cursor')
            seen.add(cursor)
        except HTTPException:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, KeyError, TypeError) as exc:
            raise HTTPException(status_code=502, detail='Native model discovery failed') from exc
        finally:
            await cleanup_response(response)
    raise HTTPException(status_code=502, detail='Native model discovery exceeded the pagination limit')


async def native_chat_stream(response, protocol, model, binding):
    """Abort failed/truncated streams before the caller can dispatch partial tools."""
    source = stream_wrapper(response, passthrough=True)
    try:
        async for chunk in stream_response(protocol, source, model, binding):
            yield chunk
    except (ValueError, KeyError, TypeError) as exc:
        # Direct API clients receive an explicit error too. Raising after it is
        # essential: middleware must not execute tool calls from an invalid round.
        yield (
            'data: ' + json.dumps({'error': {'message': str(exc), 'type': 'native_provider_error'}}) + '\n\n'
        ).encode()
        raise
    finally:
        await source.aclose()
