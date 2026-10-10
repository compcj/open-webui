"""Tool budgets protect complete calls and exact evidence; no app startup."""

import asyncio
import copy
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from test_context_compaction_v2 import IsolatedCase
import test_context_compaction_v2 as v2_test_helpers


class ToolBudgetTests(IsolatedCase):
    def test_percentage_defaults_and_strict_validation(self):
        cfg = importlib.import_module('open_webui.utils.context_compaction_config').CompactionSettings
        assert (cfg().tool_result_percent, cfg().tool_batch_percent, cfg().tool_budget_enabled) == (10, 30, True)
        for values in (
            {'tool_result_percent': 31},
            {'tool_batch_percent': 101},
            {'tool_result_percent': 0},
            {'tool_result_percent': 1.5},
            {'tool_batch_percent': True},
        ):
            with pytest.raises(ValueError):
                cfg(**values)
        assert cfg(tool_result_percent=100, tool_batch_percent=100).tool_batch_percent == 100

    def test_fair_caps_and_short_results(self):
        m = importlib.import_module('open_webui.utils.tool_result_budget')
        assert m.tool_limits(100000, SimpleNamespace(tool_result_percent=10, tool_batch_percent=30)) == (10000, 30000)
        assert m.fair_allocate([0, 100, 20000, 20000], 9000, 10000) == [0, 100, 4450, 4450]
        assert m.fair_allocate([8000] * 5, 30000, 10000) == [6000] * 5
        assert m.fair_allocate([20, 20, 20], 8, 100) == [3, 3, 2]
        assert m.fair_allocate([20000], 30000, 10000) == [10000]

    def test_excerpt_is_bounded_incomplete_and_preserves_error(self):
        m = importlib.import_module('open_webui.utils.tool_result_budget')
        v2 = importlib.import_module('open_webui.utils.context_compaction_v2')
        raw = 'ordinary detail\n' * 1000 + 'ERROR: /tmp/report.txt missing; exit=2\n' + 'tail\n' * 1000
        result = m.excerpt_result(raw, 200, 'result-1', 'terminal', 'report')
        assert v2.estimate_text(result) <= 200
        assert 'incomplete' in result.lower()
        assert 'result-1' in result
        assert 'ERROR: /tmp/report.txt missing' in result
        assert raw.endswith('tail\n' * 1000)

    def test_unbroken_sources_preserve_early_and_middle_evidence(self):
        tool = importlib.import_module('open_webui.utils.tool_result_budget')
        v2 = importlib.import_module('open_webui.utils.context_compaction_v2')
        early = 'ERROR: report.json missing; exit=2'
        middle = 'FAILED: evidence_mid.json not found; status=404'
        for prefix, suffix in (('', ''), ('{"result":"', '"}')):
            raw = (prefix + early).ljust(1200, 'x') + 'x' * 8050 + middle.ljust(1200, 'x') + 'x' * 6400 + suffix
            excerpt = tool.excerpt_result(raw, 300, 'long-result', 'search', 'report evidence_mid')
            assert early in excerpt
            assert middle in excerpt
            assert v2.estimate_text(excerpt) <= 300
            assert all(part in raw for part in excerpt.split('\n', 1)[1].split('\n[... omitted ...]\n'))


