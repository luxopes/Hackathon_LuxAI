"""Presentation layer for the public marketplace pages.

Renders the overview dashboard, the payments list and the payment receipt in a
shared design system (sidebar shell + card layout). Payments are simulated Lux
Coins; every page states that explicitly.
"""
import html
import json
from datetime import datetime, timezone

from services import CURRENCY, SERVICES

SITE_CSS = """
:root{
  --nav-bg:#0c1322;--nav-bg-2:#131d33;--nav-text:#c7d2e5;--nav-muted:#7e8ba6;
  --page:#f6f7f9;--card:#fff;--border:#e4e8ef;--text:#0f172a;--muted:#64748b;
  --green:#157347;--green-bg:#e4f6ea;--amber:#8a6100;--amber-bg:#fdf3d8;
  --red:#b3261e;--red-bg:#fde7e5;--blue:#1d4ed8;--blue-bg:#e8efff;
  --shadow:0 1px 2px rgba(15,23,42,.04),0 8px 24px rgba(15,23,42,.05);
  --radius:14px;--serif:ui-serif,Georgia,"Times New Roman",serif;
  --sans:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace;
}
*{box-sizing:border-box}
html,body{margin:0;background:var(--page);color:var(--text);font-family:var(--sans);font-size:15px;line-height:1.55}
a{color:var(--blue);text-decoration:none}a:hover{text-decoration:underline}
.layout{display:grid;grid-template-columns:250px 1fr;min-height:100vh}
.side{background:var(--nav-bg);color:var(--nav-text);display:flex;flex-direction:column;padding:16px 14px;position:sticky;top:0;height:100vh}
.brand{display:flex;align-items:center;gap:11px;padding:6px 8px 0}
.brand .mark{width:26px;height:26px;border-radius:8px;background:linear-gradient(135deg,#22d3ee,#a78bfa 55%,#f472b6);flex:none}
.brand .name{font-size:21px;letter-spacing:.16em;font-weight:600;color:#fff}
.tagline{font-size:11.5px;color:var(--nav-muted);padding:6px 10px 16px;line-height:1.4}
.nav{display:flex;flex-direction:column;gap:2px}
.nav a{display:flex;align-items:center;gap:11px;padding:9px 10px;border-radius:9px;color:var(--nav-text);font-size:13.5px}
.nav a:hover{background:var(--nav-bg-2);text-decoration:none}
.nav a.active{background:#1d2b47;color:#fff}
.nav svg{width:17px;height:17px;stroke:currentColor;fill:none;stroke-width:1.7;flex:none}
.nav .gap{height:14px}
.side .me{margin-top:auto;border-top:1px solid #1e2a42;padding-top:12px;display:flex;align-items:center;gap:10px}
.avatar{width:30px;height:30px;border-radius:50%;background:#22304d;color:#cbd5e1;display:grid;place-items:center;font-size:12px;font-weight:600;flex:none}
.me .who{font-size:13px;color:#e2e8f0;line-height:1.3}
.me .sub{font-size:11.5px;color:var(--nav-muted)}
.main{min-width:0;display:flex;flex-direction:column}
.top{display:flex;align-items:center;gap:16px;padding:11px 26px;background:#fff;border-bottom:1px solid var(--border);position:sticky;top:0;z-index:5}
.crumbs{display:flex;align-items:center;gap:9px;color:var(--muted);font-size:13px}
.crumbs .sep{color:#cbd5e1}
.crumbs b{color:var(--text);font-weight:600}
.crumbs .mono{font-family:var(--mono);font-size:12.5px}
.search{margin-left:auto;display:flex;align-items:center;gap:9px;background:#f8fafc;border:1px solid var(--border);border-radius:9px;padding:7px 11px;min-width:330px;color:var(--muted)}
.search input{border:none;background:transparent;outline:none;font-size:13px;color:var(--text);flex:1;font-family:var(--sans)}
.search kbd{font:11px var(--mono);border:1px solid var(--border);border-radius:5px;padding:1px 6px;color:var(--muted);background:#fff}
.btn{display:inline-flex;align-items:center;gap:7px;border-radius:9px;padding:8px 14px;font-size:13px;font-weight:600;border:1px solid transparent;cursor:pointer;background:#fff;font-family:var(--sans)}
.btn:hover{text-decoration:none}
.btn.dark{background:#0f172a;color:#fff}
.btn.dark:hover{background:#1e293b}
.btn.light{border-color:var(--border);color:var(--text)}
.btn.light:hover{background:#f8fafc}
.btn.icon{padding:8px 11px;color:var(--muted)}
.content{padding:26px 30px 40px;width:100%;max-width:1320px}
.kicker{display:flex;align-items:center;gap:12px;font-size:11.5px;letter-spacing:.16em;text-transform:uppercase;color:var(--muted);font-weight:700}
h1.hero{font-family:var(--serif);font-weight:600;font-size:clamp(34px,4.4vw,52px);letter-spacing:-.5px;margin:12px 0 10px;line-height:1.06}
.lede{color:#475569;max-width:700px;margin:0 0 8px}
.gen{color:var(--muted);font-size:13px}
.hero-row{display:flex;align-items:flex-start;justify-content:space-between;gap:28px}
.hero-actions{display:flex;gap:10px;align-items:center;justify-content:flex-end;padding-top:6px;flex-wrap:wrap}
.hero-side{display:flex;flex-direction:column;gap:14px;align-items:flex-end}
.deco{width:330px;height:172px;border-radius:14px;overflow:hidden;position:relative;border:1px solid var(--border);background:linear-gradient(160deg,#f2f5f9,#e2e9f2);flex:none}
.deco svg{position:absolute;inset:0;width:100%;height:100%}
.deco .cap{position:absolute;right:14px;bottom:12px;font-size:10.5px;letter-spacing:.16em;text-transform:uppercase;color:#5b6b82;text-align:right;line-height:1.6;font-weight:600}
.card{background:var(--card);border:1px solid var(--border);border-radius:var(--radius);box-shadow:var(--shadow);margin-top:18px;overflow:hidden}
.card-head{display:flex;align-items:flex-start;gap:13px;padding:17px 20px 10px}
.card-head .ico{width:34px;height:34px;border-radius:10px;background:#0f172a;display:grid;place-items:center;flex:none;margin-top:2px}
.card-head .ico svg{width:17px;height:17px;stroke:#fff;fill:none;stroke-width:1.8}
.card-head h2{font-size:17px;margin:0;font-weight:650}
.card-head .sub{color:var(--muted);font-size:13px;margin-top:2px}
.card-head .right{margin-left:auto;color:var(--muted);font-size:12.5px;padding-top:4px;white-space:nowrap}
.card-body{padding:4px 20px 18px}
.facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(228px,1fr));gap:18px 26px;padding:8px 0 2px}
.fact label{display:block;font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin-bottom:5px;font-weight:700}
.fact .v{font-size:14.5px;overflow-wrap:anywhere}
.fact .v.mono{font-family:var(--mono);font-size:12.5px}
.fact .sub{color:var(--muted);font-size:12.5px;margin-top:4px}
.table-wrap{overflow-x:auto;margin:0 -4px}
table{width:100%;border-collapse:collapse;font-size:13.5px;min-width:640px}
.num{white-space:nowrap}
.nowrap{white-space:nowrap}
th{font-size:10.5px;letter-spacing:.09em;text-transform:uppercase;color:var(--muted);text-align:left;font-weight:700;padding:9px 10px;border-bottom:1px solid var(--border);white-space:nowrap}
td{padding:12px 10px;border-bottom:1px solid #eef1f6;vertical-align:top}
tr:last-child td{border-bottom:none}
.num{text-align:right;font-variant-numeric:tabular-nums}
.mono{font-family:var(--mono);font-size:12.5px}
.muted{color:var(--muted)}
.pill{display:inline-block;border-radius:999px;padding:3px 11px;font-size:11px;font-weight:700;letter-spacing:.03em;white-space:nowrap}
.pill.ok{background:var(--green-bg);color:var(--green)}
.pill.warn{background:var(--amber-bg);color:var(--amber)}
.pill.err{background:var(--red-bg);color:var(--red)}
.pill.neutral{background:#eef1f6;color:#475569}
.pill.blue{background:var(--blue-bg);color:var(--blue)}
.steps{display:flex;align-items:flex-start;padding:10px 0 8px;overflow-x:auto}
.step{display:flex;flex-direction:column;gap:5px;min-width:180px;position:relative;padding-right:30px}
.step .dot{width:18px;height:18px;border-radius:50%;border:2px solid #cbd5e1;background:#fff;display:grid;place-items:center;color:#fff;font-size:11px;position:relative;z-index:1}
.step.done .dot{background:var(--green);border-color:var(--green)}
.step .t{font-size:13.5px;font-weight:600}
.step.done .t{color:var(--text)}
.step:not(.done) .t{color:var(--muted)}
.step .ts{font-size:12px;color:var(--muted);font-family:var(--mono)}
.step:not(:last-child):after{content:"";position:absolute;left:16px;right:4px;top:8px;height:2px;background:#e2e8f0}
.step.done:not(:last-child):after{background:var(--green)}
.foot{display:flex;justify-content:space-between;align-items:center;gap:16px;color:var(--muted);font-size:12.5px;padding:26px 4px 4px;border-top:1px solid var(--border);margin-top:34px}
.copy{cursor:pointer;border:none;background:transparent;color:#94a3b8;font-size:12px;padding:0 3px;vertical-align:middle}
.copy:hover{color:var(--text)}
.menu{position:relative;display:inline-block}
.menu>summary{list-style:none;cursor:pointer}
.menu>summary::-webkit-details-marker{display:none}
.menu[open]>summary{background:#f1f5f9}
.menu .items{position:absolute;right:0;top:calc(100% + 6px);background:#fff;border:1px solid var(--border);border-radius:10px;box-shadow:var(--shadow);padding:6px;min-width:220px;z-index:10;text-align:left}
.menu .items a,.menu .items button{display:block;width:100%;text-align:left;padding:8px 10px;border-radius:7px;color:var(--text);font-size:13px;background:none;border:none;cursor:pointer;font-family:var(--sans)}
.menu .items a:hover,.menu .items button:hover{background:#f1f5f9;text-decoration:none}
details.raw{margin-top:14px}
details.raw summary{cursor:pointer;color:var(--blue);font-size:13.5px}
pre{background:#f8fafc;border:1px solid var(--border);border-radius:10px;padding:14px;overflow:auto;font:12px var(--mono);color:#0f172a;max-height:420px;margin:10px 0 0}
pre.preview{white-space:pre-wrap;max-height:280px}
.notice{border-left:3px solid #f59e0b;background:#fffbeb;padding:13px 16px;border-radius:8px;margin-top:14px;font-size:13px;line-height:1.65;color:#78350f}
.grid-cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(215px,1fr));gap:14px;margin-top:18px}
.stat{background:var(--card);border:1px solid var(--border);border-radius:12px;box-shadow:var(--shadow);padding:14px 16px}
.stat label{display:block;font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);font-weight:700}
.stat b{font-size:22px;font-weight:650;display:block;margin-top:4px}
.stat .sub{color:var(--muted);font-size:12.5px;margin-top:2px}
.offer{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:15px 16px;box-shadow:var(--shadow)}
.offer .svc{font-weight:650;font-size:14.5px}
.offer .seller{color:var(--muted);font-size:12.5px;margin-top:2px}
.offer .price{font-family:var(--serif);font-size:24px;margin-top:8px}
.offer .del{color:#475569;font-size:12.5px;margin-top:6px;line-height:1.5}
.toolbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding:4px 0 10px}
input.filter,select.filter{background:#fff;color:var(--text);border:1px solid var(--border);border-radius:9px;padding:8px 11px;font-size:13px;font-family:var(--sans)}
input.filter:focus,select.filter:focus{outline:2px solid #bfdbfe;border-color:#93c5fd}
tr.hidden{display:none}
@media (max-width:1080px){.layout{grid-template-columns:1fr}.side{position:static;height:auto}.deco{display:none}}
"""

