"""HTML for the Checkout redirect pages (/billing/success, /billing/cancel).

A single centered card in the visual language of Stripe's payment pages (soft #f6f9fc
background, layered card shadow, navy/slate type, blurple accent), with motion: the card
rises in, rows stagger, the checkmark draws itself, and the plan status crossfades from
"Activating" to "Active" in place while a tiny script polls GET /billing/status. No Stripe
logo or wordmark: this is the merchant's page. Inline CSS/SVG/JS only. Every dynamic value
is HTML-escaped. These pages display state; they never change it (the webhook does).
"""

from dataclasses import dataclass
from decimal import Decimal
from html import escape
from string import Template

from app.services.billing import CheckoutSummary
from app.services.tenancy import PlanLimits

FALLBACK_MERCHANT = "Metering & Billing"
POLL_MS = 1500
MAX_POLLS = 40  # ~1 minute, then the page says it is still waiting

_CSS = """
:root{--bg:#f6f9fc;--card:#fff;--ink:#0a2540;--slate:#425466;--muted:#697386;--line:#e3e8ee;
--accent:#635bff;--accent-2:#5851ea;--green:#1ea672;--green-soft:#e3f8ef;--ok-bg:#d7f7c2;
--ok-ink:#006908;--wait-bg:#ebeef1;--wait-ink:#545969;--sandbox:#1d3557;
--ease:cubic-bezier(.2,.8,.2,1)}
*{box-sizing:border-box}
html,body{margin:0;background:var(--bg)}
body{color:var(--ink);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,
"Helvetica Neue",Ubuntu,sans-serif;-webkit-font-smoothing:antialiased;min-height:100vh;
display:flex;flex-direction:column;align-items:center;justify-content:center;
padding:24px 16px}
.merchant{display:flex;align-items:center;gap:10px;font-size:14px;font-weight:500;
color:var(--ink);margin-bottom:16px;animation:fade .5s var(--ease) both}
.store{width:28px;height:28px;border-radius:50%;background:#fff;display:grid;place-items:center;
color:var(--muted);box-shadow:0 0 0 1px var(--line)}
.store svg{width:14px;height:14px}
.sandbox{display:inline-flex;align-items:center;gap:4px;background:var(--sandbox);color:#fff;
font-size:11px;font-weight:600;padding:3px 7px;border-radius:4px;line-height:1;
letter-spacing:.2px}
.sandbox svg{width:11px;height:11px}
.card{position:relative;width:100%;max-width:440px;background:var(--card);border-radius:12px;
overflow:hidden;box-shadow:0 15px 35px rgba(50,50,93,.1),0 5px 15px rgba(0,0,0,.07);
animation:rise .6s var(--ease) both}
.bar{position:absolute;inset:0 0 auto;height:3px;background:rgba(99,91,255,.12);overflow:hidden;
transition:opacity .5s var(--ease)}
.bar::after{content:"";position:absolute;inset:0 auto 0 0;width:40%;background:var(--accent);
border-radius:3px;animation:slide 1.3s var(--ease) infinite}
html[data-state=active] .bar,html[data-state=none] .bar{opacity:0}
.body{padding:28px 32px 24px}
.hero{text-align:center}
.mark{width:52px;height:52px;margin:0 auto 12px;display:block}
.mark .bg{fill:var(--green-soft);transform-origin:center;animation:pop .5s var(--ease) .15s both}
.mark .ring{fill:none;stroke:var(--green);stroke-width:2.5;stroke-dasharray:151;
stroke-dashoffset:151;transform:rotate(-90deg);transform-origin:center;
animation:draw .7s var(--ease) .25s forwards}
.mark .tick{fill:none;stroke:var(--green);stroke-width:3;stroke-linecap:round;
stroke-linejoin:round;stroke-dasharray:30;stroke-dashoffset:30;
animation:draw .4s var(--ease) .8s forwards}
.mark.cancel .bg{fill:var(--wait-bg)}
.mark.cancel .ring,.mark.cancel .tick{stroke:var(--wait-ink)}
h1{margin:0;font-size:20px;font-weight:600;letter-spacing:-.2px}
.amount{margin:2px 0 0;font-size:30px;font-weight:600;letter-spacing:-.6px;line-height:1.2}
.lead{margin:6px auto 0;max-width:340px;color:var(--slate);font-size:13.5px}
.swap{display:grid}
.swap>*{grid-area:1/1;transition:opacity .4s var(--ease),transform .4s var(--ease)}
.when-active{opacity:0;transform:translateY(6px)}
html[data-state=active] .when-active{opacity:1;transform:none}
html[data-state=active] .when-pending{opacity:0;transform:translateY(-6px)}
.details{margin:20px 0 0;padding:0;list-style:none;border-top:1px solid var(--line)}
.details li{display:flex;justify-content:space-between;align-items:center;gap:16px;
padding:10px 0;border-bottom:1px solid var(--line);font-size:14px}
.details .k{color:var(--muted)}
.details .v{text-align:right;color:var(--ink);font-weight:500}
.stagger>*,.details li{animation:rise .5s var(--ease) both;
animation-delay:calc(var(--i,0) * 60ms + 350ms)}
.pill{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:600;
padding:3px 9px;border-radius:999px;justify-self:end}
.pill.wait{background:var(--wait-bg);color:var(--wait-ink)}
.pill.ok{background:var(--ok-bg);color:var(--ok-ink)}
html[data-state=active] .pill.ok{animation:pop .45s var(--ease)}
.pill svg{width:12px;height:12px}
.spin{width:10px;height:10px;border-radius:50%;border:2px solid currentColor;
border-right-color:transparent;animation:spin .8s linear infinite}
.ref{font:12px/1.4 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color:var(--slate);
max-width:210px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:400;
display:inline-block;vertical-align:middle}
.order{margin-top:14px;background:var(--bg);border-radius:8px;padding:0 16px;
interpolate-size:allow-keywords}
.order summary{list-style:none;cursor:pointer;display:flex;align-items:center;gap:8px;
padding:12px 0;font-size:14px;font-weight:500;color:var(--ink)}
.order summary::-webkit-details-marker{display:none}
.order summary .chev{width:12px;height:12px;color:var(--muted);transition:transform .3s var(--ease)}
.order[open] summary .chev{transform:rotate(180deg)}
.order summary .sum{margin-left:auto;font-weight:600}
.order summary:focus-visible{outline:2px solid rgba(99,91,255,.35);outline-offset:2px;
border-radius:4px}
.order::details-content{block-size:0;overflow:hidden;opacity:0;
transition:block-size .35s var(--ease),opacity .3s var(--ease),
content-visibility .35s allow-discrete}
.order[open]::details-content{block-size:auto;opacity:1}
.order .lines{padding:0 0 12px;border-top:1px solid var(--line)}
.line{display:flex;justify-content:space-between;gap:16px;font-size:14px;padding:8px 0 0}
.line .n{font-weight:500}
.line .d{font-size:12px;color:var(--muted);font-weight:400}
.line .a{text-align:right;font-weight:500;white-space:nowrap}
.line.total{border-top:1px solid var(--line);margin-top:10px;padding-top:10px;font-weight:600}
.note{margin:14px 0 0;font-size:13px;color:var(--muted);text-align:center;max-height:0;
opacity:0;overflow:hidden;transition:opacity .4s var(--ease),max-height .4s var(--ease)}
html[data-state=slow] .note{max-height:60px;opacity:1}
.btn{display:block;margin-top:18px;height:44px;line-height:44px;text-align:center;
border-radius:8px;background:var(--accent);color:#fff;font-size:15px;font-weight:600;
text-decoration:none;box-shadow:0 4px 6px rgba(50,50,93,.11),0 1px 3px rgba(0,0,0,.08);
transition:transform .15s var(--ease),box-shadow .15s var(--ease),background .15s}
.btn:hover{background:var(--accent-2);transform:translateY(-1px);
box-shadow:0 7px 14px rgba(50,50,93,.1),0 3px 6px rgba(0,0,0,.08)}
.btn:active{transform:none}
.btn:focus-visible{outline:3px solid rgba(99,91,255,.35);outline-offset:2px}
.hint{display:block;margin-top:12px;text-align:center;font-size:13px;color:var(--muted)}
.hint code{font:12px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color:var(--slate)}
.foot{margin:16px 0 0;font-size:12px;color:var(--muted);text-align:center;
animation:fade .6s var(--ease) .6s both}
@keyframes rise{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:none}}
@keyframes fade{from{opacity:0}to{opacity:1}}
@keyframes pop{0%{transform:scale(.6);opacity:0}70%{transform:scale(1.06);opacity:1}
100%{transform:scale(1)}}
@keyframes draw{to{stroke-dashoffset:0}}
@keyframes slide{0%{transform:translateX(-100%)}100%{transform:translateX(260%)}}
@keyframes spin{to{transform:rotate(360deg)}}
@media (prefers-reduced-motion:reduce){*,*::before,*::after{animation-duration:.01ms!important;
animation-delay:0s!important;animation-iteration-count:1!important;
transition-duration:.01ms!important}.mark .ring,.mark .tick{stroke-dashoffset:0}}
@media (max-width:480px){body{padding:24px 12px}.body{padding:32px 20px 24px}
.amount{font-size:30px}}
"""