class ToolBudgetEngineTests(IsolatedCase):
    setup_engine = v2_test_helpers.EngineTests.setup_engine

    def make_batch(self, context=10000):
        v2, request, payload, history_store, events = self.setup_engine(context=context)
        raw = history_store.read_snapshot.return_value['messages_map']
        raw['a2'] = {'id': 'a2', 'parentId': 'u', 'role': 'assistant', 'output': [], 'content': ''}
        request.state.context_compaction['metadata'] = {'chat_id': 'chat', 'message_id': 'a2'}
        text = 'search evidence: 中文结果。' * 1200
        descriptor = dict(
            message_id='a2', output_id='out', call_id='c', batch_id='batch', name='search', kind='native', text=text
        )
        raw['a2']['output'] = [
            {'id': 'call', 'call_id': 'c', 'type': 'function_call', 'status': 'completed'},
            {'id': 'out', 'call_id': 'c', 'type': 'function_call_output', 'output': text, 'status': 'completed'},
        ]
        payload['messages'] += [
            {
                'role': 'assistant',
                '_context_message_id': 'a2',
                'tool_calls': [{'id': 'c', 'type': 'function', 'function': {'name': 'search', 'arguments': '{}'}}],
            },
            {
                'role': 'tool',
                '_context_message_id': 'a2',
                'tool_call_id': 'c',
                'content': text,
                '_tool_result_refs': [descriptor],
            },
        ]
        service = SimpleNamespace(
            save_result=AsyncMock(return_value={**descriptor, 'id': 'result-1', 'source_hash': 'hash'}),
            list_contexts=AsyncMock(return_value=[]),
            get_context=AsyncMock(return_value=None),
            save_context=AsyncMock(return_value={'ok': True}),
        )
        mod = importlib.import_module('open_webui.utils.tool_result_budget')
        from test_tool_result_replay import MemorySelections

        selections = MemorySelections(payload=payload, snapshot=history_store.read_snapshot.return_value)

        async def save_context(result_id, **kw):
            saved = {'id': result_id + str(kw['budget']), **kw}
            selections.contexts[saved['id']] = saved
            return saved

        service.save_context.side_effect = save_context
        service.selections = selections
        patch.object(mod, 'get_tool_selection_store', return_value=selections).start()
        patch.object(mod, 'get_tool_result_store', return_value=service).start()
        return v2, mod, request, payload, history_store, service, events

    def test_history_compressed_before_incoming_tool_summary_and_raw_keys_preserved(self):
        v2, tool, request, payload, history, service, events = self.make_batch(context=3500)
        payload['messages'][1]['content'] = 'old fact ' * 1500
        history.read_snapshot.return_value['messages_map']['old']['content'] = payload['messages'][1]['content']
        original = copy.deepcopy(payload)
        calls = []

        async def summarize(
            req, user, model, models, messages, previous, settings, *, purpose='history', output_budget=None
        ):
            if purpose == 'tool':
                calls.append('tool')
                return 'Search succeeded. Exact resource: https://example.test/page.'
            calls.append('history')
            assert all(m.get('tool_call_id') != 'c' for m in messages)
            return 'Earlier task progress.'

        with patch.object(v2, 'generate_summary', side_effect=summarize):
            result = asyncio.run(v2.prepare_v2_request(request, None, payload, {}, 'model', {'model': {}}))
        assert calls[0] == 'history' and 'tool' in calls[1:]
        assert v2.estimate_payload_tokens(result) <= 3325
        assert payload == original
        toolmsg = next(m for m in result['messages'] if m.get('role') == 'tool')
        assert toolmsg['tool_call_id'] == 'c' and 'result-1' in toolmsg['content']
        assert v2.message_key(toolmsg) == v2.message_key(original['messages'][-1])
        assert any(kind == 'tool_result_compaction' and data['method'] == 'summary' for kind, data in events)

    def test_history_failure_uses_actual_remaining_without_dropping_history(self):
        v2, tool, request, payload, history, service, events = self.make_batch(context=4000)
        request.state.context_compaction['settings'] = request.state.context_compaction['settings'].model_copy(
            update={'trigger_percent': 30}
        )
        with patch.object(v2, 'generate_summary', AsyncMock(side_effect=TimeoutError())):
            result = asyncio.run(v2.prepare_v2_request(request, None, payload, {}, 'model', {'model': {}}))
        assert history.save_checkpoint.await_count == 0
        assert any(m.get('content') == 'old fact ' * 1000 for m in result['messages'])
        assert v2.estimate_payload_tokens(result) <= 3800
        assert any(data.get('method') == 'excerpt' for _, data in events)

    def test_sufficient_space_does_not_compact_history_and_disabled_is_unchanged(self):
        v2, tool, request, payload, history, service, events = self.make_batch(context=40000)
        with patch.object(v2, 'generate_summary', AsyncMock(return_value='facts')) as generate:
            result = asyncio.run(v2.prepare_v2_request(request, None, payload, {}, 'model', {'model': {}}))
        assert history.save_checkpoint.await_count == 0
        assert generate.await_count == 1
        assert result['messages'][1]['content'] == payload['messages'][1]['content']
        request.state.context_compaction['settings'] = request.state.context_compaction['settings'].model_copy(
            update={'tool_budget_enabled': False}
        )
        unchanged = asyncio.run(v2.prepare_v2_request(request, None, payload, {}, 'model', {'model': {}}))
        assert unchanged == payload

    def test_manual_history_compaction_does_not_adopt_or_summarize_pending_tools(self):
        v2, tool, request, payload, history, service, events = self.make_batch(context=40000)
        snapshot = history.read_snapshot.return_value
        snapshot['current_id'] = 'a2'
        raw = v2.branch_messages(snapshot['messages_map'], 'a2')
        with (
            patch.object(v2, '_history_payload', AsyncMock(return_value=(payload, raw))),
            patch.object(
                v2, 'load_compaction_settings', AsyncMock(return_value=request.state.context_compaction['settings'])
            ),
            patch.object(v2, 'generate_summary', AsyncMock(return_value='History progress.')),
        ):
            result = asyncio.run(
                v2.compact_v2_chat_branch(request, None, SimpleNamespace(id='chat'), 'model', {'model': {}})
            )
        assert result['compacted']
        service.save_context.assert_not_awaited()
        assert all(row['state'] == 'pending' for row in service.selections.rows.values())
        assert not any(kind == 'tool_result_compaction' for kind, _ in events)

    def test_cancellation_does_not_fallback_or_write_projection(self):
        v2, tool, request, payload, history, service, events = self.make_batch(context=40000)
        with patch.object(v2, 'generate_summary', AsyncMock(side_effect=asyncio.CancelledError())):
            with pytest.raises(asyncio.CancelledError):
                asyncio.run(v2.prepare_v2_request(request, None, payload, {}, 'model', {'model': {}}))
        service.save_context.assert_not_awaited()