ICONS = {
    "overview": '<svg viewBox="0 0 24 24"><rect x="3" y="3" width="7.5" height="7.5" rx="1.6"/><rect x="13.5" y="3" width="7.5" height="7.5" rx="1.6"/><rect x="3" y="13.5" width="7.5" height="7.5" rx="1.6"/><rect x="13.5" y="13.5" width="7.5" height="7.5" rx="1.6"/></svg>',
    "payments": '<svg viewBox="0 0 24 24"><path d="M3 8.5 12 4l9 4.5-9 4.5z"/><path d="M3 12.5 12 17l9-4.5"/><path d="M3 16.5 12 21l9-4.5"/></svg>',
    "receipt": '<svg viewBox="0 0 24 24"><path d="M6 3h12v18l-3-2-3 2-3-2-3 2z"/><path d="M9 8h6M9 12h6M9 16h4"/></svg>',
    "agents": '<svg viewBox="0 0 24 24"><circle cx="9" cy="8" r="3.2"/><path d="M3.5 20c.6-3.4 2.9-5.2 5.5-5.2s4.9 1.8 5.5 5.2"/><path d="M16.5 9.2h4M18.5 7.2v4"/></svg>',
    "code": '<svg viewBox="0 0 24 24"><path d="m8.5 8-4 4 4 4M15.5 8l4 4-4 4"/></svg>',
    "docs": '<svg viewBox="0 0 24 24"><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15.5H6.5A2.5 2.5 0 0 0 4 21z"/><path d="M8 7.5h8M8 11h6"/></svg>',
    "status": '<svg viewBox="0 0 24 24"><path d="M3 12h4l2.5-6 4 12L16 12h5"/></svg>',
    "support": '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M9.6 9.3a2.5 2.5 0 1 1 3.4 2.3c-.7.3-1 .8-1 1.6v.3"/><circle cx="12" cy="16.6" r=".9" fill="currentColor" stroke="none"/></svg>',
    "movement": '<svg viewBox="0 0 24 24"><path d="M4 8h13l-3-3M20 16H7l3 3"/></svg>',
    "lifecycle": '<svg viewBox="0 0 24 24"><path d="M4 6h16M4 12h10M4 18h7"/><circle cx="18" cy="12" r="2.2"/><circle cx="15" cy="18" r="2.2"/></svg>',
    "contract": '<svg viewBox="0 0 24 24"><path d="M7 3h10v18H7z"/><path d="M10 7.5h4M10 11h4M10 14.5h3"/></svg>',
    "verify": '<svg viewBox="0 0 24 24"><path d="M12 3.5 5 6.5v6c0 4 3 7 7 8 4-1 7-4 7-8v-6z"/><path d="m9 12 2.2 2.2L15.5 10"/></svg>',
    "integrity": '<svg viewBox="0 0 24 24"><path d="M5 20V9.5L12 4l7 5.5V20z"/><path d="M9.5 14.5V20M14.5 14.5V20M4 20h16"/></svg>',
    "raw": '<svg viewBox="0 0 24 24"><path d="M9 7 4.5 12 9 17M15 7l4.5 5L15 17"/></svg>',
    "search": '<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="6.5"/><path d="m16 16 4 4"/></svg>',
    "download": '<svg viewBox="0 0 24 24"><path d="M12 4v11m0 0 4-4m-4 4-4-4M5 19h14"/></svg>',
    "external": '<svg viewBox="0 0 24 24"><path d="M14 5h5v5M19 5l-8 8M18 14v4a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h4"/></svg>',
}


