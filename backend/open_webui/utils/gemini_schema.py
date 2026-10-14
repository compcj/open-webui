"""Bounded JSON Schema projection for gateways using Google's legacy Schema.

The projection guides generation; it is not an argument validator. Constraints
which the target cannot express are described and reported, and WebUI executes
tools only after validating against the resolved original schema.
"""

import json
import math
from copy import deepcopy
from dataclasses import dataclass
from urllib.parse import unquote

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from referencing import Registry

MAX_DEPTH = 48
MAX_JSON_DEPTH = 4 * MAX_DEPTH
MAX_NODES = 8192
MAX_ALTERNATIVES = 128
MAX_SCHEMA_BYTES = 1_048_576
MAX_WORK_BYTES = 8 * MAX_SCHEMA_BYTES
TYPES = {'string', 'integer', 'number', 'boolean', 'object', 'array', 'null'}
ANNOTATIONS = {'title', 'description', 'default', 'example'}
IGNORED = {'$schema', '$id', '$anchor', '$comment', 'examples', 'deprecated', 'readOnly', 'writeOnly'}
TYPE_FIELDS = {
    'string': {'minLength', 'maxLength', 'pattern'},
    'integer': {'minimum', 'maximum'},
    'number': {'minimum', 'maximum'},
    'array': {'minItems', 'maxItems'},
    'object': {'minProperties', 'maxProperties'},
}
FORMATS = {
    'string': {'date-time', 'date', 'time', 'duration', 'byte', 'enum'},
    'integer': {'int32', 'int64'},
    'number': {'float', 'double'},
}


def _pointer(path, part):
    return f'{path}/{str(part).replace("~", "~0").replace("/", "~1")}'


class GeminiSchemaError(ValueError):
    def __init__(self, path, reason):
        self.path = path
        self.reason = reason
        super().__init__(f'{path}: {reason}')


@dataclass(frozen=True)
class SchemaDiagnostic:
    path: str
    keyword: str


@dataclass(frozen=True)
class CompiledGeminiSchema:
    parameters: dict
    validation_schema: dict | bool
    diagnostics: tuple[SchemaDiagnostic, ...]


def _children(schema):
    """Only schema positions; property names and literal annotations are data."""
    if not isinstance(schema, dict):
        return
    for key in ('properties', 'patternProperties', '$defs', 'definitions', 'dependentSchemas'):
        if isinstance(schema.get(key), dict):
            for name, child in schema[key].items():
                yield (key, name), child
    if isinstance(schema.get('dependencies'), dict):
        for name, child in schema['dependencies'].items():
            if isinstance(child, (dict, bool)):
                yield ('dependencies', name), child
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
        if isinstance(schema.get(key), (dict, bool)):
            yield (key,), schema[key]
        elif key == 'items' and isinstance(schema.get(key), list):
            for index, child in enumerate(schema[key]):
                yield (key, index), child
    for key in ('anyOf', 'oneOf', 'allOf', 'prefixItems'):
        if isinstance(schema.get(key), list):
            for index, child in enumerate(schema[key]):
                yield (key, index), child


