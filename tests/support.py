"""Isolate real application code from developer credentials, files and network."""
from contextlib import ExitStack
import importlib.util
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / 'fixtures'


def load_backend():
    spec = importlib.util.spec_from_file_location('profitshield_test_backend', ROOT / 'server.py')
    module = importlib.util.module_from_spec(spec)
    exists = Path.exists
    # Import the actual source without loading the developer's .env or starting it.
    with patch.object(Path, 'exists', lambda path: False if path == ROOT / '.env' else exists(path)), patch.dict(
            os.environ, {'OPENROUTER_API_KEY': '', 'OPENROUTER_MODEL': 'fixture-model',
                         'OPENROUTER_VISION_MODEL': 'fixture-model', 'PROFITSHIELD_AI_PRIVACY_APPROVED': '', 'SQUARE_ACCESS_TOKEN': '',
                         'SQUARE_ENV': 'sandbox', 'SQUARE_VERSION': '2024-10-17',
                         'STORE_TZ': 'America/New_York', 'PORT': '8000', 'BACKHOUSE_DB': ':memory:'}):
        spec.loader.exec_module(module)
    return module


server = load_backend()


class BackendCase(unittest.TestCase):
    """Fresh database and denied outbound HTTP for every test."""
    samples = False

    def setUp(self):
        self.resources = ExitStack()
        self.addCleanup(self.resources.close)
        temporary = self.resources.enter_context(tempfile.TemporaryDirectory())
        self.db_path = Path(temporary) / 'fixture.db'
        for name, value in [('DB_PATH', self.db_path), ('KEY', ''), ('SQUARE_TOKEN', '')]:
            self.resources.enter_context(patch.object(server, name, value))
        self.network = self.resources.enter_context(patch.object(
            server.urllib.request, 'urlopen', side_effect=AssertionError('Outbound HTTP must be mocked')))
        if hasattr(server, 'SQUARE_OPENER'):
            self.resources.enter_context(patch.object(server.SQUARE_OPENER, 'open',
                side_effect=AssertionError('Square transport must be mocked')))
        if self.samples:
            with patch('sys.stdout', new=io.StringIO()):
                server.init_db()
        else:
            with patch.object(server, 'seed'):
                server.init_db()

    def import_fixture(self, name):
        path = FIXTURES / name
        return server.import_csv(path.read_text(encoding='utf-8-sig'), path.name, allow_ai=False)