def escape(value):
    return html.escape(str(value))


def _iso(stamp):
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat()


def _stamp(value):
    return escape(str(value))


def _copy(value):
    return f'<button class="copy" type="button" data-copy="{escape(value)}" title="Copy">⧉</button>'


def _usd(amount):
    # Simulované částky se zobrazují jako dolary; jedna jednotka ledgeru = 1 USD.
    return f"${float(amount):,.2f}"


def _money(amount, currency=CURRENCY):
    return f'{_usd(amount)} <span class="muted">simulated</span>'


def _path_state(state):
    if state == "PAID":
        return "ok"
    if state == "REFUNDED":
        return "warn"
    return "neutral"


def _nav(base, active):
    def item(key, label, href, icon):
        cls = ' class="active"' if key == active else ""
        return f'<a href="{href}"{cls}>{ICONS[icon]}<span>{label}</span></a>'
    home = base or "."
    docs_page = base + "docs"
    return f"""<nav class="nav">
{item("overview", "Overview", home, "overview")}
{item("payments", "Payments", base + "payments", "payments")}
{item("agents", "Agents", base + "web/", "agents")}
{item("developers", "Developers", docs_page + "#api", "code")}
<div class="gap"></div>
{item("docs", "Docs", docs_page, "docs")}
{item("status", "Status", base + "health", "status")}
{item("support", "Support", docs_page + "#support", "support")}
</nav>"""


def shell(base, active, crumbs, content, search_hint="Search by job ID, transaction or seller…",
          description="Agentic economy · simulated payments"):
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(crumbs[-1][0])} · USD</title>
<link rel="stylesheet" href="{base}assets/site.css">
</head><body>
<div class="layout">
<aside class="side">
  <div class="brand"><span class="mark"></span><span class="name">LUX</span></div>
  <div class="tagline">Autonomous value flow.</div>
  {_nav(base, active)}
  <div class="me"><span class="avatar">L</span><div><div class="who">Simulated USD</div><div class="sub">{escape(description)}</div></div></div>
</aside>
<div class="main">
  <header class="top">
    <div class="crumbs">{_crumbs(base, crumbs)}</div>
    <form class="search" action="{base}payments" method="get">
      {ICONS["search"].replace("<svg", '<svg width="15" height="15"')}
      <input type="search" name="q" placeholder="{escape(search_hint)}" aria-label="Search">
      <kbd>/</kbd>
    </form>
    <a class="btn dark" href="{base}web/">Agent console {ICONS["external"].replace("<svg", '<svg width="13" height="13"')}</a>
  </header>
  <main class="content">
{content}
    <div class="foot"><span>© 2026 Lux Protocol · <a href="{base}payments">Payments</a> · <a href="{base}health">Status</a> · <span title="Every response carries Cache-Control: no-store">no-store</span></span><span>Built for an agentic economy.</span></div>
  </main>
</div>
</div>
<script>
document.querySelectorAll('.copy').forEach((button) => button.addEventListener('click', async () => {{
  try {{ await navigator.clipboard.writeText(button.dataset.copy); button.textContent = '✓'; }}
  catch {{ button.textContent = '!'; }}
  setTimeout(() => {{ button.textContent = '⧉'; }}, 1200);
}}));
const copyLink = document.getElementById('copy-link');
if (copyLink) copyLink.addEventListener('click', async () => {{
  try {{ await navigator.clipboard.writeText(location.href); copyLink.textContent = 'Link copied ✓'; }}
  catch {{ copyLink.textContent = 'Copy failed'; }}
  setTimeout(() => {{ copyLink.textContent = 'Copy link'; }}, 1500);
}});
const keyHint = document.querySelector('.search input');
document.addEventListener('keydown', (event) => {{
  if (event.key === '/' && document.activeElement !== keyHint) {{ event.preventDefault(); keyHint.focus(); }}
}});
</script>
</body></html>"""


def _crumbs(base, crumbs):
    parts = []
    for index, (label, href) in enumerate(crumbs):
        last = index == len(crumbs) - 1
        shown = f"<span class='mono'>{escape(label)}</span>" if label.startswith("job-") or label.startswith("session-") else escape(label)
        if last:
            parts.append(f"<b>{shown}</b>")
        else:
            target = base + href if href else (base or ".")
            parts.append(f"<a href='{target}'>{shown}</a><span class='sep'>›</span>")
    return "".join(parts)


def _card(icon, title, subtitle, body, right=""):
    return f"""<section class="card">
  <div class="card-head"><span class="ico">{ICONS[icon]}</span>
    <div><h2>{escape(title)}</h2><div class="sub">{escape(subtitle)}</div></div>
    {f'<div class="right">{right}</div>' if right else ''}
  </div>
  <div class="card-body">{body}</div>
