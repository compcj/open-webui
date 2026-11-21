"""Run native agent turns with terminal attachments and saved conversation state."""

import asyncio
import copy
import hashlib
import logging
from uuid import uuid4

from open_webui.models.chat_messages import AGENT_OUTPUT_INCOMPLETE_ERROR
from open_webui.models.chats import Chats
from open_webui.tasks import tasks as local_tasks
from open_webui.models.config import Config
from open_webui.utils.misc import get_message_list
from open_webui.utils.chat_id import is_saved_chat_id
from open_webui.utils.terminals import get_terminal_json, upload_files_to_terminal

from .session import branch_digest, get_resume_point
from .output import Output
from .transport import AgentOutputRetentionError, RemoteCompletionUnconfirmed, TerminalTransport
from . import acp, codex, claude_code, pi

log = logging.getLogger(__name__)

PROVIDERS = {
    'codex': codex.run,
    'claude-code': claude_code.run,
    'cursor': acp.run,
    'grok': acp.run,
    'opencode': acp.run,
    'cline': acp.run,
    'gemini': acp.run,
    'pi': pi.run,
    'hermes': acp.run,
    'antigravity': acp.run,
    'copilot': acp.run,
    'goose': acp.run,
    'qwen': acp.run,
}


# Providers that can continue their native conversation from an earlier turn.
RESUMABLE_AT = {'codex', 'claude-code'}


def provider_runner(provider):
    # Agents configured in Open Terminal's OPEN_TERMINAL_ACP_AGENTS arrive as `acp-<id>`.
    return PROVIDERS.get(provider) or (acp.run if provider.startswith('acp-') else None)


async def confirm_remote_completion(transport, output, metadata, *, succeeded):
    """Keep the owning task alive until the executor and durable state agree."""
    task = asyncio.current_task()
    if task is not None:
        task._agent_cleanup_running = True
    notified = False
    incomplete = not succeeded
    identity = (metadata['chat_id'], metadata['message_id'], metadata['task_id'])
    attempt_id = uuid4().hex
    while True:
        try:
            execution = await Chats.get_agent_execution(metadata['chat_id'], metadata['message_id']) or {}
            if execution.get('task_id') != metadata['task_id']:
                raise RemoteCompletionUnconfirmed('Agent execution identity changed')
            if execution.get('stop_recovering'):
                # The recovery worker owns publication. Detach so it can retain
                # the remaining remote frames without racing this reader.
                await transport.stop_receiving(retain=False)
                if execution.get('state') == 'completed':
                    return
                await asyncio.sleep(2)
                continue
            if execution.get('state') == 'completed':
                incomplete = incomplete or execution.get('output_incomplete') is True
                # Another worker may have obtained the executor's stop acknowledgement.
                transport.completion_confirmed = True
                await transport.close()
                break
            # Stop intake and retain any in-flight frame before sealing the
            # journal and publishing the normalized output.
            await transport.stop_receiving()
            incomplete = incomplete or transport.output_capacity_reached
            for item in output.items:
                if item.get('status') in {'pending', 'queued', 'in_progress', 'requires_approval'}:
                    item['status'] = (
                        'completed' if not incomplete and item['status'] != 'requires_approval' else 'incomplete'
                    )
            await output.flush(done=False, force=True, persist=True)
            if not await Chats.update_agent_execution(
                metadata['chat_id'],
                metadata['message_id'],
                metadata['task_id'],
                state='stopping',
                output_published=True,
                output_incomplete=incomplete,
                output_cursor=transport.cursor,
            ):
                raise RemoteCompletionUnconfirmed('Agent execution record is unavailable')
            execution = await Chats.acquire_agent_stop(*identity, attempt_id)
            if not execution:
                await asyncio.sleep(2)
                continue
            try:
                if execution.get('remote_completed') is True:
                    transport.completion_confirmed = True
                await transport.close()
                # Only the bound executor's confirmed stop releases the reply.
                if not await Chats.update_agent_execution(
                    *identity,
                    state='completed',
                    remote_completed=True,
                    complete_reply=True,
                    stop_attempt_id=attempt_id,
                ):
                    raise RemoteCompletionUnconfirmed('Agent completion could not be recorded')
            finally:
                await Chats.update_agent_execution(*identity, stop_attempt_id=attempt_id, release_stop_attempt=True)
            break
        except asyncio.CancelledError:
            # A repeated stop or a drained-worker shutdown cannot skip confirmation.
            continue
        except Exception as error:
            log.warning('Agent remote completion remains unconfirmed (%s)', type(error).__name__)
            try:
                owns_reply = await Chats.update_agent_execution(
                    metadata['chat_id'], metadata['message_id'], metadata['task_id'], state='unconfirmed'
                )
                if owns_reply:
                    await output.flush(done=False, force=True, persist=True)
                if owns_reply and not notified and output.emitter:
                    if isinstance(error, AgentOutputRetentionError):
                        notice = {
                            'content': 'Agent output could not be retained. Restore output storage capacity before retrying.',
                            'code': 'agent_output_retention_blocked',
                        }
                    else:
                        notice = {
                            'content': 'Agent stop is unconfirmed. Reconnect the terminal and retry stopping.',
                            'code': 'agent_stop_unconfirmed',
                        }
                    await output.emitter({'type': 'chat:completion', 'data': {'done': False, 'error': notice}})
                    notified = True
            except asyncio.CancelledError:
                continue
            except Exception:
                pass  # Keep the task/stop intent active while persistence is unavailable.
            try:
                await asyncio.sleep(2)
            except asyncio.CancelledError:
                continue

    for item in output.items:
        if item.get('status') in {'pending', 'queued', 'in_progress', 'requires_approval'}:
            item['status'] = 'completed' if not incomplete and item['status'] != 'requires_approval' else 'incomplete'
    await output.flush(done=True, force=True, error=AGENT_OUTPUT_INCOMPLETE_ERROR if incomplete else None)


