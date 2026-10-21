"""Explicitly enabled knowledge text mutations, sharing the normal file pipelines."""

import io
import json
import logging
import re
import unicodedata
from pathlib import PurePath
from typing import Optional

from fastapi import Request, UploadFile
from starlette.datastructures import Headers

log = logging.getLogger(__name__)


async def knowledge_write_access(knowledge, user, db=None) -> bool:
    """Report live KB write permission, independently of request tool enablement."""
    if not knowledge or (knowledge.meta or {}).get('source') == 'external':
        return False
    user_id = user.get('id') if isinstance(user, dict) else user.id
    role = user.get('role') if isinstance(user, dict) else user.role
    if role == 'admin' or knowledge.user_id == user_id:
        return True
    from open_webui.models.access_grants import AccessGrants

    return await AccessGrants.has_access(
        user_id=user_id, resource_type='knowledge', resource_id=knowledge.id, permission='write', db=db
    )


def _check_context(knowledge_id, request, user, context):
    if request is None or not user or not user.get('id'):
        raise ValueError('Request and user context are required')
    if not isinstance(context, dict) or context.get('enabled') is not True:
        raise ValueError('Knowledge writing is disabled for this request')
    if not isinstance(knowledge_id, str) or not knowledge_id:
        raise ValueError('knowledge_id is required')
    scope = context.get('knowledge_ids')
    if not isinstance(scope, list) or (scope and knowledge_id not in scope):
        raise ValueError('Knowledge base is outside the attached write scope')


def _root_filename(filename):
    if (
        not isinstance(filename, str)
        or not filename
        or filename != filename.strip()
        or len(filename.encode('utf-8')) > 255
        or filename.startswith('.')
        or filename.endswith('.')
        or any(unicodedata.category(char).startswith('C') or char in '/\\:*?"<>|' for char in filename)
        or re.fullmatch(
            r'CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]|CONIN\$|CONOUT\$|CLOCK\$',
            unicodedata.normalize('NFKC', filename.split('.')[0]).rstrip(' .'),
            re.IGNORECASE,
        )
    ):
        raise ValueError('Invalid root filename')
    return filename


def _filename(filename):
    filename = _root_filename(filename)
    if not PurePath(filename).suffix:
        filename += '.md'
    if len(filename.encode('utf-8')) > 255 or PurePath(filename).suffix.lower() not in ('.md', '.txt'):
        raise ValueError('Only .md and .txt filenames are supported')
    return filename


async def _validate_content(content):
    from open_webui.models.config import Config

    if not isinstance(content, str) or not content.strip():
        raise ValueError('Content must be a non-blank string')
    max_size = await Config.get('rag.file.max_size')
    if max_size and len(content.encode('utf-8')) > int(max_size) * 1024 * 1024:
        raise ValueError(f'Content exceeds the configured {max_size} MB file size limit')


def _replacement(current, content, operations):
    if (content is None) == (operations is None):
        raise ValueError('Provide exactly one of content or operations')
    if operations is None:
        return content
    if not isinstance(operations, list) or not operations:
        raise ValueError('operations must be a non-empty list')
    ranges = []
    for operation in operations:
        if not isinstance(operation, dict) or operation.get('action') != 'replace_range':
            raise ValueError('Each operation must use action replace_range')
        start, end, replacement = operation.get('start'), operation.get('end'), operation.get('content')
        if type(start) is not int or type(end) is not int:
            raise ValueError('Range offsets must be integers, excluding booleans')
        if start < 0 or end < start or end > len(current):
            raise ValueError('Range offsets are out of bounds')
        if not isinstance(replacement, str):
            raise ValueError('Range content must be a string')
        expected = operation.get('expected')
        if start != end and not isinstance(expected, str):
            raise ValueError('Non-empty ranges require matching expected text')
        if expected is not None and (not isinstance(expected, str) or current[start:end] != expected):
            raise ValueError('Expected text does not match current content; read the file again')
        ranges.append((start, end, replacement))
    ranges.sort(key=lambda item: (item[0], item[1]))
    previous = None
    for start, end, _ in ranges:
        if previous and (start < previous[1] or start == previous[0]):
            raise ValueError('Ranges must not overlap or share an ambiguous starting offset')
        previous = (start, end)
    result = current
    for start, end, replacement in reversed(ranges):
        result = result[:start] + replacement + result[end:]
    return result


