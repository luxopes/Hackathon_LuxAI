"""Presentation layer for the public marketplace pages.

Renders the overview dashboard, the payments list, the payment receipt and the
docs in the agent console's design: the same shell (sidebar, top bar, hero,
stat cards, panels, tables, badges), the same light default and dark theme
(frontend/style.css is the reference). Amounts are simulated US dollars and
every page says so.
"""
import html
import json
from datetime import datetime, timezone

from services import CURRENCY, SERVICES

# Tokens and components mirror frontend/style.css (the agent console) so the
# public pages and the console read as one product.
SITE_CSS = """
:root{
  --ink:#17202b;--muted:#697586;--line:#e7eaee;--surface:#fff;--canvas:#f8f9fb;--blue:#245dee;--green:#078a4d;
  --amber:#ae7622;--red:#bf384d;
  --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace;
}
*{box-sizing:border-box}
body{margin:0;font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:var(--ink);background:var(--canvas);font-size:14px;line-height:1.5}
button,input,textarea,select{font:inherit}
button,a,input,select,textarea{outline-offset:4px}
button{border:1px solid var(--line);border-radius:6px;background:white;padding:10px 15px;color:var(--ink);cursor:pointer;font-weight:550}
button:hover{background:#f3f5f8;border-color:#ccd3dd}
a{color:inherit;text-decoration:none}
a.button,summary.button{display:inline-flex;align-items:center;justify-content:center;gap:7px;border:1px solid var(--line);border-radius:6px;background:white;padding:10px 15px;font-weight:550;cursor:pointer}
a.button:hover,summary.button:hover{background:#f3f5f8;border-color:#ccd3dd}
.primary,button.primary,a.button.primary{background:var(--ink);color:#fff;border-color:var(--ink)}
.primary:hover,button.primary:hover,a.button.primary:hover{background:#303b48;border-color:#303b48;color:#fff}
.full{width:100%}
.small{padding:6px 10px;font-size:11px}
.text-muted,.muted{color:var(--muted)}
.text-muted{font-size:12px}
.link{color:var(--blue)}
.link:hover{text-decoration:underline}
.mono{font-family:var(--mono);font-size:11.5px}
.num{text-align:right;font-variant-numeric:tabular-nums}
.nowrap{white-space:nowrap}
/* ---------- sidebar ---------- */
.sidebar{position:fixed;inset:0 auto 0 0;width:224px;background:#fff;border-right:1px solid var(--line);display:flex;flex-direction:column;z-index:20;overflow-y:auto}
.brand{height:78px;display:flex;align-items:center;gap:12px;padding:22px;font-size:20px;font-weight:750;letter-spacing:-.6px}
.brand svg{width:31px;height:31px;color:var(--ink);flex:none}
.brand small{display:block;color:var(--muted);font-size:12px;font-weight:400;letter-spacing:0}
.nav-label{font-size:11px;letter-spacing:1px;color:var(--muted);margin:27px 24px 12px}
.sidebar nav{padding:0 12px;display:flex;flex-direction:column}
.nav-item{display:flex;align-items:center;gap:13px;border-radius:5px;padding:12px;margin-bottom:4px;color:#526075;font-size:14px}
.nav-item:hover{background:#f7f8fa}
.nav-item.active{background:#f0f2f5;color:var(--ink);font-weight:600;position:relative}
.nav-item.active:before{content:"";position:absolute;left:-12px;height:23px;width:3px;background:var(--ink)}
.nav-icon{width:19px;height:19px;display:inline-flex;align-items:center;justify-content:center;flex:none}
.nav-icon svg{width:19px;height:19px;fill:none;stroke:currentColor;stroke-width:1.7}
.sidebar-bottom{margin-top:auto;padding:18px 16px}
.credit-box{border:1px solid var(--line);padding:16px;border-radius:7px}
.credit-box>span{font-size:12px;color:var(--muted)}
.credit-box strong{display:block;font-size:25px;letter-spacing:-1px;margin:6px 0 13px}
.credit-box small{font-size:14px;letter-spacing:0}
.signature{display:flex;flex-direction:column;gap:4px;font-size:12px;margin:32px 8px 8px}
.signature span{color:var(--muted)}
/* ---------- topbar ---------- */
.topbar{height:78px;position:fixed;inset:0 0 auto 224px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;padding:0 28px;gap:20px;background:#ffffffed;backdrop-filter:blur(12px);z-index:15}
.topbar nav{height:100%;display:flex;gap:26px}
.top-link{display:flex;align-items:center;border-bottom:2px solid transparent;padding:0 3px;font-size:14px;color:#536074}
.top-link:hover{color:var(--ink)}
.top-link.active{border-color:var(--ink);color:var(--ink);font-weight:600}
.search-wrap{position:relative;margin-left:auto;min-width:280px}
.search{border:1px solid var(--line);display:flex;align-items:center;gap:8px;padding:7px 10px;border-radius:6px;color:var(--muted);background:#f9fafb;margin:0}
.search:focus-within{border-color:#c3cddb;background:#fff}
.search input{border:0;background:transparent;width:100%;font-size:12px;outline:0;color:var(--ink)}
.search kbd{font-size:11px;background:#eef0f3;border-radius:3px;padding:0 5px}
.bell-wrap,.profile-wrap{position:relative}
.bell{position:relative;border:0;background:transparent;padding:6px;color:#5b697e}
.bell:hover{background:#f3f5f8}
.bell svg{width:19px;height:19px;fill:none;stroke:currentColor;stroke-width:1.7}
.bell-count{position:absolute;top:2px;right:2px;min-width:16px;height:16px;border-radius:9px;background:#245dee;color:#fff;font-size:10px;font-weight:700;display:grid;place-items:center;padding:0 4px;border:1.5px solid #fff}
.bell-count[hidden],.bell-menu[hidden],.profile-menu[hidden]{display:none}
.bell-menu{position:absolute;right:0;top:calc(100% + 8px);width:320px;background:#fff;border:1px solid var(--line);border-radius:10px;box-shadow:0 18px 45px rgba(16,24,40,.16);z-index:45;overflow:hidden}
.bell-head{display:flex;justify-content:space-between;align-items:center;padding:11px 14px;border-bottom:1px solid var(--line);font-size:12px;font-weight:600}
.bell-head span{color:var(--muted);font-weight:400}
.bell-list{max-height:320px;overflow-y:auto}
.bell-item{display:flex;gap:10px;align-items:flex-start;padding:11px 14px;border-bottom:1px solid #eef0f4}
.bell-item:last-child{border-bottom:0}
.bell-item:hover{background:#f8fafc}
.bell-item .dot-badge{width:8px;height:8px;border-radius:50%;margin-top:6px;flex:none;background:#98a4b5}
.bell-item.paid .dot-badge{background:#078a4d}
.bell-item.refund .dot-badge{background:#c98a1a}
.bell-item.error .dot-badge{background:#bf384d}
.bell-item.info .dot-badge{background:#245dee}
.bell-item b{display:block;font-size:12.5px;font-weight:600}
.bell-item time{margin-left:auto;font-size:10.5px;color:var(--muted);white-space:nowrap}
.bell-foot{display:block;padding:11px 14px;border-top:1px solid var(--line);font-size:12px;color:var(--blue);background:#fbfcfe}
.bell-empty{padding:22px 14px;text-align:center;color:var(--muted);font-size:12px}
.profile{border:0;display:flex;align-items:center;gap:10px;padding:4px 6px;background:transparent;font-size:13px;border-radius:8px}
.profile:hover{background:#f3f5f8}
.profile .chev{width:14px;height:14px;fill:none;stroke:currentColor;stroke-width:1.8;color:var(--muted)}
.profile-menu{position:absolute;right:0;top:calc(100% + 8px);background:#fff;border:1px solid var(--line);border-radius:10px;box-shadow:0 18px 45px rgba(16,24,40,.14);padding:6px;min-width:210px;z-index:40}
.profile-menu a{display:block;padding:9px 11px;border-radius:7px;font-size:12.5px}
.profile-menu a:hover{background:#f4f6f9}
.profile-menu button{display:block;width:100%;text-align:left;border:0;background:transparent;padding:9px 11px;border-radius:7px;font-size:12.5px;font-family:inherit;color:inherit;cursor:pointer}
.profile-menu button:hover{background:#f4f6f9}
.profile-menu button.pm-danger{color:#bf384d}
.pm-head{padding:8px 11px 10px;border-bottom:1px solid var(--line);margin-bottom:6px}
.pm-head b{display:block;font-size:13px}
.pm-head span{color:var(--muted);font-size:11.5px}
.avatar{border-radius:50%;width:31px;height:31px;display:inline-flex;align-items:center;justify-content:center;color:white;background:#1b2430;font-size:12px;flex:none}
/* ---------- layout ---------- */
main{margin:78px 0 0 224px;padding:24px 28px 10px;max-width:1900px}
.page-head{display:flex;justify-content:space-between;align-items:center;color:var(--muted);font-size:12px;margin-bottom:22px;gap:16px}
.page-head a:hover{color:var(--ink)}
.page-head .sep{margin:0 6px;color:#b6bfcc}
.demo-label{font-size:10px;letter-spacing:1.2px;border:1px solid var(--line);padding:2px 7px;border-radius:3px;white-space:nowrap}
.page{display:flex;flex-direction:column;gap:20px;max-width:1700px}
.split{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:20px}
/* ---------- hero ---------- */
.hero{border:1px solid #e1e5ec;border-radius:10px;min-height:236px;padding:32px 34px;position:relative;overflow:hidden;background:#eef2f7 url("hero.jpg") right center / cover no-repeat}
.hero:before{content:"";position:absolute;inset:0;background:linear-gradient(90deg,#f8fafc 0%,#f8fafc 34%,rgba(248,250,252,.94) 48%,rgba(248,250,252,.45) 72%,rgba(248,250,252,0) 96%)}
.hero-text{position:relative;max-width:620px}
.hero-quote{position:absolute;top:34px;right:34px;margin:0;text-align:right;font-family:Georgia,"Times New Roman",serif;font-size:15px;line-height:1.5;color:#4c5b70;max-width:230px}
.hero-quote .dash{display:block;width:36px;height:1px;background:#9aa7b8;margin:12px 0 0 auto}
.eyebrow{font-size:10px;letter-spacing:1.7px;font-weight:650;color:#55657c;display:flex;align-items:center;gap:10px;flex-wrap:wrap}
h1{font-size:36px;line-height:1.13;letter-spacing:-1.3px;margin:15px 0 14px;font-weight:700;max-width:520px}
h2{font-size:22px;letter-spacing:-.5px;margin:0 0 8px;font-weight:700}
h3{font-size:13px;letter-spacing:-.2px;margin:16px 0 6px}
.hero p{font-size:16px;color:#5b697e;max-width:540px;line-height:1.6;margin:0 0 22px}
.hero .gen{font-size:12px;color:#6b788c;margin:-12px 0 18px}
.hero-actions{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
/* ---------- stats ---------- */
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(178px,1fr));gap:12px}
.stat{display:flex;gap:12px;align-items:flex-start;background:#fff;border:1px solid var(--line);border-radius:9px;padding:14px 15px}
.stat-icon{width:34px;height:34px;border-radius:9px;display:grid;place-items:center;flex:none}
.stat-icon svg{width:19px;height:19px;fill:none;stroke:currentColor;stroke-width:1.7}
.stat-icon.gray{background:#f0f2f5;color:#657086}
.stat-icon.green{background:#e8f7ef;color:#078a4d}
.stat-icon.blue{background:#eaf1ff;color:#245dee}
.stat-icon.amber{background:#fff3df;color:#ae7622}
.stat-icon.red{background:#fcebed;color:#bf384d}
.stat span{font-size:12px;color:var(--muted)}
.stat strong{display:block;font-size:23px;letter-spacing:-.8px;margin:4px 0 2px}
.stat strong.good{color:var(--green)}
.stat strong.bad{color:var(--red)}
.stat small{font-size:11px;color:#8490a1}
/* ---------- panels ---------- */
.panel{border:1px solid var(--line);background:var(--surface);border-radius:7px;overflow:hidden}
.panel-head{display:flex;justify-content:space-between;align-items:center;padding:18px 20px;gap:14px;flex-wrap:wrap}
.panel-head h2{font-size:17px;letter-spacing:-.4px;margin:0}
.panel-note{margin:-14px 20px 12px;color:var(--muted);font-size:12px}
.panel-body{padding:0 20px 18px;font-size:13px}
.panel-body>.table-wrap{margin:0 -20px}
.panel-body>.table-wrap:last-child{margin-bottom:-18px}
.panel-body ul,.panel-body ol{margin:6px 0;padding-left:20px;line-height:1.7}
.panel-body p{line-height:1.65}
.table-wrap{overflow:auto}
table{border-collapse:collapse;width:100%;text-align:left;white-space:nowrap}
th{font-size:11px;color:#768296;background:#fafbfc;font-weight:500;padding:10px 16px;border-top:1px solid var(--line);border-bottom:1px solid var(--line)}
td{padding:14px 16px;border-bottom:1px solid #eef0f4;font-size:12px;vertical-align:middle}
tr:last-child td{border-bottom:0}
tr.row-link{cursor:pointer}
tr.row-link:hover,tbody tr:hover{background:#fafbfc}
td.wrap{white-space:normal;min-width:260px}
tr.hidden{display:none}
.task-cell{display:flex;align-items:center;gap:12px}
.task-cell b{display:block;font-weight:600;font-size:12px}
.task-cell small{display:block;color:#8590a0;font-size:11px;margin-top:3px}
.agent-icon{height:36px;width:36px;display:inline-flex;align-items:center;justify-content:center;border-radius:8px;flex-shrink:0}
.agent-icon svg{width:20px;height:20px;fill:none;stroke:currentColor;stroke-width:1.7}
.research{background:#f0eaff;color:#7644d4}
.code{background:#eaf1ff;color:#2463e5}
.design{background:#fff3df;color:#ae7622}
.data{background:#e7f6ef;color:#078a4d}
.automation{background:#eeeafe;color:#7355d5}
/* the console's dotted badge, under the name the pages already use */
.pill{display:inline-flex;font-size:10px;font-weight:550;border-radius:20px;padding:4px 9px;align-items:center;gap:5px;white-space:nowrap;letter-spacing:.02em}
.pill:before{content:"";width:4px;height:4px;border-radius:50%;background:currentColor}
.pill.ok{background:#e8f7ef;color:#078a4d}
.pill.warn{background:#fff3df;color:#9a6716}
.pill.err{background:#fcebed;color:#bf384d}
.pill.neutral{background:#f0f2f5;color:#657086}
.pill.blue{background:#eaf1ff;color:#245dee}
/* ---------- agent cards ---------- */
.agent-cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(198px,1fr));gap:12px;padding:0 16px 16px}
.agent-card{border:1px solid var(--line);border-radius:6px;padding:15px;background:#fcfcfd;display:flex;flex-direction:column}
.agent-card-head{display:flex;align-items:center;gap:10px}
.agent-card b{font-size:12px}
.agent-card p{font-size:11px;color:var(--muted);margin:3px 0 0}
.agent-card-bottom{display:flex;align-items:center;justify-content:space-between;margin-top:18px;font-size:11px}
.available{color:var(--green)}
.available:before{content:"";display:inline-block;height:5px;width:5px;background:var(--green);border-radius:50%;margin-right:5px}
/* ---------- filters, toolbar ---------- */
.filters{display:flex;gap:8px;flex-wrap:wrap}
.filter{padding:7px 13px;font-size:12px}
.filter b{color:var(--muted);margin-left:4px}
.filter.active{background:var(--ink);color:white;border-color:var(--ink)}
.filter.active b{color:#c8d0dc}
.panel-tools{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-left:auto}
.panel-tools .search{min-width:260px}
.toolbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding:6px 0}
.chain-row{display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding:14px 20px;border-top:1px solid var(--line);font-size:12px;color:var(--muted);background:#fbfcfd}
.chain-row a{color:var(--blue)}
/* ---------- facts, steps, notes ---------- */
.facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:18px 26px;padding:2px 0 4px}
.fact label{display:block;font-size:11px;color:var(--muted);margin-bottom:5px}
.fact .v{font-size:13px;font-weight:550;overflow-wrap:anywhere}
.fact .v.mono{font-family:var(--mono);font-size:11.5px;font-weight:400}
.fact .sub{color:var(--muted);font-size:11.5px;margin-top:3px}
.steps{display:flex;align-items:flex-start;padding:6px 0 4px;overflow-x:auto}
.step{display:flex;flex-direction:column;gap:5px;min-width:170px;position:relative;padding-right:26px}
.step .dot{width:20px;height:20px;border-radius:50%;border:1.5px solid #cdd4de;background:#fff;display:grid;place-items:center;color:#fff;font-size:11px;position:relative;z-index:1}
.step.done .dot{background:var(--green);border-color:var(--green)}
.step .t{font-size:12.5px;font-weight:600}
.step:not(.done) .t{color:var(--muted)}
.step .ts{font-size:11px;color:var(--muted);font-family:var(--mono)}
.step:not(:last-child):after{content:"";position:absolute;left:18px;right:6px;top:9px;height:2px;background:var(--line)}
.step.done:not(:last-child):after{background:var(--green)}
.notice{border:1px solid #f1e3c4;background:#fffaf0;padding:12px 15px;border-radius:7px;margin-top:14px;font-size:12.5px;line-height:1.65;color:#6b5320}
pre{background:#f9fafb;border:1px solid var(--line);border-radius:6px;padding:12px;overflow:auto;font:11.5px var(--mono);color:#2f3b4c;max-height:420px;margin:8px 0 0;white-space:pre}
pre.preview{white-space:pre-wrap;max-height:280px}
details.raw summary{cursor:pointer;color:var(--blue);font-size:12px}
.raw-panel{padding:16px 20px}
.copy{cursor:pointer;border:0;background:transparent;color:#98a4b5;font-size:12px;padding:0 4px;font-weight:400}
.copy:hover{color:var(--ink);background:transparent}
.menu{position:relative;display:inline-block}
.menu>summary{list-style:none}
.menu>summary::-webkit-details-marker{display:none}
.menu .items{position:absolute;right:0;top:calc(100% + 8px);background:#fff;border:1px solid var(--line);border-radius:10px;box-shadow:0 18px 45px rgba(16,24,40,.14);padding:6px;min-width:220px;z-index:10}
.menu .items a,.menu .items button{display:block;width:100%;text-align:left;padding:9px 11px;border-radius:7px;font-size:12.5px;border:0;background:none;color:var(--ink);font-weight:400}
.menu .items a:hover,.menu .items button:hover{background:#f4f6f9}
.step-num{width:26px;height:26px;border-radius:50%;background:#f0f2f5;color:#526075;display:inline-grid;place-items:center;font-size:11px;font-weight:650;flex:none}
footer{padding:25px 0;color:#8b95a3;font-size:11px;display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap}
footer a:hover{color:var(--ink)}
/* ---------- dark mode (shared verbatim by frontend/style.css and ui.py SITE_CSS) ----------
   Same layout; only colours change. Set by <html data-theme="dark">, chosen with the
   top-bar toggle (localStorage "pp-theme"; the default look stays light). */
.theme-toggle{position:relative;border:0;background:transparent;padding:6px;color:#5b697e;border-radius:6px}
.theme-toggle:hover{background:#f3f5f8}
.theme-toggle svg{width:19px;height:19px;fill:none;stroke:currentColor;stroke-width:1.7;stroke-linecap:round}
.theme-toggle .moon{display:block}.theme-toggle .sun{display:none}
:root[data-theme=dark] .theme-toggle .moon{display:none}:root[data-theme=dark] .theme-toggle .sun{display:block}
:root[data-theme=dark]{
  --ink:#e7ebf1;--muted:#98a3b4;--line:#262e3b;--surface:#131922;--canvas:#0c1016;--blue:#7aa2ff;--green:#43c98b;
  --amber:#e0a84a;--red:#f07a8c;color-scheme:dark}
:root[data-theme=dark] body{background:var(--canvas);color:var(--ink)}
:root[data-theme=dark] button,:root[data-theme=dark] a.button,:root[data-theme=dark] summary.button{background:#18202b;border-color:var(--line);color:var(--ink)}
:root[data-theme=dark] button:hover,:root[data-theme=dark] a.button:hover,:root[data-theme=dark] summary.button:hover{background:#1f2834;border-color:#36404f}
:root[data-theme=dark] .primary,:root[data-theme=dark] button.primary,:root[data-theme=dark] a.button.primary{background:#e7ebf1;color:#0c1016;border-color:#e7ebf1}
:root[data-theme=dark] .primary:hover,:root[data-theme=dark] button.primary:hover,:root[data-theme=dark] a.button.primary:hover{background:#ffffff;border-color:#ffffff;color:#0c1016}
:root[data-theme=dark] .sidebar{background:#10151d}
:root[data-theme=dark] .topbar{background:#10151dee}
:root[data-theme=dark] .nav-item,:root[data-theme=dark] .top-link{color:#a3adbd}
:root[data-theme=dark] .nav-item:hover{background:#18202b}
:root[data-theme=dark] .nav-item.active{background:#1c2430;color:var(--ink)}
:root[data-theme=dark] .top-link:hover,:root[data-theme=dark] .top-link.active{color:var(--ink)}
:root[data-theme=dark] .search{background:#161d27;border-color:var(--line)}
:root[data-theme=dark] .search:focus-within{background:#1a222d;border-color:#3a4556}
:root[data-theme=dark] .search kbd{background:#232c39;color:var(--muted)}
:root[data-theme=dark] .bell,:root[data-theme=dark] .theme-toggle,:root[data-theme=dark] .profile{background:transparent;color:#a3adbd}
:root[data-theme=dark] .bell:hover,:root[data-theme=dark] .theme-toggle:hover,:root[data-theme=dark] .profile:hover{background:#18202b}
:root[data-theme=dark] .bell-count,:root[data-theme=dark] .bell-dot{border-color:#10151d}
:root[data-theme=dark] .search-pop,:root[data-theme=dark] .bell-menu,:root[data-theme=dark] .profile-menu,:root[data-theme=dark] .menu .items{background:#161d27;border-color:var(--line);box-shadow:0 18px 50px rgba(0,0,0,.5)}
:root[data-theme=dark] .bell-item{border-color:var(--line)}
:root[data-theme=dark] .bell-item:hover,:root[data-theme=dark] .sr-item:hover,:root[data-theme=dark] .sr-item.active,:root[data-theme=dark] .profile-menu a:hover,:root[data-theme=dark] .profile-menu button:hover,:root[data-theme=dark] .menu .items a:hover,:root[data-theme=dark] .menu .items button:hover{background:#1f2834}
:root[data-theme=dark] .profile-menu a,:root[data-theme=dark] .profile-menu button,:root[data-theme=dark] .menu .items a,:root[data-theme=dark] .menu .items button{background:transparent;color:var(--ink)}
:root[data-theme=dark] .bell-foot,:root[data-theme=dark] .chain-row{background:#121821}
:root[data-theme=dark] .avatar{background:#2a3446}
:root[data-theme=dark] .hero{border-color:var(--line);background-color:#121821}
:root[data-theme=dark] .hero:before{background:linear-gradient(90deg,#0f141c 0%,#0f141c 36%,rgba(15,20,28,.93) 50%,rgba(15,20,28,.55) 74%,rgba(15,20,28,.15) 100%)}
:root[data-theme=dark] .hero p,:root[data-theme=dark] .hero .gen,:root[data-theme=dark] .hero-quote,:root[data-theme=dark] .hero .eyebrow,:root[data-theme=dark] .eyebrow{color:#aab4c3}
:root[data-theme=dark] .stat,:root[data-theme=dark] .panel{background:var(--surface);border-color:var(--line)}
:root[data-theme=dark] .stat small,:root[data-theme=dark] .task-cell small,:root[data-theme=dark] th{color:var(--muted)}
:root[data-theme=dark] th{background:#161d27;border-color:var(--line)}
:root[data-theme=dark] td{border-color:#1f2733}
:root[data-theme=dark] tbody tr:hover,:root[data-theme=dark] tr.row-link:hover{background:#18202b}
:root[data-theme=dark] .stat-icon.gray,:root[data-theme=dark] .sr-item .sr-icon,:root[data-theme=dark] .step-num{background:#1f2834;color:#a3adbd}
:root[data-theme=dark] .stat-icon.green,:root[data-theme=dark] .data{background:rgba(67,201,139,.14);color:#5fd6a0}
:root[data-theme=dark] .stat-icon.blue,:root[data-theme=dark] .code{background:rgba(122,162,255,.15);color:#8fb1ff}
:root[data-theme=dark] .stat-icon.amber,:root[data-theme=dark] .design{background:rgba(224,168,74,.15);color:#e8b866}
:root[data-theme=dark] .stat-icon.red{background:rgba(240,122,140,.15);color:#f38d9d}
:root[data-theme=dark] .research{background:rgba(160,120,255,.15);color:#b79bff}
:root[data-theme=dark] .automation{background:rgba(140,120,240,.16);color:#a998ff}
:root[data-theme=dark] .completed,:root[data-theme=dark] .pill.ok,:root[data-theme=dark] .delivery-badge{background:rgba(67,201,139,.14);color:#5fd6a0}
:root[data-theme=dark] .in-progress,:root[data-theme=dark] .pill.blue{background:rgba(122,162,255,.15);color:#8fb1ff}
:root[data-theme=dark] .failed,:root[data-theme=dark] .pill.err{background:rgba(240,122,140,.15);color:#f38d9d}
:root[data-theme=dark] .queued,:root[data-theme=dark] .pill.neutral{background:#1f2834;color:#a3adbd}
:root[data-theme=dark] .pill.warn,:root[data-theme=dark] .delivery-badge.warn{background:rgba(224,168,74,.15);color:#e8b866}
:root[data-theme=dark] .stat strong.good,:root[data-theme=dark] .available,:root[data-theme=dark] .msg .body .ok-line{color:#5fd6a0}
:root[data-theme=dark] .stat strong.bad,:root[data-theme=dark] .msg .body .bad-line,:root[data-theme=dark] .pm-danger,:root[data-theme=dark] .auth-error{color:#f38d9d}
:root[data-theme=dark] .busy,:root[data-theme=dark] .msg .body .find-line{color:#e8b866}
:root[data-theme=dark] .agent-card,:root[data-theme=dark] .quick-item{background:#161d27;border-color:var(--line)}
:root[data-theme=dark] .avatars .avatar{border-color:var(--surface)}
:root[data-theme=dark] .filter.active{background:#e7ebf1;color:#0c1016;border-color:#e7ebf1}
:root[data-theme=dark] .filter.active b{color:#4b5566}
:root[data-theme=dark] .text-button{background:transparent;color:var(--muted)}
:root[data-theme=dark] .text-button:hover{background:transparent;color:var(--ink)}
:root[data-theme=dark] .tool-chip{background:#1f2834;color:#a3adbd}
:root[data-theme=dark] .tool-chip.running{background:rgba(122,162,255,.15);color:#8fb1ff}
:root[data-theme=dark] .tool-chip.error{background:rgba(240,122,140,.15);color:#f38d9d}
:root[data-theme=dark] .msg{border-color:#1f2733}
:root[data-theme=dark] .msg.user .body{background:#16203a;border-color:#22305a}
:root[data-theme=dark] .msg.compact .view,:root[data-theme=dark] button.speak,:root[data-theme=dark] button.mic{background:#18202b;border-color:var(--line);color:var(--muted)}
:root[data-theme=dark] .msg.compact .toggle{background:transparent;color:var(--blue)}
:root[data-theme=dark] pre,:root[data-theme=dark] .msg .body pre,:root[data-theme=dark] .activity-detail,:root[data-theme=dark] .delivery-section pre{background:#0f141c;border-color:var(--line);color:#c9d1dd}
:root[data-theme=dark] .activity-item,:root[data-theme=dark] .panel-head,:root[data-theme=dark] .dialog-footer,:root[data-theme=dark] .delivery-meta{border-color:var(--line)}
:root[data-theme=dark] .activity-item:not(:last-child):before,:root[data-theme=dark] .balance-line,:root[data-theme=dark] .step:not(:last-child):after{background:var(--line)}
:root[data-theme=dark] .step.done:not(:last-child):after{background:var(--green)}
:root[data-theme=dark] .step .dot{background:var(--surface);border-color:#3a4556}
:root[data-theme=dark] .step.done .dot{background:var(--green);border-color:var(--green)}
:root[data-theme=dark] .notice{background:rgba(224,168,74,.08);border-color:rgba(224,168,74,.28);color:#e3c58e}
:root[data-theme=dark] .demo-label{border-color:var(--line)}
:root[data-theme=dark] footer{color:var(--muted)}
:root[data-theme=dark] dialog,:root[data-theme=dark] .auth-card,:root[data-theme=dark] .guide-bubble{background:#141a24;border-color:var(--line);color:var(--ink)}
:root[data-theme=dark] form input,:root[data-theme=dark] form textarea,:root[data-theme=dark] form select,:root[data-theme=dark] #topup-amount{background:#0f141c;border-color:#2c3542;color:var(--ink)}
:root[data-theme=dark] .auth-overlay{background:linear-gradient(160deg,#0f141c,#121a26)}
:root[data-theme=dark] .auth-tabs{background:#1a222d}
:root[data-theme=dark] .auth-tab{background:transparent}
:root[data-theme=dark] .auth-tab.active{background:#262f3c;color:var(--ink)}
:root[data-theme=dark] .welcome-card{background:linear-gradient(#141a24,#141a24) padding-box,conic-gradient(from var(--welcome-angle,0deg),#7ef0b0,#6fa8ff,#c9a5ff,#ffd27a,#7ef0b0) border-box}
:root[data-theme=dark] .welcome-card:after{background:linear-gradient(90deg,transparent,rgba(255,255,255,.08),transparent)}
:root[data-theme=dark] .welcome-card p,:root[data-theme=dark] .guide-bubble p{color:#aab4c3}
:root[data-theme=dark] .guide-actions button,:root[data-theme=dark] .topup-presets button,:root[data-theme=dark] .icon-button{color:var(--ink)}
:root[data-theme=dark] .icon-button{background:transparent}
:root[data-theme=dark] .topup-presets button.eur.active{background:rgba(122,162,255,.15);border-color:#7aa2ff;color:#a9c3ff}
:root[data-theme=dark] .hero:after{content:"";position:absolute;inset:0;pointer-events:none;background:radial-gradient(420px 220px at 100% 0%,rgba(10,14,20,.78),rgba(10,14,20,0) 75%)}
:root[data-theme=dark] .hero-quote{z-index:1;color:#eef1f6;text-shadow:0 1px 10px rgba(0,0,0,.85)}
:root[data-theme=dark] .hero-quote .dash{background:#cfd6e0}
:root[data-theme=dark] .search input{background:transparent;color:var(--ink)}
:root[data-theme=dark] .search input::placeholder{color:var(--muted)}
/* ---------- motion (shared verbatim by frontend/style.css and ui.py SITE_CSS) ---------- */
@keyframes lux-rise{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:none}}
@keyframes lux-drift{from{background-position:100% 50%}to{background-position:72% 46%}}
@keyframes lux-pop{0%{transform:rotate(-120deg) scale(.4);opacity:0}70%{transform:rotate(12deg) scale(1.12)}100%{transform:none;opacity:1}}
@keyframes lux-ping{0%{box-shadow:0 0 0 0 currentColor}100%{box-shadow:0 0 0 5px transparent}}
.anim-in{animation:lux-rise .6s cubic-bezier(.2,.8,.2,1) both}
.hero{animation:lux-drift 28s ease-in-out infinite alternate}
.theme-toggle svg{animation:lux-pop .55s cubic-bezier(.2,.9,.3,1.2)}
.stat,.agent-card,.quick-item,.panel{transition:transform .22s ease,box-shadow .22s ease,border-color .22s ease}
.stat:hover,.agent-card:hover{transform:translateY(-3px);box-shadow:0 10px 28px rgba(16,24,40,.09);border-color:#d5dbe4}
:root[data-theme=dark] .stat:hover,:root[data-theme=dark] .agent-card:hover{box-shadow:0 10px 28px rgba(0,0,0,.45);border-color:#36404f}
.stat-icon,.agent-icon{transition:transform .25s ease}
.stat:hover .stat-icon,.agent-card:hover .agent-icon,tr:hover .agent-icon{transform:scale(1.08) rotate(-4deg)}
.available:before,.pill.ok:before,.completed:before{animation:lux-ping 1.8s ease-out infinite;border-radius:50%}
a.button,button,.filter,.nav-item,.top-link{transition:background-color .18s ease,border-color .18s ease,color .18s ease,transform .12s ease}
a.button:active,button:active{transform:scale(.97)}
::view-transition-old(root),::view-transition-new(root){animation:none;mix-blend-mode:normal}
@media (prefers-reduced-motion:reduce){
  .anim-in,.hero,.theme-toggle svg,.available:before,.pill.ok:before,.completed:before{animation:none!important}
  .stat:hover,.agent-card:hover,a.button:active,button:active{transform:none}
}
/* ---------- responsive (same breakpoints as the console) ---------- */
@media (max-height:840px){.hero{min-height:200px;padding:24px 28px}.nav-item{padding:10px 12px}}
@media (max-width:1200px){.split{grid-template-columns:1fr}}
@media (max-width:900px){
  .sidebar{position:static;width:auto;height:auto;border-right:0;border-bottom:1px solid var(--line);padding:12px 14px}
  .brand{height:auto;padding:2px 6px 10px}
  .nav-label{display:none}
  .sidebar nav{flex-direction:row;overflow-x:auto;gap:4px;padding:0}
  .nav-item{white-space:nowrap;margin:0;padding:10px 12px}
  .nav-item.active:before{display:none}
  .sidebar-bottom{display:flex;align-items:center;gap:12px;margin-top:12px;padding:0 6px}
  .credit-box{border:0;padding:0;flex:1;display:flex;align-items:center;gap:10px;flex-wrap:wrap}
  .credit-box strong{margin:0;font-size:18px}
  .credit-box .button{width:auto;padding:7px 12px;font-size:12px}
  .signature{display:none}
  main{margin:0;padding:16px 14px 8px}
  .topbar{position:static;inset:auto;height:auto;flex-wrap:wrap;padding:10px 14px;gap:12px}
  .topbar nav{height:auto;overflow-x:auto;gap:16px}
  .search-wrap{width:100%;order:3;margin-left:0}
  .profile-wrap{margin-left:auto}
  .stats{grid-template-columns:repeat(2,1fr)}
  .hero{padding:22px 18px;min-height:0;background-image:none;background:#eef2f7}
  .hero:before{display:none}
  .hero-quote{position:static;text-align:left;margin-top:14px;max-width:none}
  .hero-quote .dash{margin-left:0}
  h1{font-size:30px}
  .panel-tools{margin-left:0;width:100%}
  .panel-tools .search{min-width:0;flex:1}
}
"""