class ToolBudgetAcceptanceTests(IsolatedCase):
    setup_engine = v2_test_helpers.EngineTests.setup_engine
    make_batch = ToolBudgetEngineTests.make_batch

    def test_70000_history_reserves_30000_before_summarizing_batch(self):
        v2, tool, request, payload, history, service, events = self.make_batch(context=100000)
        payload['messages'][1]['content'] = 'old fact ' * 35000
        raw_map = history.read_snapshot.return_value['messages_map']
        raw_map['old']['content'] = payload['messages'][1]['content']
        template = copy.deepcopy(payload['messages'][-1])
        template['content'] *= 2
        template['_tool_result_refs'][0]['text'] = template['content']
        payload['messages'][-2]['tool_calls'] = [
            {'id': f'c{i}', 'type': 'function', 'function': {'name': 'search', 'arguments': '{}'}} for i in range(3)
        ]
        payload['messages'].pop()
        for i in range(3):
            msg = copy.deepcopy(template)
            msg['tool_call_id'] = f'c{i}'
            msg['_tool_result_refs'][0].update(call_id=f'c{i}', output_id=f'out{i}')
            payload['messages'].append(msg)
        # Accounting validates selected immutable raw results after the send;
        # the fixture must persist the same three outputs as the request.
        raw_map['a2']['output'] = [
            {
                'id': f'out{i}',
                'call_id': f'c{i}',
                'type': 'function_call_output',
                'status': 'completed',
                'output': template['content'],
            }
            for i in range(3)
        ]
        order = []

        async def generate(
            req, user, model, models, messages, previous, settings, *, purpose='history', output_budget=None
        ):
            order.append('tool' if purpose == 'tool' else 'history')
            return 'Observed facts and resource identifiers.'

        with patch.object(v2, 'generate_summary', side_effect=generate):
            result = asyncio.run(v2.prepare_v2_request(request, None, payload, {}, 'model', {'model': {}}))
        assert order == ['history', 'tool', 'tool', 'tool']
        assert v2.estimate_payload_tokens(result) <= 95000
        assert len([m for m in result['messages'] if m.get('role') == 'tool']) == 3
        assert request.state.context_compaction['last_usage']['tool_result_limit'] == 10000
        assert request.state.context_compaction['last_usage']['tool_batch_limit'] == 30000

    def test_summary_empty_and_oversized_fall_back_without_corrupting_raw(self):
        for response in ('', 'redundant ' * 10000):
            with self.subTest(response=response[:20]):
                v2, tool, request, payload, history, service, events = self.make_batch(context=40000)
                original = copy.deepcopy(payload)
                with patch.object(v2, 'generate_summary', AsyncMock(return_value=response)):
                    result = asyncio.run(v2.prepare_v2_request(request, None, payload, {}, 'model', {'model': {}}))
                assert payload == original
                assert v2.estimate_text(result['messages'][-1]['content']) <= 4000
                assert any(data.get('method') == 'excerpt' for _, data in events)

    def test_final_fixed_input_too_large_stops_with_actionable_error(self):
        v2, tool, request, payload, history, service, events = self.make_batch(context=3500)
        payload['messages'][0]['content'] = 'mandatory instruction ' * 5000
        with patch.object(v2, 'generate_summary', AsyncMock(return_value='old facts')):
            with pytest.raises(Exception) as exc:
                asyncio.run(v2.prepare_v2_request(request, None, payload, {}, 'model', {'model': {}}))
        assert exc.value.status_code == 400
        assert 'context' in str(exc.value.detail).lower() or 'space' in str(exc.value.detail).lower()


