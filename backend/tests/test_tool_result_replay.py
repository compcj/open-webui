"""Selected history is stable; only new evidence competes for space."""

import asyncio
import copy
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from test_context_compaction_v2 import IsolatedCase


class MemorySelections:
    def __init__(self, rows=(), *, payload=None, snapshot=None):
        self.rows = {(r['message_id'], r['output_id']): copy.deepcopy(r) for r in rows}
        self.reports = []
        self.contexts = {}
        self.payload, self.snapshot = payload, snapshot
        self.accounting = None
        self.model = None

    async def list_selections(self, *args):
        if self.model is not None and args[1] != self.model:
            return []
        if self.payload:
            for message in self.payload['messages']:
                for ref in message.get('_tool_result_refs', []):
                    key = ref['message_id'], ref['output_id']
                    self.rows.setdefault(key, {**ref, 'state': 'pending'})
        return list(self.rows.values())

    async def prepare_selections(
        self, chat_id, model_id, *, branch_id, choices, batch_reports, validate=None, expected_sources=()
    ):
        selected = []
        before = copy.deepcopy(self.rows)
        fresh_ids = set()
        for choice in choices:
            ref = choice['descriptor']
            key = ref['message_id'], ref['output_id']
            current = self.rows.get(key, {})
            if current.get('state') != 'prepared':
                context = self.contexts.get(choice.get('context_id'), {})
                if current.get('state') == 'pending':
                    fresh_ids.add(str(key))
                current = {
                    **ref,
                    **choice,
                    'id': str(key),
                    'state': 'prepared',
                    'text': context.get('text', ref['text']),
                }
                self.rows[key] = current
            selected.append(current)
        for key, row in self.rows.items():
            row.setdefault('id', str(key))
        if validate and not validate([r for r in self.rows.values() if r['state'] == 'prepared'], fresh_ids):
            self.rows = before
            return None
        self.reports.extend(batch_reports)
        self.model = model_id
        if batch_reports:
            import time

            v2 = importlib.import_module('open_webui.utils.context_compaction_v2')
            report = batch_reports[-1]
            report['recorded_at'] = time.time_ns()
            report['decision_id'] = 'decision'
            refs = (
                v2.tool_accounting_refs(v2.branch_messages(self.snapshot['messages_map'], branch_id), report['results'])
                if self.snapshot
                else []
            )
            self.accounting = dict(
                model=model_id,
                branch_id=branch_id,
                recorded_at=report['recorded_at'],
                source_refs=refs,
                source_fingerprint=v2.digest(refs),
                last_tool_batch=report,
            )
        return selected

    async def latest_accounting(self, *args):
        if not self.accounting or args[1] != self.model:
            return None
        if self.snapshot:
            v2 = importlib.import_module('open_webui.utils.context_compaction_v2')
            raw = self.snapshot['messages_map']
            if self.accounting['branch_id'] not in {m['id'] for m in v2.branch_messages(raw, args[2])}:
                return None
            if not v2.tool_accounting_refs_match(self.accounting['source_refs'], raw):
                return None
        return self.accounting


def descriptor(message_id, output_id, text, batch='batch'):
    return dict(
        message_id=message_id,
        output_id=output_id,
        call_id=output_id,
        batch_id=batch,
        name='search',
        kind='native',
        text=text,
    )


def tool_message(ref):
    return {
        'role': 'tool',
        'tool_call_id': ref['call_id'],
        'content': ref['text'],
        '_context_message_id': ref['message_id'],
        '_tool_result_refs': [ref],
    }


