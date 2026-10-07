"""Generate landing.html: a monitoring-style board of the snapshot's restaurants (static, offline).

  python build_landing.py            # writes landing.html; open it in a browser
Each "Ask" link carries a question verified at build time to resolve (via resolve.auto_scope) to exactly
that CAMIS and date, so the chat locks the same scope the tile shows.
"""
import json
from collections import defaultdict
from datetime import date

from config import GROUPS_PATH, ROOT, SNAPSHOT_PATH
from resolve import auto_scope

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]


def human(d):
    y, m, dd = (int(x) for x in d.split("-"))
    return f"{MONTHS[m - 1]} {dd}, {y}"


def title(s):
    return " ".join(w.capitalize() if w.isalpha() else w for w in (s or "").split())


def verified_question(g, d, groups):
    street, boro, zipc = title(" ".join(g["street"].split())), g["boro"], g.get("zipcode")
    name = g["dba"]
    variants = [f"What did inspectors find at {name} on {human(d)}?",
                f"What did inspectors find at {name} on {street} on {human(d)}?",
                f"What did inspectors find at {name} in {boro} on {human(d)}?",
                f"What did inspectors find at {name} on {street}, {boro} on {human(d)}?",
                f"What did inspectors find at {name} (ZIP {zipc}) on {human(d)}?"]
    for q in variants:
        s = auto_scope(q, groups)
        if s["mode"] == "entity" and s["camis"] == g["camis"] and s["date"] == d:
            return q
    return None