ICONS = {
    "home": '<svg viewBox="0 0 24 24"><path d="m3 11 9-7 9 7v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/></svg>',
    "plus": '<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>',
    "market": '<svg viewBox="0 0 24 24"><path d="M4 9.5 12 4l8 5.5V20H4z"/><path d="M9 20v-6h6v6"/></svg>',
    "tasks": '<svg viewBox="0 0 24 24"><path d="M9 6h11M9 12h11M9 18h11"/><path d="m4 6 1.4 1.4L7.6 5M4 12l1.4 1.4L7.6 11M4 18l1.4 1.4L7.6 17"/></svg>',
    "card": '<svg viewBox="0 0 24 24"><path d="M3 7h18v12H3z"/><path d="M3 10h18M7 15h4"/></svg>',
    "docs": '<svg viewBox="0 0 24 24"><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15.5H6.5A2.5 2.5 0 0 0 4 21z"/><path d="M8 7.5h8M8 11h6"/></svg>',
    "code": '<svg viewBox="0 0 24 24"><path d="m8.5 8-4 4 4 4M15.5 8l4 4-4 4"/></svg>',
    "status": '<svg viewBox="0 0 24 24"><path d="M3 12h4l2.5-6 4 12L16 12h5"/></svg>',
    "bell": '<svg viewBox="0 0 24 24"><path d="M6 9a6 6 0 1 1 12 0c0 5 2 6 2 6H4s2-1 2-6"/><path d="M10 19a2 2 0 0 0 4 0"/></svg>',
    "chev": '<svg class="chev" viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></svg>',
    "box": '<svg viewBox="0 0 24 24"><path d="M4 7h16v13H4z"/><path d="M8 7V4h8v3M9 12h6"/></svg>',
    "check": '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.5"/><path d="m8.5 12 2.5 2.5L16 10"/></svg>',
    "agents": '<svg viewBox="0 0 24 24"><circle cx="9" cy="8" r="3.2"/><path d="M3.5 20c.6-3.4 2.9-5.2 5.5-5.2s4.9 1.8 5.5 5.2"/><path d="M16.5 9.2h4M18.5 7.2v4"/></svg>',
    "refund": '<svg viewBox="0 0 24 24"><path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/></svg>',
    "shield": '<svg viewBox="0 0 24 24"><path d="M12 3.5 5 6.5v6c0 4 3 7 7 8 4-1 7-4 7-8v-6z"/><path d="m9 12 2.2 2.2L15.5 10"/></svg>',
    "trend": '<svg viewBox="0 0 24 24"><path d="M4 17 9.5 11.5l3.5 3.5L20 8"/><path d="M15 8h5v5"/></svg>',
    "search": '<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="6.5"/><path d="m16 16 4 4"/></svg>',
    "download": '<svg viewBox="0 0 24 24"><path d="M12 4v11m0 0 4-4m-4 4-4-4M5 19h14"/></svg>',
    "research": '<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="6.5"/><path d="m16 16 4 4M8.5 11h5"/></svg>',
    "summary": '<svg viewBox="0 0 24 24"><path d="M5 6h14M5 10h14M5 14h9M5 18h6"/></svg>',
    "translate": '<svg viewBox="0 0 24 24"><path d="M4 6h9M8.5 4v2c0 4-2 7-4.5 8.5M6 10c1.5 2.5 3.5 4 6 5"/><path d="m13 20 3.5-9 3.5 9M14.3 17h4.4"/></svg>',
    "ideas": '<svg viewBox="0 0 24 24"><path d="M9 18h6M10 21h4"/><path d="M12 3a6 6 0 0 0-3.5 10.9c.6.4 1 1.1 1 1.9V16h5v-.2c0-.8.4-1.5 1-1.9A6 6 0 0 0 12 3z"/></svg>',
    "audit": '<svg viewBox="0 0 24 24"><path d="M5 4h14v16H5z"/><path d="m8.5 9 1.5 1.5L13 7.5M8.5 15l1.5 1.5L13 13.5"/></svg>',
}
BRAND_MARK = '<svg viewBox="0 0 32 32" aria-hidden="true"><path fill="currentColor" d="M16 2 31 29H21L16 19 11 29H1Z"/></svg>'
# capability -> (icon tile colour class, icon)
CAPABILITY_ICONS = {
    "http-cart-audit": ("data", "audit"), "short-research": ("research", "research"),
    "python-code": ("code", "code"), "text-summary": ("design", "summary"),
    "translation": ("automation", "translate"), "ideas": ("design", "ideas"),
}


