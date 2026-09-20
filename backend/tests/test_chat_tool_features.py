"""Exercise real builtin selection without importing application/database state."""

import ast
import asyncio
import importlib.util
import inspect
import json
import logging
import sys
import unittest
from functools import partial, update_wrapper
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Awaitable, Callable, Optional, get_args, get_type_hints
from unittest.mock import AsyncMock, patch


BACKEND = Path(__file__).parents[1] / 'open_webui'


def extract(path, names, namespace):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    nodes = [
        node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names
    ]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), namespace)


class ChatToolFeaturesTests(unittest.TestCase):
    def setUp(self):
        self.config_values = {'memories.enable': True, 'notes.enable': True}
        self.config = SimpleNamespace(
            get=AsyncMock(side_effect=lambda key, default=None: self.config_values.get(key, default)),
            get_many=AsyncMock(side_effect=lambda *keys: {key: self.config_values.get(key) for key in keys}),
        )
        self.permission = AsyncMock(return_value=True)
        modules = {}
        for name, attrs in {
            'open_webui.env': {'ENABLE_KB_EXEC': False},
            'open_webui.models.config': {'Config': self.config},
            'open_webui.utils.access_control': {'has_permission': self.permission},
        }.items():
            module = ModuleType(name)
            module.__dict__.update(attrs)
            modules[name] = module
        self.patcher = patch.dict(sys.modules, modules)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.env = modules['open_webui.env']
        self.namespace = {
            'Request': object,
            'Config': self.config,
            'has_permission': self.permission,
            'is_saved_chat_id': lambda value: False,
            'get_builtin_function_introspection': lambda function: {},
            'get_builtin_tool_spec': lambda function: {'name': function.__name__},
        }
        source = ast.parse((BACKEND / 'utils/tools.py').read_text(encoding='utf-8'))
        for node in source.body:
            if isinstance(node, ast.ImportFrom) and node.module == 'open_webui.tools.builtin':
                for alias in node.names:
                    function = AsyncMock(return_value='{}')
                    function.__name__ = alias.name
                    self.namespace[alias.name] = function

        async def bind(function, params, introspection):
            if not isinstance(function, AsyncMock):
                params = {key: value for key, value in params.items() if key in inspect.signature(function).parameters}

            async def bound(**kwargs):
                return await function(**kwargs, **params)

            return bound

        self.namespace['get_async_tool_function_and_apply_extra_params'] = bind
        helper_path = BACKEND / 'utils/tool_features.py'
        if helper_path.exists():
            spec = importlib.util.spec_from_file_location('tool_features_under_test', helper_path)
            self.helper = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.helper)
            self.namespace.update(vars(self.helper))
        extract(BACKEND / 'utils/tools.py', ['get_attached_knowledge', 'get_builtin_tools'], self.namespace)
        self.user = {'id': 'user-1', 'role': 'user', 'settings': {'ui': {'memory': True}}}
        self.model = {'info': {'meta': {}}}
        self.metadata = {}

    def tools(self, features=None, note_chat=False):
        return asyncio.run(
            self.namespace['get_builtin_tools'](
                SimpleNamespace(state=SimpleNamespace()),
                {'__user__': self.user, '__metadata__': self.metadata},
                features,
                self.model,
                note_chat,
            )
        )

    def test_knowledge_false_removes_discovery_with_both_exec_modes(self):
        for enabled in (False, True):
            self.env.ENABLE_KB_EXEC = enabled
            result = self.tools({'knowledge': False, 'notes': False})
            self.assertFalse(
                set(result)
                & {
                    'kb_exec',
                    'query_knowledge_files',
                    'list_knowledge_bases',
                    'search_knowledge_bases',
                    'query_knowledge_bases',
                    'view_knowledge_file',
                }
            )

    def test_legacy_and_true_retain_discovery(self):
        for features in ({}, {'knowledge': True, 'notes': True}):
            result = self.tools(features)
            self.assertIn('query_knowledge_bases', result)
            self.assertIn('search_notes', result)

    def test_notes_false_wins_in_embedded_chat(self):
        for embedded in (False, True):
            result = self.tools({'notes': False}, embedded)
            self.assertFalse(set(result) & {'search_notes', 'write_note', 'replace_note_content', 'view_note'})

    def test_explicit_notes_true_obeys_model_config_and_permission_in_embedded_chat(self):
        self.config_values['notes.enable'] = False
        self.assertNotIn('write_note', self.tools({'notes': True}, True))
        self.assertIn('write_note', self.tools({}, True))

    def test_memory_requires_global_master_and_model_gates(self):
        self.assertIn('search_memories', self.tools({'memory': True}))
        self.assertNotIn('search_memories', self.tools({}))
        self.config_values['memories.enable'] = False
        self.assertNotIn('search_memories', self.tools({'memory': True}))
        self.config_values['memories.enable'] = True
        self.user['settings']['ui']['memory'] = False
        self.assertNotIn('search_memories', self.tools({'memory': True}))

    def test_disabled_categories_keep_only_scoped_attachment_reading(self):
        self.model['info']['meta']['knowledge'] = [
            {'type': 'file', 'id': 'attached-file'},
            {'type': 'note', 'id': 'attached-note'},
        ]
        result = self.tools({'knowledge': False, 'notes': False})
        self.assertIn('view_file', result)
        self.assertIn('view_note', result)
        self.assertIn('list_knowledge', result)
        self.assertNotIn('kb_exec', result)
        for name, arg, allowed in [
            ('view_file', 'file_id', 'attached-file'),
            ('view_note', 'note_id', 'attached-note'),
        ]:
            self.assertEqual(asyncio.run(result[name]['callable'](**{arg: allowed})), '{}')
            self.assertIn('error', json.loads(asyncio.run(result[name]['callable'](**{arg: 'other'}))))

    def test_notes_scope_applies_when_knowledge_adds_view_note(self):
        self.model['info']['meta']['knowledge'] = [{'type': 'note', 'id': 'attached-note'}]
        result = self.tools({'notes': False})
        self.assertIn('error', json.loads(asyncio.run(result['view_note']['callable'](note_id='other'))))

    def test_each_explicit_category_respects_builtin_capability(self):
        self.model['info']['meta']['capabilities'] = {'builtin_tools': False}
        result = self.tools({'knowledge': True, 'notes': True, 'memory': True}, True)
        self.assertFalse(set(result) & {'query_knowledge_bases', 'write_note', 'search_memories'})

    def test_notes_obeys_permission_and_category_for_explicit_true(self):
        self.permission.return_value = False
        self.assertNotIn('write_note', self.tools({'notes': True}, True))
        self.permission.return_value = True
        self.model['info']['meta']['builtinTools'] = {'notes': False}
        self.assertNotIn('write_note', self.tools({'notes': True}, True))

    def test_memory_capabilities_category_permission_and_admin(self):
        for setting in ('capabilities', 'builtinTools'):
            self.model['info']['meta'][setting] = {'memory': False}
            self.assertNotIn('add_memory', self.tools({'memory': True}))
            self.model['info']['meta'][setting] = {}
        self.permission.return_value = False
        self.assertNotIn('add_memory', self.tools({'memory': True}))
        self.user['role'] = 'admin'
        self.assertIn('add_memory', self.tools({'memory': True}))
        self.user['settings']['ui']['memory'] = False
        self.assertNotIn('add_memory', self.tools({'memory': True}))

    def test_memory_default_interface_then_global_fallback(self):
        self.user['settings'] = None
        self.assertIn('add_memory', self.tools({'memory': True}))
        self.config_values['ui.default_interface_settings'] = {'memory': False}
        self.assertNotIn('add_memory', self.tools({'memory': True}))
        self.user['settings'] = {'ui': {'memory': True}}
        self.assertIn('add_memory', self.tools({'memory': True}))

    def test_null_memory_preferences_use_the_same_fallback_as_frontend(self):
        self.user['settings'] = {'ui': {'memory': None}}
        self.config_values['ui.default_interface_settings'] = {'memory': None}
        self.assertIn('add_memory', self.tools({'memory': True}))

    def test_memory_context_skips_database_when_disabled(self):
        self.namespace['Memories'] = SimpleNamespace(get_memories_by_user_id=AsyncMock())
        self.namespace['get_content_from_message'] = lambda message: message.get('content', '')
        extract(BACKEND / 'utils/memory.py', ['add_memory_context'], self.namespace)
        form = {'messages': [{'role': 'user', 'content': 'Remember this'}]}
        user = SimpleNamespace(id='user-1', model_dump=lambda: self.user)
        self.user['settings']['ui']['memory'] = False
        result = asyncio.run(self.namespace['add_memory_context'](None, form, user, self.model, {'memory': True}))
        self.assertIs(result, form)
        self.namespace['Memories'].get_memories_by_user_id.assert_not_awaited()

    def test_memory_background_review_respects_total_switch(self):
        self.namespace['get_content_from_message'] = lambda message: message.get('content', '')
        self.namespace['asyncio'] = asyncio
        self.namespace['_review_memory'] = AsyncMock()
        extract(BACKEND / 'utils/memory.py', ['model_allows_memory', 'review_memory_after_turn'], self.namespace)
        self.config_values.update({'memories.background_review.enable': True, 'memories.review_interval_turns': 1})
        self.user['settings']['ui']['memory'] = False

        async def run():
            await self.namespace['review_memory_after_turn'](
                request=None,
                user=self.user,
                model=self.model,
                metadata={'features': {'memory': True}},
                form_data={},
                assistant_message={'content': 'Saved'},
                messages=[{'role': 'user'}],
            )
            await asyncio.sleep(0)

        asyncio.run(run())
        self.namespace['_review_memory'].assert_not_awaited()

    def test_folder_and_tool_only_chat_attachments_are_preserved(self):
        self.model['info']['meta']['capabilities'] = {'file_context': False}
        self.metadata = {
            'folder_knowledge': [{'type': 'note', 'id': 'folder-note'}],
            'files': [
                {'type': 'collection', 'id': 'chat-kb'},
                {'type': 'note', 'id': 'chat-note'},
                {'type': 'file', 'id': 'chat-file'},
            ],
        }
        result = self.tools({'knowledge': False, 'notes': False})
        for name, kwargs in [
            ('view_note', {'note_id': 'folder-note'}),
            ('view_note', {'note_id': 'chat-note'}),
            ('view_file', {'file_id': 'chat-file'}),
        ]:
            self.assertEqual(asyncio.run(result[name]['callable'](**kwargs)), '{}')
        self.assertIn('query_chat_files', result)
        self.assertIn(
            'error',
            json.loads(asyncio.run(result['query_knowledge_files']['callable'](query='x', knowledge_ids=['other-kb']))),
        )

    def test_rag_attached_content_and_metadata_are_not_changed(self):
        self.model['info']['meta']['capabilities'] = {'file_context': True}
        self.metadata = {'files': [{'type': 'file', 'id': 'chat-file'}, {'type': 'note', 'id': 'chat-note'}]}
        self.tools({'knowledge': False, 'notes': False})
        self.assertEqual(
            self.metadata['files'], [{'type': 'file', 'id': 'chat-file'}, {'type': 'note', 'id': 'chat-note'}]
        )

    def test_embedded_note_remains_readable_when_notes_off(self):
        self.metadata['note_id'] = 'embedded-note'
        result = self.tools({'knowledge': False, 'notes': False}, True)
        self.assertEqual(asyncio.run(result['view_note']['callable'](note_id='embedded-note')), '{}')
        self.assertNotIn('write_note', result)

    def test_embedded_note_does_not_change_legacy_knowledge_discovery(self):
        self.metadata['note_id'] = 'embedded-note'
        self.assertIn('query_knowledge_bases', self.tools({}, True))

    def install_module(self, name, **attrs):
        module = ModuleType(name)
        module.__dict__.update(attrs)
        sys.modules[name] = module

    def test_collection_scope_requires_membership_and_access(self):
        self.model['info']['meta']['knowledge'] = [{'type': 'collection', 'id': 'attached-kb'}]
        bases = AsyncMock(return_value=[SimpleNamespace(id='attached-kb', user_id='other-user')])
        access = AsyncMock(return_value=False)
        self.install_module('open_webui.models.knowledge', Knowledges=SimpleNamespace(get_knowledges_by_file_id=bases))
        self.install_module('open_webui.models.access_grants', AccessGrants=SimpleNamespace(has_access=access))
        for exec_enabled in (False, True):
            self.env.ENABLE_KB_EXEC = exec_enabled
            result = self.tools({'knowledge': False})
            denied = asyncio.run(result['view_file']['callable'](file_id='kb-file'))
            self.assertIn('error', json.loads(denied))
            self.namespace['view_file'].assert_not_awaited()
            access.return_value = True
            self.assertEqual(asyncio.run(result['view_file']['callable'](file_id='kb-file')), '{}')
            self.namespace['view_file'].reset_mock()
            access.return_value = False
        bases.return_value = [SimpleNamespace(id='other-kb', user_id='user-1')]
        self.assertIn('error', json.loads(asyncio.run(result['view_file']['callable'](file_id='other-file'))))
        self.namespace['view_file'].assert_not_awaited()

    def test_empty_attachment_scope_never_calls_broad_query(self):
        query = AsyncMock(return_value='should not search')
        scoped = self.helper.scope_attachment_tool('query_knowledge_files', query, [], [], self.user)
        self.assertIn('error', json.loads(asyncio.run(scoped(query='hello'))))
        query.assert_not_awaited()

    def test_native_rebinding_preserves_attachment_scope_and_updates_context(self):
        self.namespace.update(
            {
                'Awaitable': Awaitable,
                'Callable': Callable,
                'get_args': get_args,
                'get_type_hints': get_type_hints,
                'inspect': inspect,
                'partial': partial,
                'update_wrapper': update_wrapper,
                'get_builtin_function_introspection': lambda function: (inspect.signature(function), {}),
            }
        )
        extract(
            BACKEND / 'utils/tools.py',
            ['get_async_tool_function_and_apply_extra_params', 'get_updated_tool_function'],
            self.namespace,
        )
        calls = []

        async def view_note(note_id: str, __messages__=None, __user__=None):
            calls.append(note_id)
            return json.dumps({'messages': __messages__, 'user_id': __user__['id']})

        async def view_file(file_id: str, __messages__=None, __files__=None):
            calls.append(file_id)
            return json.dumps({'messages': __messages__, 'files': __files__})

        async def list_knowledge(knowledge_id=None, __messages__=None, __model_knowledge__=None):
            calls.append(knowledge_id)
            return json.dumps({'messages': __messages__, 'attachments': __model_knowledge__})

        async def query_knowledge_files(query: str, knowledge_ids=None, __messages__=None, __model_knowledge__=None):
            calls.append(query)
            return json.dumps({'messages': __messages__, 'attachments': __model_knowledge__})

        for function in (view_note, view_file, list_knowledge, query_knowledge_files):
            self.namespace[function.__name__] = function
        attachments = [{'type': 'file', 'id': 'attached-file'}, {'type': 'note', 'id': 'attached-note'}]
        self.model['info']['meta']['knowledge'] = attachments
        tools = self.tools({'knowledge': False, 'notes': False})
        cases = [
            ('view_note', {'note_id': 'attached-note'}, {'note_id': 'other-note'}),
            ('view_file', {'file_id': 'attached-file'}, {'file_id': 'other-file'}),
            ('list_knowledge', {}, {'knowledge_id': 'other-kb'}),
            ('query_knowledge_files', {'query': 'hello'}, {'query': 'hello', 'knowledge_ids': ['other-kb']}),
        ]
        for name, allowed, denied in cases:
            with self.subTest(tool=name):
                function = tools[name]['callable']
                for turn in range(2):
                    messages = [{'role': 'user', 'content': f'turn {turn}'}]
                    files = [{'type': 'file', 'id': 'attached-file', 'name': f'file {turn}'}]
                    function = asyncio.run(
                        self.namespace['get_updated_tool_function'](
                            function, {'__messages__': messages, '__files__': files}
                        )
                    )
                    result = json.loads(asyncio.run(function(**allowed)))
                    self.assertEqual(result['messages'], messages)
                    if name == 'view_note':
                        self.assertEqual(result['user_id'], self.user['id'])
                    elif name == 'view_file':
                        self.assertEqual(result['files'], files)
                    else:
                        self.assertEqual(
                            {item['id'] for item in result['attachments']}, {'attached-file', 'attached-note'}
                        )
                    call_count = len(calls)
                    self.assertIn('error', json.loads(asyncio.run(function(**denied))))
                    self.assertEqual(len(calls), call_count)

    def test_actual_note_reader_keeps_resource_acl_after_scope_check(self):
        note = SimpleNamespace(
            id='attached-note',
            user_id='another-user',
            title='Private',
            data={'content': {'md': 'secret'}},
            updated_at=0,
            created_at=0,
        )
        access = AsyncMock(return_value=False)
        self.install_module('open_webui.models.access_grants', AccessGrants=SimpleNamespace(has_access=access))
        self.namespace.update(
            {
                'Notes': SimpleNamespace(get_note_by_id=AsyncMock(return_value=note)),
                'Groups': SimpleNamespace(get_groups_by_member_id=AsyncMock(return_value=[])),
                'JSONCodec': json,
                'log': logging.getLogger(__name__),
            }
        )
        extract(BACKEND / 'tools/builtin.py', ['view_note'], self.namespace)
        self.model['info']['meta']['knowledge'] = [{'type': 'note', 'id': 'attached-note'}]
        result = self.tools({'knowledge': False, 'notes': False})
        self.assertEqual(
            json.loads(asyncio.run(result['view_note']['callable'](note_id='attached-note'))),
            {'error': 'Access denied'},
        )
        access.return_value = True
        self.assertEqual(
            json.loads(asyncio.run(result['view_note']['callable'](note_id='attached-note')))['content'], 'secret'
        )

    def test_actual_file_reader_keeps_resource_acl_after_scope_check(self):
        file = SimpleNamespace(
            id='chat-file',
            user_id='another-user',
            filename='Private',
            data={'content': 'secret'},
            updated_at=0,
            created_at=0,
        )
        self.install_module(
            'open_webui.models.files', Files=SimpleNamespace(get_file_by_id=AsyncMock(return_value=file))
        )
        acl = AsyncMock(return_value=False)
        self.namespace.update(
            {
                'Optional': Optional,
                'JSONCodec': json,
                'log': logging.getLogger(__name__),
                'VIEW_FILE_DEFAULT_MAX_CHARS': 10000,
                'VIEW_FILE_MAX_CHARS': 10000,
                '_has_read_access_to_file': acl,
            }
        )
        extract(BACKEND / 'tools/builtin.py', ['view_file'], self.namespace)
        self.model['info']['meta']['capabilities'] = {'file_context': False}
        self.metadata['files'] = [{'type': 'file', 'id': 'chat-file'}]
        result = self.tools({'knowledge': False})
        self.assertEqual(
            json.loads(asyncio.run(result['view_file']['callable'](file_id='chat-file'))), {'error': 'File not found'}
        )
        acl.return_value = True
        self.assertEqual(
            json.loads(asyncio.run(result['view_file']['callable'](file_id='chat-file')))['content'], 'secret'
        )

    def test_middleware_still_dispatches_rag_with_new_features_off(self):
        source = ast.parse((BACKEND / 'utils/middleware.py').read_text(encoding='utf-8'))
        handler = next(
            node
            for node in ast.walk(source)
            if isinstance(node, ast.AsyncFunctionDef) and node.name == 'process_chat_payload'
        )
        index = next(
            i
            for i, node in enumerate(handler.body)
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == 'file_context_enabled' for target in node.targets)
        )
        function = ast.AsyncFunctionDef(
            name='rag_dispatch',
            args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]),
            body=handler.body[index : index + 2],
            decorator_list=[],
        )
        form = {
            'messages': [],
            'files': [{'type': 'file', 'id': 'attached-file'}],
            'features': {'knowledge': False, 'notes': False, 'memory': False},
        }
        files_handler = AsyncMock(return_value=(form, {'sources': ['attached-source']}))
        namespace = {
            'model': self.model,
            'form_data': form,
            'chat_completion_files_handler': files_handler,
            'request': None,
            'extra_params': {},
            'user': self.user,
            'sources': [],
            'log': logging.getLogger(__name__),
        }
        # The production block reassigns form_data locally, so initialize that local in the extracted function.
        function.body.insert(
            0,
            ast.Assign(
                targets=[ast.Name(id='form_data', ctx=ast.Store())], value=ast.Name(id='initial_form', ctx=ast.Load())
            ),
        )
        namespace['initial_form'] = form
        exec(
            compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])), '<rag dispatch>', 'exec'),
            namespace,
        )
        asyncio.run(namespace['rag_dispatch']())
        files_handler.assert_awaited_once()
        self.assertEqual(namespace['sources'], ['attached-source'])


if __name__ == '__main__':
    unittest.main()
