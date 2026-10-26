"""
Variational Earnings War Room - dashboard Streamlit.
Avvio:  streamlit run app.py
"""
from __future__ import annotations
import json
import math
import os
from datetime import datetime, timedelta, timezone
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import engine as eg
from config import COMPETITION, DAYS, DEFAULTS

UTC = timezone.utc
DAY_BY_KEY = {d["key"]: d for d in DAYS}
POSITIONS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "positions.json")

st.set_page_config(page_title="Variational Earnings War Room", page_icon="🎯", layout="wide")
st.markdown(
"""
<style>
html, body, [class*="css"] { font-variant-numeric: tabular-nums; }
.ticket { border: 1px solid rgba(128,128,128,.35); border-radius: 10px; padding: 14px 18px; margin-bottom: 8px; }
.ticket h3 { margin: 0 0 2px 0; font-size: 1.35rem; }
.ticket .sub { opacity: .7; font-size: .85rem; margin-bottom: 8px; }
.ticket .big { font-size: 2.1rem; font-weight: 700; line-height: 1.1; }
.ticket .row { display: flex; gap: 18px; flex-wrap: wrap; margin-top: 8px; }
.ticket .row div { min-width: 120px; }
.ticket .k { opacity: .65; font-size: .78rem; }
.ticket .v { font-size: 1.05rem; font-weight: 600; }
.long { color: #1a9c5b; } .short { color: #d43f3f; } .neutro { color: #b08a1e; }
.warn { color: #d43f3f; font-weight: 600; }
</style>
""",
unsafe_allow_html=True,
)

# ==============================================================================
# Helpers
# ==============================================================================
def fmt_td(td: timedelta) -> str:
    s = int(td.total_seconds())
    sign = "-" if s < 0 else ""
    s = abs(s)
    d, h, m = s // 86400, (s % 86400) // 3600, (s % 3600) // 60
    return f"{sign}{d}g {h}h {m:02d}m" if d else f"{sign}{h}h {m:02d}m"

def fmt_utc(dt: datetime | None) -> str:
    if dt is None:
        return "n/d"
    return dt.astimezone(UTC).strftime("%d/%m %H:%M UTC")

def nz(x, default=np.nan) -> float:
    try:
        v = float(x)
        return default if math.isnan(v) else v
    except (TypeError, ValueError):
        return default

def dir_class(direction: str) -> str:
    return {"LONG": "long", "SHORT": "short"}.get(direction, "neutro")

@st.cache_data(ttl=600, show_spinner=False)
def run_day(day_key: str, notional: float, leverage: float, mmr: float, lookback: int) -> list[dict]:
    return eg.analyze_day(DAY_BY_KEY[day_key], notional=notional, leverage=leverage, mmr=mmr, lookback=lookback)

@st.cache_data(ttl=10, show_spinner=False)
def live(symbol: str) -> dict:
    return eg.live_price(symbol)

def load_positions() -> list[dict]:
    try:
        with open(POSITIONS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save_positions(rows: list[dict]) -> None:
    try:
        with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=2, default=str)
    except Exception as e:
        st.warning(f"Non riesco a salvare positions.json: {e}")

# ==============================================================================
# Sidebar
# ==============================================================================
with st.sidebar:
    st.header("Parametri")
    day_choice = st.radio("Giorno di gara", ["Auto"] + [d["label"] for d in DAYS], key="day_choice")
    notional = st.number_input("Notional ($)", min_value=float(COMPETITION["min_notional_usd"]),
                               value=float(DEFAULTS["notional_usd"]), step=100.0, key="notional_val")
    leverage = st.slider("Leva", 1.0, 20.0, float(DEFAULTS["leverage"]), 0.5, key="leverage_val")
    mmr = st.number_input("Maintenance margin (%)", 0.1, 10.0, DEFAULTS["maintenance_margin"] * 100, 0.1,
                          help="Approssimazione per stimare la liquidazione: verifica il valore reale su Variational.",
                          key="mmr_val") / 100
    lookback = st.slider("Trimestrali passate da analizzare", 4, 12, DEFAULTS["lookback_quarters"], key="lookback_val")
    st.divider()
    auto = st.toggle("Aggiornamento live", value=True, key="auto_toggle")
    refresh = st.slider("Ogni (secondi)", 10, 120, DEFAULTS["refresh_seconds"], 5, disabled=not auto, key="refresh_val")
    if st.button("Ricalcola analisi", help="Svuota la cache e rifà l'analisi completa (opzioni, storico...)"):
        st.cache_data.clear()
        st.rerun()
    st.divider()
    with st.expander("Regole della gara"):
        st.markdown(
            f"- Posizione da **${COMPETITION['min_notional_usd']:,}+ notional** aperta dopo il "
            f"{fmt_utc(COMPETITION['positions_open_after'])}\n"
            f"- Posta la PnL card su X e tagga **{COMPETITION['x_handle']}**\n"
            f"- Invia il form entro il {fmt_utc(COMPETITION['submission_deadline'])}\n"
            f"- Premi al giorno: **${COMPETITION['prizes_per_day']['largest_pct_gain']:,}** (maggior % gain), "
            f"**${COMPETITION['prizes_per_day']['largest_usd_gain']:,}** (maggior $ gain), "
            f"**${COMPETITION['prizes_per_day']['best_post']:,}** (miglior post)"
        )

