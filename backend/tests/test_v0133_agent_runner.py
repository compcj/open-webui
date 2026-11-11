"""Native agent stop decisions require both remote completion and local publication."""

import asyncio
import copy
import hashlib
import logging
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from test_v0133_agents import agent_socket, transport
from test_web_tool_citations import load_functions


class CompletionUnknown(RuntimeError):
    pass


@pytest.fixture
def runner():
    trace = []
    terminal = transport()
    record = {
        'state': 'running',
        'task_id': 'task',
        'chat_id': 'chat',
        'message_id': 'reply',
        'user_id': 'author',
        'terminal_id': 'terminal',
        'terminal_identity': hashlib.sha256(terminal.url.encode()).hexdigest(),
        'session_id': terminal.session_id,
    }

    async def lookup(chat_id, message_id):
        assert (chat_id, message_id) == ('chat', 'reply')
        return dict(record)

    async def update(chat_id, message_id, task_id, **changes):
        assert (chat_id, message_id, task_id) == ('chat', 'reply', 'task')
        trace.append(('record', dict(changes)))
        record.update(changes)
        return True

    async def acquire(chat_id, message_id, task_id, attempt_id, *, recover_unpublished=False):
        assert (chat_id, message_id, task_id) == ('chat', 'reply', 'task')
        record['stop_attempt'] = {'id': attempt_id}
        if recover_unpublished and record.get('output_published') is not True:
            record['stop_recovering'] = True
        return dict(record)

    output = SimpleNamespace(items=[{'type': 'message', 'status': 'in_progress'}])

    async def flush(**changes):
        trace.append(('flush', dict(changes)))

    async def emit(event):
        trace.append(('emit', event))

    output.flush, output.emitter = flush, emit
    namespace = {
        'asyncio': SimpleNamespace(
            current_task=asyncio.current_task, CancelledError=asyncio.CancelledError, sleep=AsyncMock()
        ),
        'hashlib': hashlib,
        'uuid4': uuid4,
        'local_tasks': {},
        'Chats': SimpleNamespace(
            get_agent_execution=lookup,
            acquire_agent_stop=acquire,
            update_agent_execution=update,
        ),
        'TerminalTransport': SimpleNamespace(resolve=AsyncMock(return_value=terminal)),
        'RemoteCompletionUnconfirmed': CompletionUnknown,
        'AgentOutputRetentionError': terminal._pump.__globals__['AgentOutputRetentionError'],
        'log': logging.getLogger(__name__),
    }
    load_functions(namespace, 'utils/agents/runner.py', {'confirm_remote_completion', 'retry_agent_stop'})
    terminal.drain_available_output = AsyncMock()
    namespace['AGENT_OUTPUT_INCOMPLETE_ERROR'] = {
        'content': 'Agent output may be incomplete.',
        'code': 'agent_output_incomplete',
    }
    return SimpleNamespace(namespace=namespace, trace=trace, record=record, transport=terminal, output=output)


@pytest.mark.asyncio
@pytest.mark.parametrize('published', [False, True])
async def test_recovery_ack_does_not_complete_unpublished_output(runner, published):
    runner.record['output_published'] = published
    runner.transport.request = AsyncMock(return_value={'status': True})
    result = await runner.namespace['retry_agent_stop'](object(), SimpleNamespace(id='author'), dict(runner.record))
    runner.transport.request.assert_awaited_once_with('DELETE', '/agents/sessions/remote-session')
    assert runner.record['remote_completed'] is True
    if published:
        assert result['status'] is True
        assert runner.record['state'] == 'completed'
        assert runner.record['complete_reply'] is True
    else:
        assert result['status'] is False
        assert runner.record['state'] != 'completed'
        assert all(not update.get('complete_reply') for kind, update in runner.trace if kind == 'record')


@pytest.mark.asyncio
async def test_repeated_cancellation_cannot_finish_without_executor_ack(runner):
    remote_calls = 0

    async def close():
        nonlocal remote_calls
        remote_calls += 1
        runner.trace.append(('close', remote_calls))
        if remote_calls < 3:
            raise asyncio.CancelledError
        return True

    runner.transport.close = close
    await runner.namespace['confirm_remote_completion'](runner.transport, runner.output, runner.record, succeeded=False)
    assert remote_calls == 3
    assert runner.record['state'] == 'completed'
    assert runner.output.items[0]['status'] == 'incomplete'
    published = next(
        i
        for i, (event, value) in enumerate(runner.trace)
        if event == 'flush' and value.get('persist') and not value.get('done')
    )
    assert runner.record['output_published'] is True
    assert published < runner.trace.index(('close', 1))
    done_indices = [i for i, (event, value) in enumerate(runner.trace) if event == 'flush' and value.get('done')]
    assert len(done_indices) == 1 and done_indices[0] > runner.trace.index(('close', 3))


