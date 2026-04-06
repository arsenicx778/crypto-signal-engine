#!/usr/bin/env python3.11
"""Crypto Signal Engine Dashboard — Robinhood-style real-time monitoring."""

import json
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse
from datetime import datetime
import requests
from signal_store import read_latest_signals

PORT = 8765
SIGNALS_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "signals.csv")
STARTING_CAPITAL = 1000.0
RISK_PER_TRADE = 20.0
REWARD_PER_TRADE = 40.0
KRAKEN_BASE = "https://api.kraken.com/0/public"


def fetch_market_ticker():
    try:
        r = requests.get(
            f"{KRAKEN_BASE}/Ticker",
            params={"pair": "ETHUSD"},
            timeout=5,
        )
        r.raise_for_status()
        data = r.json()
        result = next(iter(data["result"].values()))
        last_price = float(result["c"][0])
        open_price = float(result["o"])
        change = last_price - open_price
        pct = (change / open_price * 100) if open_price else 0.0
        return {"price": last_price, "change": change, "pct": pct}
    except Exception:
        return None


def fetch_market_candles(period):
    period_map = {
        "1H": {"interval": 1, "limit": 60},
        "4H": {"interval": 5, "limit": 48},
        "1D": {"interval": 15, "limit": 96},
        "1W": {"interval": 60, "limit": 168},
    }
    config = period_map.get(period, period_map["4H"])
    try:
        r = requests.get(
            f"{KRAKEN_BASE}/OHLC",
            params={"pair": "ETHUSD", "interval": config["interval"]},
            timeout=6,
        )
        r.raise_for_status()
        data = r.json()
        pair_key = next(k for k in data["result"].keys() if k != "last")
        rows = data["result"][pair_key][-config["limit"]:]
        return [
            {
                "t": row[0] * 1000,
                "o": float(row[1]),
                "h": float(row[2]),
                "lo": float(row[3]),
                "c": float(row[4]),
            }
            for row in rows
        ]
    except Exception:
        return []


def read_signals():
    try:
        return read_latest_signals()
    except FileNotFoundError:
        return []


def compute_stats(signals):
    buy_signals = [s for s in signals if s.get("signal") == "Buy"]
    wins        = [s for s in buy_signals if s.get("outcome") == "W"]
    losses      = [s for s in buy_signals if s.get("outcome") == "L"]
    pending     = [s for s in buy_signals if s.get("outcome") == "pending"]

    total_wins   = len(wins)
    total_losses = len(losses)
    completed    = total_wins + total_losses
    win_rate     = (total_wins / completed * 100) if completed > 0 else 0
    capital      = STARTING_CAPITAL + total_wins * REWARD_PER_TRADE - total_losses * RISK_PER_TRADE

    loss_streak = 0
    for s in reversed(buy_signals):
        if s.get("outcome") == "L":
            loss_streak += 1
        elif s.get("outcome") == "W":
            break

    return {
        "capital": capital,
        "win_rate": win_rate,
        "wins": total_wins,
        "losses": total_losses,
        "pending_buys": len(pending),
        "total_signals": len(signals),
        "total_completed": completed,
        "loss_streak": loss_streak,
        "open_trade": pending[-1] if pending else None,
    }


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ETH · Signal Engine</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#000;--s1:#111;--s2:#1c1c1e;--sep:rgba(255,255,255,.08);
  --t1:#fff;--t2:#8e8e93;--t3:#48484a;
  --green:#00c805;--red:#ff3b30;--amber:#ff9f0a;
  --mono:'SF Mono','Courier New',monospace;
}
html,body{background:var(--bg);color:var(--t1);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:15px;-webkit-font-smoothing:antialiased;min-height:100vh}
a{color:inherit;text-decoration:none}

/* ── Header ── */
.hdr{position:sticky;top:0;z-index:50;background:rgba(0,0,0,.85);backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);border-bottom:1px solid var(--sep);padding:14px 20px;display:flex;align-items:center;justify-content:space-between}
.hdr-l{display:flex;align-items:center;gap:9px}
.btc-icon{width:28px;height:28px;background:linear-gradient(135deg,#f7931a,#ffba54);border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:700;color:#000;flex-shrink:0}
.hdr-sym{font-size:15px;font-weight:600;letter-spacing:.01em}
.hdr-sub{font-size:12px;color:var(--t2);margin-top:1px}
.hdr-r{display:flex;align-items:center;gap:16px}
.portfolio-pill{background:var(--s2);border-radius:20px;padding:5px 12px;font-size:13px;font-weight:500}
.live-dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--green);animation:pulse 2s ease-in-out infinite;margin-right:5px;vertical-align:middle}
.hdr-meta{font-size:12px;color:var(--t2)}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.25}}

/* ── Page ── */
.page{max-width:900px;margin:0 auto;padding:0 0 60px}

/* ── Price Hero ── */
.price-hero{padding:28px 20px 6px}
.coin-name{font-size:13px;color:var(--t2);font-weight:500;margin-bottom:6px;letter-spacing:.02em}
.price-main{font-size:46px;font-weight:600;letter-spacing:-.02em;line-height:1;margin-bottom:6px;font-variant-numeric:tabular-nums}
.price-change{font-size:16px;font-weight:500;margin-bottom:2px}
.price-period{font-size:13px;color:var(--t2);margin-top:2px}

