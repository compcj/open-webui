import importlib.util
import asyncio
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError


BACKEND = Path(__file__).parents[1] / 'open_webui'
MODULE_PATH = BACKEND / 'utils/images/engines.py'


def load_engines_module():
    spec = importlib.util.spec_from_file_location('image_generation_engines_under_test', MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def install_module(monkeypatch, name, **values):
    module = ModuleType(name)
    module.__dict__.update(values)
    monkeypatch.setitem(sys.modules, name, module)
    return module


@pytest.fixture
def image_router(monkeypatch, tmp_path):
    from pydantic import BaseModel, ConfigDict

    values = {
        'image_generation.enable': True,
        'image_generation.prompt.enable': True,
        'image_generation.engine': 'openai',
        'image_generation.model': 'default-model',
        'image_generation.size': '512x512',
        'image_generation.steps': 20,
        'image_generation.engines': [],
        'image_generation.tool_description_suffix': '',
        'image_generation.openai.api_base_url': 'https://default.example.test/v1',
        'image_generation.openai.api_key': 'default-key',
        'image_generation.openai.api_version': '',
        'image_generation.openai.params': {},
        'image_generation.automatic1111.base_url': '',
        'image_generation.automatic1111.api_auth': '',
        'image_generation.automatic1111.api_params': {},
        'image_generation.comfyui.base_url': 'https://comfy.example.test',
        'image_generation.comfyui.api_key': 'comfy-key',
        'image_generation.comfyui.workflow': '{}',
        'image_generation.comfyui.nodes': [],
        'image_generation.gemini.api_base_url': 'https://gemini.example.test/v1beta',
        'image_generation.gemini.api_key': 'gemini-key',
        'image_generation.gemini.endpoint_method': 'predict',
        'images.edit.enable': False,
        'images.edit.tool_description_suffix': '',
        'images.edit.engine': 'openai',
        'images.edit.model': '',
        'images.edit.size': '',
        'images.edit.openai.api_base_url': '',
        'images.edit.openai.api_key': '',
        'images.edit.openai.api_version': '',
        'images.edit.gemini.api_base_url': '',
        'images.edit.gemini.api_key': '',
        'images.edit.comfyui.base_url': '',
        'images.edit.comfyui.api_key': '',
        'images.edit.comfyui.workflow': '',
        'images.edit.comfyui.nodes': [],
        'user.permissions': {'features': {'image_generation': True}},
    }
    state = SimpleNamespace(
        values=values,
        upserts=[],
        get_many_calls=0,
        permission=True,
        posts=[],
        response_json={'data': [{'b64_json': 'aW1hZ2U='}]},
        comfy_calls=[],
    )

    class Config:
        @classmethod
        async def get_many(cls, *keys):
            state.get_many_calls += 1
            return {key: state.values[key] for key in keys if key in state.values}

        @classmethod
        async def upsert(cls, updates):
            state.upserts.append(updates.copy())
            state.values.update(updates)

    class ErrorMessages:
        ACCESS_PROHIBITED = 'Access prohibited'
        INVALID_URL = 'Invalid URL'

        @staticmethod
        def DEFAULT(error, fallback='Error'):
            return str(error) or fallback

        @staticmethod
        def INCORRECT_FORMAT(detail):
            return f'Incorrect format{detail}'

    class WorkflowNode(BaseModel):
        type: str = ''
        key: str = ''
        node_ids: list[str] = []

    class Workflow(BaseModel):
        workflow: str
        nodes: list[WorkflowNode]

    class CreateForm(BaseModel):
        model_config = ConfigDict(extra='ignore')
        workflow: Workflow
        prompt: str
        negative_prompt: str | None = None
        width: int
        height: int
        n: int = 1
        steps: int | None = None
        seed: int | None = None

    class EditForm(BaseModel):
        model_config = ConfigDict(extra='allow')
        workflow: Workflow

    async def comfy_create(model, form_data, client_id, base_url, api_key):
        state.comfy_calls.append((model, form_data, base_url, api_key))
        return {'data': [{'url': f'{base_url}/view?filename=image.png'}]}

    async def user_dependency():
        return SimpleNamespace(id='user-1', role='user')

    async def has_permission(*args):
        return state.permission

    class Response:
        headers = {'content-type': 'application/json'}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def raise_for_status(self):
            return None

        async def json(self, content_type=None):
            return state.response_json

    class Session:
        def post(self, **kwargs):
            state.posts.append(kwargs)
            return Response()

        def get(self, **kwargs):
            state.posts.append(kwargs)
            return Response()

    async def get_session():
        return Session()

    package_paths = {
        'open_webui': BACKEND,
        'open_webui.internal': BACKEND / 'internal',
        'open_webui.models': BACKEND / 'models',
        'open_webui.retrieval': BACKEND / 'retrieval',
        'open_webui.retrieval.web': BACKEND / 'retrieval/web',
        'open_webui.routers': BACKEND / 'routers',
        'open_webui.utils': BACKEND / 'utils',
        'open_webui.utils.images': BACKEND / 'utils/images',
    }
    for name, path in package_paths.items():
        package = ModuleType(name)
        package.__path__ = [str(path)]
        monkeypatch.setitem(sys.modules, name, package)

    install_module(
        monkeypatch,
        'open_webui.config',
        CACHE_DIR=tmp_path,
        ENABLE_OPENAI_IMAGE_EDIT_NORMALIZATION=False,
        IMAGE_AUTO_SIZE_MODELS_REGEX_PATTERN='^gpt-image',
        IMAGE_URL_RESPONSE_MODELS_REGEX_PATTERN='^gpt-image',
    )
    install_module(monkeypatch, 'open_webui.constants', ERROR_MESSAGES=ErrorMessages)
    install_module(
        monkeypatch,
        'open_webui.env',
        AIOHTTP_CLIENT_ALLOW_REDIRECTS=False,
        AIOHTTP_CLIENT_SESSION_SSL=True,
        ENABLE_FORWARD_USER_INFO_HEADERS=False,
    )
    install_module(
        monkeypatch,
        'open_webui.events',
        EVENTS=SimpleNamespace(CONFIG_UPDATED='config', IMAGE_GENERATED='image'),
        publish_event=AsyncMock(),
    )
    install_module(monkeypatch, 'open_webui.internal.db', get_async_session=lambda: None)
    install_module(monkeypatch, 'open_webui.models.chats', Chats=SimpleNamespace(insert_chat_files=AsyncMock()))
    install_module(monkeypatch, 'open_webui.models.config', Config=Config)
    install_module(
        monkeypatch,
        'open_webui.retrieval.web.utils',
        get_ssrf_safe_session=AsyncMock(),
        validate_url=lambda url: None,
    )
    install_module(
        monkeypatch,
        'open_webui.routers.files',
        get_file_content_by_id=AsyncMock(),
        upload_file_handler=AsyncMock(),
    )
    install_module(monkeypatch, 'open_webui.utils.access_control', has_permission=has_permission)
    install_module(
        monkeypatch,
        'open_webui.utils.auth',
        get_admin_user=user_dependency,
        get_verified_user=user_dependency,
    )
    install_module(monkeypatch, 'open_webui.utils.headers', include_user_info_headers=lambda headers, user: headers)
    install_module(
        monkeypatch,
        'open_webui.utils.images.comfyui',
        ComfyUICreateImageForm=CreateForm,
        ComfyUIEditImageForm=EditForm,
        ComfyUIWorkflow=Workflow,
        comfyui_create_image=comfy_create,
        comfyui_edit_image=AsyncMock(),
        comfyui_upload_image=AsyncMock(),
    )
    install_module(monkeypatch, 'open_webui.utils.json_codec', JSONCodec=json)
    install_module(monkeypatch, 'open_webui.utils.session_pool', get_session=get_session)
    install_module(monkeypatch, 'aiofiles', open=lambda *args, **kwargs: None)
    install_module(monkeypatch, 'sqlalchemy')
    install_module(monkeypatch, 'sqlalchemy.ext')
    install_module(monkeypatch, 'sqlalchemy.ext.asyncio', AsyncSession=object)

    engines_spec = importlib.util.spec_from_file_location(
        'open_webui.utils.images.engines', BACKEND / 'utils/images/engines.py'
    )
    engines = importlib.util.module_from_spec(engines_spec)
    monkeypatch.setitem(sys.modules, engines_spec.name, engines)
    engines_spec.loader.exec_module(engines)

    router_spec = importlib.util.spec_from_file_location('image_router_under_test', BACKEND / 'routers/images.py')
    router = importlib.util.module_from_spec(router_spec)
    monkeypatch.setitem(sys.modules, router_spec.name, router)
    router_spec.loader.exec_module(router)
    return SimpleNamespace(module=router, state=state, engines=engines)


def profile(**overrides):
    values = {
        'id': 'studio',
        'name': 'Studio',
        'engine': 'openai',
        'model': 'gpt-image-1',
        'base_url': 'https://images.example.test/v1/',
        'api_key': 'synthetic-secret',
        'api_version': '',
        'params': {'quality': 'high'},
        'size': '1024x1024',
        'steps': None,
        'gemini_endpoint_method': 'generateContent',
        'comfyui_workflow': '',
        'comfyui_workflow_nodes': [],
    }
    values.update(overrides)
    return values


def default_config():
    return SimpleNamespace(
        ENABLE_IMAGE_GENERATION=True,
        IMAGE_GENERATION_ENGINE='openai',
        IMAGE_GENERATION_MODEL='default-model',
        IMAGE_SIZE='512x512',
        IMAGE_STEPS=50,
        IMAGES_OPENAI_API_BASE_URL='https://default.example.test/v1',
        IMAGES_OPENAI_API_KEY='default-secret',
        IMAGES_OPENAI_API_VERSION='',
        IMAGES_OPENAI_API_PARAMS={'quality': 'standard'},
        IMAGES_GEMINI_API_BASE_URL='https://gemini.example.test/v1beta',
        IMAGES_GEMINI_API_KEY='gemini-default-secret',
        IMAGES_GEMINI_ENDPOINT_METHOD='predict',
        COMFYUI_BASE_URL='https://comfy.example.test',
        COMFYUI_API_KEY='comfy-default-secret',
        COMFYUI_WORKFLOW='{}',
        COMFYUI_WORKFLOW_NODES=[],
        IMAGE_EDIT_ENGINE='openai',
        IMAGE_EDIT_MODEL='default-edit-model',
        IMAGE_EDIT_SIZE='256x256',
        IMAGES_EDIT_OPENAI_API_BASE_URL='https://default-edit.example.test/v1',
        IMAGES_EDIT_OPENAI_API_KEY='default-edit-secret',
        IMAGES_EDIT_OPENAI_API_VERSION='',
        IMAGES_EDIT_GEMINI_API_BASE_URL='https://default-edit-gemini.example.test/v1beta',
        IMAGES_EDIT_GEMINI_API_KEY='default-edit-gemini-secret',
        IMAGES_EDIT_COMFYUI_BASE_URL='https://default-edit-comfy.example.test',
        IMAGES_EDIT_COMFYUI_API_KEY='default-edit-comfy-secret',
        IMAGES_EDIT_COMFYUI_WORKFLOW='{}',
        IMAGES_EDIT_COMFYUI_WORKFLOW_NODES=[],
        USER_PERMISSIONS={'features': {'image_generation': True}},
    )


def test_tool_description_suffixes_roundtrip_and_follow_engine_resolution():
    engines = load_engines_module()
    source = profile(
        tool_description_suffix='  Describe the final scene.\n保留构图。  ',
        edit={
            'engine': 'grok',
            'tool_description_suffix': '\nDescribe only the requested change.\n',
        },
    )
    profiles = engines.normalize_engine_profiles([source])
    saved = profiles[0].model_dump(mode='json')
    assert saved.get('tool_description_suffix') == 'Describe the final scene.\n保留构图。'
    assert saved['edit'].get('tool_description_suffix') == 'Describe only the requested change.'
    assert engines.normalize_engine_profiles([saved])[0].model_dump(mode='json') == saved
    assert source['tool_description_suffix'].startswith('  ')

    default = default_config()
    default.IMAGE_GENERATION_TOOL_DESCRIPTION_SUFFIX = 'Default generation'
    default.IMAGE_EDIT_TOOL_DESCRIPTION_SUFFIX = 'Default edit'
    generation = engines.resolve_image_generation_config(default, profiles, 'studio')
    edit = engines.resolve_image_edit_config(default, profiles, 'studio')
    assert generation.IMAGE_GENERATION_TOOL_DESCRIPTION_SUFFIX == saved['tool_description_suffix']
    assert edit.IMAGE_EDIT_TOOL_DESCRIPTION_SUFFIX == saved['edit']['tool_description_suffix']
    assert default.IMAGE_GENERATION_TOOL_DESCRIPTION_SUFFIX == 'Default generation'


@pytest.mark.parametrize('suffix', [None, '', ' \n '])
def test_independent_empty_tool_description_suffixes_do_not_inherit(suffix):
    engines = load_engines_module()
    profiles = engines.normalize_engine_profiles(
        [profile(tool_description_suffix=suffix, edit={'engine': 'grok', 'tool_description_suffix': suffix})]
    )
    default = default_config()
    default.IMAGE_GENERATION_TOOL_DESCRIPTION_SUFFIX = 'Default generation'
    default.IMAGE_EDIT_TOOL_DESCRIPTION_SUFFIX = 'Default edit'
    assert (
        engines.resolve_image_generation_config(default, profiles, 'studio').IMAGE_GENERATION_TOOL_DESCRIPTION_SUFFIX
        == ''
    )
    assert engines.resolve_image_edit_config(default, profiles, 'studio').IMAGE_EDIT_TOOL_DESCRIPTION_SUFFIX == ''


def test_old_profiles_have_empty_suffixes_and_default_editor_fallback():
    engines = load_engines_module()
    profiles = engines.normalize_engine_profiles([profile()])
    assert profiles[0].model_dump().get('tool_description_suffix') == ''
    default = default_config()
    default.IMAGE_GENERATION_TOOL_DESCRIPTION_SUFFIX = 'Default generation'
    default.IMAGE_EDIT_TOOL_DESCRIPTION_SUFFIX = 'Default edit'
    for selected in (None, '', 'deleted'):
        assert (
            engines.resolve_image_generation_config(
                default, profiles, selected
            ).IMAGE_GENERATION_TOOL_DESCRIPTION_SUFFIX
            == 'Default generation'
        )
    for selected in (None, '', 'deleted', 'studio'):
        assert (
            engines.resolve_image_edit_config(default, profiles, selected).IMAGE_EDIT_TOOL_DESCRIPTION_SUFFIX
            == 'Default edit'
        )


def test_paired_profile_roundtrip_and_independent_edit_resolution():
    engines = load_engines_module()
    source = profile(
        id='pair-a',
        api_key='generation-a-secret',
        edit={
            'engine': 'gemini',
            'model': 'gemini-edit-a',
            'base_url': 'https://gemini-edit-a.example.test/v1beta/',
            'api_key': 'edit-a-secret',
            'params': {'generationConfig': {'temperature': 0.4}},
        },
    )
    pair_b = profile(
        id='pair-b',
        name='Pair B',
        api_key='generation-b-secret',
        edit={
            'engine': 'openai',
            'model': 'openai-edit-b',
            'base_url': 'https://openai-edit-b.example.test/v1',
            'api_key': 'edit-b-secret',
            'params': {'quality': 'high'},
        },
    )
    profiles = engines.normalize_engine_profiles([source, pair_b])
    serialized = [item.model_dump(mode='json') for item in profiles]
    assert serialized[0]['edit']['base_url'] == 'https://gemini-edit-a.example.test/v1beta'
    assert serialized[0]['edit']['comfyui_workflow_nodes'] == []
    assert [item.model_dump(mode='json') for item in engines.normalize_engine_profiles(serialized)] == serialized

    default = default_config()
    edit_a = engines.resolve_image_edit_config(default, profiles, 'pair-a')
    edit_b = engines.resolve_image_edit_config(default, profiles, 'pair-b')
    generation_a = engines.resolve_image_generation_config(default, profiles, 'pair-a')

    assert (edit_a.IMAGE_EDIT_ENGINE, edit_a.IMAGE_EDIT_MODEL) == ('gemini', 'gemini-edit-a')
    assert edit_a.IMAGES_EDIT_GEMINI_API_KEY == 'edit-a-secret'
    assert edit_a.IMAGES_EDIT_OPENAI_API_KEY == ''
    assert edit_a.IMAGES_EDIT_COMFYUI_API_KEY == ''
    assert edit_a.IMAGE_EDIT_PARAMS == {'generationConfig': {'temperature': 0.4}}
    assert edit_b.IMAGES_EDIT_OPENAI_API_KEY == 'edit-b-secret'
    assert edit_b.IMAGES_EDIT_GEMINI_API_KEY == ''
    assert generation_a.IMAGES_OPENAI_API_KEY == 'generation-a-secret'
    assert generation_a.IMAGES_EDIT_GEMINI_API_KEY == 'default-edit-gemini-secret'
    edit_a.IMAGE_EDIT_MODEL = 'mutated'
    assert default.IMAGE_EDIT_MODEL == 'default-edit-model'
    assert edit_b.IMAGE_EDIT_MODEL == 'openai-edit-b'


@pytest.mark.parametrize('selected', [None, '', 'deleted', 'legacy', 'null-edit'])
def test_missing_or_null_edit_uses_default_edit_settings(selected):
    engines = load_engines_module()
    profiles = engines.normalize_engine_profiles(
        [profile(id='legacy'), profile(id='null-edit', name='Null Edit', edit=None)]
    )
    default = default_config()

    resolved = engines.resolve_image_edit_config(default, profiles, selected)

    assert resolved is not default
    assert vars(resolved) == vars(default)


@pytest.mark.parametrize(
    'edit',
    [
        {'engine': 'openai', 'model': '', 'base_url': ''},
        {'engine': 'nope'},
        {'engine': 'comfyui', 'base_url': 'https://edit.example.test', 'comfyui_workflow': '[]'},
        {'engine': 'gemini', 'model': 'gemini-edit', 'base_url': 'https://edit.example.test', 'params': []},
    ],
)
def test_invalid_nested_edit_is_rejected_without_echoing_credentials(edit):
    engines = load_engines_module()
    edit['api_key'] = 'nested-secret'
    with pytest.raises((ValueError, ValidationError)) as exc:
        engines.normalize_engine_profiles([profile(edit=edit)])
    assert 'nested-secret' not in engines.describe_profile_validation_error(exc.value)


def test_profiles_are_normalized_without_exposing_mutable_input():
    engines = load_engines_module()
    raw = profile(id='  studio  ', name='  Studio Images  ')

    normalized = engines.normalize_engine_profiles([raw])

    assert normalized[0].id == 'studio'
    assert normalized[0].name == 'Studio Images'
    assert normalized[0].base_url == 'https://images.example.test/v1'
    raw['params']['quality'] = 'low'
    assert normalized[0].params == {'quality': 'high'}


@pytest.mark.parametrize(
    'profiles',
    [
        [profile(id='default')],
        [profile(id='same'), profile(id='same', name='Other')],
        [profile(name='Same'), profile(id='other', name=' same ')],
        [profile(name='   ')],
    ],
)
def test_profiles_reject_reserved_duplicate_or_blank_identity(profiles):
    engines = load_engines_module()

    with pytest.raises((ValueError, ValidationError)):
        engines.normalize_engine_profiles(profiles)


@pytest.mark.parametrize(
    'change',
    [
        {'engine': 'automatic1111'},
        {'params': []},
        {'engine': 'comfyui', 'comfyui_workflow': 'not-json'},
        {'engine': 'comfyui', 'comfyui_workflow': '[]'},
        {'gemini_endpoint_method': 'unsupported'},
        {'size': 'badxvalue'},
        {'size': '0x1024'},
        {'model': ''},
        {'base_url': ''},
        {'engine': 'gemini', 'model': '', 'base_url': 'https://gemini.example.test'},
        {'engine': 'gemini', 'model': 'gemini-image', 'base_url': ''},
        {'engine': 'comfyui', 'base_url': ''},
    ],
)
def test_profiles_reject_unsupported_or_malformed_values(change):
    engines = load_engines_module()

    with pytest.raises((ValueError, ValidationError)):
        engines.normalize_engine_profiles([profile(**change)])


def test_resolution_is_request_local_and_unknown_ids_fall_back_to_default():
    engines = load_engines_module()
    default = default_config()
    profiles = engines.normalize_engine_profiles(
        [
            profile(id='first', name='First', model='model-one', api_key='key-one'),
            profile(
                id='second',
                name='Second',
                engine='gemini',
                model='model-two',
                api_key='key-two',
                params={'generationConfig': {'temperature': 0.3}},
            ),
        ]
    )

    first = engines.resolve_image_generation_config(default, profiles, 'first')
    second = engines.resolve_image_generation_config(default, profiles, 'second')
    missing = engines.resolve_image_generation_config(default, profiles, 'deleted')

    assert first.IMAGE_GENERATION_MODEL == 'model-one'
    assert first.IMAGES_OPENAI_API_KEY == 'key-one'
    assert second.IMAGE_GENERATION_ENGINE == 'gemini'
    assert second.IMAGE_GENERATION_MODEL == 'model-two'
    assert second.IMAGES_GEMINI_API_KEY == 'key-two'
    assert second.IMAGE_GENERATION_PARAMS == {'generationConfig': {'temperature': 0.3}}
    assert second.IMAGES_OPENAI_API_KEY == ''
    assert second.IMAGES_OPENAI_API_PARAMS == {}
    assert second.COMFYUI_API_KEY == ''
    assert first.IMAGES_GEMINI_API_KEY == ''
    assert missing is not default
    assert vars(missing) == vars(default)
    first.IMAGE_GENERATION_MODEL = 'mutated'
    assert default.IMAGE_GENERATION_MODEL == 'default-model'
    assert second.IMAGE_GENERATION_MODEL == 'model-two'


def test_deep_merge_preserves_nested_defaults_and_does_not_mutate_inputs():
    engines = load_engines_module()
    defaults = {'parameters': {'sampleCount': 2, 'outputOptions': {'mimeType': 'image/png'}}, 'keep': True}
    overrides = {'parameters': {'outputOptions': {'compressionQuality': 90}}}

    merged = engines.deep_merge(defaults, overrides)

    assert merged == {
        'parameters': {
            'sampleCount': 2,
            'outputOptions': {'mimeType': 'image/png', 'compressionQuality': 90},
        },
        'keep': True,
    }
    assert defaults['parameters']['outputOptions'] == {'mimeType': 'image/png'}


def test_metadata_redaction_removes_nested_credentials_without_mutating_payload():
    engines = load_engines_module()
    payload = {
        'model': 'image-model',
        'api_key': 'secret-one',
        'nested': {'Authorization': 'Bearer secret-two', 'safe': True},
        'items': [{'access_token': 'secret-three'}, {'value': 2}],
    }

    redacted = engines.redact_image_metadata(payload)

    assert redacted == {'model': 'image-model', 'nested': {'safe': True}, 'items': [{}, {'value': 2}]}
    assert payload['api_key'] == 'secret-one'


def test_openai_payload_allows_parameters_but_protects_runtime_routing():
    engines = load_engines_module()
    config = SimpleNamespace(
        IMAGE_SIZE='1024x1024',
        IMAGE_GENERATION_MODEL='gpt-image-1',
        IMAGE_GENERATION_PARAMS={
            'model': 'attacker-model',
            'prompt': 'attacker prompt',
            'quality': 'high',
            'output_format': 'png',
        },
    )
    form = SimpleNamespace(prompt='draw a fox', n=2, size=None)

    payload = engines.build_openai_payload(config, form, 'gpt-image-1', url_response=True)

    assert payload == {
        'model': 'gpt-image-1',
        'prompt': 'draw a fox',
        'n': 2,
        'size': '1024x1024',
        'quality': 'high',
        'output_format': 'png',
    }


def test_legacy_openai_parameters_keep_their_existing_override_behavior():
    engines = load_engines_module()
    config = SimpleNamespace(
        IMAGE_SIZE='512x512',
        IMAGES_OPENAI_API_PARAMS={'model': 'legacy-model', 'prompt': 'legacy prompt'},
    )
    form = SimpleNamespace(prompt='runtime prompt', n=1, size=None)

    payload = engines.build_openai_payload(config, form, 'configured-model', url_response=False)

    assert payload['model'] == 'legacy-model'
    assert payload['prompt'] == 'legacy prompt'


def test_grok_payload_uses_aspect_ratio_and_never_openai_size():
    engines = load_engines_module()
    config = SimpleNamespace(
        IMAGE_SIZE='1536x1024',
        IMAGE_GENERATION_PARAMS={
            'model': 'wrong',
            'prompt': 'wrong',
            'resolution': '2k',
            'response_format': 'url',
        },
    )
    form = SimpleNamespace(prompt='draw a launch', n=3, size=None)

    payload = engines.build_grok_payload(config, form, 'grok-imagine-image')

    assert payload == {
        'model': 'grok-imagine-image',
        'prompt': 'draw a launch',
        'n': 3,
        'aspect_ratio': '3:2',
        'resolution': '2k',
        'response_format': 'url',
    }
    assert 'size' not in payload


@pytest.mark.parametrize('method', ['predict', 'generateContent'])
def test_gemini_payload_deep_merges_parameters_and_protects_prompt(method):
    engines = load_engines_module()
    params = (
        {'parameters': {'outputOptions': {'compressionQuality': 80}, 'addWatermark': False}}
        if method == 'predict'
        else {'generationConfig': {'imageConfig': {'aspectRatio': '16:9'}, 'temperature': 0.2}}
    )
    params['contents' if method == 'generateContent' else 'instances'] = {'prompt': 'wrong'}
    config = SimpleNamespace(IMAGES_GEMINI_ENDPOINT_METHOD=method, IMAGE_GENERATION_PARAMS=params)
    form = SimpleNamespace(prompt='draw a lake', n=2)

    routed_model, payload = engines.build_gemini_request(config, form, 'imagen-test')

    assert routed_model == f'imagen-test:{method}'
    if method == 'predict':
        assert payload['instances'] == {'prompt': 'draw a lake'}
        assert payload['parameters'] == {
            'sampleCount': 2,
            'outputOptions': {'mimeType': 'image/png', 'compressionQuality': 80},
            'addWatermark': False,
        }
    else:
        assert payload['contents'] == [{'parts': [{'text': 'draw a lake'}]}]
        assert payload['generationConfig'] == {
            'imageConfig': {'aspectRatio': '16:9'},
            'temperature': 0.2,
        }


def test_comfyui_payload_accepts_form_options_but_protects_runtime_values():
    engines = load_engines_module()
    config = SimpleNamespace(
        IMAGE_GENERATION_PARAMS={
            'prompt': 'wrong',
            'width': 1,
            'seed': 1234,
            'unknown': 'ignored',
        },
        IMAGE_STEPS=25,
    )
    form = SimpleNamespace(prompt='draw a forest', n=2, steps=None, negative_prompt='fog')

    payload = engines.build_comfyui_payload(config, form, width=768, height=512)

    assert payload == {
        'prompt': 'draw a forest',
        'negative_prompt': 'fog',
        'width': 768,
        'height': 512,
        'n': 2,
        'steps': 25,
        'seed': 1234,
    }


def images_config_payload(router, state):
    return {
        field: state.values[storage_key]
        for field, storage_key in router.IMAGE_CONFIG_KEYS.items()
        if field != 'USER_PERMISSIONS'
    }


def test_admin_config_validates_profiles_and_old_clients_do_not_clear_them(image_router):
    router = image_router.module
    state = image_router.state
    assert router.IMAGE_CONFIG_KEYS['IMAGE_GENERATION_ENGINES'] == 'image_generation.engines'

    payload = images_config_payload(router, state)
    payload.pop('IMAGE_GENERATION_ENGINES')
    old_client_form = router.ImagesConfig(**payload)
    asyncio.run(router.update_config(SimpleNamespace(), old_client_form, SimpleNamespace(id='admin')))

    assert all('image_generation.engines' not in update for update in state.upserts)

    payload['IMAGE_GENERATION_ENGINES'] = [profile(engine='unsupported', api_key='must-not-leak')]
    invalid_form = router.ImagesConfig(**payload)
    prior_upserts = len(state.upserts)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(router.update_config(SimpleNamespace(), invalid_form, SimpleNamespace(id='admin')))
    assert exc.value.status_code == 400
    assert 'must-not-leak' not in str(exc.value.detail)
    assert len(state.upserts) == prior_upserts


def test_admin_config_roundtrips_tool_description_suffixes(image_router):
    router, state = image_router.module, image_router.state
    payload = images_config_payload(router, state)
    payload.update(
        IMAGE_GENERATION_TOOL_DESCRIPTION_SUFFIX='  Default generation\n第二行  ',
        IMAGE_EDIT_TOOL_DESCRIPTION_SUFFIX='  Default edit  ',
        IMAGE_GENERATION_ENGINES=[
            profile(
                tool_description_suffix='  Named generation  ',
                edit={'engine': 'grok', 'tool_description_suffix': 'Named edit'},
            )
        ],
    )
    response = asyncio.run(
        router.update_config(SimpleNamespace(), router.ImagesConfig(**payload), SimpleNamespace(id='admin'))
    )
    assert response.get('IMAGE_GENERATION_TOOL_DESCRIPTION_SUFFIX') == 'Default generation\n第二行'
    assert response.get('IMAGE_EDIT_TOOL_DESCRIPTION_SUFFIX') == 'Default edit'
    assert state.values['image_generation.tool_description_suffix'] == 'Default generation\n第二行'
    assert state.values['images.edit.tool_description_suffix'] == 'Default edit'
    readback = asyncio.run(router.get_config(SimpleNamespace(), SimpleNamespace(id='admin')))
    assert readback == response
    assert readback['IMAGE_GENERATION_ENGINES'][0]['tool_description_suffix'] == 'Named generation'
    assert readback['IMAGE_GENERATION_ENGINES'][0]['edit']['tool_description_suffix'] == 'Named edit'

    old_payload = images_config_payload(router, state)
    old_payload.pop('IMAGE_GENERATION_TOOL_DESCRIPTION_SUFFIX', None)
    old_payload.pop('IMAGE_EDIT_TOOL_DESCRIPTION_SUFFIX', None)
    asyncio.run(
        router.update_config(SimpleNamespace(), router.ImagesConfig(**old_payload), SimpleNamespace(id='admin'))
    )
    assert state.values['image_generation.tool_description_suffix'] == 'Default generation\n第二行'
    assert state.values['images.edit.tool_description_suffix'] == 'Default edit'


def test_admin_config_responses_canonicalize_minimal_profiles(image_router):
    router = image_router.module
    state = image_router.state
    state.values['image_generation.engines'] = [{'id': 'xai', 'name': 'XAI', 'engine': 'grok'}]

    response = asyncio.run(router.get_config(SimpleNamespace(), SimpleNamespace(id='admin')))

    expected_profiles = [
        {
            'id': 'xai',
            'name': 'XAI',
            'engine': 'grok',
            'model': 'grok-imagine-image',
            'tool_description_suffix': '',
            'base_url': 'https://api.x.ai/v1',
            'api_key': '',
            'api_version': '',
            'params': {},
            'size': '',
            'steps': None,
            'gemini_endpoint_method': 'generateContent',
            'comfyui_workflow': '',
            'comfyui_workflow_nodes': [],
            'edit': None,
        }
    ]
    assert response['IMAGE_GENERATION_ENGINES'] == expected_profiles

    update_form = router.ImagesConfig(**images_config_payload(router, state))
    update_response = asyncio.run(router.update_config(SimpleNamespace(), update_form, SimpleNamespace(id='admin')))
    assert update_response['IMAGE_GENERATION_ENGINES'] == expected_profiles


def test_admin_config_get_sanitizes_invalid_stored_profile_errors(image_router):
    router = image_router.module
    image_router.state.values['image_generation.engines'] = [
        {'id': 'bad', 'name': 'Bad', 'engine': 'unsupported', 'api_key': 'must-not-leak'}
    ]

    with pytest.raises(HTTPException) as exc:
        asyncio.run(router.get_config(SimpleNamespace(), SimpleNamespace(id='admin')))

    assert exc.value.status_code == 400
    assert 'must-not-leak' not in str(exc.value.detail)


def test_engine_list_is_permission_checked_disabled_and_redacted(image_router):
    router = image_router.module
    state = image_router.state
    state.values['image_generation.engines'] = [profile(api_key='must-not-leak', params={'token': 'also-secret'})]
    user = SimpleNamespace(id='user-1', role='user')

    assert asyncio.run(router.get_image_generation_engines(SimpleNamespace(), user)) == [
        {'id': 'studio', 'name': 'Studio'}
    ]

    state.permission = False
    with pytest.raises(HTTPException) as exc:
        asyncio.run(router.get_image_generation_engines(SimpleNamespace(), user))
    assert exc.value.status_code == 403

    state.values['image_generation.enable'] = False
    assert asyncio.run(router.get_image_generation_engines(SimpleNamespace(), user)) == []


@pytest.mark.parametrize(
    ('provider_result', 'expected_image_data', 'expect_auth'),
    [
        ({'data': [{'b64_json': 'aW1hZ2U='}]}, 'aW1hZ2U=', False),
        ({'data': [{'url': 'https://cdn.example.test/image.png'}]}, 'https://cdn.example.test/image.png', False),
        ({'data': [{'url': 'https://api.x.ai/output.png'}]}, 'https://api.x.ai/output.png', True),
    ],
)
def test_grok_profile_routes_request_and_never_forwards_provider_auth(
    image_router, monkeypatch, provider_result, expected_image_data, expect_auth
):
    router = image_router.module
    state = image_router.state
    state.values['image_generation.engines'] = [
        profile(
            id='xai',
            name='XAI',
            engine='grok',
            model='',
            base_url='',
            api_key='xai-secret',
            size='1536x1024',
            params={'resolution': '2k', 'response_format': 'url'},
        )
    ]
    state.response_json = provider_result
    fetched = []
    uploads = []

    async def get_image_data(data, headers=None, trusted_base_url=None):
        fetched.append((data, headers, trusted_base_url))
        return b'image', 'image/png'

    async def upload_image(request, image_data, content_type, metadata, user):
        uploads.append(metadata)
        return object(), {'id': 'file-1'}

    monkeypatch.setattr(router, 'get_image_data', get_image_data)
    monkeypatch.setattr(router, 'upload_image', upload_image)
    form = router.CreateImageForm(prompt='draw a rocket', n=2, engine_id='xai')

    result = asyncio.run(router.image_generations(SimpleNamespace(), form, user=SimpleNamespace(role='user')))

    assert result == [{'id': 'file-1'}]
    assert state.get_many_calls == 1
    assert state.posts[0]['url'] == 'https://api.x.ai/v1/images/generations'
    assert state.posts[0]['headers']['Authorization'] == 'Bearer xai-secret'
    assert state.posts[0]['json'] == {
        'model': 'grok-imagine-image',
        'prompt': 'draw a rocket',
        'n': 2,
        'aspect_ratio': '3:2',
        'resolution': '2k',
        'response_format': 'url',
    }
    expected_headers = {'Authorization': 'Bearer xai-secret'} if expect_auth else None
    assert fetched == [(expected_image_data, expected_headers, None)]
    assert all('xai-secret' not in repr(metadata) for metadata in uploads)


def test_gemini_and_comfyui_profiles_reach_their_transports(image_router, monkeypatch):
    router = image_router.module
    state = image_router.state
    state.values['image_generation.engines'] = [
        profile(
            id='google',
            name='Google',
            engine='gemini',
            model='imagen-test',
            base_url='https://gemini-profile.example.test/v1beta',
            api_key='google-secret',
            gemini_endpoint_method='predict',
            params={'parameters': {'addWatermark': False}},
        ),
        profile(
            id='comfy',
            name='Comfy',
            engine='comfyui',
            model='checkpoint.safetensors',
            base_url='https://comfy-profile.example.test',
            api_key='comfy-secret',
            params={'seed': 42},
            comfyui_workflow='{}',
            comfyui_workflow_nodes=[{'type': 'prompt', 'key': 'text', 'node_ids': ['6']}],
        ),
    ]
    monkeypatch.setattr(router, 'get_image_data', AsyncMock(return_value=(b'image', 'image/png')))
    monkeypatch.setattr(router, 'upload_image', AsyncMock(return_value=(object(), {'id': 'file-1'})))

    state.response_json = {'predictions': [{'bytesBase64Encoded': 'aW1hZ2U='}]}
    gemini_form = router.CreateImageForm(prompt='draw waves', engine_id='google')
    asyncio.run(router.image_generations(SimpleNamespace(), gemini_form, user=SimpleNamespace(role='user')))
    assert state.posts[-1]['url'] == 'https://gemini-profile.example.test/v1beta/models/imagen-test:predict'
    assert state.posts[-1]['headers']['x-goog-api-key'] == 'google-secret'
    assert state.posts[-1]['json']['parameters']['addWatermark'] is False

    comfy_form = router.CreateImageForm(prompt='draw trees', engine_id='comfy')
    asyncio.run(router.image_generations(SimpleNamespace(), comfy_form, user=SimpleNamespace(role='user')))
    model, comfy_payload, base_url, api_key = state.comfy_calls[-1]
    assert model == 'checkpoint.safetensors'
    assert comfy_payload.seed == 42
    assert comfy_payload.workflow.nodes[0].node_ids == ['6']
    assert (base_url, api_key) == ('https://comfy-profile.example.test', 'comfy-secret')


def test_openai_profile_and_legacy_default_use_their_own_settings(image_router, monkeypatch):
    router = image_router.module
    state = image_router.state
    state.values['image_generation.engines'] = [
        profile(
            id='openai-profile',
            name='OpenAI Profile',
            model='dall-e-profile',
            base_url='https://profile.example.test/v1',
            api_key='profile-secret',
            api_version='2026-01-01',
            params={'quality': 'hd'},
        )
    ]
    state.values['image_generation.openai.params'] = {'quality': 'standard'}
    monkeypatch.setattr(router, 'get_image_data', AsyncMock(return_value=(b'image', 'image/png')))
    monkeypatch.setattr(router, 'upload_image', AsyncMock(return_value=(object(), {'id': 'file-1'})))

    selected_form = router.CreateImageForm(prompt='selected', engine_id='openai-profile')
    asyncio.run(router.image_generations(SimpleNamespace(), selected_form, user=SimpleNamespace(role='user')))
    selected_call = state.posts[-1]
    assert selected_call['url'] == 'https://profile.example.test/v1/images/generations?api-version=2026-01-01'
    assert selected_call['headers']['Authorization'] == 'Bearer profile-secret'
    assert selected_call['json']['model'] == 'dall-e-profile'
    assert selected_call['json']['quality'] == 'hd'

    default_form = router.CreateImageForm(prompt='default')
    asyncio.run(router.image_generations(SimpleNamespace(), default_form, user=SimpleNamespace(role='user')))
    default_call = state.posts[-1]
    assert default_call['url'] == 'https://default.example.test/v1/images/generations'
    assert default_call['headers']['Authorization'] == 'Bearer default-key'
    assert default_call['json']['model'] == 'default-model'
    assert default_call['json']['quality'] == 'standard'


def multipart_fields(form):
    return {options['name']: value for options, _, value in form._fields}


def test_selected_pair_routes_generation_and_openai_edit_to_independent_credentials(image_router, monkeypatch):
    router = image_router.module
    state = image_router.state
    state.values['images.edit.enable'] = True
    state.values['images.edit.model'] = 'global-edit'
    state.values['images.edit.openai.api_key'] = 'global-edit-key'
    state.values['image_generation.engines'] = [
        profile(
            id='paired',
            name='Paired',
            api_key='generation-secret',
            edit={
                'engine': 'openai',
                'model': 'edit-model',
                'base_url': 'https://edit.example.test/v1',
                'api_key': 'edit-secret',
                'api_version': '2026-09-21',
                'params': {'quality': 'high', 'model': 'wrong-model', 'prompt': 'wrong-prompt'},
            },
        )
    ]
    uploads = []

    async def upload(request, image_data, content_type, metadata, user):
        uploads.append(metadata)
        return object(), {'id': 'file-1'}

    monkeypatch.setattr(router, 'upload_image', upload)
    monkeypatch.setattr(router, 'get_image_data', AsyncMock(return_value=(b'image', 'image/png')))
    source = 'data:image/png;base64,aW1hZ2U='

    asyncio.run(
        router.image_generations(
            SimpleNamespace(),
            router.CreateImageForm(prompt='generate', engine_id='paired'),
            user=SimpleNamespace(role='user'),
        )
    )
    generation_call = state.posts[-1]
    asyncio.run(
        router.image_edits(
            SimpleNamespace(),
            router.EditImageForm(prompt='edit', image=source, engine_id='paired'),
            user=SimpleNamespace(role='user'),
        )
    )
    edit_call = state.posts[-1]

    assert generation_call['headers']['Authorization'] == 'Bearer generation-secret'
    assert edit_call['url'] == 'https://edit.example.test/v1/images/edits?api-version=2026-09-21'
    assert edit_call['headers']['Authorization'] == 'Bearer edit-secret'
    fields = multipart_fields(edit_call['data'])
    assert fields['model'] == 'edit-model'
    assert fields['prompt'] == 'edit'
    assert fields['quality'] == 'high'
    assert 'image' in fields
    assert all('secret' not in repr(metadata) for metadata in uploads)


def test_old_alias_or_deleted_edit_id_keeps_global_editor(image_router, monkeypatch):
    router = image_router.module
    state = image_router.state
    state.values['images.edit.model'] = 'global-edit'
    state.values['images.edit.openai.api_base_url'] = 'https://global-edit.example.test/v1'
    state.values['images.edit.openai.api_key'] = 'global-edit-key'
    state.values['image_generation.engines'] = [profile(id='legacy')]
    monkeypatch.setattr(router, 'upload_image', AsyncMock(return_value=(object(), {'id': 'file-1'})))
    monkeypatch.setattr(router, 'get_image_data', AsyncMock(return_value=(b'image', 'image/png')))
    source = 'data:image/png;base64,aW1hZ2U='

    for engine_id in ('legacy', 'deleted', None):
        asyncio.run(
            router.image_edits(
                SimpleNamespace(),
                router.EditImageForm(prompt='edit', image=source, engine_id=engine_id),
                user=SimpleNamespace(role='user'),
            )
        )
        call = state.posts[-1]
        assert call['url'] == 'https://global-edit.example.test/v1/images/edits'
        assert call['headers']['Authorization'] == 'Bearer global-edit-key'
        assert multipart_fields(call['data'])['model'] == 'global-edit'


def test_gemini_edit_profile_deep_merges_params_without_replacing_images(image_router, monkeypatch):
    router = image_router.module
    state = image_router.state
    state.values['image_generation.engines'] = [
        profile(
            id='gemini-edit',
            edit={
                'engine': 'gemini',
                'model': 'gemini-edit-model',
                'base_url': 'https://gemini-edit.example.test/v1beta',
                'api_key': 'gemini-edit-secret',
                'params': {
                    'generationConfig': {'temperature': 0.4, 'imageConfig': {'aspectRatio': '16:9'}},
                    'contents': [{'parts': [{'text': 'wrong prompt'}]}],
                },
            },
        )
    ]
    state.response_json = {'candidates': [{'content': {'parts': [{'inlineData': {'data': 'aW1hZ2U='}}]}}]}
    upload = AsyncMock(return_value=(object(), {'id': 'file-1'}))
    monkeypatch.setattr(router, 'upload_image', upload)
    monkeypatch.setattr(router, 'get_image_data', AsyncMock(return_value=(b'image', 'image/png')))
    source = 'data:image/jpeg;base64,aW1hZ2U='

    asyncio.run(
        router.image_edits(
            SimpleNamespace(),
            router.EditImageForm(prompt='edit lake', image=[source, source], engine_id='gemini-edit'),
            user=SimpleNamespace(role='user'),
        )
    )

    call = state.posts[-1]
    assert call['url'] == 'https://gemini-edit.example.test/v1beta/models/gemini-edit-model:generateContent'
    assert call['headers']['x-goog-api-key'] == 'gemini-edit-secret'
    assert call['json']['generationConfig']['imageConfig']['aspectRatio'] == '16:9'
    parts = call['json']['contents'][0]['parts']
    assert parts[0] == {'text': 'edit lake'}
    assert len(parts) == 3
    assert all(part['inline_data']['data'] == 'aW1hZ2U=' for part in parts[1:])
    assert 'gemini-edit-secret' not in repr(upload.await_args.args[3])


def test_default_gemini_editor_keeps_legacy_png_inline_mime():
    engines = load_engines_module()
    config = SimpleNamespace()
    source = 'data:image/jpeg;base64,aW1hZ2U='
    form = SimpleNamespace(prompt='edit', image=source)

    payload = engines.build_gemini_edit_payload(config, form)

    assert payload['contents'][0]['parts'][1]['inline_data']['mime_type'] == 'image/png'


def test_comfyui_edit_profile_uses_own_workflow_key_and_options(image_router, monkeypatch):
    router = image_router.module
    state = image_router.state
    state.values['image_generation.engines'] = [
        profile(
            id='comfy-edit',
            edit={
                'engine': 'comfyui',
                'model': 'checkpoint-edit',
                'base_url': 'https://comfy-edit.example.test',
                'api_key': 'comfy-edit-secret',
                'size': '640x480',
                'steps': 25,
                'params': {'seed': 1234},
                'comfyui_workflow': '{"6":{"inputs":{"text":"original"}}}',
                'comfyui_workflow_nodes': [{'type': 'prompt', 'key': 'text', 'node_ids': ['6']}],
            },
        )
    ]
    upload_source = AsyncMock(return_value={'name': 'source.png'})
    edit_transport = AsyncMock(return_value={'data': [{'url': 'https://comfy-edit.example.test/view?type=output'}]})
    image_fetch = AsyncMock(return_value=(b'image', 'image/png'))
    monkeypatch.setattr(router, 'comfyui_upload_image', upload_source)
    monkeypatch.setattr(router, 'comfyui_edit_image', edit_transport)
    monkeypatch.setattr(router, 'get_image_data', image_fetch)
    monkeypatch.setattr(router, 'upload_image', AsyncMock(return_value=(object(), {'id': 'file-1'})))

    asyncio.run(
        router.image_edits(
            SimpleNamespace(),
            router.EditImageForm(prompt='edit forest', image='data:image/png;base64,aW1hZ2U=', engine_id='comfy-edit'),
            user=SimpleNamespace(role='user'),
        )
    )

    assert upload_source.await_args.args[1:] == ('https://comfy-edit.example.test', 'comfy-edit-secret')
    assert edit_transport.await_args.args[0] == 'checkpoint-edit'
    payload = edit_transport.await_args.args[1]
    assert payload.seed == 1234
    assert payload.steps == 25
    assert payload.width == 640 and payload.height == 480
    assert payload.workflow.nodes[0].node_ids == ['6']
    assert edit_transport.await_args.args[3:] == ('https://comfy-edit.example.test', 'comfy-edit-secret')
    assert image_fetch.await_args.args[1] == {'Authorization': 'Bearer comfy-edit-secret'}


@pytest.mark.parametrize('multiple', [False, True])
def test_grok_edit_json_uses_image_or_images_and_keeps_auth_on_origin(image_router, monkeypatch, multiple):
    router = image_router.module
    state = image_router.state
    state.values['image_generation.engines'] = [
        profile(
            id='grok-edit',
            edit={
                'engine': 'grok',
                'model': 'grok-imagine-image',
                'base_url': 'https://api.x.ai/v1',
                'api_key': 'grok-edit-secret',
                'size': '1536x1024',
                'params': {'response_format': 'url', 'resolution': '2k', 'model': 'wrong-model'},
            },
        )
    ]
    state.response_json = {'data': [{'url': 'https://outside.example.test/output.png'}]}
    image_fetch = AsyncMock(return_value=(b'image', 'image/png'))
    upload = AsyncMock(return_value=(object(), {'id': 'file-1'}))
    monkeypatch.setattr(router, 'get_image_data', image_fetch)
    monkeypatch.setattr(router, 'upload_image', upload)
    source = 'data:image/png;base64,aW1hZ2U='
    form = router.EditImageForm(
        prompt='paint mountains', image=[source, source] if multiple else source, engine_id='grok-edit'
    )

    asyncio.run(router.image_edits(SimpleNamespace(), form, user=SimpleNamespace(role='user')))

    call = state.posts[-1]
    assert call['url'] == 'https://api.x.ai/v1/images/edits'
    assert call['headers']['Authorization'] == 'Bearer grok-edit-secret'
    assert call['json']['model'] == 'grok-imagine-image'
    assert call['json']['prompt'] == 'paint mountains'
    assert call['json']['aspect_ratio'] == '3:2'
    assert call['json']['resolution'] == '2k'
    assert 'size' not in call['json']
    if multiple:
        assert call['json']['images'] == [{'url': source, 'type': 'image_url'}] * 2
        assert 'image' not in call['json']
    else:
        assert call['json']['image'] == {'url': source, 'type': 'image_url'}
        assert 'images' not in call['json']
    assert image_fetch.await_args.args[1] is None
    assert 'grok-edit-secret' not in repr(upload.await_args.args[3])


def test_grok_edit_rejects_more_than_five_sources_before_provider_post(image_router):
    router = image_router.module
    state = image_router.state
    state.values['image_generation.engines'] = [
        profile(
            id='grok-edit',
            edit={
                'engine': 'grok',
                'model': 'grok-imagine-image',
                'base_url': 'https://api.x.ai/v1',
                'api_key': 'grok-edit-secret',
            },
        )
    ]
    source = 'data:image/png;base64,aW1hZ2U='
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            router.image_edits(
                SimpleNamespace(),
                router.EditImageForm(prompt='edit', image=[source] * 6, engine_id='grok-edit'),
                user=SimpleNamespace(role='user'),
            )
        )
    assert exc.value.status_code == 400
    assert not state.posts


