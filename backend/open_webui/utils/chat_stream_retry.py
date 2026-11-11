"""Bounded recovery for an incomplete Chat Completions stream before output."""

import asyncio
import json
import logging

import aiohttp

log = logging.getLogger(__name__)


def _empty_chat_prelude(line: str) -> bool:
    """Only known, empty Chat Completions deltas are safe to discard on retry."""
    if not line.endswith('\n'):
        return False
    if not line.strip() or line.startswith(':'):
        return True
    if not line.startswith('data:'):
        return False
    try:
        data = json.loads(line[5:].strip())
    except (ValueError, TypeError):
        return False
    if not isinstance(data, dict) or not isinstance(data.get('choices'), list):
        return False
    if data.keys() - {'id', 'object', 'created', 'model', 'system_fingerprint', 'service_tier', 'choices', 'usage'}:
        return False
    if data.get('usage') not in (None, {}):
        return False
    for choice in data['choices']:
        if not isinstance(choice, dict) or choice.keys() - {'index', 'delta', 'finish_reason', 'logprobs'}:
            return False
        if choice.get('finish_reason') not in (None, '') or choice.get('logprobs') is not None:
            return False
        delta = choice.get('delta')
        if not isinstance(delta, dict) or delta.keys() - {'role', 'content', 'reasoning', 'reasoning_content'}:
            return False
        if delta.get('role') not in (None, 'assistant'):
            return False
        if any(value not in (None, '') for key, value in delta.items() if key != 'role'):
            return False
    return True


async def stream_chat_with_retry(source, retry_stream):
    """Retry one payload failure, keeping tool execution outside the retry boundary.

    ``source`` and the result of ``retry_stream`` yield complete SSE lines and own
    HTTP-response cleanup. Hold at most 64 KiB of empty preludes, forwarding
    complete comment lines while waiting. Any other event commits the stream.
    """
    current = source
    for attempt in range(2):
        pending = []
        event = []
        buffered_size = 0
        committed = False
        try:
            async for line in current:
                if committed:
                    yield line
                    continue
                text = line.decode('utf-8', errors='replace') if isinstance(line, bytes) else line
                if text.startswith(':') and text.endswith('\n'):
                    # SSE comments do not need an empty line to terminate.
                    # Keep downstream idle timers alive even with a role held.
                    yield line
                    continue
                event.append(line)
                buffered_size += len(line) if isinstance(line, bytes) else len(line.encode('utf-8'))
                if not _empty_chat_prelude(text) or buffered_size > 65536:
                    committed = True
                    for chunk in pending + event:
                        yield chunk
                    pending.clear()
                    event.clear()
                elif not text.strip():
                    if len(event) == 1:
                        yield line
                        buffered_size -= len(line) if isinstance(line, bytes) else len(line.encode('utf-8'))
                    else:
                        pending.extend(event)
                    event.clear()
            for chunk in pending + event:
                yield chunk
            return
        except aiohttp.ClientPayloadError:
            if attempt or committed:
                for chunk in pending + event:
                    yield chunk
                raise
        finally:
            await current.aclose()

        log.warning('Chat Completions payload interrupted before output; retrying the provider request once')
        # Leave a cancellation point between closing the first response and a
        # second HTTP send. Only the serialized provider request is repeated.
        await asyncio.sleep(0.5)
        current = await retry_stream()
