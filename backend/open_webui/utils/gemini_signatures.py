"""Opaque Gemini replay fields on the OpenAI-compatible transport."""


def gemini_signature_fields(item: dict) -> dict:
    """Copy only the documented carrier; never infer a provider from a model ID.

    A signature delta is a complete opaque value, not a text fragment. Repeated
    snapshots replace it; absent/empty deltas must not erase a previous value.
    """
    extra = item.get('extra_content')
    google = extra.get('google') if isinstance(extra, dict) else None
    signature = google.get('thought_signature') if isinstance(google, dict) else None
    if isinstance(signature, str) and signature:
        return {'extra_content': {'google': {'thought_signature': signature}}}
    return {}


def strip_gemini_signature(item: dict) -> dict:
    """Remove model-bound replay data from a projection without changing storage."""
    result = dict(item)
    extra = result.get('extra_content')
    if isinstance(extra, dict) and isinstance(extra.get('google'), dict) and 'thought_signature' in extra['google']:
        google = {key: value for key, value in extra['google'].items() if key != 'thought_signature'}
        extra = {key: value for key, value in extra.items() if key != 'google'}
        if google:
            extra['google'] = google
        if extra:
            result['extra_content'] = extra
        else:
            result.pop('extra_content', None)
    if isinstance(result.get('tool_calls'), list):
        result['tool_calls'] = [strip_gemini_signature(call) for call in result['tool_calls']]
    return result
