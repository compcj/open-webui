import copy
from contextlib import asynccontextmanager
from unittest.mock import patch
import pytest
from fastapi import HTTPException
import test_temporary_context_adapters as fixtures
from open_webui.utils import temporary_contexts as scopes
from open_webui.utils.context_store_errors import ContextConflict


@asynccontextmanager
async def context():
    gen = fixtures.context.__wrapped__()
    try:
        yield await anext(gen)
    finally:
        await gen.aclose()


def args(ctx):
    return ctx.doc['id'], 'owner', ctx.doc['epoch']


async def begin(ctx):
    record, fresh = await ctx.repo.begin_execution(
        *args(ctx),
        task_id='task',
        identity=dict(round_id='round', ordinal=0, call_id='call', reply_id='a', model_id='model'),
        target_hash='target',
    )
    assert fresh
    return record['id']


def output(text, call_id='call'):
    return [
        dict(type='function_call', id=call_id, call_id=call_id, name='search', arguments='{}'),
        dict(
            type='function_call_output',
            id='out',
            call_id=call_id,
            output=[dict(type='input_text', text=text)],
            tool_result=dict(batch_id='batch', name='search', kind='native'),
        ),
    ]


async def reject(coro):
    try:
        await coro
    except ContextConflict:
        return
    raise AssertionError('invalid commit accepted')


@pytest.mark.asyncio
async def test_late_metadata_entry():
    async with context() as ctx:
        await begin(ctx)
        await ctx.repo.close(*args(ctx))
        await ctx.repo.acknowledge_task_done(*args(ctx), task_id='task')
        metadata = dict(
            user_id='owner',
            chat_id=ctx.scope.store_key,
            message_id='a',
            task_id='task',
            temporary_context=scopes.scope_metadata(ctx.scope),
        )
        with patch.object(scopes, '_deployment_repository', ctx.repo):
            bound = await scopes.store_from_metadata(metadata, settlement=True)
        assert bound.scope.task_id == 'task'


@pytest.mark.asyncio
async def test_late_result_read():
    async with context() as ctx:
        eid = await begin(ctx)
        await ctx.repo.close(*args(ctx))
        await ctx.repo.acknowledge_task_done(*args(ctx), task_id='task')
        await ctx.store.save_execution_result(eid, {'content': 'late'})
        assert (await ctx.store.execution_result(eid))['content'] == 'late'


@pytest.mark.asyncio
async def test_empty_result():
    async with context() as ctx:
        eid = await begin(ctx)
        await ctx.store.save_execution_result(eid, {'content': ''})
        raw = output('')
        raw[1]['output'] = []
        await ctx.store.commit_raw_results('a', {'out': eid}, raw, [])
        assert (await ctx.store.execution_result(eid))['content'] == ''


@pytest.mark.asyncio
async def test_media_not_discarded():
    async with context() as ctx:
        eid = await begin(ctx)
        await ctx.store.save_execution_result(
            eid,
            {
                'content': '',
                'files': [{'id': 'image-original', 'url': 'https://example.invalid/image.png'}],
                'embeds': [{'url': 'https://example.invalid/embed'}],
            },
        )
        await reject(ctx.store.commit_raw_results('a', {'out': eid}, output(''), []))


@pytest.mark.asyncio
async def test_call_binding():
    async with context() as ctx:
        eid = await begin(ctx)
        await ctx.store.save_execution_result(eid, {'content': 'same text'})
        await reject(ctx.store.commit_raw_results('a', {'out': eid}, output('same text', call_id='different-call'), []))


@pytest.mark.asyncio
async def test_admitted_retry_immutable():
    async with context() as ctx:
        eid = await begin(ctx)
        await ctx.store.save_execution_result(eid, {'content': 'original'})
        await ctx.store.commit_raw_results('a', {'out': eid}, output('original'), [])
        await reject(ctx.store.commit_raw_results('a', {'out': eid}, output('tampered'), []))


@pytest.mark.asyncio
async def test_alias_and_sidecar_echo():
    async with context() as ctx:
        state = await ctx.repo.read(*args(ctx))
        graph = dict(messages=copy.deepcopy(state['data']['snapshot']['messages_map']), currentId='leaf')
        graph['messages']['a']['parent_id'] = 'leaf'
        try:
            scopes.validate_temporary_history(graph)
        except HTTPException as exc:
            assert exc.status_code == 400
        else:
            raise AssertionError('divergent alias accepted')
        graph['messages']['a'].pop('parent_id')
        await scopes.sync_temporary_history(
            ctx.repo, 'owner', ctx.doc['id'], ctx.doc['epoch'], state['revision'], graph
        )
        sidecar = dict(id='side', text='text', call_id='call', batch_id='batch', name='tool', kind='native')
        await ctx.store.put_reply_patch('a', {'tool_results': [sidecar]})
        state = await ctx.repo.read(*args(ctx))
        graph = dict(messages=copy.deepcopy(state['data']['snapshot']['messages_map']), currentId='leaf')
        result = await scopes.sync_temporary_history(
            ctx.repo, 'owner', ctx.doc['id'], ctx.doc['epoch'], state['revision'], graph
        )
        assert result['revision'] == state['revision']
        assert (await ctx.repo.read(*args(ctx)))['data']['snapshot']['messages_map']['a']['tool_results'][0][
            'kind'
        ] == 'native'


