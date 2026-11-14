"""Branded HTML for the Stripe Checkout redirect pages (/billing/success, /billing/cancel).

Self-contained: inline CSS, inline SVG, no external fonts or scripts. Every dynamic value
is HTML-escaped. These pages display state; they never change it (the webhook does).
"""

from dataclasses import dataclass
from html import escape
from string import Template

PRODUCT = "Metering &amp; Billing"

_CHECK = (
    "<svg viewBox='0 0 24 24' aria-hidden='true'><path d='M5 12.5l4.2 4.2L19 7' "
    "fill='none' stroke='currentColor' stroke-width='2.4' stroke-linecap='round' "
    "stroke-linejoin='round'/></svg>"
)
_CROSS = (
    "<svg viewBox='0 0 24 24' aria-hidden='true'><path d='M7 7l10 10M17 7L7 17' "
    "fill='none' stroke='currentColor' stroke-width='2.4' stroke-linecap='round'/></svg>"
)
_LOCK = (
    "<svg viewBox='0 0 24 24' aria-hidden='true'><rect x='5' y='10.5' width='14' "
    "height='10' rx='2.5' fill='none' stroke='currentColor' stroke-width='1.8'/>"
    "<path d='M8.5 10.5V8a3.5 3.5 0 017 0v2.5' fill='none' stroke='currentColor' "
    "stroke-width='1.8'/></svg>"
)
_LOGO = (
    "<svg viewBox='0 0 32 32' aria-hidden='true'><rect width='32' height='32' rx='8' "
    "fill='var(--brand)'/><path d='M9 21V14M14 21V10M19 21v-5M24 21V12' stroke='#fff' "
    "stroke-width='2.6' stroke-linecap='round'/></svg>"
)

_CSS = """
:root{--bg:#f4f6fb;--bg2:#e9edf7;--card:#fff;--ink:#0f1728;--muted:#5b6478;--line:#e3e7f0;
--soft:#f7f8fc;--brand:#4f46e5;--brand-ink:#fff;--ok:#0f9f6e;--ok-soft:#e6f7f0;
--warn:#b45309;--warn-soft:#fdf3e4;--code:#0f1728;--code-ink:#e6e9f2;
--shadow:0 1px 2px rgba(15,23,40,.04),0 12px 32px -8px rgba(15,23,40,.12)}
@media (prefers-color-scheme:dark){:root{--bg:#0b0f19;--bg2:#111829;--card:#141b2b;
--ink:#eef1f8;--muted:#9aa3b8;--line:#25304a;--soft:#192238;--brand:#6d66f2;
--ok:#34d399;--ok-soft:#10291f;--warn:#f5b45a;--warn-soft:#2b2010;--code:#0b0f19;
--code-ink:#d6dbe8;--shadow:0 1px 2px rgba(0,0,0,.3),0 16px 40px -12px rgba(0,0,0,.6)}}
*{box-sizing:border-box}
html,body{margin:0}
body{min-height:100vh;background:radial-gradient(1200px 600px at 50% -10%,var(--bg2),var(--bg));
color:var(--ink);font:15px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,
"Helvetica Neue",Arial,sans-serif;-webkit-font-smoothing:antialiased}
.top{display:flex;align-items:center;justify-content:space-between;max-width:1080px;
margin:0 auto;padding:20px 16px}
.brand{display:flex;align-items:center;gap:10px;font-weight:650;letter-spacing:-.01em}
.brand svg{width:28px;height:28px}
.badge{font-size:12px;font-weight:600;padding:4px 10px;border-radius:999px;
background:var(--warn-soft);color:var(--warn);
border:1px solid color-mix(in srgb,var(--warn) 25%,transparent)}
main{max-width:560px;margin:24px auto 48px;padding:0 16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;
box-shadow:var(--shadow);overflow:hidden}
.hero{padding:36px 32px 28px;text-align:center}
.icon{width:56px;height:56px;margin:0 auto 18px;border-radius:50%;display:grid;place-items:center}
.icon svg{width:28px;height:28px}
.icon.ok{background:var(--ok-soft);color:var(--ok)}
.icon.neutral{background:var(--soft);color:var(--muted)}
h1{margin:0 0 8px;font-size:24px;line-height:1.25;letter-spacing:-.02em}
.lead{margin:0 auto;max-width:420px;color:var(--muted)}
.section{padding:22px 32px;border-top:1px solid var(--line)}
.label{margin:0 0 12px;font-size:12px;font-weight:650;text-transform:uppercase;
letter-spacing:.06em;color:var(--muted)}
.rows{margin:0;display:grid;grid-template-columns:auto 1fr;gap:10px 16px}
.rows dt{color:var(--muted)}
.rows dd{margin:0;text-align:right;font-weight:550;overflow-wrap:anywhere}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:13px}
.pill{display:inline-flex;align-items:center;gap:6px;font-size:13px;font-weight:600;
padding:2px 10px;border-radius:999px;background:var(--warn-soft);color:var(--warn)}
.pill.ok{background:var(--ok-soft);color:var(--ok)}
.dot{width:7px;height:7px;border-radius:50%;background:currentColor;animation:pulse 1.6s infinite}
@keyframes pulse{50%{opacity:.35}}
@media (prefers-reduced-motion:reduce){.dot{animation:none}}
.steps{list-style:none;margin:0;padding:0;display:grid;gap:14px}
.steps li{display:grid;grid-template-columns:28px 1fr;gap:12px;align-items:start}
.n{width:28px;height:28px;border-radius:50%;display:grid;place-items:center;font-size:13px;
font-weight:650;background:var(--soft);color:var(--muted);border:1px solid var(--line)}
.n.done{background:var(--ok-soft);color:var(--ok);border-color:transparent}
.n.done svg{width:16px;height:16px}
.steps b{display:block;font-weight:600}
.steps span{color:var(--muted);font-size:14px}
pre{margin:0;padding:14px 16px;border-radius:10px;background:var(--code);color:var(--code-ink);
font:13px/1.6 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;overflow-x:auto}
.actions{display:flex;gap:10px;flex-wrap:wrap;padding:0 32px 28px}
.btn{flex:1 1 180px;text-align:center;text-decoration:none;font-weight:600;padding:11px 16px;
border-radius:10px;border:1px solid var(--line);color:var(--ink);background:var(--card)}
.btn.primary{background:var(--brand);border-color:var(--brand);color:var(--brand-ink)}
.btn:hover{filter:brightness(1.05)}
.btn:focus-visible{outline:3px solid color-mix(in srgb,var(--brand) 45%,transparent);
outline-offset:2px}
.foot{display:flex;align-items:center;justify-content:center;gap:8px;margin-top:20px;
color:var(--muted);font-size:13px;text-align:center}
.foot svg{width:15px;height:15px;flex:none}
@media (max-width:480px){.hero{padding:28px 20px 22px}.section{padding:20px}
.actions{padding:0 20px 22px}h1{font-size:21px}}
"""

