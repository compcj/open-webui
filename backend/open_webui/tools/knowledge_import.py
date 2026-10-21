"""Explicit KB imports return receipts, keeping complete source content server-side."""

import asyncio
import json
import posixpath
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

from fastapi import Request, UploadFile
from starlette.datastructures import Headers

from open_webui.tools.knowledge_write import _check_context, _get_writable_knowledge, _root_filename
from open_webui.utils.knowledge_import_sources import (
    ImportSourceError,
    check_size,
    fetch_terminal_source as _fetch_terminal_source,
    fetch_url_source as _fetch_url_source,
    settle,
    terminal_access as _terminal_access,
    terminal_path,
)


async def _preflight(knowledge_id, user, filename=None):
    from open_webui.internal.db import get_async_db_context
    from open_webui.models.knowledge import Knowledges

    async with get_async_db_context() as db:
        try:
            await _get_writable_knowledge(knowledge_id, user, db)
        except ValueError as exc:
            if str(exc) == 'Knowledge base not found, external, or write access denied':
                raise ImportSourceError(str(exc)) from None
            raise
        if filename is not None:
            files = await Knowledges.get_files_with_directory_ids(knowledge_id, db=db)
            directories = await Knowledges.get_directories(knowledge_id, parent_id=None, db=db)
            names = {file.filename.casefold() for file, directory_id in files if directory_id is None}
            names.update(directory.name.casefold() for directory in directories)
            if filename.casefold() in names:
                raise ImportSourceError(
                    'A file or directory with this root name already exists; choose another filename'
                )


async def _source_filename(source, filename):
    from open_webui.models.config import Config

    try:
        original = _root_filename(source.filename)
        filename = _root_filename(filename) if filename is not None else original
    except ValueError as exc:
        raise ImportSourceError(str(exc)) from None
    extension = posixpath.splitext(original)[1].lower()
    if getattr(source, 'extension_unspecified', False) and filename is not None:
        extension = posixpath.splitext(filename)[1].lower()
    if not extension or posixpath.splitext(filename)[1].lower() != extension:
        raise ImportSourceError('Filename must retain the source file extension')
    document_extensions = {
        'application/pdf': {'.pdf'},
        'application/msword': {'.doc'},
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document': {'.docx'},
        'application/vnd.ms-excel': {'.xls'},
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': {'.xlsx'},
        'application/vnd.ms-powerpoint': {'.ppt'},
        'application/vnd.openxmlformats-officedocument.presentationml.presentation': {'.pptx'},
    }
    expected = document_extensions.get(source.content_type)
    if expected and extension not in expected:
        raise ImportSourceError('Source content type does not match its filename extension')
    allowed = await Config.get('rag.file.allowed_extensions')
    if allowed and extension[1:] not in [item for item in allowed if item]:
        raise ImportSourceError('Source file type is not allowed by the configured file extension policy')
    return filename


async def _stored_state(file_id, knowledge_id):
    from open_webui.internal.db import get_async_db_context
    from open_webui.models.files import Files
    from open_webui.models.knowledge import Knowledges

    async with get_async_db_context() as db:
        file = await Files.get_file_by_id(file_id, db=db)
        members = await Knowledges.get_knowledges_by_file_id(file_id, db=db) if file else []
    text = (file.data or {}).get('content') if file else None
    return file, bool(isinstance(text, str) and text.strip()), any(item.id == knowledge_id for item in members)