/* ── Chart ── */
.chart-wrap{position:relative;padding:8px 0 0;user-select:none}
#price-svg{display:block;width:100%;overflow:visible;cursor:crosshair}
.chart-tooltip{position:absolute;top:8px;left:50%;transform:translateX(-50%);background:var(--s2);border:1px solid var(--sep);border-radius:8px;padding:4px 10px;font-size:12px;font-weight:600;font-family:var(--mono);pointer-events:none;white-space:nowrap;opacity:0;transition:opacity .1s}
.chart-tooltip.show{opacity:1}

/* Period pills */
.periods{display:flex;gap:4px;padding:14px 20px 0;border-bottom:1px solid var(--sep)}
.period-btn{padding:6px 14px;border-radius:20px;font-size:13px;font-weight:500;cursor:pointer;border:none;background:transparent;color:var(--t2);transition:all .15s}
.period-btn:hover{color:var(--t1)}
.period-btn.active{background:var(--s2);color:var(--t1)}

/* ── Position Card ── */
.position-card{margin:0;padding:20px;border-bottom:1px solid var(--sep)}
.pos-tag{font-size:11px;text-transform:uppercase;letter-spacing:.1em;color:var(--green);font-weight:600;margin-bottom:12px}
.pos-row{display:grid;grid-template-columns:1fr 1fr 1fr;gap:0;margin-bottom:16px}
.pos-col{padding:0 0 0 0}
.pos-col+.pos-col{border-left:1px solid var(--sep);padding-left:16px}
.pos-label{font-size:11px;color:var(--t2);text-transform:uppercase;letter-spacing:.06em;margin-bottom:4px}
.pos-val{font-size:22px;font-weight:600;font-variant-numeric:tabular-nums;letter-spacing:-.01em}
.pos-hint{font-size:11px;color:var(--t2);font-family:var(--mono);margin-top:2px}

/* Position progress bar */
.pos-bar-wrap{margin-top:4px}
.pos-bar-labels{display:flex;justify-content:space-between;font-size:11px;color:var(--t2);font-family:var(--mono);margin-bottom:5px}
.pos-bar-track{height:4px;background:var(--s2);border-radius:2px;position:relative}
.pos-bar-fill{position:absolute;left:0;top:0;height:100%;border-radius:2px;background:linear-gradient(90deg,var(--red),var(--amber) 50%,var(--green));transition:width .5s}
.pos-bar-thumb{position:absolute;top:50%;transform:translate(-50%,-50%);width:12px;height:12px;border-radius:50%;background:var(--t1);border:2px solid var(--t1);box-shadow:0 0 0 3px rgba(255,255,255,.15);transition:left .5s}
.pos-conf{display:inline-block;margin-top:10px;font-size:12px;color:var(--t2)}
.pos-conf span{color:var(--green);font-weight:600}

/* ── Stats Strip ── */
.stats-strip{display:grid;grid-template-columns:repeat(4,1fr);padding:0;border-bottom:1px solid var(--sep)}
.stat{padding:18px 20px;border-right:1px solid var(--sep)}
.stat:last-child{border-right:none}
.stat-label{font-size:11px;color:var(--t2);text-transform:uppercase;letter-spacing:.07em;margin-bottom:5px}
.stat-val{font-size:22px;font-weight:600;font-variant-numeric:tabular-nums;letter-spacing:-.01em}
.stat-sub{font-size:11px;color:var(--t2);font-family:var(--mono);margin-top:3px}

/* ── Alerts ── */
.alerts-wrap{padding:0 20px}
.alert{padding:12px 14px;border-radius:10px;font-size:13px;margin:12px 0 0;display:flex;align-items:flex-start;gap:8px;line-height:1.4}
.alert-warn{background:rgba(255,159,10,.08);border:1px solid rgba(255,159,10,.2);color:var(--amber)}
.alert-danger{background:rgba(255,59,48,.08);border:1px solid rgba(255,59,48,.2);color:var(--red)}

/* ── Signal Feed ── */
.feed-header{padding:20px 20px 10px;display:flex;align-items:center;justify-content:space-between}
.feed-title{font-size:17px;font-weight:600}
.feed-tabs{display:flex;gap:0;background:var(--s2);border-radius:8px;padding:2px}
.feed-tab{padding:4px 10px;border-radius:6px;font-size:12px;font-weight:500;cursor:pointer;border:none;background:transparent;color:var(--t2);transition:all .15s}
.feed-tab.active{background:var(--s1);color:var(--t1)}

