"""Pure native provider conversion, with opaque signed history replay.

This module deliberately has no application, database, transport, or SDK imports.
"""

import codecs
import copy
import hashlib
import json
import time
from urllib.parse import quote, urlsplit, urlunsplit

NATIVE_API_TYPES = frozenset({'gemini', 'anthropic'})
_FORMAT = 'open-webui-native-v1'


def validate_connection(config: dict) -> dict:
    result = copy.deepcopy(config)
    kind = result.get('api_type')
    if kind not in (None, '', 'chat_completions', 'responses', *NATIVE_API_TYPES):
        raise ValueError('Unsupported connection api_type')
    if kind in NATIVE_API_TYPES and (
        any(result.get(k) for k in ('azure', 'azure_deployment', 'azure_endpoint'))
        or result.get('provider') == 'azure'
        or result.get('auth_type') in ('azure_ad', 'microsoft_entra_id')
    ):
        raise ValueError('Native providers cannot use Azure configuration')
    if result.get('auth_type') == 'api_key' and kind not in NATIVE_API_TYPES:
        raise ValueError('api_key authentication requires a native provider')
    return result


def _kind(api_type):
    if api_type not in NATIVE_API_TYPES:
        raise ValueError('Unsupported native provider')
    return api_type


def make_binding(api_type: str, base_url: str, model: str) -> dict:
    _kind(api_type)
    parsed = urlsplit(base_url.rstrip('/'))
    # Credentials and query strings are neither replay identity nor safe metadata.
    host = parsed.hostname or ''
    if parsed.port:
        host += f':{parsed.port}'
    normalized = urlunsplit((parsed.scheme.lower(), host.lower(), parsed.path.rstrip('/'), '', ''))
    canonical_model = model.removeprefix('models/') if api_type == 'gemini' else model
    return {'protocol': api_type, 'model': canonical_model, 'url_hash': hashlib.sha256(normalized.encode()).hexdigest()}