_STORE = (
    "<svg viewBox='0 0 16 16' aria-hidden='true'><path d='M2.5 6.5V13h11V6.5M1.5 3h13l-1 3.5h-11z"
    "M6.5 13V9.5h3V13' fill='none' stroke='currentColor' stroke-width='1.3' "
    "stroke-linejoin='round'/></svg>"
)
_BOX = (
    "<svg viewBox='0 0 16 16' aria-hidden='true'><path d='M8 1.5 14 4.75v6.5L8 14.5 2 11.25v-6.5z"
    "M2 4.75 8 8l6-3.25M8 8v6.5' fill='none' stroke='currentColor' stroke-width='1.4' "
    "stroke-linejoin='round'/></svg>"
)
_MARK_OK = (
    "<svg class='mark' viewBox='0 0 64 64' aria-hidden='true'>"
    "<circle class='bg' cx='32' cy='32' r='30'/><circle class='ring' cx='32' cy='32' r='24'/>"
    "<path class='tick' d='M22 33l7 7 13-14'/></svg>"
)
_MARK_CANCEL = (
    "<svg class='mark cancel' viewBox='0 0 64 64' aria-hidden='true'>"
    "<circle class='bg' cx='32' cy='32' r='30'/><circle class='ring' cx='32' cy='32' r='24'/>"
    "<path class='tick' d='M25 25l14 14M39 25 25 39'/></svg>"
)
_TICK_SMALL = (
    "<svg viewBox='0 0 12 12' aria-hidden='true'><path d='m2.5 6.2 2.3 2.3 4.7-4.9' "
    "fill='none' stroke='currentColor' stroke-width='1.8' stroke-linecap='round' "
    "stroke-linejoin='round'/></svg>"
)