run_every = timedelta(seconds=refresh) if auto else None
now = eg.now_utc()
day_key = eg.current_day_key(now) if day_choice == "Auto" else next(d["key"] for d in DAYS if d["label"] == day_choice)
day = DAY_BY_KEY[day_key]

# ==============================================================================
# Header + striscia live
# ==============================================================================
st.title("Variational Earnings War Room")
st.caption("Analisi earnings per la PnL Card Competition · non è consulenza finanziaria · una trimestrale resta ~50/50 sulla direzione")

@st.fragment(run_every=run_every)
def live_strip(day: dict) -> None:
    now = eg.now_utc()
    status = eg.competition_status(now)
    phase_txt = {"pre": f"La gara apre tra {fmt_td(status['to_start'])}",
                 "open": f"Gara aperta · form entro {fmt_td(status['to_deadline'])}",
                 "closed": "Gara chiusa"}[status["phase"]]
    upcoming = [(s, eg.timing_guidance(s, day["date"], now)) for s in day["stocks"]]
    nxt = min((t for t in upcoming if t[1]["to_report"].total_seconds() > 0),
              key=lambda t: t[1]["to_report"], default=None)
    c1, c2, c3 = st.columns([1, 1.4, 1.6])
    c1.metric("Ora UTC", now.strftime("%H:%M:%S"), now.strftime("%a %d %b"))
    c2.metric("Stato gara", status["phase"].upper(), phase_txt)
    if nxt:
        c3.metric(f"Prossimo report: {nxt[0]['ticker']}", fmt_td(nxt[1]["to_report"]),
                  f"{nxt[0]['report']} · {fmt_utc(nxt[1]['report_at'])}")
    else:
        c3.metric("Prossimo report", "—", "tutti i report del giorno sono usciti")

    cols = st.columns(len(day["stocks"]))
    for col, s in zip(cols, day["stocks"]):
        lp = live(eg.yf_symbol(s))
        p = nz(lp["price"])
        chg = nz(lp["change_pct"])
        src = {"pre": "pre-market", "post": "after-hours", "regular": "regular", "close": "chiusura", "last": "ultimo"}.get(lp["source"], "n/d")
        cur = lp.get("currency", "USD")
        val = "n/d" if math.isnan(p) else (f"{p:,.2f}" if cur == "USD" else f"{p:,.0f} {cur}")
        col.metric(f"{s['ticker']} · {src}", val, None if math.isnan(chg) else f"{chg:+.2f}%")

live_strip(day)

# ==============================================================================
# Analisi (cache 10 min)
# ==============================================================================
with st.spinner(f"Analisi {day['label']} in corso (opzioni, storico trimestrali, tecnica)..."):
    results = run_day(day_key, float(notional), float(leverage), float(mmr), int(lookback))

pk = eg.picks(results)
ok_results = [r for r in results if "error" not in r]
tab_now, tab_strategy, tab_analysis, tab_pos, tab_post = st.tabs(
    ["Cosa fare oggi", "Strategia multi-account", "Analisi completa", "Posizioni e PnL card", "Post e invio"])

