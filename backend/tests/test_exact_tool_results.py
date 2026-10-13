"""Trusted builtins retain exact schemas/text under V2 result budgets."""

import asyncio
import copy
import importlib
import json
from unittest.mock import AsyncMock, patch

import pytest
from test_context_compaction_v2 import IsolatedCase
import test_tool_result_budget as tool_tests


class ExactToolResultTests(IsolatedCase):
    def test_schema_reduction_keeps_whole_required_ranked_definitions(self):
        exact = importlib.import_module('open_webui.utils.exact_tool_results')
        tools = [
            {
                'name': f'tool{i}',
                'description': 'details ' * 10,
                'parameters': {
                    'type': 'object',
                    'properties': {'payload': {'type': 'object', 'properties': {'count': {'type': 'integer'}}}},
                    'required': ['payload'],
                    'additionalProperties': False,
                },
            }
            for i in range(4)
        ]
        raw = json.dumps({'tools': tools}, ensure_ascii=False)
        rendered = exact.resize_exact_tool_result(raw, 'tool_schema', len(raw) // 2, len)
        result = json.loads(rendered)
        assert 0 < len(result['tools']) < len(tools)
        assert result['tools'] == tools[: len(result['tools'])]
        assert result['omitted_count'] == len(tools) - len(result['tools'])
        assert len(rendered) <= len(raw) // 2
        with pytest.raises(ValueError, match='definition'):
            exact.resize_exact_tool_result(raw, 'tool_schema', 20, len)

    def test_skill_page_preserves_exact_unicode_prefix_and_absolute_offsets(self):
        exact = importlib.import_module('open_webui.utils.exact_tool_results')
        content = '中\n文😀 exact instructions. ' * 30
        page = {
            'id': 'skill',
            'version_id': 'version',
            'path': 'SKILL.md',
            'offset': 900,
            'content': content,
            'next_offset': None,
        }
        raw = json.dumps(page, ensure_ascii=False)
        result = json.loads(exact.resize_exact_tool_result(raw, 'skill_page', 240, len))
        assert content.startswith(result['content']) and result['content']
        assert result['offset'] == 900
        assert result['next_offset'] == 900 + len(result['content'])
        assert result['version_id'] == 'version'
        assert page['next_offset'] is None
        assert len(json.dumps(result, ensure_ascii=False, separators=(',', ':'))) <= 240
        with pytest.raises(ValueError, match='skill'):
            exact.resize_exact_tool_result(raw, 'skill_page', 10, len)

    def test_builtin_classification_uses_registration_not_untrusted_names(self):
        sources = importlib.import_module('open_webui.utils.tool_result_sources')
        names = ['search_tools', 'view_skill', 'read_skill_file']
        for kind, registry in [
            ('external', {name: {'type': 'external', 'tool_id': f'builtin:{name}'} for name in names}),
            ('builtin', {name: {'type': 'builtin', 'tool_id': f'builtin:{name}'} for name in names}),
        ]:
            output = [{'type': 'function_call', 'id': name, 'call_id': name, 'name': name} for name in names]
            sources.annotate_output_batch(output, names, tools=registry)
            assert [item['tool_result']['kind'] for item in output] == (
                ['native'] * 3 if kind == 'external' else ['tool_schema', 'skill_page', 'skill_page']
            )
            # Approval replay must not reclassify already persisted executions.
            previous = copy.deepcopy(output)
            sources.annotate_output_batch(output, names, tools={})
            assert output == previous

    def test_injected_and_forged_registrations_keep_the_same_trust_boundary(self):
        sources = importlib.import_module('open_webui.utils.tool_result_sources')
        assert (
            sources.registered_tool_kind(
                'view_skill', {'view_skill': {'type': 'builtin', 'tool_id': 'external:view_skill'}}, default='injected'
            )
            == 'injected'
        )
        assert (
            sources.registered_tool_kind(
                'view_skill', {'view_skill': {'type': 'builtin', 'tool_id': 'builtin:view_skill'}}, default='injected'
            )
            == 'skill_page'
        )


class ExactToolEngineTests(IsolatedCase):
    setup_engine = tool_tests.ToolBudgetEngineTests.setup_engine
    make_batch = tool_tests.ToolBudgetEngineTests.make_batch

    def install_exact(self, payload, history, kind, name, page):
        text = json.dumps(page, ensure_ascii=False)
        payload['messages'][-1]['content'] = text
        descriptor = payload['messages'][-1]['_tool_result_refs'][0]
        descriptor.update(text=text, kind=kind, name=name)
        payload['messages'][-2]['tool_calls'][0]['function']['name'] = name
        raw = history.read_snapshot.return_value['messages_map']['a2']['output']
        raw[0].update(name=name, tool_result={'kind': kind, 'name': name, 'batch_id': 'batch'})
        raw[1]['output'] = text
        return text

    def test_exact_skill_is_silent_and_never_summarized_or_changed_on_replay(self):
        v2, tool, request, payload, history, service, events = self.make_batch(context=10000)
        original = self.install_exact(
            payload,
            history,
            'skill_page',
            'view_skill',
            {'id': 'test', 'offset': 0, 'content': 'skill instructions ' * 1500, 'next_offset': None},
        )
        with patch.object(v2, 'generate_summary', AsyncMock(return_value='invalid summary')) as generate:
            result = asyncio.run(v2.prepare_v2_request(request, None, payload, {}, 'model', {'model': {}}))
        generate.assert_not_awaited()
        page = json.loads(result['messages'][-1]['content'])
        assert json.loads(original)['content'].startswith(page['content'])
        assert page['next_offset'] == len(page['content'])
        assert not any(kind == 'tool_result_compaction' for kind, _ in events)
        assert service.save_context.call_args.kwargs['method'] == 'read'
        assert payload['messages'][-1]['content'] == original
        request.state.context_compaction['settings'] = request.state.context_compaction['settings'].model_copy(
            update={'tool_result_percent': 1, 'tool_batch_percent': 1}
        )
        with patch.object(v2, 'generate_summary', AsyncMock()) as generate:
            replay = asyncio.run(v2.prepare_v2_request(request, None, payload, {}, 'model', {'model': {}}))
        assert replay['messages'][-1]['content'] == result['messages'][-1]['content']
        generate.assert_not_awaited()

    def test_schema_failure_is_actionable_and_never_falls_back_to_summary(self):
        v2, tool, request, payload, history, service, events = self.make_batch(context=10000)
        self.install_exact(
            payload,
            history,
            'tool_schema',
            'search_tools',
            {'tools': [{'name': 'required', 'description': 'huge schema ' * 5000, 'parameters': {'type': 'object'}}]},
        )
        with patch.object(v2, 'generate_summary', AsyncMock()) as generate:
            with pytest.raises(v2.CompactionFailure, match='definition'):
                asyncio.run(v2.prepare_v2_request(request, None, payload, {}, 'model', {'model': {}}))
        generate.assert_not_awaited()
        assert not any(kind == 'tool_result_compaction' for kind, _ in events)

    def test_shared_policy_and_cache_scope_use_trusted_state(self):
        v2, tool, request, payload, history, service, events = self.make_batch(context=10000)
        settings = request.state.context_compaction['settings']
        budget = v2.resolve_budget(settings, 'model', {'model': {}}, payload)
        state = request.state.context_compaction
        common = (
            request,
            None,
            payload,
            {},
            {'chat_id': 'chat', 'message_id': 'a2', 'shared_history': True},
            'model',
            {'model': {}},
            settings,
            budget,
        )
        private = tool.ToolBudgetPlan(*common, {**state, 'shared_history': False})
        shared = tool.ToolBudgetPlan(*common, {**state, 'shared_history': True})
        assert shared.policy_hash != private.policy_hash
        entry = next(iter(shared.entries.values()))
        entry.record = {'id': 'result'}
        asyncio.run(shared.cache_projection(entry, 'reduced', 'read', 100))
        assert service.save_context.call_args.kwargs['source_scope'] == 'saved_history_v1'

    def test_schema_reduction_uses_whole_entries_and_silent_accounting(self):
        v2, tool, request, payload, history, service, events = self.make_batch(context=10000)
        definitions = [
            {
                'name': f'tool{i}',
                'description': 'exact instruction ' * 100,
                'parameters': {'type': 'object', 'properties': {'count': {'type': 'integer'}}, 'required': ['count']},
            }
            for i in range(10)
        ]
        original = self.install_exact(payload, history, 'tool_schema', 'search_tools', {'tools': definitions})
        with patch.object(v2, 'generate_summary', AsyncMock()) as generate:
            result = asyncio.run(v2.prepare_v2_request(request, None, payload, {}, 'model', {'model': {}}))
        generate.assert_not_awaited()
        page = json.loads(result['messages'][-1]['content'])
        assert page['tools'] == definitions[: len(page['tools'])]
        assert 1 <= len(page['tools']) < len(definitions)
        assert v2.estimate_payload_tokens(result) <= 9500
        assert service.selections.rows[('a2', 'out')]['method'] == 'read'
        assert payload['messages'][-1]['content'] == original
        assert not any(kind == 'tool_result_compaction' for kind, _ in events)

    def test_external_skill_name_remains_a_normal_result(self):
        v2, tool, request, payload, history, service, events = self.make_batch(context=10000)
        self.install_exact(payload, history, 'native', 'view_skill', {'content': 'external text ' * 5000})
        with patch.object(v2, 'generate_summary', AsyncMock(return_value='external evidence')) as generate:
            result = asyncio.run(v2.prepare_v2_request(request, None, payload, {}, 'model', {'model': {}}))
        generate.assert_awaited_once()
        assert 'external evidence' in result['messages'][-1]['content']
        assert service.save_context.call_args.kwargs['method'] == 'summary'
