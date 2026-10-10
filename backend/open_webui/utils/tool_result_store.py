"""Shared authenticated reader for exact, bounded original tool-result pages."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import struct

from fastapi import HTTPException
from open_webui.models.chat_tool_results import ChatToolResults

DEFAULT_PAGE_TOKENS = 2048
MAX_PAGE_TOKENS = 8192


def get_tool_result_store():
    return ChatToolResults


def _serialize(page: dict) -> str:
    return json.dumps(page, ensure_ascii=False, separators=(',', ':'))


def _cursor_signature(record, branch_id, query, offset: bytes) -> bytes:
    from open_webui.env import WEBUI_SECRET_KEY

    binding = [record['id'], record['chat_id'], record['source_hash'], record['source_fingerprint'], branch_id, query]
    data = json.dumps(binding, ensure_ascii=False, separators=(',', ':')).encode('utf-8') + offset
    return hmac.new(WEBUI_SECRET_KEY.encode('utf-8'), data, hashlib.sha256).digest()[:16]


def _encode_cursor(record, branch_id, query, start: int) -> str:
    anchor = branch_id.encode('utf-8')
    if not 1 <= len(anchor) <= 1024:
        raise HTTPException(status_code=400, detail='Invalid tool-result branch identifier.')
    offset = struct.pack('>Q', start)
    payload = b'\x02' + struct.pack('>H', len(anchor)) + anchor + offset
    signature = _cursor_signature(record, branch_id, query, payload)
    return base64.urlsafe_b64encode(payload + signature).decode().rstrip('=')


def _decode_cursor(record, branch_id, query, cursor) -> int:
    try:
        if not isinstance(cursor, str) or not 32 <= len(cursor) <= 1402:
            raise ValueError
        data = base64.b64decode((cursor + '=' * (-len(cursor) % 4)).encode(), altchars=b'-_', validate=True)
        anchors = record.get('_cursor_branch_ids', [branch_id])
        if len(cursor) == 32 and len(data) == 24:
            # Previous cursors did not embed their issuing branch. Verify the
            # signature against only current, source-validated ancestors.
            offset, signature = data[:8], data[8:]
            if not any(
                hmac.compare_digest(signature, _cursor_signature(record, anchor, query, offset)) for anchor in anchors
            ):
                raise ValueError
        else:
            if len(data) < 28 or data[0] != 2:
                raise ValueError
            size = struct.unpack('>H', data[1:3])[0]
            if not 1 <= size <= 1024 or len(data) != 27 + size:
                raise ValueError
            anchor = data[3 : 3 + size].decode('utf-8')
            if anchor not in anchors:
                raise ValueError
            payload, signature = data[:-16], data[-16:]
            if not hmac.compare_digest(signature, _cursor_signature(record, anchor, query, payload)):
                raise ValueError
            offset = data[3 + size : 11 + size]
        start = struct.unpack('>Q', offset)[0]
        if start > len(record['text']):
            raise ValueError
        return start
    except (ValueError, TypeError, UnicodeError, struct.error):
        raise HTTPException(
            status_code=400, detail='Invalid tool-result cursor or changed source/query/branch.'
        ) from None


async def _readable_result(user, chat_id, result_id, branch_id):
    from open_webui.models.chats import Chats

    if user is None or getattr(user, 'role', None) not in {'user', 'admin'}:
        raise HTTPException(status_code=404, detail='Tool result not found.')
    if not await Chats.get_chat_by_id_for_user(chat_id, user):
        raise HTTPException(status_code=404, detail='Tool result not found.')
    record = await get_tool_result_store().get_result(result_id, branch_id=branch_id)
    if record is None or record['chat_id'] != chat_id:
        raise HTTPException(status_code=404, detail='Tool result not found on this branch, or its source changed.')
    from open_webui.internal.db import get_async_db_context
    from open_webui.models.chat_context_checkpoints import read_snapshot_in_session
    from open_webui.models.chat_tool_results import branch_chain, source_state

    async with get_async_db_context() as db:
        snapshot = await read_snapshot_in_session(db, chat_id)
    chain = branch_chain(snapshot['messages_map'], branch_id) if snapshot else None
    if not chain or source_state(snapshot, record, branch_id) != (record['source_hash'], record['source_fingerprint']):
        raise HTTPException(status_code=404, detail='Tool result not found on this branch, or its source changed.')
    source_index = next(i for i, message in enumerate(chain) if message['id'] == record['message_id'])
    record['_cursor_branch_ids'] = [message['id'] for message in chain[source_index:]]
    return record


def _bounded_page(record, *, branch_id, query, request_cursor, start, max_tokens, end_limit=None):
    from open_webui.utils.context_compaction_v2 import estimate_text

    if type(max_tokens) is not int or max_tokens < 1:
        raise HTTPException(status_code=400, detail='max_tokens must be a positive integer.')
    max_tokens = min(max_tokens, MAX_PAGE_TOKENS)
    text = record['text']
    total = len(text)
    last = total if end_limit is None else min(total, end_limit)

    def page(end):
        return {
            'result_id': record['id'],
            'text': text[start:end],
            'start': start,
            'end': end,
            'total_chars': total,
            'next_cursor': _encode_cursor(record, branch_id, query, end) if end < total else None,
            'source': {key: record[key] for key in ('name', 'call_id', 'batch_id', 'message_id', 'output_id')},
            'incomplete': start > 0 or end < total,
            'query': query,
            'request_cursor': request_cursor,
        }

    empty = page(start)
    if estimate_text(_serialize(empty)) > max_tokens:
        raise HTTPException(
            status_code=400,
            detail='The tool-result page metadata exceeds the allocated token budget. '
            'Increase this result allocation and read it again.',
        )
    # Bound candidate characters before tokenizing: adversarial multi-megabyte
    # results should not repeatedly encode the entire raw source for a small page.
    high = min(last, start + max_tokens * 16)
    low = start
    best = empty
    while low <= high:
        middle = (low + high) // 2
        candidate = page(middle)
        if estimate_text(_serialize(candidate)) <= max_tokens:
            best, low = candidate, middle + 1
        else:
            high = middle - 1
    if best['end'] == start and start < last:
        raise HTTPException(
            status_code=400, detail='Increase this result allocation to include an exact source excerpt.'
        )
    return best


async def read_tool_result_page(
    user, chat_id, result_id, *, branch_id, query=None, cursor=None, max_tokens=DEFAULT_PAGE_TOKENS
) -> dict:
    record = await _readable_result(user, chat_id, result_id, branch_id)
    if query is not None and (not isinstance(query, str) or len(query) > 1024):
        raise HTTPException(status_code=400, detail='query must contain at most 1024 characters.')
    query = query or None
    if cursor:
        start = _decode_cursor(record, branch_id, query, cursor)
    elif query:
        # Casefolding can change character positions. Locate matches in the exact
        # original text with a regex so returned scalar offsets remain truthful.
        import re

        match = re.search(re.escape(query), record['text'], re.IGNORECASE)
        if match is None:
            raise HTTPException(status_code=404, detail='No matching passage in the original tool result.')
        start = match.start()
    else:
        start = 0
    return _bounded_page(
        record, branch_id=branch_id, query=query, request_cursor=cursor, start=start, max_tokens=max_tokens
    )


async def resize_tool_result_page(user, chat_id, page: dict, *, branch_id: str, max_tokens: int) -> dict:
    """Shrink only exact evidence, regenerating a valid cursor at the shorter end."""
    record = await _readable_result(user, chat_id, page.get('result_id'), branch_id)
    start, end = page.get('start'), page.get('end')
    if (
        type(start) is not int
        or type(end) is not int
        or not 0 <= start <= end <= len(record['text'])
        or page.get('text') != record['text'][start:end]
        or page.get('total_chars') != len(record['text'])
    ):
        raise HTTPException(status_code=400, detail='The supplied page is not an exact original source excerpt.')
    query = page.get('query') or None
    cursor = page.get('request_cursor')
    if cursor and _decode_cursor(record, branch_id, query, cursor) != start:
        raise HTTPException(status_code=400, detail='The supplied page cursor does not match its source position.')
    return _bounded_page(
        record,
        branch_id=branch_id,
        query=query,
        request_cursor=cursor,
        start=start,
        max_tokens=max_tokens,
        end_limit=end,
    )