def test_grok_edit_ratio_override_skips_configured_dimensions(image_router, monkeypatch):
    router = image_router.module
    state = image_router.state
    state.values['image_generation.engines'] = [
        profile(
            id='grok-edit',
            edit={
                'engine': 'grok',
                'model': 'grok-imagine-image',
                'base_url': 'https://api.x.ai/v1',
                'api_key': 'grok-edit-secret',
                'size': '1536x1024',
            },
        )
    ]
    monkeypatch.setattr(router, 'get_image_data', AsyncMock(return_value=(b'image', 'image/png')))
    monkeypatch.setattr(router, 'upload_image', AsyncMock(return_value=(object(), {'id': 'file-1'})))

    asyncio.run(
        router.image_edits(
            SimpleNamespace(),
            router.EditImageForm(
                prompt='edit',
                image='data:image/png;base64,aW1hZ2U=',
                size='16:9',
                engine_id='grok-edit',
            ),
            user=SimpleNamespace(role='user'),
        )
    )

    assert state.posts[-1]['json']['aspect_ratio'] == '16:9'


def test_grok_edit_singleton_list_uses_image_field(image_router, monkeypatch):
    router = image_router.module
    state = image_router.state
    state.values['image_generation.engines'] = [
        profile(
            id='grok-edit',
            edit={
                'engine': 'grok',
                'model': 'grok-imagine-image',
                'base_url': 'https://api.x.ai/v1',
                'api_key': 'grok-edit-secret',
            },
        )
    ]
    monkeypatch.setattr(router, 'get_image_data', AsyncMock(return_value=(b'image', 'image/png')))
    monkeypatch.setattr(router, 'upload_image', AsyncMock(return_value=(object(), {'id': 'file-1'})))
    source = 'data:image/png;base64,aW1hZ2U='

    asyncio.run(
        router.image_edits(
            SimpleNamespace(),
            router.EditImageForm(prompt='edit', image=[source], engine_id='grok-edit'),
            user=SimpleNamespace(role='user'),
        )
    )

    assert state.posts[-1]['json']['image'] == {'url': source, 'type': 'image_url'}
    assert 'images' not in state.posts[-1]['json']