</section>"""


def _fact(label, value, mono=False, sub="", copy_value=None):
    cls = "v mono" if mono else "v"
    button = _copy(copy_value) if copy_value else ""
    return (f'<div class="fact"><label>{escape(label)}</label>'
            f'<div class="{cls}">{escape(value)}{button}</div>'
            f'{f"<div class=sub>{escape(sub)}</div>" if sub else ""}</div>')


def _deco():
    return """<div class="deco">
<svg viewBox="0 0 330 172" preserveAspectRatio="none" aria-hidden="true">
  <defs><linearGradient id="g1" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#e8eef6"/><stop offset="1" stop-color="#d6e0ec"/></linearGradient></defs>
  <rect width="330" height="172" fill="url(#g1)"/>
  <g fill="none" stroke="#b9c6d8" stroke-width="1">
    <path d="M-10 128C40 96 78 138 118 118s62-58 112-40 78 46 122 26"/>
    <path d="M-10 142C44 112 84 152 126 132s60-54 108-38 76 44 120 26"/>
    <path d="M-10 156C48 128 90 166 134 148s58-50 104-36 74 42 118 26"/>
    <path d="M-10 100C36 74 70 108 106 92s60-48 104-32 72 38 130 22"/>
    <path d="M-10 72C30 52 62 78 96 64s58-40 98-26 66 32 146 18"/>
    <path d="M-10 44C26 30 54 50 84 38s54-30 90-18 60 24 166 12"/>
  </g>
  <g fill="none" stroke="#8fa2ba" stroke-width="1.2" opacity=".55">
    <path d="M236 172c-14-22-4-40 8-52 10-10 24-16 40-18"/>
    <path d="M262 172c-10-18-2-32 8-42 8-8 20-13 33-15"/>
  </g>
</svg>
<span class="cap">Agents<br>transact<br>the real world</span></div>"""


def docs_page():
    components = [
        ("Agent console", "3069", "LSL (compiled binary)", "Conversational agent (LuxAI Flash), tool-call feed, chat API, serves the web console"),
        ("Marketplace", "3070", "Python + SQLite", "Wallets, escrow, offers, jobs, ledger, verification, public pages"),
        ("Sellers ×5", "3081–3085", "LSL (one binary)", "Deliver purchased services: cart audits over HTTP checks, model services with seller attestation"),
        ("Speech sidecar", "3071", "Python", "ElevenLabs text-to-speech for stored agent messages (message-index API, disk cache)"),
    ]
    component_rows = "".join(
        f"<tr><td><b>{escape(name)}</b></td><td class='mono'>{escape(port)}</td>"
        f"<td class='mono'>{escape(stack)}</td><td>{escape(role)}</td></tr>"
        for name, port, stack, role in components)
    endpoints = [
        ("GET", "/", "Overview dashboard: offers, sessions, ledger invariant"),
        ("GET", "/payments", "Payments list with totals, revenue per seller/service and filters"),
        ("GET", "/receipt/{job_id}", "Payment receipt: ledger movements, escrow lifecycle, contract, verification, hashes"),
        ("GET", "/api/payments", "JSON: summary, revenue, services and every payment with its ledger movements"),
        ("GET", "/api/receipt/{job_id}", "JSON: the full receipt payload (receipt_version 1.0)"),
        ("GET", "/api/offers", "JSON: active offers with prices and delivery terms"),
        ("GET", "/api/dashboard", "JSON: public overview incl. the issued == accounted invariant"),
        ("GET", "/health", "Liveness probe: ok + simulated payments marker"),
        ("GET", "/api/state", "Console state for the frontend (lite=1 returns wallet, status and revisions only)"),
        ("POST", "/api/chat", "Start one agent turn with a message (one at a time, 2 s cooldown)"),
        ("POST", "/api/catalog", "Fetch the live catalog without purchasing"),
        ("POST", "/api/cancel", "Stop the running turn; a funded job is always settled or refunded first"),
        ("POST", "/api/new", "Reset the conversation; wallets and the ledger stay on the marketplace"),
        ("POST", "/api/speak", "Read one stored agent message aloud (ElevenLabs, index-only API)"),
        ("POST", "/api/topup/stripe", "Create a Stripe test-mode card checkout in USD (console, signed-in users)"),
        ("POST", "/api/topup/stripe/confirm", "Verify the Stripe payment and credit the wallet once (console)"),
        ("POST", "/api/topup/stripe/sandbox", "Server-side test helper: confirmed Stripe test payment (not exposed in the UI)"),
    ]
    endpoint_rows = "".join(
        f"<tr><td><span class='pill {('ok' if method == 'GET' else 'blue')}'>{method}</span></td>"
        f"<td class='mono'>{escape(path)}</td><td>{escape(description)}</td></tr>"
        for method, path, description in endpoints)
    lifecycle = [
        ("Discovery", "The agent calls <span class='mono'>fetch_offers</span> (a real Flash tool call) and reads the live catalog."),
        ("Escrow", "<span class='mono'>POST /api/jobs</span> moves the price from the buyer's <b>available</b> to <b>locked</b> balance with a unique idempotency key."),
        ("Delivery", "The seller executes and streams a live preview; the console renders it while the job runs."),
        ("Verification", "The marketplace checks the delivery against the agreed contract: structure, syntax, cited sources, execution receipts."),
        ("Settlement", "Verified delivery releases escrow to the seller (<b>PAID</b>); a failed one refunds the buyer (<b>REFUNDED</b>) and the agent buys elsewhere."),
    ]
    lifecycle_rows = "".join(
        f"<tr><td class='nowrap'><b>{index + 1}. {escape(title)}</b></td><td>{body}</td></tr>"
        for index, (title, body) in enumerate(lifecycle))
    content = f"""<div class="hero-row">
  <div>
    <div class="kicker"><span>Documentation</span><span class="pill neutral">SIMULATED PAYMENTS</span></div>
    <h1 class="hero">How it works</h1>
    <p class="lede">An agent-to-agent marketplace with escrow, verification and dispute resolution.
    Everything is public and inspectable: every payment has a receipt with its full ledger trail.</p>
  </div>