class _Resolver:
    def __init__(self, schema):
        self.root = schema
        self.nodes = 0
        self.work_bytes = 0
        self.anchors = {}
        try:
            size = len(json.dumps(schema, ensure_ascii=False, allow_nan=False).encode('utf-8'))
        except (ValueError, TypeError, RecursionError):
            raise GeminiSchemaError('#', 'schema must be finite JSON') from None
        if size > MAX_SCHEMA_BYTES:
            raise GeminiSchemaError('#', 'schema size limit exceeded')
        # Annotation and const values are data, not schema nodes, but still
        # reach deepcopy/validators. Bound their container depth before those
        # recursive operations without interpreting their keys as keywords.
        pending = [(schema, 0)]
        while pending:
            value, depth = pending.pop()
            if depth > MAX_JSON_DEPTH:
                raise GeminiSchemaError('#', 'JSON container depth limit exceeded')
            if isinstance(value, dict):
                pending.extend((item, depth + 1) for item in value.values())
            elif isinstance(value, list):
                pending.extend((item, depth + 1) for item in value)
        self._scan(schema, '#', 0)
        self.nodes = 0

    def _visit(self, path, depth):
        self.nodes += 1
        if depth > MAX_DEPTH:
            raise GeminiSchemaError(path, 'schema depth limit exceeded')
        if self.nodes > MAX_NODES:
            raise GeminiSchemaError(path, 'schema expansion limit exceeded')

    def _scan(self, schema, path, depth):
        self._visit(path, depth)
        if not isinstance(schema, dict):
            return
        if '_conjuncts' in schema:
            raise GeminiSchemaError(_pointer(path, '_conjuncts'), 'reserved compiler keyword')
        for key in ('$dynamicRef', '$recursiveRef', '$dynamicAnchor', '$recursiveAnchor'):
            if key in schema:
                raise GeminiSchemaError(
                    _pointer(path, key), 'dynamic references are unsupported by this gateway profile'
                )
        if path != '#' and ('$id' in schema or 'id' in schema):
            raise GeminiSchemaError(_pointer(path, '$id'), 'nested reference scopes are unsupported')
        if '$anchor' in schema:
            anchor = schema['$anchor']
            if not isinstance(anchor, str) or anchor in self.anchors:
                raise GeminiSchemaError(_pointer(path, '$anchor'), 'invalid or duplicate anchor')
            self.anchors[anchor] = (schema, path)
        for parts, child in _children(schema):
            child_path = path
            for part in parts:
                child_path = _pointer(child_path, part)
            self._scan(child, child_path, depth + 1)

    def _target(self, ref, path):
        if not isinstance(ref, str) or not ref.startswith('#'):
            raise GeminiSchemaError(path, 'only local schema references are supported; no external lookup is performed')
        fragment = unquote(ref[1:])
        if fragment and not fragment.startswith('/'):
            if fragment in self.anchors:
                return self.anchors[fragment]
            raise GeminiSchemaError(path, 'unresolved local anchor')
        target, target_path = self.root, '#'
        try:
            for token in fragment.split('/')[1:]:
                token = token.replace('~1', '/').replace('~0', '~')
                if isinstance(target, list):
                    if not token.isdecimal() or str(int(token)) != token:
                        raise KeyError(token)
                    target = target[int(token)]
                else:
                    target = target[token]
                target_path = _pointer(target_path, token)
        except (KeyError, IndexError, TypeError, ValueError):
            raise GeminiSchemaError(path, 'unresolved local schema reference') from None
        if not isinstance(target, (dict, bool)):
            raise GeminiSchemaError(path, 'reference does not identify a schema')
        return target, target_path

    def resolve(self, schema=None, path='#', depth=0, active=()):
        if schema is None:
            schema = self.root
        self._visit(path, depth)
        if isinstance(schema, bool):
            return schema
        if not isinstance(schema, dict):
            raise GeminiSchemaError(path, 'expected an object or boolean schema')
        self.work_bytes += len(json.dumps(schema, ensure_ascii=False).encode('utf-8'))
        if self.work_bytes > MAX_WORK_BYTES:
            raise GeminiSchemaError(path, 'schema expansion byte limit exceeded')
        if '$ref' in schema:
            target, target_path = self._target(schema['$ref'], _pointer(path, '$ref'))
            if target_path in active:
                raise GeminiSchemaError(_pointer(path, '$ref'), 'recursive schema references cannot be expanded')
            target = self.resolve(target, target_path, depth + 1, (*active, target_path))
            siblings = {k: v for k, v in schema.items() if k != '$ref'}
            # Draft 4-7 ignore validation siblings of $ref. Keep annotations as
            # model guidance, but do not unexpectedly tighten legacy schemas.
            if any(draft in str(self.root.get('$schema', '')) for draft in ('draft-04', 'draft-06', 'draft-07')):
                siblings = {k: v for k, v in siblings.items() if k in ANNOTATIONS}
            if not siblings:
                return target
            return {'allOf': [target, self.resolve(siblings, path, depth + 1, active)]}
        result = deepcopy(schema)
        for parts, child in _children(schema):
            if parts[0] in ('$defs', 'definitions'):
                continue
            dest = result
            child_path = path
            for part in parts[:-1]:
                dest = dest[part]
                child_path = _pointer(child_path, part)
            dest[parts[-1]] = self.resolve(child, _pointer(child_path, parts[-1]), depth + 1, active)
        for key in ('$defs', 'definitions', '$schema', '$id', '$anchor', 'id'):
            result.pop(key, None)
        # Upgrade legacy tuple/dependency syntax before using a single validator.
        if isinstance(result.get('items'), list):
            result['prefixItems'] = result.pop('items')
            result['items'] = result.pop('additionalItems', True)
        dependencies = result.pop('dependencies', {})
        if not isinstance(dependencies, dict):
            raise GeminiSchemaError(_pointer(path, 'dependencies'), 'expected an object')
        for name, dependency in dependencies.items():
            key = 'dependentRequired' if isinstance(dependency, list) else 'dependentSchemas'
            if key in result and not isinstance(result[key], dict):
                raise GeminiSchemaError(_pointer(path, key), 'expected an object')
            result.setdefault(key, {})[name] = dependency
        for key, bound in (('exclusiveMinimum', 'minimum'), ('exclusiveMaximum', 'maximum')):
            if type(result.get(key)) is bool:
                enabled = result.pop(key)
                if enabled and bound in result:
                    result[key] = result.pop(bound)
        if result.pop('nullable', False) is True:
            return {'anyOf': [result, {'type': 'null'}]}
        return result


