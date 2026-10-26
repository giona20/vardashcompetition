"""
Monitor di alert per la Variational PnL Card Competition.

Gira in un terminale separato dalla dashboard:
    python alert_monitor.py            # giorno automatico
    python alert_monitor.py day2       # forza un giorno

Manda su Discord e/o Telegram (vedi ALERTS in config.py):
  - il piano del giorno (una volta)
  - apertura della finestra di ingresso di ogni titolo
  - countdown al report (X minuti prima)
  - movimento post-report oltre le soglie (3/5/8/12/20 %)
  - per le posizioni in positions.json: nuovo massimo di ROE, ritracciamento dal massimo,
    vicinanza alla liquidazione
Lo stato (alert già inviati, prezzi di riferimento) è in alerts_state.json, così un riavvio
non rimanda tutto.
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

import engine as eg
from config import ALERTS, DAYS, DEFAULTS

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "alerts_state.json")
POSITIONS_FILE = os.path.join(HERE, "positions.json")
UTC = timezone.utc


# ---------------------------------------------------------------------------
# invio
# ---------------------------------------------------------------------------
def send(msg: str) -> None:
    stamp = datetime.now(UTC).strftime("%H:%M:%S UTC")
    print(f"[{stamp}] {msg.replace(chr(10), ' | ')}")
    url = ALERTS.get("discord_webhook_url")
    if url and "YOUR_" not in url:
        try:
            requests.post(url, json={"content": msg, "username": "Variational Bot"}, timeout=10)
        except Exception as e:
            print(f"  discord KO: {e}")
    tok, chat = ALERTS.get("telegram_bot_token"), ALERTS.get("telegram_chat_id")
    if tok and chat:
        try:
            requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                          json={"chat_id": chat, "text": msg}, timeout=10)
        except Exception as e:
            print(f"  telegram KO: {e}")


# ---------------------------------------------------------------------------
# stato
# ---------------------------------------------------------------------------
def load_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: str, data) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)


def once(state: dict, key: str) -> bool:
    """True la prima volta che viene chiamata con quella chiave."""
    sent = state.setdefault("sent", {})
    if key in sent:
        return False
    sent[key] = datetime.now(UTC).isoformat()
    return True


# ---------------------------------------------------------------------------
# blocchi di controllo
# ---------------------------------------------------------------------------
def daily_plan(day: dict, state: dict) -> None:
    if not once(state, f"plan:{day['key']}"):
        return
    res = eg.analyze_day(day)
    pk = eg.picks(res)
    lines = [f"📋 Piano {day['label']} ({day['date']})"]
    for r in res:
        if "error" in r:
            lines.append(f"  {r['ticker']}: errore dati")
            continue
        lines.append(f"  {r['ticker']:5} {r['report']}  exp.move ±{eg._f(r['score_implied_move_pct'], 0):.1f}%  "
                     f"opp {r['score_opportunity']}/10  {r['score_direction']} ({r['score_conviction']})")
    if pk:
        lines.append(f"🏆 Premio %: {pk['pct']['ticker']}   💵 Premio $: {pk['usd']['ticker']}   "
                     f"🧭 Direzione più convinta: {pk['direction']['ticker']} {pk['direction']['score_direction']}")
    send("\n".join(lines))


def timing_alerts(day: dict, state: dict, now: datetime) -> None:
    warn = timedelta(minutes=int(ALERTS["report_warning_minutes"]))
    for s in day["stocks"]:
        tg = eg.timing_guidance(s, day["date"], now)
        if tg["state"] == "finestra di ingresso" and once(state, f"entry:{s['ticker']}:{day['date']}"):
            send(f"🟢 {s['ticker']}: finestra di ingresso aperta fino alle {tg['entry_window'][1].strftime('%H:%M')} UTC "
                 f"(report {s['report']} alle {tg['report_at'].strftime('%H:%M')} UTC)")
        if timedelta(0) < tg["to_report"] <= warn and once(state, f"soon:{s['ticker']}:{day['date']}"):
            send(f"⏰ {s['ticker']}: report tra {int(tg['to_report'].total_seconds() // 60)} minuti. "
                 f"Ultima chiamata per entrare o per decidere di restare fuori.")


def post_report_moves(day: dict, state: dict, now: datetime) -> None:
    refs = state.setdefault("ref_price", {})
    for s in day["stocks"]:
        tg = eg.timing_guidance(s, day["date"], now)
        if tg["state"] not in ("reazione in corso", "evento passato"):
            continue
        if tg["to_report"] < -timedelta(hours=18):
            continue  # troppo vecchio
        sym = eg.yf_symbol(s)
        key = f"{s['ticker']}:{day['date']}"
        lp = eg.live_price(sym)
        price = eg._f(lp["price"])
        if math.isnan(price):
            continue
        if key not in refs:
            # riferimento = chiusura regolare del giorno del report (AMC) o chiusura precedente (BMO)
            try:
                h = eg.fetch_history(sym, period="5d")
                ref = float(h["Close"].iloc[-1]) if not h.empty else price
            except Exception:
                ref = price
            refs[key] = ref
            continue
        move = (price / refs[key] - 1) * 100
        for th in ALERTS["move_thresholds_pct"]:
            if abs(move) >= th and once(state, f"move:{key}:{th}"):
                send(f"🚀 {s['ticker']} {move:+.1f}% dal riferimento {refs[key]:.2f} → ora {price:.2f} ({lp['source']}). "
                     f"Soglia {th}% superata.")
                break


def position_alerts(state: dict) -> None:
    positions = load_json(POSITIONS_FILE, [])
    peaks = state.setdefault("peaks", {})
    mmr = DEFAULTS["maintenance_margin"]
    for p in positions:
        if not p.get("Attiva") or eg._f(p.get("Entry"), 0) <= 0:
            continue
        stock = next((s for d in DAYS for s in d["stocks"] if s["ticker"] == p["Ticker"]), {"ticker": p["Ticker"]})
        lp = eg.live_price(eg.yf_symbol(stock))
        price = eg._f(lp["price"]) * lp.get("fx_to_usd", 1.0)
        if math.isnan(price):
            continue
        entry, lev = float(p["Entry"]), max(float(p.get("Leva", 1)), 1.0)
        sign = 1 if p["Lato"] == "LONG" else -1
        move = (price / entry - 1) * 100 * sign
        roe = move * lev
        key = f"{p['Ticker']}|{p['Lato']}|{entry}"
        prev_peak = peaks.get(key, -1e9)
        if roe > prev_peak:
            peaks[key] = roe
            if roe > 0 and roe - max(prev_peak, 0) >= ALERTS["peak_step_pct"]:
                send(f"📸 {p['Ticker']} {p['Lato']}: nuovo massimo ROE {roe:+.1f}% (prezzo {price:.2f}). Buon momento per la card.")
        elif prev_peak > 0 and prev_peak - roe >= prev_peak * DEFAULTS["peak_retrace_alert"]:
            if once(state, f"retrace:{key}:{int(prev_peak)}"):
                send(f"⚠️ {p['Ticker']} {p['Lato']}: ROE {roe:+.1f}%, ritracciato dal massimo {prev_peak:+.1f}%. "
                     f"Valuta di postare la card o chiudere.")
        liq_move = -(1 / lev - mmr) * 100  # movimento (nel verso contrario) che liquida
        if move < 0 and move <= liq_move * (1 - ALERTS["liq_proximity"]):
            if once(state, f"liq:{key}:{int(move)}"):
                send(f"🛑 {p['Ticker']} {p['Lato']}: move {move:+.1f}% su leva {lev:g}x, liquidazione stimata a {liq_move:.1f}%. "
                     f"Sei vicino: riduci o chiudi.")


# ---------------------------------------------------------------------------
# main loop
# ---------------------------------------------------------------------------
def main() -> None:
    forced = sys.argv[1] if len(sys.argv) > 1 else None
    state = load_json(STATE_FILE, {})
    interval = int(ALERTS["check_interval_seconds"])
    send("🤖 Monitor avviato" + (f" (giorno forzato: {forced})" if forced else ""))
    while True:
        try:
            now = eg.now_utc()
            key = forced or eg.current_day_key(now)
            day = next(d for d in DAYS if d["key"] == key)
            daily_plan(day, state)
            timing_alerts(day, state, now)
            post_report_moves(day, state, now)
            position_alerts(state)
            save_json(STATE_FILE, state)
        except KeyboardInterrupt:
            print("\nmonitor fermato")
            break
        except Exception as e:
            print(f"errore nel ciclo: {e}")
        time.sleep(interval)


if __name__ == "__main__":
    main()