def prepare_payload(api_type: str, payload: dict) -> dict:
    _kind(api_type)
    result = copy.deepcopy(payload)
    if 'max_completion_tokens' in result:
        result.setdefault('max_tokens', result.pop('max_completion_tokens'))
    if api_type == 'anthropic':
        result.setdefault('max_tokens', 4096)
    for name in ('generationConfig', 'thinkingConfig', 'thinking', 'output_config', 'toolConfig'):
        if name in result and not isinstance(result[name], dict):
            raise ValueError(f'{name} must be an object')
    if api_type == 'gemini' and 'maxOutputTokens' in result.get('generationConfig', {}):
        # Consume native spelling once, before budgeting, so a later budget reduction
        # cannot be overridden when the transport body is rebuilt.
        result['max_tokens'] = result['generationConfig'].pop('maxOutputTokens')
    for name in ('max_tokens',):
        if name in result and (type(result[name]) is not int or result[name] <= 0):
            raise ValueError(f'{name} must be a positive integer')
    thinking = result.get('thinking')
    if thinking is not None:
        if thinking.get('type') not in ('enabled', 'disabled', 'adaptive'):
            raise ValueError('Unsupported native thinking type')
        if thinking['type'] == 'enabled' and (
            type(thinking.get('budget_tokens')) is not int or thinking['budget_tokens'] < 1024
        ):
            raise ValueError('Enabled Anthropic thinking requires budget_tokens >= 1024')
    for config in (result.get('thinkingConfig'), result.get('generationConfig', {}).get('thinkingConfig')):
        if config is None:
            continue
        if not isinstance(config, dict):
            raise ValueError('thinkingConfig must be an object')
        if 'thinkingBudget' in config and (type(config['thinkingBudget']) is not int or config['thinkingBudget'] < -1):
            raise ValueError('thinkingBudget must be an integer >= -1')
        if 'includeThoughts' in config and type(config['includeThoughts']) is not bool:
            raise ValueError('includeThoughts must be a boolean')
    return result


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def _block_integrity(blocks):
    canonical = json.dumps(blocks, ensure_ascii=False, separators=(',', ':'), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _function(call):
    return call.get('provider_function') or call.get('function', {})


def _projection(message):
    return {
        'content': message.get('content') or '',
        'tool_calls': [
            {
                'id': c.get('id'),
                'name': _function(c).get('name'),
                'arguments': _arguments(_function(c).get('arguments', '{}')),
            }
            for c in message.get('tool_calls', [])
        ],
    }


def _arguments(value):
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (ValueError, TypeError) as exc:
        raise ValueError('Invalid tool call JSON arguments') from exc
    if not isinstance(parsed, dict):
        raise ValueError('Tool call arguments must be an object')
    return parsed


def _replay(message, binding):
    for detail in message.get('reasoning_details') or []:
        if detail.get('type') != 'reasoning.native' or detail.get('format') != _FORMAT:
            continue
        data = detail.get('data')
        if not isinstance(data, dict):
            raise ValueError('Invalid native reasoning envelope')
        if data.get('binding') != binding:
            continue
        blocks = copy.deepcopy(data.get('blocks'))
        if not isinstance(blocks, list):
            raise ValueError('Invalid native reasoning blocks')
        for position in data.get('signed_parts', []):
            if (
                type(position) is not int
                or position < 0
                or position >= len(blocks)
                or not blocks[position].get('thoughtSignature')
            ):
                raise ValueError('Native Gemini replay is missing a required thought signature')
        if data.get('block_integrity') is not None and data['block_integrity'] != _block_integrity(blocks):
            raise ValueError('Native reasoning block integrity mismatch')
        derived = _projection(_message(binding['protocol'], blocks, binding, data.get('source_id')))
        if 'source_id' not in data:
            # Old envelopes did not retain responseId; their canonical synthetic IDs
            # remain the pairing identity, while names and arguments are still checked.
            for actual, expected in zip(derived['tool_calls'], data.get('projection', {}).get('tool_calls', [])):
                if str(actual.get('id', '')).startswith('call_gemini_'):
                    actual['id'] = expected.get('id')
        if derived != data.get('projection'):
            raise ValueError('Native reasoning block projection mismatch')
        if data.get('projection') != _projection(message):
            if any(b.get('thoughtSignature') or b.get('type') in ('thinking', 'redacted_thinking') for b in blocks):
                raise ValueError('Native signed assistant content or tool calls were edited')
            return None
        for block in blocks:
            if block.get('type') == 'thinking' and not block.get('signature'):
                raise ValueError('Native thinking block is missing its required signature')
            if block.get('type') == 'redacted_thinking' and not block.get('data'):
                raise ValueError('Native redacted thinking block is missing its required data')
        return blocks
    return None


def _image(url, kind):
    if url.startswith('data:'):
        try:
            header, data = url.split(',', 1)
            media = header[5:].removesuffix(';base64')
            if not header.endswith(';base64') or not media.startswith('image/') or not data:
                raise ValueError()
        except ValueError as exc:
            raise ValueError('Images require a base64 image data URL') from exc
        return (
            {'inlineData': {'mimeType': media, 'data': data}}
            if kind == 'gemini'
            else {'type': 'image', 'source': {'type': 'base64', 'media_type': media, 'data': data}}
        )
    if urlsplit(url).scheme not in ('https', 'http', 'gs'):
        raise ValueError('Unsupported image URL')
    if kind == 'anthropic':
        if not url.startswith(('http://', 'https://')):
            raise ValueError('Anthropic image URLs require HTTP(S)')
        return {'type': 'image', 'source': {'type': 'url', 'url': url}}
    return {'fileData': {'fileUri': url}}


def _content(content, kind):
    if content is None:
        return []
    if isinstance(content, str):
        if not content:
            return []
        return [{'text': content}] if kind == 'gemini' else [{'type': 'text', 'text': content}]
    if not isinstance(content, list):
        raise ValueError('Message content must be text or a list')
    result = []
    for part in content:
        if part.get('type') in ('text', 'input_text'):
            result.extend(_content(part['text'], kind))
        elif part.get('type') in ('image_url', 'input_image'):
            image_url = part['image_url']
            result.append(_image(image_url if isinstance(image_url, str) else image_url['url'], kind))
        else:
            raise ValueError(f'Unsupported message content type: {part.get("type")}')
    return result


def _messages(kind, messages, binding):
    system, output, calls = [], [], {}
    prefix = True
    for message in messages:
        role = message.get('role')
        if prefix and role in ('system', 'developer'):
            parts = _content(message.get('content'), kind)
            if any(('text' not in p) for p in parts):
                raise ValueError('System instructions must be text')
            system.extend(parts)
            continue
        prefix = False
        if role in ('system', 'developer'):
            text = '\n'.join(p['text'] for p in _content(message.get('content'), kind))
            parts = _content('[System reminder]\n' + text, kind)
            role = 'user'
        elif role == 'tool':
            call_id = message.get('tool_call_id')
            if call_id not in calls:
                raise ValueError(f'Tool result has no matching call: {call_id}')
            name, provider_id = calls.pop(call_id)
            content = _content(message.get('content'), kind)
            if kind == 'anthropic':
                parts = [{'type': 'tool_result', 'tool_use_id': call_id, 'content': content}]
            else:
                text = '\n'.join(p['text'] for p in content if 'text' in p)
                response = {'name': name, 'response': {'result': text}}
                if provider_id:
                    response['id'] = provider_id
                # FunctionResponsePart accepts inlineData only. URI images use the
                # enclosing user turn's ordinary Part.fileData alongside this result.
                inline_images = [p for p in content if 'inlineData' in p]
                if inline_images:
                    response['parts'] = inline_images
                parts = [{'functionResponse': response}]
                parts.extend(p for p in content if 'fileData' in p)
            role = 'user'
        elif role in ('user', 'assistant'):
            parts = _replay(message, binding) if role == 'assistant' else None
            replayed = parts is not None
            if parts is None:
                parts = _content(message.get('content'), kind)
            for index, call in enumerate(message.get('tool_calls', [])):
                if call.get('type', 'function') != 'function':
                    raise ValueError('Unsupported tool call type')
                function = _function(call)
                provider_id = call.get('id')
                if kind == 'gemini' and provider_id and provider_id.startswith('call_gemini_'):
                    provider_id = None
                calls[call['id']] = (function['name'], provider_id)
                if not replayed:
                    if kind == 'gemini':
                        fc = {'name': function['name'], 'args': _arguments(function.get('arguments', '{}'))}
                        if provider_id:
                            fc['id'] = provider_id
                        parts.append({'functionCall': fc})
                    else:
                        parts.append(
                            {
                                'type': 'tool_use',
                                'id': call['id'],
                                'name': function['name'],
                                'input': _arguments(function.get('arguments', '{}')),
                            }
                        )
        else:
            raise ValueError(f'Unsupported message role: {role}')
        native_role = 'model' if kind == 'gemini' and role == 'assistant' else role
        key = 'parts' if kind == 'gemini' else 'content'
        # Consecutive tool results form one user turn; no loss of result boundaries.
        if output and output[-1]['role'] == native_role:
            output[-1][key].extend(parts)
        else:
            output.append({'role': native_role, key: parts})
    return system, output


def _tools(kind, tools, compatibility=None):
    result = []
    for tool in tools:
        if tool.get('type') != 'function':
            raise ValueError('Unsupported native tool type')
        function = tool['function']
        item = {'name': function['name']}
        if 'description' in function:
            item['description'] = function['description']
        schema = function.get('parametersJsonSchema', function.get('parameters', {'type': 'object', 'properties': {}}))
        schema_key = 'input_schema'
        if kind == 'gemini':
            schema_key = (
                'parameters'
                if compatibility == 'gemini' and 'parametersJsonSchema' not in function
                else 'parametersJsonSchema'
            )
        item[schema_key] = copy.deepcopy(schema)
        result.append(item)
    return [{'functionDeclarations': result}] if kind == 'gemini' else result


def build_request(api_type: str, base_url: str, payload: dict, binding: dict) -> tuple[str, dict]:
    kind = _kind(api_type)
    payload = prepare_payload(kind, payload)
    model = payload.get('model', '')
    if not isinstance(model, str) or not model or any(p in ('.', '..', '') for p in model.split('/')):
        raise ValueError('Invalid native model path')
    if binding.get('model') != (model.removeprefix('models/') if kind == 'gemini' else model):
        raise ValueError('Native binding model does not match request')
    system, messages = _messages(kind, payload.get('messages', []), binding)
    if kind == 'gemini':
        model_path = quote(model.removeprefix('models/'), safe='/')
        method = 'streamGenerateContent?alt=sse' if payload.get('stream') else 'generateContent'
        url = base_url.rstrip('/') + '/models/' + model_path + ':' + method
        body = {'contents': messages}
        if system:
            body['systemInstruction'] = {'parts': system}
        generation = {}
        for source, target in (
            ('temperature', 'temperature'),
            ('top_p', 'topP'),
            ('top_k', 'topK'),
            ('max_tokens', 'maxOutputTokens'),
            ('stop', 'stopSequences'),
            ('seed', 'seed'),
            ('frequency_penalty', 'frequencyPenalty'),
            ('presence_penalty', 'presencePenalty'),
        ):
            if source in payload:
                generation[target] = copy.deepcopy(payload[source])
        if isinstance(generation.get('stopSequences'), str):
            generation['stopSequences'] = [generation['stopSequences']]
        response_format = payload.get('response_format')
        if response_format:
            if response_format.get('type') == 'json_object':
                generation['responseMimeType'] = 'application/json'
            elif response_format.get('type') == 'json_schema':
                generation.update(
                    responseMimeType='application/json', responseJsonSchema=response_format['json_schema']['schema']
                )
            elif response_format.get('type') != 'text':
                raise ValueError('Unsupported Gemini response format')
        generation.update(copy.deepcopy(payload.get('generationConfig', {})))
        if 'thinkingConfig' in payload:
            generation['thinkingConfig'] = copy.deepcopy(payload['thinkingConfig'])
        if generation:
            body['generationConfig'] = generation
        for key in ('safetySettings', 'cachedContent', 'toolConfig'):
            if key in payload:
                body[key] = copy.deepcopy(payload[key])
    else:
        url = base_url.rstrip('/') + '/messages'
        body = {'model': model, 'messages': messages, 'max_tokens': payload['max_tokens']}
        if system:
            body['system'] = system
        for key in ('stream', 'temperature', 'top_p', 'top_k', 'thinking', 'output_config', 'service_tier'):
            if key in payload:
                body[key] = copy.deepcopy(payload[key])
        if 'stop' in payload:
            body['stop_sequences'] = [payload['stop']] if isinstance(payload['stop'], str) else payload['stop']
    if payload.get('tools'):
        body['tools'] = _tools(kind, payload['tools'], payload.get('_native_tool_schema_compatibility'))
    choice = payload.get('tool_choice')
    if choice is not None:
        if isinstance(choice, str):
            if choice not in ('auto', 'none', 'required'):
                raise ValueError('Unsupported tool choice')
            converted = (
                {'mode': {'auto': 'AUTO', 'none': 'NONE', 'required': 'ANY'}[choice]}
                if kind == 'gemini'
                else {'type': {'auto': 'auto', 'none': 'none', 'required': 'any'}[choice]}
            )
        elif isinstance(choice, dict) and choice.get('type') == 'function':
            name = choice['function']['name']
            converted = (
                {'mode': 'ANY', 'allowedFunctionNames': [name]} if kind == 'gemini' else {'type': 'tool', 'name': name}
            )
        else:
            raise ValueError('Unsupported tool choice')
        if kind == 'gemini':
            body.setdefault('toolConfig', {})['functionCallingConfig'] = converted
        else:
            if payload.get('parallel_tool_calls') is False:
                converted['disable_parallel_tool_use'] = True
            body['tool_choice'] = converted
    elif kind == 'anthropic' and payload.get('parallel_tool_calls') is False and payload.get('tools'):
        body['tool_choice'] = {'type': 'auto', 'disable_parallel_tool_use': True}
    return url, body


def _usage(kind, data):
    if kind == 'gemini':
        usage = data.get('usageMetadata', {})
        prompt = usage.get('promptTokenCount', 0)
        thought = usage.get('thoughtsTokenCount', 0)
        output = usage.get('candidatesTokenCount', 0) + thought
        result = {
            'prompt_tokens': prompt,
            'completion_tokens': output,
            'total_tokens': usage.get('totalTokenCount', prompt + output),
        }
        if thought:
            result['completion_tokens_details'] = {'reasoning_tokens': thought}
        if usage.get('cachedContentTokenCount'):
            result['prompt_tokens_details'] = {'cached_tokens': usage['cachedContentTokenCount']}
    else:
        usage = data.get('usage', {})
        cached = usage.get('cache_read_input_tokens', 0)
        prompt = usage.get('input_tokens', 0) + cached + usage.get('cache_creation_input_tokens', 0)
        output = usage.get('output_tokens', 0)
        result = {'prompt_tokens': prompt, 'completion_tokens': output, 'total_tokens': prompt + output}
        if cached:
            result['prompt_tokens_details'] = {'cached_tokens': cached}
    return result


def _finish(kind, reason, has_calls=False):
    if kind == 'gemini':
        if reason == 'MAX_TOKENS':
            return 'length'
        if reason in ('SAFETY', 'RECITATION', 'BLOCKLIST', 'PROHIBITED_CONTENT', 'SPII', 'IMAGE_SAFETY'):
            return 'content_filter'
        if reason in ('MALFORMED_FUNCTION_CALL', 'UNEXPECTED_TOOL_CALL'):
            raise ValueError(f'Gemini completion failed: {reason}')
        return 'tool_calls' if has_calls else 'stop'
    return {'max_tokens': 'length', 'tool_use': 'tool_calls', 'refusal': 'content_filter'}.get(reason, 'stop')


def _call_id(binding, ordinal, call, source_id=None):
    return 'call_gemini_' + hashlib.sha256(_json([binding, source_id, ordinal, call]).encode()).hexdigest()[:24]


def _message(kind, blocks, binding, source_id=None):
    message = {'role': 'assistant', 'content': ''}
    thoughts, calls = [], []
    for index, block in enumerate(blocks):
        if kind == 'gemini':
            if set(block) - {'text', 'thought', 'thoughtSignature', 'functionCall'}:
                raise ValueError('Unsupported Gemini output part')
            if 'text' in block:
                if block.get('thought'):
                    thoughts.append(block['text'])
                else:
                    message['content'] += block['text']
            elif 'functionCall' in block:
                fc = block['functionCall']
                if not isinstance(fc, dict):
                    raise ValueError('Gemini function call arguments and name require an object')
                if 'partialArgs' in fc or 'willContinue' in fc:
                    raise ValueError('Unsupported Gemini function call fragment fields')
                if not isinstance(fc.get('name'), str) or not fc['name'].strip():
                    raise ValueError('Gemini function call requires a nonempty name')
                if 'args' in fc and not isinstance(fc['args'], dict):
                    raise ValueError('Gemini function call arguments must be an object')
                call_id = fc.get('id') or _call_id(binding, len(calls), fc, source_id)
                calls.append(
                    {
                        'id': call_id,
                        'type': 'function',
                        'function': {'name': fc['name'], 'arguments': _json(fc.get('args', {}))},
                    }
                )
            elif set(block) - {'thoughtSignature', 'thought'}:
                raise ValueError('Unsupported Gemini output part')
        else:
            typ = block.get('type')
            if typ == 'text':
                message['content'] += block['text']
            elif typ == 'thinking':
                thoughts.append(block.get('thinking', ''))
            elif typ == 'tool_use':
                calls.append(
                    {
                        'id': block['id'],
                        'type': 'function',
                        'function': {'name': block['name'], 'arguments': _json(block.get('input', {}))},
                    }
                )
            elif typ == 'redacted_thinking':
                pass
            elif typ == 'refusal':
                message['refusal'] = block.get('refusal', '')
            else:
                raise ValueError(f'Unsupported Anthropic output block: {typ}')
    if thoughts:
        message['reasoning_content'] = ''.join(thoughts)
    if calls:
        message['tool_calls'] = calls
    if blocks:
        message['reasoning_details'] = [
            {
                'type': 'reasoning.native',
                'format': _FORMAT,
                'index': 0,
                'data': {
                    'binding': copy.deepcopy(binding),
                    'blocks': copy.deepcopy(blocks),
                    'projection': _projection(message),
                    'source_id': source_id,
                    'block_integrity': _block_integrity(blocks),
                    'signed_parts': [i for i, block in enumerate(blocks) if block.get('thoughtSignature')],
                },
            }
        ]
    return message


def normalize_response(api_type: str, data: dict, model: str, binding: dict) -> dict:
    kind = _kind(api_type)
    if 'error' in data:
        raise ValueError(f'Native provider error: {_json(data["error"])}')
    if kind == 'gemini':
        candidates = data.get('candidates', [])
        if not candidates:
            feedback = data.get('promptFeedback', {})
            if feedback.get('blockReason'):
                candidates = [{'content': {'parts': []}, 'finishReason': 'SAFETY'}]
            else:
                raise ValueError('Gemini response has no candidates')
        choices = []
        for index, candidate in enumerate(candidates):
            message = _message(kind, candidate.get('content', {}).get('parts', []), binding, data.get('responseId'))
            choices.append(
                {
                    'index': candidate.get('index', index),
                    'message': message,
                    'finish_reason': _finish(kind, candidate.get('finishReason'), bool(message.get('tool_calls'))),
                }
            )
    else:
        message = _message(kind, data.get('content', []), binding)
        choices = [{'index': 0, 'message': message, 'finish_reason': _finish(kind, data.get('stop_reason'))}]
    return {
        'id': data.get('id') or data.get('responseId') or 'chatcmpl-native',
        'object': 'chat.completion',
        'created': int(time.time()),
        'model': model,
        'choices': choices,
        'usage': _usage(kind, data),
    }


def normalize_models(api_type: str, data: dict) -> dict:
    kind = _kind(api_type)
    result = []
    for item in data.get('models' if kind == 'gemini' else 'data', []):
        if kind == 'gemini':
            methods = item.get('supportedGenerationMethods')
            if methods is not None and 'generateContent' not in methods:
                continue
            model_id = item['name'].removeprefix('models/')
        else:
            model_id = item['id']
        result.append(
            {
                'id': model_id,
                'name': item.get('display_name') or item.get('displayName') or model_id,
                'object': 'model',
                'created': 0,
                'owned_by': kind,
            }
        )
    return {'object': 'list', 'data': result}


async def _events(chunks):
    decoder = codecs.getincrementaldecoder('utf-8')()
    line, lines, after_cr = '', [], False
    async for chunk in chunks:
        for char in decoder.decode(chunk):
            if after_cr and char == '\n':
                after_cr = False
                continue
            after_cr = char == '\r'
            if char in ('\r', '\n'):
                if line == '':
                    if lines:
                        yield '\n'.join(lines)
                        lines = []
                elif line.startswith('data:'):
                    lines.append(line[5:].removeprefix(' '))
                line = ''
            else:
                line += char
    tail = decoder.decode(b'', final=True)
    if line or lines or tail:
        raise ValueError('Native SSE stream truncated in a frame')


def _sse(model, delta, finish=None, usage=None, response_id='chatcmpl-native'):
    data = {
        'id': response_id,
        'object': 'chat.completion.chunk',
        'created': int(time.time()),
        'model': model,
        'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}],
    }
    if usage is not None:
        data['usage'] = usage
    return ('data: ' + _json(data) + '\n\n').encode()


async def stream_response(api_type: str, chunks, model: str, binding: dict):
    kind = _kind(api_type)
    blocks, partial_json, usage_data = [], {}, {}
    completed, reason, response_id = False, None, 'chatcmpl-native'
    tool_index = {}
    call_source_id = None
    async for frame in _events(chunks):
        if frame == '[DONE]':
            break
        data = json.loads(frame)
        if 'error' in data or data.get('type') == 'error':
            raise ValueError(f'Native provider stream error: {_json(data)}')
        if kind == 'gemini':
            if 'usageMetadata' in data:
                usage_data['usageMetadata'] = data['usageMetadata']
            response_id = data.get('responseId', response_id)
            if data.get('promptFeedback', {}).get('blockReason'):
                completed, reason = True, 'SAFETY'
            candidates = data.get('candidates', [])
            if len(candidates) > 1:
                raise ValueError('Streaming multiple Gemini candidates is unsupported')
            for candidate in candidates:
                for block in candidate.get('content', {}).get('parts', []):
                    # Text fragments remain separate parts, preserving signatures on their originating part.
                    if 'functionCall' in block:
                        if not tool_index:
                            # A provider may report responseId only after calls have
                            # already streamed. Their emitted IDs must remain frozen.
                            call_source_id = response_id if response_id != 'chatcmpl-native' else None
                        index = len(blocks)
                        msg = _message(kind, [block], binding)
                        call = msg['tool_calls'][0]
                        # Call ordinal is stable even when streaming fragments text parts.
                        if not block['functionCall'].get('id'):
                            call['id'] = _call_id(
                                binding,
                                len(tool_index),
                                block['functionCall'],
                                call_source_id,
                            )
                        tool_index[index] = len(tool_index)
                        yield _sse(
                            model, {'tool_calls': [{'index': tool_index[index], **call}]}, response_id=response_id
                        )
                    elif 'text' in block:
                        yield _sse(
                            model,
                            {'reasoning_content' if block.get('thought') else 'content': block['text']},
                            response_id=response_id,
                        )
                    elif set(block) - {'thoughtSignature', 'thought'}:
                        raise ValueError('Unsupported Gemini stream output part')
                    blocks.append(copy.deepcopy(block))
                if candidate.get('finishReason'):
                    completed, reason = True, candidate['finishReason']
        else:
            typ = data.get('type')
            if typ == 'message_start':
                message = data['message']
                response_id = message.get('id', response_id)
                usage_data['usage'] = copy.deepcopy(message.get('usage', {}))
            elif typ == 'content_block_start':
                index = data['index']
                if index != len(blocks):
                    raise ValueError('Invalid Anthropic block sequence')
                block = copy.deepcopy(data['content_block'])
                blocks.append(block)
                block_type = block.get('type')
                if block_type == 'text':
                    if block.get('text'):
                        yield _sse(model, {'content': block['text']}, response_id=response_id)
                elif block_type == 'thinking':
                    if block.get('thinking'):
                        yield _sse(model, {'reasoning_content': block['thinking']}, response_id=response_id)
                elif block_type == 'tool_use':
                    tool_index[index] = len(tool_index)
                    partial_json[index] = ''
                    yield _sse(
                        model,
                        {
                            'tool_calls': [
                                {
                                    'index': tool_index[index],
                                    'id': block['id'],
                                    'type': 'function',
                                    'function': {'name': block['name'], 'arguments': ''},
                                }
                            ]
                        },
                        response_id=response_id,
                    )
                elif block_type != 'redacted_thinking':
                    raise ValueError(f'Unsupported Anthropic stream block: {block_type}')
            elif typ == 'content_block_delta':
                index, delta = data['index'], data['delta']
                if index >= len(blocks):
                    raise ValueError('Native delta without content block')
                block = blocks[index]
                delta_type = delta.get('type')
                if delta_type == 'text_delta':
                    block['text'] = block.get('text', '') + delta['text']
                    yield _sse(model, {'content': delta['text']}, response_id=response_id)
                elif delta_type == 'thinking_delta':
                    block['thinking'] = block.get('thinking', '') + delta['thinking']
                    yield _sse(model, {'reasoning_content': delta['thinking']}, response_id=response_id)
                elif delta_type == 'signature_delta':
                    block['signature'] = block.get('signature', '') + delta['signature']
                elif delta_type == 'input_json_delta':
                    partial_json[index] += delta['partial_json']
                    yield _sse(
                        model,
                        {
                            'tool_calls': [
                                {'index': tool_index[index], 'function': {'arguments': delta['partial_json']}}
                            ]
                        },
                        response_id=response_id,
                    )
                else:
                    raise ValueError(f'Unsupported Anthropic stream delta: {delta_type}')
            elif typ == 'content_block_stop':
                index = data['index']
                if index in partial_json and partial_json[index]:
                    blocks[index]['input'] = _arguments(partial_json[index])
                elif index in partial_json:
                    yield _sse(
                        model,
                        {
                            'tool_calls': [
                                {
                                    'index': tool_index[index],
                                    'function': {'arguments': _json(blocks[index].get('input', {}))},
                                }
                            ]
                        },
                        response_id=response_id,
                    )
            elif typ == 'message_delta':
                reason = data.get('delta', {}).get('stop_reason', reason)
                usage_data.setdefault('usage', {}).update(data.get('usage', {}))
            elif typ == 'message_stop':
                completed = True
            elif typ != 'ping':
                raise ValueError(f'Unsupported Anthropic stream event: {typ}')
    if not completed:
        raise ValueError('Native provider stream truncated before completion')
    source_id = (
        call_source_id
        if kind == 'gemini' and tool_index
        else (response_id if response_id != 'chatcmpl-native' else None)
    )
    message = _message(kind, blocks, binding, source_id)
    if message.get('reasoning_details'):
        yield _sse(model, {'reasoning_details': message['reasoning_details']}, response_id=response_id)
    yield _sse(model, {}, _finish(kind, reason, bool(message.get('tool_calls'))), _usage(kind, usage_data), response_id)
    yield b'data: [DONE]\n\n'
