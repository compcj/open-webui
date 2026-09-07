def remap_reasoning_effort_for_responses(responses_payload: dict) -> dict:
    """Remap Completions ``reasoning_effort`` to Responses ``reasoning.effort``.

    Always pops the flat key (including ``None``), because the Responses API
    rejects ``reasoning_effort`` even when null. Merges into any existing
    ``reasoning`` object so fields such as ``mode`` or ``summary`` are kept.
    """
    reasoning_effort = responses_payload.pop('reasoning_effort', None)
    if reasoning_effort:
        existing_reasoning = responses_payload.get('reasoning')
        if not isinstance(existing_reasoning, dict):
            existing_reasoning = {}
        responses_payload['reasoning'] = {**existing_reasoning, 'effort': reasoning_effort}
    return responses_payload