async def _ingest(source, knowledge_id, filename, request, user, recheck, receipt, source_metadata, progress):
    from open_webui.internal.db import get_async_db_context
    from open_webui.routers.files import upload_file_handler
    from open_webui.routers.knowledge import KnowledgeFileIdForm, _add_file_to_knowledge
    from open_webui.routers.retrieval import ProcessFileForm, process_file

    await recheck(filename)
    upload = UploadFile(file=source.file, filename=filename, headers=Headers({'content-type': source.content_type}))
    # Obtain a confirmed new ID before parsing/indexing. Never auto-link via upload metadata.
    result = await upload_file_handler(
        request=request,
        file=upload,
        metadata=source_metadata,
        process=False,
        process_in_background=False,
        background_tasks=None,
        user=user,
        db=None,
    )
    file_id = result.get('id') if isinstance(result, dict) else result.id
    if not file_id:
        raise ImportSourceError('Upload did not return a file ID')
    receipt['file_id'] = file_id
    await recheck(filename)
    association_state = {}

    async def before_associate(processing_result):
        if not isinstance(processing_result, dict) or processing_result.get('status') is not True:
            raise ImportSourceError('Knowledge indexing did not return a confirmed processing result')
        progress['kb_index_attempted'] = processing_result.get('collection_name') is not None
        await recheck(filename)
        association_state['indexed'] = processing_result.get('collection_name') == knowledge_id
        association_state['indexing_skipped'] = processing_result.get('collection_name') is None

    async with get_async_db_context() as db:
        await process_file(request=request, form_data=ProcessFileForm(file_id=file_id), user=user, db=db)
    file, content_saved, _ = await _stored_state(file_id, knowledge_id)
    if not content_saved or (file.data or {}).get('status') != 'completed':
        raise ImportSourceError('Source processing did not complete with non-blank extracted text')
    await recheck(filename)
    async with get_async_db_context() as db:
        progress['kb_index_attempted'] = True
        await _add_file_to_knowledge(
            request=request,
            id=knowledge_id,
            form_data=KnowledgeFileIdForm(file_id=file_id, directory_id=None),
            user=user,
            db=db,
            before_associate=before_associate,
        )
    file, content_saved, associated = await _stored_state(file_id, knowledge_id)
    if not associated or not content_saved or (file.data or {}).get('status') != 'completed':
        raise ImportSourceError('Saved knowledge association or processing could not be verified')
    return {
        **receipt,
        'status': 'success',
        'content_saved': True,
        'associated': True,
        'indexed': association_state.get('indexed', False),
        **({'indexing_skipped': True} if association_state.get('indexing_skipped') else {}),
    }


async def _recover(request, user, knowledge_id, receipt, progress):
    from open_webui.routers.files import _cleanup_new_orphan_upload
    from open_webui.routers.retrieval import ASYNC_VECTOR_DB_CLIENT

    file_id = receipt.get('file_id')
    if file_id:
        try:
            file, _, associated = await _stored_state(file_id, knowledge_id)

            async def cleanup_target():
                if not progress.get('kb_index_attempted'):
                    return
                # KB embedding may have succeeded before the final authorization
                # callback rejected membership. The orphan SQL claim must win
                # before deleting chunks; a concurrent association preserves all.
                await ASYNC_VECTOR_DB_CLIENT.delete(collection_name=knowledge_id, filter={'file_id': file_id})
                remaining = await ASYNC_VECTOR_DB_CLIENT.query(
                    collection_name=knowledge_id, filter={'file_id': file_id}
                )
                ids = getattr(remaining, 'ids', None)
                if not isinstance(ids, list) or any(not isinstance(group, list) or group for group in ids):
                    raise ImportSourceError('Target index cleanup could not be verified')

            await _cleanup_new_orphan_upload(request, file_id, user, before_cleanup=cleanup_target)
            file, content_saved, associated = await _stored_state(file_id, knowledge_id)
        except Exception:
            try:
                file, content_saved, associated = await _stored_state(file_id, knowledge_id)
                retained = file is not None
            except Exception:
                content_saved, associated, retained = None, None, None
            return {
                **receipt,
                'status': 'partial_completion',
                'content_saved': content_saved,
                'associated': associated,
                'indexed': False,
                'file_retained': retained,
                'cleanup_verified': False,
                'error': 'Import cleanup could not be verified.',
                'recovery': (
                    'Inspect this retained file ID and its knowledge memberships before recovery.'
                    if retained
                    else 'Ask an administrator to check storage/index cleanup for this file ID before retrying; the upload row is absent.'
                    if retained is False
                    else 'Ask an administrator to verify this file ID and storage/index state before retrying.'
                ),
            }
        if file:
            return {
                **receipt,
                'status': 'partial_completion',
                'content_saved': content_saved,
                'associated': associated,
                'indexed': False,
                'error': 'Import did not finish; the uploaded file is retained.',
                'recovery': 'Inspect this file ID and its knowledge memberships before recovery; do not blindly repeat the import.',
            }
    return {
        **receipt,
        'status': 'error',
        'content_saved': False,
        'associated': False,
        'indexed': False,
        'error': 'Import did not complete; no retained upload was found.',
    }