.feed-item{display:flex;align-items:center;padding:13px 20px;border-bottom:1px solid var(--sep);cursor:pointer;transition:background .1s;gap:12px}
.feed-item:hover{background:rgba(255,255,255,.03)}
.feed-icon{width:36px;height:36px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:14px;flex-shrink:0}
.icon-buy{background:rgba(0,200,5,.12);color:var(--green)}
.icon-dne{background:rgba(255,59,48,.1);color:var(--red)}
.icon-hold{background:rgba(142,142,147,.1);color:var(--t2)}
.feed-main{flex:1;min-width:0}
.feed-sig{font-size:14px;font-weight:500}
.feed-time{font-size:12px;color:var(--t2);margin-top:1px}
.feed-right{text-align:right;flex-shrink:0}
.feed-price{font-size:14px;font-weight:500;font-family:var(--mono);font-variant-numeric:tabular-nums}
.feed-outcome{font-size:12px;margin-top:2px}
.conf-badge{display:inline-block;font-size:11px;font-family:var(--mono);background:rgba(142,142,147,.12);color:var(--t2);border-radius:4px;padding:1px 5px;margin-left:5px;vertical-align:middle}

.outcome-w{color:var(--green)}
.outcome-l{color:var(--red)}
.outcome-open{color:var(--amber)}
.outcome-pend{color:var(--t2)}

.show-more{text-align:center;padding:16px;font-size:13px;color:var(--t2);cursor:pointer;transition:color .15s}
.show-more:hover{color:var(--t1)}

/* ── Modal ── */
.mo{display:none;position:fixed;inset:0;background:rgba(0,0,0,.8);z-index:200;backdrop-filter:blur(12px);padding:20px;overflow-y:auto;align-items:flex-start;justify-content:center}
.mo.open{display:flex}
.mc{background:#1c1c1e;border:1px solid rgba(255,255,255,.1);border-radius:18px;width:100%;max-width:600px;margin:auto;overflow:hidden;animation:mup .18s ease}
@keyframes mup{from{opacity:0;transform:translateY(16px)}to{opacity:1;transform:translateY(0)}}
.mc-hdr{padding:18px 20px 14px;border-bottom:1px solid var(--sep);display:flex;align-items:center;justify-content:space-between}
.mc-title{font-size:16px;font-weight:600}
.mc-x{width:28px;height:28px;border-radius:50%;background:var(--s2);border:none;color:var(--t2);cursor:pointer;font-size:17px;display:flex;align-items:center;justify-content:center;transition:all .15s}
.mc-x:hover{background:var(--red);color:#fff}
.mc-body{padding:18px 20px}
.mc-kv{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:14px}
.mc-field{background:rgba(255,255,255,.04);border-radius:10px;padding:11px 13px}
.mc-field.full{grid-column:1/-1}
.mfl{font-size:10px;text-transform:uppercase;letter-spacing:.08em;color:var(--t2);margin-bottom:3px}
.mfv{font-size:13px;font-family:var(--mono);word-break:break-word;line-height:1.4}
.mfv.prose{font-family:inherit;font-size:12px;line-height:1.55;color:var(--t1);white-space:pre-wrap}

/* ── Loading ── */
.loading{text-align:center;padding:80px 20px;color:var(--t2)}
.spin{display:inline-block;width:20px;height:20px;border:2px solid var(--sep);border-top-color:var(--green);border-radius:50%;animation:rot .7s linear infinite;margin-bottom:12px}
@keyframes rot{to{transform:rotate(360deg)}}

/* ── Scrollbar ── */
::-webkit-scrollbar{width:4px;height:4px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:var(--t3);border-radius:2px}

@media(max-width:600px){
  .price-main{font-size:34px}
  .stats-strip{grid-template-columns:1fr 1fr}
  .stat:nth-child(2){border-right:none}
  .stat:nth-child(3){border-top:1px solid var(--sep)}
  .pos-row{grid-template-columns:1fr}
  .pos-col+.pos-col{border-left:none;padding-left:0;border-top:1px solid var(--sep);padding-top:12px;margin-top:12px}
  .mc-kv{grid-template-columns:1fr}
}
</style>
</head>
<body>

<header class="hdr">
  <div class="hdr-l">
    <div class="btc-icon">&#x25C7;</div>
    <div>
      <div class="hdr-sym">ETH &middot; USD</div>
      <div class="hdr-sub">Ethereum</div>
    </div>
  </div>
  <div class="hdr-r">
    <div class="portfolio-pill" id="hdr-portfolio">$1,000.00</div>
    <div class="hdr-meta"><span class="live-dot"></span><span id="hdr-countdown">60s</span></div>
  </div>
</header>

<div class="page">
  <div id="loading" class="loading"><div class="spin"></div><br>Loading&hellip;</div>
  <div id="app" style="display:none">

    <!-- Price Hero -->
    <div class="price-hero">
      <div class="coin-name">Ethereum</div>
      <div class="price-main" id="hero-price">&#8212;</div>
      <div class="price-change" id="hero-change">&nbsp;</div>
      <div class="price-period" id="hero-period">Loading price&hellip;</div>
    </div>

    <!-- Chart -->
    <div class="chart-wrap" id="chart-wrap">
      <svg id="price-svg" height="200"></svg>
      <div class="chart-tooltip" id="chart-tip"></div>
    </div>

    <!-- Period Selector -->
    <div class="periods" id="periods">
      <button class="period-btn" data-p="1H">1H</button>
      <button class="period-btn active" data-p="4H">4H</button>
      <button class="period-btn" data-p="1D">1D</button>
      <button class="period-btn" data-p="1W">1W</button>
    </div>

    <!-- Open Position -->
    <div class="position-card" id="position-card" style="display:none"></div>

    <!-- Stats Strip -->
    <div class="stats-strip" id="stats-strip"></div>

    <!-- Alerts -->
    <div class="alerts-wrap" id="alerts-wrap"></div>

    <!-- Signal Feed -->
    <div class="feed-header">
      <div class="feed-title">Signals</div>
      <div class="feed-tabs" id="feed-tabs"></div>
    </div>
    <div id="feed-list"></div>

  </div><!-- /app -->
</div><!-- /page -->

<!-- Modal -->
<div class="mo" id="mo" onclick="if(event.target===this)closeMo()">
  <div class="mc">
    <div class="mc-hdr">
      <div class="mc-title" id="mo-title">Signal Detail</div>
      <button class="mc-x" onclick="closeMo()">&#215;</button>
    </div>
    <div class="mc-body" id="mo-body"></div>
  </div>
</div>

<script>
// ── Config ──────────────────────────────────────────────────────────────────
const REFRESH = 60;
const PERIOD_MAP = {
  '1H': {interval:'1m',  limit:60},
  '4H': {interval:'5m',  limit:48},
  '1D': {interval:'15m', limit:96},
  '1W': {interval:'1h',  limit:168},
};
const FEED_TABS = [
  {k:'all',l:'All'},{k:'buy',l:'Buy'},{k:'win',l:'Win'},
  {k:'loss',l:'Loss'},{k:'pending',l:'Pending'},{k:'dne',l:'Skip'},
];

// ── State ────────────────────────────────────────────────────────────────────
let signals = [], openTrade = null, stats = {};
let feedFilter = 'all', period = '4H';
let livePrice = 0, periodOpen = 0;
let countdown = REFRESH, cdTimer;
let feedLimit = 30;
let chartCandles = [];

// ── Utils ────────────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);
function esc(s){
  if(s==null)return'';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function fmtUSD(v,d=2){
  const n=parseFloat(v);
  if(isNaN(n)||v===''||v==null)return'—';
  return '$'+n.toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d});
}
function fmtNum(v,d=2){
  const n=parseFloat(v);
  return isNaN(n)?'—':n.toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d});
}
function fmtTime(ts){
  if(!ts)return'—';
  try{
    const d=new Date(ts.replace(' ','T'));
    const now=new Date();
    const sameDay=d.toDateString()===now.toDateString();
    if(sameDay) return d.toLocaleTimeString('en-US',{hour:'2-digit',minute:'2-digit',hour12:true});
    return d.toLocaleDateString('en-US',{month:'short',day:'numeric'})+' '+
           d.toLocaleTimeString('en-US',{hour:'2-digit',minute:'2-digit',hour12:true});
  }catch(e){return esc(ts);}
}