class ToolProjectionIdentityTests(IsolatedCase):
    def test_model_window_inheritance_drives_percentages_and_output_only_reduces_input(self):
        v2 = importlib.import_module('open_webui.utils.context_compaction_v2')
        tool = importlib.import_module('open_webui.utils.tool_result_budget')
        settings = v2.CompactionSettings()
        models = {
            'custom': {'info': {'base_model_id': 'base'}},
            'base': {'info': {'meta': {'context_window': 100000}}, 'owned_by': 'ollama'},
        }
        budget = v2.resolve_budget(settings, 'custom', models, {'options': {'num_predict': 5000}})
        assert budget.input_budget == 90000
        assert tool.tool_limits(budget.context_window, settings) == (10000, 30000)
        small = v2.resolve_budget(settings, 'custom', models, {'options': {'num_ctx': 60000, 'num_predict': 5000}})
        assert small.input_budget == 52000
        assert tool.tool_limits(small.context_window, settings) == (6000, 18000)

    def test_exact_spans_do_not_change_code_or_neighboring_identical_results(self):
        v2 = importlib.import_module('open_webui.utils.context_compaction_v2')
        tool = importlib.import_module('open_webui.utils.tool_result_budget')
        content = "print('hello')\nOUTPUT hello\nOUTPUT hello"
        first, second = content.find('hello', content.index('OUTPUT')), content.rfind('hello')
        refs = [
            dict(
                message_id='a',
                output_id=f'o{i}',
                call_id=f'c{i}',
                batch_id='b',
                name='execute_code',
                kind='code',
                text='hello',
                start=start,
                end=start + 5,
            )
            for i, start in enumerate((first, second))
        ]
        payload = {
            'messages': [
                {'role': 'assistant', 'content': content, '_context_message_id': 'a', '_tool_result_refs': refs}
            ]
        }
        settings = v2.CompactionSettings()
        from test_tool_result_replay import MemorySelections

        with (
            patch.object(tool, 'get_tool_result_store', return_value=SimpleNamespace()),
            patch.object(tool, 'get_tool_selection_store', return_value=MemorySelections()),
        ):
            plan = tool.ToolBudgetPlan(
                None,
                None,
                payload,
                {},
                {'message_id': 'a'},
                'm',
                {'m': {}},
                settings,
                v2.resolve_budget(settings, 'm', {'m': {}}, {}),
                {'chat_id': 'chat'},
            )
        rewritten = plan.replace(payload, {('a', 'o0'): 'first result', ('a', 'o1'): 'second result'})
        assert rewritten['messages'][0]['content'] == "print('hello')\nOUTPUT first result\nOUTPUT second result"
        again = plan.replace(rewritten, {('a', 'o0'): 'ONE', ('a', 'o1'): 'TWO'})
        assert again['messages'][0]['content'] == "print('hello')\nOUTPUT ONE\nOUTPUT TWO"
        assert v2.message_key(again['messages'][0]) == v2.message_key(payload['messages'][0])
        assert payload['messages'][0]['content'] == content

    def test_injected_raw_tool_edit_invalidates_history_checkpoint(self):
        v2 = importlib.import_module('open_webui.utils.context_compaction_v2')
        raw = {
            'id': 'a',
            'role': 'assistant',
            'content': 'answer',
            'meta': {'tool_results': [{'id': 'out', 'text': 'original'}]},
        }
        refs = v2.source_refs([raw])
        edited = copy.deepcopy(raw)
        edited['meta']['tool_results'][0]['text'] = 'edited'
        assert not v2.source_refs_match(refs, {'a': edited})

    def test_short_results_do_not_create_redundant_raw_snapshots(self):
        v2 = importlib.import_module('open_webui.utils.context_compaction_v2')
        tool = importlib.import_module('open_webui.utils.tool_result_budget')
        settings = v2.CompactionSettings(mode='v2')
        ref = dict(
            message_id='a', output_id='out', call_id='c', batch_id='batch', name='search', kind='native', text='short'
        )
        payload = {'messages': [{'role': 'tool', 'tool_call_id': 'c', 'content': 'short', '_tool_result_refs': [ref]}]}
        store = SimpleNamespace(list_contexts=AsyncMock(return_value=[]), save_result=AsyncMock())
        from test_tool_result_replay import MemorySelections

        selections = MemorySelections([{**ref, 'state': 'pending'}])
        with (
            patch.object(tool, 'get_tool_result_store', return_value=store),
            patch.object(tool, 'get_tool_selection_store', return_value=selections),
            patch.object(v2, 'emit_event', AsyncMock()) as emit,
        ):
            plan = tool.ToolBudgetPlan(
                None,
                None,
                payload,
                {},
                {'message_id': 'a'},
                'm',
                {'m': {}},
                settings,
                v2.resolve_budget(settings, 'm', {'m': {}}, {}),
                {'chat_id': 'chat'},
            )
            result = asyncio.run(plan.finish(asyncio.run(plan.load(payload))))
        assert result['messages'][0]['content'] == 'short'
        store.save_result.assert_not_awaited()
        emit.assert_not_awaited()
        assert plan.state['last_tool_batch']['results'][0]['method'] == 'original'