@pytest.mark.asyncio
async def test_lost_start_response_keeps_reply_pending_until_external_confirmation(runner, caplog):
    runner.transport.session_id = None
    runner.transport.request = AsyncMock(side_effect=TimeoutError('synthetic-private-start-response'))
    with pytest.raises(TimeoutError):
        await runner.transport.start('codex', {'request_id': 'reply'})

    delayed_ack = []

    async def pause(_):
        delayed_ack.append(True)
        if len(delayed_ack) == 1:
            assert runner.record['state'] == 'unconfirmed'
            assert all(not value.get('done') for event, value in runner.trace if event == 'flush')
            runner.record['remote_completed'] = True
        else:
            # Bound the fixture even if the implementation fails to consume a remote acknowledgement.
            runner.record['state'] = 'completed'

    runner.namespace['asyncio'].sleep = pause
    await runner.namespace['confirm_remote_completion'](runner.transport, runner.output, runner.record, succeeded=False)
    assert delayed_ack == [True]
    assert runner.record['state'] == 'completed' and runner.record['output_published'] is True
    assert runner.transport.request.await_count == 1
    assert 'synthetic-private-start-response' not in caplog.text


@pytest.mark.asyncio
async def test_executor_ack_during_output_publication_avoids_redundant_delete(runner):
    flush = runner.output.flush

    async def publish_with_recovery_ack(**kwargs):
        await flush(**kwargs)
        if kwargs.get('persist') and not kwargs.get('done'):
            runner.record['remote_completed'] = True

    runner.output.flush = publish_with_recovery_ack
    runner.transport.request = AsyncMock(side_effect=ValueError('404'))
    await runner.namespace['confirm_remote_completion'](runner.transport, runner.output, runner.record, succeeded=False)
    assert runner.record['state'] == 'completed'
    runner.transport.request.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('acknowledged', [False, True])
async def test_worker_loss_force_stop_keeps_partial_output_and_requires_remote_ack(runner, acknowledged):
    runner.record.update(output_journal_version=1, output_cursor=3)

    async def delete(method, path):
        assert (method, path) == ('DELETE', '/agents/sessions/remote-session')
        assert runner.record['output_published'] is True
        assert runner.record['output_incomplete'] is True
        if not acknowledged:
            raise ValueError('404')
        return {'status': True}

    runner.transport.request = AsyncMock(side_effect=delete)
    result = await runner.namespace['retry_agent_stop'](object(), SimpleNamespace(id='author'), dict(runner.record))
    runner.transport.request.assert_awaited_once()
    runner.transport.drain_available_output.assert_awaited_once()
    assert result['status'] is acknowledged
    assert runner.record['output_published'] is True
    assert runner.record['output_incomplete'] is True
    if acknowledged:
        assert 'incomplete' in result['message'].lower()
        assert runner.record['state'] == 'completed'
        assert runner.record['remote_completed'] is True
    else:
        assert runner.record['state'] == 'unconfirmed'
        assert runner.record.get('remote_completed') is not True


@pytest.mark.asyncio
async def test_recovery_does_not_compete_with_the_current_stop_attempt(runner):
    runner.record.update(output_journal_version=1, output_cursor=3, output_published=True)
    runner.namespace['Chats'].acquire_agent_stop = AsyncMock(return_value=None)
    runner.transport.request = AsyncMock(return_value={'status': True})
    result = await runner.namespace['retry_agent_stop'](object(), SimpleNamespace(id='author'), dict(runner.record))
    assert result['status'] is False
    runner.transport.request.assert_not_awaited()