_CHEVRON = (
    "<svg class='chev' viewBox='0 0 12 12' aria-hidden='true'><path d='m3 4.5 3 3 3-3' "
    "fill='none' stroke='currentColor' stroke-width='1.6' stroke-linecap='round' "
    "stroke-linejoin='round'/></svg>"
)

# Polls GET /billing/status and flips <html data-state>, which drives every transition.
_POLL_JS = Template(
    """<script>
(function () {
  var root = document.documentElement, url = root.getAttribute("data-status-url"), n = 0;
  if (root.getAttribute("data-state") !== "pending" || !url) return;
  function again(ms) {
    if (++n < $max) setTimeout(tick, ms); else root.setAttribute("data-state", "slow");
  }
  function tick() {
    fetch(url, {headers: {Accept: "application/json"}, cache: "no-store"})
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.status === "active") root.setAttribute("data-state", "active");
        else again($poll);
      })
      .catch(function () { again($poll * 2); });
  }
  setTimeout(tick, 900);
})();
</script>"""
)

_LAYOUT = Template(
    """<!doctype html>
<html lang="en" data-state="$state"$status_attr>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
$noscript<title>$title</title>
<style>$css</style>
</head>
<body>
$merchant
<main class="card">$bar<div class="body">$body</div></main>
<p class="foot">$foot</p>
$script
</body>
</html>"""
)