_LAYOUT = Template(
    """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>$title · $product</title>
<style>$css</style>
</head>
<body>
<header class="top">
  <div class="brand">$logo<span>$product</span></div>
  <span class="badge">Test mode</span>
</header>
<main>
  <div class="card">
    <div class="hero">
      <div class="icon $icon_class">$icon</div>
      <h1>$title</h1>
      <p class="lead">$lead</p>
    </div>
    $body
  </div>
  <p class="foot">$lock<span>Payments processed securely by Stripe.
    Test mode: no real card is charged.</span></p>
</main>
</body>
</html>"""
)


@dataclass(frozen=True)
class PlanSummary:
    name: str
    api_calls_limit: int
    ai_tokens_limit: int


def _page(*, title: str, lead: str, icon: str, icon_class: str, body: str) -> str:
    return _LAYOUT.substitute(
        title=title,
        product=PRODUCT,
        css=_CSS,
        logo=_LOGO,
        lock=_LOCK,
        icon=icon,
        icon_class=icon_class,
        lead=lead,
        body=body,
    )


def success_page(plan: PlanSummary | None, session_id: str | None, base_url: str) -> str:
    base = escape(base_url.rstrip("/"))
    plan_name = escape(plan.name) if plan else "Pro"
    rows = [f"<dt>Plan</dt><dd>{plan_name} &middot; monthly</dd>"]
    if plan:
        rows.append(f"<dt>API calls</dt><dd>{plan.api_calls_limit:,} / month</dd>")
        rows.append(f"<dt>AI tokens</dt><dd>{plan.ai_tokens_limit:,} / month</dd>")
    rows.append(
        "<dt>Status</dt><dd><span class='pill'><span class='dot'></span>Activating</span></dd>"
    )
    if session_id:
        rows.append(f"<dt>Reference</dt><dd class='mono'>{escape(session_id[:255])}</dd>")

    body = f"""
    <div class="section">
      <p class="label">Order summary</p>
      <dl class="rows">{"".join(rows)}</dl>
    </div>
    <div class="section">
      <p class="label">What happens next</p>
      <ol class="steps">
        <li><span class="n done">{_CHECK}</span><div><b>Payment confirmed</b>
          <span>Stripe accepted your card and created the subscription.</span></div></li>
        <li><span class="n">2</span><div><b>Plan activation</b>
          <span>Stripe sends a signed webhook. Once we verify it, your workspace moves
          to {plan_name}, usually within a few seconds.</span></div></li>
        <li><span class="n">3</span><div><b>New limits are live</b>
          <span>Your quotas and current usage are always available from the API.</span></div></li>
      </ol>
    </div>
    <div class="section">
      <p class="label">Check your plan</p>
      <pre>curl -H "X-API-Key: $YOUR_API_KEY" \\
     {base}/usage</pre>
    </div>
    <div class="actions">
      <a class="btn primary" href="/docs#/usage">Open API reference</a>
      <a class="btn" href="/health">System status</a>
    </div>"""
    return _page(
        title="Payment complete",
        lead=f"Thank you for upgrading. Your {plan_name} subscription is being activated.",
        icon=_CHECK,
        icon_class="ok",
        body=body,
    )


def cancel_page(free_plan: PlanSummary | None, base_url: str) -> str:
    base = escape(base_url.rstrip("/"))
    rows = ["<dt>Charge</dt><dd>None</dd>", "<dt>Current plan</dt><dd>Free</dd>"]
    if free_plan:
        rows.append(f"<dt>API calls</dt><dd>{free_plan.api_calls_limit:,} / month</dd>")
        rows.append(f"<dt>AI tokens</dt><dd>{free_plan.ai_tokens_limit:,} / month</dd>")
    body = f"""
    <div class="section">
      <p class="label">Your account</p>
      <dl class="rows">{"".join(rows)}</dl>
    </div>
    <div class="section">
      <p class="label">Upgrade any time</p>
      <pre>curl -X POST -H "X-API-Key: $YOUR_API_KEY" \\
     {base}/billing/checkout</pre>
    </div>
    <div class="actions">
      <a class="btn primary" href="/docs#/billing">Open API reference</a>
    </div>"""
    return _page(
        title="Checkout cancelled",
        lead="No payment was taken and nothing on your account changed.",
        icon=_CROSS,
        icon_class="neutral",
        body=body,
    )
