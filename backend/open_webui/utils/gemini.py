"""Manual Gemini compatibility at OpenAI request and tool execution boundaries."""

import logging

from fastapi import HTTPException

from open_webui.utils.gemini_schema import GeminiSchemaError, compile_gemini_schema

log = logging.getLogger(__name__)


def _model_field(value, name, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


async def resolve_tool_schema_compatibility(model, *, get_model=None) -> str:
    """Use saved metadata or a direct descriptor; never infer from provider IDs."""
    seen = set()
    while model is not None:
        info = _model_field(model, 'info')
        info = model if info is None else info
        mode = _model_field(_model_field(info, 'meta'), 'tool_schema_compatibility')
        if mode is not None:
            return 'gemini' if mode == 'gemini' else 'default'
        # A browser descriptor's IDs/base pointers cannot authorize a server
        # model lookup. Its explicit metadata is the entire configuration.
        if _model_field(model, 'direct', False) or get_model is None:
            break
        base_id = _model_field(info, 'base_model_id')
        if not isinstance(base_id, str) or not base_id or base_id in seen:
            break
        seen.add(base_id)
        model = await get_model(base_id)
    return 'default'


async def configure_gemini_tool_validation(metadata, model, *, get_model=None):
    # Always overwrite request metadata. A client value must not disable
    # validation for a server-selected Gemini model or affect another model.
    compatibility = (
        await resolve_tool_schema_compatibility(model, get_model=get_model)
        if metadata.get('params', {}).get('function_calling') != 'legacy'
        else 'default'
    )
    # This request-owned map survives the shallow metadata copies used by
    # routing. Keep separate model entries so internal summary/task sends cannot
    # overwrite the main model's execution contract.
    metadata['_tool_schema_validation'] = {}
    record_gemini_tool_compatibility(metadata, compatibility, _model_field(model, 'id', ''))


def record_gemini_tool_compatibility(metadata, compatibility, model_id):
    if not isinstance(metadata, dict):
        return
    mode = 'default' if metadata.get('params', {}).get('function_calling') == 'legacy' else compatibility
    metadata['_tool_schema_compatibility'] = mode
    state = metadata.get('_tool_schema_validation')
    if isinstance(state, dict):
        state[model_id] = mode


def mark_gemini_tool_calls(tool_calls, output, metadata, model_id):
    """Freeze the generating round's validation requirement, including approvals."""
    mode = metadata.get('_tool_schema_validation', {}).get(model_id, metadata.get('_tool_schema_compatibility'))
    call_ids = set()
    for call in tool_calls:
        # Record both modes: the outer execution loop can still hold a model
        # from an earlier round, including the reverse Gemini -> Default route.
        call['_gemini_schema_validation'] = mode == 'gemini'
        call_ids.add(call.get('id'))
    for item in output:
        if item.get('type') == 'function_call' and (item.get('call_id') or item.get('id')) in call_ids:
            item['tool_schema_compatibility'] = 'gemini' if mode == 'gemini' else 'default'


def normalize_gemini_tool_schemas(payload: dict, *, compatibility: str | None = None) -> dict:
    """Compile copies; preserve cached specs and all non-schema transport data."""
    if compatibility != 'gemini' or not isinstance(payload.get('tools'), list):
        return payload

    tools = []
    for tool in payload['tools']:
        if not isinstance(tool, dict) or tool.get('type') != 'function':
            tools.append(tool)
            continue
        # Chat Completions wraps the declaration; Responses puts it on the tool.
        function = tool.get('function', tool)
        if not isinstance(function, dict) or 'parameters' not in function:
            tools.append(tool)
            continue
        try:
            compiled = compile_gemini_schema(function['parameters'])
        except GeminiSchemaError as exc:
            # Never include schema values, remote URLs, defaults or examples in
            # logs/errors; a tool name and JSON pointer suffice to locate it.
            raise HTTPException(
                status_code=400,
                detail=f'Gemini tool schema for {function.get("name", "<unnamed>")}: {exc}. '
                'Simplify this definition or use a gateway with native JSON Schema support and Default compatibility.',
            ) from None
        if compiled.diagnostics:
            log.warning(
                'Gemini tool %s: %d constraints require original-schema validation; first paths: %s',
                function.get('name', '<unnamed>'),
                len(compiled.diagnostics),
                ', '.join(item.path for item in compiled.diagnostics[:4]),
            )
        function = {**function, 'parameters': compiled.parameters}
        tools.append({**tool, 'function': function} if 'function' in tool else function)
    return {**payload, 'tools': tools}
