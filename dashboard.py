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

PORT               = 8765
COIN_CAPITAL_START = 1000.0
RISK_PERCENT   = 0.02
REWARD_PERCENT = 0.03
KRAKEN_BASE        = "https://api.kraken.com/0/public"
KRAKEN_PAIRS = {
    "ETH": "XETHZUSD", "SOL": "SOLUSD", "AVAX": "AVAXUSD", "XRP": "XXRPZUSD",
}

_BASE = os.path.dirname(os.path.abspath(__file__))
COIN_CSV_FILES = {
    "ETH":  os.path.join(_BASE, "eth_signals.csv"),
    "SOL":  os.path.join(_BASE, "sol_signals.csv"),
    "AVAX": os.path.join(_BASE, "avax_signals.csv"),
    "XRP":  os.path.join(_BASE, "xrp_signals.csv"),
}


def fetch_coin_price(coin):
    pair = KRAKEN_PAIRS.get(coin)
    if not pair:
        return None
    try:
        r = requests.get(
            f"{KRAKEN_BASE}/Ticker",
            params={"pair": pair},
            timeout=5,
        )
        r.raise_for_status()
        data = r.json()
        result = next(iter(data["result"].values()))
        return float(result["c"][0])
    except Exception:
        return None


def fetch_market_ticker(coin="ETH"):
    pair = KRAKEN_PAIRS.get(coin, KRAKEN_PAIRS["ETH"])
    try:
        r = requests.get(
            f"{KRAKEN_BASE}/Ticker",
            params={"pair": pair},
            timeout=5,
        )
        r.raise_for_status()
        data = r.json()
        result = next(iter(data["result"].values()))
        last_price = float(result["c"][0])
        open_price = float(result["o"])
        change = last_price - open_price
        pct = (change / open_price * 100) if open_price else 0.0
        return {"coin": coin, "price": last_price, "change": change, "pct": pct}
    except Exception:
        return None