// ── Market API (served locally by dashboard backend) ────────────────────────
async function fetchTicker(){
  try{
    const r=await fetch('/api/market?ticker=1',{signal:AbortSignal.timeout(5000)});
    const d=await r.json();
    if(!r.ok||!d.ok)return null;
    return d.ticker;
  }catch(e){return null;}
}

async function fetchCandles(p){
  try{
    const r=await fetch(`/api/market?period=${encodeURIComponent(p)}`,{signal:AbortSignal.timeout(6000)});
    const d=await r.json();
    if(!r.ok||!d.ok)return[];
    return d.candles||[];
  }catch(e){return[];}
}

// ── Chart Drawing ─────────────────────────────────────────────────────────────
function smoothPath(pts){
  if(pts.length<2)return'';
  let d=`M${pts[0][0]},${pts[0][1]}`;
  for(let i=0;i<pts.length-1;i++){
    const p0=pts[Math.max(0,i-1)],p1=pts[i],p2=pts[i+1],p3=pts[Math.min(pts.length-1,i+2)];
    const t=0.25;
    const cp1x=p1[0]+(p2[0]-p0[0])*t, cp1y=p1[1]+(p2[1]-p0[1])*t;
    const cp2x=p2[0]-(p3[0]-p1[0])*t, cp2y=p2[1]-(p3[1]-p1[1])*t;
    d+=` C${cp1x},${cp1y} ${cp2x},${cp2y} ${p2[0]},${p2[1]}`;
  }
  return d;
}

