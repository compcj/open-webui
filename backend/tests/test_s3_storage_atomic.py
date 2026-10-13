"""Exercise the actual S3 cache writer without importing application configuration."""

import ast
import logging
import os
import re
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import BinaryIO, Dict, Tuple

import pytest


class DownloadError(Exception):
    pass


@pytest.fixture
def storage(tmp_path):
    source = Path(__file__).parents[1] / 'open_webui/storage/provider.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    definition = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'S3StorageProvider')
    namespace = {
        'StorageProvider': object,
        'BinaryIO': BinaryIO,
        'Dict': Dict,
        'Tuple': Tuple,
        'ClientError': DownloadError,
        'UPLOAD_DIR': str(tmp_path),
        'os': os,
        're': re,
        'tempfile': tempfile,
        'log': logging.getLogger(__name__),
    }
    exec(compile(ast.Module(body=[definition], type_ignores=[]), str(source), 'exec'), namespace)
    provider = object.__new__(namespace['S3StorageProvider'])
    provider.bucket_name = 'synthetic-bucket'
    return SimpleNamespace(provider=provider, directory=tmp_path)


def test_incomplete_concurrent_download_does_not_truncate_completed_reader(storage):
    payload = b'complete-synthetic-document-data'
    started, finish = threading.Event(), threading.Event()

    class Client:
        calls = 0

        def download_fileobj(self, bucket, key, output):
            self.calls += 1
            if self.calls == 1:
                output.write(payload)
                return
            output.write(payload[:5])
            output.flush()
            started.set()
            assert finish.wait(5), 'Reader did not finish the concurrent probe'
            output.write(payload[5:])

    provider = storage.provider
    provider.s3_client = Client()
    first = Path(provider.get_file('s3://synthetic-bucket/document.txt'))
    with ThreadPoolExecutor(max_workers=1) as pool:
        download = pool.submit(provider.get_file, 's3://synthetic-bucket/document.txt')
        try:
            assert started.wait(5), 'Second download did not start'
            assert first.read_bytes() == payload
        finally:
            finish.set()
        assert Path(download.result()).read_bytes() == payload
    assert list(storage.directory.iterdir()) == [first]


@pytest.mark.parametrize('cached', [False, True])
@pytest.mark.parametrize('error', [DownloadError('provider interrupted'), OSError('local write interrupted')])
def test_failed_download_preserves_cache_and_removes_temporary_file(storage, cached, error):
    destination = storage.directory / 'document.txt'
    if cached:
        destination.write_bytes(b'previous complete content')

    class Client:
        def download_fileobj(self, bucket, key, output):
            output.write(b'partial')
            output.flush()
            raise error

    storage.provider.s3_client = Client()
    with pytest.raises(RuntimeError if isinstance(error, DownloadError) else OSError):
        storage.provider.get_file('s3://synthetic-bucket/document.txt')
    if cached:
        assert destination.read_bytes() == b'previous complete content'
        assert list(storage.directory.iterdir()) == [destination]
    else:
        assert not list(storage.directory.iterdir())


def test_multibyte_filename_keeps_temporary_name_short(storage):
    name = '文' * 82 + '.txt'
    opened = []

    class Client:
        def download_fileobj(self, bucket, key, output):
            opened.append(Path(output.name))
            output.write(b'complete')

    storage.provider.s3_client = Client()
    result = Path(storage.provider.get_file(f's3://synthetic-bucket/{name}'))
    assert result.name == name and result.read_bytes() == b'complete'
    assert opened[0].parent == result.parent
    assert len(opened[0].name.encode('utf-8')) < 64
    assert opened[0] != result
    assert list(storage.directory.iterdir()) == [result]


def test_failed_atomic_replacement_preserves_previous_cache(storage, monkeypatch):
    destination = storage.directory / 'document.txt'
    destination.write_bytes(b'previous complete content')

    class Client:
        def download_fileobj(self, bucket, key, output):
            output.write(b'new complete content')

    def unavailable_destination(source, target):
        raise PermissionError('synthetic destination is in use')

    storage.provider.s3_client = Client()
    monkeypatch.setattr(os, 'replace', unavailable_destination)
    with pytest.raises(PermissionError):
        storage.provider.get_file('s3://synthetic-bucket/document.txt')
    assert destination.read_bytes() == b'previous complete content'
    assert list(storage.directory.iterdir()) == [destination]
