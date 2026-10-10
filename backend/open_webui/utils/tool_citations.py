"""Project persisted tool citations into provider input without changing raw output."""


def citation_output(item: dict):
    """Keep legacy output unchanged; append frozen citation text on a new value."""
    output = item.get('output', [])
    context = item.get('citation_context')
    if not isinstance(context, str) or not context:
        return output
    if isinstance(output, str):
        return f'{output}\n\n{context}'
    if isinstance(output, list):
        return [*output, {'type': 'input_text', 'text': f'\n\n{context}'}]
    return output