def build():
    groups = json.loads(GROUPS_PATH.read_text())
    snap = json.loads(SNAPSHOT_PATH.read_text())
    by = defaultdict(list)
    for g in groups:
        by[g["camis"]].append(g)
    name_count = defaultdict(list)
    for c, gs in by.items():
        name_count[gs[0]["dba"]].append(c)
    restaurants, unresolved = [], 0
    for c, gs in sorted(by.items(), key=lambda kv: (kv[1][0]["dba"], kv[0])):
        g0 = gs[0]
        insp = []
        for g in sorted(gs, key=lambda g: (g["inspection_date_key"], g["inspection_type"] or ""), reverse=True):
            crit = sum(1 for v in g["violations"] if v.get("critical_flag") == "Critical")
            q = verified_question(g0, g["inspection_date_key"], groups)
            unresolved += q is None
            insp.append({"date": g["inspection_date_key"], "type": g["inspection_type"], "score": g["score_values"],
                         "grade": g["grade_values"], "codes": g["violation_codes"], "critical": crit,
                         "n": len(g["violations"]), "q": q, "coverage": g["coverage"]})
        restaurants.append({"camis": c, "dba": g0["dba"], "boro": g0["boro"], "address": " ".join(g0["address"].split()),
                            "cuisine": g0.get("cuisine_description"), "inspections": insp,
                            "twins": [t for t in name_count[g0["dba"]] if t != c]})
    meta = {"snapshot_id": snap["snapshot_id"], "rows": snap["unique_raw_row_count"],
            "restaurants": snap["unique_camis_count"], "groups": snap["inspection_group_count"],
            "retrieved": snap["retrieved_at"][:10], "index": (snap.get("ingestion_outcome") or {}).get("index"),
            "built": date.today().isoformat()}
    html = TEMPLATE.replace("__DATA__", json.dumps({"meta": meta, "restaurants": restaurants}, ensure_ascii=False))
    out = ROOT / "landing.html"
    out.write_text(html)
    site = ROOT / "site"
    site.mkdir(exist_ok=True)
    (site / "landing.html").write_text(html)  # the ONLY file served by the local board server
    print(f"wrote {out} · {len(restaurants)} restaurants · {sum(len(r['inspections']) for r in restaurants)} "
          f"inspections · {unresolved} without a verified question")


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>LiteralLock NYC · Inspection board</title>
<style>
:root{--bg:#090B0E;--side:#0E1217;--surface:#12161F;--card:#151A24;--hover:#1A212E;--elev:#202738;
--hair:rgba(255,255,255,.07);--subtle:rgba(255,255,255,.12);--blue:#2563EB;--blue2:#3B82F6;--bsub:rgba(37,99,235,.12);
--bbord:rgba(37,99,235,.28);--t1:#F8FAFC;--t2:#94A3B8;--t3:#64748B;--ok:#10B981;--okbg:rgba(16,185,129,.10);
--warn:#F59E0B;--warnbg:rgba(245,158,11,.10);--err:#EF4444;--errbg:rgba(239,68,68,.10);--pur:#8B5CF6}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--t1);font:14px/1.5 Inter,ui-sans-serif,system-ui,-apple-system,Segoe UI,sans-serif}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
header{display:flex;align-items:center;justify-content:space-between;padding:18px 28px;border-bottom:1px solid var(--hair);background:var(--side);position:sticky;top:0;z-index:5}
.brand{display:flex;gap:12px;align-items:center}.logo{width:34px;height:34px;border-radius:8px;background:linear-gradient(135deg,#3B82F6,#1D4ED8);display:grid;place-items:center;font-size:18px}
.brand h1{font-size:17px;margin:0}.brand p{margin:0;color:var(--t2);font-size:12px}
.live{display:flex;gap:8px;align-items:center;color:var(--t2);font-size:12px}.pulse{width:8px;height:8px;border-radius:50%;background:var(--ok);box-shadow:0 0 0 0 rgba(16,185,129,.6);animation:p 2s infinite}
@keyframes p{70%{box-shadow:0 0 0 8px rgba(16,185,129,0)}100%{box-shadow:0 0 0 0 rgba(16,185,129,0)}}
main{padding:22px 28px 60px;max-width:1500px;margin:0 auto}
.kpis{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:18px}
.kpi{background:var(--card);border:1px solid var(--hair);border-radius:12px;padding:14px 16px}
.kpi b{display:block;font-size:22px}.kpi span{color:var(--t2);font-size:12px}
.bar{display:flex;gap:10px;align-items:center;margin:6px 0 16px;flex-wrap:wrap}
.search{flex:1;min-width:240px;background:var(--surface);border:1px solid var(--subtle);border-radius:10px;padding:10px 14px;color:var(--t1);font-size:14px;outline:none}
.search:focus{border-color:var(--blue2)}
.chip{background:var(--surface);border:1px solid var(--subtle);color:var(--t2);border-radius:999px;padding:7px 12px;cursor:pointer;font-size:12px;transition:.14s}
.chip.on,.chip:hover{background:var(--bsub);border-color:var(--bbord);color:#93c5fd}
.legend{color:var(--t3);font-size:12px;margin-bottom:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px}
.tile{background:var(--card);border:1px solid var(--hair);border-radius:12px;padding:14px;cursor:pointer;transition:transform .2s cubic-bezier(.16,1,.3,1),border-color .14s,background .14s;position:relative;animation:in .3s ease both}
@keyframes in{from{opacity:0;transform:translateY(6px)}to{opacity:1}}
.tile:hover{transform:translateY(-2px);border-color:var(--bbord);background:var(--hover)}
.tile.twin{border-color:var(--warn);box-shadow:0 0 0 1px var(--warn) inset}
.row{display:flex;align-items:center;gap:8px}.dot{width:9px;height:9px;border-radius:50%;flex-shrink:0}
.name{font-weight:650;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sub{color:var(--t2);font-size:12px;margin:2px 0 8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.camis{font-size:11px;color:#93c5fd;background:var(--bsub);border:1px solid var(--bbord);border-radius:4px;padding:1px 6px}
.dup{font-size:11px;color:var(--warn);background:var(--warnbg);border:1px solid rgba(245,158,11,.3);border-radius:4px;padding:1px 6px}
.spark{display:flex;align-items:flex-end;gap:3px;height:34px;margin:10px 0 6px}
.spark i{flex:0 0 16px;border-radius:3px 3px 0 0;min-height:3px;opacity:.95}.spark i.empty{background:var(--elev);opacity:.5;height:3px}
.foot{display:flex;justify-content:space-between;color:var(--t3);font-size:11px}
.scrim{position:fixed;inset:0;background:rgba(0,0,0,.55);opacity:0;pointer-events:none;transition:.2s;z-index:9}
.scrim.on{opacity:1;pointer-events:auto}
.drawer{position:fixed;top:0;right:0;height:100%;width:min(560px,96vw);background:var(--surface);border-left:1px solid var(--subtle);transform:translateX(100%);transition:transform .25s cubic-bezier(.16,1,.3,1);z-index:10;overflow:auto;padding:24px}
.drawer.on{transform:none}
.drawer h2{margin:6px 0 2px;font-size:22px}.x{float:right;background:none;border:1px solid var(--subtle);color:var(--t2);border-radius:8px;padding:4px 10px;cursor:pointer}
.warnbox{background:var(--warnbg);border:1px solid rgba(245,158,11,.3);color:#fcd34d;border-radius:10px;padding:10px 12px;margin:12px 0;font-size:13px}
.cta{display:inline-flex;gap:8px;align-items:center;background:var(--blue);color:#fff;border:0;border-radius:10px;padding:11px 16px;font-weight:600;cursor:pointer;text-decoration:none;margin:10px 0 4px}
.cta:hover{background:#1D4ED8}
.insp{background:var(--card);border:1px solid var(--hair);border-radius:10px;padding:12px;margin:10px 0}
.insp .top{display:flex;justify-content:space-between;align-items:center;gap:8px}
.code{display:inline-block;font-size:11px;margin:4px 4px 0 0;padding:1px 6px;border-radius:4px;background:var(--elev);color:var(--t2)}
.code.c{background:var(--errbg);color:#fca5a5}
.ask{font-size:12px;color:#93c5fd;background:var(--bsub);border:1px solid var(--bbord);border-radius:6px;padding:4px 9px;cursor:pointer;text-decoration:none;white-space:nowrap}
.q{color:var(--t3);font-size:12px;margin-top:6px}
.note{color:var(--t3);font-size:12px;margin-top:18px;border-top:1px solid var(--hair);padding-top:12px}
.hero{display:flex;justify-content:space-between;align-items:center;gap:24px;background:radial-gradient(1200px 300px at 0% 0%,rgba(37,99,235,.22),transparent 60%),var(--card);border:1px solid var(--hair);border-radius:16px;padding:26px 28px;margin-bottom:16px}
.hero h2{margin:0;font-size:30px;letter-spacing:-.5px}.hero h2 span{background:linear-gradient(90deg,#60A5FA,#3B82F6);-webkit-background-clip:text;background-clip:text;color:transparent}
.hero p{margin:6px 0 0;color:var(--t2);max-width:720px}
.hero .cta{margin:0;white-space:nowrap;box-shadow:0 8px 24px rgba(37,99,235,.35)}
.steps{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap}.steps span{font-size:12px;color:#cbd5e1;background:var(--surface);border:1px solid var(--hair);border-radius:999px;padding:4px 10px}
</style></head><body>
<header><div class="brand"><div class="logo">🔒</div><div><h1>LiteralLock NYC</h1><p>Same name. Different restaurant. Show the evidence.</p></div></div>
<div class="live"><span class="pulse"></span><span id="snap"></span></div></header>
<main>
<div class="hero"><div><h2>Every answer locked to the <span>right restaurant</span>.</h2>
<p>Click any restaurant. LiteralLock searches Elastic (BM25 + Mistral semantic), rejects wrong-restaurant and wrong-date evidence in code, and returns a Mistral answer where every claim cites its source row.</p>
<div class="steps"><span>1 · identity from your words</span><span>2 · two Elastic rankings</span><span>3 · evidence gate</span><span>4 · verified Mistral answer</span></div></div>
<a class="cta" id="openchat" href="#" target="_self">Open evidence chat →</a></div>
<div class="kpis" id="kpis"></div>
<div class="bar"><input class="search" id="q" placeholder="Search restaurant, street, CAMIS…">
<span class="chip on" data-b="">All</span><span class="chip" data-b="Manhattan">Manhattan</span><span class="chip" data-b="Brooklyn">Brooklyn</span>
<span class="chip" data-b="Queens">Queens</span><span class="chip" data-b="Bronx">Bronx</span><span class="chip" data-b="Staten Island">Staten Island</span>
<span class="chip" id="dupf">⚠ Same name only</span></div>
<div class="legend">Dot = critical violations recorded at the latest inspection in this snapshot (<b style="color:var(--ok)">●</b> 0 · <b style="color:var(--warn)">●</b> 1–2 · <b style="color:var(--err)">●</b> 3+). Bars = recorded score per inspection (older → newer). A recorded snapshot field, not a current safety rating.</div>
<div class="grid" id="grid"></div>
</main>
<div class="scrim" id="scrim"></div><aside class="drawer" id="drawer"></aside>
<script>
const DATA=__DATA__;
const CHAT=(new URLSearchParams(location.search).get("chat"))||"http://localhost:8502";
const M=DATA.meta, R=DATA.restaurants; let boro="", dupOnly=false, term="";
document.getElementById("snap").textContent=`Snapshot ${M.snapshot_id} · NYC Open Data 43nn-pn8j · retrieved ${M.retrieved}`;
const k=[["Restaurants",M.restaurants],["Inspection groups",M.groups],["Violation rows",M.rows],["Same-name pairs",R.filter(r=>r.twins.length).length/2],["Elastic index",`<span class="mono" style="font-size:13px">${M.index}</span>`]];
document.getElementById("kpis").innerHTML=k.map(([a,b])=>`<div class="kpi"><b>${b}</b><span>${a}</span></div>`).join("");
const col=c=>c>=3?"var(--err)":c>=1?"var(--warn)":"var(--ok)";
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const askUrl=q=>`${CHAT}/?q=${encodeURIComponent(q)}`;
function tile(r){const L=r.inspections[0]||{};const bars=r.inspections.slice(0,10).reverse().map(i=>{const s=parseInt((i.score||[])[0]);const h=isNaN(s)?3:Math.min(34,4+s*0.5);return `<i title="${i.date} · score ${isNaN(s)?"n/a":s}" style="height:${h}px;background:${col(i.critical)}"></i>`}).join("")+'<i class="empty"></i>'.repeat(Math.max(0,10-Math.min(10,r.inspections.length)));
return `<div class="tile" data-c="${r.camis}" data-n="${esc(r.dba)}"><div class="row"><span class="dot" style="background:${col(L.critical||0)}"></span><span class="name">${esc(r.dba)}</span></div>
<div class="sub">${esc(r.boro)} · ${esc(r.cuisine||"")}</div><div class="row"><span class="camis mono">CAMIS ${r.camis}</span>${r.twins.length?`<span class="dup">same name ×${r.twins.length+1}</span>`:""}</div>
<div class="spark">${bars}</div><div class="foot"><span>latest ${L.date||"—"}</span><span>${r.inspections.length} inspection${r.inspections.length>1?"s":""}</span></div></div>`}
function render(){const t=term.toLowerCase();const list=R.filter(r=>(!boro||r.boro===boro)&&(!dupOnly||r.twins.length)&&(!t||(r.dba+" "+r.address+" "+r.camis).toLowerCase().includes(t)));
const g=document.getElementById("grid");g.innerHTML=list.map(tile).join("")||`<div class="legend">No restaurants match.</div>`;
g.querySelectorAll(".tile").forEach(el=>{el.onclick=()=>open(el.dataset.c);el.onmouseenter=()=>g.querySelectorAll(`.tile[data-n="${CSS.escape(el.dataset.n)}"]`).forEach(x=>x!==el&&x.classList.add("twin"));el.onmouseleave=()=>g.querySelectorAll(".tile.twin").forEach(x=>x.classList.remove("twin"))})}
function open(c){const r=R.find(x=>x.camis===c), L=r.inspections[0];const tw=r.twins.map(t=>R.find(x=>x.camis===t));
document.getElementById("drawer").innerHTML=`<button class="x" onclick="closeD()">Close ✕</button>
<span class="camis mono">CAMIS ${r.camis}</span><h2>${esc(r.dba)}</h2><div class="sub" style="white-space:normal">${esc(r.address)} · ${esc(r.cuisine||"")}</div>
${tw.length?`<div class="warnbox">⚠ <b>Same name, different restaurant.</b> ${tw.map(x=>`CAMIS <span class="mono">${x.camis}</span> · ${esc(x.address)}`).join("<br>")}<br>LiteralLock locks on CAMIS, so answers never mix these.</div>`:""}
${L&&L.q?`<a class="cta" href="${askUrl(L.q)}" target="_self">Ask LiteralLock about ${L.date} →</a><div class="q">“${esc(L.q)}”</div>`:""}
<h3 style="margin:18px 0 4px;font-size:14px;color:var(--t2)">Inspections in this snapshot</h3>
${r.inspections.map(i=>`<div class="insp"><div class="top"><div><b>${i.date}</b> <span class="sub" style="display:inline">· ${esc(i.type||"type not recorded")}</span></div>
${i.q?`<a class="ask" href="${askUrl(i.q)}" target="_self">Ask →</a>`:`<span class="q">no verified question</span>`}</div>
<div class="sub" style="margin:2px 0">score ${i.score.length?i.score.join(" | "):"not recorded"} · grade ${i.grade.length?i.grade.join(" | "):"not recorded"} · ${i.n} violation row${i.n===1?"":"s"} · <span style="color:${col(i.critical)}">${i.critical} critical</span></div>
<div>${i.codes.map(x=>`<span class="code mono">${x}</span>`).join("")}</div></div>`).join("")}
<div class="note">Bounded snapshot of NYC Open Data rows; recorded fields, not current status. Clicking “Ask” opens the evidence chat with a question that code resolves to exactly this CAMIS and date.</div>`;
document.getElementById("drawer").classList.add("on");document.getElementById("scrim").classList.add("on")}
function closeD(){document.getElementById("drawer").classList.remove("on");document.getElementById("scrim").classList.remove("on")}
document.getElementById("scrim").onclick=closeD;document.addEventListener("keydown",e=>e.key==="Escape"&&closeD());
document.getElementById("q").oninput=e=>{term=e.target.value;render()};
document.querySelectorAll(".chip[data-b]").forEach(ch=>ch.onclick=()=>{document.querySelectorAll(".chip[data-b]").forEach(x=>x.classList.remove("on"));ch.classList.add("on");boro=ch.dataset.b;render()});
document.getElementById("dupf").onclick=e=>{dupOnly=!dupOnly;e.target.classList.toggle("on",dupOnly);render()};
document.getElementById("openchat").href=CHAT+"/";
render();
const _o=new URLSearchParams(location.search).get("open");if(_o)open(_o);
</script></body></html>
"""

if __name__ == "__main__":
    build()