function drawChart(candles, trade){
  const svg=$('price-svg');
  const W=svg.getBoundingClientRect().width||800;
  const H=200, PAD=16;

  if(!candles.length){
    svg.innerHTML='<text x="50%" y="50%" text-anchor="middle" fill="#8e8e93" font-size="13">No chart data</text>';
    return;
  }

  const closes=candles.map(c=>c.c);
  if(livePrice>0) closes[closes.length-1]=livePrice;

  const rawMin=Math.min(...closes), rawMax=Math.max(...closes);
  // Include trade levels in y range if present
  let yMin=rawMin, yMax=rawMax;
  if(trade){
    const sl=parseFloat(trade.stop_loss)||0, tp=parseFloat(trade.take_profit)||0;
    if(sl>0) yMin=Math.min(yMin,sl);
    if(tp>0) yMax=Math.max(yMax,tp);
  }
  const margin=(yMax-yMin)*0.08||1;
  yMin-=margin; yMax+=margin;

  const xS=i=>(i/(candles.length-1))*(W-PAD*2)+PAD;
  const yS=v=>H-((v-yMin)/(yMax-yMin))*(H-PAD*2)-PAD;

  const pts=closes.map((v,i)=>[xS(i),yS(v)]);
  const linePath=smoothPath(pts);
  const isUp=closes[closes.length-1]>=closes[0];
  const col=isUp?'#00c805':'#ff3b30';
  const gradId='cg'+Date.now();

  // Area path: line + down to bottom + back
  const areaPath=linePath+` L${xS(candles.length-1)},${H} L${xS(0)},${H} Z`;

  // Build SVG
  let s=`<defs>
    <linearGradient id="${gradId}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="${col}" stop-opacity=".18"/>
      <stop offset="100%" stop-color="${col}" stop-opacity="0"/>
    </linearGradient>
    <clipPath id="cp"><rect x="0" y="0" width="${W}" height="${H}"/></clipPath>
  </defs>
  <rect width="${W}" height="${H}" fill="transparent"/>`;

  // Area fill
  s+=`<path d="${areaPath}" fill="url(#${gradId})" clip-path="url(#cp)"/>`;

  // Subtle open-price line (start of period)
  const openY=yS(closes[0]);
  s+=`<line x1="${PAD}" y1="${openY}" x2="${W-PAD}" y2="${openY}" stroke="rgba(255,255,255,.1)" stroke-width="1" stroke-dasharray="4,4"/>`;

  // Trade level lines
  if(trade){
    const sl=parseFloat(trade.stop_loss)||0;
    const ep=parseFloat(trade.entry_price)||0;
    const tp=parseFloat(trade.take_profit)||0;
    if(sl>0&&sl>yMin&&sl<yMax){
      const y=yS(sl);
      s+=`<line x1="${PAD}" y1="${y}" x2="${W-PAD}" y2="${y}" stroke="#ff3b30" stroke-width="1.5" stroke-dasharray="6,4" opacity=".7"/>`;
      s+=`<text x="${W-PAD+4}" y="${y+4}" fill="#ff3b30" font-size="10" font-family="'SF Mono','Courier New',monospace" opacity=".8">SL</text>`;
    }
    if(ep>0&&ep>yMin&&ep<yMax){
      const y=yS(ep);
      s+=`<line x1="${PAD}" y1="${y}" x2="${W-PAD}" y2="${y}" stroke="rgba(255,255,255,.5)" stroke-width="1.5" stroke-dasharray="6,4"/>`;
      s+=`<text x="${W-PAD+4}" y="${y+4}" fill="rgba(255,255,255,.6)" font-size="10" font-family="'SF Mono','Courier New',monospace">IN</text>`;
    }
    if(tp>0&&tp>yMin&&tp<yMax){
      const y=yS(tp);
      s+=`<line x1="${PAD}" y1="${y}" x2="${W-PAD}" y2="${y}" stroke="#00c805" stroke-width="1.5" stroke-dasharray="6,4" opacity=".7"/>`;
      s+=`<text x="${W-PAD+4}" y="${y+4}" fill="#00c805" font-size="10" font-family="'SF Mono','Courier New',monospace" opacity=".8">TP</text>`;
    }
  }

  // Main line
  s+=`<path d="${linePath}" fill="none" stroke="${col}" stroke-width="2" clip-path="url(#cp)"/>`;

  // End dot (current price)
  const lastX=xS(candles.length-1), lastY=yS(closes[closes.length-1]);
  s+=`<circle cx="${lastX}" cy="${lastY}" r="4" fill="${col}"/>`;
  s+=`<circle cx="${lastX}" cy="${lastY}" r="8" fill="${col}" opacity=".2"/>`;

  // Hover crosshair (invisible rect + line)
  s+=`<line id="xhair" x1="-100" y1="${PAD}" x2="-100" y2="${H-PAD}" stroke="rgba(255,255,255,.25)" stroke-width="1" pointer-events="none"/>`;
  s+=`<circle id="xdot" cx="-100" cy="-100" r="4" fill="${col}" pointer-events="none"/>`;
  s+=`<rect id="xrect" x="0" y="0" width="${W}" height="${H}" fill="transparent"/>`;

  svg.setAttribute('viewBox',`0 0 ${W} ${H}`);
  svg.innerHTML=s;

  // Hover interaction
  const rect=svg.getElementById('xrect');
  const xhair=svg.getElementById('xhair');
  const xdot=svg.getElementById('xdot');
  const tip=$('chart-tip');

  function onMove(e){
    const bnd=svg.getBoundingClientRect();
    const mx=(e.clientX||e.touches[0].clientX)-bnd.left;
    const ci=Math.min(candles.length-1,Math.max(0,Math.round((mx-PAD)/(W-PAD*2)*(candles.length-1))));
    const px=xS(ci);
    const py=yS(closes[ci]);
    xhair.setAttribute('x1',px);xhair.setAttribute('x2',px);
    xdot.setAttribute('cx',px);xdot.setAttribute('cy',py);
    tip.textContent='$'+closes[ci].toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
    tip.className='chart-tooltip show';
    // Update hero price to hover value
    $('hero-price').textContent='$'+closes[ci].toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
  }
  function onLeave(){
    xhair.setAttribute('x1','-100');xhair.setAttribute('x2','-100');
    xdot.setAttribute('cx','-100');
    tip.className='chart-tooltip';
    // Restore live price
    if(livePrice>0) $('hero-price').textContent='$'+livePrice.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
  }
  rect.addEventListener('mousemove',onMove);
  rect.addEventListener('mouseleave',onLeave);
  rect.addEventListener('touchmove',e=>{e.preventDefault();onMove(e);},{passive:false});
  rect.addEventListener('touchend',onLeave);
}