</div>
{_card("docs", "Architecture", "Four processes on loopback, one public origin",
       f"<div class='table-wrap'><table><tr><th>Component</th><th>Port</th><th>Stack</th><th>Role</th></tr>{component_rows}</table></div>")}
{_card("lifecycle", "Payment lifecycle", "One purchase from discovery to settlement",
       f"<div class='table-wrap'><table>{lifecycle_rows}</table></div>")}
{_card("code", "API", "Everything the console and the public pages use",
       f"<div class='table-wrap'><table><tr><th>Method</th><th>Path</th><th>Description</th></tr>{endpoint_rows}</table></div>")}
{_card("integrity", "Invariants and honest disclosure", "What is enforced, what is simulated",
       """<ul>
<li><b>Fully autonomous turns.</b> From the first message the agent inspects the live catalog, buys the right service in escrow, verifies the delivery and settles or refunds it on its own; it asks for confirmation nowhere. A dialog pops up only when a required input is genuinely missing (for example the text to translate), and the saved answer resumes the same task.</li>
<li><b>Simulated payments.</b> Every amount is <b>simulated US dollars</b> in a central SQLite ledger — no real money, no blockchain. One ledger unit is one dollar, amounts render with the dollar sign, and everything is labelled simulated. Transaction IDs are scoped to this database.</li>
<li><b>Card top-ups run in Stripe test mode.</b> The buyer pays on a real Stripe Checkout page (sandbox): the card flow, the amounts in USD, the redirect and the signed confirmation are genuine Stripe objects in test mode, while the credited dollars and every internal settlement stay simulated and labelled as such.</li>
<li><b>Caps hold.</b> A wallet can never spend beyond its budget; the market-wide invariant <span class="mono">issued == accounted</span> is checked on every page.</li>
<li><b>Nothing pays twice.</b> Idempotency keys plus unique constraints allow exactly one escrow and one settlement per job.</li>
<li><b>Structural verification only.</b> Deliveries are checked for structure, Python syntax, cited sources and execution receipts — this does not guarantee general semantic correctness.</li>
</ul>
<div class="notice">The reference implementation (agent console, marketplace, LSL sellers, deployment) lives in a private
source repository; ask the team for access. The frontend is a plain poller: <span class="mono">GET /api/state?lite=1</span>
every 500 ms and a full fetch whenever a revision changes.</div>""")}
{_card("agents", "Run it yourself", "From zero on a Linux x86_64 machine",
       """<div class="table-wrap"><table>
<tr><th>Step</th><th>Command</th></tr>
<tr><td class="nowrap">1. Install LSL</td><td class="mono">curl -LO https://lsl.lux-ai.cz/downloads/lsl-0.8.9-linux-x86_64.tar.gz</td></tr>
<tr><td class="nowrap">2. Unpack + install</td><td class="mono">tar -xzf lsl-0.8.9-linux-x86_64.tar.gz &amp;&amp; ./lsl-0.8.9/bin/lsl-install "$HOME/.local"</td></tr>
<tr><td class="nowrap">3. Model client</td><td class="mono">lsl install aikit</td></tr>
<tr><td class="nowrap">4. Start the stack</td><td class="mono">scripts/run-local.sh</td></tr>
</table></div>
<p class="muted">The script builds the LSL binaries, generates tokens and configuration, starts the marketplace,
five sellers and the console, and waits for their health endpoints. Details live in <span class="mono">backend/docs/SETUP.md</span>.</p>""")}
<section class="card" id="support">
  <div class="card-head"><span class="ico">{ICONS["support"]}</span>
    <div><h2>Support</h2><div class="sub">Questions about the demo, the data or the API</div></div>
  </div>
  <div class="card-body">
    <p>The marketplace is a hackathon project. For access to the source repository, a walkthrough of the
    ledger and verification model, or to reproduce any receipt, reach out to the team. Every number on this
    site can be re-derived from the receipt JSON: movements, balances, hashes and checks.</p>
    <p class="muted">Nothing here is financial advice and no real funds are involved.</p>
  </div>
</section>"""
    return shell("", "docs", [("Docs", "docs")], content,
                 description="agentic economy · simulated payments")


def dashboard_page(data):
    services = data["services"]
    service_cards = "".join(
        f'<div class="offer"><div class="svc">{escape(service["name"])}</div>'
        f'<div class="seller">{len([o for o in data["offers"] if o["capability"] == capability])} offers</div>'
        f'<div class="del">{escape(service["delivery"])}</div></div>'
        for capability, service in services.items())
    session_cards = []
    for session in data["sessions"][:8]:
        rows = "".join(
            f'<tr><td>{escape(services.get(job["contract"]["capability"], {}).get("name", job["contract"]["capability"]))}</td>'
            f'<td class="mono">{escape(job["seller_id"])}</td>'
            f'<td class="num">{_money(job["price"])}</td>'
            f'<td class="nowrap"><span class="pill {_path_state(job["state"])}">{escape(job["state"])}</span> '
            f'<a href="receipt/{escape(job["id"])}">receipt ↗</a></td></tr>'
            for job in session["jobs"])
        session_cards.append(f"""<section class="card">
  <div class="card-head"><span class="ico">{ICONS["lifecycle"]}</span>
    <div><h2>{escape(session["title"])}</h2>
    <div class="sub mono">{escape(session["id"])} · budget {_money(session["budget"])} · fixture {escape(session["fixture"])}</div></div>
    <div class="right">available {session["wallet"]["available"]} · locked {session["wallet"]["locked"]}</div>
  </div>
  <div class="card-body"><div class="table-wrap"><table>
    <tr><th>Service</th><th>Seller</th><th class="num">Price</th><th>State</th></tr>{rows}
  </table></div></div>
</section>""")
    invariant = data["invariant"]
    stats = f"""<div class="grid-cards">
  <div class="stat"><label>Sessions</label><b>{len(data["sessions"])}</b><div class="sub">recent, newest first</div></div>
  <div class="stat"><label>Offers</label><b>{len(data["offers"])}</b><div class="sub">{len(services)} services, 5 sellers</div></div>
  <div class="stat"><label>Issued == accounted</label><b style="color:{'var(--green)' if invariant['holds'] else 'var(--red)'}">{'HOLDS' if invariant['holds'] else 'BROKEN'}</b><div class="sub">{_usd(invariant["issued"])} == {_usd(invariant["accounted"])}</div></div>
</div>"""
    content = f"""<div class="hero-row">
  <div>
    <div class="kicker"><span>Marketplace overview</span><span class="pill neutral">SIMULATED PAYMENTS</span></div>
    <h1 class="hero">Simulated USD</h1>
    <p class="lede">The agent orders work, verifies delivery and resolves disputes. Sellers are paid only
    for deliveries that pass the agreed contract; failed ones are refunded automatically.</p>
    <div class="gen">Every payment is a simulated test credit in the central ledger.</div>
  </div>
  <div class="hero-actions">
    <a class="btn dark" href="payments">All payments {ICONS["external"].replace('<svg', '<svg width="13" height="13"')}</a>
    <a class="btn light" href="web/">Open agent console</a>
  </div>
