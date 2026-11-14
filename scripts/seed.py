"""Seed demo tenants. Idempotent: re-running resets their plan and API key.

Run: docker compose run --rm api python scripts/seed.py

The demo keys are fixed so the README and capstone.yaml can reference them. They only
exist in your local database; they are not secrets.
"""

from app.db.session import SessionLocal
from app.services import tenancy

DEMO_TENANTS = [
    ("acme", "free", "demo_free_acme_key"),
    ("globex", "pro", "demo_pro_globex_key"),
]


def main() -> None:
    with SessionLocal() as session:
        for name, plan_id, api_key in DEMO_TENANTS:
            tenancy.create_or_update_tenant(session, name=name, plan_id=plan_id, api_key=api_key)
        session.commit()

    print("Seeded demo tenants:")
    print(f"  {'tenant':<8} {'plan':<5} X-API-Key")
    for name, plan_id, api_key in DEMO_TENANTS:
        print(f"  {name:<8} {plan_id:<5} {api_key}")


if __name__ == "__main__":
    main()