class StableReplayTests(IsolatedCase):
    def make_plan(self, refs, rows, *, fixed='', limit=223000, single=30, batch=50, chars=False):
        v2 = importlib.import_module('open_webui.utils.context_compaction_v2')
        tool = importlib.import_module('open_webui.utils.tool_result_budget')
        payload = {'messages': [{'role': 'system', 'content': fixed}, *map(tool_message, refs)]}
        settings = v2.CompactionSettings(mode='v2', tool_result_percent=single, tool_batch_percent=batch)
        if chars:
            self.enterContext(patch.object(v2, 'estimate_text', side_effect=lambda x: len(x or '')))
        selections = MemorySelections(rows)

        async def save_context(result, **kw):
            saved = {'id': result, **kw}
            selections.contexts[result] = saved
            return saved

        service = SimpleNamespace(
            list_contexts=AsyncMock(return_value=[]),
            get_context=AsyncMock(return_value=None),
            save_result=AsyncMock(side_effect=lambda chat, ref, **kw: {**ref, 'id': ref['output_id']}),
            save_context=AsyncMock(side_effect=save_context),
        )
        self.enterContext(patch.object(tool, 'get_tool_result_store', return_value=service))
        self.enterContext(patch.object(tool, 'get_tool_selection_store', return_value=selections, create=True))
        self.enterContext(patch.object(v2, 'emit_event', AsyncMock()))
        plan = tool.ToolBudgetPlan(
            None,
            None,
            payload,
            {},
            {'message_id': 'new'},
            'model',
            {'model': {}},
            settings,
            v2.Budget(262144, 249036, limit, limit, 0, 0),
            {'chat_id': 'chat'},
        )
        return v2, tool, plan, payload, selections

    def test_19k_new_batch_keeps_raw_with_10k_fixed_selected_history(self):
        refs = [descriptor(f'old{i}', f'o{i}', 'r' * 60000) for i in range(5)]
        rows = [
            {
                **r,
                'state': 'prepared',
                'method': 'summary',
                'text': 's' * 2000,
                'allocation': 20000,
                'context_id': r['output_id'],
            }
            for r in refs
        ]
        incoming = descriptor('new', 'incoming', 'n' * 19000)
        refs.append(incoming)
        rows.append({**incoming, 'state': 'pending'})
        v2, _, plan, payload, _ = self.make_plan(refs, rows, fixed='f' * 120000, chars=True)
        with (
            patch.object(v2, 'estimate_text', side_effect=lambda x: len(x or '')),
            patch.object(
                v2,
                'estimate_payload_tokens',
                side_effect=lambda p: sum(len(m.get('content', '')) for m in p['messages']),
            ),
            patch.object(plan, 'summarize', AsyncMock(return_value='should not summarize')) as summarize,
        ):
            loaded = asyncio.run(plan.load(payload))
            assert plan.reserve(loaded) == 149000
            result = asyncio.run(plan.finish(loaded))
        summarize.assert_not_awaited()
        assert result['messages'][-1]['content'] == incoming['text']
        assert [m['content'] for m in result['messages'][1:-1]] == ['s' * 2000] * 5

    def test_legacy_history_does_not_turn_into_new_tool_work(self):
        ref = descriptor('old', 'o', 'evidence ' * 1000)
        _, _, plan, payload, _ = self.make_plan([ref], [], limit=100000)
        with patch.object(plan, 'summarize', AsyncMock(return_value='no')) as summarize:
            result = asyncio.run(plan.finish(asyncio.run(plan.load(payload))))
        assert result['messages'][-1]['content'] == ref['text']
        assert plan.last_batch is None
        summarize.assert_not_awaited()

    def test_new_summary_releases_space_before_shorter_result_is_reduced(self):
        refs = [descriptor('new', 'long', 'l' * 10000), descriptor('new', 'short', 's' * 300)]
        v2, _, plan, payload, _ = self.make_plan(refs, [{**r, 'state': 'pending'} for r in refs], limit=500, chars=True)
        with (
            patch.object(v2, 'estimate_text', side_effect=lambda x: len(x or '')),
            patch.object(
                v2,
                'estimate_payload_tokens',
                side_effect=lambda p: sum(len(m.get('content', '')) for m in p['messages']),
            ),
            patch.object(plan, 'summarize', AsyncMock(return_value='z' * 51)) as summarize,
        ):
            result = asyncio.run(plan.finish(asyncio.run(plan.load(payload))))
        assert summarize.await_count == 1
        assert result['messages'][-1]['content'] == refs[-1]['text']

    def test_empty_current_result_is_protected_even_after_previous_text_batch(self):
        older = descriptor('new', 'first', 'old', 'prior')
        empty = descriptor('new', 'second', '', 'current')
        _, _, plan, payload, _ = self.make_plan([older, empty], [{**empty, 'state': 'pending'}])
        asyncio.run(plan.load(payload))
        assert 2 in plan.indices(payload, incoming_only=True)

    def test_legacy_adjacent_rounds_have_distinct_batch_ids(self):
        sources = importlib.import_module('open_webui.utils.tool_result_sources')
        output = [
            {'type': 'function_call', 'call_id': 'a'},
            {'type': 'function_call_output', 'call_id': 'a', 'id': 'oa', 'output': 'a'},
            {'type': 'function_call', 'call_id': 'b'},
            {'type': 'function_call_output', 'call_id': 'b', 'id': 'ob', 'output': 'b'},
        ]
        refs = sources.output_descriptors(output)
        assert refs['oa']['batch_id'] != refs['ob']['batch_id']

    def test_final_full_json_envelope_fits_without_fixed_excess_subtraction(self):
        v2 = importlib.import_module('open_webui.utils.context_compaction_v2')
        with patch.object(v2, 'token_encoder', return_value=None):
            ref = descriptor('new', 'quotes', chr(34) * 240)
            v2, _, plan, payload, _ = self.make_plan([ref], [{**ref, 'state': 'pending'}], limit=175)
            payload['messages'][-1]['content'] = [{'type': 'text', 'text': ref['text']}]
            asyncio.run(plan.load(payload))
            entry = plan.entries[('new', 'quotes')]
            entry.rendered, entry.allocation = entry.text, entry.tokens
            assert v2.estimate_payload_tokens(payload) > plan.budget.input_budget
            fitted = asyncio.run(plan.fit_final_payload(payload, {entry.key: entry.text}, [entry]))
            assert v2.estimate_payload_tokens(fitted) <= plan.budget.input_budget
            assert entry.method == 'excerpt'
            assert entry.reduction_reasons == ['final_payload_fit']

    def test_duplicate_injection_is_measured_in_complete_payload(self):
        ref = descriptor('new', 'duplicate', 'x' * 1000)
        v2, _, plan, payload, _ = self.make_plan([ref], [{**ref, 'state': 'pending'}], limit=500, chars=True)
        payload['messages'].append(copy.deepcopy(payload['messages'][-1]))
        with patch.object(
            v2, 'estimate_payload_tokens', side_effect=lambda p: sum(len(m['content']) for m in p['messages'])
        ):
            asyncio.run(plan.load(payload))
            entry = plan.entries[('new', 'duplicate')]
            entry.rendered, entry.allocation = entry.text, entry.tokens
            fitted = asyncio.run(plan.fit_final_payload(payload, {entry.key: entry.text}, [entry]))
            assert v2.estimate_payload_tokens(fitted) <= 500
            assert fitted['messages'][-1]['content'] == fitted['messages'][-2]['content']

    def test_final_fit_continues_after_one_result_cannot_shrink(self):
        refs = [descriptor('new', 'large', 'x' * 300), descriptor('new', 'small', 'y' * 200)]
        v2, _, plan, payload, _ = self.make_plan(refs, [{**r, 'state': 'pending'} for r in refs], limit=450, chars=True)

        async def excerpt(entry, allowance):
            if entry.key[-1] == 'large':
                raise v2.CompactionFailure('minimum reference does not fit')
            return 'e' * min(100, allowance), 'excerpt'

        with (
            patch.object(
                v2, 'estimate_payload_tokens', side_effect=lambda p: sum(len(m['content']) for m in p['messages'])
            ),
            patch.object(plan, 'exact_or_excerpt', side_effect=excerpt),
        ):
            asyncio.run(plan.load(payload))
            result = asyncio.run(
                plan.fit_final_payload(
                    payload, {e.key: e.text for e in plan.entries.values()}, list(plan.entries.values())
                )
            )
        assert result['messages'][-2]['content'] == refs[0]['text']
        assert result['messages'][-1]['content'] != refs[1]['text']

    def test_cancelled_projection_closes_same_operation_without_preparation(self):
        ref = descriptor('new', 'large', 'x' * 10000)
        v2, _, plan, payload, selections = self.make_plan([ref], [{**ref, 'state': 'pending'}], limit=500, chars=True)
        with (
            patch.object(plan, 'summarize', AsyncMock(side_effect=asyncio.CancelledError)),
            self.assertRaises(asyncio.CancelledError),
        ):
            asyncio.run(plan.finish(payload))
        events = [call.args[2] for call in v2.emit_event.await_args_list]
        assert len(events) == 2
        assert events[0]['operation_id'] == events[1]['operation_id']
        assert events[1]['done'] and events[1]['cancelled']
        assert selections.rows[('new', 'large')]['state'] == 'pending'
        assert not selections.reports

    def test_canonical_competing_version_is_the_only_sent_text(self):
        ref = descriptor('new', 'out', 'x' * 1000)
        v2, _, plan, payload, selections = self.make_plan([ref], [{**ref, 'state': 'pending'}], limit=500, chars=True)
        original_prepare = selections.prepare_selections

        async def winner(*args, **kwargs):
            selections.rows[('new', 'out')] = {
                **ref,
                'state': 'prepared',
                'text': 'canonical',
                'method': 'summary',
                'context_id': 'winner',
                'allocation': 100,
            }
            return await original_prepare(*args, **kwargs)

        with (
            patch.object(plan, 'summarize', AsyncMock(return_value='local candidate')),
            patch.object(selections, 'prepare_selections', side_effect=winner),
        ):
            first = asyncio.run(plan.finish(payload))
            replay = asyncio.run(plan.finish(first))
        assert first == replay
        assert first['messages'][-1]['content'] == 'canonical'
        assert plan.entries[('new', 'out')].rendered == 'canonical'
        assert v2.emit_event.await_args_list[-1].args[2]['reused']

    def test_batch_resume_charges_frozen_actual_length_and_keeps_small_raw(self):
        old = descriptor('new', 'old', 'x' * 1000)
        new = descriptor('new', 'new', 'y' * 300)
        rows = [
            {
                **old,
                'state': 'prepared',
                'method': 'summary',
                'text': 'x' * 50,
                'allocation': 450,
                'original_tokens': 1000,
            },
            {**new, 'state': 'pending'},
        ]
        v2, _, plan, payload, _ = self.make_plan([old, new], rows, limit=600, chars=True)
        plan.single, plan.batch = 500, 500
        with patch.object(plan, 'summarize', AsyncMock()) as summarize:
            result = asyncio.run(plan.finish(payload))
        summarize.assert_not_awaited()
        assert result['messages'][-1]['content'] == new['text']
        assert plan.last_batch['processed_tokens'] == 350

    def test_partial_race_rechecks_batch_before_fixing_fresh_members(self):
        refs = [descriptor('new', 'first', 'a' * 20), descriptor('new', 'second', 'b' * 50)]
        v2, _, plan, payload, selections = self.make_plan(
            refs, [{**r, 'state': 'pending'} for r in refs], limit=1000, chars=True
        )
        plan.single, plan.batch = 100, 100
        original_prepare = selections.prepare_selections

        async def competitor(*args, **kwargs):
            selections.rows[('new', 'first')] = {
                **refs[0],
                'state': 'prepared',
                'text': 'winner' * 15,
                'method': 'summary',
                'allocation': 100,
                'id': 'winner',
            }
            return await original_prepare(*args, **kwargs)

        with (
            patch.object(selections, 'prepare_selections', side_effect=competitor),
            self.assertRaises(Exception) as error,
        ):
            asyncio.run(plan.finish(payload))
        assert error.exception.status_code == 409
        assert selections.rows[('new', 'second')]['state'] == 'pending'
        assert not selections.reports

    def test_recent_retention_uses_capped_incoming_cost_instead_of_large_raw_text(self):
        v2 = importlib.import_module('open_webui.utils.context_compaction_v2')
        messages = [
            {'role': 'user', 'content': 'latest'},
            {'role': 'assistant', 'tool_calls': [{'id': 'old'}]},
            {'role': 'tool', 'tool_call_id': 'old', 'content': 'recent evidence'},
            {'role': 'assistant', 'tool_calls': [{'id': 'new'}]},
            {'role': 'tool', 'tool_call_id': 'new', 'content': 'long evidence ' * 1000},
        ]
        assert v2.select_compactable_indices(messages, 50, 0, message_costs=[5, 5, 10, 5, 10]) == []
        assert v2.select_compactable_indices(messages, 50, 0)