from open_webui.utils.images.context import build_tool_result_parts


@pytest.mark.asyncio
async def test_context_only_media_round_trip():
    async with context() as ctx:
        eid = await begin(ctx)
        staged = {
            'content': '',
            'files': [{'type': 'image', 'url': 'https://example.invalid/generated.png', 'context_only': True}],
        }
        await ctx.store.save_execution_result(eid, staged)
        parts, display = build_tool_result_parts(staged)
        raw = output('')
        raw[1]['output'], raw[1]['files'] = parts, display
        await ctx.store.commit_raw_results('a', {'out': eid}, raw, [])
        replay = await ctx.store.execution_result(eid)
        assert build_tool_result_parts(replay) == (parts, display), 'image-only result lost image evidence on replay'


@pytest.mark.asyncio
async def test_prior_batch_immutable_with_incremental_new_mapping():
    async with context() as ctx:
        eid = await begin(ctx)
        await ctx.store.save_execution_result(eid, {'content': 'original'})
        await ctx.store.commit_raw_results('a', {'out': eid}, output('original'), [])
        second, fresh = await ctx.repo.begin_execution(
            *args(ctx),
            task_id='task',
            identity=dict(round_id='next-round', ordinal=0, call_id='next-call', reply_id='a', model_id='model'),
            target_hash='next-target',
        )
        assert fresh
        await ctx.store.save_execution_result(second['id'], {'content': 'new result'})
        new = output('new result', call_id='next-call')
        new[1]['id'] = 'out-two'
        try:
            await ctx.store.commit_raw_results('a', {'out-two': second['id']}, output('tampered old result') + new, [])
        except ContextConflict:
            return
        replay = await ctx.store.execution_result(eid)
        assert replay['content'] == 'original', (
            'new batch changed previously admitted output omitted from execution_ids'
        )


@pytest.mark.asyncio
async def test_unpublished_result_blocks_close_until_raw_recovery():
    async with context() as ctx:
        eid = await begin(ctx)
        raw = output('durable')
        await ctx.store.save_execution_result(
            eid, {'content': 'durable', 'output_item': raw[1], 'publication': {'call_item': raw[0], 'sidecar': False}}
        )
        await ctx.repo.acknowledge_task_done(*args(ctx), task_id='task')
        closed = await ctx.repo.close(*args(ctx))
        assert closed['status'] == 'closing' and not closed['rebuild_allowed']
        assert scopes.public_state(await ctx.repo.read(*args(ctx), allow_closed=True), ctx.repo.config)['uncertain']
        assert await ctx.store.recover_publications()
        recovered = await ctx.repo.read(*args(ctx), allow_closed=True)
        assert recovered['status'] == 'closed_confirmed'
        assert recovered['data']['snapshot']['messages_map']['a']['output'] == raw
        assert not recovered['executions'][eid]['publication_pending']
        assert not await ctx.store.recover_publications()


@pytest.mark.asyncio
async def test_pending_publication_blocks_new_task_but_not_owned_batch():
    async with context() as ctx:
        eid = await begin(ctx)
        await ctx.store.save_execution_result(eid, {'content': 'first'})
        _, fresh = await ctx.repo.begin_execution(
            *args(ctx),
            task_id='task',
            identity=dict(round_id='round', ordinal=1, call_id='second', reply_id='a', model_id='model'),
            target_hash='next',
        )
        assert fresh
        doc = await ctx.repo.read(*args(ctx))
        with pytest.raises(ContextConflict):
            await ctx.repo.register_tasks(
                *args(ctx),
                [dict(task_id='new', reply_id='b', model_id='model', branch_id='u', worker_nonce='w')],
                expected_revision=doc['revision'],
            )


@pytest.mark.asyncio
async def test_recovery_completes_already_streamed_call():
    async with context() as ctx:
        eid = await begin(ctx)
        raw = output('complete')
        raw[0]['status'] = 'completed'
        await ctx.store.put_reply_patch('a', {'output': [{**raw[0], 'status': 'in_progress'}]})
        await ctx.store.save_execution_result(
            eid, {'content': 'complete', 'output_item': raw[1], 'publication': {'call_item': raw[0], 'sidecar': False}}
        )
        await ctx.repo.acknowledge_task_done(*args(ctx), task_id='task')
        await ctx.store.recover_publications()
        doc = await ctx.repo.read(*args(ctx))
        assert doc['data']['snapshot']['messages_map']['a']['output'] == raw


@pytest.mark.asyncio
async def test_completed_task_cannot_publish_an_empty_mapping_to_replace_raw():
    async with context() as ctx:
        await ctx.repo.acknowledge_task_done(*args(ctx), task_id='task')
        with pytest.raises(ContextConflict):
            await ctx.store.commit_raw_results('a', {}, [], [], settlement=True)