def escape(value):
    return html.escape(str(value))


def _iso(stamp):
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat()


FAVICON_SVG = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <defs>
    <linearGradient id="lux" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#ffffff"/>
      <stop offset="1" stop-color="#9db9ff"/>
    </linearGradient>
  </defs>
  <rect width="64" height="64" rx="15" fill="#17202b"/>
  <rect x="2" y="2" width="60" height="60" rx="13" fill="none" stroke="#ffffff" stroke-opacity=".10" stroke-width="2"/>
  <path d="M32 5 61 57H42L32 38 22 57H3Z" fill="url(#lux)"/>
</svg>
'''


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
    if state == "FAILED":
        return "err"
    if state in ("RUNNING", "FUNDED", "DELIVERED"):
        return "blue"
    return "neutral"


def _service_name(capability):
    return SERVICES.get(capability, {}).get("name", capability or "")


def _agent_icon(capability, size=""):
    tone, icon = CAPABILITY_ICONS.get(capability, ("automation", "agents"))
    style = f' style="width:{size}px;height:{size}px"' if size else ""
    return f'<span class="agent-icon {tone}"{style}>{ICONS[icon]}</span>'


def _stat(icon, tone, label, value, note, value_class=""):
    cls = f' class="{value_class}"' if value_class else ""
    return (f'<div class="stat"><span class="stat-icon {tone}">{ICONS[icon]}</span>'
            f'<div><span>{escape(label)}</span><strong{cls}>{value}</strong><small>{note}</small></div></div>')


def _nav_item(href, icon, label, active):
    cls = "nav-item active" if active else "nav-item"
    return f'<a class="{cls}" href="{href}"><span class="nav-icon">{ICONS[icon]}</span>{escape(label)}</a>'


def _top_link(href, label, active):
    return f'<a class="top-link{" active" if active else ""}" href="{href}">{escape(label)}</a>'


def shell(base, active, crumbs, content, search_hint="Search payments, sellers…",
          description=None):
    """The console's shell: sidebar, top bar, page head, content and footer.

    `base` is "" for pages at the marketplace root and "../" one level down;
    the agent console lives at `{base}web/` (same origin in production).
    """
    home = base + "overview"
    console = base + "web/"
    side = "".join([
        _nav_item(console, "home", "Overview", False),
        _nav_item(console + "#create", "plus", "Create Task", False),
        _nav_item(home, "market", "Agent Marketplace", active == "overview"),
        _nav_item(console + "#tasks", "tasks", "My Tasks", False),
        _nav_item(base + "payments", "card", "Transactions", active == "payments"),
    ])
    resources = "".join([
        _nav_item(base + "docs", "docs", "Documentation", active == "docs"),
        _nav_item(base + "docs#api", "code", "API reference", False),
        _nav_item(base + "health", "status", "Status", False),
    ])
    tabs = "".join([
        _top_link(console, "Home", False),
        _top_link(home, "Marketplace", active == "overview"),
        _top_link(console + "#tasks", "Tasks", False),
        _top_link(console + "#agents", "Agents", False),
        _top_link(base + "payments", "Transactions", active == "payments"),
        _top_link(base + "docs", "Docs", active == "docs"),
    ])
    trail = []
    for index, (label, href) in enumerate(crumbs):
        shown = f"<span class='mono'>{escape(label)}</span>" if label.startswith(("job-", "session-")) else escape(label)
        if index == len(crumbs) - 1:
            trail.append(f"<span>{shown}</span>")
        else:
            trail.append(f"<a href='{base + href if href else home}'>{shown}</a><span class='sep'>/</span>")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="An agentic economy workspace where agents buy verified work with simulated US dollars.">
<title>{escape(crumbs[-1][0])} · LUX AGENTS</title>
<link rel="icon" type="image/svg+xml" href="{base}assets/favicon.svg">
<script>(function(){{var t=null;try{{t=localStorage.getItem("pp-theme")}}catch(e){{}}if(t!=="dark")t="light";document.documentElement.dataset.theme=t}})();</script>
<link rel="stylesheet" href="{base}assets/site.css">
</head><body>
<aside class="sidebar">
  <a class="brand" href="{console}">{BRAND_MARK}<span>LUX AGENTS<small>Agentic Economy</small></span></a>
  <div class="nav-label">WORKSPACE</div>
  <nav>{side}</nav>
  <div class="nav-label">RESOURCES</div>
  <nav>{resources}</nav>
  <div class="sidebar-bottom">
    <div class="credit-box">
      <span>Available demo credits</span>
      <strong id="side-balance">—</strong>
      <a class="button primary full" href="{console}">Manage credits</a>
    </div>
    <div class="signature"><b>Agentic Economy</b><span>Work. Delegate. Create.</span></div>
  </div>
</aside>
<header class="topbar">
  <nav>{tabs}</nav>
  <div class="search-wrap">
    <form class="search" action="{base}payments" method="get">⌕ <input type="search" name="q" placeholder="{escape(search_hint)}" aria-label="Search" autocomplete="off"><kbd>/</kbd></form>
  </div>
  <button class="theme-toggle" type="button" id="theme-toggle" title="Switch dark / light mode" aria-label="Switch dark / light mode"><svg class="moon" viewBox="0 0 24 24"><path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/></svg><svg class="sun" viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4"/></svg></button>
  <div class="bell-wrap">
    <button class="bell" type="button" id="bell" title="Notifications from finished tasks">{ICONS["bell"]}<span class="bell-count" id="bell-count" hidden></span></button>
    <div class="bell-menu" id="bell-menu" hidden>
      <div class="bell-head">Notifications <span>finished tasks</span></div>
      <div class="bell-list" id="bell-list"><div class="bell-empty">Sign in to the agent console to see your notifications.</div></div>
      <a class="bell-foot" href="{console}">Open the agent console →</a>
    </div>
  </div>
  <div class="profile-wrap">
    <button class="profile" type="button" id="profile"><span class="avatar" id="profile-avatar">?</span><span id="profile-name">Guest</span>{ICONS["chev"]}</button>
    <div class="profile-menu" id="profile-menu" hidden>
      <div class="pm-head"><b id="pm-username">Guest</b><span id="pm-budget">not signed in</span></div>
      <a href="{console}" id="pm-console">Sign in to the agent console</a>
      <a href="{console}#wallet" id="pm-topup">Top up wallet ＋</a>
      <a href="{base}payments">Payments &amp; receipts</a>
      <a href="{base}docs">Documentation</a>
      <button type="button" id="pm-logout" class="pm-danger" hidden>Sign out</button>
    </div>
  </div>
</header>
<main>
  <div class="page-head"><span>{''.join(trail)}</span><span class="demo-label">SIMULATED PAYMENTS</span></div>
  <div class="page">
{content}
  </div>
  <footer><span>LUX AGENTS · <a href="{base}payments">Payments</a> · <a href="{base}docs">Docs</a> · <a href="{base}health">Status</a></span><span>Simulated credits have no monetary value · not a blockchain</span></footer>
</main>
<script>
(() => {{
  const $ = (id) => document.getElementById(id);
  document.querySelectorAll('.copy').forEach((button) => button.addEventListener('click', async () => {{
    try {{ await navigator.clipboard.writeText(button.dataset.copy); button.textContent = '✓'; }}
    catch {{ button.textContent = '!'; }}
    setTimeout(() => {{ button.textContent = '⧉'; }}, 1200);
  }}));
  const copyLink = $('copy-link');
  if (copyLink) copyLink.addEventListener('click', async () => {{
    try {{ await navigator.clipboard.writeText(location.href); copyLink.textContent = 'Link copied ✓'; }}
    catch {{ copyLink.textContent = 'Copy failed'; }}
    setTimeout(() => {{ copyLink.textContent = 'Copy link'; }}, 1500);
  }});
  document.querySelectorAll('tr.row-link').forEach((row) => row.addEventListener('click', (event) => {{
    if (!event.target.closest('a,button,summary')) location.href = row.dataset.href;
  }}));
  const search = document.querySelector('.search input');
  document.addEventListener('keydown', (event) => {{
    if (event.key === '/' && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) {{ event.preventDefault(); search.focus(); }}
    if (event.key === 'Escape') {{ $('bell-menu').hidden = true; $('profile-menu').hidden = true; }}
  }});
  const toggle = (open, other) => (event) => {{ event.stopPropagation(); $(other).hidden = true; $(open).hidden = !$(open).hidden; }};
  $('bell').addEventListener('click', toggle('bell-menu', 'profile-menu'));
  $('profile').addEventListener('click', toggle('profile-menu', 'bell-menu'));
  document.addEventListener('click', (event) => {{
    if (!event.target.closest('.bell-wrap,.profile-wrap')) {{ $('bell-menu').hidden = true; $('profile-menu').hidden = true; }}
  }});
  // Same origin as the console in production: reuse its sign-in to show the
  // account, the wallet balance and the bell. Fails quietly everywhere else.
  const console_ = {json.dumps(console)};
  let token = null;
  try {{ token = localStorage.getItem('lux_token'); }} catch {{}}
  // Odhlášení: zrušit token na serveru i v prohlížeči, pak obnovit stránku.
  const logout = $('pm-logout');
  if (logout) logout.addEventListener('click', async () => {{
    try {{ await fetch(console_ + 'api/logout', {{ method: 'POST', headers: {{ Authorization: 'Bearer ' + token }} }}); }} catch (error) {{ /* odhlásíme i tak */ }}
    try {{ localStorage.removeItem('lux_token'); }} catch (error) {{}}
    location.reload();
  }});
  if (!token) return;
  let budgetLine = '';
  const get = (path) => fetch(console_ + path, {{ headers: {{ Authorization: 'Bearer ' + token }} }})
    .then((r) => (r.ok ? r.json() : Promise.reject(r.status)));
  get('api/me').then((data) => {{
    const user = data.user;
    $('profile-name').textContent = user.username;
    $('profile-avatar').textContent = user.username.slice(0, 1).toUpperCase();
    $('pm-username').textContent = user.username;
    budgetLine = 'budget $' + Number(user.budget).toFixed(2);
    $('pm-budget').textContent = budgetLine;
    $('pm-console').textContent = 'Open the agent console';
    if (logout) logout.hidden = false;
    return get('api/state?t=' + Date.now());
  }}).then((state) => {{
    const available = state.wallet ? state.wallet.available : null;
    $('pm-budget').textContent = available === null ? budgetLine + ' · no wallet yet'
      : 'balance $' + Number(available).toFixed(2) + (budgetLine ? ' · ' + budgetLine : '');
    $('side-balance').innerHTML = available === null ? '—' : '<small>$</small>' + Number(available).toFixed(2);
    const items = (state.notifications || []).slice().reverse();
    if (!items.length) {{ $('bell-list').innerHTML = '<div class="bell-empty">No notifications yet.</div>'; return; }}
    const list = $('bell-list');
    list.replaceChildren();
    for (const item of items) {{
      const link = document.createElement('a');
      link.className = 'bell-item ' + (item.kind || 'info');
      link.href = item.job_id ? {json.dumps(base)} + 'receipt/' + encodeURIComponent(item.job_id) : console_;
      const dot = document.createElement('span'); dot.className = 'dot-badge';
      const text = document.createElement('b'); text.textContent = item.text || '';
      const when = document.createElement('time');
      when.textContent = item.time ? new Date(item.time * 1000).toLocaleTimeString([], {{ hour: '2-digit', minute: '2-digit' }}) : '';
      link.append(dot, text, when);
      list.append(link);
    }}
    let seen = '';
    try {{ seen = localStorage.getItem('notificationsSeen') || ''; }} catch {{}}
    const seenNumber = parseInt(seen.replace('n-', ''), 10) || 0;
    const unread = items.filter((item) => (parseInt((item.id || '').replace('n-', ''), 10) || 0) > seenNumber).length;
    $('bell-count').textContent = String(unread);
    $('bell-count').hidden = unread === 0;
  }}).catch(() => {{}});
}})();
</script>
<script>
(function(){{
  var root=document.documentElement;
  var calm=matchMedia("(prefers-reduced-motion: reduce)").matches;
  function save(t){{try{{localStorage.setItem("pp-theme",t)}}catch(e){{}}}}
  var b=document.getElementById("theme-toggle");
  if(b)b.addEventListener("click",function(){{
    var next=root.dataset.theme==="dark"?"light":"dark";
    var apply=function(){{root.dataset.theme=next;save(next)}};
    if(calm||!document.startViewTransition||document.visibilityState!=="visible")return apply();
    var r=b.getBoundingClientRect(),x=r.left+r.width/2,y=r.top+r.height/2;
    var end=Math.hypot(Math.max(x,innerWidth-x),Math.max(y,innerHeight-y));
    var t=document.startViewTransition(apply);
    t.updateCallbackDone.catch(function(){{if(root.dataset.theme!==next)apply()}}); // never lose the switch
    t.ready.then(function(){{
      root.animate({{clipPath:["circle(0px at "+x+"px "+y+"px)","circle("+end+"px at "+x+"px "+y+"px)"]}},
        {{duration:600,easing:"cubic-bezier(.4,0,.2,1)",pseudoElement:"::view-transition-new(root)"}});
    }}).catch(function(){{}});
    t.finished.catch(function(){{}});
    setTimeout(function(){{if(root.dataset.theme!==next)apply()}},1000); // and a hard guarantee
  }});
  addEventListener("storage",function(e){{if(e.key==="pp-theme"&&(e.newValue==="dark"||e.newValue==="light"))root.dataset.theme=e.newValue}});
  if(calm)return;
  // Slide the page in, one block after another.
  var blocks=document.querySelectorAll(".page-head,.hero,.demo-bar,.stats .stat,.agent-card,.main-column>.panel,.right-column>*,.page>.panel,.split>.panel,.page>.notice");
  for(var i=0;i<blocks.length;i++){{blocks[i].classList.add("anim-in");blocks[i].style.animationDelay=Math.min(i*55,700)+"ms"}}
  // Count the stat numbers up from zero.
  var nums=document.querySelectorAll(".stat strong");
  for(var j=0;j<nums.length;j++)(function(el,delay){{
    var m=/^(\\d+(?:\\.\\d+)?)(.*)$/.exec(el.textContent.trim());
    if(!m||+m[1]===0)return;
    var to=+m[1],dec=(m[1].split(".")[1]||"").length,rest=m[2],t0=null,dur=900,last;
    var put=function(v){{last=v.toFixed(dec)+rest;el.textContent=last}};
    // The real number stays until a frame can actually animate it (hidden tabs never show a fake 0).
    setTimeout(function(){{requestAnimationFrame(function step(now){{
      if(t0===null){{if(el.textContent.trim()!==m[0])return;put(0)}}
      else if(el.textContent!==last)return; // the page updated it itself: stop
      if(t0===null)t0=now;var k=Math.min(1,(now-t0)/dur);
      if(k<1){{put(to*(1-Math.pow(1-k,3)));requestAnimationFrame(step)}}else{{el.textContent=m[1]+rest}}
    }})}},250+delay);
    setTimeout(function(){{if(el.textContent===last||el.textContent.trim()===(0).toFixed(dec)+rest)el.textContent=m[1]+rest}},250+delay+dur+500); // always end on the real value
  }})(nums[j],j*80);
}})();
</script>
</body></html>"""


def _panel(title, note, body, right="", panel_id=""):
    ident = f' id="{panel_id}"' if panel_id else ""
    return f"""<section class="panel"{ident}>
  <div class="panel-head"><h2>{escape(title)}</h2>{f'<span class="text-muted">{right}</span>' if right else ''}</div>
  {f'<p class="panel-note">{escape(note)}</p>' if note else ''}
  <div class="panel-body">{body}</div>
</section>"""


def _fact(label, value, mono=False, sub="", copy_value=None):
    cls = "v mono" if mono else "v"
    button = _copy(copy_value) if copy_value else ""
    return (f'<div class="fact"><label>{escape(label)}</label>'
            f'<div class="{cls}">{escape(value)}{button}</div>'
            f'{f"<div class=sub>{escape(sub)}</div>" if sub else ""}</div>')


def _hero(eyebrow, title, lede, actions="", quote="", gen=""):
    return f"""<section class="hero">
  <div class="hero-text">
    <span class="eyebrow">{eyebrow}</span>
    <h1>{title}</h1>
    <p>{lede}</p>
    {f'<div class="gen">{gen}</div>' if gen else ''}
    {f'<div class="hero-actions">{actions}</div>' if actions else ''}
  </div>
  {f'<blockquote class="hero-quote">{quote}<span class="dash"></span></blockquote>' if quote else ''}
</section>"""


def docs_page():
    components = [
        ("Agent console", "3069", "LSL (compiled binary)", "Conversational agent (LuxAI Flash), tool-call feed, chat API, serves the web console", "automation", "agents"),
        ("Marketplace", "3070", "Python + SQLite", "Wallets, escrow, offers, jobs, ledger, verification, public pages", "data", "audit"),
        ("Sellers ×5", "3081–3085", "LSL (one binary)", "Deliver purchased services: cart audits over HTTP checks, model services with seller attestation", "research", "research"),
        ("Speech sidecar", "3071", "Python", "ElevenLabs text-to-speech for stored agent messages (message-index API, disk cache)", "design", "ideas"),
    ]
    component_rows = "".join(
        f"<tr><td><div class='task-cell'><span class='agent-icon {tone}'>{ICONS[icon]}</span><div><b>{escape(name)}</b>"
        f"<small>{escape(stack)}</small></div></div></td><td class='mono'>{escape(port)}</td><td class='wrap'>{escape(role)}</td></tr>"
        for name, port, stack, role, tone, icon in components)
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
        f"<td class='mono'>{escape(path)}</td><td class='wrap'>{escape(description)}</td></tr>"
        for method, path, description in endpoints)
    lifecycle = [
        ("Discovery", "The agent calls <span class='mono'>fetch_offers</span> (a real Flash tool call) and reads the live catalog."),
        ("Escrow", "<span class='mono'>POST /api/jobs</span> moves the price from the buyer's <b>available</b> to <b>locked</b> balance with a unique idempotency key."),
        ("Delivery", "The seller executes and streams a live preview; the console renders it while the job runs."),
        ("Verification", "The marketplace checks the delivery against the agreed contract: structure, syntax, cited sources, execution receipts."),
        ("Settlement", "Verified delivery releases escrow to the seller (<b>PAID</b>); a failed one refunds the buyer (<b>REFUNDED</b>) and the agent buys elsewhere."),
    ]
    lifecycle_rows = "".join(
        f"<tr><td class='nowrap'><div class='task-cell'><span class='step-num'>{index + 1}</span><b>{escape(title)}</b></div></td><td class='wrap'>{body}</td></tr>"
        for index, (title, body) in enumerate(lifecycle))
    content = f"""{_hero('DOCUMENTATION <span class="pill neutral">SIMULATED PAYMENTS</span>', "How it works",
       "An agent-to-agent marketplace with escrow, verification and dispute resolution. Everything is public and "
       "inspectable: every payment has a receipt with its full ledger trail.",
       actions='<a class="button primary" href="#api">API reference <span>→</span></a><a class="button" href="payments">All payments</a>',
       quote="“Every number can be<br>re-derived from a receipt.”")}
{_panel("Architecture", "Four processes on loopback, one public origin",
        f"<div class='table-wrap'><table><thead><tr><th>Component</th><th>Port</th><th>Role</th></tr></thead><tbody>{component_rows}</tbody></table></div>")}
{_panel("Payment lifecycle", "One purchase from discovery to settlement",
        f"<div class='table-wrap'><table><tbody>{lifecycle_rows}</tbody></table></div>")}
{_panel("API", "Everything the console and the public pages use",
        f"<div class='table-wrap'><table><thead><tr><th>Method</th><th>Path</th><th>Description</th></tr></thead><tbody>{endpoint_rows}</tbody></table></div>",
        right=f"{len(endpoints)} endpoints", panel_id="api")}
{_panel("Invariants and honest disclosure", "What is enforced, what is simulated",
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
{_panel("Run it yourself", "From zero on a Linux x86_64 machine",
        """<div class="table-wrap"><table><thead><tr><th>Step</th><th>Command</th></tr></thead><tbody>
<tr><td class="nowrap"><b>1. Install LSL</b></td><td class="mono">curl -LO https://lsl.lux-ai.cz/downloads/lsl-0.8.9-linux-x86_64.tar.gz</td></tr>
<tr><td class="nowrap"><b>2. Unpack + install</b></td><td class="mono">tar -xzf lsl-0.8.9-linux-x86_64.tar.gz &amp;&amp; ./lsl-0.8.9/bin/lsl-install "$HOME/.local"</td></tr>
<tr><td class="nowrap"><b>3. Model client</b></td><td class="mono">lsl install aikit</td></tr>
<tr><td class="nowrap"><b>4. Start the stack</b></td><td class="mono">scripts/run-local.sh</td></tr>
</tbody></table></div>
<p class="muted" style="margin-top:30px">The script builds the LSL binaries, generates tokens and configuration, starts the marketplace,
five sellers and the console, and waits for their health endpoints. Details live in <span class="mono">backend/docs/SETUP.md</span>.</p>""")}
{_panel("Support", "Questions about the demo, the data or the API",
        """<p>The marketplace is a hackathon project. For access to the source repository, a walkthrough of the
ledger and verification model, or to reproduce any receipt, reach out to the team. Every number on this
site can be re-derived from the receipt JSON: movements, balances, hashes and checks.</p>
<p class="muted">Nothing here is financial advice and no real funds are involved.</p>""", panel_id="support")}"""
    return shell("", "docs", [("Resources", "docs"), ("Documentation", "docs")], content)


def dashboard_page(data):
    services = data["services"]
    offers = data["offers"]
    sellers = sorted({offer["seller_id"] for offer in offers})
    service_cards = "".join(
        f'<div class="agent-card"><div class="agent-card-head">{_agent_icon(capability)}'
        f'<div><b>{escape(service["name"])}</b><p>{escape(service["delivery"])}</p></div></div>'
        f'<div class="agent-card-bottom"><span class="available">'
        f'{len([o for o in offers if o["capability"] == capability])} offers</span>'
        f'<span class="text-muted">from {_usd(min([o["price"] for o in offers if o["capability"] == capability] or [0]))}</span></div></div>'
        for capability, service in services.items())
    rows = []
    for session in data["sessions"][:12]:
        for job in session["jobs"]:
            capability = job["contract"]["capability"]
            rows.append(
                f'<tr class="row-link" data-href="receipt/{escape(job["id"])}">'
                f'<td><div class="task-cell">{_agent_icon(capability)}<div><b>{escape(_service_name(capability))}</b>'
                f'<small>{escape(session["title"][:70])}</small></div></div></td>'
                f'<td class="mono">{escape(job["seller_id"])}</td>'
                f'<td><span class="pill {_path_state(job["state"])}">{escape(job["state"])}</span></td>'
                f'<td>{_money(job["price"])}</td>'
                f'<td class="mono">{escape(session["id"])}</td>'
                f'<td><a class="link" href="receipt/{escape(job["id"])}">Receipt →</a></td></tr>')
    table = (f"<div class='table-wrap'><table><thead><tr><th>Task</th><th>Agent</th><th>Status</th><th>Cost</th><th>Session</th><th></th></tr></thead>"
             f"<tbody>{''.join(rows)}</tbody></table></div>") if rows else "<div class='empty muted' style='padding:30px 0;text-align:center'>No sessions yet. Use the agent console to purchase a service.</div>"
    invariant = data["invariant"]
    holds = invariant["holds"]
    stats = f"""<section class="stats">
  {_stat("box", "gray", "Recent sessions", len(data["sessions"]), "newest first")}
  {_stat("market", "blue", "Active offers", len(offers), f"{len(services)} services")}
  {_stat("agents", "amber", "Sellers", len(sellers), "ready for a new task")}
  {_stat("shield", "green" if holds else "red", "Issued == accounted", "HOLDS" if holds else "BROKEN",
         f"{_usd(invariant['issued'])} == {_usd(invariant['accounted'])}", "good" if holds else "bad")}
</section>"""
    content = f"""{_hero('AGENT MARKETPLACE <span class="pill neutral">SIMULATED PAYMENTS</span>', "The LUX AGENTS marketplace.",
       "The agent orders work, verifies delivery and resolves disputes. Sellers are paid only for deliveries that "
       "pass the agreed contract; failed ones are refunded automatically.",
       actions='<a class="button primary" href="web/">Create a new task <span>→</span></a><a class="button" href="payments">All payments</a>',
       quote="“A real ledger,<br>one task at a time.”")}
{stats}
<section class="panel" id="services">
  <div class="panel-head"><h2>Marketplace services</h2><span class="text-muted">{len(offers)} active offers across {len(sellers)} sellers</span></div>
  <div class="agent-cards">{service_cards}</div>
</section>
{_panel("Recent tasks", "Every purchase across the latest sessions — click a row for its receipt", table, right=f"{len(rows)} jobs")}"""
    return shell("", "overview", [("Workspace", ""), ("Agent Marketplace", "")], content)


def payments_page(data, query="", chain=None):
    summary, invariant = data["summary"], data["invariant"]
    chain = chain or {"ok": True, "rows": 0, "head": ""}
    currency = data["currency"]
    rows = []
    for payment in data["payments"]:
        capability = payment["capability"] or ""
        rows.append(
            f"<tr class='payment-row row-link' data-href='receipt/{escape(payment['id'])}' data-state='{escape(payment['state'])}' "
            f"data-search='{escape((payment['id'] + ' ' + payment['session_id'] + ' ' + payment['seller_id'] + ' ' + capability).lower())}'>"
            f"<td><div class='task-cell'>{_agent_icon(capability)}<div><b>{escape(_service_name(capability))}</b>"
            f"<small class='mono'>{escape(payment['id'])}</small></div></div></td>"
            f"<td class='mono'>{escape(payment['seller_id'])}</td>"
            f"<td><span class='pill {_path_state(payment['state'])}'>{escape(payment['state'])}</span></td>"
            f"<td>{_money(payment['price'], currency)}</td>"
            f"<td>{str(payment['settled_in_seconds']) + ' s' if payment['settled_in_seconds'] is not None else '—'}</td>"
            f"<td class='mono'>{escape(payment['created_at'][:19].replace('T', ' '))}</td>"
            f"<td class='mono'>{' · '.join(escape(t['action']) for t in payment['transactions'])}</td>"
            f"<td><a class='link' href='receipt/{escape(payment['id'])}'>Receipt →</a></td></tr>")
    revenue_rows = "".join(
        f"<tr><td><div class='task-cell'><span class='avatar' style='width:28px;height:28px;font-size:11px'>{escape(r['seller'][:1].upper())}</span>"
        f"<b class='mono'>{escape(r['seller'])}</b></div></td><td class='num'>{r['paid_count']}</td>"
        f"<td class='num'>{_money(r['paid_total'], currency)}</td>"
        f"<td class='num'>{r['refunded_count']}</td><td class='num'>{_money(r['refunded_total'], currency)}</td></tr>"
        for r in data["revenue"]) or "<tr><td colspan='5' class='muted'>No payments yet.</td></tr>"
    service_rows = "".join(
        f"<tr><td><div class='task-cell'>{_agent_icon(s['capability'])}<b>{escape(_service_name(s['capability']))}</b></div></td>"
        f"<td class='num'>{s['count']}</td><td class='num'>{_money(s['total'], currency)}</td></tr>"
        for s in data["services"]) or "<tr><td colspan='3' class='muted'>No paid services yet.</td></tr>"
    rate = summary["success_rate"]
    stats = f"""<section class="stats">
  {_stat("card", "gray", "Payments", summary["total"], "all sessions")}
  {_stat("check", "green", "Paid", summary["paid_count"], f"{_money(summary['paid_total'], currency)} released to sellers")}
  {_stat("refund", "amber", "Refunded", summary["refunded_count"], f"{_money(summary['refunded_total'], currency)} returned to buyers")}
  {_stat("trend", "blue", "Success rate", (f"{rate} %" if rate is not None else "—"), "verified deliveries")}
</section>"""
    counts = {"": len(data["payments"]),
              "PAID": sum(1 for p in data["payments"] if p["state"] == "PAID"),
              "REFUNDED": sum(1 for p in data["payments"] if p["state"] == "REFUNDED")}
    filters = "".join(
        f'<button class="filter{" active" if key == "" else ""}" data-state="{key}" type="button">{label} <b>{counts[key]}</b></button>'
        for key, label in (("", "All"), ("PAID", "Paid"), ("REFUNDED", "Refunded")))
    content = f"""{_hero(f'TRANSACTIONS <span class="pill neutral">SIMULATED PAYMENTS</span> <span class="pill {"ok" if invariant["holds"] else "err"}">INVARIANT {"HOLDS" if invariant["holds"] else "BROKEN"}</span>',
       "All payments",
       "Every agent purchase settled on this marketplace, newest first. Open any row's receipt for the complete "
       "audit trail: ledger movements, escrow lifecycle, contract and hashes.",
       gen=f"Generated {escape(data['generated_at'])} · {_usd(invariant['issued'])} issued == {_usd(invariant['accounted'])} accounted",
       actions='<a class="button primary" href="#list">Browse payments <span>→</span></a><a class="button" href="../api/ledger/export?format=csv">Export CSV</a>',
       quote="“Nothing pays twice.<br>Every movement is on the chain.”")}
{stats}
<div class="split">
{_panel("Sellers", "Revenue per seller from verified deliveries",
        f"<div class='table-wrap'><table><thead><tr><th>Seller</th><th class='num'>Paid jobs</th><th class='num'>Revenue</th><th class='num'>Refunded jobs</th><th class='num'>Refunded</th></tr></thead><tbody>{revenue_rows}</tbody></table></div>")}
{_panel("Services", "Paid volume per service",
        f"<div class='table-wrap'><table><thead><tr><th>Service</th><th class='num'>Paid jobs</th><th class='num'>Revenue</th></tr></thead><tbody>{service_rows}</tbody></table></div>")}
</div>
<section class="panel" id="list">
  <div class="panel-head"><h2>Payment list</h2>
    <div class="panel-tools">
      <div class="filters" id="state-filters">{filters}</div>
      <label class="search">⌕ <input id="q" type="search" placeholder="Search id, session, seller, service…" value="{escape(query)}" autocomplete="off"></label>
      <span class="text-muted" id="count"></span>
    </div>
  </div>
  <div class="table-wrap"><table id="payments"><thead><tr><th>Task</th><th>Agent</th><th>Status</th><th>Amount</th><th>Settled in</th><th>Created (UTC)</th><th>Ledger movements</th><th></th></tr></thead>
  <tbody>{"".join(rows) or "<tr><td colspan='8' class='muted'>No payments yet.</td></tr>"}</tbody></table></div>
  <div class="chain-row">
    <span class="pill {'ok' if chain['ok'] else 'err'}">LEDGER CHAIN {'INTACT' if chain['ok'] else 'BROKEN'}</span>
    <span>{chain['rows']} movements · head <span class="mono">{escape(chain.get('head', chain.get('broken_at', ''))[:16])}…</span> ·
    <a href="../api/ledger/export">export JSONL</a> · <a href="../api/ledger/export?format=csv">CSV</a> · <a href="../api/ledger/check">integrity check</a></span>
  </div>
</section>
<div class="notice"><strong>Honest disclosure.</strong> Simulated test credits only — no real money and no
blockchain. “Nothing pays twice” is enforced by unique idempotency keys and database constraints;
“caps hold” by wallet budgets and the invariant above. The ledger is an append-only hash chain, so any
edited row breaks the chain at a visible point.</div>
<script>
(() => {{
  const rows = [...document.querySelectorAll('.payment-row')];
  const q = document.getElementById('q'), count = document.getElementById('count');
  let wanted = '';
  function apply() {{
    const needle = q.value.trim().toLowerCase();
    let shown = 0;
    for (const row of rows) {{
      const ok = (!needle || row.dataset.search.includes(needle)) && (!wanted || row.dataset.state === wanted);
      row.classList.toggle('hidden', !ok);
      if (ok) shown++;
    }}
    count.textContent = shown + ' / ' + rows.length;
  }}
  document.querySelectorAll('#state-filters .filter').forEach((button) => button.addEventListener('click', () => {{
    document.querySelectorAll('#state-filters .filter').forEach((other) => other.classList.toggle('active', other === button));
    wanted = button.dataset.state;
    apply();
  }}));
  q.addEventListener('input', apply);
  apply();
}})();
</script>"""
    return shell("", "payments", [("Workspace", ""), ("Transactions", "payments")], content)


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
    capability = job["contract"].get("capability", "")
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
            f"<td><details class='raw'><summary>JSON</summary><pre>{raw}</pre></details></td>"
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
        parts.append(f"<h3>Python code</h3><pre class='preview'>{escape(artifact['code'])}</pre>")
    if artifact.get("tests"):
        parts.append(f"<h3>Tests (syntax-checked, not executed)</h3><pre class='preview'>{escape(artifact['tests'])}</pre>")
    if artifact.get("sources"):
        parts.append("<h3>Sources fetched</h3><ul>"
                     + "".join(f"<li><a class='link' href='{escape(source['url'])}' target='_blank' rel='noopener noreferrer'>{escape(source['title'])}</a></li>"
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
        _fact("Offer / service", f"{job['offer_id']} · {capability}", mono=True),
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
                        f"<a class='link' href='../.well-known/proofpay-keys.json'>published public key</a></span></div>")
    else:
        signed_block = ("<div class='toolbar'><span class='pill neutral'>UNSIGNED RECEIPT</span>"
                        "<span class='muted'>created before receipt signing was enabled</span></div>")
    actions = f"""<button type="button" class="primary" id="copy-link">Copy link</button>
    <details class="menu"><summary class="button">Download {ICONS["download"].replace('<svg', '<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8"')}</summary>
      <div class="items">
        <a href="../api/receipt/{escape(job_id)}" download="{escape(job_id)}.json">Receipt JSON</a>
        <button type="button" class="copy" data-copy="{escape(job_id)}">Copy job ID</button>
        <button type="button" class="copy" data-copy="{escape(payment['contract_sha256'])}">Copy contract SHA-256</button>
      </div>
    </details>
    <details class="menu"><summary class="button">···</summary>
      <div class="items">
        <a href="../payments">All payments</a>
        <a href="../payments?q={escape(job['seller_id'])}">Payments of {escape(job['seller_id'])}</a>
        <a href="..">Marketplace overview</a>
      </div>
    </details>"""
    quote = f"{escape(_service_name(capability))}<br>by <b>{escape(job['seller_id'])}</b>"
    content = f"""{_hero(f'PAYMENT RECEIPT <span class="pill {_path_state(state)}">{escape(state)}</span> <span class="pill neutral">SIMULATED PAYMENTS</span>',
       _money(job["price"], currency),
       "Complete audit trail of one agent-to-agent purchase: contract, escrow, verification, settlement and the "
       "central ledger entries behind every movement.",
       actions=actions, quote=quote,
       gen=f"Generated {escape(data['generated_at'])} · <span class='mono'>{escape(job_id)}</span>")}
<section class="stats">
  {_stat("card", "gray", "Amount", _money(job["price"], currency), "escrowed for this job")}
  {_stat("check" if state == "PAID" else "refund", "green" if state == "PAID" else "amber", "Status", escape(state),
         "seller paid" if state == "PAID" else "buyer made whole" if state == "REFUNDED" else "in progress")}
  {_stat("shield", "green" if verification["valid_delivery"] else "amber", "Verification",
         "VALID" if verification["valid_delivery"] else "NOT VERIFIED", "against the agreed contract")}
  {_stat("tasks", "blue", "Ledger movements", len(payment["transactions"]), f"{done_count} of {len(steps)} stages done")}
</section>
{_panel("Transaction details", "Identity, parties and hashes for this payment", f"<div class='facts'>{facts}</div>")}
{_panel("Money movement", "Immutable ledger records for this payment",
        f"<div class='table-wrap'><table><thead><tr><th>TX ID</th><th class='num'>Ledger #</th><th>Action</th><th class='num'>Amount</th><th>From</th><th>To</th><th class='num'>Buyer after</th><th class='num'>Buyer locked</th><th>Timestamp (UTC)</th><th>Details</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>",
        right=f"{len(payment['transactions'])} transactions")}
{_panel("Settlement lifecycle", "Key stages of this payment", f"<div class='steps'>{step_html}</div>",
        right=f"{done_count} of {len(steps)} completed")}
<div class="split">
{_panel("Contract", "What the seller agreed to deliver",
        f"<pre>{contract}</pre><p class='muted'>Contract SHA-256: <span class='mono'>{escape(payment['contract_sha256'])}</span> {_copy(payment['contract_sha256'])}</p>")}
{_panel("Integrity", "Reconciliation and market-wide invariants",
        f'''{signed_block}