async def _get_writable_knowledge(knowledge_id, user, db):
    from open_webui.models.knowledge import Knowledges

    knowledge = await Knowledges.get_knowledge_by_id(knowledge_id, db=db)
    if not await knowledge_write_access(knowledge, user, db=db):
        raise ValueError('Knowledge base not found, external, or write access denied')
    return knowledge


async def _cleanup_orphan(request, file_id, user, db):
    """Only the new owned file is eligible; preserve anything associated with a KB."""
    from open_webui.models.files import Files
    from open_webui.models.knowledge import Knowledges
    from open_webui.routers.files import delete_file_by_id

    try:
        file = await Files.get_file_by_id(file_id, db=db)
        if file and file.user_id == user.id and not await Knowledges.get_knowledges_by_file_id(file_id, db=db):
            await delete_file_by_id(request=request, id=file_id, user=user, db=db)
    except Exception:
        log.warning('Unable to clean up new knowledge tool upload %s', file_id, exc_info=True)


async def write_knowledge_file(
    knowledge_id: str,
    filename: str,
    content: str,
    __request__: Request = None,
    __user__: dict = None,
    __knowledge_write_context__: dict = None,
) -> str:
    """Create a UTF-8 .md/.txt file in an existing writable knowledge base root.

    An extensionless filename receives .md. Existing root names are never overwritten.
    No knowledge bases or directories are created.

    :param knowledge_id: Existing writable KB ID from knowledge discovery
    :param filename: Root filename ending in .md or .txt, without a path
    :param content: Non-blank UTF-8 text content
    :return: JSON with success and file_id, or an explicit error
    """
    file_id = None
    try:
        _check_context(knowledge_id, __request__, __user__, __knowledge_write_context__)
        filename = _filename(filename)
        await _validate_content(content)
        from open_webui.internal.db import get_async_db_context
        from open_webui.models.files import Files
        from open_webui.models.knowledge import Knowledges
        from open_webui.models.users import UserModel
        from open_webui.routers.files import upload_file_handler
        from open_webui.routers.knowledge import KnowledgeFileIdForm, add_file_to_knowledge_by_id

        user = UserModel(**__user__)
        async with get_async_db_context() as db:
            await _get_writable_knowledge(knowledge_id, user, db)
            root_files = await Knowledges.get_files_with_directory_ids(knowledge_id, db=db)
            directories = await Knowledges.get_directories(knowledge_id, parent_id=None, db=db)
            names = {file.filename.casefold() for file, directory_id in root_files if directory_id is None}
            names.update(directory.name.casefold() for directory in directories)
            if filename.casefold() in names:
                raise ValueError('A file or directory with this root name already exists; choose another filename')
            upload = UploadFile(
                file=io.BytesIO(content.encode('utf-8')),
                filename=filename,
                headers=Headers(
                    {'content-type': 'text/markdown' if filename.lower().endswith('.md') else 'text/plain'}
                ),
            )
            try:
                result = await upload_file_handler(
                    request=__request__,
                    file=upload,
                    metadata={},
                    process=True,
                    process_in_background=False,
                    background_tasks=None,
                    user=user,
                    db=db,
                )
                file_id = result.get('id') if isinstance(result, dict) else result.id
                if not file_id:
                    raise ValueError('Upload did not return a file ID')
                # The upload helper can return its stale pending model even when processing failed.
                file = await Files.get_file_by_id(file_id, db=db)
                data = file.data if file else {}
                if (
                    not data
                    or data.get('status') != 'completed'
                    or not isinstance(data.get('content'), str)
                    or not data['content'].strip()
                ):
                    raise ValueError('Uploaded file processing did not complete')
                await _get_writable_knowledge(knowledge_id, user, db)
                await add_file_to_knowledge_by_id(
                    request=__request__,
                    id=knowledge_id,
                    form_data=KnowledgeFileIdForm(file_id=file_id, directory_id=None),
                    user=user,
                    db=db,
                )
                file = await Files.get_file_by_id(file_id, db=db)
                members = await Knowledges.get_knowledges_by_file_id(file_id, db=db)
                if (
                    not file
                    or (file.data or {}).get('status') != 'completed'
                    or not (file.data or {}).get('content', '').strip()
                    or not any(base.id == knowledge_id for base in members)
                ):
                    raise ValueError('File was uploaded but knowledge association or indexing did not complete')
                return json.dumps(
                    {'status': 'success', 'knowledge_id': knowledge_id, 'file_id': file_id, 'filename': filename}
                )
            except Exception:
                if file_id:
                    await _cleanup_orphan(__request__, file_id, user, db)
                raise
            finally:
                await upload.close()
    except Exception as exc:
        error = str(exc.detail) if hasattr(exc, 'detail') else str(exc)
        return json.dumps({'error': error, **({'file_id': file_id} if file_id else {})})


