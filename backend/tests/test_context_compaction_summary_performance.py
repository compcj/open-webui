"""Exercise V2 summary planning without a live provider or application startup."""

import asyncio
import copy
import json
import threading
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from test_web_tool_citations import load_functions

from open_webui.utils import context_compaction_v2 as v2


FRAGMENT_MARKER = '\n\nConversation data fragment (may continue):\n'


def handoff_text(body):
    content = body['messages'][-1]['content']
    return content[0]['text'] if isinstance(content, list) else content


def completion(text):
    return {'choices': [{'message': {'content': text}}]}


@pytest.fixture(autouse=True)
def offline_encoder(monkeypatch):
    def offline(path):
        raise FileNotFoundError(path)

    # Use the real cl100k_base when cached. CI can exercise the same work bounds
    # with the conservative UTF-8 estimate without downloading its vocabulary.
    try:
        import tiktoken.load

        monkeypatch.setattr(tiktoken.load, 'read_file', offline)
        encoder = tiktoken.get_encoding('cl100k_base')
    except (ImportError, FileNotFoundError):
        encoder = None
    monkeypatch.setattr(v2, 'token_encoder', lambda: encoder)
    return encoder


@pytest.mark.asyncio
async def test_short_history_fits_one_call_with_bounded_tokenization(monkeypatch, offline_encoder):
    notes = (
        'Please review the meeting notes and summarize the decisions on scheduling and staffing.',
        'The group agreed to review the schedule on Friday and keep the current staffing plan.',
    )
    source = [{'role': ('user', 'assistant')[i % 2], 'content': f'Turn {i + 1:02d}. {notes[i % 2]}'} for i in range(64)]
    units = [json.dumps(message, separators=(',', ':')) for message in source]
    assert sum(map(len, units)) == 8032
    encoded_chars = 0

    def encode(text, **kwargs):
        nonlocal encoded_chars
        encoded_chars += len(text)
        if offline_encoder:
            return offline_encoder.encode(text, **kwargs)
        return range((len(text.encode('utf-8')) + 1) // 2)

    counting_encoder = SimpleNamespace(encode=encode)
    monkeypatch.setattr(v2, 'token_encoder', lambda: counting_encoder)
    estimates = 0
    estimate = v2.estimate_payload_tokens

    def count_estimate(payload):
        nonlocal estimates
        estimates += 1
        return estimate(payload)

    monkeypatch.setattr(v2, 'estimate_payload_tokens', count_estimate)
    sends = []

    async def provider(payload):
        sends.append((estimates, encoded_chars, payload))
        return completion('Friday review.')

    result = await v2.progressive_summary(source, '', v2.CompactionSettings(), {'messages': []}, 16000, provider)

    assert result == 'Friday review.'
    assert len(sends) == 1
    probes, chars, body = sends[0]
    assert handoff_text(body).partition(FRAGMENT_MARKER)[2] == '\n'.join(units)
    assert probes <= 12 and chars <= 211448, f'{probes} payload estimates, {chars} characters encoded before first send'


@pytest.mark.asyncio
async def test_long_text_probes_stay_near_the_chunk_budget(monkeypatch):
    source = [{'role': 'user', 'content': 'Weekly meeting notes. ' * 4000}]
    expected = json.dumps(source[0], separators=(',', ':'))
    budget = 1600
    monkeypatch.setattr(v2, 'estimate_text', lambda value: len(value or ''))
    estimate = v2.estimate_payload_tokens
    largest_probe = 0

    def measure(payload):
        nonlocal largest_probe
        fragment = handoff_text(payload).partition(FRAGMENT_MARKER)[2]
        largest_probe = max(largest_probe, len(fragment))
        return estimate(payload)

    monkeypatch.setattr(v2, 'estimate_payload_tokens', measure)
    fragments = []

    async def provider(body):
        assert estimate(body) <= budget
        fragments.append(handoff_text(body).partition(FRAGMENT_MARKER)[2])
        return completion('ok')

    await v2.progressive_summary(source, '', v2.CompactionSettings(summary_prompt='Summarize.'), {}, budget, provider)
    assert len(fragments) > 2
    assert ''.join(fragments) == expected
    assert largest_probe <= 2 * budget


def hold_planning_work(monkeypatch, stage):
    """Hold the first source serialization or nonempty fragment budget probe."""
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = threading.Event()
    returned = threading.Event()
    operation = getattr(v2, stage)

    def held(*args, **kwargs):
        if returned.is_set() or (
            stage == 'estimate_payload_tokens' and not handoff_text(args[0]).partition(FRAGMENT_MARKER)[2]
        ):
            return operation(*args, **kwargs)
        loop.call_soon_threadsafe(entered.set)
        try:
            release.wait(timeout=5)  # Let a regressed event-loop implementation fail without deadlocking pytest.
            return operation(*args, **kwargs)
        finally:
            returned.set()

    monkeypatch.setattr(v2, stage, held)
    return entered, release, returned


@pytest.fixture(scope='module')
def health_app():
    # Register the repository's real /health handler without booting the database or application.
    app = FastAPI()
    load_functions({'app': app}, 'main.py', {'healthcheck'})
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['summary_units', 'estimate_payload_tokens'])
async def test_health_responds_while_summary_planning_is_held(monkeypatch, health_app, stage):
    entered, release, returned = hold_planning_work(monkeypatch, stage)

    async def provider(body):
        return completion('handoff')

    source = [{'role': 'user', 'content': 'Meeting notes for the summary.'}]
    task = asyncio.create_task(v2.progressive_summary(source, '', v2.CompactionSettings(), {}, 16000, provider))
    try:
        await asyncio.wait_for(entered.wait(), timeout=8)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=health_app), base_url='http://test') as client:
            response = await client.get('/health')
        assert response.status_code == 200
        assert response.json() == {'status': True}
        assert not returned.is_set(), f'/health did not finish while {stage} was held'
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    assert task.result() == 'handoff'