class ToolNotificationTests(IsolatedCase):
    setup_engine = v2_test_helpers.EngineTests.setup_engine
    make_batch = ToolBudgetEngineTests.make_batch

    def test_only_current_new_projections_emit_status(self):
        for historical, cached in ((False, False), (True, False), (False, True), (True, True)):
            with self.subTest(historical=historical, cached=cached):
                v2, tool, request, payload, history, service, events = self.make_batch(context=40000)
                state = request.state.context_compaction
                metadata = {**state['metadata'], 'message_id': 'new-reply' if historical else 'a2'}
                if historical:
                    service.selections.payload = None
                if cached:
                    service.get_context.return_value = {'text': 'Existing handoff', 'method': 'summary'}
                plan = tool.ToolBudgetPlan(
                    request,
                    None,
                    payload,
                    {},
                    metadata,
                    'model',
                    {'model': {}},
                    state['settings'],
                    v2.resolve_budget(state['settings'], 'model', {'model': {}}, payload),
                    state,
                )
                with patch.object(v2, 'generate_summary', AsyncMock(return_value='Observed evidence.')):
                    result = asyncio.run(plan.finish(payload))
                notices = [event for kind, event in events if kind == 'tool_result_compaction']
                assert len(notices) == (0 if historical or cached else 2)
                if historical:
                    assert 'last_tool_batch' not in state
                    assert result['messages'][-1]['content'] == payload['messages'][-1]['content']
                else:
                    assert state['last_tool_batch']['results'][0]['method'] == 'summary'
                    assert result['messages'][-1]['content'] != payload['messages'][-1]['content']
                if notices:
                    assert notices[0]['source_message_id'] == notices[1]['source_message_id'] == 'a2'
                    assert notices[0]['operation_id'] == notices[1]['operation_id']
                    assert not notices[0]['done'] and notices[1]['done']
                    events.clear()
                    asyncio.run(plan.finish(result))
                    assert not events

    def test_same_source_budget_has_stable_operation_identity(self):
        v2, tool, request, payload, history, service, events = self.make_batch(context=40000)
        state = request.state.context_compaction
        identities = []
        for _ in range(2):
            service.selections.rows.clear()
            plan = tool.ToolBudgetPlan(
                request,
                None,
                payload,
                {},
                state['metadata'],
                'model',
                {'model': {}},
                state['settings'],
                v2.resolve_budget(state['settings'], 'model', {'model': {}}, payload),
                state,
            )
            with patch.object(v2, 'generate_summary', AsyncMock(return_value='Observed evidence.')):
                asyncio.run(plan.finish(payload))
            identities.append(
                [data['operation_id'] for kind, data in events if kind == 'tool_result_compaction' and data['done']][-1]
            )
        assert identities[0] == identities[1]
