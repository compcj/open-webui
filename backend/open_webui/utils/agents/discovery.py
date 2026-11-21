"""Models exposed by the selected Open Terminal environment."""

from urllib.parse import urlencode

from open_webui.utils.agents.session import legacy_model_id, model_id
from open_webui.utils.terminals import MissingTerminalContext, get_terminal_json


def resolve_catalog_model(catalog, requested_id):
    """Resolve both generations of IDs only when the current actor's live catalog proves one target."""
    matches = {}
    for model in catalog:
        agent = model.get('agent') or {}
        if all(isinstance(agent.get(k), str) and agent[k] for k in ('terminal_id', 'provider', 'model')):
            if requested_id == model.get('id') or (model.get('legacy_id', legacy_model_id(agent)) == requested_id):
                identity = (agent['terminal_id'], agent['provider'], agent['model'])
                matches[identity] = model
    if len(matches) == 1:
        return next(iter(matches.values()))
    return None


def model_controls(model):
    controls = {}
    for key, label, values, value_key, label_key, default_key in (
        (
            'reasoning_effort',
            'Reasoning effort',
            'supportedReasoningEfforts',
            'reasoningEffort',
            'label',
            'defaultReasoningEffort',
        ),
        ('service_tier', 'Service tier', 'serviceTiers', 'id', 'name', 'defaultServiceTier'),
    ):
        options = {
            option[value_key]: {
                'label': option.get(label_key) or option[value_key],
                'params': {key: option[value_key]},
            }
            for option in model.get(values, [])
        }
        if options:
            controls[key] = {'label': label, 'options': options}
            if model.get(default_key) in options:
                controls[key]['default'] = model[default_key]
    return controls


async def get_models(request, user, metadata, *, provider=None, cwd=None):
    terminal_id = metadata.get('terminal_id')
    if not terminal_id:
        return []
    try:
        if not cwd:
            directory = await get_terminal_json(request, user, metadata, '/files/cwd')
            cwd = (directory or {}).get('cwd')
        params = {'provider': provider} if provider else {}
        if cwd:
            params['cwd'] = cwd
        path = '/agents' + ('?' + urlencode(params) if params else '')
        catalog = await get_terminal_json(request, user, metadata, path)
    except MissingTerminalContext:
        return []
    if not catalog or catalog.get('protocol_version') != 1:
        return []
    models = [
        {
            'id': model_id({'terminal_id': terminal_id, 'provider': agent['id'], 'model': model['id']}),
            'name': f'{agent["id"]}/{model["id"]}',
            'owned_by': 'terminal',
            'object': 'model',
            'agent': {'terminal_id': terminal_id, 'provider': agent['id'], 'model': model['id']},
            'info': {
                'params': {'model_controls': model_controls(model)},
                'meta': {
                    'capabilities': {
                        'vision': True,
                        'file_upload': True,
                        'web_search': False,
                        'image_generation': False,
                        'video_generation': False,
                        'code_interpreter': False,
                        'builtin_tools': False,
                    }
                },
            },
        }
        for agent in catalog.get('agents', [])
        if agent.get('available')
        for model in agent.get('models', [])
    ]
    # Canonical and legacy IDs may collide even though the raw server tuples differ.
    # Never select a model by collection or enumeration order.
    identities = {}
    for item in models:
        agent = item['agent']
        identity = (agent['terminal_id'], agent['provider'], agent['model'])
        for candidate in (item['id'], legacy_model_id(agent)):
            identities.setdefault(candidate, set()).add(identity)
    result = {}
    for item in models:
        if len(identities[item['id']]) != 1:
            continue
        alias = legacy_model_id(item['agent'])
        result[item['id']] = {**item, 'legacy_id': alias if len(identities[alias]) == 1 else None}
    return list(result.values())