async def replace_knowledge_file_content(
    knowledge_id: str,
    file_id: str,
    content: Optional[str] = None,
    operations: Optional[list[dict]] = None,
    __request__: Request = None,
    __user__: dict = None,
    __knowledge_write_context__: dict = None,
) -> str:
    """Replace extracted retrieval text in a KB file, retaining original upload bytes.

    Provide exactly one of content or operations. Ranges address original, zero-based
    character offsets with an exclusive end. Non-empty ranges require matching expected
    text. Ranges cannot overlap or share a starting offset. An empty EOF range appends.
    If text is saved but indexing fails, read current text before deciding how to recover;
    repeating an append can duplicate content. Other KBs sharing the file are reindexed.

    :param knowledge_id: Writable KB currently containing the file
    :param file_id: Existing file ID from KB discovery, including extracted PDF/Word text
    :param content: Whole replacement text, excluding operations
    :param operations: [{"action":"replace_range","start":0,"end":3,"content":"new","expected":"old"}]
    :return: JSON success, error, or partial_completion; indexing_skipped marks configured retrieval bypass
    """
    try:
        _check_context(knowledge_id, __request__, __user__, __knowledge_write_context__)
        from open_webui.internal.db import get_async_db_context
        from open_webui.models.config import Config
        from open_webui.models.files import Files
        from open_webui.models.knowledge import Knowledges
        from open_webui.models.users import UserModel
        from open_webui.routers.files import ContentForm, update_file_data_content_by_id
        from open_webui.utils.access_control.files import has_access_to_file

        user = UserModel(**__user__)
        async with get_async_db_context() as db:
            await _get_writable_knowledge(knowledge_id, user, db)
            file = await Files.get_file_by_id(file_id, db=db)
            members = await Knowledges.get_knowledges_by_file_id(file_id, db=db)
            if not file or not any(base.id == knowledge_id for base in members):
                raise ValueError('File is not a member of this knowledge base')
            if (
                user.role != 'admin'
                and file.user_id != user.id
                and not await has_access_to_file(file_id, 'write', user, db=db)
            ):
                raise ValueError('File write access denied')
            current = (file.data or {}).get('content')
            if not isinstance(current, str):
                raise ValueError('Processed file text is unavailable; read the file again')
            updated = _replacement(current, content, operations)
            await _validate_content(updated)
            indexing_skipped = bool(await Config.get('rag.bypass_embedding_and_retrieval', False))
            try:
                await update_file_data_content_by_id(
                    request=__request__, id=file_id, form_data=ContentForm(content=updated), user=user, db=db
                )
            except Exception:
                # Pre-existing equal text does not establish that this invocation saved it.
                if updated == current:
                    raise
                saved = await Files.get_file_by_id(file_id, db=db)
                if saved and (saved.data or {}).get('content') == updated:
                    return json.dumps(
                        {
                            'status': 'partial_completion',
                            'file_id': file_id,
                            'knowledge_id': knowledge_id,
                            'content_saved': True,
                            'indexed': False,
                            'error': 'Text is saved but indexing did not complete. Read the current file before recovery; do not repeat append operations blindly.',
                        }
                    )
                raise
            saved = await Files.get_file_by_id(file_id, db=db)
            if not saved or (saved.data or {}).get('content') != updated:
                raise ValueError('File update could not be verified; read the current file before retrying')
            return json.dumps(
                {
                    'status': 'success',
                    'knowledge_id': knowledge_id,
                    'file_id': file_id,
                    'content_saved': True,
                    'indexed': not indexing_skipped,
                    **({'indexing_skipped': True} if indexing_skipped else {}),
                }
            )
    except Exception as exc:
        return json.dumps({'error': str(exc.detail) if hasattr(exc, 'detail') else str(exc), 'file_id': file_id})


# Mutations validate original provider argument types. The shared binding helper's
# legacy number/string coercion must not silently turn invalid content into a write.
write_knowledge_file.__tool_strict_types__ = True
replace_knowledge_file_content.__tool_strict_types__ = True