// ── Render: Price Hero ────────────────────────────────────────────────────────
function renderHero(ticker){
  if(!ticker)return;
  livePrice=ticker.price;
  const isUp=ticker.pct>=0;
  const col=isUp?'#00c805':'#ff3b30';
  const sign=isUp?'+':'';
  $('hero-price').textContent='$'+ticker.price.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
  $('hero-change').innerHTML=`<span style="color:${col}">${sign}$${Math.abs(ticker.change).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2})} (${sign}${ticker.pct.toFixed(2)}%)</span>`;
  $('hero-period').textContent='Past 24 hours';
}

// ── Render: Position Card ─────────────────────────────────────────────────────
function renderPosition(trade){
  const el=$('position-card');
  if(!trade){el.style.display='none';return;}
  el.style.display='block';

  const entry=parseFloat(trade.entry_price)||0;
  const sl=parseFloat(trade.stop_loss)||0;
  const tp=parseFloat(trade.take_profit)||0;
  const range=tp-sl;
  let prog=50;
  if(range>0&&entry>0) prog=Math.max(2,Math.min(98,((entry-sl)/range)*100));

  // If live price available, show unrealized P&L
  let pnlHtml='';
  if(livePrice>0&&entry>0){
    const diff=livePrice-entry;
    const pnlUSD=(diff/entry)*20*(entry>0?1:0); // rough estimate based on $20 risk
    const pnlCol=diff>=0?'#00c805':'#ff3b30';
    const ps=diff>=0?'+':'';
    // live progress
    if(range>0) prog=Math.max(2,Math.min(98,((livePrice-sl)/range)*100));
    pnlHtml=`<div style="margin-top:10px;font-size:13px">Current price: <span style="font-family:var(--mono);color:${pnlCol}">${fmtUSD(livePrice)} &nbsp;(${ps}${fmtUSD(diff,2)})</span></div>`;
  }

  el.innerHTML=`
    <div class="pos-tag">&#9679; Open Position &mdash; Buy</div>
    <div class="pos-row">
      <div class="pos-col">
        <div class="pos-label">Entry</div>
        <div class="pos-val">${fmtUSD(trade.entry_price)}</div>
        <div class="pos-hint">Risk $20.00</div>
      </div>
      <div class="pos-col">
        <div class="pos-label">Stop Loss</div>
        <div class="pos-val" style="color:var(--red)">${fmtUSD(trade.stop_loss)}</div>
        <div class="pos-hint">&minus;$20.00</div>
      </div>
      <div class="pos-col">
        <div class="pos-label">Take Profit</div>
        <div class="pos-val" style="color:var(--green)">${fmtUSD(trade.take_profit)}</div>
        <div class="pos-hint">+$20.00</div>
      </div>
    </div>
    <div class="pos-bar-wrap">
      <div class="pos-bar-labels">
        <span>SL ${fmtUSD(trade.stop_loss)}</span>
        <span>TP ${fmtUSD(trade.take_profit)}</span>
      </div>
      <div class="pos-bar-track">
        <div class="pos-bar-fill" style="width:100%"></div>
        <div class="pos-bar-thumb" style="left:${prog}%"></div>
      </div>
    </div>
    <div class="pos-conf">Confidence: <span>${trade.confidence||0}%</span> &nbsp;&middot;&nbsp; ${fmtTime(trade.timestamp)}</div>
    ${pnlHtml}
  `;
}

// ── Render: Stats Strip ───────────────────────────────────────────────────────
function renderStats(st){
  const pnl=st.capital-1000;
  const pnlSign=pnl>=0?'+':'';
  const pnlCol=pnl>0?'#00c805':pnl<0?'#ff3b30':'#8e8e93';
  const wrc=st.total_completed===0?'#8e8e93':st.win_rate>=55?'#00c805':st.win_rate>=45?'#ff9f0a':'#ff3b30';

  $('stats-strip').innerHTML=`
    <div class="stat">
      <div class="stat-label">Portfolio</div>
      <div class="stat-val" style="color:${pnlCol}">${fmtUSD(st.capital)}</div>
      <div class="stat-sub" style="color:${pnlCol}">${pnlSign}${fmtUSD(Math.abs(pnl))}</div>
    </div>
    <div class="stat">
      <div class="stat-label">Win Rate</div>
      <div class="stat-val" style="color:${wrc}">${st.total_completed>0?fmtNum(st.win_rate,1)+'%':'—'}</div>
      <div class="stat-sub">${st.total_completed} trades</div>
    </div>
    <div class="stat">
      <div class="stat-label">Wins</div>
      <div class="stat-val" style="color:${st.wins>0?'#00c805':'#8e8e93'}">${st.wins}</div>
      <div class="stat-sub">+${fmtUSD(st.wins*20)}</div>
    </div>
    <div class="stat">
      <div class="stat-label">Losses</div>
      <div class="stat-val" style="color:${st.losses>0?'#ff3b30':'#8e8e93'}">${st.losses}</div>
      <div class="stat-sub">&minus;${fmtUSD(st.losses*20)}</div>
    </div>
  `;
  $('hdr-portfolio').textContent=fmtUSD(st.capital);
}