@pytest.mark.asyncio
async def test_capacity_frame_is_retained_without_ack_before_force_stop(runner, monkeypatch):
    raw = '{"delta":"reserve this frame"}'
    sent, _ = agent_socket(monkeypatch, runner.transport, raw)
    stored = asyncio.Event()

    async def retain_at_capacity(sequence, data):
        assert (sequence, data) == (1, raw)
        runner.trace.append(('stored', data))
        stored.set()
        return 'capacity'

    runner.transport.request = AsyncMock(return_value={'status': True})
    runner.transport.start_receiving(retain_at_capacity)
    try:
        await asyncio.wait_for(stored.wait(), 1)
        await runner.namespace['confirm_remote_completion'](
            runner.transport, runner.output, runner.record, succeeded=False
        )
    finally:
        if runner.transport.task:
            runner.transport.task.cancel()
            await asyncio.gather(runner.transport.task, return_exceptions=True)
    assert not any(frame['type'] == 'ack' for frame in sent)
    assert runner.record['output_incomplete'] is True
    assert runner.record['state'] == 'completed'
    assert any(
        event == 'flush' and value.get('error', {}).get('code') == 'agent_output_incomplete'
        for event, value in runner.trace
    )
    runner.transport.request.assert_awaited_once_with('DELETE', '/agents/sessions/remote-session')


@pytest.mark.asyncio
async def test_cleanup_retries_interrupted_frame_before_marking_output_published(runner, monkeypatch):
    raw = ' {"delta":"interrupted write"} '
    sent, _ = agent_socket(monkeypatch, runner.transport, raw)
    journal_started = asyncio.Event()
    attempts = []

    async def journal(sequence, data):
        attempts.append((sequence, data))
        if len(attempts) == 1:
            journal_started.set()
            await asyncio.Future()
        runner.trace.append(('journal', sequence))
        return True

    runner.transport.request = AsyncMock(return_value={'status': True})
    runner.transport.start_receiving(journal)
    try:
        await asyncio.wait_for(journal_started.wait(), 1)
        await runner.namespace['confirm_remote_completion'](
            runner.transport, runner.output, runner.record, succeeded=False
        )
    finally:
        if runner.transport.task:
            runner.transport.task.cancel()
            await asyncio.gather(runner.transport.task, return_exceptions=True)
    assert attempts == [(1, raw), (1, raw)]
    assert not any(frame['type'] == 'ack' for frame in sent)
    published = next(
        (i, value)
        for i, (event, value) in enumerate(runner.trace)
        if event == 'record' and value.get('output_published')
    )
    assert runner.trace.index(('journal', 1)) < published[0]
    assert published[1]['output_cursor'] == 1
    runner.transport.request.assert_awaited_once_with('DELETE', '/agents/sessions/remote-session')


@pytest.mark.asyncio
async def test_retention_failure_blocks_ack_publication_and_remote_delete(runner, monkeypatch):
    raw = '{"delta":"beyond configured capacity"}'
    sent, _ = agent_socket(monkeypatch, runner.transport, raw)
    journal_started = asyncio.Event()

    async def refuse(sequence, data):
        assert (sequence, data) == (1, raw)
        journal_started.set()
        return False

    runner.transport.request = AsyncMock()
    runner.transport.start_receiving(refuse)

    class ProbeComplete(BaseException):
        pass

    async def check_blocked(delay):
        assert runner.record['state'] == 'unconfirmed'
        assert runner.record.get('output_published') is not True
        assert runner.transport.pending_output == (1, raw)
        assert runner.transport.cursor == 0
        raise ProbeComplete

    runner.namespace['asyncio'].sleep = check_blocked
    try:
        await asyncio.wait_for(journal_started.wait(), 1)
        with pytest.raises(ProbeComplete):
            await runner.namespace['confirm_remote_completion'](
                runner.transport, runner.output, runner.record, succeeded=False
            )
    finally:
        if runner.transport.task:
            runner.transport.task.cancel()
            await asyncio.gather(runner.transport.task, return_exceptions=True)
    assert not any(frame['type'] == 'ack' for frame in sent)
    assert any(
        event == 'emit' and value['data'].get('error', {}).get('code') == 'agent_output_retention_blocked'
        for event, value in runner.trace
    )
    runner.transport.request.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('old_binding', [None, 'digest-for-the-previous-url'])
