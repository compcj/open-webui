"""Trusted final-send hooks and a bounded context-limit recovery.

Recovery retries only the provider send: middleware/tool execution is never run
again. Stream preludes are held until the first visible delta, allowing an early
provider context error to be recovered without duplicate response content.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException
from starlette.responses import JSONResponse, StreamingResponse


def is_context_limit_error(error: Any) -> bool:
    if isinstance(error, dict):
        error = error.get('error', error)
        if isinstance(error, dict):
            code = str(error.get('code') or error.get('type') or '').lower()
            if code in {'context_length_exceeded', 'context_window_exceeded', 'max_context_length_exceeded'}:
                return True
            error = error.get('message') or error.get('detail') or ''
    message = str(error).lower()
    return any(
        phrase in message
        for phrase in (
            'maximum context length',
            'context length exceeded',
            'context_length_exceeded',
            'context window exceeded',
            'exceeds the context window',
            'prompt is too long',
            'input length exceeds',
            'input tokens exceed',
        )
    )


async def apply_context_compaction(request, user, payload: dict, metadata: dict | None, model_id: str) -> dict:
    from open_webui.utils.context_compaction_v2 import is_internal_compaction, prepare_v2_request

    state = getattr(request.state, 'context_compaction', None)
    if state and state.get('mode') == 'v2' and not is_internal_compaction():
        # Provider-owned conversation history cannot be measured or compacted locally.
        payload.pop('previous_response_id', None)
        models = request.app.state.MODELS
        if getattr(request.state, 'direct', False) and hasattr(request.state, 'model'):
            models = {**dict(models.items()), request.state.model['id']: request.state.model}
        payload = await prepare_v2_request(
            request, user, payload, metadata or {}, model_id, models, force=bool(state.get('force'))
        )
    # Server-only source identity must never become an inference parameter.
    payload['messages'] = [
        {
            key: value
            for key, value in message.items()
            if key not in {'_context_message_id', '_context_original_key', '_tool_result_refs'}
        }
        for message in payload.get('messages', [])
    ]
    return payload


def response_error(response):
    if isinstance(response, JSONResponse) and response.status_code >= 400:
        try:
            return json.loads(response.body)
        except (ValueError, TypeError):
            return response.body.decode('utf-8', errors='replace')
    if isinstance(response, dict) and response.get('error'):
        return response['error']
    return None


def stream_event_state(text: str) -> tuple[bool, bool]:
    """Return (explicit context error, visible content/end) for complete lines."""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith('data:'):
            line = line[5:].strip()
        if line == '[DONE]':
            return False, True
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(event, dict):
            continue
        if event.get('error'):
            return is_context_limit_error(event), not is_context_limit_error(event)
        for choice in event.get('choices') or []:
            delta = choice.get('delta') or choice.get('message') or {}
            if any(value for key, value in delta.items() if key != 'role') or choice.get('finish_reason'):
                return False, True
        # Native Ollama and Responses events, before conversion if ever called there.
        message = event.get('message') or {}
        if (
            message.get('content')
            or message.get('thinking')
            or message.get('tool_calls')
            or event.get('delta')
            or event.get('done')
        ):
            return False, True
    return False, False


async def run_with_context_retry(request, send):
    from open_webui.utils.context_compaction_v2 import is_internal_compaction

    state = getattr(request.state, 'context_compaction', None)
    if not state or state.get('mode') != 'v2' or is_internal_compaction():
        return await send()

    def can_retry():
        return not state.get('context_limit_retried', False)

    async def record_usage(event):
        if not isinstance(event, dict):
            return
        usage = event.get('usage') or event
        if not any(usage.get(key) for key in ('prompt_tokens', 'input_tokens', 'prompt_eval_count')):
            return
        from open_webui.utils.context_compaction_v2 import record_provider_usage

        record_provider_usage(state, usage)

    async def invoke(force=False):
        if force:
            state['context_limit_retried'] = True
            state['force'] = True
        try:
            result = await send()
            await record_usage(result)
            return result
        finally:
            if force:
                state.pop('force', None)

    try:
        response = await invoke()
    except Exception as exc:
        if not can_retry() or not is_context_limit_error(getattr(exc, 'detail', exc)):
            raise
        response = await invoke(force=True)
    if can_retry() and is_context_limit_error(response_error(response)):
        response = await invoke(force=True)
    if not isinstance(response, StreamingResponse):
        return response

    original = response
    cleaned = set()

    async def cleanup(item):
        if id(item) in cleaned:
            return
        cleaned.add(id(item))
        close = getattr(item.body_iterator, 'aclose', None)
        if close:
            await close()
        if item.background:
            await item.background()

    async def iterator():
        current = original
        pending = []
        pending_text = ''
        visible = False
        usage_buffer = ''
        try:
            async for chunk in current.body_iterator:
                usage_buffer += chunk.decode('utf-8', errors='replace') if isinstance(chunk, bytes) else str(chunk)
                while '\n' in usage_buffer:
                    line, usage_buffer = usage_buffer.split('\n', 1)
                    line = line.strip().removeprefix('data:').strip()
                    try:
                        event = json.loads(line)
                    except (ValueError, TypeError):
                        continue
                    await record_usage(event)
                if len(usage_buffer) > 65536:
                    usage_buffer = ''
                if visible:
                    yield chunk
                    continue
                pending.append(chunk)
                pending_text += chunk.decode('utf-8', errors='replace') if isinstance(chunk, bytes) else str(chunk)
                # An error JSON object may span network chunks. Only inspect complete lines.
                complete = pending_text.rsplit('\n', 1)[0] if '\n' in pending_text else ''
                overflow, content = stream_event_state(complete)
                if overflow and can_retry():
                    await cleanup(current)
                    current = await invoke(force=True)
                    if isinstance(current, StreamingResponse):
                        async for retry_chunk in current.body_iterator:
                            yield retry_chunk
                    else:
                        error = response_error(current)
                        if error:
                            raise HTTPException(status_code=400, detail=error)
                        raise HTTPException(
                            status_code=502, detail='The provider did not return a stream after context recovery.'
                        )
                    return
                if content or overflow or len(pending_text) > 65536:
                    visible = True
                    for buffered in pending:
                        yield buffered
                    pending.clear()
                    pending_text = ''
            for buffered in pending:
                yield buffered
        finally:
            if isinstance(current, StreamingResponse):
                await cleanup(current)
            await cleanup(original)

    return StreamingResponse(
        iterator(),
        status_code=original.status_code,
        headers={key: value for key, value in original.headers.items() if key.lower() != 'content-length'},
        media_type=original.media_type,
    )
