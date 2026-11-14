"""Point the whole test run at the test database and fake Stripe settings, before any app
module reads settings. Tests never call the real Stripe API (the key below is not real)."""

import os

os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://billing:billing@localhost:5433/billing_test"
)
os.environ["STRIPE_SECRET_KEY"] = "sk_test_dummy_for_tests"
os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_test_only"
os.environ["STRIPE_PRO_PRICE_ID"] = "price_test_pro"
os.environ["PUBLIC_BASE_URL"] = "http://testserver"