# ==============================================================================
# TAB 1 - Cosa fare oggi
# ==============================================================================
def ticket(r: dict, title: str, why: str) -> None:
    d = r["score_direction"]
    em = nz(r["score_implied_move_pct"])
    side = d if d != "NEUTRO" else "LONG"
    plan = eg.position_plan(nz(r["price_usd"]), float(notional), float(leverage), float(mmr), side, em)
    tg = eg.timing_guidance(next(s for s in day["stocks"] if s["ticker"] == r["ticker"]), day["date"])
    liq_flag = (' <span class="warn">⚠️ liquidato</span>' if plan.get("liquidated_at_1_5x_implied") else "ok")
    dir_txt = {"LONG": "LONG", "SHORT": "SHORT"}.get(d, "NEUTRO · coin flip")
    html = f"""
<div class="ticket">
 <div class="sub">{title}</div>
 <h3>{r['ticker']} <span style="opacity:.6;font-weight:400">{r['name']}</span></h3>
 <div class="sub">{why}</div>
 <div class="big {dir_class(d)}">{dir_txt} <span style="font-size:1rem;opacity:.7">convinzione {r['score_conviction']}/100</span></div>
 <div class="row">
  <div><div class="k">Exp. move</div><div class="v">±{em:.1f}%</div><div class="k">{r['score_implied_source']}</div></div>
  <div><div class="k">Prezzo</div><div class="v">{nz(r['price_usd']):,.2f} $</div><div class="k">{r['price_source']}</div></div>
  <div><div class="k">Opportunità</div><div class="v">{r['score_opportunity']}/10</div><div class="k">reaz. media {nz(r.get('rx_avg_abs_move')):.1f}%</div></div>
 </div>
 <div class="row">
  <div><div class="k">Piano</div><div class="v">${notional:,.0f} · {leverage:g}x</div><div class="k">margine ${plan.get('margin_usd', 0):,.0f} · {plan.get('units', 0):.3f} unità</div></div>
  <div><div class="k">Exp. move a favore</div><div class="v long">+${plan.get('usd_at_implied', 0):,.0f} · ROE +{plan.get('roe_at_implied_pct', 0):.0f}%</div></div>
  <div><div class="k">1.5× exp. move contro</div><div class="v short">-${plan.get('usd_loss_at_1_5x_implied', 0):,.0f} · ROE -{plan.get('roe_loss_at_1_5x_implied_pct', 0):.0f}%</div><div class="k">liquidazione a {plan.get('liq_move_pct', 0):+.0f}% → {liq_flag}</div></div>
  <div><div class="k">Leva max prudente</div><div class="v">{plan.get('max_safe_leverage', '-')}x</div><div class="k">sopravvive a 1.5× exp. move</div></div>
 </div>
 <div class="row">
  <div><div class="k">Ingresso</div><div class="v">{tg['entry_window'][0].strftime('%H:%M')}–{tg['entry_window'][1].strftime('%H:%M')} UTC</div><div class="k">{tg['state']}</div></div>
  <div><div class="k">Report</div><div class="v">{fmt_utc(tg['report_at'])}</div><div class="k">{r['report']}</div></div>
  <div><div class="k">Card</div><div class="k" style="max-width:380px">{tg['card_hint']}</div></div>
 </div>
</div>
"""
    st.markdown(html, unsafe_allow_html=True)
    if d == "NEUTRO":
        st.info(
            f"Nessun fattore spinge chiaramente {r['ticker']} in una direzione. Due modi di giocarla: "
            "(A) scegli tu il lato e accetta il coin flip sul movimento pieno; "
            "(B) entra **dopo** il print nella direzione del gap after-hours: movimento più piccolo ma direzione già nota "
            "(post-earnings drift)."
        )
    with st.expander(f"Perché {r['ticker']}: dettaglio fattori"):
        if r.get("factors"):
            st.dataframe(pd.DataFrame(r["factors"]), hide_index=True, width="stretch")
        else:
            st.write("Nessun fattore direzionale disponibile.")

