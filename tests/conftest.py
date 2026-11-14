"""Point the whole test run at the test database before any app module reads settings."""

import os

os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://billing:billing@localhost:5433/billing_test"
)