@dataclass(frozen=True)
class SuccessState:
    """Whether the verified webhook has upgraded the tenant yet."""

    active: bool
    known: bool  # False when we could not tell which tenant this session belongs to


def money(cents: int, currency: str) -> str:
    whole, part = divmod(abs(cents), 100)
    sign = "-" if cents < 0 else ""
    if currency.lower() == "usd":
        return f"{sign}${whole:,}.{part:02d}"
    return f"{sign}{whole:,}.{part:02d} {escape(currency.upper())}"


def unit_price(unit_amount_decimal: str | None, currency: str) -> str:
    """Stripe stores metered prices in cents with up to 12 decimals; show dollars per unit."""
    if not unit_amount_decimal:
        return ""
    dollars = format((Decimal(unit_amount_decimal) / 100).normalize(), "f")
    symbol = "$" if currency.lower() == "usd" else ""
    return f"{symbol}{dollars} per unit"


def _merchant(name: str, sandbox: bool) -> str:
    badge = f"<span class='sandbox'>{_BOX}Sandbox</span>" if sandbox else ""
    return (
        f"<header class='merchant'><span class='store'>{_STORE}</span>"
        f"<span>{escape(name)}</span>{badge}</header>"
    )


def _li(i: int, key: str, value: str) -> str:
    return f"<li style='--i:{i}'><span class='k'>{key}</span><span class='v'>{value}</span></li>"


def _status_pill(plan_name: str) -> str:
    return (
        "<span class='swap'>"
        "<span class='pill wait when-pending'><span class='spin'></span>Activating</span>"
        f"<span class='pill ok when-active'>{_TICK_SMALL}{escape(plan_name)} · Active</span>"
        "</span>"
    )


def _order(s: CheckoutSummary) -> str:
    rows = []
    for line in s.lines:
        if line.amount_cents is None:
            rate = escape(unit_price(line.unit_amount_decimal, s.currency))
            amount = f"Price varies<div class='d'>{rate}</div>"
        else:
            amount = money(line.amount_cents, s.currency)
        rows.append(
            f"<div class='line'><div><div class='n'>{escape(line.name)}</div>"
            f"<div class='d'>{escape(line.detail)}</div></div><div class='a'>{amount}</div></div>"
        )
    total = money(s.amount_total, s.currency)
    rows.append(
        f"<div class='line total'><div>Total paid today</div><div class='a'>{total}</div></div>"
    )
    return (
        "<details class='order' style='--i:6'><summary>Order summary"
        f"<span class='sum'>{total}</span>{_CHEVRON}</summary>"
        f"<div class='lines'>{''.join(rows)}</div></details>"
    )