with tab_now:
    status = eg.competition_status(now)
    if status["phase"] == "pre":
        st.warning(f"Posizioni valide solo dopo il {fmt_utc(COMPETITION['positions_open_after'])}. Prepara il piano, non aprire ancora.")
    elif status["phase"] == "closed":
        st.error("La finestra della gara è chiusa.")

    mism = [r for r in ok_results if r.get("date_mismatch")]
    for r in mism:
        st.warning(f"{r['ticker']}: Yahoo indica earnings il {r['yahoo_earnings_date']}, il calendario di gara dice {day['date']}. Verifica prima di entrare.")

    for r in ok_results:
        if r.get("note"):
            st.caption(f"{r['ticker']}: {r['note']}")

    if not pk:
        st.error("Nessun dato utilizzabile per questo giorno. Prova 'Ricalcola analisi'.")
    else:
        st.subheader(f"{day['label']} · {day['date']}")
        c1, c2 = st.columns(2)
        with c1:
            ticket(pk["pct"], "Per il premio % gain",
                   "Il movimento atteso più grande del giorno: con leva, la % sulla card cresce. Rischio simmetrico.")
        with c2:
            if pk["usd"]["ticker"] != pk["pct"]["ticker"]:
                ticket(pk["usd"], "Per il premio $ gain",
                       "Movimento grande E storicamente affidabile: qui ha senso mettere più notional e meno leva.")
            else:
                st.markdown('<div class="ticket"><div class="sub">Per il premio $ gain</div><h3>Stesso titolo</h3>'
                            '<div class="sub">Il $ gain è notional × movimento: con lo stesso titolo vince chi mette più capitale. '
                            'Alza il notional e abbassa la leva.</div></div>', unsafe_allow_html=True)
                if pk["direction"]["ticker"] != pk["pct"]["ticker"]:
                    ticket(pk["direction"], "Direzione più convinta del giorno",
                           "Il titolo con più fattori concordi sulla direzione (resta una scommessa).")

        st.subheader("Piano della giornata")
        rows = []
        for s in day["stocks"]:
            tg = eg.timing_guidance(s, day["date"], now)
            r = next((x for x in ok_results if x["ticker"] == s["ticker"]), None)
            rows.append({
                "Ticker": s["ticker"], "Nome": s.get("name", ""), "Report": s["report"],
                "Orario report (UTC)": tg["report_at"].strftime("%d/%m %H:%M"),
                "Finestra ingresso (UTC)": f"{tg['entry_window'][0].strftime('%H:%M')}–{tg['entry_window'][1].strftime('%H:%M')}",
                "Stato": tg["state"],
                "Exp. move %": nz(r["score_implied_move_pct"]) if r else np.nan,
                "Direzione": r["score_direction"] if r else "n/d",
                "Data Yahoo": str(r["yahoo_earnings_date"]) if r and r.get("yahoo_earnings_date") else "n/d",
            })
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                     column_config={"Exp. move %": st.column_config.NumberColumn(format="%.1f")})

# ==============================================================================
# TAB STRATEGIA - Multi-account delta con TP/SL
# ==============================================================================
ACCOUNT_PROFILES = [
    {"account": "A", "ruolo": "Caccia al % gain · aggressivo", "lato": "LONG",
     "tp_x": 1.75, "sl_x": 0.45, "notional_x": 1.0, "leva": 5.0},
    {"account": "B", "ruolo": "Caccia al $ gain · più capitale, meno leva", "lato": "LONG",
     "tp_x": 1.00, "sl_x": 0.70, "notional_x": 2.0, "leva": 3.0},
    {"account": "C", "ruolo": "Hedge · lato opposto (copre il coin flip)", "lato": "SHORT",
     "tp_x": 1.75, "sl_x": 0.45, "notional_x": 1.0, "leva": 5.0},
]

def strategy_rows(r: dict, base_notional: float) -> list[dict]:
    price = nz(r.get("price_usd"))
    em = nz(r.get("score_implied_move_pct"))
    if math.isnan(price) or price <= 0 or math.isnan(em) or em <= 0:
        return []
    rows = []
    for pf in ACCOUNT_PROFILES:
        sign = 1 if pf["lato"] == "LONG" else -1
        tp_pct = em * pf["tp_x"]
        sl_pct = em * pf["sl_x"]
        rows.append({
            "Account": pf["account"], "Ruolo": pf["ruolo"], "Ticker": r["ticker"],
            "Lato": pf["lato"], "Entry": price,
            "TP": price * (1 + sign * tp_pct / 100),
            "SL": price * (1 - sign * sl_pct / 100),
            "Notional": base_notional * pf["notional_x"], "Leva": pf["leva"],
            "max_gain_usd": base_notional * pf["notional_x"] * tp_pct / 100,
            "max_loss_usd": base_notional * pf["notional_x"] * sl_pct / 100,
        })
    return rows

def strategy_live_df(rows: list[dict]) -> pd.DataFrame:
    out = []
    for p in rows:
        stock = next((s for d in DAYS for s in d["stocks"] if s["ticker"] == p["Ticker"]), {"ticker": p["Ticker"]})
        lp = live(eg.yf_symbol(stock))
        px = nz(lp["price"]) * lp.get("fx_to_usd", 1.0)
        sign = 1 if p["Lato"] == "LONG" else -1
        if math.isnan(px):
            stato, move, pnl, roe, d_tp, d_sl = "prezzo n/d", np.nan, np.nan, np.nan, np.nan, np.nan
        else:
            move = (px / p["Entry"] - 1) * 100 * sign
            pnl = p["Notional"] * move / 100
            roe = move * p["Leva"]
            tp_hit = (sign > 0 and px >= p["TP"]) or (sign < 0 and px <= p["TP"])
            sl_hit = (sign > 0 and px <= p["SL"]) or (sign < 0 and px >= p["SL"])
            stato = "🎯 TP HIT" if tp_hit else ("🛑 SL HIT" if sl_hit else "in corso")
            d_tp = (p["TP"] / px - 1) * 100 * sign
            d_sl = (px / p["SL"] - 1) * 100 * sign
        out.append({**p, "Ultimo": px, "Move %": move, "PnL $": pnl, "ROE %": roe,
                    "Dist. TP %": d_tp, "Dist. SL %": d_sl, "Stato": stato})
    return pd.DataFrame(out)