</div>
{stats}
<section class="card"><div class="card-head"><span class="ico">{ICONS["overview"]}</span>
  <div><h2>Marketplace services</h2><div class="sub">{len(data["offers"])} active offers across 5 sellers</div></div></div>
  <div class="card-body"><div class="grid-cards" style="margin-top:2px">{service_cards}</div></div>
</section>
{''.join(session_cards)}"""
    return shell("", "overview",
                 [("Overview", "")], content,
                 description="simulated US dollars · overview")


def payments_page(data, query="", chain=None):
    summary, invariant = data["summary"], data["invariant"]
    chain = chain or {"ok": True, "rows": 0, "head": ""}
    currency = data["currency"]
    rows = []
    for payment in data["payments"]:
        rows.append(
            f"<tr class='payment-row' data-state='{escape(payment['state'])}' "
            f"data-search='{escape((payment['id'] + ' ' + payment['session_id'] + ' ' + payment['seller_id'] + ' ' + (payment['capability'] or '')).lower())}'>"
            f"<td class='mono'>{escape(payment['created_at'][:19].replace('T', ' '))}</td>"
            f"<td class='mono'><a href='receipt/{escape(payment['id'])}'>{escape(payment['id'])}</a></td>"
            f"<td class='mono'>{escape(payment['seller_id'])}</td>"
            f"<td>{escape(SERVICES.get(payment['capability'], {}).get('name', payment['capability'] or ''))}</td>"
            f"<td class='num'>{_money(payment['price'], currency)}</td>"
            f"<td><span class='pill {_path_state(payment['state'])}'>{escape(payment['state'])}</span></td>"
            f"<td class='num'>{str(payment['settled_in_seconds']) + ' s' if payment['settled_in_seconds'] is not None else '—'}</td>"
            f"<td class='mono'>{' · '.join(escape(t['action']) for t in payment['transactions'])}</td>"
            f"<td class='nowrap'><a href='receipt/{escape(payment['id'])}'>receipt ↗</a></td></tr>")
    revenue_rows = "".join(
        f"<tr><td class='mono'>{escape(r['seller'])}</td><td class='num'>{r['paid_count']}</td>"
        f"<td class='num'>{_money(r['paid_total'], currency)}</td>"
        f"<td class='num'>{r['refunded_count']}</td><td class='num'>{_money(r['refunded_total'], currency)}</td></tr>"
        for r in data["revenue"])
    service_rows = "".join(
        f"<tr><td>{escape(SERVICES.get(s['capability'], {}).get('name', s['capability']))}</td>"
        f"<td class='num'>{s['count']}</td><td class='num'>{_money(s['total'], currency)}</td></tr>"
        for s in data["services"])
    stats = f"""<div class="grid-cards">
  <div class="stat"><label>Payments</label><b>{summary["total"]}</b><div class="sub">all sessions</div></div>
  <div class="stat"><label>Paid</label><b style="color:var(--green)">{summary["paid_count"]}</b><div class="sub">{_money(summary["paid_total"], currency)} released to sellers</div></div>
  <div class="stat"><label>Refunded</label><b style="color:var(--amber)">{summary["refunded_count"]}</b><div class="sub">{_money(summary["refunded_total"], currency)} returned to buyers</div></div>
  <div class="stat"><label>Success rate</label><b>{summary["success_rate"] if summary["success_rate"] is not None else "—"}{" %" if summary["success_rate"] is not None else ""}</b><div class="sub">verified deliveries</div></div>
</div>"""
    content = f"""<div class="hero-row">
  <div>
    <div class="kicker"><span>Payments</span><span class="pill neutral">SIMULATED PAYMENTS</span>
      <span class="pill {'ok' if invariant['holds'] else 'err'}">INVARIANT {'HOLDS' if invariant['holds'] else 'BROKEN'}</span></div>
    <h1 class="hero">All payments</h1>
    <p class="lede">Every agent purchase settled on this marketplace, newest first. Open any row's
    receipt for the complete audit trail: ledger movements, escrow lifecycle, contract and hashes.</p>
    <div class="gen">Generated {escape(data["generated_at"])} · {_usd(invariant["issued"])} issued == {_usd(invariant["accounted"])} accounted</div>
  </div>
  {_deco()}
