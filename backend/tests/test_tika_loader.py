import ast
import json
import logging
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock


MODULE_PATH = Path(__file__).parents[1] / 'open_webui' / 'retrieval' / 'loaders' / 'main.py'


def load_tika_loader(put):
    # Execute the real loader without importing unrelated providers or app config,
    # which would require the full backend and could touch the development database.
    tree = ast.parse(MODULE_PATH.read_text(encoding='utf-8'))
    node = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'TikaLoader')
    namespace = {
        'Document': SimpleNamespace,
        'requests': SimpleNamespace(put=put),
        'REQUESTS_VERIFY': True,
        'log': logging.getLogger(__name__),
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(MODULE_PATH), 'exec'), namespace)
    return namespace['TikaLoader']


class TikaLoaderTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.file_path = Path(directory.name) / 'document.pdf'
        self.pdf_bytes = b'%PDF-1.4\n% Synthetic upload fixture\n'
        self.file_path.write_bytes(self.pdf_bytes)

    def check_extraction(self, options, endpoint, content_key):
        def respond(url, *, data, headers, verify):
            # Tika 3 negotiates JSON via Accept; without it the extracted text
            # can begin with a newline and trigger the reported JSONDecodeError.
            # https://cwiki.apache.org/confluence/spaces/TIKA/pages/148639291/TikaServer
            text = '\nExtracted PDF text\n'
            if endpoint == 'tika/json/text' or headers.get('Accept') == 'application/json':
                body = json.dumps({content_key: text, 'Content-Type': 'application/pdf'})
            else:
                body = text
            return SimpleNamespace(ok=True, json=lambda: json.loads(body))

        put = Mock(side_effect=respond)
        loader = load_tika_loader(put)(url='http://tika:9998/', file_path=self.file_path, **options)

        documents = loader.load()

        self.assertEqual(len(documents), 1)
        self.assertEqual(documents[0].page_content, 'Extracted PDF text')
        self.assertEqual(documents[0].metadata['Content-Type'], 'application/pdf')
        expected_headers = {'Accept': 'application/json'}
        if options.get('mime_type'):
            expected_headers['Content-Type'] = options['mime_type']
        if options.get('extract_images'):
            expected_headers['X-Tika-PDFextractInlineImages'] = 'true'
        put.assert_called_once_with(
            f'http://tika:9998/{endpoint}',
            data=self.pdf_bytes,
            headers=expected_headers,
            verify=True,
        )

    def test_default_version_extracts_pdf_as_json(self):
        self.check_extraction({}, 'tika/text', 'X-TIKA:content')

    def test_tika3_preserves_mime_and_image_extraction_options(self):
        self.check_extraction(
            {'server_version': '3', 'mime_type': 'application/pdf', 'extract_images': True},
            'tika/text',
            'X-TIKA:content',
        )

    def test_tika4_preserves_json_endpoint_and_content_key(self):
        self.check_extraction({'server_version': '4'}, 'tika/json/text', 'tk:content')

    def test_http_failure_reports_tika_error_without_parsing_json(self):
        response = SimpleNamespace(ok=False, reason='Service Unavailable', json=Mock())
        loader = load_tika_loader(Mock(return_value=response))(url='http://tika:9998', file_path=self.file_path)

        with self.assertRaisesRegex(Exception, 'Error calling Tika: Service Unavailable'):
            loader.load()

        response.json.assert_not_called()


if __name__ == '__main__':
    unittest.main()