with tab_strategy:
    st.subheader("Strategia multi-account · delta con TP/SL")
    st.caption("3 account × ticker = posizioni con TP/SL sfalsati per catturare il movimento massimo in entrambe "
               "le direzioni. Livelli = multipli dell'expected move (A/C = 1.75×, B = 1.0×).")
    if st.session_state.pop("strategy_loaded_msg", None):
        st.success(st.session_state.pop("strategy_loaded_msg"))
    if not ok_results:
        st.error("Nessun dato utilizzabile per costruire la strategia.")
    else:
        opts = [r["ticker"] for r in ok_results]
        defaults = []
        if pk:
            for cand in (pk["pct"]["ticker"], pk["direction"]["ticker"], pk["usd"]["ticker"]):
                if cand not in defaults:
                    defaults.append(cand)
        sel = st.multiselect("Ticker da giocare (consigliati: i primi 2 del giorno)", opts,
                             default=defaults[:2], key="strategy_tickers")
        if len(sel) > 3:
            st.warning("Massimo 3 ticker (9 posizioni): uso i primi 3 selezionati.")
            sel = sel[:3]
        all_rows = []
        for t in sel:
            r = next((x for x in ok_results if x["ticker"] == t), None)
            if r:
                all_rows += strategy_rows(r, float(notional))
        if not all_rows:
            st.info("Seleziona almeno un ticker per generare il piano.")
        else:
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Posizioni", len(all_rows))
            k2.metric("Notional totale", f"${sum(p['Notional'] for p in all_rows):,.0f}")
            k3.metric("Gain max (tutti i TP)", f"+${sum(p['max_gain_usd'] for p in all_rows):,.0f}")
            k4.metric("Loss max (tutti gli SL)", f"-${sum(p['max_loss_usd'] for p in all_rows):,.0f}")
            st.caption("I lati A e C sono opposti: il loss max reale è molto inferiore, perché una gamba perde lo "
                       "SL mentre l'altra è in guadagno.")

            with st.expander("Piano statico (TP/SL e rischio per posizione)"):
                st.dataframe(pd.DataFrame(all_rows)[["Account", "Ruolo", "Ticker", "Lato", "Entry", "TP", "SL",
                                                     "Notional", "Leva", "max_gain_usd", "max_loss_usd"]],
                             hide_index=True, width="stretch",
                             column_config={c: st.column_config.NumberColumn(format="%.2f")
                                            for c in ("Entry", "TP", "SL", "max_gain_usd", "max_loss_usd")})

            @st.fragment(run_every=run_every)
            def strategy_live(rows: list[dict]) -> None:
                df = strategy_live_df(rows)
                st.dataframe(df[["Account", "Ticker", "Lato", "Entry", "TP", "SL", "Ultimo", "Move %",
                                 "PnL $", "ROE %", "Dist. TP %", "Dist. SL %", "Stato"]],
                             hide_index=True, width="stretch",
                             column_config={c: st.column_config.NumberColumn(format="%.2f")
                                            for c in ("Entry", "TP", "SL", "Ultimo", "Move %", "PnL $",
                                                      "ROE %", "Dist. TP %", "Dist. SL %")})
                for _, row in df.iterrows():
                    if row["Stato"] == "🎯 TP HIT":
                        st.success(f"{row['Account']} · {row['Ticker']} {row['Lato']}: TP toccato a "
                                   f"{row['Ultimo']:,.2f} → PnL {row['PnL $']:+,.0f} $ (ROE {row['ROE %']:+.0f}%). "
                                   f"Screenshot della card ADESSO!")
                    elif row["Stato"] == "🛑 SL HIT":
                        st.error(f"{row['Account']} · {row['Ticker']} {row['Lato']}: SL toccato a {row['Ultimo']:,.2f} "
                                 f"→ PnL {row['PnL $']:+,.0f} $. La gamba opposta dovrebbe coprire.")

            strategy_live(all_rows)

            if st.button("Carica queste posizioni nel tracker (tab 'Posizioni e PnL card')", key="load_strategy"):
                tracker_rows = [{"Ticker": p["Ticker"], "Lato": p["Lato"], "Entry": round(p["Entry"], 2),
                                 "Notional": p["Notional"], "Leva": p["Leva"], "Attiva": False,
                                 "TP": round(p["TP"], 2), "SL": round(p["SL"], 2),
                                 "Account": p["Account"]} for p in all_rows]
                st.session_state.positions = tracker_rows
                if "pos_editor" in st.session_state:
                    del st.session_state["pos_editor"]
                save_positions(tracker_rows)
                st.session_state["strategy_loaded_msg"] = (
                    f"{len(tracker_rows)} posizioni salvate in positions.json. Aprile su Variational nella finestra "
                    "di ingresso consigliata, poi spunta 'Aperta' nel tab Posizioni.")
                st.rerun()
            st.caption("Esecuzione: ordini limite nella finestra di ingresso (es. 19:00–20:05 UTC), TP/SL come ordini "
                       "GTC. Dopo il print, la card migliore è di solito nei 90-110 minuti successivi.")