</div>
{stats}
{_card("payments", "Sellers", "Revenue per seller from verified deliveries", f"<div class='table-wrap'><table><tr><th>Seller</th><th class='num'>Paid jobs</th><th class='num'>Revenue</th><th class='num'>Refunded jobs</th><th class='num'>Refunded</th></tr>{revenue_rows}</table></div>")}
{_card("overview", "Services", "Paid volume per service", f"<div class='table-wrap'><table><tr><th>Service</th><th class='num'>Paid jobs</th><th class='num'>Revenue</th></tr>{service_rows}</table></div>")}
{_card("receipt", "Payment list", "Filter by state or search across ids, sessions, sellers and services",
       f'''<div class="toolbar">
  <input class="filter" id="q" type="search" placeholder="Search id, session, seller, service…" style="min-width:300px" value="{escape(query)}">
  <select class="filter" id="state"><option value="">All states</option><option value="PAID">Paid</option><option value="REFUNDED">Refunded</option></select>
  <span class="muted" id="count"></span>
</div>
<div class="table-wrap"><table id="payments"><tr><th>Created (UTC)</th><th>Payment ID</th><th>Seller</th><th>Service</th><th class="num">Amount</th><th>State</th><th class="num">Settled in</th><th>Ledger movements</th><th></th></tr>
{"".join(rows)}</table></div>
<div class="toolbar" style="margin-top:12px">
  <span class="pill {'ok' if chain['ok'] else 'err'}">LEDGER CHAIN {'INTACT' if chain['ok'] else 'BROKEN'}</span>
  <span class="muted">{chain['rows']} movements · head <span class="mono">{chain.get('head', chain.get('broken_at', ''))[:16]}…</span> ·
  <a href="../api/ledger/export">export JSONL</a> · <a href="../api/ledger/export?format=csv">CSV</a> · <a href="../api/ledger/check">integrity check</a></span>
</div>
<div class="notice"><strong>Honest disclosure.</strong> Simulated test credits only — no real money and no
blockchain. “Nothing pays twice” is enforced by unique idempotency keys and database constraints;
“caps hold” by wallet budgets and the invariant above. The ledger is an append-only hash chain, so any
edited row breaks the chain at a visible point.</div>
<script>
const rows = [...document.querySelectorAll('.payment-row')];
const q = document.getElementById('q'), state = document.getElementById('state'), count = document.getElementById('count');
function apply() {{
  const needle = q.value.trim().toLowerCase(), wanted = state.value;
  let shown = 0;
  for (const row of rows) {{
    const okSearch = !needle || row.dataset.search.includes(needle);
    const okState = !wanted || row.dataset.state === wanted;
    row.classList.toggle('hidden', !(okSearch && okState));
    if (okSearch && okState) shown++;
  }}
  count.textContent = shown + ' / ' + rows.length;
}}
q.addEventListener('input', apply); state.addEventListener('change', apply); apply();
</script>''',
       right=f"{len(data['payments'])} records")}"""
    return shell("", "payments", [("Payments", "payments")], content)


def _steps(data):
    job = data["job"]
    steps = [("Contract created", _iso(job["created"]), True)]
    labels = {"ESCROW_LOCKED": "Escrow locked", "RESULT_DELIVERED": "Delivery recorded",
              "DELIVERY_FAILED": "Delivery failed", "PAYMENT_RELEASED": "Payment released",
              "REFUND_AUTHORIZED_BY_CONTRACT": "Refund authorized"}
    for entry in data["job_timeline"]:
        if entry["action"] in labels:
            steps.append((labels[entry["action"]], entry["created_at"], True))
    settled = job["state"] in ("PAID", "REFUNDED")
    steps.append(("Completed", "" if not settled else data["generated_at"], settled))
    return steps


def receipt_page(data, job_id):
    job, payment, verification = data["job"], data["payment"], data["verification"]
    currency = data["currency"]
    state = job["state"]
    balances = {entry["ledger_id"]: entry for entry in data["timeline"]}
    rows = []
    for tx in payment["transactions"]:
        balance = balances.get(tx["ledger_id"], {})
        action_class = {"ESCROW_LOCKED": "neutral", "PAYMENT_RELEASED": "ok",
                        "REFUND_AUTHORIZED_BY_CONTRACT": "warn"}.get(tx["action"], "blue")
        raw = escape(json.dumps(tx, ensure_ascii=False, sort_keys=True))
        rows.append(
            "<tr>"
            f"<td class='mono'>{escape(tx['id'])}</td>"
            f"<td class='num'>{tx['ledger_id']}</td>"
            f"<td><span class='pill {action_class}'>{escape(tx['action'])}</span></td>"
            f"<td class='num'>{_money(tx['amount'], currency)}</td>"
            f"<td class='mono'>{escape(tx['from_wallet'])}<div class='muted'>{escape(tx['from_account'])}</div></td>"
            f"<td class='mono'>{escape(tx['to_wallet'])}<div class='muted'>{escape(tx['to_account'])}</div></td>"
            f"<td class='num'>{balance.get('available_after', '—')}</td>"
            f"<td class='num'>{balance.get('locked_after', '—')}</td>"
            f"<td class='mono'>{escape(tx['created_at'])}</td>"
            f"<td><details class='raw'><summary>…</summary><pre>{raw}</pre></details></td>"
            "</tr>")
    steps = _steps(data)
    step_html = "".join(
        f"<div class='step{' done' if done else ''}'><span class='dot'>{'✓' if done else ''}</span>"
        f"<span class='t'>{escape(name)}</span>"
        f"<span class='ts'>{escape(stamp[11:19]) if stamp else '—'}</span></div>"
        for name, stamp, done in steps)
    done_count = sum(1 for _, _, done in steps if done)
    checks = verification.get("checks") or []
    check_rows = "".join(
        f"<tr><td class='mono'>{escape(c['case_id'])}</td><td class='num'>{c['expected_cents']}</td>"
        f"<td class='num'>{c['observed_cents']}</td>"
        f"<td><span class='pill {'ok' if c['passed'] else 'warn'}'>{'OK' if c['passed'] else 'DEFECT FOUND'}</span></td></tr>"
        for c in checks if type(c) is dict)
    reasons = verification.get("reasons") or []
    reason_html = "".join(f"<li>{escape(r)}</li>" for r in reasons) or "<li>No discrepancies found.</li>"
    receipts = "".join(f"<li class='mono'>{escape(r)}</li>" for r in payment["verification_receipts"]) or "<li class='muted'>None</li>"
    artifact = (job.get("result") or {}).get("artifact") or {}
    parts = []
    if artifact.get("summary"):
        parts.append(f"<p>{escape(artifact['summary'])}</p>")
    if artifact.get("content"):
        parts.append(f"<pre class='preview'>{escape(artifact['content'])}</pre>")
    if artifact.get("ideas"):
        parts.append("<ol>" + "".join(f"<li>{escape(idea)}</li>" for idea in artifact["ideas"]) + "</ol>")
    if artifact.get("code"):
        parts.append("<h3 style='font-size:13px;margin:14px 0 6px'>Python code</h3>"
                     f"<pre class='preview'>{escape(artifact['code'])}</pre>")
    if artifact.get("tests"):
        parts.append("<h3 style='font-size:13px;margin:14px 0 6px'>Tests (syntax-checked, not executed)</h3>"
                     f"<pre class='preview'>{escape(artifact['tests'])}</pre>")
    if artifact.get("sources"):
        parts.append("<h3 style='font-size:13px;margin:14px 0 6px'>Sources fetched</h3><ul>"
                     + "".join(f"<li><a href='{escape(source['url'])}' target='_blank' rel='noopener noreferrer'>{escape(source['title'])}</a></li>"
                               for source in artifact["sources"]) + "</ul>")
    delivery = "".join(parts) if parts else "<p class='muted'>No text artifact for this job (cart audits deliver server-side execution receipts, listed above).</p>"
    reconciliation, invariant = data["reconciliation"], data["invariant"]
    raw = escape(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
    contract = escape(json.dumps(job["contract"], ensure_ascii=False, indent=2, sort_keys=True))
    facts = "".join([
        _fact("Amount", _money(job["price"], currency)),
        f'<div class="fact"><label>Status</label><div class="v"><span class="pill {_path_state(state)}">{escape(state)}</span></div></div>',
        _fact("Payer (buyer wallet)", job["session_id"], mono=True, copy_value=job["session_id"]),
        _fact("Payee (seller)", job["seller_id"], mono=True, copy_value=job["seller_id"]),
        _fact("Offer / service", f"{job['offer_id']} · {job['contract'].get('capability', '')}", mono=True),
        _fact("Idempotency key", job["idempotency_key"], mono=True, copy_value=job["idempotency_key"]),
        _fact("Created (UTC)", data["session"]["created_at"]),
        _fact("Method", "Central SQLite ledger · escrow · double-entry"),
        _fact("Session", data["session"]["title"], sub=f"budget {_money(data['session']['budget'], currency)} · fixture {data['session']['fixture']}"),
        _fact("Buyer wallet (now)", f"available {data['wallets']['buyer']['available']} · locked {data['wallets']['buyer']['locked']}"),
        _fact("Seller wallet (now)", f"available {data['wallets']['seller']['available']} · locked {data['wallets']['seller']['locked']}"),
        _fact("Contract SHA-256", payment["contract_sha256"], mono=True, copy_value=payment["contract_sha256"]),
        (_fact("Receipt signature", f"{data['signature']['alg']} · {data['signature']['key_id']}", mono=True,
               sub=f"payload SHA-256 {data['signature']['payload_sha256'][:24]}…",
               copy_value=data["signature"]["value_base64"])
         if data.get("signature") else
         _fact("Receipt signature", "not signed", sub="created before receipt signing was enabled")),
    ])
    if data.get("signature"):
        signed_block = (f"<div class='toolbar'><span class='pill ok'>SIGNED RECEIPT</span>"
                        f"<span class='muted'>Ed25519 signature over the canonical settlement payload · verify offline with "
                        f"<span class='mono'>tools/verify_receipt.py</span> and the "
                        f"<a href='../.well-known/proofpay-keys.json'>published public key</a></span></div>")
    else:
        signed_block = ("<div class='toolbar'><span class='pill neutral'>UNSIGNED RECEIPT</span>"
                        "<span class='muted'>created before receipt signing was enabled</span></div>")
    hero_actions = f"""<div class="hero-side">
  <div class="hero-actions">
    <button type="button" class="btn dark" id="copy-link">Copy link</button>
    <details class="menu"><summary class="btn light">Download {ICONS["download"].replace('<svg', '<svg width="13" height="13"')}</summary>
      <div class="items">
        <a href="../api/receipt/{escape(job_id)}" download="{escape(job_id)}.json">Receipt JSON</a>
        <button type="button" class="copy" data-copy="{escape(job_id)}">Copy job ID</button>
        <button type="button" class="copy" data-copy="{escape(payment['contract_sha256'])}">Copy contract SHA-256</button>
      </div>
    </details>
    <details class="menu"><summary class="btn light icon">···</summary>
      <div class="items">
        <a href="../payments">All payments</a>
        <a href="../payments?q={escape(job['seller_id'])}">Payments of {escape(job['seller_id'])}</a>
        <a href="..">Marketplace overview</a>
      </div>
    </details>
  </div>
  {_deco()}
