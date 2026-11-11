"""Reconnectable carrier for native agent JSONL, through direct terminals or Tether."""

import asyncio
import contextlib
import hashlib
import json
import time

import aiohttp
from fastapi import HTTPException

from open_webui.env import AIOHTTP_CLIENT_SESSION_TOOL_SERVER_SSL
from open_webui.utils.terminals import TERMINAL_CONTEXT_HEADER, get_terminal_server_url, resolve_terminal_connection
from open_webui.utils.tether import get_terminal_client

from .access import authorize_agent_terminal


class RemoteCompletionUnconfirmed(RuntimeError):
    """The executing terminal has not confirmed completion; retain stop intent."""


class AgentOutputRetentionError(RuntimeError):
    """A received frame has no durable acknowledgement yet."""


class TerminalTransport:
    def __init__(self, connection, headers, cookies, user_id):
        self.url = get_terminal_server_url(connection)
        self.headers, self.cookies, self.user_id = headers, cookies, user_id
        self.terminal_identity = hashlib.sha256(self.url.encode()).hexdigest()
        self.terminal_context = headers.get(TERMINAL_CONTEXT_HEADER)
        self.authorization = None
        self.session_id = None
        self.socket = None
        self.task = None
        self.cursor = 0
        self.output_journal = None
        self.pending_output = None
        self.output_capacity_reached = False
        self.recovering_output = False
        self.active_peer = False
        self.sequence = 0
        self.pending = {}
        self.messages = asyncio.Queue(maxsize=128)
        self.lock = asyncio.Lock()
        self.ready = asyncio.Event()
        self.closed = False
        self.start_attempted = False
        self.completion_confirmed = False
        self.failure = None

    @classmethod
    async def resolve(cls, request, user, metadata):
        resolved = await resolve_terminal_connection(request, user, metadata)
        if not resolved:
            raise ValueError('Select an accessible server or Tether terminal to use agents')
        transport = cls(*resolved, user.id)
        authorization_metadata = dict(metadata)
        if isinstance(metadata.get('user_message'), dict):
            authorization_metadata['user_message'] = dict(metadata['user_message'])
        transport.authorization = (
            request,
            user,
            authorization_metadata,
        )
        return transport

    async def authorize_input(self):
        if self.authorization is None or self.recovering_output:
            raise HTTPException(403, 'This agent transport cannot accept new input.')
        _, headers, cookies = await authorize_agent_terminal(
            *self.authorization,
            self.terminal_identity,
            terminal_context=self.terminal_context,
        )
        # Use current credentials only after checking the original URL and context.
        # If access is later revoked, retained credentials still allow bound cleanup.
        self.headers, self.cookies = headers, cookies

    async def request(self, method, path, body=None):
        async with get_terminal_client(
            self.url, self.user_id, timeout=aiohttp.ClientTimeout(total=30), trust_env=True
        ) as client:
            async with client.request(
                method,
                self.url + path,
                headers=self.headers,
                cookies=self.cookies,
                json=body,
                ssl=AIOHTTP_CLIENT_SESSION_TOOL_SERVER_SSL,
                allow_redirects=False,
            ) as response:
                data = await response.json()
                if not 200 <= response.status < 300:
                    raise ValueError(data.get('detail', 'Agent environment request failed'))
                return data

    async def start(self, provider, options):
        await self.authorize_input()
        # A lost POST response may still have started a remote process.
        self.start_attempted = True
        session = await self.request('POST', f'/agents/{provider}/sessions', options)
        self.session_id = session['id']
        return session

    def start_receiving(self, output_journal):
        self.output_journal = output_journal
        self.task = asyncio.create_task(self._pump())

    async def retain_pending_output(self):
        if self.pending_output is None:
            return
        sequence, data = self.pending_output
        try:
            retained = await self.output_journal(sequence, data)
        except Exception as error:
            raise AgentOutputRetentionError('Agent output storage is unavailable') from error
        if not retained:
            raise AgentOutputRetentionError('Agent output could not be retained')
        self.cursor = sequence
        self.pending_output = None
        if retained == 'capacity':
            self.output_capacity_reached = True

    async def drain_available_output(self, output_journal, cursor):
        # A bounded read-only attempt to retain more of the remote tail before
        # force-stop. The protocol does not prove a live queue has drained.
        self.cursor = cursor
        self.recovering_output = True
        self.start_receiving(output_journal)
        try:
            await asyncio.wait({self.task}, timeout=1)
        finally:
            await self.stop_receiving()
        return not self.active_peer

    async def _send(self, frame):
        encoded = json.dumps(frame)
        if len(encoded.encode()) > 16 * 1024 * 1024:
            raise ValueError('Agent protocol frame is too large')
        if self.socket is None:
            raise ConnectionError('Agent socket disconnected')
        if frame.get('type') == 'input':
            # Covers ordinary input, automatic approval and each replayed frame.
            # ACKs and EOF retain output / stop input; they do not authorize work.
            await self.authorize_input()
        await self.socket.send_str(encoded)

    async def _pump(self):
        disconnected = time.monotonic()
        try:
            while not self.closed:
                try:
                    if not self.recovering_output:
                        await self.authorize_input()
                    async with get_terminal_client(
                        self.url,
                        self.user_id,
                        timeout=aiohttp.ClientTimeout(total=None, connect=30),
                        trust_env=True,
                        cookies=self.cookies,
                    ) as client:
                        async with client.ws_connect(
                            self.url + f'/agents/sessions/{self.session_id}/connect',
                            headers=self.headers,
                            ssl=AIOHTTP_CLIENT_SESSION_TOOL_SERVER_SSL,
                            max_msg_size=20 * 1024 * 1024,
                            heartbeat=20,
                        ) as ws:
                            async with self.lock:
                                if not self.recovering_output:
                                    await self.authorize_input()
                                self.socket = ws
                                token = self.headers.get('Authorization', '').removeprefix('Bearer ')
                                await self._send({'type': 'auth', 'token': token, 'cursor': self.cursor})
                                if not self.recovering_output:
                                    for sequence, (data, _) in self.pending.items():
                                        await self._send({'type': 'input', 'sequence': sequence, 'data': data})
                                self.ready.set()
                            async for message in ws:
                                if message.type != aiohttp.WSMsgType.TEXT:
                                    raise ConnectionError('Agent socket disconnected')
                                frame = json.loads(message.data)
                                disconnected = time.monotonic()
                                if frame['type'] == 'ack':
                                    pending = self.pending.pop(frame['sequence'], None)
                                    if pending:
                                        pending[1].set_result(None)
                                elif frame['type'] == 'output':
                                    sequence = frame['sequence']
                                    if type(sequence) is not int or sequence < 1 or sequence > self.cursor + 1:
                                        raise ValueError('Agent protocol output sequence is invalid')
                                    if sequence > self.cursor:
                                        data = frame['data']
                                        self.pending_output = (sequence, data)
                                        # ACK and reconnect cursors only advance after the exact frame commits.
                                        await self.retain_pending_output()
                                        if self.output_capacity_reached:
                                            raise AgentOutputRetentionError(
                                                'Agent reached its configured output capacity'
                                            )
                                        if not self.recovering_output:
                                            await self.messages.put(json.loads(data))
                                    await self._send({'type': 'ack', 'sequence': self.cursor})
                                elif frame['type'] == 'exit':
                                    if self.recovering_output:
                                        return
                                    raise ValueError(frame.get('error') or f'Agent exited ({frame.get("code")})')
                            if self.recovering_output and ws.close_code == 4409:
                                self.active_peer = True
                                return
                            raise ConnectionError('Agent socket disconnected')
                except (ConnectionError, aiohttp.ClientError, TimeoutError, HTTPException) as exc:
                    if isinstance(exc, HTTPException) and exc.status_code not in {404, 503}:
                        raise
                    self.ready.clear()
                    self.socket = None
                    if time.monotonic() - disconnected > 60:
                        raise ConnectionError('Agent environment disconnected. Reconnect before continuing this chat.')
                    await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.failure = exc
            self.ready.set()
            for _, future in self.pending.values():
                if not future.done():
                    future.set_exception(exc)
            await self.messages.put(exc)

    def raise_if_failed(self):
        if self.failure:
            raise self.failure

    async def write(self, data):
        if len(data.encode()) > 16 * 1024 * 1024:
            raise ValueError('Agent input is too large')
        await asyncio.wait_for(self.ready.wait(), 65)
        self.raise_if_failed()
        async with self.lock:
            self.sequence += 1
            sequence = self.sequence
            future = asyncio.get_running_loop().create_future()
            self.pending[sequence] = (data, future)
            try:
                await self._send({'type': 'input', 'sequence': sequence, 'data': data})
            except (ConnectionError, aiohttp.ClientError):
                if self.socket:
                    await self.socket.close()
            except Exception:
                self.pending.pop(sequence, None)
                future.cancel()
                raise
        try:
            await asyncio.wait_for(asyncio.shield(future), 65)
        finally:
            self.pending.pop(sequence, None)
            if not future.done():
                future.cancel()

    async def read_messages(self):
        while True:
            message = await self.messages.get()
            if isinstance(message, Exception):
                raise message
            yield message

    async def stop_receiving(self, *, retain=True):
        self.closed = True
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        # Cancellation may interrupt a write or arrive after it committed. The
        # same sequence and bytes can be retried without duplicating the record.
        if retain:
            await self.retain_pending_output()

    async def close(self):
        await self.stop_receiving()
        for _, future in self.pending.values():
            if not future.done():
                future.cancel()
        if self.completion_confirmed:
            return True
        if self.session_id:
            try:
                result = await self.request('DELETE', f'/agents/sessions/{self.session_id}')
            except Exception as error:
                raise RemoteCompletionUnconfirmed(
                    'Remote agent stop is unconfirmed; retry stopping after reconnecting'
                ) from error
            if not isinstance(result, dict) or result.get('status') is not True:
                raise RemoteCompletionUnconfirmed('Remote agent did not confirm process termination')
        elif self.start_attempted:
            raise RemoteCompletionUnconfirmed('Agent start outcome is unknown; remote completion must be confirmed')
        self.completion_confirmed = True
        return True

    async def end_input(self):
        if self.socket:
            await self._send({'type': 'eof'})