# ==============================================================================
# TAB 2 - Analisi completa
# ==============================================================================
with tab_analysis:
    tbl = eg.results_table(results)
    num_cols = {c: st.column_config.NumberColumn(format="%.2f") for c in tbl.columns if tbl[c].dtype.kind == "f"}
    st.dataframe(tbl, hide_index=True, width="stretch", column_config=num_cols)
    st.caption("Exp. move = prezzo della straddle ATM sulla prima scadenza dopo il report, in % del prezzo. "
               "Reaz. media = media |variazione close→close| nel giorno di reazione delle trimestrali passate. "
               "Opportunità misura la grandezza del movimento atteso, non la direzione.")

    if ok_results:
        tick = st.selectbox("Titolo", [r["ticker"] for r in ok_results], key="analysis_select")
        r = next(x for x in ok_results if x["ticker"] == tick)
        hist = r.get("_hist")
        em = nz(r["score_implied_move_pct"])
        c1, c2 = st.columns([1.6, 1])
        with c1:
            if isinstance(hist, pd.DataFrame) and not hist.empty:
                h = hist.tail(60)
                fig = go.Figure()
                fig.add_trace(go.Candlestick(x=h.index, open=h["Open"], high=h["High"], low=h["Low"], close=h["Close"], name=tick))
                fig.add_trace(go.Scatter(x=h.index, y=hist["Close"].rolling(20).mean().tail(60), name="MA20",
                                         line=dict(width=1, dash="dot")))
                p = nz(r["price"])
                if not math.isnan(p) and not math.isnan(em):
                    for lvl, lab in ((p * (1 + em / 100), f"+{em:.1f}%"), (p * (1 - em / 100), f"-{em:.1f}%")):
                        fig.add_hline(y=lvl, line=dict(width=1, dash="dash"), annotation_text=lab, annotation_position="right")
                fig.update_layout(height=420, margin=dict(l=10, r=10, t=30, b=10), xaxis_rangeslider_visible=False,
                                  title=f"{tick} · ultimi 60 giorni · bande = expected move")
                st.plotly_chart(fig, width="stretch")
        with c2:
            rx = r.get("_reactions")
            if isinstance(rx, pd.DataFrame) and not rx.empty:
                fig2 = go.Figure(go.Bar(x=[str(d) for d in rx["date"]], y=rx["move_pct"],
                                        marker_color=["#1a9c5b" if v >= 0 else "#d43f3f" for v in rx["move_pct"]]))
                if not math.isnan(em):
                    fig2.add_hline(y=em, line=dict(width=1, dash="dash"))
                    fig2.add_hline(y=-em, line=dict(width=1, dash="dash"))
                fig2.update_layout(height=420, margin=dict(l=10, r=10, t=30, b=10),
                                   title=f"Reazione alle ultime {len(rx)} trimestrali (%)")
                st.plotly_chart(fig2, width="stretch")
            else:
                st.info("Nessuna trimestrale storica trovata su Yahoo per questo titolo.")

        m1, m2, m3, m4, m5, m6 = st.columns(6)
        m1.metric("IV ATM", f"{nz(r.get('opt_atm_iv_pct')):.0f}%" if not math.isnan(nz(r.get('opt_atm_iv_pct'))) else "n/d")
        m2.metric("Straddle ATM", f"${nz(r.get('opt_straddle')):.2f}" if not math.isnan(nz(r.get('opt_straddle'))) else "n/d",
                  r.get("opt_expiry", ""))
        m3.metric("Put/Call vol", f"{nz(r.get('opt_pc_ratio_volume')):.2f}" if not math.isnan(nz(r.get('opt_pc_ratio_volume'))) else "n/d")
        m4.metric("Skew put-call", f"{nz(r.get('opt_skew_put_minus_call')):+.1f} pt" if not math.isnan(nz(r.get('opt_skew_put_minus_call'))) else "n/d")
        m5.metric("HV 20d / 60d", f"{nz(r.get('tech_hv20')):.0f}% / {nz(r.get('tech_hv60')):.0f}%")
        m6.metric("RSI 14", f"{nz(r.get('tech_rsi14')):.0f}")

        with st.expander("Fattori direzionali"):
            st.dataframe(pd.DataFrame(r.get("factors", [])), hide_index=True, width="stretch")
        with st.expander("Trimestrali passate"):
            if isinstance(rx, pd.DataFrame) and not rx.empty:
                st.dataframe(rx, hide_index=True, width="stretch",
                             column_config={c: st.column_config.NumberColumn(format="%.2f") for c in rx.columns if rx[c].dtype.kind == "f"})

