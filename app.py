"""Flask app for the Bonds project.

One UI, one button (Aggiorna). Screener + chart + table + anomalies + export.

Routes
------
GET  /                       -> screener page
GET  /api/bonds              -> all bonds with latest price + derived fields
POST /api/sync               -> start a scrape run in a background thread
POST /api/sync/stop          -> request cancel
GET  /api/sync/status        -> current progress + last completed result
GET  /api/export             -> portable self-contained HTML download
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Flask, Response, jsonify, make_response, render_template_string, request

from calculations import (
    average_yield,
    enrich_bond,
    find_anomalies,
    yield_by_nation,
)
from database import Database
from scraper import SCRAPE_PROFILES, ScrapeStats, run_scrape


log = logging.getLogger("bonds.app")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


app = Flask(__name__)
DB = Database()


# ──────────────────────────────────────────────────────────────────────────────
# Sync state (in-memory; one sync at a time)
# ──────────────────────────────────────────────────────────────────────────────
class SyncState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.thread: Optional[threading.Thread] = None
        self.cancel_requested = False
        self.status: str = "idle"          # idle | running | completed | failed | stopped
        self.started_at: Optional[str] = None
        self.finished_at: Optional[str] = None
        self.profile_stats: Dict[str, dict] = {}
        self.error: Optional[str] = None

    def begin(self) -> bool:
        with self._lock:
            if self.thread is not None and self.thread.is_alive():
                return False
            self.cancel_requested = False
            self.status = "running"
            self.started_at = datetime.now().isoformat(timespec="seconds")
            self.finished_at = None
            self.profile_stats = {}
            self.error = None
            return True

    def update_profile(self, stats: ScrapeStats) -> None:
        with self._lock:
            self.profile_stats[stats.profile] = stats.asdict()

    def finish(self, status: str, error: Optional[str] = None) -> None:
        with self._lock:
            self.status = status
            self.error = error
            self.finished_at = datetime.now().isoformat(timespec="seconds")

    def to_dict(self) -> dict:
        with self._lock:
            return {
                "status": self.status,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "profile_stats": dict(self.profile_stats),
                "error": self.error,
                "cancel_requested": self.cancel_requested,
            }


SYNC = SyncState()


def _run_sync_thread() -> None:
    try:
        results = run_scrape(
            DB,
            cancel_flag=lambda: SYNC.cancel_requested,
            page_callback=SYNC.update_profile,
        )
        had_error = any(r.get("error") for r in results.values()
                        if isinstance(r, dict))
        SYNC.finish("stopped" if SYNC.cancel_requested
                    else ("failed" if had_error else "completed"),
                    error=results.get("__error__", {}).get("error") if had_error else None)
    except Exception as exc:
        log.error("Sync thread crashed: %s", exc, exc_info=True)
        SYNC.finish("failed", error=str(exc))


# ──────────────────────────────────────────────────────────────────────────────
# API
# ──────────────────────────────────────────────────────────────────────────────
def _enriched_bonds() -> List[dict]:
    raw = DB.list_bonds_with_latest_price()
    today = date.today()
    return [enrich_bond(b, reference=today) for b in raw]


@app.get("/api/bonds")
def api_bonds() -> Response:
    bonds = _enriched_bonds()
    avg = average_yield(bonds)
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total": len(bonds),
        "with_price": sum(1 for b in bonds if b.get("net_yield_pa") is not None),
        "average_yield": avg,
        "bonds": bonds,
        "anomalies": find_anomalies(bonds),
        # Sovereign-only EUR bonds in the 10y proxy band (7-12y). This filters
        # out supranationals (EIB, World Bank, EFSF, ...) which would inflate
        # geo_area buckets, and excludes USD/GBP/etc. bonds whose yield reflects
        # the local rate curve rather than sovereign credit risk.
        "nations": yield_by_nation(
            bonds, min_count=2, years_range=(7.0, 12.0),
            currency="EUR", sovereign_only=True,
        ),
        "nations_all": yield_by_nation(bonds, min_count=2),  # diagnostics
        "last_run": DB.last_scrape_run(),
    }
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.post("/api/sync")
def api_sync_start() -> Response:
    if not SYNC.begin():
        return jsonify({"queued": False, "reason": "already_running"}), 409
    SYNC.thread = threading.Thread(target=_run_sync_thread, daemon=True)
    SYNC.thread.start()
    return jsonify({"queued": True})


@app.post("/api/sync/stop")
def api_sync_stop() -> Response:
    SYNC.cancel_requested = True
    return jsonify({"requested": True})


@app.get("/api/sync/status")
def api_sync_status() -> Response:
    return jsonify(SYNC.to_dict())


@app.get("/api/export")
def api_export() -> Response:
    bonds = _enriched_bonds()
    avg = average_yield(bonds)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    html = _build_portable_html(
        bonds=bonds,
        anomalies=find_anomalies(bonds),
        nations=yield_by_nation(
            bonds, min_count=2, years_range=(7.0, 12.0),
            currency="EUR", sovereign_only=True,
        ),
        avg_yield=avg,
        generated_at=generated_at,
    )
    resp = make_response(html)
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    fname = f"screener_obbligazioni_{date.today().isoformat()}.html"
    resp.headers["Content-Disposition"] = f'attachment; filename="{fname}"'
    return resp


# ──────────────────────────────────────────────────────────────────────────────
# Routes — main page
# ──────────────────────────────────────────────────────────────────────────────
@app.get("/")
def index() -> Response:
    return Response(MAIN_TEMPLATE, mimetype="text/html")


# ──────────────────────────────────────────────────────────────────────────────
# Templates (kept inline for portability; live + portable share most CSS)
# ──────────────────────────────────────────────────────────────────────────────
_SHARED_CSS = r"""
:root{--bg:#f4f7fb;--panel:#fff;--ink:#22313f;--muted:#5f7183;--primary:#153a5b;--accent:#2a7abf;--line:#d8e3ec}
*{box-sizing:border-box}
html,body{overflow-x:hidden}
body{margin:0;padding:32px 20px 48px;background:radial-gradient(circle at top,#fff 0%,var(--bg) 56%);color:var(--ink);font-family:'Segoe UI',-apple-system,Arial,sans-serif;-webkit-text-size-adjust:100%}
.shell{max-width:1040px;margin:0 auto}
.hdr{background:linear-gradient(135deg,#102a43,#1f5d8e);color:#fff;padding:28px 30px;border-radius:16px;box-shadow:0 20px 50px rgba(16,42,67,.18);margin-bottom:18px}
.hdr h1{margin:0;font-size:30px;letter-spacing:.02em}
.hdr p{margin:8px 0 0;color:rgba(255,255,255,.88);font-size:14px}
.act-bar{display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding:14px 16px;background:var(--panel);border:1px solid var(--line);border-radius:14px;box-shadow:0 8px 24px rgba(20,41,61,.06);margin-bottom:18px}
.act-bar .ts{margin-left:auto;color:var(--muted);font-size:13px}
.btn{border:0;border-radius:10px;padding:10px 16px;font-weight:700;cursor:pointer;font-size:14px;white-space:nowrap}
.btn-p{background:#18486f;color:#fff;box-shadow:0 8px 18px rgba(24,72,111,.18)}
.btn-s{background:#e2f0fb;color:#153a5b}
.btn:disabled{opacity:.55;cursor:wait}
.sync-wrap{display:none;padding:14px 16px;background:var(--panel);border:1px solid var(--line);border-radius:14px;margin-bottom:18px}
.sync-wrap.on{display:block}
.sync-hdr{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;font-weight:600;color:var(--primary)}
.pbg{height:10px;border-radius:999px;background:#d8e3ec;overflow:hidden;margin-bottom:6px}
.pf{width:0%;height:100%;border-radius:inherit;background:linear-gradient(90deg,#2a7abf,#2f855a);transition:width .35s}
.pf.indeterminate{width:35%!important;background:linear-gradient(90deg,transparent,#2a7abf,#2f855a,transparent);background-size:200% 100%;animation:shimmer 1.4s linear infinite}
@keyframes shimmer{from{background-position:200% 0}to{background-position:-200% 0}}
.profile-line{display:grid;grid-template-columns:auto 1fr auto;gap:10px;align-items:center;padding:6px 0;font-size:13px}
.profile-line .lbl{font-weight:600;color:var(--primary)}
.profile-line .det{color:var(--muted)}
.profile-line .ok{color:#216a4a}
.profile-line .err{color:#b43232;font-weight:600}
.pmeta{display:flex;justify-content:space-between;font-size:13px;color:var(--muted)}
.btn-stop{background:rgba(180,50,50,.12);color:#b43232;border:1px solid rgba(180,50,50,.3);border-radius:8px;padding:4px 12px;font-size:13px;cursor:pointer}
.sum-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:24px}
.sum-card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px;box-shadow:0 8px 24px rgba(20,41,61,.06)}
.sum-card .lbl{display:block;font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin-bottom:6px}
.sum-card .val{font-size:22px;font-weight:700;color:var(--primary)}
.sum-card .sub{display:block;font-size:11px;color:var(--muted);margin-top:4px}
.info{background:var(--panel);border:1px solid var(--line);border-left:6px solid var(--accent);border-radius:14px;padding:16px 18px;margin-bottom:20px;box-shadow:0 8px 24px rgba(20,41,61,.06);font-size:14px}
.info .fml{display:inline-block;margin-top:8px;padding:10px 12px;background:#edf5fc;border-radius:10px;color:var(--primary);font-family:Consolas,monospace;font-size:12px;word-break:break-word}
.sec{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:20px 20px 8px;margin-bottom:20px;box-shadow:0 8px 24px rgba(20,41,61,.06)}
h2{margin:0 0 4px;color:var(--primary);font-size:20px}
.sec p{margin:0 0 14px;color:var(--muted);font-size:13px}
.chart-box{background:linear-gradient(180deg,#f8fbfe,#eef3f8);border-radius:14px;border:1px solid var(--line);padding:12px;min-height:380px}
#yc{width:100%;min-height:360px}
#yc-nation{width:100%;min-height:300px}
.chart-box.nation{min-height:340px}
.tbl-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{width:100%;border-collapse:collapse;background:#fff;border-radius:12px}
th,td{padding:10px 12px;text-align:left;border-bottom:1px solid #edf2f7;font-size:14px;white-space:nowrap}
th{background:#18486f;color:#fff;font-weight:600}
tr:nth-child(even){background:#f7fafc}
#bondTable thead tr.cf th{background:#f8fbfe;padding:8px 10px}
#bondTable thead tr.cf select{width:100%;padding:6px 8px;border:1px solid var(--line);border-radius:8px;background:#fff;color:var(--ink);min-width:120px}
div.dataTables_wrapper div.dataTables_filter input,div.dataTables_wrapper div.dataTables_length select{border:1px solid var(--line);border-radius:8px;padding:6px 8px;background:#fff}
.hlrow td{animation:pulse 2s ease-in-out}
@keyframes pulse{0%{background:#fff3bf}45%{background:#ffec99}100%{background:inherit}}
.ftr{text-align:center;color:var(--muted);font-size:12px;margin-top:24px;padding-top:16px;border-top:1px solid var(--line)}
@media(max-width:600px){
  body{padding:14px 8px 28px}
  .shell{max-width:100%}
  .hdr{padding:18px 16px;border-radius:12px;margin-bottom:14px}
  .hdr h1{font-size:20px;line-height:1.2}
  .hdr p{font-size:12px}
  .act-bar{padding:10px 12px;border-radius:12px;gap:8px;margin-bottom:14px}
  .act-bar .ts{margin-left:0;width:100%;text-align:right;font-size:12px}
  .btn{padding:8px 12px;font-size:13px;flex:1 1 auto;text-align:center}
  .sync-wrap{padding:12px;border-radius:12px;margin-bottom:14px}
  .sync-hdr{font-size:13px}
  .pmeta{font-size:12px}
  .profile-line{grid-template-columns:1fr;gap:4px;font-size:12px}
  .sum-grid{grid-template-columns:repeat(2,1fr);gap:8px;margin-bottom:18px}
  .sum-card{padding:10px;border-radius:10px}
  .sum-card .lbl{font-size:10px;margin-bottom:4px}
  .sum-card .val{font-size:18px}
  .sum-card .sub{font-size:10px;margin-top:2px}
  .info{padding:12px 14px;font-size:12px;border-radius:12px;margin-bottom:14px}
  .info .fml{font-size:10px;padding:6px 8px}
  .sec{padding:14px 12px 4px;border-radius:12px;margin-bottom:14px}
  h2{font-size:17px}
  .sec p{font-size:12px;margin-bottom:10px}
  .chart-box{padding:6px;min-height:320px}
  .chart-box.nation{min-height:300px}
  #yc{min-height:300px}
  #yc-nation{min-height:280px}
  th,td{padding:7px 8px;font-size:12px}
  .ftr{font-size:11px}
  /* Hide less-essential columns on phones to keep the table readable.
     Order: ISIN, Name, Geo, Currency, Issuer, Duration, Maturity, Price, Coupon, Net Yield, Categoria. */
  #bondTable th:nth-child(4),#bondTable td:nth-child(4),     /* Currency */
  #bondTable th:nth-child(5),#bondTable td:nth-child(5),     /* Issuer */
  #bondTable th:nth-child(6),#bondTable td:nth-child(6),     /* Duration */
  #bondTable th:nth-child(8),#bondTable td:nth-child(8),     /* Price */
  #bondTable th:nth-child(11),#bondTable td:nth-child(11)    /* Categoria */
  {display:none}
  div.dataTables_wrapper div.dataTables_length,div.dataTables_wrapper div.dataTables_filter,
  div.dataTables_wrapper div.dataTables_info,div.dataTables_wrapper div.dataTables_paginate{font-size:12px}
  .dataTables_paginate .paginate_button{padding:4px 8px!important;min-width:0!important}
}
"""

_SHARED_RENDER_JS = r"""
let dtInst=null,anomTableInst=null;
function esc(v){return String(v??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
function fn(v){const n=Number(v);return isFinite(n)?n.toFixed(2):'—'}
function fp(v){const n=Number(v);return isFinite(n)?n.toFixed(2)+'%':'—'}
function rid(isin){return 'r-'+String(isin||'').replace(/[^A-Za-z0-9_-]/g,'')}
function updCards(payload){
  const bonds=payload.bonds||[];
  const total=bonds.length;
  const withPrice=bonds.filter(b=>b.net_yield_pa!==null&&b.net_yield_pa!==undefined).length;
  const gov=bonds.filter(b=>(b.issuer_type||'').toLowerCase()==='government').length;
  const corp=bonds.filter(b=>(b.issuer_type||'').toLowerCase()==='corporate').length;
  const avg=payload.average_yield;
  document.getElementById('c0').textContent=total;
  document.getElementById('c0sub').textContent=withPrice+' con prezzo';
  document.getElementById('c1').textContent=gov;
  document.getElementById('c2').textContent=corp;
  document.getElementById('c3').textContent=(avg!==null&&avg!==undefined)?Number(avg).toFixed(2)+'%':'—';
  document.getElementById('c3sub').textContent='su '+withPrice+' bond';
}
const PAL=['#2a7abf','#e28743','#2f855a','#7b61ff','#c05621','#1f4e79','#6b4c9a','#c0392b'];
function renderChart(bonds){
  const div=document.getElementById('yc');
  if(!div||typeof Plotly==='undefined')return;
  const g=new Map();
  (bonds||[]).forEach(b=>{
    const y=Number(b.years_to_maturity);const yv=Number(b.net_yield_pa);
    if(!isFinite(y)||y<=0||y>35||!isFinite(yv)||yv<-2||yv>15)return;
    const k=(b.currency||'EUR')+'|'+(b.issuer_type||'N/D');
    if(!g.has(k))g.set(k,{c:b.currency,t:b.issuer_type,x:[],y:[],cd:[]});
    const gr=g.get(k);gr.x.push(y);gr.y.push(yv);
    gr.cd.push([b.isin,b.name,Number(b.latest_price),b.duration_bucket,b.currency,b.issuer_type]);
  });
  const isMobile=window.innerWidth<600;
  const traces=Array.from(g.values()).map((gr,i)=>({
    type:'scatter',mode:'markers',name:gr.c+', '+gr.t,
    x:gr.x,y:gr.y,customdata:gr.cd,
    marker:{color:PAL[i%PAL.length],opacity:.6,size:9,symbol:(gr.t||'').toLowerCase()==='government'?'circle':'diamond'},
    hovertemplate:'Currency=%{customdata[4]}<br>Issuer Type=%{customdata[5]}<br>Years=%{x:.2f}<br>Net Yield=%{y:.2f}%<br>ISIN=%{customdata[0]}<br>%{customdata[1]}<br>Price=%{customdata[2]:.2f}<br>%{customdata[3]}<extra></extra>'
  }));
  Plotly.newPlot(div,traces,{
    paper_bgcolor:'#f4f7fb',plot_bgcolor:'#f4f7fb',font:{color:'#22313f'},
    height:isMobile?340:440,
    legend:isMobile
      ?{orientation:'h',yanchor:'top',y:-0.25,xanchor:'left',x:0,font:{size:10}}
      :{orientation:'v',yanchor:'top',y:1,xanchor:'left',x:1.02},
    margin:isMobile?{t:20,l:42,r:18,b:90}:{t:35,l:50,r:130,b:50},
    hovermode:'closest',
    xaxis:{title:'Years to Maturity',range:[0,32],showgrid:true,gridcolor:'#d8e3ec',zeroline:false},
    yaxis:{title:'Net Yield %',range:[0,10],showgrid:true,gridcolor:'#d8e3ec',zeroline:false},
    shapes:[
      {type:'line',x0:3,x1:3,y0:0,y1:1,xref:'x',yref:'y domain',line:{color:'#7aa6c8',dash:'dash'},opacity:.8},
      {type:'line',x0:7,x1:7,y0:0,y1:1,xref:'x',yref:'y domain',line:{color:'#7aa6c8',dash:'dash'},opacity:.8}
    ],
    annotations:[
      {x:1.5,y:1,yref:'paper',text:'Short',showarrow:false,font:{color:'#42637f',size:10}},
      {x:5,y:1,yref:'paper',text:'Medium',showarrow:false,font:{color:'#42637f',size:10}},
      {x:28,y:1,yref:'paper',text:'Long',showarrow:false,font:{color:'#42637f',size:10}}
    ]
  },{responsive:true,displayModeBar:!isMobile});
}
function renderNationChart(rows){
  const div=document.getElementById('yc-nation');
  if(!div||typeof Plotly==='undefined')return;
  if(!rows||rows.length===0){
    div.innerHTML='<div style="padding:30px;text-align:center;color:var(--muted);font-size:13px">Dati insufficienti per il confronto fra nazioni.</div>';
    return;
  }
  const isMobile=window.innerWidth<600;
  // Use MEDIAN as the primary statistic — robust to maturity-mix outliers.
  // Sort ascending so the highest median ends up at the top of the horizontal chart.
  const data=rows.slice().sort((a,b)=>a.median-b.median);
  const colors=data.map(r=>r.nation==='Italia'?'#e28743':'#2a7abf');
  const customdata=data.map(r=>[r.count,r.min,r.max,r.avg]);
  const trace={
    type:'bar',orientation:'h',
    x:data.map(r=>r.median),
    y:data.map(r=>r.nation),
    text:data.map(r=>r.median.toFixed(2)+'% ('+r.count+')'),
    textposition:'outside',
    marker:{color:colors},
    customdata:customdata,
    hovertemplate:'<b>%{y}</b><br>'
      +'Yield mediano: %{x:.2f}%<br>'
      +'Yield medio: %{customdata[3]:.2f}%<br>'
      +'Bond conteggiati: %{customdata[0]}<br>'
      +'Min: %{customdata[1]:.2f}%<br>'
      +'Max: %{customdata[2]:.2f}%<extra></extra>'
  };
  const maxX=Math.max(...data.map(r=>r.median));
  Plotly.newPlot(div,[trace],{
    paper_bgcolor:'#f4f7fb',plot_bgcolor:'#f4f7fb',font:{color:'#22313f'},
    height:Math.max(220,data.length*(isMobile?28:30)+60),
    margin:isMobile?{t:10,l:80,r:30,b:35}:{t:10,l:110,r:60,b:40},
    xaxis:{title:'Yield netto mediano (%)',showgrid:true,gridcolor:'#d8e3ec',zeroline:false,
      range:[0,maxX*1.2]},
    yaxis:{automargin:true},
    showlegend:false
  },{responsive:true,displayModeBar:false});
}
function fixIds(){document.querySelectorAll('#bondTable tbody tr').forEach(r=>{const isin=r.cells[0]?.textContent?.trim();if(isin)r.id=rid(isin);})}
function scrollToIsin(isin){
  if(dtInst){dtInst.search(isin).draw();fixIds();}
  const row=document.getElementById(rid(isin));
  if(!row)return;
  row.scrollIntoView({behavior:'smooth',block:'center'});
  row.classList.remove('hlrow');void row.offsetWidth;row.classList.add('hlrow');
  setTimeout(()=>row.classList.remove('hlrow'),2200);
}
function renderTable(bonds){
  const rows=(bonds||[]).slice().sort((a,b)=>(Number(b.net_yield_pa)||-99)-(Number(a.net_yield_pa)||-99))
    .map(b=>'<tr id="'+rid(b.isin)+'">'
      +'<td>'+esc(b.isin)+'</td>'
      +'<td>'+esc(b.name)+'</td>'
      +'<td>'+esc(b.geo_area||'')+'</td>'
      +'<td>'+esc(b.currency||'')+'</td>'
      +'<td>'+esc(b.issuer_type||'')+'</td>'
      +'<td>'+esc(b.duration_bucket||'')+'</td>'
      +'<td>'+esc(b.maturity_date||'')+'</td>'
      +'<td>'+fn(b.latest_price)+'</td>'
      +'<td>'+fp(b.coupon)+'</td>'
      +'<td>'+fp(b.net_yield_pa)+'</td>'
      +'<td>'+esc(b.category||'')+'</td>'
      +'</tr>').join('');
  if(dtInst){dtInst.destroy();dtInst=null;}
  $('#bondTable thead tr.cf').remove();
  document.getElementById('tb').innerHTML=rows||'<tr><td colspan="11">Nessun bond ancora scaricato. Click “Aggiorna” per scaricare.</td></tr>';
  if(!rows)return;
  dtInst=$('#bondTable').DataTable({
    pageLength:25,order:[[9,'desc']],destroy:true,
    initComplete:function(){
      const api=this.api(),fc=[2,3,4,5,10];
      const fr=$('<tr class="cf"></tr>').appendTo($('#bondTable thead'));
      $('#bondTable thead tr').first().children('th').each(function(i){
        const c=$('<th></th>').appendTo(fr);
        if(!fc.includes(i))return;
        const col=api.column(i);
        const s=$('<select><option value="">All '+$(this).text()+'</option></select>').appendTo(c)
          .on('change',function(){const v=$.fn.dataTable.util.escapeRegex($(this).val());col.search(v?'^'+v+'$':'',true,false).draw();});
        col.data().unique().sort().each(v=>{if(v)s.append('<option value="'+v+'">'+v+'</option>');});
      });
      fixIds();
    }
  });
}
function renderAnomalies(anom){
  const tb=document.getElementById('anom-tb');
  if(!tb)return;
  if(anomTableInst){anomTableInst.destroy();anomTableInst=null;}
  if(!anom||anom.length===0){
    tb.innerHTML='<tr><td colspan="7" style="text-align:center;color:var(--muted);padding:18px">Nessuna anomalia significativa rilevata.</td></tr>';
    return;
  }
  tb.innerHTML=anom.map(a=>'<tr data-isin="'+esc(a.isin)+'" style="cursor:pointer">'
    +'<td>'+esc(a.isin)+'</td>'
    +'<td>'+esc(a.name)+'</td>'
    +'<td>'+esc(a.maturity_date)+'</td>'
    +'<td>'+Number(a.years).toFixed(2)+'</td>'
    +'<td>'+Number(a.yield).toFixed(2)+'%</td>'
    +'<td>'+Number(a.peer_mean).toFixed(2)+'%</td>'
    +'<td><strong style="color:#216a4a">+'+Number(a.spread).toFixed(2)+'%</strong> <span style="color:#5f7183;font-size:11px">('+a.peer_count+' peers)</span></td>'
    +'</tr>').join('');
  anomTableInst=$('#anomTable').DataTable({pageLength:5,order:[[6,'desc']],destroy:true,searching:false,paging:false,info:false,lengthChange:false});
  tb.querySelectorAll('tr[data-isin]').forEach(tr=>tr.addEventListener('click',()=>scrollToIsin(tr.dataset.isin)));
}
"""

MAIN_TEMPLATE = r"""<!DOCTYPE html>
<html lang="it">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Screener Obbligazionario</title>
  <link rel="stylesheet" href="https://cdn.datatables.net/1.13.6/css/jquery.dataTables.min.css">
  <script src="https://code.jquery.com/jquery-3.7.0.min.js"></script>
  <script src="https://cdn.datatables.net/1.13.6/js/jquery.dataTables.min.js"></script>
  <script src="https://cdn.plot.ly/plotly-3.1.0.min.js"></script>
  <style>__SHARED_CSS__</style>
</head>
<body><div class="shell">
  <div class="hdr">
    <h1>Screener Obbligazionario</h1>
    <p>Dati live da Borsa Italiana (filtri Plain Vanilla, no callable, no floating, no inflation-linked)</p>
  </div>
  <div class="act-bar">
    <button class="btn btn-p" id="sync-btn">&#8635; Aggiorna</button>
    <button class="btn btn-s" id="exp-btn">&#8595; Esporta Report Portabile</button>
    <span class="ts">Ultimo sync: <span id="ts">—</span></span>
  </div>

  <div class="sync-wrap" id="sync-wrap">
    <div class="sync-hdr">
      <span id="sync-txt">Sincronizzazione in corso…</span>
      <button class="btn-stop" id="stop-btn">&#9632; Stop</button>
    </div>
    <div class="pbg"><div class="pf indeterminate" id="pf"></div></div>
    <div id="profile-list" style="margin-top:10px"></div>
  </div>

  <div class="sum-grid">
    <div class="sum-card"><span class="lbl">Bonds</span><span class="val" id="c0">—</span><span class="sub" id="c0sub"></span></div>
    <div class="sum-card"><span class="lbl">Government</span><span class="val" id="c1">—</span></div>
    <div class="sum-card"><span class="lbl">Corporate</span><span class="val" id="c2">—</span></div>
    <div class="sum-card"><span class="lbl">Yield medio</span><span class="val" id="c3">—</span><span class="sub" id="c3sub"></span></div>
  </div>

  <div class="info">
    <strong>Methodology:</strong> Net Annualized Yield calcolato dopo tassazione (12.5% govt, 26% corporate). Geo area dal prefisso ISIN, simbolo per tipo emittente.
    <div class="fml">Net Yield = [Coupon &times; (1−t) + (((100−Price)/Anni) &times; (1−t))] / Price</div>
  </div>

  <div class="sec">
    <h2>Curva dei rendimenti</h2>
    <p>Rendimenti netti annualizzati per durata. I bond senza prezzo non compaiono qui.</p>
    <div class="chart-box"><div id="yc"></div></div>
  </div>

  <div class="sec">
    <h2>Yield per nazione</h2>
    <p>Yield netto <strong>mediano</strong> dei <strong>soli titoli di stato sovrani in EUR</strong> con scadenza <strong>7&ndash;12 anni</strong> (proxy 10y). La nazione &egrave; dedotta dal nome del bond, non dall'ISIN. Sopranazionali (EIB, EFSF, World Bank...) e bond in altre valute sono esclusi per garantire confronto apples-to-apples del solo rischio credito. Conteggio fra parentesi.</p>
    <div class="chart-box nation"><div id="yc-nation"></div></div>
  </div>

  <div class="sec">
    <h2>Bond Dashboard Table</h2>
    <p>Tabella interattiva ordinata per rendimento netto annuo. Filtri sulle colonne Geo, Currency, Issuer, Duration, Categoria.</p>
    <div class="tbl-wrap">
      <table id="bondTable" class="display compact stripe hover">
        <thead><tr><th>ISIN</th><th>Name</th><th>Geo</th><th>Currency</th><th>Issuer</th><th>Duration</th><th>Maturity</th><th>Price</th><th>Coupon</th><th>Net Yield</th><th>Categoria</th></tr></thead>
        <tbody id="tb"><tr><td colspan="11">Caricamento…</td></tr></tbody>
      </table>
    </div>
  </div>

  <div class="sec">
    <h2>Anomalie BTP EUR</h2>
    <p>I 2 BTP EUR governativi italiani con il maggior scostamento positivo dal yield medio dei pari (±1 anno). Click sulla riga per filtrare la tabella principale.</p>
    <div class="tbl-wrap">
      <table id="anomTable" class="display compact stripe hover">
        <thead><tr><th>ISIN</th><th>Name</th><th>Maturity</th><th>Years</th><th>Net Yield</th><th>Peer Avg</th><th>Spread</th></tr></thead>
        <tbody id="anom-tb"></tbody>
      </table>
    </div>
  </div>

  <div class="ftr">Dati indicativi — non costituiscono consulenza finanziaria</div>
</div>

<script>__SHARED_RENDER_JS__</script>
<script>
let pollTimer=null;
async function loadData(){
  try{
    const r=await fetch('/api/bonds',{cache:'no-store'});
    if(!r.ok)throw new Error('HTTP '+r.status);
    const p=await r.json();
    updCards(p);
    renderChart(p.bonds||[]);
    renderNationChart(p.nations||[]);
    renderTable(p.bonds||[]);
    renderAnomalies(p.anomalies||[]);
    if(p.last_run&&p.last_run.finished_at){
      document.getElementById('ts').textContent=new Date(p.last_run.finished_at).toLocaleString('it-IT');
    }else if(p.generated_at){
      document.getElementById('ts').textContent=new Date(p.generated_at).toLocaleString('it-IT');
    }
  }catch(e){
    console.error(e);
    document.getElementById('tb').innerHTML='<tr><td colspan="11">Errore caricamento dati. Server attivo?</td></tr>';
  }
}
function showSync(on){
  document.getElementById('sync-wrap').classList.toggle('on',on);
  document.getElementById('sync-btn').disabled=on;
}
const TOTAL_PROFILES=2;  // fixed_vanilla + zero_coupon
function profileBadge(p){
  if(p.error){return '<span class="err">❌ '+esc(p.error)+'</span>';}
  if(p.pages===0&&p.rows===0){return '<span class="det">apertura form…</span>';}
  return '<span class="det">pagina '+p.pages+' &nbsp;•&nbsp; '+p.rows+' bond</span>';
}
function renderProfileList(s){
  const wrap=document.getElementById('profile-list');
  if(!wrap)return;
  const seen=Object.values(s.profile_stats||{});
  if(seen.length===0){
    wrap.innerHTML='<div class="profile-line"><span class="det">avvio sessione Selenium e apertura ricerca avanzata…</span></div>';
    return;
  }
  wrap.innerHTML=seen.map(p=>'<div class="profile-line">'
    +'<span class="lbl">'+esc(p.profile)+'</span>'
    +profileBadge(p)
    +(p.saved?('<span class="ok">'+p.saved+' salvati</span>'):'<span></span>')
    +'</div>').join('');
}
async function pollStatus(){
  try{
    const r=await fetch('/api/sync/status');
    const s=await r.json();
    const txt=
      (s.status==='running'?'Sincronizzazione in corso…':
       s.status==='completed'?'Completato!':
       s.status==='stopped'?'Interrotto':
       s.status==='failed'?('Errore: '+(s.error||'sconosciuto')):s.status);
    document.getElementById('sync-txt').textContent=txt;
    renderProfileList(s);
    const pf=document.getElementById('pf');
    const seen=Object.values(s.profile_stats||{}).length;
    if(['completed','failed','stopped','idle'].includes(s.status)){
      pf.classList.remove('indeterminate');
      pf.style.width=s.status==='completed'?'100%':'0%';
      clearInterval(pollTimer);pollTimer=null;
      setTimeout(async()=>{showSync(false);await loadData();},1000);
    }else{
      // Keep the bar shimmering during scrape; nudge width forward as profiles complete
      pf.classList.add('indeterminate');
    }
  }catch(e){console.error(e);}
}
async function startSync(){
  showSync(true);
  document.getElementById('sync-txt').textContent='Avvio…';
  const pf=document.getElementById('pf');
  pf.classList.add('indeterminate');pf.style.width='35%';
  renderProfileList({profile_stats:{}});
  await fetch('/api/sync',{method:'POST'});
  pollTimer=setInterval(pollStatus,1200);
}
async function stopSync(){
  await fetch('/api/sync/stop',{method:'POST'});
}
function exportReport(){
  const a=document.createElement('a');
  a.href='/api/export';a.download='';
  document.body.appendChild(a);a.click();document.body.removeChild(a);
}
document.getElementById('sync-btn').addEventListener('click',startSync);
document.getElementById('stop-btn').addEventListener('click',stopSync);
document.getElementById('exp-btn').addEventListener('click',exportReport);
loadData();
</script>
</body></html>
"""

# Build the actual templates with shared CSS / JS substituted in.
MAIN_TEMPLATE = (
    MAIN_TEMPLATE
    .replace("__SHARED_CSS__", _SHARED_CSS)
    .replace("__SHARED_RENDER_JS__", _SHARED_RENDER_JS)
)


_PORTABLE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="it">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Screener Obbligazionario __DATE__</title>
  <link rel="stylesheet" href="https://cdn.datatables.net/1.13.6/css/jquery.dataTables.min.css">
  <script src="https://code.jquery.com/jquery-3.7.0.min.js"></script>
  <script src="https://cdn.datatables.net/1.13.6/js/jquery.dataTables.min.js"></script>
  <script src="https://cdn.plot.ly/plotly-3.1.0.min.js"></script>
  <style>__SHARED_CSS__</style>
</head>
<body><div class="shell">
  <div style="background:#fff8e1;border:1px solid #f6c90e;border-radius:12px;padding:12px 18px;margin-bottom:18px;font-size:13px;color:#856404">
    &#128196; Report esportato il <strong>__GENERATED_AT__</strong> &mdash; dati indicativi.
  </div>
  <div class="hdr">
    <h1>Screener Obbligazionario</h1>
    <p>Dati al __GENERATED_AT__</p>
  </div>
  <div class="sum-grid">
    <div class="sum-card"><span class="lbl">Bonds</span><span class="val" id="c0">—</span><span class="sub" id="c0sub"></span></div>
    <div class="sum-card"><span class="lbl">Government</span><span class="val" id="c1">—</span></div>
    <div class="sum-card"><span class="lbl">Corporate</span><span class="val" id="c2">—</span></div>
    <div class="sum-card"><span class="lbl">Yield medio</span><span class="val" id="c3">—</span><span class="sub" id="c3sub"></span></div>
  </div>
  <div class="info">
    <strong>Methodology:</strong> Net Annualized Yield calcolato dopo tassazione (12.5% govt, 26% corporate).
    <div class="fml">Net Yield = [Coupon &times; (1−t) + (((100−Price)/Anni) &times; (1−t))] / Price</div>
  </div>
  <div class="sec">
    <h2>Curva dei rendimenti</h2>
    <div class="chart-box"><div id="yc"></div></div>
  </div>
  <div class="sec">
    <h2>Yield per nazione</h2>
    <div class="chart-box nation"><div id="yc-nation"></div></div>
  </div>
  <div class="sec">
    <h2>Bond Dashboard Table</h2>
    <div class="tbl-wrap">
      <table id="bondTable" class="display compact stripe hover">
        <thead><tr><th>ISIN</th><th>Name</th><th>Geo</th><th>Currency</th><th>Issuer</th><th>Duration</th><th>Maturity</th><th>Price</th><th>Coupon</th><th>Net Yield</th><th>Categoria</th></tr></thead>
        <tbody id="tb"></tbody>
      </table>
    </div>
  </div>
  <div class="sec">
    <h2>Anomalie BTP EUR</h2>
    <div class="tbl-wrap">
      <table id="anomTable" class="display compact stripe hover">
        <thead><tr><th>ISIN</th><th>Name</th><th>Maturity</th><th>Years</th><th>Net Yield</th><th>Peer Avg</th><th>Spread</th></tr></thead>
        <tbody id="anom-tb"></tbody>
      </table>
    </div>
  </div>
  <div class="ftr">Report portabile &mdash; dati indicativi.</div>
</div>
<script>__SHARED_RENDER_JS__</script>
<script>
const PAYLOAD=__PAYLOAD_JSON__;
updCards(PAYLOAD);
renderChart(PAYLOAD.bonds||[]);
renderNationChart(PAYLOAD.nations||[]);
renderTable(PAYLOAD.bonds||[]);
renderAnomalies(PAYLOAD.anomalies||[]);
</script>
</body></html>
"""

_PORTABLE_TEMPLATE = (
    _PORTABLE_TEMPLATE
    .replace("__SHARED_CSS__", _SHARED_CSS)
    .replace("__SHARED_RENDER_JS__", _SHARED_RENDER_JS)
)


def _build_portable_html(
    *,
    bonds: List[dict],
    anomalies: List[dict],
    nations: List[dict],
    avg_yield: Optional[float],
    generated_at: str,
) -> str:
    payload = {
        "generated_at": generated_at,
        "bonds": bonds,
        "anomalies": anomalies,
        "nations": nations,
        "average_yield": avg_yield,
    }
    payload_json = json.dumps(payload, ensure_ascii=False, default=str)
    payload_json = payload_json.replace("</script>", "<\\/script>")
    return (
        _PORTABLE_TEMPLATE
        .replace("__PAYLOAD_JSON__", payload_json)
        .replace("__GENERATED_AT__", generated_at)
        .replace("__DATE__", generated_at[:10])
    )


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    host = "127.0.0.1"
    # Avoid Chrome's unsafe-ports list (5060 = SIP, blocked as ERR_UNSAFE_PORT).
    port = 5070
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    log.info("Starting Bonds Screener on http://%s:%d", host, port)
    app.run(host=host, port=port, debug=False)