async def _import(knowledge_id, location, filename, source_type, request, actor, context, metadata):
    source, user = None, None
    progress = {}
    receipt = {'knowledge_id': knowledge_id, 'source_type': source_type}
    try:
        try:
            _check_context(knowledge_id, request, actor, context)
        except ValueError as exc:
            raise ImportSourceError(str(exc)) from None
        if not isinstance(location, str) or not location:
            raise ImportSourceError('Source URL or path must be a non-empty string')
        from open_webui.models.config import Config
        from open_webui.models.users import UserModel

        user = UserModel(**actor)
        metadata = dict(metadata or {})
        terminal_binding = None
        if source_type == 'terminal':
            terminal_path(location)
            if context.get('terminal_enabled') is not True:
                raise ImportSourceError('Terminal capability is disabled for this request')
            if not metadata.get('terminal_id') or not (metadata.get('chat_id') or metadata.get('automation_id')):
                raise ImportSourceError('An active saved terminal and chat or automation context are required')
            terminal_binding = await _terminal_access(request, user, metadata)
        if filename is not None:
            try:
                filename = _root_filename(filename)
            except ValueError as exc:
                raise ImportSourceError(str(exc)) from None
        await _preflight(knowledge_id, user, filename)
        max_mb = await Config.get('rag.file.max_size')
        max_bytes = int(max_mb) * 1024 * 1024 if max_mb else None
        source = (
            await _fetch_url_source(request, location, max_bytes)
            if source_type == 'url'
            else await _fetch_terminal_source(request, user, metadata, location, max_bytes, binding=terminal_binding)
        )
        check_size(source.size, max_bytes)
        filename = await _source_filename(source, filename)
        receipt.update(filename=filename, size=source.size)

        cancelled = asyncio.Event()

        async def recheck(name):
            if cancelled.is_set():
                raise asyncio.CancelledError
            await _preflight(knowledge_id, user, name)
            if source_type == 'terminal':
                if await _terminal_access(request, user, metadata) != terminal_binding:
                    raise ImportSourceError('Selected terminal connection or context changed; start a new import')

        # Shield the whole started persistence pipeline: cancellation is propagated only
        # after storage/parser threads settle, then cleanup can safely inspect durable state.
        parsed = urlsplit(location) if source_type == 'url' else None
        source_metadata = (
            {'source_url': urlunsplit((parsed.scheme, parsed.netloc, parsed.path, '', ''))}
            if parsed
            else {'source_terminal_id': metadata['terminal_id'], 'source_terminal_path': location}
        )
        return json.dumps(
            await settle(
                _ingest(source, knowledge_id, filename, request, user, recheck, receipt, source_metadata, progress),
                on_cancel=cancelled.set,
            )
        )
    except BaseException as exc:
        if user and receipt.get('file_id'):
            recovered = await settle(_recover(request, user, knowledge_id, receipt, progress))
        else:
            error = (
                str(exc)
                if isinstance(exc, ImportSourceError)
                else 'Unable to fetch or import source; check source access and configured loaders.'
            )
            # Only locally-authored validation errors are exposed. Remote exceptions may
            # carry credentials, document text or private network addresses.
            recovered = {**receipt, 'status': 'error', 'error': error}
        if not isinstance(exc, Exception):
            raise
        return json.dumps(recovered)
    finally:
        if source is not None:
            source.file.close()


async def import_knowledge_url(
    knowledge_id: str,
    url: str,
    filename: Optional[str] = None,
    __request__: Request = None,
    __user__: dict = None,
    __knowledge_write_context__: dict = None,
    __metadata__: dict = None,
) -> str:
    """Import one HTTP(S) page or document into an existing writable KB root.

    Fetch and extract complete source content on the server. Return only a compact
    persistence/indexing receipt; source text and bytes never enter tool results.
    Existing root names fail; optional filenames must retain the source extension.
    For a download without a reliable source extension, a filename can identify its type.

    :param knowledge_id: Existing writable knowledge base ID
    :param url: HTTP(S) URL of one page or supported document
    :param filename: Optional safe root filename retaining the source extension (.txt for web pages)
    :return: JSON receipt with saved file ID and honest association/indexing status
    """
    return await _import(
        knowledge_id, url, filename, 'url', __request__, __user__, __knowledge_write_context__, __metadata__
    )


async def import_knowledge_terminal_file(
    knowledge_id: str,
    path: str,
    filename: Optional[str] = None,
    __request__: Request = None,
    __user__: dict = None,
    __knowledge_write_context__: dict = None,
    __metadata__: dict = None,
) -> str:
    """Import one file from the current saved administrator terminal into a writable KB root.

    Use the active actor and chat/automation context. Personal terminals, directory
    imports and overwrites are unavailable. Full bytes/text stay on the server.

    :param knowledge_id: Existing writable knowledge base ID
    :param path: Canonical absolute POSIX file path on the selected terminal
    :param filename: Optional safe root filename retaining the source extension
    :return: Compact JSON receipt; inspect retained file IDs before retrying partial imports
    """
    return await _import(
        knowledge_id, path, filename, 'terminal', __request__, __user__, __knowledge_write_context__, __metadata__
    )


import_knowledge_url.__tool_strict_types__ = True
import_knowledge_terminal_file.__tool_strict_types__ = True