def resolve_gemini_schema(schema):
    """Return a standalone schema for local validation; never resolve a URL."""
    resolved = _Resolver(schema).resolve()
    if len(json.dumps(resolved, ensure_ascii=False).encode('utf-8')) > MAX_SCHEMA_BYTES:
        raise GeminiSchemaError('#', 'resolved schema byte limit exceeded')
    try:
        Draft202012Validator.check_schema(resolved)
    except SchemaError as exc:
        path = '#'
        for part in exc.absolute_path:
            path = _pointer(path, part)
        raise GeminiSchemaError(path, 'invalid JSON Schema keyword value') from None
    return resolved


def _validator(schema):
    return Draft202012Validator(schema, registry=Registry())


def _json_type(value):
    if value is None:
        return 'null'
    if isinstance(value, bool):
        return 'boolean'
    if isinstance(value, str):
        return 'string'
    if isinstance(value, dict):
        return 'object'
    if isinstance(value, list):
        return 'array'
    return 'integer' if value == int(value) else 'number'


class _Compiler:
    def __init__(self):
        self.nodes = 0
        self.diagnostics = []
        self.work_bytes = 0

    def _charge(self, value):
        self.work_bytes += len(json.dumps(value, ensure_ascii=False).encode('utf-8'))
        if self.work_bytes > MAX_WORK_BYTES:
            raise GeminiSchemaError('#', 'schema projection byte limit exceeded')

    def _hint(self, result, key, value, path):
        self.diagnostics.append(SchemaDiagnostic(_pointer(path, key), key))
        hint = f'JSON Schema constraint: {json.dumps({key: value}, ensure_ascii=False, separators=(",", ":"))}.'
        description = result.get('description', '')
        if hint not in description:
            result['description'] = f'{description}\n{hint}'.strip()

    def _intersect(self, left, right):
        self._charge([left, right])
        if left is False or right is False:
            return False
        if left is True:
            return right
        if right is True:
            return left
        result = deepcopy(left)
        # Tuple tails start after their own prefix length. Combining two items
        # schemas at one scope can reject valid inputs. Project one complete
        # tuple and retain the other conjunct as guidance + local validation.
        tuple_scope = (
            ('prefixItems' in left or 'prefixItems' in right)
            and ('items' in left or 'prefixItems' in left)
            and ('items' in right or 'prefixItems' in right)
        )
        if tuple_scope:
            result.setdefault('_conjuncts', []).append(
                {'allOf': [{k: v for k, v in right.items() if k in ('prefixItems', 'items')}]}
            )
        for key, value in right.items():
            if tuple_scope and key in ('items', 'prefixItems'):
                continue
            if key not in result or json.dumps(result[key], sort_keys=True) == json.dumps(value, sort_keys=True):
                result[key] = deepcopy(value)
            elif key == 'description':
                result[key] += '\n' + value
            elif key in ANNOTATIONS:
                continue
            elif key == 'type':
                a = result[key] if isinstance(result[key], list) else [result[key]]
                b = value if isinstance(value, list) else [value]
                types = [t for t in a if t in b or (t == 'integer' and 'number' in b)]
                if 'number' in a and 'integer' in b and 'integer' not in types:
                    types.append('integer')
                if not types:
                    return False
                result[key] = types[0] if len(types) == 1 else types
            elif key == 'properties':
                for name, child in value.items():
                    result[key][name] = {'allOf': [result[key][name], child]} if name in result[key] else child
            elif key == 'required':
                result[key] = list(dict.fromkeys(result[key] + value))
            elif key == '_conjuncts':
                result[key].extend(value)
            elif key in ('minimum', 'minLength', 'minItems', 'minProperties', 'exclusiveMinimum'):
                result[key] = max(result[key], value)
            elif key in ('maximum', 'maxLength', 'maxItems', 'maxProperties', 'exclusiveMaximum'):
                result[key] = min(result[key], value)
            elif key == 'enum':
                if all(isinstance(v, str) for v in [*result[key], *value]):
                    members = set(value)
                    result[key] = [v for v in result[key] if v in members]
                else:
                    # Complex equality is type-sensitive (True != 1). Bound
                    # comparisons before invoking the JSON Schema validator;
                    # bytes alone do not bound a quadratic enum intersection.
                    if len(result[key]) * len(value) > MAX_NODES:
                        raise GeminiSchemaError('#/enum', 'enum intersection work limit exceeded')
                    validator = _validator({'enum': value})
                    result[key] = [v for v in result[key] if validator.is_valid(v)]
                if not result[key]:
                    return False
            elif key == 'const':
                if not _validator({'const': value}).is_valid(result[key]):
                    return False
            elif key == 'items':
                result[key] = {'allOf': [result[key], value]}
            else:
                # Keep the conjunct for execution and model guidance. Choosing
                # either side would erase constraints (e.g. two regex patterns).
                result.setdefault('_conjuncts', []).append({key: value})
        return result

    def _alternatives(self, schema, path, depth):
        """Distribute conjunctions before projection; bound combinatorial growth."""
        if isinstance(schema, bool):
            return [schema]
        base = {k: v for k, v in schema.items() if k not in ('allOf', 'anyOf', 'oneOf')}
        groups = schema.get('allOf', [])
        variants = [base]
        for index, part in enumerate(groups):
            choices = self._alternatives(part, _pointer(_pointer(path, 'allOf'), index), depth + 1)
            variants = [self._intersect(a, b) for a in variants for b in choices]
            if len(variants) > MAX_ALTERNATIVES:
                raise GeminiSchemaError(path, 'union expansion limit exceeded')
        for key in ('anyOf', 'oneOf'):
            if key not in schema:
                continue
            choices = []
            for index, part in enumerate(schema[key]):
                choices.extend(self._alternatives(part, _pointer(_pointer(path, key), index), depth + 1))
            if len(variants) * len(choices) > MAX_ALTERNATIVES:
                raise GeminiSchemaError(path, 'union expansion limit exceeded')
            variants = [self._intersect(a, b) for a in variants for b in choices]
            if key == 'oneOf' and len(schema[key]) > 1:
                for variant in variants:
                    if isinstance(variant, dict):
                        variant.setdefault('_conjuncts', []).append({'oneOf': schema[key]})
        return variants

    def compile(self, schema, path='#', depth=0):
        self.nodes += 1
        if depth > MAX_DEPTH or self.nodes > MAX_NODES:
            raise GeminiSchemaError(path, 'schema projection expansion/depth limit exceeded')
        variants = self._alternatives(schema, path, depth)
        result = []
        for variant in variants:
            if variant is False:
                continue
            result.extend(self._leaf(variant, path, depth))
            if len(result) > MAX_ALTERNATIVES:
                raise GeminiSchemaError(path, 'union expansion limit exceeded')
        if not result:
            return None
        # Null alternatives become nullable on every surviving branch, keeping
        # anyOf as the only field at its node on strict Google gateways.
        nulls = [branch for branch in result if branch.get('type') == 'null']
        nonnull = [branch for branch in result if branch.get('type') != 'null']
        if nulls and nonnull:
            result = [{**branch, 'nullable': True} for branch in nonnull]
        # Deterministic de-duplication, without sorting properties/alternatives.
        unique = {json.dumps(item, sort_keys=True): item for item in result}
        result = list(unique.values())
        if len(result) == 1 and not (
            (nulls and nonnull) or (isinstance(schema, dict) and ('anyOf' in schema or 'oneOf' in schema))
        ):
            return result[0]
        return {'anyOf': result}

    def _leaf(self, schema, path, depth):
        self._charge(schema)
        if schema is True:
            schema = {}
        result = {k: deepcopy(v) for k, v in schema.items() if k in ANNOTATIONS}
        constraints = {k: v for k, v in schema.items() if not k.startswith('_')}
        if 'const' in schema or 'enum' in schema:
            values = [schema['const']] if 'const' in schema else schema['enum']
            if len(values) > MAX_ALTERNATIVES and not all(isinstance(value, str) and value for value in values):
                raise GeminiSchemaError(_pointer(path, 'enum'), 'literal alternative expansion limit exceeded')
            # Candidates already came from this enum. Rechecking membership
            # for every candidate would make large string enums quadratic.
            if 'const' not in schema:
                constraints.pop('enum', None)
            validator = _validator(constraints)
            values = [value for value in values if validator.is_valid(value)]
            if not values:
                return []
            base = {k: v for k, v in schema.items() if k not in ('const', 'enum')}
            if all(isinstance(value, str) and value != '' for value in values):
                # A string enum is exactly representable; keep the existing
                # shape instead of expanding hundreds of single-value branches.
                base['type'] = 'string'
                leaves = self._leaf(base, path, depth)
                for leaf in leaves:
                    leaf['enum'] = values
                return leaves
            branches = []
            for value in values:
                literal = self._literal(value, path)
                merged = self._intersect(base, literal)
                if merged is not False:
                    branches.extend(self._leaf(merged, path, depth + 1))
            return branches
        schema_type = schema.get('type')
        if isinstance(schema_type, list):
            branches = []
            for item in schema_type:
                branches.extend(self._leaf({**schema, 'type': item}, path, depth + 1))
            return branches
        if schema_type is None:
            if path == '#' and ('properties' in schema or 'additionalProperties' in schema):
                schema_type = 'object'
            else:
                raise GeminiSchemaError(path, 'unconstrained value needs an explicit type for this gateway profile')
        if schema_type not in TYPES:
            raise GeminiSchemaError(_pointer(path, 'type'), 'unsupported JSON type')
        result['type'] = schema_type
        for key in TYPE_FIELDS.get(schema_type, set()):
            if key in schema:
                result[key] = schema[key]
        known = (
            ANNOTATIONS
            | IGNORED
            | set().union(*TYPE_FIELDS.values())
            | {'type', 'properties', 'required', 'propertyOrdering', 'items', 'prefixItems', 'format', '_conjuncts'}
        )
        if schema_type == 'object':
            properties = {}
            for name, child in schema.get('properties', {}).items():
                compiled = self.compile(child, _pointer(_pointer(path, 'properties'), name), depth + 1)
                if compiled is None:
                    if child is not False:
                        keyword = 'const' if isinstance(child, dict) and 'const' in child else 'schema'
                        raise GeminiSchemaError(
                            _pointer(_pointer(_pointer(path, 'properties'), name), keyword),
                            'property schema has no representable valid values',
                        )
                    if name in schema.get('required', []):
                        return []
                    self._hint(result, 'properties', {name: False}, path)
                else:
                    properties[name] = compiled
            if 'properties' in schema:
                result['properties'] = properties
            required = schema.get('required', [])
            if set(required) - properties.keys():
                raise GeminiSchemaError(_pointer(path, 'required'), 'required property has no representable definition')
            if 'required' in schema:
                result['required'] = required
            if 'propertyOrdering' in schema:
                ordering = schema['propertyOrdering']
                if not isinstance(ordering, list) or any(
                    not isinstance(k, str) or k not in properties for k in ordering
                ):
                    raise GeminiSchemaError(_pointer(path, 'propertyOrdering'), 'invalid property order')
                result['propertyOrdering'] = list(dict.fromkeys(ordering))
        elif schema_type == 'array':
            items = schema.get('items', True)
            if 'prefixItems' in schema:
                prefix = schema['prefixItems']
                choices = [*prefix, *([] if items is False else [items])]
                if items is False:
                    result['maxItems'] = min(result.get('maxItems', len(prefix)), len(prefix))
                self._hint(result, 'prefixItems', prefix, path)
                items = {'anyOf': choices} if choices else False
            if items is True or items == {}:
                raise GeminiSchemaError(_pointer(path, 'items'), 'array items need an explicit schema')
            compiled = self.compile(items, _pointer(path, 'items'), depth + 1)
            if compiled is None:
                if result.get('minItems', 0) > 0:
                    return []
                # Empty-only arrays: the item type is unreachable.
                result['maxItems'] = 0
                compiled = {'type': 'string'}
            result['items'] = compiled
        if 'format' in schema:
            if schema['format'] in FORMATS.get(schema_type, set()):
                result['format'] = schema['format']
            else:
                self._hint(result, 'format', schema['format'], path)
        for key, value in schema.items():
            if key not in known:
                if key in ('exclusiveMinimum', 'exclusiveMaximum') and schema_type in ('integer', 'number'):
                    bound = 'minimum' if key == 'exclusiveMinimum' else 'maximum'
                    converted = (
                        (math.floor(value) + 1 if bound == 'minimum' else math.ceil(value) - 1)
                        if schema_type == 'integer'
                        else value
                    )
                    result[bound] = (max if bound == 'minimum' else min)(result.get(bound, converted), converted)
                    if schema_type == 'integer':
                        continue
                self._hint(result, key, value, path)
        for conjunction in schema.get('_conjuncts', []):
            for key, value in conjunction.items():
                self._hint(result, key, value, path)
        # Tuple tails and exclusive bounds can introduce a tighter limit after
        # the initial fields were copied. Reject empty branches on the first
        # compilation, not only when a retry compiles the projected schema.
        for low, high in (
            ('minimum', 'maximum'),
            ('minLength', 'maxLength'),
            ('minItems', 'maxItems'),
            ('minProperties', 'maxProperties'),
        ):
            if low in result and high in result and result[low] > result[high]:
                return []
        return [result]

    def _literal(self, value, path):
        kind = _json_type(value)
        literal = {'type': kind}
        if kind == 'string':
            literal.update({'enum': [value]} if value else {'minLength': 0, 'maxLength': 0})
        elif kind in ('integer', 'number'):
            literal.update(minimum=value, maximum=value)
        elif kind == 'object':
            literal.update(
                properties={name: {'const': item} for name, item in value.items()},
                required=list(value),
                minProperties=len(value),
                maxProperties=len(value),
            )
        elif kind == 'array':
            literal.update(
                prefixItems=[{'const': item} for item in value], items=False, minItems=len(value), maxItems=len(value)
            )
        elif kind == 'boolean':
            literal['_conjuncts'] = [{'const': value}]
        return literal