async def run(request, form_data, user, metadata, model):
    if not is_saved_chat_id(metadata.get('chat_id')):
        raise ValueError('Agent conversations require a saved chat')
    if metadata.get('assistant_message_id'):
        raise ValueError('Continue an agent conversation with a new message')
    agent = model['agent']
    run_provider = provider_runner(agent['provider'])
    if run_provider is None:
        raise ValueError('Unsupported terminal agent')
    chat = await Chats.get_accessible_chat_by_id(metadata['chat_id'], user, permission='write')
    if chat is None:
        raise ValueError('Chat reply access denied')
    saved_messages = await Chats.get_messages_map_by_chat_id(chat.id) or {}
    saved_reply = saved_messages.get(metadata['message_id']) or {}
    current_user_id = saved_reply.get('parentId')
    current = saved_messages.get(current_user_id) or {}
    incoming = metadata.get('user_message') or {}
    if (
        saved_reply.get('role') != 'assistant'
        or (saved_reply.get('user_id') or chat.user_id) != user.id
        or current.get('role') != 'user'
        or incoming.get('id', current_user_id) != current_user_id
        or incoming.get('parentId', current.get('parentId')) != current.get('parentId')
    ):
        raise ValueError('The saved Agent reply no longer belongs to this message and branch')
    parent = current.get('parentId')
    previous, branched = (
        await get_resume_point(chat, parent, user.id, exclude=metadata.get('message_id')) if parent else (None, False)
    )
    selected_cwd = (chat.chat or {}).get('working_directory')
    if previous and (
        any(previous[key] != agent[key] for key in ('terminal_id', 'provider'))
        or selected_cwd
        and selected_cwd != previous['cwd']
    ):
        previous = None
    metadata['terminal_id'] = agent['terminal_id']
    transport = await TerminalTransport.resolve(request, user, metadata)
    terminal_identity = hashlib.sha256(transport.url.encode()).hexdigest()
    terminal_instance = transport.terminal_instance
    if previous and (
        previous['terminal_identity'] != terminal_identity
        or previous.get('terminal_context') != transport.terminal_context
        or previous.get('terminal_instance') != terminal_instance
    ):
        # This target is authorized for the actor, but the old conversation belongs to another instance.
        previous = None
    fork = bool(previous and (branched or metadata.get('regeneration_message_id')))
    if fork and (agent['provider'] not in RESUMABLE_AT or not previous.get('turn')):
        previous, fork = None, False
    resume_at = previous['turn'] if fork else None
    cwd = (previous or {}).get('cwd') or selected_cwd or metadata.get('initial_terminal_cwd')
    if not cwd:
        directory = await get_terminal_json(request, user, metadata, '/files/cwd')
        cwd = (directory or {}).get('cwd')
    if not isinstance(cwd, str) or not cwd:
        raise ValueError('Agent working directory is unavailable')
    if metadata.get('automation_id') and model.get('legacy_id') and model['legacy_id'] != model['id']:
        from .references import migrate_automation_reference

        await migrate_automation_reference(
            user.id, metadata['automation_id'], agent['terminal_id'], cwd, model['legacy_id'], model['id']
        )
    output = Output(metadata, user, transport.raise_if_failed, form_data)
    output.state = {
        **{k: previous[k] for k in ('model_config', *(() if fork else ('session_id',))) if previous and k in previous},
        **agent,
        'cwd': cwd,
        'instance_user_id': user.id,
        'terminal_identity': terminal_identity,
        'terminal_context': transport.terminal_context,
        'terminal_instance': terminal_instance,
        'checkpoint_message_id': metadata['message_id'],
        'source_history_digest': branch_digest(saved_messages, current_user_id) if current_user_id else None,
    }
    mode = (metadata.get('params') or {}).get('tool_approval_mode', 'full')
    output.native_auto = (
        mode == 'auto'
        and agent['provider'] in {'codex', 'claude-code', 'grok'}
        and await Config.get('chat.tool_permissions.auto.native_agent_reviewer', 'webui') == 'native'
    )
    execution = {
        'state': 'starting',
        'user_id': user.id,
        'chat_id': metadata['chat_id'],
        'task_id': metadata['task_id'],
        'terminal_id': agent['terminal_id'],
        'provider': agent['provider'],
        'model': agent['model'],
        'cwd': cwd,
        'terminal_identity': terminal_identity,
        'terminal_context': transport.terminal_context,
        'automation_id': metadata.get('automation_id'),
        'terminal_instance': terminal_instance,
    }
    if not await Chats.claim_agent_execution(metadata['chat_id'], metadata['message_id'], execution):
        raise ValueError('This chat has an unfinished agent operation; confirm its completion before starting another')
    succeeded = False
    try:
        messages = form_data.get('messages') or []
        user_message = copy.deepcopy(current)
        files = [file for file in user_message.get('files') or [] if file in (incoming.get('files') or [])]
        uploads = [
            f
            for f in files
            if f.get('id') and f.get('type') in {None, 'file', 'image', 'doc', 'text', 'audio', 'video'}
        ]
        if uploads:
            await transport.authorize_input()
            await upload_files_to_terminal(
                request, user, uploads, metadata, {}, directory=cwd, authorize=transport.authorize_upload
            )
        if any(f.get('terminal_error') or not f.get('terminal_path') for f in uploads):
            raise ValueError('An attachment could not be uploaded to the agent environment')
        paths = [f['terminal_path'] for f in uploads]
        for file in files:
            if file.get('type') == 'filesystem':
                if file.get('terminal_id') != agent['terminal_id']:
                    raise ValueError('Attachment belongs to another terminal')
                await transport.authorize_input()
                paths.append(file['path'])

        def text(content):
            return (
                content
                if isinstance(content, str)
                else '\n'.join(p.get('text', '') for p in content or [] if p.get('type') == 'text')
            )

        prompt = text(user_message.get('content', ''))
        if not previous or not previous.get('session_id'):
            # A new native conversation starts from this branch's transcript. Saved chats send only
            # the system prompt, so the branch is read from the stored messages.
            stored = get_message_list(saved_messages, parent) if parent else []
            history = [m for m in stored if m.get('role') in {'user', 'assistant'}]
            history.append(user_message)
            if len(history) > 1:
                prompt = '\n\n'.join(f'{m["role"]}: {text(m.get("content", ""))}' for m in history)
        if metadata.get('regeneration_message_id') and form_data.get('regeneration_prompt'):
            # Guided regeneration: the agent revises a reply its branched conversation no longer holds.
            regenerated = await Chats.get_message_by_id_and_message_id(chat.id, metadata['regeneration_message_id'])
            if (
                not regenerated
                or regenerated.get('role') != 'assistant'
                or regenerated.get('parentId') != user_message.get('id')
            ):
                raise ValueError('The reply being regenerated is outside the current turn')
            earlier = text(regenerated.get('content', ''))
            if earlier:
                prompt += f'\n\nYour previous reply to this was:\n<previous_reply>\n{earlier}\n</previous_reply>'
            prompt += f'\n\n{form_data["regeneration_prompt"]}'
        if paths:
            prompt += '\n\nAttached files in this environment:\n' + '\n'.join(paths)
        # Convert images only after narrowing to the explicit files and content of this message.
        content = user_message.get('content') if isinstance(user_message.get('content'), list) else []
        image_parts = [
            part for part in content if isinstance(part, dict) and part.get('type') in {'image_url', 'input_image'}
        ]
        image_parts.extend(
            {'type': 'image_url', 'image_url': {'url': f['url']}}
            for f in files
            if f.get('type') == 'image' and isinstance(f.get('url'), str)
        )
        images = []
        if image_parts:
            from open_webui.utils.middleware import convert_url_images_to_base64

            current = {'role': 'user', 'content': image_parts}
            await convert_url_images_to_base64({'messages': [current]}, user=user)
            for part in current['content']:
                image = part.get('image_url')
                url = image.get('url') if isinstance(image, dict) else image
                if isinstance(url, str) and url.startswith('data:image/') and url not in images:
                    images.append(url)
        instructions = '\n\n'.join(text(m.get('content')) for m in messages if m.get('role') == 'system')
        options = {
            'request_id': metadata['message_id'],
            'cwd': cwd,
            'model': agent['model'],
            'resume': (previous or {}).get('session_id'),
            'resume_at': resume_at,
            'fork': fork,
            'instructions': instructions,
            'approval_mode': 'ask' if mode == 'auto' and not output.native_auto else mode,
        }
        session = await transport.start(agent['provider'], options)
        if not await Chats.update_agent_execution(
            metadata['chat_id'],
            metadata['message_id'],
            metadata['task_id'],
            state='running',
            session_id=transport.session_id,
        ):
            raise RemoteCompletionUnconfirmed('Agent execution could not be recorded')

        async def retain_output(sequence, data):
            return await Chats.append_agent_output_frame(
                metadata['chat_id'], metadata['message_id'], metadata['task_id'], transport.session_id, sequence, data
            )

        transport.start_receiving(retain_output)
        if (
            agent['provider'] == 'claude-code'
            and (resume_at or fork)
            and (session.get('resume_at') != resume_at or bool(session.get('fork')) != fork)
        ):
            # An older Open Terminal would resume the latest turn of the original conversation instead.
            raise ValueError('Update Open Terminal to regenerate or edit earlier messages in this chat')
        extra = {'auth_method': 'cursor_login'} if agent['provider'] == 'cursor' else {}
        await run_provider(
            transport,
            output,
            prompt,
            images,
            {**options, 'resume_state': previous or {}, 'params': form_data.get('params') or {}},
            **extra,
        )
        succeeded = True
    except Exception:
        if not transport.output_capacity_reached:
            current = await Chats.get_agent_execution(metadata['chat_id'], metadata['message_id']) or {}
            if current.get('task_id') != metadata['task_id'] or not current.get('stop_recovering'):
                raise
        # Cleanup publishes the incomplete reply for a capacity or recovery stop.
    finally:
        await confirm_remote_completion(transport, output, metadata, succeeded=succeeded)
    return {'status': True, 'chat_id': metadata['chat_id']}


