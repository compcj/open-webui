"""Pure V2 contracts; imports deliberately avoid application/database startup."""

import asyncio
import copy
import importlib
import sys
import unittest
from types import ModuleType
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))


class IsolatedCase(unittest.TestCase):
    def setUp(self):
        package = ModuleType('open_webui')
        package.__path__ = [str(Path(__file__).parents[1] / 'open_webui')]
        self.patch = patch.dict(sys.modules, {'open_webui': package})
        self.patch.start()
        self.addCleanup(self.patch.stop)
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        tool = importlib.import_module('open_webui.utils.tool_result_budget')
        self.enterContext(
            patch.object(
                tool,
                'get_tool_selection_store',
                return_value=SimpleNamespace(
                    list_selections=AsyncMock(return_value=[]), latest_accounting=AsyncMock(return_value=None)
                ),
            )
        )


class ConfigurationTests(IsolatedCase):
    def test_v2_defaults_and_legacy_fallback(self):
        module = importlib.import_module('open_webui.utils.context_compaction_config')
        settings = module.CompactionSettings.from_config({})
        self.assertEqual(settings.mode, 'off')
        self.assertEqual(settings.default_context_window, 262144)
        self.assertEqual(module.CompactionSettings.from_config({}, legacy_enabled=True).mode, 'legacy')
        self.assertEqual(module.CompactionSettings.from_config({'chat.context_compaction.mode': 'v2'}).mode, 'v2')
        with self.assertRaises(ValueError):
            module.CompactionSettings(trigger_percent=99, effective_window_percent=95)
        with self.assertRaises(ValueError):
            module.CompactionSettings(default_context_window=1000, recent_tokens=950)


class BudgetTests(IsolatedCase):
    def test_inheritance_output_reserve_and_num_ctx(self):
        m = importlib.import_module('open_webui.utils.context_compaction_v2')
        cfg = m.CompactionSettings(default_context_window=10000, recent_tokens=500)
        models = {
            'custom': {'info': {'base_model_id': 'base'}},
            'base': {'owned_by': 'ollama', 'info': {'meta': {'context_window': 8000}}},
        }
        b = m.resolve_budget(cfg, 'custom', models, {'options': {'num_ctx': 6000, 'num_predict': 1000}})
        self.assertEqual((b.context_window, b.input_budget, b.threshold), (6000, 4700, 4700))
        self.assertEqual(m.resolve_budget(cfg, 'custom', models, {}).input_budget, 7600)

    def test_chinese_tools_and_multimodal_are_counted(self):
        m = importlib.import_module('open_webui.utils.context_compaction_v2')
        plain = {'messages': [{'role': 'user', 'content': '请保留全部重要信息。' * 100}]}
        rich = {
            'messages': plain['messages']
            + [
                {'role': 'user', 'content': [{'type': 'image_url', 'image_url': {'url': 'https://example.test/a.png'}}]}
            ],
            'tools': [{'type': 'function', 'function': {'name': 'search', 'description': 'search the web ' * 100}}],
        }
        self.assertGreater(m.estimate_payload_tokens(plain), 200)
        self.assertGreater(m.estimate_payload_tokens(rich), m.estimate_payload_tokens(plain) + 1000)

    def test_tool_groups_cannot_split_and_latest_user_is_protected(self):
        m = importlib.import_module('open_webui.utils.context_compaction_v2')
        messages = [
            {'role': 'user', 'content': 'old'},
            {'role': 'assistant', 'content': 'x' * 500},
            {'role': 'user', 'id': 'u2', 'content': 'current'},
            {'role': 'assistant', 'tool_calls': [{'id': 'call', 'function': {'name': 'f', 'arguments': '{}'}}]},
            {'role': 'tool', 'tool_call_id': 'call', 'content': 'y' * 500},
            {'role': 'assistant', 'tool_calls': [{'id': 'pending'}]},
        ]
        groups = m.atomic_groups(messages)
        self.assertEqual(
            [(g.start, g.end, g.complete) for g in groups],
            [(0, 1, True), (1, 2, True), (2, 3, True), (3, 5, True), (5, 6, False)],
        )
        chosen = m.select_compactable_indices(messages, 1, 2, force=True)
        self.assertEqual(chosen, [0, 1, 3, 4])
        self.assertNotIn(2, chosen)
        self.assertNotIn(5, chosen)

    def test_final_content_only_and_internal_bypass(self):
        m = importlib.import_module('open_webui.utils.context_compaction_v2')
        self.assertEqual(m.response_text({'choices': [{'message': {'reasoning_content': 'private reasoning'}}]}), '')
        self.assertEqual(m.response_text({'choices': [{'message': {'content': 'summary'}}]}), 'summary')
        self.assertFalse(m.is_internal_compaction())
        with m.internal_compaction():
            self.assertTrue(m.is_internal_compaction())
        self.assertFalse(m.is_internal_compaction())


class CheckpointHistoryTests(IsolatedCase):
    def test_source_prefix_allows_growth_but_rejects_edits(self):
        m = importlib.import_module('open_webui.utils.context_compaction_v2')
        raw = [
            {'id': 'u', 'role': 'user', 'content': 'question'},
            {
                'id': 'a',
                'parentId': 'u',
                'role': 'assistant',
                'content': '',
                'output': [{'id': 'o1', 'type': 'message', 'content': 'one'}],
                'done': False,
            },
        ]
        refs = m.source_refs(raw)
        grown = __import__('copy').deepcopy(raw)
        grown[1]['output'].append({'id': 'o2', 'type': 'message', 'content': 'two'})
        grown[1]['content'] = 'new final text'
        self.assertTrue(m.source_refs_match(refs, {x['id']: x for x in grown}))
        grown[1]['output'][0]['content'] = 'edited'
        self.assertFalse(m.source_refs_match(refs, {x['id']: x for x in grown}))

    def test_checkpoint_replay_preserves_instructions_and_new_outputs(self):
        m = importlib.import_module('open_webui.utils.context_compaction_v2')
        messages = [
            {'role': 'system', 'content': 'rules'},
            {'role': 'user', 'content': 'old'},
            {'role': 'assistant', 'content': 'answer'},
            {'role': 'user', 'id': 'u', 'content': 'current'},
        ]
        checkpoint = {'summary': 'handoff', 'covered_message_keys': [m.message_key(x) for x in messages[1:3]]}
        result, applied = m.apply_checkpoint(messages, checkpoint)
        self.assertTrue(applied)
        self.assertEqual(result[0], messages[0])
        self.assertEqual(result[-1], messages[-1])
        self.assertIn(m.HISTORY_START, result[1]['content'])
        repeated, applied = m.apply_checkpoint(result + [{'role': 'assistant', 'content': 'new'}], checkpoint)
        self.assertTrue(applied)
        self.assertEqual(sum(m.HISTORY_START in str(x.get('content')) for x in repeated), 1)
        edited = __import__('copy').deepcopy(messages)
        edited[1]['content'] = 'edit'
        self.assertFalse(m.apply_checkpoint(edited, checkpoint)[1])

    def test_identifies_real_user_before_synthetic_instructions(self):
        m = importlib.import_module('open_webui.utils.context_compaction_v2')
        raw = {'id': 'u', 'role': 'user', 'content': 'real task'}
        wire = [
            {'role': 'user', 'content': 'Attachment context\nreal task'},
            {'role': 'user', 'content': 'Terminal AGENTS rules'},
        ]
        self.assertEqual(m.find_protected_user(wire, raw), 0)
        self.assertIsNone(m.find_protected_user(wire, {'id': 'missing', 'content': 'different'}))


