"""Compatibility for Gemini tools sent through OpenAI-compatible connections."""

import json
from copy import deepcopy


def _matches_json_type(value, schema_type):
    if isinstance(schema_type, list):
        return any(_matches_json_type(value, item) for item in schema_type)
    if schema_type == 'string':
        return isinstance(value, str)
    if schema_type == 'boolean':
        return isinstance(value, bool)
    if schema_type == 'null':
        return value is None
    if schema_type == 'integer':
        return type(value) is int or (type(value) is float and value.is_integer())
    if schema_type == 'number':
        return type(value) in (int, float)
    if schema_type == 'array':
        return isinstance(value, list)
    if schema_type == 'object':
        return isinstance(value, dict)
    # Unknown/missing types cannot prove that an enum value is impossible.
    return True


def _schema_children(schema):
    # Visit schema positions only: defaults/examples and property names are data.
    for key in ('properties', 'patternProperties', '$defs', 'definitions', 'dependentSchemas', 'dependencies'):
        if isinstance(schema.get(key), dict):
            yield from schema[key].values()
    for key in (
        'items',
        'additionalItems',
        'additionalProperties',
        'unevaluatedItems',
        'unevaluatedProperties',
        'contains',
        'propertyNames',
        'contentSchema',
        'not',
        'if',
        'then',
        'else',
    ):
        child = schema.get(key)
        if isinstance(child, list):
            yield from child
        else:
            yield child
    for key in ('anyOf', 'oneOf', 'allOf', 'prefixItems'):
        if isinstance(schema.get(key), list):
            yield from schema[key]


def _has_schema_references(schema):
    if not isinstance(schema, dict):
        return False
    if schema.keys() & {
        '$ref',
        '$dynamicRef',
        '$recursiveRef',
        '$id',
        'id',
        '$anchor',
        '$dynamicAnchor',
        '$recursiveAnchor',
    }:
        return True
    return any(_has_schema_references(child) for child in _schema_children(schema))


def _normalize_schema(schema):
    if not isinstance(schema, dict):
        return

    # Native Gemini Schema has no plural examples field. Visit schema nodes
    # only, so properties with this name and literal default/example data survive.
    schema.pop('examples', None)
    # JSON Schema ignores string patterns on other types; native Gemini
    # restricts pattern to strings. Keep the constraint on string alternatives.
    if schema.get('type') in ('integer', 'number', 'boolean', 'array', 'object', 'null'):
        schema.pop('pattern', None)

    for child in _schema_children(schema):
        _normalize_schema(child)

    variants = schema.get('anyOf')
    if not isinstance(variants, list) or not variants or not all(isinstance(item, dict) for item in variants):
        return
    siblings = {key: value for key, value in schema.items() if key != 'anyOf'}
    if not siblings:
        return

    # These keywords are independent of sibling schema locations. Moving refs,
    # definitions or object/tuple applicators could change their scope/meaning.
    annotations = {'title', 'description', 'default', 'deprecated', 'readOnly', 'writeOnly'}
    independent = {
        'type',
        'enum',
        'const',
        'format',
        'pattern',
        'minLength',
        'maxLength',
        'minimum',
        'maximum',
        'exclusiveMinimum',
        'exclusiveMaximum',
        'multipleOf',
        'minItems',
        'maxItems',
        'uniqueItems',
        'minProperties',
        'maxProperties',
        'required',
    }
    if siblings.keys() - annotations - independent:
        return

    merged = []
    for variant in variants:
        constant = siblings.get('const', variant.get('const'))
        if isinstance(constant, str):
            # A string literal can prove an intersection impossible without
            # dropping valid alternatives or treating bool/number as strings.
            if any(
                ('const' in constraints and constraints['const'] != constant)
                or not _matches_json_type(constant, constraints.get('type'))
                or (isinstance(constraints.get('enum'), list) and constant not in constraints['enum'])
                for constraints in (siblings, variant)
            ):
                continue

        # Never silently replace a constraint with a different branch constraint.
        # Unsupported intersections retain the original schema for provider validation.
        if any(
            key not in annotations and json.dumps(value, sort_keys=True) != json.dumps(variant[key], sort_keys=True)
            for key, value in siblings.items()
            if key in variant
            and not (
                key == 'enum'
                and isinstance(constant, str)
                and isinstance(value, list)
                and isinstance(variant[key], list)
            )
        ):
            return
        branch = {**deepcopy(siblings), **variant}
        if isinstance(branch.get('enum'), list):
            # A finite enum can make a branch impossible, e.g. integer with
            # enum=['1min', '5min']. Keep precisely the values its type accepts.
            branch['enum'] = [
                value
                for value in branch['enum']
                if _matches_json_type(value, branch.get('type')) or (value is None and branch.get('nullable') is True)
            ]
            if not branch['enum']:
                continue
        if siblings.get('description') and variant.get('description'):
            if siblings['description'] != variant['description']:
                branch['description'] = f'{siblings["description"]}\n{variant["description"]}'
        _normalize_schema(branch)
        merged.append(branch)

    if not merged:
        return

    # Gemini requires anyOf to be the only field at its schema node. Distribute
    # the shared annotations/constraints instead of dropping them or choosing a type.
    schema.clear()
    schema['anyOf'] = merged


def _normalize_string_constants(schema):
    if not isinstance(schema, dict):
        return

    # Gemini supports string enums, but not JSON Schema const. Keep the exact
    # constraint; incompatible types/enums and non-string literals must not be
    # silently widened or coerced to strings.
    constant = schema.get('const')
    if (
        'anyOf' not in schema
        and isinstance(constant, str)
        and ('type' not in schema or schema['type'] == 'string')
        and ('enum' not in schema or (isinstance(schema['enum'], list) and constant in schema['enum']))
    ):
        schema['enum'] = [schema.pop('const')]
        schema.setdefault('type', 'string')

    for child in _schema_children(schema):
        _normalize_string_constants(child)


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


def normalize_gemini_tool_schemas(payload: dict, *, compatibility: str | None = None) -> dict:
    """Copy Gemini function parameters, keeping cached/shared tool specs intact."""
    if compatibility != 'gemini':
        return payload
    if not isinstance(payload.get('tools'), list):
        return payload

    tools = []
    for tool in payload['tools']:
        if not isinstance(tool, dict) or tool.get('type') != 'function':
            tools.append(tool)
            continue
        # Chat Completions wraps the declaration; Responses puts it on the tool.
        function = tool.get('function', tool)
        if not isinstance(function, dict) or not isinstance(function.get('parameters'), dict):
            tools.append(tool)
            continue
        # Moving constraints or pruning alternatives changes the meaning of
        # pointers into those branches. Leave reference-bearing documents intact.
        if _has_schema_references(function['parameters']):
            tools.append(tool)
            continue
        parameters = deepcopy(function['parameters'])
        # Distribute original constraints before const introduces type/enum
        # fields that would otherwise look like conflicting union constraints.
        _normalize_schema(parameters)
        _normalize_string_constants(parameters)
        function = {**function, 'parameters': parameters}
        tools.append({**tool, 'function': function} if 'function' in tool else function)
    return {**payload, 'tools': tools}