def fetch_market_candles(period, coin="ETH"):
    period_map = {
        "1H": {"interval": 1, "limit": 60},
        "4H": {"interval": 5, "limit": 48},
        "1D": {"interval": 15, "limit": 96},
        "1W": {"interval": 60, "limit": 168},
    }
    config = period_map.get(period, period_map["4H"])
    pair = KRAKEN_PAIRS.get(coin, KRAKEN_PAIRS["ETH"])
    try:
        r = requests.get(
            f"{KRAKEN_BASE}/OHLC",
            params={"pair": pair, "interval": config["interval"]},
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
    """Read all four coin CSVs and return merged list with 'coin' field on each row."""
    all_signals = []
    for coin_name, csv_path in COIN_CSV_FILES.items():
        try:
            rows = read_latest_signals(csv_path)
            for row in rows:
                row["coin"] = coin_name
            all_signals.extend(rows)
        except Exception:
            pass
    all_signals.sort(key=lambda r: r.get("timestamp") or "")
    return all_signals


def compute_stats(signals):
    tradeable = [s for s in signals if s.get("signal") in ("Buy", "Sell")]
    wins      = [s for s in tradeable if s.get("outcome") == "W"]
    losses    = [s for s in tradeable if s.get("outcome") == "L"]
    pending   = [s for s in tradeable if s.get("outcome") == "pending"]

    total_wins   = len(wins)
    total_losses = len(losses)
    completed    = total_wins + total_losses
    win_rate     = (total_wins / completed * 100) if completed > 0 else 0
    # Replay using stored amounts per trade; fall back to % of running capital
    capital = COIN_CAPITAL_START * 4
    for s in tradeable:
        if s.get("outcome") == "W":
            amt = s.get("reward_amount")
            capital += float(amt) if amt else round(capital * REWARD_PERCENT, 2)
        elif s.get("outcome") == "L":
            amt = s.get("risk_amount")
            capital -= float(amt) if amt else round(capital * RISK_PERCENT, 2)

    loss_streak = 0
    for s in reversed(tradeable):
        if s.get("outcome") == "L":
            loss_streak += 1
        elif s.get("outcome") == "W":
            break

    return {
        "capital":          capital,
        "win_rate":         win_rate,
        "wins":             total_wins,
        "losses":           total_losses,
        "pending_buys":     len(pending),
        "total_signals":    len(signals),
        "total_completed":  completed,
        "loss_streak":      loss_streak,
        "open_trades":      pending,            # list of all open trades (multi-coin)
        "open_trade":       pending[-1] if pending else None,  # compat
    }


def compute_coin_stats(coin_name, csv_path):
    try:
        rows = read_latest_signals(csv_path)
    except Exception:
        rows = []
    tradeable = [r for r in rows if r.get("signal") in ("Buy", "Sell")]
    wins      = [r for r in tradeable if r.get("outcome") == "W"]
    losses    = [r for r in tradeable if r.get("outcome") == "L"]
    pending   = [r for r in tradeable if r.get("outcome") == "pending"]
    longs_open  = [r for r in pending if r.get("signal") == "Buy"]
    shorts_open = [r for r in pending if r.get("signal") == "Sell"]
    completed = len(wins) + len(losses)
    # Replay stored risk/reward amounts; fall back to % of running capital
    capital = COIN_CAPITAL_START
    for r in tradeable:
        if r.get("outcome") == "W":
            amt = r.get("reward_amount")
            capital += float(amt) if amt else round(capital * REWARD_PERCENT, 2)
        elif r.get("outcome") == "L":
            amt = r.get("risk_amount")
            capital -= float(amt) if amt else round(capital * RISK_PERCENT, 2)
    win_rate = round(len(wins) / completed * 100) if completed > 0 else 0
    open_trade = pending[-1] if pending else None

    # Prefer live market data; fall back to CSV values only if the market fetch fails.
    current_price = fetch_coin_price(coin_name)
    if not current_price:
        for row in reversed(rows):
            for field in ("close_price", "entry_price"):
                val = row.get(field)
                if val and str(val).strip() not in ("", "None", "nan"):
                    try:
                        p = float(val)
                        if p > 0:
                            current_price = p
                            break
                    except (ValueError, TypeError):
                        pass
            if current_price:
                break

    cap = round(capital, 2)
    return {
        "coin":            coin_name,
        "capital":         cap,
        "wins":            len(wins),
        "losses":          len(losses),
        "pending":         len(pending),
        "longs_open":      len(longs_open),
        "shorts_open":     len(shorts_open),
        "win_rate":        win_rate,
        "completed":       completed,
        "open_trade":      open_trade,
        "current_price":   current_price,
        "risk_per_trade":  round(cap * RISK_PERCENT, 2),
        "reward_per_trade": round(cap * REWARD_PERCENT, 2),
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

/* ── Metrics Strip (4 summary cards) ── */
.metrics-strip{display:grid;grid-template-columns:repeat(4,1fr);border-bottom:1px solid var(--sep)}
.met-card{padding:16px 20px;border-right:1px solid var(--sep)}
.met-card:last-child{border-right:none}
.met-label{font-size:11px;color:var(--t2);text-transform:uppercase;letter-spacing:.07em;margin-bottom:5px}
.met-val{font-size:22px;font-weight:600;font-variant-numeric:tabular-nums;letter-spacing:-.01em}
.met-sub{font-size:11px;color:var(--t2);font-family:var(--mono);margin-top:3px}

/* ── Coin Row (4 per-coin cards) ── */
.coin-row{display:grid;grid-template-columns:repeat(4,1fr);border-bottom:1px solid var(--sep)}
.coin-card{padding:14px 16px;border-right:1px solid var(--sep);cursor:pointer;transition:background .1s}
.coin-card:last-child{border-right:none}
.coin-card:hover{background:rgba(255,255,255,.03)}
.cc-name{font-size:13px;font-weight:600;margin-bottom:6px}
.cc-capital{font-size:20px;font-weight:600;font-variant-numeric:tabular-nums;letter-spacing:-.01em;margin-bottom:3px}
.cc-record{font-size:11px;color:var(--t2);font-family:var(--mono)}
.cc-open{font-size:11px;color:var(--amber);margin-top:4px}

/* ── Signal Table ── */
.sig-table{width:100%;border-collapse:collapse}
.sig-table th{font-size:10px;text-transform:uppercase;letter-spacing:.08em;color:var(--t2);text-align:left;padding:8px 20px;font-weight:500;border-bottom:1px solid var(--sep)}
.sig-table td{padding:10px 20px;border-bottom:1px solid var(--sep);font-size:13px;vertical-align:middle}
.sig-table tr:hover td{background:rgba(255,255,255,.02)}
.sig-row-buy td{color:var(--t1)}
.sig-row-dne td{color:var(--t2)}
.outcome-pill{display:inline-block;font-size:11px;font-family:var(--mono);border-radius:4px;padding:2px 7px;font-weight:600}
.pill-w{background:rgba(0,200,5,.15);color:var(--green)}
.pill-l{background:rgba(255,59,48,.15);color:var(--red)}
.pill-open{background:rgba(255,159,10,.12);color:var(--amber)}
.pill-dne{background:rgba(142,142,147,.1);color:var(--t2)}

/* ── Coin Filter Bar ── */
.coin-filter{display:flex;gap:6px;padding:14px 20px;border-bottom:1px solid var(--sep);flex-wrap:wrap;align-items:center}
.coin-btn{padding:5px 13px;border-radius:6px;font-size:12px;font-weight:500;cursor:pointer;border:1px solid var(--sep);background:transparent;color:var(--t2);transition:all .15s}
.coin-btn:hover{color:var(--t1);border-color:var(--t2)}
.coin-btn.active{background:var(--s2);color:var(--t1);border-color:transparent}
.filter-sep{width:1px;height:20px;background:var(--sep);margin:0 4px;flex-shrink:0}
.trades-btn{padding:5px 13px;border-radius:6px;font-size:12px;font-weight:500;cursor:pointer;border:1px solid var(--sep);background:transparent;color:var(--t2);transition:all .15s}
.trades-btn:hover{color:var(--t1);border-color:var(--t2)}
.trades-btn.active{background:rgba(0,200,5,.12);color:var(--green);border-color:rgba(0,200,5,.25)}

/* ── Date Filter Bar ── */
.date-filter{padding:10px 20px;border-bottom:1px solid var(--sep);display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.date-filter-mode{font-size:11px;color:var(--t2);cursor:pointer;text-decoration:underline;text-underline-offset:2px;margin-left:auto;flex-shrink:0;padding:2px 0}
.date-filter-mode:hover{color:var(--t1)}
.quick-btns{display:flex;gap:4px;flex-wrap:wrap;flex:1}
.quick-btn{padding:4px 10px;border-radius:5px;font-size:11px;font-weight:500;cursor:pointer;border:1px solid var(--sep);background:transparent;color:var(--t2);transition:all .15s}
.quick-btn:hover{color:var(--t1);border-color:var(--t2)}
.quick-btn.active{background:var(--s2);color:var(--t1);border-color:transparent}
.abs-range{display:none;gap:12px;align-items:center;flex:1;flex-wrap:wrap}
.abs-range.show{display:flex}
.abs-range label{font-size:11px;color:var(--t2);display:flex;align-items:center;gap:5px}
.abs-range input[type=date],.abs-range input[type=time]{background:var(--s2);border:1px solid var(--sep);color:var(--t1);border-radius:5px;padding:3px 7px;font-size:11px;font-family:var(--mono);cursor:pointer}
.abs-range input[type=date]:focus,.abs-range input[type=time]:focus{outline:none;border-color:rgba(255,255,255,.3)}

/* ── Signal Count ── */
.sig-count{padding:8px 20px 4px;font-size:12px;color:var(--t2)}

/* ── Duration column ── */
.dur-open{color:var(--amber);font-size:12px;font-family:var(--mono)}
.dur-closed{color:var(--t3);font-size:12px;font-family:var(--mono)}

/* ── Coin Badges in Feed ── */
.coin-badge{display:inline-block;font-size:10px;font-family:var(--mono);border-radius:4px;padding:1px 5px;margin-left:4px;vertical-align:middle;font-weight:600}
.coin-eth {background:rgba(98,126,234,.18);color:#627eea}
.coin-sol {background:rgba(153,69,255,.18);color:#9945ff}
.coin-avax{background:rgba(232,65,66,.18);color:#e84142}
.coin-xrp {background:rgba(0,90,212,.18);color:#4da6ff}

/* ── Page Tab Switcher ── */
.page-tabs{display:flex;gap:8px;padding:16px 20px 0;border-bottom:1px solid var(--sep)}
.page-tab{padding:8px 18px;border-radius:8px 8px 0 0;font-size:14px;font-weight:500;cursor:pointer;border:none;background:transparent;color:var(--t2);transition:all .15s;border-bottom:2px solid transparent;margin-bottom:-1px}
.page-tab:hover{color:var(--t1)}
.page-tab.active{color:var(--t1);border-bottom-color:var(--green)}

/* ── Project Status Tab ── */
.ps-section{padding:20px 20px 0}
.ps-section-title{font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:var(--t2);font-weight:600;margin-bottom:12px}
.ps-cards{display:grid;grid-template-columns:repeat(4,1fr);gap:0;border:1px solid var(--sep);border-radius:12px;overflow:hidden;margin-bottom:20px}
.ps-card{padding:16px 18px;border-right:1px solid var(--sep)}
.ps-card:last-child{border-right:none}
.ps-card-label{font-size:11px;color:var(--t2);text-transform:uppercase;letter-spacing:.07em;margin-bottom:5px}
.ps-card-val{font-size:22px;font-weight:600;font-variant-numeric:tabular-nums;letter-spacing:-.01em;font-family:var(--mono)}
.ps-card-sub{font-size:11px;color:var(--t2);margin-top:3px}
.ps-info-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:20px}
.ps-info-block{background:var(--s2);border-radius:10px;padding:14px 16px}
.ps-info-label{font-size:10px;text-transform:uppercase;letter-spacing:.08em;color:var(--t2);margin-bottom:3px}
.ps-info-val{font-size:14px;font-family:var(--mono);font-weight:500}
.ps-milestones{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:20px}
.ps-milestone{display:flex;align-items:center;gap:8px;font-size:13px;padding:8px 10px;background:var(--s2);border-radius:8px}
.ms-icon{font-size:16px;flex-shrink:0}
.ps-progress-list{display:flex;flex-direction:column;gap:14px;padding-bottom:32px}
.ps-prog-item{}
.ps-prog-header{display:flex;justify-content:space-between;font-size:12px;margin-bottom:5px}
.ps-prog-label{color:var(--t2)}
.ps-prog-nums{font-family:var(--mono);color:var(--t1)}
.ps-prog-track{height:6px;background:var(--s2);border-radius:3px;position:relative;overflow:hidden}
.ps-prog-fill{height:100%;border-radius:3px;background:linear-gradient(90deg,var(--amber),var(--green));transition:width .5s}
.ps-prog-fill.done{background:var(--green)}

@media(max-width:600px){
  .ps-cards{grid-template-columns:1fr 1fr}
  .ps-card:nth-child(2){border-right:none}
  .ps-card:nth-child(3){border-top:1px solid var(--sep)}
  .ps-milestones{grid-template-columns:1fr}
}

@media(max-width:700px){
  .price-main{font-size:34px}
  .metrics-strip{grid-template-columns:1fr 1fr}
  .met-card:nth-child(2){border-right:none}
  .met-card:nth-child(3){border-top:1px solid var(--sep)}
  .coin-row{grid-template-columns:1fr 1fr}
  .coin-card:nth-child(2){border-right:none}
  .coin-card:nth-child(3){border-top:1px solid var(--sep)}
  .mc-kv{grid-template-columns:1fr}
  .sig-table th:nth-child(4),.sig-table td:nth-child(4){display:none}
}

/* ── Go-Live Tracker ── */
.gl-section{padding:20px 20px 0}
.gl-section-title{font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:var(--t2);font-weight:600;margin-bottom:12px}
.gl-metric-cards{display:grid;grid-template-columns:repeat(4,1fr);border:1px solid var(--sep);border-radius:12px;overflow:hidden;margin-bottom:20px}
.gl-metric-card{padding:16px 18px;border-right:1px solid var(--sep)}
.gl-metric-card:last-child{border-right:none}
.gl-metric-label{font-size:11px;color:var(--t2);text-transform:uppercase;letter-spacing:.07em;margin-bottom:5px}
.gl-metric-val{font-size:24px;font-weight:600;font-variant-numeric:tabular-nums;letter-spacing:-.01em}
.gl-metric-sub{font-size:11px;color:var(--t2);margin-top:3px;font-family:var(--mono)}
.gl-verdict{margin:0 0 20px;padding:18px 20px;border-radius:12px;font-size:16px;font-weight:600;text-align:center;letter-spacing:.02em}
.gl-verdict-ready{background:rgba(0,200,5,.1);border:1px solid rgba(0,200,5,.3);color:var(--green)}
.gl-verdict-close{background:rgba(255,159,10,.1);border:1px solid rgba(255,159,10,.3);color:var(--amber)}
.gl-verdict-no{background:rgba(255,59,48,.08);border:1px solid rgba(255,59,48,.2);color:var(--red)}
.gl-criteria-list{display:flex;flex-direction:column;gap:0;border:1px solid var(--sep);border-radius:12px;overflow:hidden;margin-bottom:20px}
.gl-criterion{display:flex;align-items:center;padding:12px 16px;border-bottom:1px solid var(--sep);gap:12px}
.gl-criterion:last-child{border-bottom:none}
.gl-crit-name{flex:1;font-size:13px}
.gl-crit-val{font-size:12px;font-family:var(--mono);color:var(--t2);min-width:80px;text-align:right}
.gl-crit-badge{font-size:10px;font-weight:700;letter-spacing:.08em;padding:3px 8px;border-radius:4px;min-width:62px;text-align:center}
.gl-badge-pass{background:rgba(0,200,5,.15);color:var(--green)}
.gl-badge-close{background:rgba(255,159,10,.12);color:var(--amber)}
.gl-badge-no{background:rgba(255,59,48,.12);color:var(--red)}
.gl-progress-list{display:flex;flex-direction:column;gap:14px;margin-bottom:20px}
.gl-prog-item{}
.gl-prog-header{display:flex;justify-content:space-between;font-size:12px;margin-bottom:5px}
.gl-prog-label{color:var(--t2)}
.gl-prog-nums{font-family:var(--mono);color:var(--t1)}
.gl-prog-track{height:6px;background:var(--s2);border-radius:3px;overflow:hidden}
.gl-prog-fill{height:100%;border-radius:3px;background:linear-gradient(90deg,var(--amber),var(--green));transition:width .5s}
.gl-prog-fill.done{background:var(--green)}
.gl-capital-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:20px}
.gl-cap-block{background:var(--s2);border-radius:10px;padding:14px 16px}
.gl-cap-label{font-size:10px;text-transform:uppercase;letter-spacing:.08em;color:var(--t2);margin-bottom:3px}
.gl-cap-val{font-size:15px;font-family:var(--mono);font-weight:500}
.gl-timeline{display:flex;flex-direction:column;gap:10px;padding-bottom:40px}
.gl-tl-item{display:flex;align-items:flex-start;gap:14px;padding:10px 0;border-bottom:1px solid var(--sep)}
.gl-tl-item:last-child{border-bottom:none}
.gl-tl-dot{width:10px;height:10px;border-radius:50%;flex-shrink:0;margin-top:3px}
.gl-tl-dot-done{background:var(--green)}
.gl-tl-dot-active{background:var(--amber)}
.gl-tl-dot-future{background:var(--t3)}
.gl-tl-body{flex:1}
.gl-tl-label{font-size:13px;font-weight:500}
.gl-tl-date{font-size:11px;color:var(--t2);font-family:var(--mono);margin-top:2px}
@media(max-width:700px){
  .gl-metric-cards{grid-template-columns:1fr 1fr}
  .gl-metric-card:nth-child(2){border-right:none}
  .gl-metric-card:nth-child(3){border-top:1px solid var(--sep)}
  .gl-capital-grid{grid-template-columns:1fr 1fr}
}

/* ── Learnings Tab ── */
.lr-coin-filter{display:flex;gap:6px;padding:14px 20px;border-bottom:1px solid var(--sep)}
.lr-coin-btn{padding:5px 13px;border-radius:6px;font-size:12px;font-weight:500;cursor:pointer;border:1px solid var(--sep);background:transparent;color:var(--t2);transition:all .15s}
.lr-coin-btn:hover{color:var(--t1);border-color:var(--t2)}
.lr-coin-btn.active{background:var(--s2);color:var(--t1);border-color:transparent}
.lr-card{padding:20px;border-bottom:1px solid var(--sep)}
.lr-card-header{display:flex;align-items:center;gap:12px;margin-bottom:14px;flex-wrap:wrap}
.lr-coin-name{font-size:16px;font-weight:700;letter-spacing:.02em}
.lr-meta{font-size:12px;color:var(--t2);font-family:var(--mono)}
.lr-empty{font-size:13px;color:var(--t2);padding:16px 0}
.lr-table{width:100%;border-collapse:collapse;margin-bottom:16px}
.lr-table th{font-size:10px;text-transform:uppercase;letter-spacing:.08em;color:var(--t2);text-align:left;padding:7px 10px;font-weight:500;border-bottom:1px solid var(--sep)}
.lr-table td{padding:9px 10px;border-bottom:1px solid var(--sep);font-size:12px;vertical-align:middle;font-family:var(--mono)}
.lr-table tr:hover td{background:rgba(255,255,255,.02)}
.lr-badge{display:inline-block;font-size:10px;font-weight:700;letter-spacing:.07em;padding:2px 7px;border-radius:4px;text-align:center;min-width:58px}
.lr-badge-avoid{background:rgba(255,59,48,.18);color:var(--red)}
.lr-badge-caution{background:rgba(255,159,10,.15);color:var(--amber)}
.lr-badge-neutral{background:rgba(142,142,147,.15);color:var(--t2)}
.lr-badge-favor{background:rgba(0,200,5,.15);color:var(--green)}
.lr-wr-green{background:rgba(0,200,5,.12)}
.lr-wr-yellow{background:rgba(255,159,10,.1)}
.lr-wr-red{background:rgba(255,59,48,.08)}
.lr-setup{font-size:12px;color:var(--t2);line-height:1.6;margin-bottom:14px}
.lr-setup span{color:var(--t1);font-family:var(--mono)}
.lr-summary{font-size:13px;color:var(--t1);line-height:1.5;padding:10px 14px;background:var(--s2);border-radius:8px;margin-bottom:14px}
.lr-history-toggle{font-size:12px;color:var(--t2);cursor:pointer;text-decoration:underline;text-underline-offset:2px;display:inline-block;margin-bottom:10px}
.lr-history-toggle:hover{color:var(--t1)}
.lr-history-table{width:100%;border-collapse:collapse;margin-bottom:8px;display:none}
.lr-history-table th{font-size:10px;text-transform:uppercase;letter-spacing:.07em;color:var(--t2);text-align:left;padding:6px 8px;font-weight:500;border-bottom:1px solid var(--sep)}
.lr-history-table td{padding:7px 8px;border-bottom:1px solid var(--sep);font-size:11px;color:var(--t2);font-family:var(--mono);vertical-align:top}
.lr-history-table td:last-child{font-family:inherit;color:var(--t1);font-size:11px;white-space:normal}
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
    <div class="portfolio-pill" id="hdr-portfolio">$4,000.00</div>
    <div class="hdr-meta"><span class="live-dot"></span><span id="hdr-countdown">60s</span></div>
  </div>
</header>

<div class="page">
  <div class="page-tabs">
    <button class="page-tab active" id="tab-live" onclick="switchTab('live')">Live Signals</button>
    <button class="page-tab" id="tab-project" onclick="switchTab('project')">Project Status</button>
    <button class="page-tab" id="tab-golive" onclick="switchTab('golive')">Go-Live Tracker</button>
    <button class="page-tab" id="tab-learnings" onclick="switchTab('learnings')">Learnings</button>
  </div>

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

    <!-- Metrics Strip (4 summary cards) -->
    <div class="metrics-strip" id="metrics-strip"></div>

    <!-- Coin Row (4 per-coin cards) -->
    <div class="coin-row" id="coin-row"></div>

    <!-- Coin Filter + Trades-Only Toggle -->
    <div class="coin-filter" id="coin-filter">
      <button class="coin-btn active" data-coin="all"  onclick="setCoinFilter('all')">All</button>
      <button class="coin-btn"        data-coin="ETH"  onclick="setCoinFilter('ETH')">ETH</button>
      <button class="coin-btn"        data-coin="SOL"  onclick="setCoinFilter('SOL')">SOL</button>
      <button class="coin-btn"        data-coin="AVAX" onclick="setCoinFilter('AVAX')">AVAX</button>
      <button class="coin-btn"        data-coin="XRP"  onclick="setCoinFilter('XRP')">XRP</button>
      <div class="filter-sep"></div>
      <button class="trades-btn active" id="trades-only-btn" onclick="toggleTradesOnly()">Trades only</button>
    </div>

    <!-- Date Filter Bar -->
    <div class="date-filter" id="date-filter">
      <div class="quick-btns" id="quick-btns">
        <button class="quick-btn" data-q="today"  onclick="setQuickFilter('today')">Today</button>
        <button class="quick-btn" data-q="24h"    onclick="setQuickFilter('24h')">Last 24h</button>
        <button class="quick-btn active" data-q="48h" onclick="setQuickFilter('48h')">Last 48h</button>
        <button class="quick-btn" data-q="7d"     onclick="setQuickFilter('7d')">Last 7 days</button>
        <button class="quick-btn" data-q="all"    onclick="setQuickFilter('all')">All time</button>
      </div>
      <div class="abs-range" id="abs-range">
        <label>From <input type="date" id="abs-from-date"> <input type="time" id="abs-from-time" value="00:00"></label>
        <label>To &nbsp;&nbsp;<input type="date" id="abs-to-date"> <input type="time" id="abs-to-time" value="23:59"></label>
      </div>
      <span class="date-filter-mode" id="date-mode-toggle" onclick="toggleDateMode()">switch to date range</span>
    </div>

    <!-- Signal Count -->
    <div class="sig-count" id="sig-count"></div>

    <!-- Alerts -->
    <div class="alerts-wrap" id="alerts-wrap"></div>

    <!-- Signal Feed -->
    <div class="feed-header">
      <div class="feed-title">Signals</div>
      <div class="feed-tabs" id="feed-tabs"></div>
    </div>
    <div id="feed-list"></div>

  </div><!-- /app -->

  <!-- Project Status Tab -->
  <div id="project-view" style="display:none">
    <div id="ps-loading" class="loading" style="display:none"><div class="spin"></div><br>Loading project data&hellip;</div>
    <div id="ps-content"></div>
  </div>

  <!-- Go-Live Tracker Tab -->
  <div id="golive-view" style="display:none">
    <div id="gl-loading" class="loading"><div class="spin"></div><br>Loading go-live data&hellip;</div>
    <div id="gl-content" style="display:none"></div>
  </div>

  <!-- Learnings Tab -->
  <div id="learnings-view" style="display:none">
    <div id="lr-loading" class="loading"><div class="spin"></div><br>Loading learnings&hellip;</div>
    <div id="lr-content" style="display:none"></div>
  </div>

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
  {k:'all',l:'All'},{k:'buy',l:'Buy'},{k:'sell',l:'Sell'},{k:'win',l:'Win'},
  {k:'loss',l:'Loss'},{k:'pending',l:'Pending'},{k:'dne',l:'Skip'},
];

// ── State ────────────────────────────────────────────────────────────────────
let signals = [], openTrades = [], stats = {}, coinStats = [], projectTotals = null;
let feedFilter = 'all', coinFilter = 'all', period = '4H';
let livePrice = 0, periodOpen = 0;
let countdown = REFRESH, cdTimer;
let feedLimit = 30;
let chartCandles = [];

// Signal log filter state
let tradesOnly  = true;           // CHANGE 1: hide DNE by default
let quickFilter = '48h';          // CHANGE 2: default last 48h
let dateMode    = 'relative';     // 'relative' | 'absolute'

// ── Date filter helpers ───────────────────────────────────────────────────────
function nowPacific(){
  // Returns current Date adjusted to Pacific (UTC-7 / UTC-8) via Intl
  return new Date();
}

function pacificMidnight(){
  // Midnight of today in Pacific time
  const now = new Date();
  const pStr = now.toLocaleDateString('en-US', {timeZone:'America/Los_Angeles'});
  return new Date(new Date(pStr).getTime());
}

function getDateWindow(){
  // Returns {from: Date|null, to: Date|null} based on active date filter
  if(dateMode === 'absolute'){
    const fd = $('abs-from-date').value, ft = $('abs-from-time').value||'00:00';
    const td = $('abs-to-date').value,   tt = $('abs-to-time').value||'23:59';
    const from = fd ? new Date(`${fd}T${ft}:00`) : null;
    const to   = td ? new Date(`${td}T${tt}:59`) : null;
    return {from, to};
  }
  const now = new Date();
  switch(quickFilter){
    case 'today':  return {from: pacificMidnight(), to: null};
    case '24h':    return {from: new Date(now - 24*3600*1000), to: null};
    case '48h':    return {from: new Date(now - 48*3600*1000), to: null};
    case '7d':     return {from: new Date(now - 7*24*3600*1000), to: null};
    default:       return {from: null, to: null}; // all time
  }
}

function parseSignalDate(ts){
  if(!ts) return null;
  try{ return new Date(ts.replace(' ','T')); } catch(e){ return null; }
}

function applyDateFilter(sigs){
  const {from, to} = getDateWindow();
  if(!from && !to) return sigs;
  return sigs.filter(s=>{
    const d = parseSignalDate(s.timestamp);
    if(!d) return false;
    if(from && d < from) return false;
    if(to   && d > to)   return false;
    return true;
  });
}

// ── Trades-only toggle ────────────────────────────────────────────────────────
function toggleTradesOnly(){
  tradesOnly = !tradesOnly;
  const btn = $('trades-only-btn');
  btn.classList.toggle('active', tradesOnly);
  btn.textContent = tradesOnly ? 'Trades only' : 'All signals';
  feedLimit = 30;
  renderFeed();
}

// ── Quick date filter ─────────────────────────────────────────────────────────
function setQuickFilter(q){
  quickFilter = q;
  document.querySelectorAll('.quick-btn').forEach(b=>{
    b.classList.toggle('active', b.dataset.q === q);
  });
  feedLimit = 30;
  renderFeed();
}

// ── Toggle relative / absolute date mode ─────────────────────────────────────
function toggleDateMode(){
  dateMode = dateMode === 'relative' ? 'absolute' : 'relative';
  const qb = $('quick-btns'), ar = $('abs-range'), tog = $('date-mode-toggle');
  if(dateMode === 'absolute'){
    qb.style.display = 'none';
    ar.classList.add('show');
    tog.textContent = 'switch to quick filters';
    // Set defaults: From = today 00:00, To = now
    const now   = new Date();
    const today = now.toLocaleDateString('en-CA'); // YYYY-MM-DD
    const hhmm  = now.toTimeString().slice(0,5);
    if(!$('abs-from-date').value) $('abs-from-date').value = today;
    if(!$('abs-to-date').value)   $('abs-to-date').value   = today;
    if(!$('abs-to-time').value)   $('abs-to-time').value   = hhmm;
  } else {
    qb.style.display = '';
    ar.classList.remove('show');
    tog.textContent = 'switch to date range';
  }
  feedLimit = 30;
  renderFeed();
}

// Wire absolute inputs to live-filter (idempotent — only runs once)
let _absInputsInited = false;
function _initAbsInputs(){
  if(_absInputsInited) return;
  _absInputsInited = true;
  ['abs-from-date','abs-from-time','abs-to-date','abs-to-time'].forEach(id=>{
    const el = document.getElementById(id);
    if(el) el.addEventListener('change', ()=>{ feedLimit=30; renderFeed(); });
  });
}

// ── Duration helper ───────────────────────────────────────────────────────────
function fmtDuration(openTs, closeTs){
  const a = parseSignalDate(openTs), b = parseSignalDate(closeTs);
  if(!a || !b) return null;
  const mins = Math.round((b - a) / 60000);
  if(mins < 1) return '<1m';
  const h = Math.floor(mins / 60), m = mins % 60;
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

function activeCoin(){
  return coinFilter === 'all' ? 'ETH' : coinFilter;
}

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
    const r=await fetch(`/api/market?ticker=1&coin=${encodeURIComponent(activeCoin())}`,{signal:AbortSignal.timeout(5000)});
    const d=await r.json();
    if(!r.ok||!d.ok)return null;
    return d.ticker;
  }catch(e){return null;}
}

async function fetchCandles(p){
  try{
    const r=await fetch(`/api/market?period=${encodeURIComponent(p)}&coin=${encodeURIComponent(activeCoin())}`,{signal:AbortSignal.timeout(6000)});
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
  const coinNameMap = {ETH:'Ethereum', SOL:'Solana', AVAX:'Avalanche', XRP:'XRP'};
  const coin = ticker.coin || activeCoin();
  const isUp=ticker.pct>=0;
  const col=isUp?'#00c805':'#ff3b30';
  const sign=isUp?'+':'';
  document.title=`${coin} · Signal Engine`;
  const hdrSym=document.querySelector('.hdr-sym');
  const hdrSub=document.querySelector('.hdr-sub');
  const heroName=document.querySelector('.coin-name');
  if(hdrSym) hdrSym.innerHTML=`${coin} &middot; USD`;
  if(hdrSub) hdrSub.textContent=coinNameMap[coin] || coin;
  if(heroName) heroName.textContent=coinNameMap[coin] || coin;
  $('hero-price').textContent='$'+ticker.price.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
  $('hero-change').innerHTML=`<span style="color:${col}">${sign}$${Math.abs(ticker.change).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2})} (${sign}${ticker.pct.toFixed(2)}%)</span>`;
  $('hero-period').textContent='Past 24 hours';
}

// ── Render: Metrics Strip (4 summary cards) ──────────────────────────────────
function renderMetrics(cs, sigs, pt){
  const totalCapital = cs.reduce((a,c)=>a+c.capital, 0);
  const totalOpen    = cs.reduce((a,c)=>a+c.pending, 0);
  const pnl          = totalCapital - 4000;
  const pnlSign      = pnl >= 0 ? '+' : '';
  const pnlCol       = pnl > 0 ? '#00c805' : pnl < 0 ? '#ff3b30' : '#8e8e93';

  // Win rate + trades: use all-time data from project_log if available
  const atWins   = pt ? pt.all_time_wins   : cs.reduce((a,c)=>a+c.wins, 0);
  const atLosses = pt ? pt.all_time_losses : cs.reduce((a,c)=>a+c.losses, 0);
  const atWR     = pt ? pt.all_time_win_rate : (atWins+atLosses>0 ? atWins/(atWins+atLosses)*100 : 0);
  const atTrades = pt ? pt.all_time_trades : (atWins + atLosses);
  const wrCol    = (atWins+atLosses)===0?'#8e8e93':atWR>=55?'#00c805':atWR>=45?'#ff9f0a':'#ff3b30';

  $('metrics-strip').innerHTML = `
    <div class="met-card">
      <div class="met-label">Total Capital</div>
      <div class="met-val" style="color:${pnlCol}">${fmtUSD(totalCapital)}</div>
      <div class="met-sub" style="color:${pnlCol}">${pnlSign}${fmtUSD(Math.abs(pnl))}</div>
    </div>
    <div class="met-card">
      <div class="met-label">Win Rate</div>
      <div class="met-val" style="color:${wrCol}">${(atWins+atLosses)>0?fmtNum(atWR,1)+'%':'—'}</div>
      <div class="met-sub">${atWins}w ${atLosses}l</div>
    </div>
    <div class="met-card">
      <div class="met-label">Open Trades</div>
      <div class="met-val" style="color:${totalOpen>0?'var(--amber)':'#8e8e93'}">${totalOpen}</div>
      <div class="met-sub">across all coins</div>
    </div>
    <div class="met-card">
      <div class="met-label">All-time Trades</div>
      <div class="met-val">${atTrades}</div>
      <div class="met-sub">${sigs.length} signals total</div>
    </div>
  `;
  $('hdr-portfolio').textContent = fmtUSD(totalCapital);
}

// ── Render: Coin Row (4 per-coin cards) ──────────────────────────────────────
function renderCoinRow(cs){
  $('coin-row').innerHTML = cs.map(c=>{
    const pnl      = c.capital - 1000;
    const capCol   = pnl > 0 ? '#00c805' : pnl < 0 ? '#ff3b30' : '#8e8e93';
    const wrCol    = c.completed===0?'#8e8e93':c.win_rate>=55?'#00c805':c.win_rate>=45?'#ff9f0a':'#ff3b30';
    const priceStr = c.current_price ? fmtUSD(c.current_price, c.current_price < 10 ? 4 : 2) : '—';
    const riskStr  = c.risk_per_trade   ? fmtUSD(c.risk_per_trade,   2) : fmtUSD(c.capital*0.02, 2);
    const rewStr   = c.reward_per_trade ? fmtUSD(c.reward_per_trade, 2) : fmtUSD(c.capital*0.03, 2);
    return `<div class="coin-card" onclick="setCoinFilter('${c.coin}')">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:7px">
        <span class="coin-badge coin-${c.coin.toLowerCase()}">${c.coin}</span>
        <span style="font-size:13px;font-weight:600;font-family:var(--mono);font-variant-numeric:tabular-nums">${priceStr}</span>
      </div>
      <div style="display:flex;justify-content:space-between;margin-bottom:4px">
        <div>
          <div style="font-size:10px;color:var(--t2);text-transform:uppercase;letter-spacing:.06em;margin-bottom:2px">Capital</div>
          <div class="cc-capital" style="font-size:16px;color:${capCol}">${fmtUSD(c.capital)}</div>
        </div>
        <div style="text-align:right">
          <div style="font-size:10px;color:var(--t2);text-transform:uppercase;letter-spacing:.06em;margin-bottom:2px">W/L</div>
          <div class="cc-record" style="font-size:13px;color:${wrCol}">${c.wins}W / ${c.losses}L</div>
        </div>
      </div>
      <div style="display:flex;justify-content:space-between;margin-bottom:4px">
        <div>
          <div style="font-size:10px;color:var(--t2);text-transform:uppercase;letter-spacing:.06em;margin-bottom:2px">Win Rate</div>
          <div style="font-size:12px;font-family:var(--mono);color:${wrCol}">${c.completed>0?c.win_rate+'%':'—'}</div>
        </div>
        <div style="text-align:right">
          <div style="font-size:10px;color:var(--t2);text-transform:uppercase;letter-spacing:.06em;margin-bottom:2px">Open</div>
          <div style="font-size:12px;color:${c.pending>0?'var(--amber)':'var(--t2)'}">L:${c.longs_open||0} S:${c.shorts_open||0}</div>
        </div>
      </div>
      <div style="display:flex;justify-content:space-between;padding-top:6px;border-top:1px solid rgba(255,255,255,.06)">
        <div>
          <div style="font-size:10px;color:var(--t2);text-transform:uppercase;letter-spacing:.06em;margin-bottom:2px">Risk/trade</div>
          <div style="font-size:11px;font-family:var(--mono);color:var(--red)">${riskStr} (2%)</div>
        </div>
        <div style="text-align:right">
          <div style="font-size:10px;color:var(--t2);text-transform:uppercase;letter-spacing:.06em;margin-bottom:2px">Win/trade</div>
          <div style="font-size:11px;font-family:var(--mono);color:var(--green)">${rewStr} (3%)</div>
        </div>
      </div>
    </div>`;
  }).join('');
}

// ── Coin Filter ───────────────────────────────────────────────────────────────
function setCoinFilter(coin){
  coinFilter=coin;
  document.querySelectorAll('.coin-btn').forEach(b=>{
    b.classList.toggle('active', b.dataset.coin===coin);
  });
  const filtered = coinFilter==='all' ? signals : signals.filter(s=>s.coin===coinFilter);
  const st = computeStatsJS(filtered);
  renderMetrics(coinStats, signals, projectTotals);
  renderCoinRow(coinStats);
  renderAlerts(st);
  renderFeed();
  refreshMarketView();
}

async function refreshMarketView(){
  const [ticker, candles] = await Promise.all([fetchTicker(), fetchCandles(period)]);
  if(ticker){
    livePrice = ticker.price;
    renderHero(ticker);
  }
  if(candles.length){
    chartCandles = candles;
    const chartTrade = openTrades.find(t=>coinFilter==='all'||t.coin===coinFilter)||null;
    drawChart(chartCandles, chartTrade);
  }
}

function computeStatsJS(sigs){
  const tradeable=sigs.filter(s=>s.signal==='Buy'||s.signal==='Sell');
  const wins=tradeable.filter(s=>s.outcome==='W').length;
  const losses=tradeable.filter(s=>s.outcome==='L').length;
  const pending=tradeable.filter(s=>s.outcome==='pending');
  const completed=wins+losses;
  // Replay stored risk/reward amounts; fall back to 2%/3% of running capital
  let capital=4000;
  for(const s of tradeable){
    if(s.outcome==='W') capital+=s.reward_amount?parseFloat(s.reward_amount):capital*0.03;
    else if(s.outcome==='L') capital-=s.risk_amount?parseFloat(s.risk_amount):capital*0.02;
  }
  return {
    capital,
    win_rate: completed>0?(wins/completed*100):0,
    wins, losses,
    pending_buys: pending.length,
    total_signals: sigs.length,
    total_completed: completed,
    loss_streak: (()=>{let s=0;for(const b of [...tradeable].reverse()){if(b.outcome==='L')s++;else if(b.outcome==='W')break;}return s;})(),
    open_trades: pending,
    open_trade: pending[pending.length-1]||null,
  };
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
    case'sell':   return sigs.filter(s=>s.signal==='Sell');
    case'win':    return sigs.filter(s=>s.outcome==='W');
    case'loss':   return sigs.filter(s=>s.outcome==='L');
    case'pending':return sigs.filter(s=>(s.signal==='Buy'||s.signal==='Sell')&&s.outcome==='pending');
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

// Build the filtered+sorted row array used by both renderFeed and openModal
function _buildFeedRows(){
  let coinSigs = coinFilter==='all' ? signals : signals.filter(s=>s.coin===coinFilter);
  // tradesOnly: hide DNE
  if(tradesOnly) coinSigs = coinSigs.filter(s=>s.signal!=='Do Not Enter');
  // date filter
  coinSigs = applyDateFilter(coinSigs);
  // feed-tab filter (buy/sell/win/loss/pending/dne/all)
  coinSigs = applyFeedFilter(coinSigs, feedFilter);
  return [...coinSigs].reverse();
}

function renderFeed(){
  const rows = _buildFeedRows();
  const totalCoinSigs = (coinFilter==='all' ? signals : signals.filter(s=>s.coin===coinFilter));
  const totalAfterDate = applyDateFilter(totalCoinSigs);
  const tradesInWindow = totalAfterDate.filter(s=>s.signal==='Buy'||s.signal==='Sell');
  const dneHidden      = tradesOnly ? totalAfterDate.filter(s=>s.signal==='Do Not Enter').length : 0;
  const coinCount      = [...new Set(rows.map(s=>s.coin).filter(Boolean))].length;

  // Signal count line
  const countEl = $('sig-count');
  if(countEl){
    if(rows.length===0){
      countEl.textContent = 'No signals in this window';
    } else {
      const tradeWord = rows.length===1?'trade':'trades';
      const coinWord  = coinCount===1?'coin':'coins';
      let txt = `Showing ${rows.length} ${tradeWord} across ${coinCount} ${coinWord}`;
      if(dneHidden>0) txt += ` (${dneHidden} DNE signal${dneHidden===1?'':'s'} hidden)`;
      countEl.textContent = txt;
    }
  }

  const slice = rows.slice(0, feedLimit);
  const el = $('feed-list');

  if(!slice.length){
    el.innerHTML='<div style="text-align:center;padding:32px;color:var(--t2);font-size:13px">No signals</div>';
    return;
  }

  let html=`<table class="sig-table"><thead><tr>
    <th>Time</th><th>Coin</th><th>Signal</th><th>Confidence</th><th>Outcome</th><th>Duration</th>
  </tr></thead><tbody>`;

  html+=slice.map((s,i)=>{
    const isBuy=s.signal==='Buy', isSell=s.signal==='Sell';
    const isActive=isBuy||isSell;
    const coin=s.coin||'';
    const coinBadge=coin?`<span class="coin-badge coin-${coin.toLowerCase()}">${coin}</span>`:'—';
    const conf=parseInt(s.confidence)||0;
    const confStr=conf>0?`${conf}%`:'—';

    let outcomePill='';
    if(isActive){
      if(s.outcome==='W'){
        const amt=s.reward_amount?'+'+fmtUSD(s.reward_amount,2):'+3%';
        outcomePill=`<span class="outcome-pill pill-w">W ${amt}</span>`;
      } else if(s.outcome==='L'){
        const amt=s.risk_amount?'&minus;'+fmtUSD(s.risk_amount,2):'&minus;2%';
        outcomePill=`<span class="outcome-pill pill-l">L ${amt}</span>`;
      } else {
        outcomePill=`<span class="outcome-pill pill-open">Open</span>`;
      }
    } else {
      outcomePill=`<span class="outcome-pill pill-dne">DNE</span>`;
    }

    let sigLabel, sigStyle, rowCls;
    if(isBuy){
      sigLabel='Buy'; sigStyle='color:var(--green)'; rowCls='sig-row-buy';
    } else if(isSell){
      sigLabel='Sell'; sigStyle='color:var(--red)'; rowCls='sig-row-buy';
    } else {
      sigLabel='Do Not Enter'; sigStyle='color:var(--t2)'; rowCls='sig-row-dne';
    }

    // Duration column
    let durCell='<td style="color:var(--t2);font-family:var(--mono)">—</td>';
    if(isActive){
      if(s.outcome==='W'||s.outcome==='L'){
        const d=fmtDuration(s.timestamp,s.close_time);
        durCell=d?`<td class="dur-closed" style="font-family:var(--mono)">${d}</td>`:'<td style="color:var(--t2);font-family:var(--mono)">—</td>';
      } else if(s.outcome==='pending'){
        const d=fmtDuration(s.timestamp,new Date().toISOString().replace('T',' ').slice(0,19));
        durCell=d?`<td class="dur-open" style="font-family:var(--mono)">${d}</td>`:'<td style="color:var(--t2);font-family:var(--mono)">—</td>';
      }
    }

    // openModal index into the full _buildFeedRows array
    const modalIdx = i; // slice is the first feedLimit items of rows; rows[i] === slice[i]
    return`<tr class="${rowCls}" onclick="openModal(${modalIdx})" style="cursor:pointer">
      <td style="font-family:var(--mono);color:var(--t2)">${fmtTime(s.timestamp)}</td>
      <td>${coinBadge}</td>
      <td style="${sigStyle}">${sigLabel}</td>
      <td style="font-family:var(--mono)">${confStr}</td>
      <td>${outcomePill}</td>
      ${durCell}
    </tr>`;
  }).join('');

  html+='</tbody></table>';

  if(rows.length>feedLimit){
    html+=`<div class="show-more" onclick="feedLimit+=30;renderFeed()">Show more (${rows.length-feedLimit} remaining)</div>`;
  }
  el.innerHTML=html;
}

// ── Modal ─────────────────────────────────────────────────────────────────────
function openModal(idx){
  const rows = _buildFeedRows();
  const s=rows[idx];
  if(!s)return;

  const conf=parseInt(s.confidence)||0;
  const sigLabel=s.signal==='Buy'?'Buy (Long)':s.signal==='Sell'?'Sell (Short)':s.signal==='Do Not Enter'?'Do Not Enter':'Hold';
  $('mo-title').textContent=sigLabel+(conf?` · ${conf}% confidence`:'');

  const isSell=s.signal==='Sell';
  const slHint=isSell?'above entry':'below entry';
  const tpHint=isSell?'below entry':'above entry';
  const kv=[
    ['Time',fmtTime(s.timestamp),true],
    ['Direction',s.direction||(s.signal==='Buy'?'LONG':s.signal==='Sell'?'SHORT':'—'),true],
    ['Confidence',conf>0?conf+'%':'—',true],
    ['Entry',fmtUSD(s.entry_price),true],
    ['Stop Loss',s.stop_loss?fmtUSD(s.stop_loss)+' ('+slHint+')':'—',true],
    ['Take Profit',s.take_profit?fmtUSD(s.take_profit)+' ('+tpHint+')':'—',true],
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
  const chartTrade=openTrades.find(t=>coinFilter==='all'||t.coin===coinFilter)||null;
  drawChart(chartCandles,chartTrade);
});

// ── Main Load ─────────────────────────────────────────────────────────────────
async function loadAll(){
  const [sigRes, coinsRes, ticker, candles] = await Promise.all([
    fetch('/api/data').then(r=>r.json()).catch(()=>null),
    fetch('/api/coins').then(r=>r.json()).catch(()=>null),
    fetchTicker(),
    fetchCandles(period),
  ]);

  if(!sigRes){ $('loading').innerHTML='<div style="color:#ff3b30">Error loading signals</div>'; return; }

  signals       = sigRes.signals || [];
  coinStats     = (coinsRes && coinsRes.coins) || [];
  openTrades    = (sigRes.stats && sigRes.stats.open_trades) || [];
  projectTotals = sigRes.project_totals || null;
  chartCandles = candles;

  if(ticker) livePrice = ticker.price;
  if(candles.length) periodOpen = candles[0].o;

  const filtered = coinFilter==='all' ? signals : signals.filter(s=>s.coin===coinFilter);
  stats = computeStatsJS(filtered);

  $('loading').style.display='none';
  $('app').style.display='block';

  _initAbsInputs();

  const chartTrade = openTrades.find(t=>coinFilter==='all'||t.coin===coinFilter)||null;
  renderHero(ticker);
  drawChart(candles, chartTrade);
  renderMetrics(coinStats, signals, projectTotals);
  renderCoinRow(coinStats);
  renderAlerts(stats);
  renderFeedTabs();
  renderFeed();
}

// Price-only refresh (every 10s)
async function refreshPrice(){
  await refreshMarketView();
}

// Countdown + full reload every 60s
function startCountdown(){
  countdown=REFRESH;
  clearInterval(cdTimer);
  cdTimer=setInterval(async ()=>{
    countdown--;
    $('hdr-countdown').textContent=countdown+'s';
    if(countdown%10===0) refreshPrice(); // partial refresh every 10s
    if(countdown<=0){ countdown=REFRESH; await loadAll(); if(currentTab==='golive') loadGoLive(); }
  },1000);
}

// Redraw chart on resize
window.addEventListener('resize',()=>{
  if(chartCandles.length){
    const chartTrade=openTrades.find(t=>coinFilter==='all'||t.coin===coinFilter)||null;
    drawChart(chartCandles,chartTrade);
  }
});

// ── Tab Switching ─────────────────────────────────────────────────────────────
let currentTab = 'live';

function switchTab(tab) {
  currentTab = tab;
  $('tab-live').classList.toggle('active', tab === 'live');
  $('tab-project').classList.toggle('active', tab === 'project');
  $('tab-golive').classList.toggle('active', tab === 'golive');
  $('tab-learnings').classList.toggle('active', tab === 'learnings');
  $('app').style.display = tab === 'live' ? 'block' : 'none';
  $('project-view').style.display = tab === 'project' ? 'block' : 'none';
  $('golive-view').style.display = tab === 'golive' ? 'block' : 'none';
  $('learnings-view').style.display = tab === 'learnings' ? 'block' : 'none';
  if (tab === 'project') loadProjectStatus();
  if (tab === 'golive') loadGoLive();
  if (tab === 'learnings') loadLearnings();
}

// ── Project Status ─────────────────────────────────────────────────────────────
async function loadProjectStatus() {
  $('ps-loading').style.display = 'block';
  $('ps-content').style.display = 'none';
  try {
    const r = await fetch('/api/project', {signal: AbortSignal.timeout(5000)});
    const d = await r.json();
    if (!r.ok || !d.ok) { $('ps-loading').innerHTML = '<div style="color:var(--red)">Error loading project data</div>'; return; }
    renderProjectStatus(d.data);
  } catch(e) {
    $('ps-loading').innerHTML = '<div style="color:var(--red)">Error loading project data</div>';
  }
}

function renderProjectStatus(data) {
  $('ps-loading').style.display = 'none';
  $('ps-content').style.display = 'block';

  const t = data.totals;
  const sessions = data.sessions;
  const sidx = data.current_session - 1;
  const sess = sessions[sidx];
  const thresholds = data.success_thresholds;
  const milestones = data.milestones;

  const wrCol = t.all_time_win_rate >= 60 ? 'var(--green)' : t.all_time_win_rate >= 45 ? 'var(--amber)' : 'var(--red)';

  const MILESTONE_LABELS = {
    engine_running:        'Engine running',
    first_win:             'First win recorded',
    paper_trading_started: 'Paper trading started',
    di_rules_added:        'DI directional rules added',
    gate_fixed:            'Gate fix applied',
    multi_news_sources:    'Multiple news sources',
    cooldown_rule:         '60 min cooldown rule',
    three_minute_cycles:   '3 minute cycles',
    cloud_server:          'Cloud server deployed',
    multi_coin:            'Multi coin tracking',
    short_signals:         'Short signals added',
    live_trading:          'Live trading started',
  };

  const currentSignalsPerDay = 4;

  function progBar(current, target, label, unit='') {
    const pct = Math.min(100, Math.round(current / target * 100));
    const done = pct >= 100;
    return `<div class="ps-prog-item">
      <div class="ps-prog-header">
        <span class="ps-prog-label">${label}</span>
        <span class="ps-prog-nums">${current}${unit} / ${target}${unit}</span>
      </div>
      <div class="ps-prog-track">
        <div class="ps-prog-fill${done?' done':''}" style="width:${pct}%"></div>
      </div>
    </div>`;
  }

  const html = `
    <!-- Section 1: Metric cards -->
    <div class="ps-section">
      <div class="ps-section-title">All Time Performance</div>
      <div class="ps-cards">
        <div class="ps-card">
          <div class="ps-card-label">Win Rate</div>
          <div class="ps-card-val" style="color:${wrCol}">${t.all_time_win_rate}%</div>
          <div class="ps-card-sub">${t.all_time_wins}W / ${t.all_time_losses}L</div>
        </div>
        <div class="ps-card">
          <div class="ps-card-label">Total Trades</div>
          <div class="ps-card-val">${t.all_time_trades}</div>
          <div class="ps-card-sub">${data.sessions_run || data.current_session} sessions</div>
        </div>
        <div class="ps-card">
          <div class="ps-card-label">Peak Capital</div>
          <div class="ps-card-val" style="color:var(--green)">${fmtUSD(t.peak_capital)}</div>
          <div class="ps-card-sub">All time high</div>
        </div>
        <div class="ps-card">
          <div class="ps-card-label">Session Capital</div>
          <div class="ps-card-val">${fmtUSD(sess.results.capital_end)}</div>
          <div class="ps-card-sub">Started ${fmtUSD(sess.results.capital_start)}</div>
        </div>
      </div>
    </div>

    <!-- Section 2: Current session -->
    <div class="ps-section">
      <div class="ps-section-title">Session ${sess.session} — ${sess.date_start} to ${sess.date_end}</div>
      <div class="ps-info-grid">
        <div class="ps-info-block"><div class="ps-info-label">Coins</div><div class="ps-info-val">${Array.isArray(sess.settings.coins)?sess.settings.coins.join(' / '):sess.settings.coin||'—'}</div></div>
        <div class="ps-info-block"><div class="ps-info-label">Confidence Threshold</div><div class="ps-info-val">${sess.settings.threshold}%</div></div>
        <div class="ps-info-block"><div class="ps-info-label">Reward : Risk</div><div class="ps-info-val">${sess.settings.reward_risk}</div></div>
        <div class="ps-info-block"><div class="ps-info-label">Cycle Minutes</div><div class="ps-info-val">${sess.settings.cycle_minutes} min</div></div>
        <div class="ps-info-block"><div class="ps-info-label">Session Trades</div><div class="ps-info-val">${sess.results.trades} &nbsp;(${sess.results.wins}W / ${sess.results.losses}L)</div></div>
        <div class="ps-info-block"><div class="ps-info-label">Session Win Rate</div><div class="ps-info-val">${sess.results.win_rate}%</div></div>
      </div>
    </div>

    <!-- Section 3: Milestones -->
    <div class="ps-section">
      <div class="ps-section-title">Milestones</div>
      <div class="ps-milestones">
        ${Object.entries(MILESTONE_LABELS).map(([k, label]) =>
          `<div class="ps-milestone">
            <span class="ms-icon" style="color:${milestones[k] ? 'var(--green)' : 'var(--t3)'}">${milestones[k] ? '&#10003;' : '&#9711;'}</span>
            <span style="color:${milestones[k] ? 'var(--t1)' : 'var(--t2)'}">${label}</span>
          </div>`
        ).join('')}
      </div>
    </div>

    <!-- Section 4: Progress bars -->
    <div class="ps-section">
      <div class="ps-section-title">Progress to Live Trading</div>
      <div class="ps-progress-list">
        ${progBar(t.all_time_win_rate, thresholds.win_rate_to_go_live, 'Win Rate', '%')}
        ${progBar(t.all_time_trades, thresholds.min_trades_to_go_live, 'Total Trades')}
        ${progBar(sess.results.capital_end, thresholds.target_capital, 'Session Capital', '')}
        ${progBar(currentSignalsPerDay, thresholds.target_signals_per_day, 'Signals per Day')}
      </div>
    </div>
  `;

  $('ps-content').innerHTML = html;
}

// ── Go-Live Tracker ────────────────────────────────────────────────────────
async function loadGoLive() {
  $('gl-loading').style.display = 'block';
  $('gl-content').style.display = 'none';
  try {
    const r = await fetch('/api/golive', {signal: AbortSignal.timeout(6000)});
    const d = await r.json();
    if (!r.ok || !d.ok) { $('gl-loading').innerHTML = '<div style="color:var(--red)">Error loading go-live data</div>'; return; }
    renderGoLive(d.data);
  } catch(e) {
    $('gl-loading').innerHTML = '<div style="color:var(--red)">Error loading go-live data</div>';
  }
}

function renderGoLive(d) {
  $('gl-loading').style.display = 'none';
  $('gl-content').style.display = 'block';

  const gl   = d.go_live_criteria;
  const tot  = d.totals;
  const sigs = d.live_stats;

  // ── Computed values ────────────────────────────────────────────────────────
  const winRate   = sigs.win_rate;
  const trades    = sigs.total_trades;
  const startDate = new Date(gl.paper_trading_start);
  const today     = new Date();
  const paperDays = Math.max(0, Math.floor((today - startDate) / 86400000));
  const stableDays = gl.current_stable_days || 0;

  // Coins profitable = coins where per_coin capital > 1000
  const perCoin = tot.per_coin || {};
  const coinsProfitable = Object.values(perCoin).filter(c => c.capital > 1000).length;

  // Max drawdown per coin (simple: lowest capital / 1000 - 1)
  const maxDrawdown = Math.max(0, ...Object.values(perCoin).map(c => Math.max(0, (1000 - c.capital) / 1000 * 100)));

  // Criteria evaluation
  const criteria = [
    {
      label: 'Win rate above 60% sustained',
      value: winRate.toFixed(1) + '%',
      target: '≥ 60%',
      pass: winRate >= 60,
      close: winRate >= 55,
    },
    {
      label: '200+ completed trades',
      value: trades,
      target: '≥ 200',
      pass: trades >= 200,
      close: trades >= 150,
    },
    {
      label: '7 consecutive stable days',
      value: stableDays + ' days',
      target: '7 days',
      pass: stableDays >= 7,
      close: stableDays >= 4,
    },
    {
      label: 'Cloud server deployed',
      value: gl.cloud_deployed ? 'Yes' : 'No',
      target: 'Deployed',
      pass: gl.cloud_deployed === true,
      close: false,
    },
    {
      label: 'Short signals paper tested 1 week',
      value: gl.short_signals_tested ? 'Yes' : 'No',
      target: 'Tested',
      pass: gl.short_signals_tested === true,
      close: false,
    },
    {
      label: 'All 4 coins profitable',
      value: coinsProfitable + ' / 4',
      target: '4 coins',
      pass: coinsProfitable >= 4,
      close: coinsProfitable >= 3,
    },
    {
      label: 'Max drawdown below 15% per coin',
      value: maxDrawdown.toFixed(1) + '%',
      target: '< 15%',
      pass: maxDrawdown < 15,
      close: maxDrawdown < 20,
    },
    {
      label: 'Percentage risk sizing active',
      value: gl.percentage_risk_active ? 'Active' : 'Fixed',
      target: 'Active',
      pass: gl.percentage_risk_active === true,
      close: false,
    },
    {
      label: '60 min cooldown rule active',
      value: gl.cooldown_rule_active ? 'Active' : 'Off',
      target: 'Active',
      pass: gl.cooldown_rule_active === true,
      close: false,
    },
  ];

  const passingCount = criteria.filter(c => c.pass).length;
  const total9 = criteria.length;

  // Verdict
  let verdictClass, verdictText;
  if (passingCount >= total9) {
    verdictClass = 'gl-verdict-ready'; verdictText = '✓ READY FOR LIVE TRADING';
  } else if (passingCount >= 6) {
    verdictClass = 'gl-verdict-close'; verdictText = `⟳ GETTING CLOSE — ${passingCount}/${total9} criteria passing`;
  } else {
    verdictClass = 'gl-verdict-no'; verdictText = `✗ NOT READY — ${passingCount}/${total9} criteria passing`;
  }

  // Per-coin capital rows
  const coinCapRows = Object.entries(perCoin).map(([name, pc]) => {
    const riskAmt = (pc.capital * 0.02).toFixed(2);
    const pnl = pc.capital - 1000;
    const col = pnl > 0 ? 'var(--green)' : pnl < 0 ? 'var(--red)' : 'var(--t2)';
    return `<div class="gl-cap-block">
      <div class="gl-cap-label">${name}</div>
      <div class="gl-cap-val" style="color:${col}">${fmtUSD(pc.capital)}</div>
      <div style="font-size:11px;color:var(--t2);margin-top:2px">Risk/trade: $${riskAmt} (2%)</div>
    </div>`;
  }).join('');

  const totalCapital = Object.values(perCoin).reduce((a, c) => a + c.capital, 0);
  const maxSimRisk   = Object.values(perCoin).reduce((a, c) => a + c.capital * 0.02 * 2, 0); // 2 positions per coin

  function glBar(current, target, label, unit='') {
    const pct = Math.min(100, Math.round(current / target * 100));
    const done = pct >= 100;
    return `<div class="gl-prog-item">
      <div class="gl-prog-header">
        <span class="gl-prog-label">${label}</span>
        <span class="gl-prog-nums">${current}${unit} / ${target}${unit}</span>
      </div>
      <div class="gl-prog-track">
        <div class="gl-prog-fill${done?' done':''}" style="width:${pct}%"></div>
      </div>
    </div>`;
  }

  // Timeline
  const week1End   = new Date(startDate); week1End.setDate(week1End.getDate() + 7);
  const week2End   = new Date(startDate); week2End.setDate(week2End.getDate() + 14);
  const week3End   = new Date(startDate); week3End.setDate(week3End.getDate() + 21);
  const goLiveDate = new Date(startDate); goLiveDate.setDate(goLiveDate.getDate() + 30);
  function fmtDate(d){ return d.toLocaleDateString('en-US',{month:'short',day:'numeric'}); }
  function tlDot(d){ return today>d?'gl-tl-dot-done':today>=new Date(d.getTime()-86400000)?'gl-tl-dot-active':'gl-tl-dot-future'; }

  const timeline = [
    {label:'Paper trading started', date: fmtDate(startDate), cls: 'gl-tl-dot-done'},
    {label:'Week 1 — baseline signals, basic stability', date: fmtDate(week1End), cls: tlDot(week1End)},
    {label:'Week 2 — short signals introduced, DI rules validated', date: fmtDate(week2End), cls: tlDot(week2End)},
    {label:'Week 3 — 21 paper days reached, stable day streak target', date: fmtDate(week3End), cls: tlDot(week3End)},
    {label:'Go-Live target — all criteria passing', date: fmtDate(goLiveDate), cls: tlDot(goLiveDate)},
  ];

  $('gl-content').innerHTML = `
    <!-- Row 1: Metric cards -->
    <div class="gl-section">
      <div class="gl-section-title">Go-Live Summary</div>
      <div class="gl-metric-cards">
        <div class="gl-metric-card">
          <div class="gl-metric-label">Win Rate</div>
          <div class="gl-metric-val" style="color:${winRate>=60?'var(--green)':winRate>=55?'var(--amber)':'var(--red)'}">${winRate.toFixed(1)}%</div>
          <div class="gl-metric-sub">target ≥ 60%</div>
        </div>
        <div class="gl-metric-card">
          <div class="gl-metric-label">Total Trades</div>
          <div class="gl-metric-val" style="color:${trades>=200?'var(--green)':trades>=150?'var(--amber)':'var(--t1)'}">${trades}</div>
          <div class="gl-metric-sub">target ≥ 200</div>
        </div>
        <div class="gl-metric-card">
          <div class="gl-metric-label">Paper Days</div>
          <div class="gl-metric-val" style="color:${paperDays>=21?'var(--green)':paperDays>=14?'var(--amber)':'var(--t1)'}">${paperDays}</div>
          <div class="gl-metric-sub">target ≥ 21 days</div>
        </div>
        <div class="gl-metric-card">
          <div class="gl-metric-label">Criteria Passing</div>
          <div class="gl-metric-val" style="color:${passingCount>=9?'var(--green)':passingCount>=6?'var(--amber)':'var(--red)'}">${passingCount} / ${total9}</div>
          <div class="gl-metric-sub">need all ${total9}</div>
        </div>
      </div>
    </div>

    <!-- Row 2: Verdict -->
    <div class="gl-section">
      <div class="gl-verdict ${verdictClass}">${verdictText}</div>
    </div>

    <!-- Row 3: Criteria checklist -->
    <div class="gl-section">
      <div class="gl-section-title">Criteria Checklist</div>
      <div class="gl-criteria-list">
        ${criteria.map(c => {
          const badge = c.pass ? 'gl-badge-pass' : c.close ? 'gl-badge-close' : 'gl-badge-no';
          const label = c.pass ? 'PASS' : c.close ? 'CLOSE' : 'NOT YET';
          return `<div class="gl-criterion">
            <div class="gl-crit-name">${c.label}</div>
            <div class="gl-crit-val">${c.value} / ${c.target}</div>
            <div class="gl-crit-badge ${badge}">${label}</div>
          </div>`;
        }).join('')}
      </div>
    </div>

    <!-- Row 4: Progress bars -->
    <div class="gl-section">
      <div class="gl-section-title">Progress</div>
      <div class="gl-progress-list">
        ${glBar(trades, 200, 'Trades completed')}
        ${glBar(paperDays, 21, 'Paper trading days')}
        ${glBar(stableDays, 7, 'Consecutive stable days')}
        ${glBar(coinsProfitable, 4, 'Coins profitable')}
        ${glBar(passingCount, total9, 'Criteria passing')}
      </div>
    </div>

    <!-- Row 5: Capital breakdown -->
    <div class="gl-section">
      <div class="gl-section-title">Capital & Risk Overview</div>
      <div class="gl-capital-grid">
        ${coinCapRows}
        <div class="gl-cap-block">
          <div class="gl-cap-label">Total Capital</div>
          <div class="gl-cap-val">${fmtUSD(totalCapital)}</div>
          <div style="font-size:11px;color:var(--t2);margin-top:2px">across all 4 coins</div>
        </div>
        <div class="gl-cap-block">
          <div class="gl-cap-label">Max Simultaneous Risk</div>
          <div class="gl-cap-val" style="color:var(--amber)">${fmtUSD(maxSimRisk)}</div>
          <div style="font-size:11px;color:var(--t2);margin-top:2px">2 positions × 2% per coin</div>
        </div>
        <div class="gl-cap-block">
          <div class="gl-cap-label">Risk Mode</div>
          <div class="gl-cap-val" style="color:var(--green)">${gl.percentage_risk_active ? '2% / 3%' : 'Fixed $20/$30'}</div>
          <div style="font-size:11px;color:var(--t2);margin-top:2px">${gl.percentage_risk_active ? 'percentage sizing' : 'legacy fixed sizing'}</div>
        </div>
      </div>
    </div>

    <!-- Row 6: Timeline -->
    <div class="gl-section">
      <div class="gl-section-title">Timeline</div>
      <div class="gl-timeline">
        ${timeline.map(t => `<div class="gl-tl-item">
          <div class="gl-tl-dot ${t.cls}"></div>
          <div class="gl-tl-body">
            <div class="gl-tl-label">${t.label}</div>
            <div class="gl-tl-date">${t.date}</div>
          </div>
        </div>`).join('')}
      </div>
    </div>
  `;
}

loadAll();
startCountdown();

// ── Learnings Tab ─────────────────────────────────────────────────────────────
let lrCoin = 'ETH';
let lrData = null;

async function loadLearnings() {
  $('lr-loading').style.display = 'block';
  $('lr-content').style.display = 'none';
  try {
    const r = await fetch('/api/learnings', {signal: AbortSignal.timeout(5000)});
    const d = await r.json();
    if (!r.ok || !d.ok) { $('lr-loading').innerHTML = '<div style="color:var(--red)">Error loading learnings</div>'; return; }
    lrData = d.data;
    renderLearnings();
  } catch(e) {
    $('lr-loading').innerHTML = '<div style="color:var(--red)">Error loading learnings</div>';
  }
}

function setLrCoin(coin) {
  lrCoin = coin;
  document.querySelectorAll('.lr-coin-btn').forEach(b => b.classList.toggle('active', b.dataset.coin === coin));
  renderLearningsCard();
}

function renderLearnings() {
  $('lr-loading').style.display = 'none';
  $('lr-content').style.display = 'block';
  const COINS_ORDER = ['ETH','SOL','XRP','AVAX'];
  $('lr-content').innerHTML = `
    <div class="lr-coin-filter">
      ${COINS_ORDER.map(c => `<button class="lr-coin-btn${c===lrCoin?' active':''}" data-coin="${c}" onclick="setLrCoin('${c}')">${c}</button>`).join('')}
    </div>
    <div id="lr-card-area"></div>`;
  renderLearningsCard();
}

function renderLearningsCard() {
  const area = document.getElementById('lr-card-area');
  if (!area || !lrData) return;
  const cd = lrData[lrCoin];
  if (!cd) { area.innerHTML = `<div class="lr-card"><div class="lr-empty">No learning data for ${lrCoin}.</div></div>`; return; }
  if (cd.error) { area.innerHTML = `<div class="lr-card"><div class="lr-empty" style="color:var(--red)">Learning data unavailable: ${cd.error}</div></div>`; return; }
  if (!cd.current) {
    area.innerHTML = `<div class="lr-card"><div class="lr-card-header"><span class="lr-coin-name">${lrCoin}</span></div><div class="lr-empty">No learnings yet — need 10+ completed trades before patterns appear.</div></div>`;
    return;
  }
  const cur = cd.current;
  const hist = cd.history || [];
  const patterns = cur.patterns || [];

  function badge(wr) {
    if (wr >= 70) return `<span class="lr-badge lr-badge-favor">FAVOR</span>`;
    if (wr >= 40) return `<span class="lr-badge lr-badge-neutral">NEUTRAL</span>`;
    if (wr >= 25) return `<span class="lr-badge lr-badge-caution">CAUTION</span>`;
    return `<span class="lr-badge lr-badge-avoid">AVOID</span>`;
  }
  function wrClass(wr) {
    if (wr >= 70) return 'lr-wr-green';
    if (wr >= 40) return 'lr-wr-yellow';
    return 'lr-wr-red';
  }

  const patternsHtml = patterns.length ? `
    <table class="lr-table">
      <thead><tr>
        <th>Signal</th><th>Condition</th><th>W</th><th>L</th><th>Win Rate</th><th>Conf</th><th>Recommendation</th>
      </tr></thead>
      <tbody>
        ${patterns.map(p => `<tr class="${wrClass(p.win_rate||0)}">
          <td>${badge(p.win_rate||0)}</td>
          <td style="font-family:inherit;color:var(--t1)">${p.condition||''}</td>
          <td style="color:var(--green)">${p.wins||0}</td>
          <td style="color:var(--red)">${p.losses||0}</td>
          <td style="font-weight:600">${p.win_rate||0}%</td>
          <td style="color:var(--t2)">${p.confidence||''}</td>
          <td style="font-family:inherit;color:var(--t2)">${p.recommendation||''}</td>
        </tr>`).join('')}
      </tbody>
    </table>` : '<div class="lr-empty">No patterns yet.</div>';

  const histHtml = hist.length ? `
    <span class="lr-history-toggle" onclick="toggleLrHistory(this)">Show learning history (${Math.min(hist.length,20)} updates)</span>
    <table class="lr-history-table">
      <thead><tr><th>Time</th><th>Trades</th><th>WR</th><th>Summary</th></tr></thead>
      <tbody>
        ${[...hist].reverse().slice(0,20).map(h=>`<tr>
          <td>${h.timestamp||''}</td>
          <td>${h.trade_count||''}</td>
          <td>${h.overall_win_rate!=null?h.overall_win_rate+'%':''}</td>
          <td>${h.summary||''}</td>
        </tr>`).join('')}
      </tbody>
    </table>` : '';

  area.innerHTML = `<div class="lr-card">
    <div class="lr-card-header">
      <span class="lr-coin-name">${lrCoin}</span>
      <span class="lr-meta">Last updated: ${cur.generated_at||'—'}</span>
      <span class="lr-meta">${cur.trade_count||0} trades analyzed</span>
      <span class="lr-meta">${cur.overall_win_rate!=null?cur.overall_win_rate+'%':''} overall WR</span>
    </div>
    ${patternsHtml}
    <div class="lr-setup">
      <div>Best long setup: <span>${cur.strongest_long_setup||'—'}</span></div>
      <div>Best short setup: <span>${cur.strongest_short_setup||'—'}</span></div>
    </div>
    ${cur.summary?`<div class="lr-summary">${cur.summary}</div>`:''}
    ${histHtml}
  </div>`;
}

function toggleLrHistory(el) {
  const tbl = el.nextElementSibling;
  const showing = tbl.style.display === 'table';
  tbl.style.display = showing ? 'none' : 'table';
  el.textContent = showing ? el.textContent.replace('Hide','Show') : el.textContent.replace('Show','Hide');
}
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
            query     = parse_qs(parsed.query)
            coin_filter = query.get("coin", ["all"])[0]
            sigs = read_signals()
            if coin_filter != "all":
                sigs = [s for s in sigs if s.get("coin") == coin_filter]
            st   = compute_stats(sigs)
            project_totals = None
            try:
                log_path = os.path.join(_BASE, "project_log.json")
                with open(log_path, "r") as f:
                    project_totals = json.load(f).get("totals")
            except Exception:
                pass
            payload = json.dumps(
                {
                    "signals":          sigs,
                    "stats":            st,
                    "starting_capital": COIN_CAPITAL_START * 4,
                    "updated":          datetime.now().isoformat(),
                    "project_totals":   project_totals,
                },
                default=str,
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        elif parsed.path == "/api/market":
            query = parse_qs(parsed.query)
            coin = query.get("coin", ["ETH"])[0].upper()
            if query.get("ticker"):
                ticker = fetch_market_ticker(coin=coin)
                payload = json.dumps({"ok": bool(ticker), "ticker": ticker}).encode("utf-8")
            else:
                period = query.get("period", ["4H"])[0]
                candles = fetch_market_candles(period, coin=coin)
                payload = json.dumps({"ok": bool(candles), "candles": candles}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        elif parsed.path == "/api/coins":
            coin_stats = [
                compute_coin_stats(name, path)
                for name, path in COIN_CSV_FILES.items()
            ]
            payload = json.dumps({"ok": True, "coins": coin_stats}, default=str).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        elif parsed.path == "/api/golive":
            try:
                log_path = os.path.join(_BASE, "project_log.json")
                with open(log_path, "r") as f:
                    project_data = json.load(f)
                # Compute live stats from CSVs
                all_sigs = read_signals()
                tradeable = [s for s in all_sigs if s.get("signal") in ("Buy", "Sell")]
                wins   = [s for s in tradeable if s.get("outcome") == "W"]
                losses = [s for s in tradeable if s.get("outcome") == "L"]
                completed = len(wins) + len(losses)
                wr = round(len(wins) / completed * 100, 1) if completed > 0 else 0
                live_stats = {
                    "total_trades": completed,
                    "wins": len(wins),
                    "losses": len(losses),
                    "win_rate": wr,
                }
                payload = json.dumps({
                    "ok": True,
                    "data": {
                        **project_data,
                        "live_stats": live_stats,
                    }
                }, default=str).encode("utf-8")
            except Exception as e:
                payload = json.dumps({"ok": False, "error": str(e)}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        elif parsed.path == "/api/learnings":
            LEARNING_COINS = ["ETH", "SOL", "XRP", "AVAX"]
            result = {}
            for coin in LEARNING_COINS:
                prefix = coin.lower()
                cur_path  = os.path.join(_BASE, f"{prefix}_learning.json")
                hist_path = os.path.join(_BASE, f"{prefix}_learning_history.json")
                coin_data = {}
                if not os.path.exists(cur_path):
                    result[coin] = {"current": None, "history": []}
                    continue
                try:
                    with open(cur_path, "r") as f:
                        coin_data["current"] = json.load(f)
                except Exception as e:
                    coin_data["error"] = str(e)
                    result[coin] = coin_data
                    continue
                try:
                    with open(hist_path, "r") as f:
                        coin_data["history"] = json.load(f)
                except Exception:
                    coin_data["history"] = []
                result[coin] = coin_data
            payload = json.dumps({"ok": True, "data": result}, default=str).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        elif parsed.path == "/api/project":
            try:
                log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "project_log.json")
                with open(log_path, "r") as f:
                    project_data = json.load(f)
                payload = json.dumps({"ok": True, "data": project_data}).encode("utf-8")
            except Exception as e:
                payload = json.dumps({"ok": False, "error": str(e)}).encode("utf-8")
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


class ReusableTCPServer(HTTPServer):
    def server_bind(self):
        import socket
        self.socket.setsockopt(
            socket.SOL_SOCKET, socket.SO_REUSEADDR, 1
        )
        super().server_bind()


if __name__ == "__main__":
    url = f"http://localhost:{PORT}"
    server = ReusableTCPServer(("localhost", PORT), DashboardHandler)
    threading.Thread(target=_open_browser, args=(url,), daemon=True).start()
    print(f"Dashboard: {url}")
    print("Coins:     ETH | SOL | AVAX | XRP")
    print("Ctrl+C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.shutdown()