class SummaryTests(IsolatedCase):
    def test_progressive_summary_preserves_all_chunks_and_checks_capacity(self):
        m = importlib.import_module('open_webui.utils.context_compaction_v2')
        seen = []

        async def provider(payload):
            seen.append(payload)
            return {'choices': [{'message': {'content': 'short handoff'}}]}

        cfg = m.CompactionSettings(default_context_window=2000, recent_tokens=10, summary_max_output_tokens=100)
        source = [{'role': 'tool', 'content': 'part%d ' % i + ('fact ' * 500)} for i in range(4)]
        summary = asyncio.run(
            m.progressive_summary(source, '', cfg, {'model': 'summary', 'messages': []}, 800, provider)
        )
        self.assertEqual(summary, 'short handoff')
        self.assertGreater(len(seen), 1)
        for payload in seen:
            self.assertLessEqual(m.estimate_payload_tokens(payload), 800)
        for i in range(4):
            self.assertTrue(any('part%d' % i in p['messages'][-1]['content'] for p in seen))

    def test_empty_reasoning_only_timeout_and_no_reduction_fail(self):
        m = importlib.import_module('open_webui.utils.context_compaction_v2')
        cfg = m.CompactionSettings(default_context_window=2000, recent_tokens=10, max_retries=0)

        cfg = cfg.model_copy(update={'timeout_seconds': 0.01})

        async def empty(payload):
            return {'choices': [{'message': {'reasoning_content': 'not final'}}]}

        with self.assertRaises(m.CompactionFailure):
            asyncio.run(
                m.progressive_summary(
                    [{'role': 'user', 'content': 'old history'}], '', cfg, {'messages': []}, 1000, empty
                )
            )

        async def slow(payload):
            await asyncio.sleep(1)

        with self.assertRaises(m.CompactionFailure):
            asyncio.run(
                m.progressive_summary(
                    [{'role': 'user', 'content': 'old history'}], '', cfg, {'messages': []}, 1000, slow
                )
            )


