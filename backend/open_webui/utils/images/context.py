"""Keep image tool context separate from the attachments already displayed in chat."""

import json


def get_image_tool_result_files(tool_name, tool_result, tool_type) -> list[dict]:
    if tool_type != 'builtin' or tool_name not in {'generate_image', 'edit_image'}:
        return []
    if isinstance(tool_result, str):
        try:
            tool_result = json.loads(tool_result)
        except (TypeError, ValueError):
            return []
    if not isinstance(tool_result, dict) or tool_result.get('status') != 'success' or tool_result.get('error'):
        return []
    images = tool_result.get('images')
    if not isinstance(images, list):
        return []

    files = []
    seen = set()
    for image in images:
        url = image.get('url') if isinstance(image, dict) else None
        if isinstance(url, str) and url and url not in seen:
            seen.add(url)
            files.append({'type': 'image', 'url': url, 'context_only': True})
    return files


def build_tool_result_parts(result: dict) -> tuple[list[dict], list[dict]]:
    output_parts = [{'type': 'input_text', 'text': result.get('content', '')}]
    display_files = []
    for file in result.get('files', []):
        url = file.get('url', '')
        if file.get('type') == 'image' and (file.get('context_only') or url.startswith('data:')):
            # Persist compact references; resolve authorized image bytes only when sending to the model.
            output_parts.append({'type': 'input_image', 'image_url': url})
        else:
            display_files.append(file)
    return output_parts, display_files