@pytest.mark.parametrize('stop', ['cancel', 'timeout'])
def test_stopping_planning_settles_before_worker_release_and_never_sends(monkeypatch, stop):
    sends = []

    async def provider(body):
        sends.append(body)
        return completion('handoff')

    async def exercise():
        stage = 'summary_units' if stop == 'cancel' else 'estimate_payload_tokens'
        entered, release, returned = hold_planning_work(monkeypatch, stage)
        source = [{'role': 'user', 'content': 'A draft which must never reach the provider.'}]
        cfg = v2.CompactionSettings(timeout_seconds=1 if stop == 'timeout' else 60)
        task = asyncio.create_task(v2.progressive_summary(source, '', cfg, {}, 16000, provider))
        try:
            await asyncio.wait_for(entered.wait(), timeout=8)
            if stop == 'cancel':
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                with pytest.raises(v2.CompactionFailure, match='timed out'):
                    await asyncio.wait_for(asyncio.shield(task), timeout=8)
            assert not returned.is_set(), f'{stop} did not settle until the planning worker returned'
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            # Drain this private loop's workers and their queued callbacks before checking late sends.
            await asyncio.get_running_loop().shutdown_default_executor()
            await asyncio.sleep(0)

    asyncio.run(exercise())
    assert not sends


@pytest.mark.asyncio
@pytest.mark.parametrize('stop', ['cancel', 'timeout'])
async def test_provider_returning_after_cancellation_cannot_finish_summary(stop):
    started = asyncio.Event()
    sends = []

    async def provider(body):
        sends.append(body)
        started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            # A transport can return a buffered response during its cleanup.
            return completion('late')

    cfg = v2.CompactionSettings(timeout_seconds=1 if stop == 'timeout' else 60)
    task = asyncio.create_task(
        v2.progressive_summary([{'role': 'user', 'content': 'meeting notes'}], '', cfg, {}, 16000, provider)
    )
    await asyncio.wait_for(started.wait(), timeout=5)
    if stop == 'cancel':
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(v2.CompactionFailure, match='timed out'):
            await task
    assert len(sends) == 1