// ── Render: Alerts ────────────────────────────────────────────────────────────
function renderAlerts(st){
  const w=$('alerts-wrap');
  const a=[];
  if(st.loss_streak>=3)
    a.push(`<div class="alert alert-danger">&#9888;&#65039; ${st.loss_streak} consecutive losses — consider pausing</div>`);
  if(st.total_completed>=10&&st.win_rate<45)
    a.push(`<div class="alert alert-danger">Win rate ${fmtNum(st.win_rate,1)}% is below 45% — strategy needs review</div>`);
  else if(st.total_completed>=10&&st.win_rate<55)
    a.push(`<div class="alert alert-warn">Win rate ${fmtNum(st.win_rate,1)}% is in the caution zone (45–55%)</div>`);
  if(st.pending_buys>1)
    a.push(`<div class="alert alert-warn">${st.pending_buys} open Buy positions — single-trade gate may be off</div>`);
  w.innerHTML=a.join('');
}

// ── Render: Feed ──────────────────────────────────────────────────────────────
function applyFeedFilter(sigs,f){
  switch(f){
    case'buy':    return sigs.filter(s=>s.signal==='Buy');
    case'win':    return sigs.filter(s=>s.outcome==='W');
    case'loss':   return sigs.filter(s=>s.outcome==='L');
    case'pending':return sigs.filter(s=>s.signal==='Buy'&&s.outcome==='pending');
    case'dne':    return sigs.filter(s=>s.signal==='Do Not Enter');
    default:      return sigs.filter(s=>s.signal!=='Hold'); // 'all' excludes noise
  }
}

function renderFeedTabs(){
  $('feed-tabs').innerHTML=FEED_TABS.map(t=>
    `<button class="feed-tab ${feedFilter===t.k?'active':''}" onclick="setFeedFilter('${t.k}')">${t.l}</button>`
  ).join('');
}

function setFeedFilter(k){feedFilter=k;feedLimit=30;renderFeedTabs();renderFeed();}

function renderFeed(){
  const rows=[...applyFeedFilter(signals,feedFilter)].reverse();
  const slice=rows.slice(0,feedLimit);
  const el=$('feed-list');

  if(!slice.length){
    el.innerHTML='<div style="text-align:center;padding:32px;color:var(--t2);font-size:13px">No signals</div>';
    return;
  }

  let html=slice.map((s,i)=>{
    const isBuy=s.signal==='Buy', isDNE=s.signal==='Do Not Enter';
    const iconCls=isBuy?'icon-buy':isDNE?'icon-dne':'icon-hold';
    const icon=isBuy?'&#8679;':isDNE?'&#8678;':'&middot;';
    const sigLabel=isBuy?'Buy':isDNE?'Do Not Enter':'Hold';
    const conf=parseInt(s.confidence)||0;
    const confHtml=conf>0?`<span class="conf-badge">${conf}%</span>`:'';

    let outcomeHtml='', priceHtml='';
    if(isBuy){
      if(s.outcome==='W') outcomeHtml='<div class="feed-outcome outcome-w">Win +$40</div>';
      else if(s.outcome==='L') outcomeHtml='<div class="feed-outcome outcome-l">Loss &minus;$20</div>';
      else outcomeHtml='<div class="feed-outcome outcome-open">Open</div>';
      priceHtml=s.entry_price?`<div class="feed-price">${fmtUSD(s.entry_price)}</div>`:'';
    }

    return`<div class="feed-item" onclick="openModal(${rows.length-1-i})">
      <div class="feed-icon ${iconCls}">${icon}</div>
      <div class="feed-main">
        <div class="feed-sig">${esc(sigLabel)}${confHtml}</div>
        <div class="feed-time">${fmtTime(s.timestamp)}</div>
      </div>
      <div class="feed-right">${priceHtml}${outcomeHtml}</div>
    </div>`;
  }).join('');

  if(rows.length>feedLimit){
    html+=`<div class="show-more" onclick="feedLimit+=30;renderFeed()">Show more (${rows.length-feedLimit} remaining)</div>`;
  }
  el.innerHTML=html;
}

