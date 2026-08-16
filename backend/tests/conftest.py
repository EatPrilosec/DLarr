import os
import shutil
import asyncio
from pathlib import Path
import pytest

# Ensure test suite runs against an isolated temporary directory, never touching /config/dlarr.db
TEST_DATA_DIR = "/tmp/dlarr_test"
os.environ["DLARR_DATA_DIR"] = TEST_DATA_DIR

Path(TEST_DATA_DIR).mkdir(parents=True, exist_ok=True)

from backend.app.core.database import init_db


@pytest.fixture(scope="session", autouse=True)
def setup_test_db():
    asyncio.run(init_db())
    yield
    shutil.rmtree(TEST_DATA_DIR, ignore_errors=True)