def success_page(
    summary: CheckoutSummary | None,
    pro: PlanLimits | None,
    state: SuccessState,
    base_url: str,
) -> str:
    merchant = summary.merchant if summary else FALLBACK_MERCHANT
    plan_name = pro.name if pro else "Pro"
    if state.active:
        page_state = "active"
    elif summary is not None:
        page_state = "pending"  # poll even if the tenant is not known yet
    else:
        page_state = "none"

    amount = ""
    if summary:
        amount = f"<p class='amount'>{money(summary.amount_total, summary.currency)}</p>"
    receipt = (
        f" A receipt was sent to <strong>{escape(summary.email)}</strong>."
        if summary and summary.email
        else ""
    )
    lead = (
        "<p class='lead swap'>"
        f"<span class='when-pending'>Paid to {escape(merchant)}. We're activating your "
        f"{escape(plan_name)} plan now.{receipt}</span>"
        f"<span class='when-active'>Paid to {escape(merchant)}. Your {escape(plan_name)} plan "
        f"is active.{receipt}</span></p>"
    )

    rows = [_li(0, "Plan", _status_pill(plan_name))]
    if pro:
        includes = f"{pro.api_calls:,} API calls · {pro.ai_tokens:,} AI tokens / mo"
        rows.append(_li(1, "Includes", includes))
    if summary:
        ref = escape(summary.session_id)
        rows.append(_li(2, "Reference", f"<span class='ref' title='{ref}'>{ref}</span>"))

    body = (
        f"<div class='hero'>{_MARK_OK}<h1>Payment successful</h1>{amount}{lead}</div>"
        f"<ul class='details'>{''.join(rows)}</ul>"
        "<div class='stagger'>"
        + (_order(summary) if summary else "")
        + "<p class='note'>Still waiting for Stripe to confirm. This can take a minute; "
        "refresh the page to check again.</p>"
        "<a class='btn' style='--i:7' href='/docs#/metering/get_usage_usage_get'>"
        "View your usage</a>"
        "</div>"
    )

    status_attr = script = noscript = ""
    if page_state == "pending" and summary:
        status_url = f"/billing/status?session_id={summary.session_id}"
        status_attr = f' data-status-url="{escape(status_url)}"'
        script = _POLL_JS.substitute(max=MAX_POLLS, poll=POLL_MS)
        noscript = '<noscript><meta http-equiv="refresh" content="4"></noscript>\n'

    live = summary.livemode if summary else False
    return _LAYOUT.substitute(
        state=page_state,
        status_attr=status_attr,
        noscript=noscript,
        title=f"Payment successful · {escape(merchant)}",
        css=_CSS,
        merchant=_merchant(merchant, sandbox=not live),
        bar="<div class='bar' aria-hidden='true'></div>",
        body=body,
        foot="Payments are processed by Stripe."
        + ("" if live else " Sandbox: no real money was moved."),
        script=script,
    )


def cancel_page(pro: PlanLimits | None, free: PlanLimits | None, merchant: str | None) -> str:
    name = merchant or FALLBACK_MERCHANT
    free_name = escape(free.name) if free else "Free"
    rows = [_li(0, "Current plan", f"<span class='pill wait'>{free_name}</span>")]
    if free:
        rows.append(
            _li(1, "Included", f"{free.api_calls:,} API calls · {free.ai_tokens:,} AI tokens")
        )
    if pro:
        rows.append(
            _li(2, "With Pro", f"{pro.api_calls:,} API calls · {pro.ai_tokens:,} AI tokens")
        )
    body = (
        f"<div class='hero'>{_MARK_CANCEL}<h1>Checkout canceled</h1>"
        "<p class='lead'>You weren't charged and your plan hasn't changed. "
        "You can upgrade any time.</p></div>"
        f"<ul class='details'>{''.join(rows)}</ul>"
        "<div class='stagger'>"
        "<a class='btn' style='--i:4' href='/docs#/billing/create_checkout_billing_checkout_post'>"
        "Upgrade to Pro</a>"
        "<span class='hint' style='--i:5'>Upgrading starts a new checkout from the API</span>"
        "</div>"
    )
    return _LAYOUT.substitute(
        state="none",
        status_attr="",
        noscript="",
        title=f"Checkout canceled · {escape(name)}",
        css=_CSS,
        merchant=_merchant(name, sandbox=True),
        bar="",
        body=body,
        foot="Payments are processed by Stripe. Nothing was charged.",
        script="",
    )
