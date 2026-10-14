"""Exercise cloud cache publication without importing application configuration."""

import ast
import os
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import BinaryIO, Dict, Tuple

import pytest


class DownloadError(Exception):
    pass


@pytest.fixture(params=['gcs', 'azure'])
def storage(request, tmp_path):
    source = Path(__file__).parents[1] / 'open_webui/storage/provider.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    class_name = {'gcs': 'GCSStorageProvider', 'azure': 'AzureStorageProvider'}[request.param]
    definition = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    namespace = {
        'StorageProvider': object,
        'BinaryIO': BinaryIO,
        'Dict': Dict,
        'Tuple': Tuple,
        'NotFound': DownloadError,
        'ResourceNotFoundError': DownloadError,
        'UPLOAD_DIR': str(tmp_path),
        'os': os,
        'tempfile': tempfile,
    }
    exec(compile(ast.Module(body=[definition], type_ignores=[]), str(source), 'exec'), namespace)
    provider = object.__new__(namespace[class_name])

    def set_download(download):
        if request.param == 'gcs':

            def download_to_filename(filename):
                with open(filename, 'wb') as output:
                    output.write(b'partial')
                    output.flush()
                    contents = download()
                    output.seek(0)
                    output.write(contents)
                    output.truncate()

            provider.bucket = SimpleNamespace(
                get_blob=lambda filename: SimpleNamespace(download_to_filename=download_to_filename)
            )
        else:
            provider.container_client = SimpleNamespace(
                get_blob_client=lambda filename: SimpleNamespace(
                    download_blob=lambda: SimpleNamespace(readall=download)
                )
            )

    base_url = 'gs://synthetic-bucket' if request.param == 'gcs' else 'https://synthetic.invalid/container'
    return SimpleNamespace(provider=provider, directory=tmp_path, set_download=set_download, base_url=base_url)


@pytest.mark.parametrize('cached', [False, True])
def test_incomplete_download_does_not_publish_partial_content(storage, cached):
    destination = storage.directory / 'document.txt'
    previous = b'previous complete content'
    complete = b'new complete content'
    if cached:
        destination.write_bytes(previous)
    started, finish = threading.Event(), threading.Event()

    def download():
        started.set()
        assert finish.wait(5), 'Reader did not finish the concurrent probe'
        return complete

    storage.set_download(download)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(storage.provider.get_file, f'{storage.base_url}/document.txt')
        try:
            assert started.wait(5), 'Download did not start'
            if cached:
                assert destination.read_bytes() == previous
            else:
                assert not destination.exists()
        finally:
            finish.set()
        assert Path(pending.result()) == destination
    assert destination.read_bytes() == complete
    assert list(storage.directory.iterdir()) == [destination]


@pytest.mark.parametrize('cached', [False, True])
@pytest.mark.parametrize('error', [DownloadError('provider interrupted'), OSError('download interrupted')])
def test_failed_download_preserves_cache_and_removes_temporary_file(storage, cached, error):
    destination = storage.directory / 'document.txt'
    if cached:
        destination.write_bytes(b'previous complete content')

    def download():
        raise error

    storage.set_download(download)
    with pytest.raises(RuntimeError if isinstance(error, DownloadError) else OSError):
        storage.provider.get_file(f'{storage.base_url}/document.txt')
    if cached:
        assert destination.read_bytes() == b'previous complete content'
        assert list(storage.directory.iterdir()) == [destination]
    else:
        assert not list(storage.directory.iterdir())


def test_failed_atomic_replacement_preserves_previous_cache(storage, monkeypatch):
    destination = storage.directory / 'document.txt'
    destination.write_bytes(b'previous complete content')
    storage.set_download(lambda: b'new complete content')

    def unavailable_destination(source, target):
        raise PermissionError('synthetic destination is in use')

    monkeypatch.setattr(os, 'replace', unavailable_destination)
    with pytest.raises(PermissionError):
        storage.provider.get_file(f'{storage.base_url}/document.txt')
    assert destination.read_bytes() == b'previous complete content'
    assert list(storage.directory.iterdir()) == [destination]