# ==============================================================================
# TAB 3 - Posizioni e PnL card
# ==============================================================================
with tab_pos:
    st.markdown("Inserisci le posizioni aperte su Variational: la tabella sotto si aggiorna da sola e ti dice "
                "quando la card è al massimo. I dati restano in `positions.json` (letti anche da `alert_monitor.py`).")
    if "positions" not in st.session_state:
        st.session_state.positions = load_positions()
    if "peaks" not in st.session_state:
        st.session_state.peaks = {}

    base = pd.DataFrame(st.session_state.positions or [],
                        columns=["Ticker", "Lato", "Entry", "Notional", "Leva", "Attiva"])
    if base.empty:
        base = pd.DataFrame([{"Ticker": day["stocks"][0]["ticker"], "Lato": "LONG", "Entry": 0.0,
                              "Notional": float(notional), "Leva": float(leverage), "Attiva": False}])

    all_tickers = sorted({s["ticker"] for d in DAYS for s in d["stocks"]})
    edited = st.data_editor(
        base, num_rows="dynamic", hide_index=True, width="stretch", key="pos_editor",
        column_config={
            "Ticker": st.column_config.SelectboxColumn(options=all_tickers, required=True),
            "Lato": st.column_config.SelectboxColumn(options=["LONG", "SHORT"], required=True),
            "Entry": st.column_config.NumberColumn("Prezzo di ingresso ($)", min_value=0.0, format="%.2f"),
            "Notional": st.column_config.NumberColumn("Notional ($)", min_value=0.0, format="%.0f"),
            "Leva": st.column_config.NumberColumn(min_value=1.0, max_value=100.0, format="%.1f"),
            "Attiva": st.column_config.CheckboxColumn("Aperta"),
        },
    )
    rows = edited.dropna(subset=["Ticker"]).to_dict("records")
    if rows != st.session_state.positions:
        st.session_state.positions = rows
        save_positions(rows)

    retrace = st.slider("Avvisami se il PnL ritraccia dal massimo di", 10, 60, int(DEFAULTS["peak_retrace_alert"] * 100), 5,
                        format="%d%%", key="retrace_slider") / 100

    @st.fragment(run_every=run_every)
    def positions_live(retrace: float) -> None:
        active = [p for p in st.session_state.positions if p.get("Attiva") and nz(p.get("Entry"), 0) > 0]
        if not active:
            st.info("Nessuna posizione aperta: spunta 'Aperta' e metti il prezzo di ingresso.")
            return
        out = []
        for p in active:
            stock = next((s for d in DAYS for s in d["stocks"] if s["ticker"] == p["Ticker"]), {"ticker": p["Ticker"]})
            lp = live(eg.yf_symbol(stock))
            price = nz(lp["price"]) * lp.get("fx_to_usd", 1.0)
            entry, notl, lev = nz(p["Entry"]), nz(p["Notional"]), max(nz(p["Leva"], 1), 1)
            sign = 1 if p["Lato"] == "LONG" else -1
            move = (price / entry - 1) * 100 * sign if entry > 0 and not math.isnan(price) else np.nan
            pnl = notl * move / 100 if not math.isnan(move) else np.nan
            roe = move * lev if not math.isnan(move) else np.nan
            liq = entry * (1 - 1 / lev + mmr) if p["Lato"] == "LONG" else entry * (1 + 1 / lev - mmr)
            key = f"{p['Ticker']}|{p['Lato']}|{entry}"
            peak = st.session_state.peaks.get(key, -1e9)
            if not math.isnan(roe) and roe > peak:
                peak = roe
                st.session_state.peaks[key] = peak
            from_peak = (peak - roe) if not math.isnan(roe) and peak > -1e9 else np.nan
            tp, sl = nz(p.get("TP"), 0), nz(p.get("SL"), 0)
            if math.isnan(roe):
                alert = "prezzo n/d"
            elif tp > 0 and ((sign > 0 and price >= tp) or (sign < 0 and price <= tp)):
                alert = f"🎯 TP {tp:,.2f} toccato: posta la card o chiudi"
            elif sl > 0 and ((sign > 0 and price <= sl) or (sign < 0 and price >= sl)):
                alert = f"🛑 SL {sl:,.2f} toccato: piano rispettato"
            elif roe <= 0:
                alert = "sotto ingresso"
            elif peak > 0 and from_peak >= peak * retrace:
                alert = f"⚠️ ritraccio {from_peak:.0f} pt dal massimo ({peak:.0f}%): posta la card o chiudi"
            elif roe >= peak:
                alert = "📸 nuovo massimo: momento buono per la card"
            else:
                alert = "in guadagno"
            out.append({"Ticker": p["Ticker"], "Lato": p["Lato"], "Entry": entry, "Ultimo": price,
                        "Fonte": lp["source"], "Move %": move, "PnL $": pnl, "ROE %": roe,
                        "Max ROE %": peak if peak > -1e9 else np.nan, "Liq. stimata": liq, "Segnale": alert})
        df = pd.DataFrame(out)
        st.dataframe(df, hide_index=True, width="stretch",
                     column_config={c: st.column_config.NumberColumn(format="%.2f") for c in df.columns if df[c].dtype.kind == "f"})
        tot = df["PnL $"].sum(skipna=True)
        st.metric("PnL totale", f"{tot:+,.2f} $", f"aggiornato {eg.now_utc().strftime('%H:%M:%S')} UTC")
        for a in df["Segnale"]:
            if a.startswith("⚠️") or a.startswith("🛑"):
                st.warning(a)
            elif a.startswith("📸") or a.startswith("🎯"):
                st.success(a)

    positions_live(retrace)

