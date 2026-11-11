"""Run native agent turns with terminal attachments and saved conversation state."""

import asyncio
import copy
import hashlib
import logging
from uuid import uuid4

from open_webui.models.chat_messages import AGENT_OUTPUT_INCOMPLETE_ERROR
from open_webui.models.chats import Chats
from open_webui.tasks import tasks as local_tasks
from open_webui.utils.auth import decode_token
from open_webui.utils.chat_id import is_saved_chat_id
from open_webui.utils.terminals import get_terminal_json, upload_files_to_terminal

from .output import Output
from .transport import AgentOutputRetentionError, RemoteCompletionUnconfirmed, TerminalTransport
from . import acp, codex, claude_code, pi

PROVIDERS = {
    'codex': codex.run,
    'claude-code': claude_code.run,
    'cursor': acp.run,
    'grok': acp.run,
    'opencode': acp.run,
    'cline': acp.run,
    'gemini': acp.run,
    'pi': pi.run,
}

log = logging.getLogger(__name__)


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
    if metadata.get('regeneration_message_id') or metadata.get('assistant_message_id'):
        raise ValueError('Continue an agent conversation with a new message')
    agent = model['agent']
    if agent['provider'] not in PROVIDERS:
        raise ValueError('Unsupported terminal agent')
    metadata['terminal_id'] = agent['terminal_id']
    transport = await TerminalTransport.resolve(request, user, metadata)
    terminal_identity = hashlib.sha256(transport.url.encode()).hexdigest()
    output = Output(metadata, user, transport.raise_if_failed, form_data)
    output.state = {**agent, 'terminal_identity': terminal_identity}
    previous = None
    parent = (metadata.get('user_message') or {}).get('parentId')
    if parent:
        previous_message = await Chats.get_message_by_id_and_message_id(metadata['chat_id'], parent)
        token = ((previous_message or {}).get('meta') or {}).get('agent')
        previous = decode_token(token) if isinstance(token, str) else None
        if previous and not (
            previous.get('purpose') == 'terminal-agent'
            and previous.get('user') == user.id
            and previous.get('chat') == metadata['chat_id']
            and all(previous.get(k) == v for k, v in agent.items())
        ):
            previous = None
        if previous:
            previous_identity = previous.get('terminal_identity')
            if previous_identity is None:
                # Existing signed provider sessions may use their original normalized
                # execution's server-owned binding if it matches the signed state.
                prior_execution = await Chats.get_agent_execution(metadata['chat_id'], parent)
                expected = {
                    'chat_id': metadata['chat_id'],
                    'message_id': parent,
                    'user_id': user.id,
                    'terminal_id': agent['terminal_id'],
                    'provider': agent['provider'],
                    'model': agent['model'],
                    'cwd': previous.get('cwd'),
                    'state': 'completed',
                }
                if prior_execution and all(prior_execution.get(key) == value for key, value in expected.items()):
                    previous_identity = prior_execution.get('terminal_identity')
            if previous_identity != terminal_identity:
                raise ValueError('The agent terminal connection changed or could not be verified. Start a new chat.')
        if previous and any(
            child != metadata.get('user_message_id') for child in (previous_message or {}).get('childrenIds', [])
        ):
            raise ValueError('Agent history cannot be branched in place. Start a new chat instead.')
    # Browsing files must not move an existing agent conversation's workspace.
    cwd = (previous or {}).get('cwd') or metadata.get('initial_terminal_cwd')
    if not cwd:
        directory = await get_terminal_json(request, user, metadata, '/files/cwd')
        cwd = (directory or {}).get('cwd')
    if not cwd:
        raise ValueError('Agent working directory is unavailable')
    output.state['cwd'] = cwd
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
        'automation_id': metadata.get('automation_id'),
    }
    if not await Chats.claim_agent_execution(metadata['chat_id'], metadata['message_id'], execution):
        raise ValueError('This chat has an unfinished agent operation; confirm its completion before starting another')
    succeeded = False
    try:
        files = copy.deepcopy(form_data.get('files') or [])
        uploads = [f for f in files if f.get('type') != 'filesystem' and f.get('id')]
        await upload_files_to_terminal(request, user, uploads, metadata, {})
        if any(f.get('terminal_error') for f in uploads):
            raise ValueError('An attachment could not be uploaded to the agent environment')
        paths = [f['terminal_path'] for f in uploads]
        for file in files:
            if file.get('type') == 'filesystem':
                if file.get('terminal_id') != agent['terminal_id']:
                    raise ValueError('Attachment belongs to another terminal')
                paths.append(file['path'])
        messages = form_data.get('messages') or []
        user_message = metadata.get('user_message') or next(
            (m for m in reversed(messages) if m.get('role') == 'user'), {}
        )

        def text(content):
            return (
                content
                if isinstance(content, str)
                else '\n'.join(p.get('text', '') for p in content or [] if p.get('type') == 'text')
            )

        prompt = text(user_message.get('content', ''))
        if not previous or not previous.get('session_id'):
            history = [m for m in messages if m.get('role') in {'user', 'assistant'}]
            if len(history) > 1:
                prompt = '\n\n'.join(f'{m["role"]}: {text(m.get("content", ""))}' for m in history)
        if paths:
            prompt += '\n\nAttached files in this environment:\n' + '\n'.join(paths)
        images = [
            p['image_url']['url']
            for m in [user_message, *messages[-1:]]
            for p in (m.get('content') if isinstance(m.get('content'), list) else [])
            if p.get('type') == 'image_url' and p.get('image_url', {}).get('url', '').startswith('data:image/')
        ]
        images = list(dict.fromkeys(images))
        instructions = '\n\n'.join(text(m.get('content')) for m in messages if m.get('role') == 'system')
        options = {
            'request_id': metadata['message_id'],
            'cwd': cwd,
            'model': agent['model'],
            'resume': (previous or {}).get('session_id'),
            'instructions': instructions,
        }
        await transport.start(agent['provider'], options)
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
        extra = {'auth_method': 'cursor_login'} if agent['provider'] == 'cursor' else {}
        await PROVIDERS[agent['provider']](
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
        if hashlib.sha256(transport.url.encode()).hexdigest() != execution.get('terminal_identity'):
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