async def retry_agent_stop(request, user, execution):
    """Force-stop the bound session while preserving available output and explicit uncertainty."""
    if execution.get('user_id') != user.id:
        return {'status': False, 'message': 'Only the reply author may stop this agent.'}
    identity = tuple(execution[key] for key in ('chat_id', 'message_id', 'task_id'))
    chat_id, message_id, task_id = identity
    pending = {'status': False, 'message': 'Remote agent stop is unconfirmed. Reconnect and retry stopping.'}
    if not await Chats.update_agent_execution(*identity, state='stopping'):
        return pending
    attempt_id = uuid4().hex
    acquired = False
    try:
        execution = await Chats.get_agent_execution(chat_id, message_id) or {}
        if (
            execution.get('task_id') != task_id
            or execution.get('user_id') != user.id
            or not execution.get('session_id')
        ):
            return pending
        recover_output = execution.get('output_journal_version') == 1 and execution.get('output_published') is not True
        # A local owner can still retain a frame currently in its memory.
        local_task = local_tasks.get(task_id)
        if recover_output and local_task and not local_task.done() and not execution.get('stop_recovering'):
            return pending
        transport = await TerminalTransport.resolve(
            request,
            user,
            {
                'chat_id': chat_id,
                'terminal_id': execution['terminal_id'],
                'automation_id': execution.get('automation_id'),
            },
        )
        if (
            hashlib.sha256(transport.url.encode()).hexdigest() != execution.get('terminal_identity')
            or ('terminal_context' in execution and execution['terminal_context'] != transport.terminal_context)
            or execution.get('terminal_instance') != transport.terminal_instance
        ):
            return pending
        transport.session_id = execution['session_id']
        execution = await Chats.acquire_agent_stop(*identity, attempt_id, recover_unpublished=recover_output)
        if not execution:
            return pending
        acquired = True
        if recover_output and execution.get('output_published') is not True:
            transport.cursor = execution['output_cursor']
            if not execution.get('output_capacity_reached'):

                async def retain_output(sequence, data):
                    return await Chats.append_agent_output_frame(
                        *identity, transport.session_id, sequence, data, stop_attempt_id=attempt_id
                    )

                if not await transport.drain_available_output(retain_output, transport.cursor):
                    return pending
            if not await Chats.update_agent_execution(
                *identity,
                output_published=True,
                output_cursor=transport.cursor,
                output_incomplete=True,
                stop_attempt_id=attempt_id,
            ):
                return pending
            # Refresh the DELETE lease after the bounded drain and its SQL writes.
            execution = await Chats.acquire_agent_stop(*identity, attempt_id)
            if not execution:
                return pending
        if execution.get('remote_completed') is True:
            transport.completion_confirmed = True
        await transport.close()
        if execution.get('output_published') is not True:
            # Historical executions can record a remote ACK independently.
            await Chats.update_agent_execution(*identity, remote_completed=True, stop_attempt_id=attempt_id)
            return pending
        if not await Chats.update_agent_execution(
            *identity,
            state='completed',
            remote_completed=True,
            complete_reply=True,
            stop_attempt_id=attempt_id,
        ):
            return pending
        if execution.get('output_incomplete') is True:
            return {'status': True, 'message': AGENT_OUTPUT_INCOMPLETE_ERROR['content']}
        return {'status': True, 'message': 'Remote executor confirmed the agent has stopped.'}
    except Exception:
        current = await Chats.get_agent_execution(chat_id, message_id) or {}
        if current.get('task_id') == task_id and current.get('state') == 'completed':
            return {'status': True, 'message': 'Remote executor confirmed the agent has stopped.'}
        if acquired:
            await Chats.update_agent_execution(*identity, state='unconfirmed', stop_attempt_id=attempt_id)
        return pending
    finally:
        if acquired:
            try:
                await Chats.update_agent_execution(*identity, stop_attempt_id=attempt_id, release_stop_attempt=True)
            except Exception:
                pass  # A lost release leaves the attempt fenced until its deadline.