class EngineTests(IsolatedCase):
    def setup_engine(self, *, context=1800):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        m = importlib.import_module('open_webui.utils.context_compaction_v2')
        self.addCleanup(patch.stopall)
        cfg = m.CompactionSettings(mode='v2', default_context_window=context, recent_tokens=10, reminder_enabled=False)
        raw = [
            {'id': 'old', 'role': 'user', 'content': 'old fact ' * 1000},
            {'id': 'a', 'parentId': 'old', 'role': 'assistant', 'content': 'answer ' * 100},
            {'id': 'u', 'parentId': 'a', 'role': 'user', 'content': 'current task'},
        ]
        payload = {
            'model': 'model',
            'messages': [{'role': 'system', 'content': 'rules'}, *[{**x, '_context_message_id': x['id']} for x in raw]],
            'tools': [{'type': 'function', 'function': {'name': 'search'}}],
        }
        store = SimpleNamespace(
            read_snapshot=AsyncMock(
                return_value={'messages_map': {x['id']: x for x in raw}, 'current_id': 'u', 'chat': {}}
            ),
            list_checkpoints=AsyncMock(return_value=[]),
            latest_version=AsyncMock(return_value=0),
            save_checkpoint=AsyncMock(return_value={'version': 1}),
        )
        request = SimpleNamespace(
            state=SimpleNamespace(
                context_compaction={
                    'mode': 'v2',
                    'settings': cfg,
                    'chat_id': 'chat',
                    'user_message_id': 'u',
                    'metadata': {},
                }
            )
        )
        patch.object(m, 'get_checkpoint_store', return_value=store).start()
        patch.object(m, 'load_stored_models', AsyncMock(return_value={})).start()
        events = []

        async def emit(metadata, kind, data):
            events.append((kind, data))

        patch.object(m, 'emit_event', emit).start()
        return m, request, payload, store, events

    def test_success_saves_checkpoint_and_never_changes_raw(self):
        from unittest.mock import AsyncMock

        m, request, payload, store, events = self.setup_engine()
        original = __import__('copy').deepcopy(payload)
        with patch.object(m, 'generate_summary', AsyncMock(return_value='Decisions and prior work.')):
            result = asyncio.run(m.prepare_v2_request(request, object(), payload, {}, 'model', {'model': {}}))
        self.assertEqual(payload, original)
        self.assertLess(m.estimate_payload_tokens(result), m.estimate_payload_tokens(payload))
        self.assertEqual(result['messages'][0]['content'], 'rules')
        self.assertTrue(any(x.get('content') == 'current task' for x in result['messages']))
        self.assertEqual(store.save_checkpoint.await_count, 1)
        self.assertTrue(
            any(kind == 'context_compaction' and data.get('done') and not data.get('error') for kind, data in events)
        )

    def test_failure_inside_budget_warns_over_budget_raises(self):
        from unittest.mock import AsyncMock

        m, request, payload, store, events = self.setup_engine(context=40000)
        with patch.object(m, 'generate_summary', AsyncMock(side_effect=m.CompactionFailure('empty'))):
            result = asyncio.run(
                m.prepare_v2_request(request, object(), payload, {}, 'model', {'model': {}}, force=True)
            )
        self.assertEqual(result, payload)
        self.assertEqual(store.save_checkpoint.await_count, 0)
        self.assertTrue(events[-2][1].get('error') or events[-1][1].get('error'))
        request.state.context_compaction['settings'] = m.CompactionSettings(
            mode='v2', default_context_window=1000, recent_tokens=1
        )
        with patch.object(m, 'generate_summary', AsyncMock(side_effect=m.CompactionFailure('empty'))):
            with self.assertRaises(Exception) as error:
                asyncio.run(m.prepare_v2_request(request, object(), payload, {}, 'model', {'model': {}}, force=True))
        self.assertEqual(error.exception.status_code, 400)

    def test_cancelled_summary_and_stale_write_preserve_checkpoint(self):
        from unittest.mock import AsyncMock

        m, request, payload, store, events = self.setup_engine(context=40000)
        with patch.object(m, 'generate_summary', AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(m.prepare_v2_request(request, object(), payload, {}, 'model', {'model': {}}, force=True))
        self.assertEqual(store.save_checkpoint.await_count, 0)
        store.save_checkpoint.return_value = None
        with patch.object(m, 'generate_summary', AsyncMock(return_value='handoff')):
            result = asyncio.run(
                m.prepare_v2_request(request, object(), payload, {}, 'model', {'model': {}}, force=True)
            )
        self.assertEqual(result, payload)

    def test_untrusted_or_internal_requests_are_noops(self):
        from types import SimpleNamespace

        m = importlib.import_module('open_webui.utils.context_compaction_v2')
        payload = {'messages': [{'role': 'user', 'content': 'raw'}], 'metadata': {'enable_context_compaction': True}}
        request = SimpleNamespace(state=SimpleNamespace())
        self.assertIs(asyncio.run(m.prepare_v2_request(request, None, payload, {}, 'm', {})), payload)
        request.state.context_compaction = {'mode': 'v2', 'chat_id': 'chat'}
        with m.internal_compaction():
            self.assertIs(asyncio.run(m.prepare_v2_request(request, None, payload, {}, 'm', {})), payload)


class RepeatedReplayTests(IsolatedCase):
    def test_second_checkpoint_replays_coverage_across_previously_protected_user(self):
        m = importlib.import_module('open_webui.utils.context_compaction_v2')
        first_user = {'role': 'user', 'content': 'first', '_context_message_id': 'u1'}
        call = {'role': 'assistant', 'tool_calls': [{'id': 'call', 'function': {'name': 'f', 'arguments': '{}'}}]}
        result = {'role': 'tool', 'tool_call_id': 'call', 'content': 'large result'}
        newer_user = {'role': 'user', 'content': 'next', '_context_message_id': 'u2'}
        # The first checkpoint summarized a tool group while u1 was protected.
        # A later turn may summarize u1 as well; accumulated keys aren't wire order.
        checkpoint = {
            'summary': 'combined',
            'covered_message_keys': [m.message_key(call), m.message_key(result), m.message_key(first_user)],
        }
        rebuilt, applied = m.apply_checkpoint([first_user, call, result, newer_user], checkpoint)
        self.assertTrue(applied)
        self.assertEqual(rebuilt, [m.history_message('combined'), newer_user])

    def test_summary_plus_full_replay_does_not_resurrect_covered_tools(self):
        m = importlib.import_module('open_webui.utils.context_compaction_v2')
        old = {'role': 'assistant', 'content': 'covered tool content'}
        checkpoint = {'summary': 'handoff', 'covered_message_keys': [m.message_key(old)]}
        rebuilt, applied = m.apply_checkpoint(
            [m.history_message('handoff'), old, {'role': 'user', 'content': 'new'}], checkpoint
        )
        self.assertTrue(applied)
        self.assertNotIn(old, rebuilt)

    def test_provider_usage_calibration_never_uses_cumulative_total(self):
        m = importlib.import_module('open_webui.utils.context_compaction_v2')
        payload = {'messages': [{'role': 'user', 'content': 'small request'}]}
        state = {'last_request_fingerprint': m.request_fingerprint(payload)}
        m.record_provider_usage(state, {'total_tokens': 999999, 'completion_tokens': 999999})
        self.assertNotIn('calibration', state)
        m.record_provider_usage(state, {'input_tokens': 42, 'total_tokens': 999999})
        self.assertEqual(m.request_tokens(payload, state), (42, 'provider:single-call'))
        payload['tools'] = [{'type': 'function', 'function': {'name': 'new'}}]
        self.assertEqual(m.request_tokens(payload, state)[0], m.estimate_payload_tokens(payload))

    def test_recent_turn_budget_retains_contiguous_suffix(self):
        m = importlib.import_module('open_webui.utils.context_compaction_v2')
        messages = [
            {'role': 'user', 'content': 'tiny old'},
            {'role': 'assistant', 'content': 'old reply'},
            {'role': 'user', 'content': 'middle'},
            {'role': 'assistant', 'content': 'many words ' * 1000},
            {'role': 'user', 'content': 'latest'},
        ]
        selected = m.select_compactable_indices(messages, 200, 4)
        self.assertEqual(selected, [0, 1, 2, 3])

    def test_setting_timeout_bound_matches_api(self):
        m = importlib.import_module('open_webui.utils.context_compaction_config')
        with self.assertRaises(ValueError):
            m.CompactionSettings(timeout_seconds=601)


class ScopedSourceTests(IsolatedCase):
    def test_only_covered_sources_invalidate_checkpoint(self):
        m = importlib.import_module('open_webui.utils.context_compaction_v2')
        raw = [
            {'id': 'old', 'role': 'user', 'content': 'old'},
            {'id': 'u', 'parentId': 'old', 'role': 'user', 'content': 'current'},
            {
                'id': 'a',
                'parentId': 'u',
                'role': 'assistant',
                'content': '',
                'output': [{'id': 'pending', 'type': 'message', 'status': 'in_progress'}],
            },
        ]
        refs = m.coverage_refs(raw, [{**raw[0], '_context_message_id': 'old'}], None)
        self.assertEqual([r['id'] for r in refs], ['old'])
        raw[-1]['output'][0]['content'] = 'growing response'
        self.assertTrue(m.source_refs_match(refs, {x['id']: x for x in raw}))

    def test_pending_output_suffix_growth_does_not_invalidate_complete_prefix(self):
        m = importlib.import_module('open_webui.utils.context_compaction_v2')
        raw = {
            'id': 'a',
            'role': 'assistant',
            'output': [
                {'id': 'done', 'type': 'message', 'status': 'completed', 'content': 'facts'},
                {'id': 'pending', 'type': 'message', 'status': 'in_progress', 'content': ''},
            ],
        }
        refs = m.source_refs([raw])
        raw['output'][1]['content'] = 'new suffix'
        self.assertTrue(m.source_refs_match(refs, {'a': raw}))

    def test_tool_image_message_is_part_of_call_result_group(self):
        m = importlib.import_module('open_webui.utils.context_compaction_v2')
        messages = [
            {'role': 'assistant', 'tool_calls': [{'id': 'c'}]},
            {'role': 'tool', 'tool_call_id': 'c', 'content': 'image generated'},
            {
                'role': 'user',
                '_tool_image_context': True,
                'content': [{'type': 'image_url', 'image_url': {'url': 'https://example.test/image.png'}}],
            },
            {'role': 'user', 'content': 'next user'},
        ]
        self.assertEqual([(g.start, g.end) for g in m.atomic_groups(messages)], [(0, 3), (3, 4)])


class CoveragePrefixTests(IsolatedCase):
    def test_compaction_reuses_previous_prefix_without_claiming_new_outputs(self):
        m = importlib.import_module('open_webui.utils.context_compaction_v2')
        raw = [
            {
                'id': 'a',
                'role': 'assistant',
                'output': [
                    {'id': 'one', 'type': 'function_call', 'call_id': 'c1', 'status': 'completed'},
                    {
                        'id': 'out1',
                        'type': 'function_call_output',
                        'call_id': 'c1',
                        'output': 'one',
                        'status': 'completed',
                    },
                    {'id': 'two', 'type': 'function_call', 'call_id': 'c2', 'status': 'completed'},
                    {
                        'id': 'out2',
                        'type': 'function_call_output',
                        'call_id': 'c2',
                        'output': 'two',
                        'status': 'completed',
                    },
                ],
            }
        ]
        selected = [
            {'role': 'assistant', '_context_message_id': 'a', 'tool_calls': [{'id': 'c1'}]},
            {'role': 'tool', '_context_message_id': 'a', 'tool_call_id': 'c1', 'content': 'one'},
        ]
        refs = m.coverage_refs(raw, selected, None)
        self.assertEqual(len(refs[0]['output_prefix']), 2)
        raw[0]['output'][3]['output'] = 'edited retained output'
        self.assertTrue(m.source_refs_match(refs, {'a': raw[0]}))
        newraw = [*raw, {'id': 'u', 'role': 'user', 'content': 'next'}]
        newrefs = m.coverage_refs(newraw, [newraw[1]], {'source_refs': refs})
        self.assertEqual(len(newrefs[0]['output_prefix']), 2)


class EngineReuseTests(IsolatedCase):
    setup_engine = EngineTests.setup_engine

    def test_checkpoint_survives_pending_assistant_growth_and_next_turn(self):
        from unittest.mock import AsyncMock

        m, request, payload, store, events = self.setup_engine()
        raw_map = store.read_snapshot.return_value['messages_map']
        raw_map['pending'] = {
            'id': 'pending',
            'parentId': 'u',
            'role': 'assistant',
            'content': '',
            'output': [],
            'done': False,
        }
        request.state.context_compaction['metadata']['message_id'] = 'pending'
        saved = []

        async def save(data, expected_version):
            row = {**data, 'version': 1}
            saved.append(row)
            return row

        store.save_checkpoint.side_effect = save
        generate = AsyncMock(return_value='Earlier decisions.')
        with patch.object(m, 'generate_summary', generate):
            asyncio.run(m.prepare_v2_request(request, None, payload, {}, 'model', {'model': {}}))
            self.assertNotIn('pending', [r['id'] for r in saved[0]['source_refs']])
            raw_map['pending']['content'] = 'finished answer'
            raw_map['pending']['done'] = True
            raw_map['next'] = {'id': 'next', 'parentId': 'pending', 'role': 'user', 'content': 'follow up'}
            request.state.context_compaction['user_message_id'] = 'next'
            request.state.context_compaction['metadata']['message_id'] = 'not_saved_yet'
            store.list_checkpoints.return_value = saved
            replay = {
                **payload,
                'messages': [
                    *payload['messages'],
                    {**raw_map['pending'], '_context_message_id': 'pending'},
                    {**raw_map['next'], '_context_message_id': 'next'},
                ],
            }
            result = asyncio.run(m.prepare_v2_request(request, None, replay, {}, 'model', {'model': {}}))
        self.assertEqual(generate.await_count, 1)
        self.assertIn(m.history_message('Earlier decisions.'), result['messages'])
        self.assertTrue(any(x.get('content') == 'finished answer' for x in result['messages']))

    def test_nonreducing_summary_is_rejected(self):
        from unittest.mock import AsyncMock

        m, request, payload, store, events = self.setup_engine(context=40000)
        with patch.object(m, 'generate_summary', AsyncMock(return_value='redundant ' * 20000)):
            result = asyncio.run(m.prepare_v2_request(request, None, payload, {}, 'model', {'model': {}}, force=True))
        self.assertEqual(result, payload)
        self.assertEqual(store.save_checkpoint.await_count, 0)
        self.assertTrue(any(data.get('error') and 'reduce' in data.get('description', '') for _, data in events))

    def test_reminder_preserves_message_prefix_and_is_counted_in_final_usage(self):
        m, request, payload, store, events = self.setup_engine(context=40000)
        original = copy.deepcopy(payload)
        request.state.context_compaction['settings'] = request.state.context_compaction['settings'].model_copy(
            update={'reminder_enabled': True, 'reminder_percent': 100}
        )
        result = asyncio.run(m.prepare_v2_request(request, None, payload, {}, 'model', {'model': {}}))
        self.assertEqual(result['messages'][:-1], original['messages'])
        self.assertEqual(result['messages'][-1]['role'], 'system')
        self.assertTrue(result['messages'][-1]['content'].startswith('Context budget reminder:'))
        self.assertEqual(payload, original)
        self.assertEqual(request.state.context_compaction['last_usage']['tokens'], m.estimate_payload_tokens(result))
        self.assertEqual(store.save_checkpoint.await_count, 0)

    def test_tool_continuations_replace_old_reminder_after_complete_tool_results(self):
        m, request, payload, store, events = self.setup_engine(context=40000)
        snapshot = copy.deepcopy(store.read_snapshot.return_value)
        request.state.context_compaction['settings'] = request.state.context_compaction['settings'].model_copy(
            update={'reminder_enabled': True, 'reminder_percent': 100}
        )
        result = asyncio.run(m.prepare_v2_request(request, None, payload, {}, 'model', {'model': {}}))
        expected_messages = list(payload['messages'])
        for call_id in ('first', 'second'):
            previous_reminder = request.state.context_compaction['reminder_message']
            tool_group = [
                {
                    'role': 'assistant',
                    'content': '',
                    'tool_calls': [
                        {'id': call_id, 'type': 'function', 'function': {'name': 'search', 'arguments': '{}'}}
                    ],
                },
                {'role': 'tool', 'tool_call_id': call_id, 'content': 'new evidence ' * 50},
            ]
            expected_messages.extend(tool_group)
            followup = {**result, 'messages': [*result['messages'], *tool_group]}
            result = asyncio.run(m.prepare_v2_request(request, None, followup, {}, 'model', {'model': {}}))
            self.assertEqual(result['messages'][:-1], expected_messages)
            self.assertEqual(result['messages'][-1], request.state.context_compaction['reminder_message'])
            self.assertNotIn(previous_reminder, result['messages'])
            self.assertEqual(
                request.state.context_compaction['last_usage']['tokens'], m.estimate_payload_tokens(result)
            )
        self.assertEqual(store.read_snapshot.return_value, snapshot)
        self.assertEqual(store.save_checkpoint.await_count, 0)

    def test_reminder_is_omitted_when_it_would_exceed_input_budget(self):
        m, request, payload, store, events = self.setup_engine(context=40000)
        request.state.context_compaction['settings'] = request.state.context_compaction['settings'].model_copy(
            update={'reminder_enabled': True, 'reminder_percent': 100, 'reminder_prompt': 'long reminder ' * 40000}
        )
        result = asyncio.run(m.prepare_v2_request(request, None, payload, {}, 'model', {'model': {}}))
        self.assertEqual(result, payload)
        self.assertNotIn('reminder_message', request.state.context_compaction)
        self.assertEqual(request.state.context_compaction['last_usage']['tokens'], m.estimate_payload_tokens(result))

    def test_reminder_does_not_displace_assistant_prefill(self):
        m, request, payload, store, events = self.setup_engine(context=40000)
        request.state.context_compaction['settings'] = request.state.context_compaction['settings'].model_copy(
            update={'reminder_enabled': True, 'reminder_percent': 100}
        )
        payload['messages'].append({'role': 'assistant', 'content': 'Continue this answer'})
        payload.update(continue_final_message=True, add_generation_prompt=False)
        result = asyncio.run(m.prepare_v2_request(request, None, payload, {}, 'model', {'model': {}}))
        self.assertEqual(result, payload)
        self.assertNotIn('reminder_message', request.state.context_compaction)


class ProviderSummaryTests(IsolatedCase):
    def test_summary_uses_selected_model_defaults_override_and_private_state(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        m = importlib.import_module('open_webui.utils.context_compaction_v2')
        chat_module = ModuleType('open_webui.utils.chat')
        payload_module = ModuleType('open_webui.utils.payload')
        seen = []
        request = SimpleNamespace(
            state=SimpleNamespace(metadata={'chat_id': 'real'}, context_compaction={'mode': 'v2'})
        )

        async def generate(req, form_data, user, **kwargs):
            self.assertTrue(m.is_internal_compaction())
            self.assertIsNot(req.state, request.state)
            self.assertTrue(kwargs['bypass_system_prompt'])
            req.state.metadata = {'changed': True}
            seen.append(form_data)
            return {'choices': [{'message': {'content': 'summary'}}]}

        chat_module.generate_chat_completion = generate
        payload_module.apply_params_to_form_data = lambda body, model, params: {
            **body,
            **{k: v for k, v in params.items() if k != 'system'},
        }
        payload_module.resolve_system_prompt = AsyncMock(return_value='summary model rules')
        cfg = m.CompactionSettings(mode='v2', summary_model='summary', summary_max_output_tokens=321)
        stored = {
            'summary': {
                'params': {'temperature': 0.25, 'max_completion_tokens': 777, 'system': 'summary model rules'},
                'meta': {'context_window': 2000},
            }
        }
        with (
            patch.dict(sys.modules, {'open_webui.utils.chat': chat_module, 'open_webui.utils.payload': payload_module}),
            patch.object(m, 'load_stored_models', AsyncMock(return_value=stored)),
        ):
            result = asyncio.run(
                m.generate_summary(
                    request,
                    None,
                    'chat-model',
                    {'summary': {}, 'chat-model': {}},
                    [{'role': 'user', 'content': 'facts ' * 100}],
                    '',
                    cfg,
                )
            )
        self.assertEqual(result, 'summary')
        self.assertEqual(seen[0]['model'], 'summary')
        self.assertEqual(seen[0]['temperature'], 0.25)
        self.assertEqual(seen[0]['max_completion_tokens'], 321)
        self.assertEqual(seen[0]['messages'][0]['content'], 'summary model rules')
        self.assertEqual(request.state.metadata, {'chat_id': 'real'})
        self.assertFalse(m.is_internal_compaction())


class MultimodalSummaryTests(IsolatedCase):
    def test_images_are_sent_as_images_and_never_as_base64_transcript_text(self):
        m = importlib.import_module('open_webui.utils.context_compaction_v2')
        seen = []
        image_url = 'data:image/png;base64,' + 'aGVsbG8=' * 1000

        async def provider(body):
            seen.append(body)
            return {'choices': [{'message': {'content': 'The earlier diagram shows the architecture.'}}]}

        cfg = m.CompactionSettings(recent_tokens=1)
        source = [
            {
                'role': 'user',
                'content': [
                    {'type': 'text', 'text': 'Explain this diagram'},
                    {'type': 'image_url', 'image_url': {'url': image_url}},
                ],
            }
        ]
        asyncio.run(m.progressive_summary(source, '', cfg, {'messages': []}, 10000, provider))
        image_parts = [
            part
            for body in seen
            for message in body['messages']
            if isinstance(message.get('content'), list)
            for part in message['content']
            if part.get('type') == 'image_url'
        ]
        self.assertEqual(image_parts, [{'type': 'image_url', 'image_url': {'url': image_url}}])
        self.assertTrue(
            all(
                image_url not in str(part.get('text', ''))
                for body in seen
                for message in body['messages']
                if isinstance(message.get('content'), list)
                for part in message['content']
            )
        )

    def test_image_too_large_for_summary_model_fails_without_dropping_it(self):
        m = importlib.import_module('open_webui.utils.context_compaction_v2')

        async def provider(body):
            return {'choices': [{'message': {'content': 'short'}}]}

        cfg = m.CompactionSettings(recent_tokens=1)
        with self.assertRaises(m.CompactionFailure):
            asyncio.run(
                m.progressive_summary(
                    [
                        {
                            'role': 'user',
                            'content': [{'type': 'image_url', 'image_url': {'url': 'https://example.test/image.png'}}],
                        }
                    ],
                    '',
                    cfg,
                    {'messages': []},
                    2000,
                    provider,
                )
            )


class CompletedSummaryTests(IsolatedCase):
    def test_truncated_or_incomplete_provider_outputs_are_not_valid_handoffs(self):
        m = importlib.import_module('open_webui.utils.context_compaction_v2')
        self.assertEqual(
            m.response_text({'choices': [{'finish_reason': 'length', 'message': {'content': 'cut off handoff'}}]}), ''
        )
        self.assertEqual(
            m.response_text(
                {
                    'status': 'incomplete',
                    'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'cut off'}]}],
                }
            ),
            '',
        )


class StrictSettingsAndProviderBudgetTests(IsolatedCase):
    def test_integer_config_fields_reject_bool_and_fractional_values(self):
        m = importlib.import_module('open_webui.utils.context_compaction_config')
        fields = [
            'default_context_window',
            'effective_window_percent',
            'trigger_percent',
            'reminder_percent',
            'recent_tokens',
            'summary_max_output_tokens',
            'timeout_seconds',
            'max_retries',
        ]
        self.assertIs(type(m.CompactionSettings().timeout_seconds), int)
        for field in fields:
            for value in (True, 1.5, '2'):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    m.CompactionSettings(**{field: value})

    def test_num_ctx_cap_applies_only_to_ollama_including_base_inheritance(self):
        m = importlib.import_module('open_webui.utils.context_compaction_v2')
        cfg = m.CompactionSettings(default_context_window=10000, recent_tokens=1)
        models = {
            'custom': {'info': {'base_model_id': 'base'}},
            'base': {'owned_by': 'openai', 'info': {'meta': {'context_window': 8000}, 'params': {'num_ctx': 2000}}},
        }
        payload = {'options': {'num_ctx': 3000}}
        self.assertEqual(m.resolve_budget(cfg, 'custom', models, payload).context_window, 8000)
        models['base']['owned_by'] = 'ollama'
        self.assertEqual(m.resolve_budget(cfg, 'custom', models, payload).context_window, 3000)


class ActiveBudgetRetentionTests(IsolatedCase):
    setup_engine = EngineTests.setup_engine

    def test_default_retention_compacts_smaller_model_and_output_reserved_budget(self):
        from unittest.mock import AsyncMock

        for context, output, repetitions in ((8000, None, 5000), (16000, 10000, 3500)):
            with self.subTest(context=context, output=output):
                m, request, payload, store, _ = self.setup_engine(context=40000)
                request.state.context_compaction['settings'] = m.CompactionSettings(mode='v2', reminder_enabled=False)
                payload['messages'][1]['content'] = 'old fact ' * repetitions
                store.read_snapshot.return_value['messages_map']['old']['content'] = payload['messages'][1]['content']
                if output:
                    payload['max_completion_tokens'] = output
                models = {'model': {'info': {'meta': {'context_window': context}}}}
                summary = AsyncMock(return_value='Earlier decisions and completed work.')
                with patch.object(m, 'generate_summary', summary):
                    result = asyncio.run(m.prepare_v2_request(request, None, payload, {}, 'model', models))
                self.assertGreater(summary.await_count, 0)
                self.assertLessEqual(
                    m.estimate_payload_tokens(result),
                    m.resolve_budget(
                        request.state.context_compaction['settings'], 'model', models, payload
                    ).input_budget,
                )
                self.assertTrue(any(msg.get('content') == 'current task' for msg in result['messages']))

    def test_summary_size_can_reduce_optional_retention_again_without_touching_protected_groups(self):
        from unittest.mock import AsyncMock

        m, request, payload, store, _ = self.setup_engine(context=40000)
        request.state.context_compaction['settings'] = m.CompactionSettings(mode='v2', reminder_enabled=False)
        raw = []
        parent = None
        for index in range(4):
            for role in ('user', 'assistant'):
                identity = f'{role}{index}'
                raw.append({'id': identity, 'parentId': parent, 'role': role, 'content': f'{index} ' + 'fact ' * 300})
                parent = identity
        raw.append({'id': 'u', 'parentId': parent, 'role': 'user', 'content': 'current task'})
        raw_map = {msg['id']: msg for msg in raw}
        store.read_snapshot.return_value['messages_map'] = raw_map
        payload['messages'] = [
            payload['messages'][0],
            *[{**msg, '_context_message_id': msg['id']} for msg in raw],
            {'role': 'user', 'content': 'Terminal runtime instructions'},
            {
                'role': 'assistant',
                'tool_calls': [{'id': 'pending', 'function': {'name': 'waiting', 'arguments': '{}'}}],
            },
        ]
        initial = __import__('copy').deepcopy(payload)
        models = {'model': {'info': {'meta': {'context_window': 2000}}}}
        summary = AsyncMock(return_value='summary ' * 800)
        with patch.object(m, 'generate_summary', summary):
            result = asyncio.run(m.prepare_v2_request(request, None, payload, {}, 'model', models))
        self.assertGreater(summary.await_count, 1)
        self.assertLessEqual(m.estimate_payload_tokens(result), 1900)
        self.assertEqual(payload, initial)
        for protected in initial['messages'][-3:]:
            self.assertIn(protected, result['messages'])


def actual_ollama_converter():
    import ast
    import json

    path = Path(__file__).parents[1] / 'open_webui/utils/payload.py'
    node = next(
        n
        for n in ast.parse(path.read_text(encoding='utf-8')).body
        if isinstance(n, ast.FunctionDef) and n.name == 'convert_messages_openai_to_ollama'
    )
    namespace = {'JSONCodec': json}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace[node.name]


class TransportCoverageTests(IsolatedCase):
    def test_actual_ollama_tool_conversion_reuses_coverage_in_both_directions(self):
        m = importlib.import_module('open_webui.utils.context_compaction_v2')
        openai = [
            {
                'role': 'assistant',
                '_context_message_id': 'a',
                'reasoning_content': 'plan',
                'tool_calls': [{'id': 'call1', 'type': 'function', 'function': {'name': 'search', 'arguments': '{}'}}],
            },
            {'role': 'tool', '_context_message_id': 'a', 'tool_call_id': 'call1', 'content': 'result'},
            {
                'role': 'user',
                '_context_message_id': 'a',
                'content': [
                    {'type': 'text', 'text': 'tool diagram'},
                    {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,aGVsbG8='}},
                ],
            },
        ]
        raw = [
            {
                'id': 'a',
                'role': 'assistant',
                'output': [
                    {
                        'type': 'function_call',
                        'id': 'call1',
                        'call_id': 'call1',
                        'arguments': '{}',
                        'status': 'completed',
                    }
                ],
            }
        ]
        fingerprint = m.digest(m.source_refs(raw))
        native = actual_ollama_converter()(openai)
        latest = {'role': 'user', '_context_message_id': 'u', 'content': 'current'}
        for source, target in ((openai, native), (native, openai)):
            checkpoint = {
                'summary': 'handoff',
                'covered_message_keys': [{'key': m.message_key(msg), 'source_id': 'a'} for msg in source],
            }
            rebuilt, applied = m.apply_checkpoint([*target, latest], checkpoint)
            self.assertTrue(applied)
            self.assertEqual(rebuilt, [m.history_message('handoff'), latest])
        self.assertEqual(m.digest(m.source_refs(raw)), fingerprint)
        changed = __import__('copy').deepcopy(native)
        changed[0]['tool_calls'][0]['function']['arguments'] = {'query': 'different'}
        self.assertNotEqual(m.message_key(changed[0]), m.message_key(openai[0]))


class ManualNativeRoundtripTests(IsolatedCase):
    def test_manual_native_send_and_saved_usage_share_tool_reasoning_media_coverage(self):
        import ast
        import copy
        import json
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        m = importlib.import_module('open_webui.utils.context_compaction_v2')
        misc = ModuleType('open_webui.utils.misc')
        middleware = ModuleType('open_webui.utils.middleware')
        payload_module = ModuleType('open_webui.utils.payload')
        for relative, names, module in [
            ('utils/misc.py', {'convert_output_to_messages', 'reconcile_tool_pairs'}, misc),
            ('utils/middleware.py', {'get_reasoning_format'}, middleware),
        ]:
            path = Path(__file__).parents[1] / 'open_webui' / relative
            nodes = [
                node
                for node in ast.parse(path.read_text(encoding='utf-8')).body
                if isinstance(node, ast.FunctionDef) and node.name in names
            ]
            namespace = {'JSONCodec': json}
            exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), namespace)
            for name in names:
                setattr(module, name, namespace[name])
        payload_module.convert_messages_openai_to_ollama = actual_ollama_converter()
        raw = [
            {'id': 'old', 'role': 'user', 'content': 'original question ' * 500},
            {
                'id': 'a',
                'parentId': 'old',
                'role': 'assistant',
                'output': [
                    {
                        'id': 'r',
                        'type': 'reasoning',
                        'status': 'completed',
                        'content': [{'type': 'output_text', 'text': 'planning'}],
                    },
                    {
                        'id': 'c',
                        'type': 'function_call',
                        'call_id': 'call1',
                        'name': 'search',
                        'arguments': '{}',
                        'status': 'completed',
                    },
                    {
                        'id': 'o',
                        'type': 'function_call_output',
                        'call_id': 'call1',
                        'status': 'completed',
                        'output': [
                            {'type': 'input_text', 'text': 'result ' * 500},
                            {'type': 'input_image', 'image_url': 'data:image/png;base64,aGVsbG8='},
                        ],
                    },
                ],
            },
            {'id': 'u', 'parentId': 'a', 'role': 'user', 'content': 'current'},
        ]
        snapshot = {'messages_map': {msg['id']: msg for msg in raw}, 'current_id': 'u', 'chat': {}}
        raw_before = copy.deepcopy(snapshot)
        settings = m.CompactionSettings(mode='v2', reminder_enabled=False)
        models = {'model': {'owned_by': 'ollama', 'info': {'meta': {'context_window': 20000}}}}
        checkpoints = []

        async def save(data, expected_version):
            row = {**data, 'version': expected_version + 1}
            checkpoints[:] = [row]
            return row

        store = SimpleNamespace(
            read_snapshot=AsyncMock(return_value=snapshot),
            list_checkpoints=AsyncMock(side_effect=lambda *args: list(checkpoints)),
            latest_version=AsyncMock(return_value=0),
            save_checkpoint=AsyncMock(side_effect=save),
        )
        request = SimpleNamespace(
            state=SimpleNamespace(
                context_compaction={'mode': 'v2', 'chat_id': 'chat', 'user_message_id': 'u', 'settings': settings}
            )
        )
        generation = AsyncMock(return_value='Earlier search results and diagram.')
        tool_budget = importlib.import_module('open_webui.utils.tool_result_budget')
        tool_store = SimpleNamespace(
            list_contexts=AsyncMock(return_value=[]),
            save_result=AsyncMock(side_effect=lambda chat, descriptor, **kwargs: {**descriptor, 'id': 'stored-tool'}),
            get_context=AsyncMock(return_value=None),
            save_context=AsyncMock(return_value={'id': 'context'}),
        )
        with (
            patch.object(tool_budget, 'get_tool_result_store', return_value=tool_store),
            patch.dict(
                sys.modules,
                {
                    'open_webui.utils.misc': misc,
                    'open_webui.utils.middleware': middleware,
                    'open_webui.utils.payload': payload_module,
                },
            ),
            patch.object(m, 'get_checkpoint_store', return_value=store),
            patch.object(m, 'load_stored_models', AsyncMock(return_value={})),
            patch.object(m, 'load_compaction_settings', AsyncMock(return_value=settings)),
            patch.object(m, 'emit_event', AsyncMock()),
            patch.object(m, 'generate_summary', generation),
        ):
            manual = asyncio.run(m.compact_v2_chat_branch(request, None, SimpleNamespace(id='chat'), 'model', models))
            self.assertTrue(manual['compacted'])
            native_history, _ = asyncio.run(m._history_payload(snapshot, 'model', models))
            calls = [msg for msg in native_history['messages'] if msg.get('tool_calls')]
            self.assertEqual(calls[0]['tool_calls'][0]['function']['arguments'], {})
            self.assertEqual(calls[0]['thinking'], 'planning')
            self.assertTrue(any(msg.get('images') == ['aGVsbG8='] for msg in native_history['messages']))
            reused = asyncio.run(m.prepare_v2_request(request, None, native_history, {}, 'model', models))
            self.assertEqual(generation.await_count, 1)
            self.assertIn(m.history_message('Earlier search results and diagram.'), reused['messages'])
            # Generate a checkpoint via the native final-send path, then query it.
            checkpoints.clear()
            asyncio.run(m.prepare_v2_request(request, None, native_history, {}, 'model', models, force=True))
            usage = asyncio.run(m.get_v2_chat_context_usage(SimpleNamespace(id='chat'), 'model', models))
            self.assertLess(usage['tokens'], m.estimate_payload_tokens(native_history))
        self.assertEqual(snapshot, raw_before)


class DirectSummaryRoutingTests(IsolatedCase):
    def test_summary_keeps_trusted_socket_routing_metadata(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        m = importlib.import_module('open_webui.utils.context_compaction_v2')
        chat_module = ModuleType('open_webui.utils.chat')
        payload_module = ModuleType('open_webui.utils.payload')
        routing = {
            'chat_id': 'chat',
            'message_id': 'response',
            'user_id': 'user',
            'session_id': 'socket',
            'task': 'old-task',
        }
        request = SimpleNamespace(state=SimpleNamespace(metadata=dict(routing), direct=True))

        async def generate(req, form_data, user, **kwargs):
            for key in ('chat_id', 'message_id', 'user_id', 'session_id'):
                self.assertEqual(req.state.metadata.get(key), routing[key])
            self.assertEqual(req.state.metadata['task'], 'context_compaction_v2')
            self.assertTrue(m.is_internal_compaction())
            return {'choices': [{'message': {'content': 'handoff'}}]}

        chat_module.generate_chat_completion = generate
        payload_module.apply_params_to_form_data = lambda body, model, params: body
        payload_module.resolve_system_prompt = AsyncMock(return_value='')
        with (
            patch.dict(sys.modules, {'open_webui.utils.chat': chat_module, 'open_webui.utils.payload': payload_module}),
            patch.object(m, 'load_stored_models', AsyncMock(return_value={})),
        ):
            asyncio.run(
                m.generate_summary(
                    request,
                    None,
                    'direct',
                    {'direct': {}},
                    [{'role': 'user', 'content': 'history'}],
                    '',
                    m.CompactionSettings(),
                )
            )
        self.assertEqual(request.state.metadata, routing)


class UsageBranchSelectionTests(IsolatedCase):
    def test_explicit_saved_branch_is_queried_without_mutating_active_branch(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        import copy

        m = importlib.import_module('open_webui.utils.context_compaction_v2')
        snapshot = {
            'current_id': 'active',
            'chat': {},
            'messages_map': {
                'active': {'id': 'active', 'role': 'user', 'content': 'short'},
                'sibling': {'id': 'sibling', 'role': 'user', 'content': 'other branch ' * 100},
            },
        }
        original = copy.deepcopy(snapshot)
        store = SimpleNamespace(
            read_snapshot=AsyncMock(return_value=snapshot), list_checkpoints=AsyncMock(return_value=[])
        )

        async def render(selected, model_id, models):
            raw = m.branch_messages(selected['messages_map'], selected['current_id'])
            return {'messages': raw}, raw

        with (
            patch.object(m, 'get_checkpoint_store', return_value=store),
            patch.object(m, 'load_compaction_settings', AsyncMock(return_value=m.CompactionSettings(mode='v2'))),
            patch.object(m, 'load_stored_models', AsyncMock(return_value={})),
            patch.object(m, '_history_payload', render),
        ):
            active = asyncio.run(m.get_v2_chat_context_usage(SimpleNamespace(id='chat'), 'model', {'model': {}}))
            sibling = asyncio.run(
                m.get_v2_chat_context_usage(SimpleNamespace(id='chat'), 'model', {'model': {}}, current_id='sibling')
            )
            missing = asyncio.run(
                m.get_v2_chat_context_usage(SimpleNamespace(id='chat'), 'model', {'model': {}}, current_id='missing')
            )
        self.assertEqual(active['branch_id'], 'active')
        self.assertEqual(sibling['branch_id'], 'sibling')
        self.assertGreater(sibling['tokens'], active['tokens'])
        self.assertIsNone(missing)
        self.assertEqual(snapshot, original)


class DirectModelParameterIsolationTests(IsolatedCase):
    def test_direct_id_collision_and_base_pointer_never_read_or_merge_server_models(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        m = importlib.import_module('open_webui.utils.context_compaction_v2')
        private = {
            'id': 'collision',
            'base_model_id': 'private-base',
            'params': {
                'system': 'private server system',
                'custom_params': {'secret_parameter': 'synthetic-private-value'},
            },
            'meta': {'context_window': 1},
        }
        database = ModuleType('open_webui.models.models')
        database.Models = SimpleNamespace(get_model_by_id=AsyncMock(return_value=private))
        models = {
            'collision': {
                'id': 'collision',
                'direct': True,
                'info': {
                    'base_model_id': 'private-base',
                    'meta': {'context_window': 8192},
                    'params': {'temperature': 0.2},
                },
            },
            'private-base': {
                'info': {'params': {'system': 'another private instruction'}, 'meta': {'context_window': 100000}}
            },
        }
        with patch.dict(sys.modules, {'open_webui.models.models': database}):
            stored = asyncio.run(m.load_stored_models('collision', models))
            self.assertEqual(stored, {})
            self.assertEqual(asyncio.run(m.resolve_context_window('collision', models, 262144)), 8192)
        database.Models.get_model_by_id.assert_not_awaited()
        chain = m.model_chain(
            'collision', models, {'collision': private, 'private-base': {'params': {'system': 'private base'}}}
        )
        self.assertEqual(len(chain), 1)
        self.assertEqual(m.inherited_params(chain), {'temperature': 0.2})

    def test_direct_summary_cannot_exfiltrate_private_params_but_server_summary_inheritance_remains(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        m = importlib.import_module('open_webui.utils.context_compaction_v2')
        database = ModuleType('open_webui.models.models')
        rows = {
            'collision': {
                'id': 'collision',
                'params': {'system': 'PRIVATE SYSTEM', 'custom_params': {'private_key': 'synthetic-secret'}},
            },
            'server-summary': {
                'id': 'server-summary',
                'params': {'system': 'server summary instructions', 'temperature': 0.75},
            },
        }
        database.Models = SimpleNamespace(get_model_by_id=AsyncMock(side_effect=lambda identity: rows.get(identity)))
        chat_module = ModuleType('open_webui.utils.chat')
        payload_module = ModuleType('open_webui.utils.payload')
        seen = []

        async def provider(request, form_data, user, **kwargs):
            seen.append(form_data)
            return {'choices': [{'message': {'content': 'handoff'}}]}

        async def system_prompt(system, **kwargs):
            return system or ''

        chat_module.generate_chat_completion = provider
        payload_module.resolve_system_prompt = system_prompt
        payload_module.apply_params_to_form_data = lambda body, model, params: {
            **body,
            **{key: value for key, value in params.items() if key != 'system'},
        }
        models = {
            'collision': {
                'id': 'collision',
                'direct': True,
                'info': {'base_model_id': 'private-base', 'params': {'temperature': 0.2}},
            },
            'private-base': {'info': {'params': {'system': 'PRIVATE BASE'}}},
            'server-summary': {'id': 'server-summary'},
        }
        request = SimpleNamespace(
            state=SimpleNamespace(metadata={'session_id': 'owned-session'}, direct=True, model=models['collision'])
        )
        with patch.dict(
            sys.modules,
            {
                'open_webui.models.models': database,
                'open_webui.utils.chat': chat_module,
                'open_webui.utils.payload': payload_module,
            },
        ):
            asyncio.run(
                m.generate_summary(
                    request,
                    None,
                    'collision',
                    models,
                    [{'role': 'user', 'content': 'old facts'}],
                    '',
                    m.CompactionSettings(),
                )
            )
            database.Models.get_model_by_id.assert_not_awaited()
            self.assertEqual(seen[0]['temperature'], 0.2)
            self.assertNotIn('private_key', seen[0])
            self.assertNotIn('PRIVATE', str(seen[0]))
            asyncio.run(
                m.generate_summary(
                    request,
                    None,
                    'collision',
                    models,
                    [{'role': 'user', 'content': 'old facts'}],
                    '',
                    m.CompactionSettings(summary_model='server-summary'),
                )
            )
        database.Models.get_model_by_id.assert_awaited_once_with('server-summary')
        self.assertEqual(seen[1]['temperature'], 0.75)
        self.assertEqual(seen[1]['messages'][0]['content'], 'server summary instructions')


class ServerSummaryCollisionTests(IsolatedCase):
    def test_server_origin_inheritance_ignores_shadowing_browser_descriptor(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, call

        m = importlib.import_module('open_webui.utils.context_compaction_v2')
        rows = {
            'server': {'id': 'server', 'base_model_id': 'collision', 'params': {'temperature': 0.8}},
            'collision': {'id': 'collision', 'params': {'system': 'server base instruction', 'max_tokens': 128}},
        }
        database = ModuleType('open_webui.models.models')
        database.Models = SimpleNamespace(get_model_by_id=AsyncMock(side_effect=lambda identity: rows.get(identity)))
        models = {
            'server': {'id': 'server'},
            'collision': {
                'id': 'collision',
                'direct': True,
                'info': {
                    'base_model_id': 'unrelated-private-id',
                    'params': {'system': 'client override', 'max_tokens': 7},
                },
            },
        }
        with patch.dict(sys.modules, {'open_webui.models.models': database}):
            stored = asyncio.run(m.load_stored_models('server', models))
        self.assertEqual(database.Models.get_model_by_id.await_args_list, [call('server'), call('collision')])
        params = m.inherited_params(m.model_chain('server', models, stored))
        self.assertEqual(params, {'system': 'server base instruction', 'max_tokens': 128, 'temperature': 0.8})