async def test_agent_resume_rejects_unproven_or_changed_terminal_url(runner, old_binding):
    provider = AsyncMock()
    claim = AsyncMock(side_effect=AssertionError('Agent must reject the connection before claiming or starting'))
    agent = {'provider': 'codex', 'terminal_id': 'terminal', 'model': 'agent-model'}
    previous = {
        **agent,
        'purpose': 'terminal-agent',
        'user': 'author',
        'chat': 'chat',
        'session_id': 'private-provider-session',
        'cwd': '/old-environment',
        'terminal_identity': old_binding,
    }
    runner.transport.request = AsyncMock()
    runner.namespace.update(
        PROVIDERS={'codex': provider},
        is_saved_chat_id=lambda _: True,
        decode_token=lambda _: previous,
        Output=lambda *args: runner.output,
    )
    runner.namespace['Chats'].get_message_by_id_and_message_id = AsyncMock(
        return_value={
            'meta': {'agent': 'signed'},
            'childrenIds': ['next-user'],
        }
    )
    runner.namespace['Chats'].get_agent_execution = AsyncMock(return_value=None)
    runner.namespace['Chats'].claim_agent_execution = claim
    load_functions(runner.namespace, 'utils/agents/runner.py', {'run'})
    metadata = {
        **runner.record,
        'message_id': 'next-reply',
        'user_message_id': 'next-user',
        'user_message': {'parentId': 'previous'},
    }
    with pytest.raises(ValueError, match='terminal connection'):
        await runner.namespace['run'](object(), {}, SimpleNamespace(id='author'), metadata, {'agent': agent})
    claim.assert_not_awaited()
    provider.assert_not_awaited()
    runner.transport.request.assert_not_awaited()


@pytest.mark.asyncio
async def test_output_publication_retries_after_background_save_failure():
    namespace = {'asyncio': asyncio, 'copy': copy, 'time': time}
    load_functions(namespace, 'utils/agents/output.py', {'Output'})
    output = namespace['Output']({}, SimpleNamespace(id='author'), None, {})
    output.save = AsyncMock()
    output.emitter = AsyncMock()
    prior_save = asyncio.get_running_loop().create_future()
    prior_save.set_exception(ConnectionError('storage temporarily unavailable'))
    output.save_task = prior_save

    with pytest.raises(ConnectionError):
        await output.flush(persist=True)
    await output.flush(persist=True)
    output.save.assert_awaited_once_with(done=False)
    output.emitter.assert_awaited_once()


@pytest.mark.asyncio
async def test_agent_approval_polls_only_current_reply_output():
    resolution = {'action': 'approve', 'answers': {'question': 'synthetic'}}
    read = AsyncMock(return_value=[{'call_id': 'approval_token', 'agent_resolution': resolution}])
    namespace = {
        'asyncio': asyncio,
        'copy': copy,
        'time': time,
        'json': __import__('json'),
        'uuid4': lambda: SimpleNamespace(hex='token'),
        'Chats': SimpleNamespace(get_agent_approval_output=read),
    }
    load_functions(namespace, 'utils/agents/output.py', {'Output'})
    metadata = {'chat_id': 'chat', 'message_id': 'reply', 'task_id': 'task'}
    output = namespace['Output'](metadata, SimpleNamespace(id='author'), lambda: None, {})
    output.flush = AsyncMock()
    assert await output.approve('ask_user', {}, {}) == resolution
    read.assert_awaited_once_with('chat', 'reply', 'task')


@pytest.mark.asyncio
async def test_capacity_stop_does_not_replace_the_confirmed_incomplete_notice(runner):
    runner.transport.output_capacity_reached = True
    runner.transport.start = AsyncMock()
    runner.transport.start_receiving = lambda _journal: None
    provider = AsyncMock(side_effect=runner.namespace['AgentOutputRetentionError']('configured output capacity'))
    finalize = AsyncMock()
    runner.namespace.update(
        PROVIDERS={'codex': provider},
        is_saved_chat_id=lambda _: True,
        Output=lambda *args: runner.output,
        copy=copy,
        upload_files_to_terminal=AsyncMock(),
        confirm_remote_completion=finalize,
    )
    runner.namespace['Chats'].claim_agent_execution = AsyncMock(return_value=True)
    load_functions(runner.namespace, 'utils/agents/runner.py', {'run'})
    metadata = {**runner.record, 'initial_terminal_cwd': '/workspace'}
    agent = {'provider': 'codex', 'terminal_id': 'terminal', 'model': 'agent-model'}

    result = await runner.namespace['run'](object(), {}, SimpleNamespace(id='author'), metadata, {'agent': agent})

    assert result['status'] is True
    finalize.assert_awaited_once_with(runner.transport, runner.output, metadata, succeeded=False)