<div class="toolbar">
  <span class="pill {'ok' if reconciliation['balanced'] else 'err'}">ESCROW {'BALANCED' if reconciliation['balanced'] else 'MISMATCH'}</span>
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
</div>
{_panel("Verification", verification.get("scope", ""),
        f'''<div class="toolbar"><span class="pill {'ok' if verification['valid_delivery'] else 'warn'}">{'VALID DELIVERY' if verification['valid_delivery'] else 'NOT VERIFIED'}</span></div>
<ul>{reason_html}</ul>
{f"<h3>Cart checks</h3><div class='table-wrap'><table><thead><tr><th>Case</th><th class='num'>Expected (cents)</th><th class='num'>Observed (cents)</th><th>Result</th></tr></thead><tbody>{check_rows}</tbody></table></div><p class='muted'>A defect found and reported with execution receipts is a valid audit delivery — the audit did its job.</p>" if check_rows else ""}
<h3>Execution receipts</h3><ul>{receipts}</ul>
<p class="muted">Delivery SHA-256: <span class="mono">{escape(payment['result_sha256'] or '—')}</span></p>
{delivery}''')}
<section class="panel raw-panel"><details class="raw"><summary>Raw receipt JSON</summary><pre>{raw}</pre></details></section>"""
    return shell("../", "payments",
                 [("Workspace", ""), ("Transactions", "payments"), (job_id, "")], content)
