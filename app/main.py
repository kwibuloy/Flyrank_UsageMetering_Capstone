import logging

from fastapi import FastAPI

from app.api.errors import register_error_handlers
from app.api.routes import alerts, billing, generate, health, usage, webhooks
from app.config import get_settings
from app.services.pricing import get_price_book


def create_app() -> FastAPI:
    logging.basicConfig(
        level=get_settings().log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    app = FastAPI(
        title="Usage Metering & Billing Engine",
        version="0.1.0",
        description="Idempotent metering, quota enforcement, cost math and Stripe test mode.",
    )
    get_price_book()  # fail fast on a missing or invalid pricing.toml
    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(generate.router)
    app.include_router(usage.router)
    app.include_router(alerts.router)
    app.include_router(billing.router)
    app.include_router(webhooks.router)
    return app


app = create_app()