</div>"""
    content = f"""<div class="hero-row">
  <div>
    <div class="kicker"><span>Payment receipt</span><span class="pill {_path_state(state)}">{escape(state)}</span>
      <span class="pill neutral">SIMULATED PAYMENTS</span></div>
    <h1 class="hero">{_money(job["price"], currency)}</h1>
    <p class="lede">Complete audit trail of one agent-to-agent purchase: contract, escrow, verification,
    settlement and the central ledger entries behind every movement.</p>
    <div class="gen">Generated {escape(data["generated_at"])} · <span class="mono">{escape(job_id)}</span></div>
  </div>
  {hero_actions}
</div>
{_card("receipt", "Transaction details", "Identity, parties and hashes for this payment", f"<div class='facts'>{facts}</div>")}
{_card("movement", "Money movement", "Immutable ledger records for this payment",
       f"<div class='table-wrap'><table><tr><th>TX ID</th><th class='num'>Ledger #</th><th>Action</th><th class='num'>Amount</th><th>From</th><th>To</th><th class='num'>Buyer after</th><th class='num'>Buyer locked</th><th>Timestamp (UTC)</th><th>Details</th></tr>{''.join(rows)}</table></div>",
       right=f"{len(payment['transactions'])} transactions")}
{_card("lifecycle", "Settlement lifecycle", "Key stages of this payment",
       f"<div class='steps'>{step_html}</div>", right=f"{done_count} of {len(steps)} completed")}
{_card("contract", "Contract", "What the seller agreed to deliver", f"<pre>{contract}</pre><p class='muted'>Contract SHA-256: <span class='mono'>{escape(payment['contract_sha256'])}</span> {_copy(payment['contract_sha256'])}</p>")}
{_card("verify", "Verification", verification.get("scope", ""),
       f'''<div class="toolbar"><span class="pill {'ok' if verification['valid_delivery'] else 'warn'}">{'VALID DELIVERY' if verification['valid_delivery'] else 'NOT VERIFIED'}</span></div>
<ul>{reason_html}</ul>
{f"<h3 style='font-size:13px;margin:14px 0 0'>Cart checks</h3><div class='table-wrap'><table><tr><th>Case</th><th class='num'>Expected (cents)</th><th class='num'>Observed (cents)</th><th>Result</th></tr>{check_rows}</table></div><p class='muted' style='margin-top:8px'>A defect found and reported with execution receipts is a valid audit delivery — the audit did its job.</p>" if check_rows else ""}
<h3 style="font-size:13px;margin:14px 0 0">Execution receipts</h3><ul>{receipts}</ul>
<p class="muted">Delivery SHA-256: <span class="mono">{escape(payment['result_sha256'] or '—')}</span></p>
{delivery}''')}
{_card("integrity", "Integrity", "Reconciliation and market-wide invariants",
       f'''{signed_block}
<div class="toolbar">
  <span class="pill {'ok' if reconciliation['balanced'] else 'err'}">ESCROW RECONCILIATION {'BALANCED' if reconciliation['balanced'] else 'MISMATCH'}</span>
  <span class="muted">locked {_money(reconciliation['escrow_locked'], currency)} · settled {_money(reconciliation['settled'], currency)} · price {_money(reconciliation['price'], currency)} · {reconciliation['movements']} movements</span>
</div>
<div class="toolbar">
  <span class="pill {'ok' if invariant['holds'] else 'err'}">MARKET INVARIANT {'HOLDS' if invariant['holds'] else 'BROKEN'}</span>
  <span class="muted">issued {_usd(invariant['issued'])} == accounted {_usd(invariant['accounted'])}</span>
</div>
<div class="notice"><strong>Honest disclosure.</strong> Payments are simulated US dollars in a central SQLite
ledger on this marketplace; nothing here is a blockchain transaction and transaction IDs are scoped to this
database. The structure mirrors a real payment rail: unique transaction IDs, double-entry movements, escrow,
idempotency, execution receipts and content hashes. Delivery checks are structural (syntax, cited sources,
execution receipts) and do not guarantee general semantic correctness.</div>''')}
<details class="raw"><summary>Raw receipt JSON</summary><pre>{raw}</pre></details>"""
    return shell("../", "payments",
                 [("Payments", "payments"), ("Receipts", "payments"), (job_id, "")], content,
                 search_hint="Search by job ID, transaction or seller…")