@pytest.mark.asyncio
async def test_interleaved_summaries_keep_independent_sources_handoffs_and_payloads():
    common = {'messages': [{'role': 'system', 'content': 'shared model instructions'}], 'options': {'seed': 5}}
    original = copy.deepcopy(common)
    first_sends = asyncio.Barrier(2)

    async def summarize(label, budget):
        source = {'role': 'user', 'content': (label + ' 進度 👩🏽‍💻 ') * 80}
        settings = v2.CompactionSettings(summary_prompt=f'Write the {label} handoff.', max_retries=0)
        packets = []

        async def provider(body):
            packets.append(copy.deepcopy(body))
            body['messages'][0]['content'] = label
            body['options']['seed'] = label
            if len(packets) == 1:
                await asyncio.wait_for(first_sends.wait(), timeout=5)
            return completion(f'{label} {len(packets)}')

        summary = await v2.progressive_summary([source], f'{label} start', settings, common, budget, provider)
        assert len(packets) > 1
        assert summary == f'{label} {len(packets)}'
        fragments = []
        for i, body in enumerate(packets):
            assert v2.estimate_payload_tokens(body) <= budget
            assert body['messages'][0] == original['messages'][0]
            assert body['messages'][1]['content'] == settings.summary_prompt
            assert body['options'] == original['options']
            handoff, marker, fragment = handoff_text(body).partition(FRAGMENT_MARKER)
            assert marker and handoff == f'Previous handoff:\n{label} {i or "start"}'
            fragments.append(fragment)
        assert ''.join(fragments) == json.dumps(source, ensure_ascii=False, separators=(',', ':'))

    await asyncio.gather(summarize('alpha', 150), summarize('beta', 225))
    assert common == original


@pytest.mark.asyncio
@pytest.mark.parametrize('headroom', [0, -1])
async def test_mixed_unicode_and_image_preserve_source_at_the_exact_budget(headroom):
    url = 'data:image/png;base64,c3ludGhldGlj'
    text = {'type': 'text', 'text': '记录 👩🏽‍💻\n"café"'}
    first = {'role': 'user', 'content': [text, {'type': 'input_image', 'image_url': url}]}
    second = {'role': 'assistant', 'content': 'e\u0301 / 決定 / ☕ — Friday.' * 8}
    serialized = [
        json.dumps(message, ensure_ascii=False, separators=(',', ':'))
        for message in ({'role': 'user', 'content': [text, {'type': 'media_reference', 'index': 1}]}, second)
    ]
    image = {'type': 'image_url', 'image_url': {'url': url}}
    prompt = 'Summarize.'
    exact_body = {
        'messages': [
            {'role': 'system', 'content': prompt},
            {
                'role': 'user',
                'content': [
                    {'type': 'text', 'text': 'Previous handoff:\n' + FRAGMENT_MARKER + '\n'.join(serialized)},
                    image,
                ],
            },
        ]
    }
    budget = v2.estimate_payload_tokens(exact_body) + headroom
    source = [first, second]
    original = copy.deepcopy(source)
    packets = []

    async def provider(body):
        packets.append(body)
        return completion('ok')

    assert (
        await v2.progressive_summary(source, '', v2.CompactionSettings(summary_prompt=prompt), {}, budget, provider)
        == 'ok'
    )
    if headroom == 0:
        assert packets == [exact_body]
    else:
        assert len(packets) > 1
    assert all(v2.estimate_payload_tokens(body) <= budget for body in packets)
    fragments = ''.join(handoff_text(body).partition(FRAGMENT_MARKER)[2] for body in packets)
    assert fragments.replace('\n', '') == ''.join(serialized)
    media = [
        part for body in packets if isinstance(content := body['messages'][-1]['content'], list) for part in content[1:]
    ]
    assert media == [image]
    assert source == original
