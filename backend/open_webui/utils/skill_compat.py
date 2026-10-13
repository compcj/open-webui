"""Lossless adapters between legacy OpenClaw bags and the canonical file tree.

This module has no application/database imports and is also used by migration.
"""

from copy import deepcopy
import re

import yaml
from open_webui.utils.skill_files import file_bytes, frontmatter, validate_files, validate_path


def canonical_snapshot(skill_id: str, snapshot: dict) -> dict:
    value = deepcopy(snapshot)
    meta = value.get('meta') or {}
    oc = meta.get('openclaw')
    if oc is not None and not isinstance(oc, dict):
        raise ValueError(f'Skill {skill_id}: invalid openclaw metadata')
    oc = oc or {}
    data = value.get('data') or {}
    files = deepcopy(data.get('files'))
    if files is None:
        files = [{'path': 'SKILL.md', 'content': value.get('content') or ''}]
    try:
        indexed = {}
        for file in files:
            path = validate_path(file['path'])
            if path in indexed:
                raise ValueError(f'duplicate file {path}')
            indexed[path] = file
        legacy = oc.get('files') or {}
        if not isinstance(legacy, dict):
            raise ValueError('invalid legacy files')
        for path, item in legacy.items():
            validate_path(path)
            if not isinstance(item, dict) or not isinstance(item.get('content'), str):
                raise ValueError(f'invalid file {path}')
            encoding = item.get('encoding', 'utf-8')
            if encoding not in ('utf-8', 'base64'):
                raise ValueError(f'invalid encoding {path}')
            file = {'path': path, 'content': item['content']}
            if encoding == 'base64':
                file['encoding'] = encoding
            if path in indexed and file_bytes(indexed[path]) != file_bytes(file):
                raise ValueError(f'conflicting file {path}')
            indexed.setdefault(path, file)
        fm = oc.get('frontmatter')
        if fm is not None and not isinstance(fm, dict):
            raise ValueError('invalid frontmatter SKILL.md')
        if fm is not None:
            # Match the fork export semantics: supplied frontmatter wins name/description;
            # trailing body newlines are normalized, while the stored legacy snapshot stays intact.
            fields = {'name': skill_id, 'description': value.get('description') or '', **fm}
            body = value.get('content') or ''
            serialized = (
                '---\n'
                + yaml.safe_dump(fields, allow_unicode=True, sort_keys=False)
                + '---\n\n'
                + re.sub(r'(\r?\n)+$', '', body)
                + '\n'
            )
            root = indexed.get('SKILL.md')
            existing_fields = frontmatter(root['content']) if root and not root.get('encoding') else {}
            if root and existing_fields and root['content'] == body:
                # Canonical content may mirror the root while retaining a legacy bag.
                # Remove the redundant bag only when its explicit fields agree.
                if any(existing_fields.get(key) != item for key, item in fm.items()):
                    raise ValueError('conflicting file SKILL.md')
            else:
                if root and root['content'] != body and file_bytes(root) != serialized.encode('utf-8'):
                    raise ValueError('conflicting file SKILL.md')
                indexed['SKILL.md'] = {'path': 'SKILL.md', 'content': serialized}
        # Existing oversized snapshots are retained, but invalid paths/encodings are never skipped.
        files = validate_files(list(indexed.values()), list(indexed.values()))
    except (ValueError, KeyError, TypeError) as error:
        raise ValueError(f'Skill {skill_id}: {error}') from error
    if 'openclaw' in meta:
        meta['openclaw'] = {key: item for key, item in oc.items() if key not in ('files', 'frontmatter')}
    value['meta'] = meta
    value['data'] = {**data, 'files': files}
    value['content'] = next(file['content'] for file in files if file['path'] == 'SKILL.md')
    return value


def projected_openclaw(skill) -> dict:
    get = skill.get if isinstance(skill, dict) else lambda key, default=None: getattr(skill, key, default)
    meta = get('meta') or {}
    if hasattr(meta, 'model_dump'):
        meta = meta.model_dump()
    oc = deepcopy(meta.get('openclaw') or {})
    data = get('data') or {}
    if isinstance(data.get('files'), list):
        root = next((f for f in data['files'] if f['path'] == 'SKILL.md'), None)
        fields = frontmatter(root['content']) if root else {}
        if (
            isinstance(meta.get('openclaw'), dict)
            or isinstance(fields.get('metadata'), dict)
            and 'openclaw' in fields['metadata']
        ):
            oc['frontmatter'] = fields
            oc['files'] = {
                f['path']: {'content': f['content'], 'encoding': f.get('encoding', 'utf-8')} for f in data['files']
            }
    return oc
