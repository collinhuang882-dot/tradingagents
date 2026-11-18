import uuid
from pathlib import Path


def pytest_configure(config):
    if not config.option.basetemp:
        config.option.basetemp = str(Path(__file__).resolve().parents[1] / 'data' / ('test-' + uuid.uuid4().hex))