def compile_gemini_schema(schema) -> CompiledGeminiSchema:
    resolved = resolve_gemini_schema(schema)
    compiler = _Compiler()
    parameters = compiler.compile(resolved)
    if parameters is None:
        keyword = 'const' if isinstance(resolved, dict) and 'const' in resolved else 'schema'
        raise GeminiSchemaError(_pointer('#', keyword), 'schema has no representable valid values')
    if len(json.dumps(parameters, ensure_ascii=False).encode('utf-8')) > MAX_SCHEMA_BYTES:
        raise GeminiSchemaError('#', 'projected schema byte limit exceeded')
    return CompiledGeminiSchema(parameters, resolved, tuple(dict.fromkeys(compiler.diagnostics)))


def validate_gemini_tool_arguments(schema, arguments):
    """Return a safe error, without values, or None; never coerce arguments."""
    # The legacy parser can fall back to Python literals. Validate JSON values
    # before jsonschema or serialization: both can otherwise accept non-string
    # keys or coerce tuples, while tool kwargs still receive the original data.
    pending = [(arguments, '#')]
    while pending:
        value, path = pending.pop()
        if isinstance(value, dict):
            if any(not isinstance(key, str) for key in value):
                return f'Error: Tool arguments must use JSON string keys at {path}.'
            pending.extend((item, _pointer(path, key)) for key, item in value.items())
        elif isinstance(value, list):
            pending.extend((item, _pointer(path, index)) for index, item in enumerate(value))
        elif not isinstance(value, (str, int, float, bool, type(None))) or (
            isinstance(value, float) and not math.isfinite(value)
        ):
            return f'Error: Tool arguments contain a non-JSON value at {path}.'
    try:
        resolved = resolve_gemini_schema(schema)
        error = next(_validator(resolved).iter_errors(arguments), None)
    except GeminiSchemaError as exc:
        return f'Error: Tool input schema cannot be validated ({exc}).'
    if error is None:
        return None
    path = '#'
    for part in error.absolute_path:
        path = _pointer(path, part)
    return f'Error: Tool arguments do not match the original input schema at {path} ({error.validator}). Please correct the arguments.'