# ==============================================================================
# TAB 4 - Post e invio
# ==============================================================================
with tab_post:
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Checklist")
        for item in [
            f"Posizione aperta dopo il {fmt_utc(COMPETITION['positions_open_after'])}",
            f"Notional ≥ ${COMPETITION['min_notional_usd']:,}",
            "Screenshot della PnL card da Variational al momento migliore (vedi tab Posizioni)",
            f"Post su X con la card, tag {COMPETITION['x_handle']} e una riga di tesi",
            f"Form di partecipazione inviato entro il {fmt_utc(COMPETITION['submission_deadline'])}",
            "Link del post salvato per il form",
        ]:
            st.checkbox(item, key=f"chk_{item[:30]}")

    with c2:
        st.subheader("Bozza del post (premio 'best post')")
        opts = [f"{p['Ticker']} {p['Lato']}" for p in st.session_state.get("positions", []) if p.get("Attiva")] or [
            f"{pk['pct']['ticker']} {pk['pct']['score_direction'] if pk and pk['pct']['score_direction'] != 'NEUTRO' else 'LONG'}" if pk else "TICKER LONG"
        ]
        chosen = st.selectbox("Posizione", opts, key="post_position_select")
        tk, side = chosen.split()[0], chosen.split()[-1]
        r = next((x for x in ok_results if x["ticker"] == tk), None)
        em = nz(r["score_implied_move_pct"]) if r else np.nan
        thesis = st.text_input("Tesi in una riga (inglese, è per X)",
                               value=f"options priced a ±{em:.0f}% move, history says it usually delivers" if not math.isnan(em) else "",
                               key="thesis_input")
        pnl_txt = st.text_input("Risultato (es. +42% ROE)", value="+__% ROE", key="pnl_input")
        post = (f"${tk} {side.lower()} into earnings on @variational_io perps.\n"
                f"Thesis: {thesis}\n"
                f"Result: {pnl_txt}\n"
                f"#PnLCard #earnings")
        st.text_area("Testo pronto da copiare", post, height=160, key="post_text_area")
        st.caption("Per il premio 'best post' conta la card chiara, una tesi leggibile e il tag giusto. "
                   "Una card fatta al picco del movimento after-hours vale più di una fatta il giorno dopo.")