// ── Modal ─────────────────────────────────────────────────────────────────────
function openModal(idx){
  const rows=[...applyFeedFilter(signals,feedFilter)].reverse();
  const s=rows[idx];
  if(!s)return;

  const conf=parseInt(s.confidence)||0;
  const sigLabel=s.signal==='Buy'?'Buy':s.signal==='Do Not Enter'?'Do Not Enter':'Hold';
  $('mo-title').textContent=sigLabel+(conf?` · ${conf}% confidence`:'');

  const kv=[
    ['Time',fmtTime(s.timestamp),true],
    ['Confidence',conf>0?conf+'%':'—',true],
    ['Entry',fmtUSD(s.entry_price),true],
    ['Stop Loss',fmtUSD(s.stop_loss),true],
    ['Take Profit',fmtUSD(s.take_profit),true],
    ['Outcome',s.outcome||'—',true],
    ['Close Price',fmtUSD(s.close_price),true],
    ['Close Time',s.close_time||'—',true],
  ];
  const prose=[
    ['Rationale',s.decision_rationale],
    ['Technical Analysis',s.ta_summary],
    ['Sentiment',s.sentiment_summary],
    ['History',s.history_summary],
    ['Indicators',s.indicators],
    ['Overrides',s.overrides],
  ];

  let html='<div class="mc-kv">';
  kv.forEach(([l,v,m])=>{
    html+=`<div class="mc-field"><div class="mfl">${l}</div><div class="mfv${m?' mono':''}">${esc(v)||'—'}</div></div>`;
  });
  html+='</div>';
  prose.forEach(([l,v])=>{
    if(v&&v!=='error'&&String(v).trim()){
      html+=`<div class="mc-field full" style="margin-bottom:8px">
        <div class="mfl">${l}</div>
        <div class="mfv prose">${esc(v)}</div>
      </div>`;
    }
  });
  $('mo-body').innerHTML=html;
  $('mo').classList.add('open');
}
function closeMo(){$('mo').classList.remove('open');}
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeMo();});

// ── Period selector ───────────────────────────────────────────────────────────
document.getElementById('periods').addEventListener('click',async e=>{
  const btn=e.target.closest('.period-btn');
  if(!btn)return;
  document.querySelectorAll('.period-btn').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  period=btn.dataset.p;
  chartCandles=await fetchCandles(period);
  drawChart(chartCandles,openTrade);
});

// ── Main Load ─────────────────────────────────────────────────────────────────
async function loadAll(){
  // Parallel: local signals + Binance ticker + candles
  const [sigRes, ticker, candles] = await Promise.all([
    fetch('/api/data').then(r=>r.json()).catch(()=>null),
    fetchTicker(),
    fetchCandles(period),
  ]);

  if(!sigRes){ $('loading').innerHTML='<div style="color:#ff3b30">Error loading signals</div>'; return; }

  signals = sigRes.signals || [];
  stats   = sigRes.stats   || {};
  openTrade = stats.open_trade || null;
  chartCandles = candles;

  if(ticker) livePrice = ticker.price;
  if(candles.length) periodOpen = candles[0].o;

  $('loading').style.display='none';
  $('app').style.display='block';

  renderHero(ticker);
  drawChart(candles, openTrade);
  renderPosition(openTrade);
  renderStats(stats);
  renderAlerts(stats);
  renderFeedTabs();
  renderFeed();
}

// Price-only refresh (every 10s)
async function refreshPrice(){
  const ticker=await fetchTicker();
  if(!ticker)return;
  livePrice=ticker.price;
  renderHero(ticker);
  if(openTrade) renderPosition(openTrade);
  // Redraw chart with updated last candle
  if(chartCandles.length){
    chartCandles[chartCandles.length-1].c=livePrice;
    drawChart(chartCandles,openTrade);
  }
}

// Countdown + full reload every 60s
function startCountdown(){
  countdown=REFRESH;
  clearInterval(cdTimer);
  cdTimer=setInterval(async ()=>{
    countdown--;
    $('hdr-countdown').textContent=countdown+'s';
    if(countdown%10===0) refreshPrice(); // partial refresh every 10s
    if(countdown<=0){ countdown=REFRESH; await loadAll(); }
  },1000);
}

// Redraw chart on resize
window.addEventListener('resize',()=>{ if(chartCandles.length) drawChart(chartCandles,openTrade); });

loadAll();
startCountdown();
</script>
</body>
</html>"""


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/":
            body = HTML_TEMPLATE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif parsed.path == "/api/data":
            sigs = read_signals()
            st   = compute_stats(sigs)
            payload = json.dumps(
                {"signals": sigs, "stats": st, "updated": datetime.now().isoformat()},
                default=str,
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        elif parsed.path == "/api/market":
            query = parse_qs(parsed.query)
            if query.get("ticker"):
                ticker = fetch_market_ticker()
                payload = json.dumps({"ok": bool(ticker), "ticker": ticker}).encode("utf-8")
            else:
                period = query.get("period", ["4H"])[0]
                candles = fetch_market_candles(period)
                payload = json.dumps({"ok": bool(candles), "candles": candles}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):
        pass


def _open_browser(url):
    import time
    time.sleep(0.8)
    webbrowser.open(url)


if __name__ == "__main__":
    url = f"http://localhost:{PORT}"
    if not os.path.exists(SIGNALS_CSV):
        print(f"Warning: {SIGNALS_CSV} not found.")
    server = HTTPServer(("localhost", PORT), DashboardHandler)
    threading.Thread(target=_open_browser, args=(url,), daemon=True).start()
    print(f"Dashboard: {url}")
    print(f"Signals:   {SIGNALS_CSV}")
    print("Ctrl+C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.shutdown()
