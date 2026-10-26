"""
Motore di analisi per la Variational PnL Card Competition.

Nessuna dipendenza da Streamlit: tutte le funzioni restituiscono dict/DataFrame
e possono essere usate anche da riga di comando (vedi fondo del file).
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf

from config import COMPETITION, DAYS, DEFAULTS, US_SESSION

ET = ZoneInfo("America/New_York")
UTC = timezone.utc


# =============================================================================
# Utility
# =============================================================================
def now_utc() -> datetime:
    return datetime.now(UTC)


def yf_symbol(stock: dict) -> str:
    return stock.get("yf_symbol", stock["ticker"])


def _f(x, default=np.nan) -> float:
    """float sicuro."""
    try:
        v = float(x)
        return default if (v is None or math.isnan(v)) else v
    except (TypeError, ValueError):
        return default


def _nearest(values, target):
    values = list(values)
    return min(values, key=lambda s: abs(s - target)) if values else None


# =============================================================================
# Dati di mercato
# =============================================================================
def fetch_history(symbol: str, period: str = "3y") -> pd.DataFrame:
    """Storico giornaliero (OHLCV), indice tz-naive normalizzato a mezzanotte."""
    h = yf.Ticker(symbol).history(period=period, auto_adjust=True)
    if h is None or h.empty:
        return pd.DataFrame()
    h = h.dropna(subset=["Close"]).copy()
    h.index = pd.to_datetime(h.index).tz_localize(None).normalize()
    return h


def live_price(symbol: str) -> dict:
    """
    Ultimo prezzo disponibile, incluse pre/after-hours (barre a 1 minuto con prepost=True).
    source: regular | pre | post | close | n/d
    """
    tk = yf.Ticker(symbol)
    out = {"price": np.nan, "prev_close": np.nan, "source": "n/d", "ts": None, "change_pct": np.nan,
           "currency": "USD", "fx_to_usd": 1.0}
    fi = None

    try:
        fi = tk.fast_info
        out["price"] = _f(fi.last_price)
        out["prev_close"] = _f(fi.previous_close)
        out["source"] = "close"
        out["currency"] = str(fi.currency or "USD")
    except Exception:
        out["currency"] = "USD"

    try:
        h = tk.history(period="1d", interval="1m", prepost=True)
        if h is not None and not h.empty:
            closes = h["Close"].dropna()
            if not closes.empty:
                ts = closes.index[-1]
                p = _f(closes.iloc[-1])
                if not math.isnan(p):
                    out["price"] = p
                    out["ts"] = ts.to_pydatetime()
                    t_et = ts.tz_convert(ET)
                    mins = t_et.hour * 60 + t_et.minute
                    if str(getattr(fi, "timezone", "America/New_York")) != "America/New_York":
                        out["source"] = "last"
                    elif mins < 9 * 60 + 30:
                        out["source"] = "pre"
                    elif mins >= 16 * 60:
                        out["source"] = "post"
                    else:
                        out["source"] = "regular"
    except Exception:
        pass

    if not math.isnan(out["price"]) and not math.isnan(out["prev_close"]) and out["prev_close"] > 0:
        out["change_pct"] = (out["price"] / out["prev_close"] - 1) * 100
    if out["currency"] not in ("USD", "USd"):
        try:  # es. KRW=X = quanti KRW per 1 USD
            rate = _f(yf.Ticker(f"{out['currency']}=X").fast_info.last_price)
            if rate > 0:
                out["fx_to_usd"] = 1.0 / rate
        except Exception:
            pass
    return out


def yahoo_earnings_date(symbol: str):
    """Data earnings secondo Yahoo (per confronto con il calendario di gara)."""
    try:
        cal = yf.Ticker(symbol).calendar
        d = cal.get("Earnings Date") if isinstance(cal, dict) else None
        if d:
            d = d[0]
            return d if isinstance(d, date) else pd.Timestamp(d).date()
    except Exception:
        pass
    return None


# =============================================================================
# Opzioni: expected move, IV, put/call, skew
# =============================================================================
def options_snapshot(symbol: str, spot: float, earnings_day: date) -> dict | None:
    tk = yf.Ticker(symbol)
    try:
        exps = list(tk.options)
    except Exception:
        exps = []
    if not exps or math.isnan(spot) or spot <= 0:
        return None

    # prima scadenza DOPO la giornata di reazione (così la straddle include l'evento)
    exp = next((e for e in exps if pd.Timestamp(e).date() > earnings_day), exps[0])
    try:
        oc = tk.option_chain(exp)
    except Exception:
        return None
    calls, puts = oc.calls.copy(), oc.puts.copy()
    if calls.empty or puts.empty:
        return None

    for df in (calls, puts):
        for c in ("bid", "ask", "lastPrice", "volume", "openInterest", "impliedVolatility"):
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)

    def mid(row) -> float:
        b, a, last = _f(row.bid, 0), _f(row.ask, 0), _f(row.lastPrice, 0)
        return (b + a) / 2 if (b > 0 and a > 0) else last

    strikes = sorted(set(calls.strike) & set(puts.strike))
    atm = _nearest(strikes, spot)
    if atm is None:
        return None
    c = calls[calls.strike == atm].iloc[0]
    p = puts[puts.strike == atm].iloc[0]
    straddle = mid(c) + mid(p)
    implied_move = straddle / spot * 100 if straddle > 0 else np.nan
    atm_iv = np.nanmean([_f(c.impliedVolatility), _f(p.impliedVolatility)]) * 100

    pc_vol = puts.volume.sum() / max(calls.volume.sum(), 1.0)
    pc_oi = puts.openInterest.sum() / max(calls.openInterest.sum(), 1.0)

    # skew: IV della put OTM a -1 expected move vs call OTM a +1 expected move
    skew = np.nan
    if not math.isnan(implied_move):
        em = implied_move / 100
        pk = _nearest(puts.strike, spot * (1 - em))
        ck = _nearest(calls.strike, spot * (1 + em))
        piv = _f(puts[puts.strike == pk].iloc[0].impliedVolatility) * 100
        civ = _f(calls[calls.strike == ck].iloc[0].impliedVolatility) * 100
        if piv > 0 and civ > 0:
            skew = piv - civ

    return {
        "expiry": exp,
        "days_to_expiry": (pd.Timestamp(exp).date() - date.today()).days,
        "atm_strike": float(atm),
        "straddle": float(straddle),
        "implied_move_pct": float(implied_move),
        "atm_iv_pct": float(atm_iv),
        "pc_ratio_volume": float(pc_vol),
        "pc_ratio_oi": float(pc_oi),
        "skew_put_minus_call": float(skew) if not math.isnan(skew) else np.nan,
    }


# =============================================================================
# Reazioni storiche alle trimestrali
# =============================================================================
def earnings_reactions(symbol: str, hist: pd.DataFrame, n: int = 8) -> pd.DataFrame:
    """
    Per ogni trimestrale passata: variazione close->close del giorno di reazione
    (giorno dopo se AMC, stesso giorno se BMO) e gap in apertura.
    """
    cols = ["date", "when", "close_before", "open_after", "close_after", "gap_pct", "move_pct", "surprise_pct"]
    if hist is None or hist.empty:
        return pd.DataFrame(columns=cols)
    try:
        ed = yf.Ticker(symbol).earnings_dates
    except Exception:
        ed = None
    if ed is None or ed.empty:
        return pd.DataFrame(columns=cols)

    rep_col = next((c for c in ed.columns if "Reported" in c), None)
    sur_col = next((c for c in ed.columns if "Surprise" in c), None)
    past = ed[ed[rep_col].notna()] if rep_col else ed
    past = past[past.index < pd.Timestamp.now(tz=past.index.tz)].sort_index(ascending=False).head(n)

    closes, opens = hist["Close"], hist["Open"]
    rows = []
    for ts, r in past.iterrows():
        d0 = pd.Timestamp(ts).tz_localize(None).normalize()
        hour = pd.Timestamp(ts).hour
        when = "AMC" if hour >= 15 else ("BMO" if 0 < hour < 12 else "?")
        try:
            if when == "AMC":
                before_idx = closes.index[closes.index <= d0]
                after_idx = closes.index[closes.index > d0]
            elif when == "BMO":
                before_idx = closes.index[closes.index < d0]
                after_idx = closes.index[closes.index >= d0]
            else:  # orario ignoto: finestra di 2 giorni
                before_idx = closes.index[closes.index < d0]
                after_idx = closes.index[closes.index > d0]
            if len(before_idx) == 0 or len(after_idx) == 0:
                continue
            b, a = before_idx[-1], after_idx[0]
            cb, oa, ca = _f(closes[b]), _f(opens[a]), _f(closes[a])
            if cb <= 0:
                continue
            rows.append({
                "date": d0.date(),
                "when": when,
                "close_before": cb,
                "open_after": oa,
                "close_after": ca,
                "gap_pct": (oa / cb - 1) * 100,
                "move_pct": (ca / cb - 1) * 100,
                "surprise_pct": _f(r[sur_col]) if sur_col else np.nan,
            })
        except Exception:
            continue
    return pd.DataFrame(rows, columns=cols)


def reaction_stats(rx: pd.DataFrame) -> dict:
    if rx is None or rx.empty:
        return {"n": 0, "avg_abs_move": np.nan, "median_abs_move": np.nan, "max_abs_move": np.nan,
                "positive_rate": np.nan, "big_move_rate": np.nan, "last_move": np.nan, "beat_rate": np.nan}
    m = rx["move_pct"]
    return {
        "n": int(len(rx)),
        "avg_abs_move": float(m.abs().mean()),
        "median_abs_move": float(m.abs().median()),
        "max_abs_move": float(m.abs().max()),
        "positive_rate": float((m > 0).mean() * 100),
        "big_move_rate": float((m.abs() >= 5).mean() * 100),
        "last_move": float(m.iloc[0]),
        "beat_rate": float((rx["surprise_pct"] > 0).mean() * 100) if rx["surprise_pct"].notna().any() else np.nan,
    }


# =============================================================================
# Indicatori tecnici
# =============================================================================
def technicals(hist: pd.DataFrame) -> dict:
    keys = ["hv20", "hv60", "atr14", "atr_pct", "mom_5d", "mom_20d", "rsi14",
            "dist_20dma_pct", "dist_52w_high_pct", "daily_sigma_pct"]
    if hist is None or len(hist) < 30:
        return {k: np.nan for k in keys}
    c = hist["Close"]
    ret = c.pct_change().dropna()

    tr = pd.concat([hist["High"] - hist["Low"],
                    (hist["High"] - c.shift()).abs(),
                    (hist["Low"] - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean().iloc[-1]

    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = (100 - 100 / (1 + rs)).iloc[-1]

    last = c.iloc[-1]
    return {
        "hv20": float(ret.tail(20).std() * np.sqrt(252) * 100),
        "hv60": float(ret.tail(60).std() * np.sqrt(252) * 100),
        "atr14": float(atr),
        "atr_pct": float(atr / last * 100),
        "mom_5d": float((last / c.iloc[-6] - 1) * 100),
        "mom_20d": float((last / c.iloc[-21] - 1) * 100),
        "rsi14": float(rsi),
        "dist_20dma_pct": float((last / c.tail(20).mean() - 1) * 100),
        "dist_52w_high_pct": float((last / c.tail(252).max() - 1) * 100),
        "daily_sigma_pct": float(ret.tail(60).std() * 100),
    }


# =============================================================================
# Scoring
# =============================================================================
def score(opt: dict | None, rx_stats: dict, tech: dict) -> dict:
    """
    opportunity (0-10): QUANTO è grande il movimento atteso.
    bias (-100..100): verso dove pende (debole per natura: una trimestrale è ~50/50).
    """
    implied = opt["implied_move_pct"] if opt else np.nan
    if math.isnan(implied):
        # fallback senza opzioni: media delle reazioni storiche, altrimenti 2 sigma giornaliere
        if rx_stats.get("n", 0) >= 4 and not math.isnan(_f(rx_stats.get("avg_abs_move"))):
            implied = _f(rx_stats["avg_abs_move"])
        else:
            implied = 2.0 * _f(tech.get("daily_sigma_pct"))
        implied_source = "stimato (storico)"
    else:
        implied_source = "straddle ATM"

    # --- opportunity ---------------------------------------------------------
    opp = 0.0
    avg_hist = _f(rx_stats.get("avg_abs_move"))
    if implied >= 12: opp += 4
    elif implied >= 8: opp += 3
    elif implied >= 5: opp += 2
    elif implied >= 3: opp += 1
    else: opp += 0.5
    if avg_hist >= 10: opp += 3
    elif avg_hist >= 6: opp += 2
    elif avg_hist >= 3: opp += 1
    if _f(rx_stats.get("big_move_rate")) >= 60: opp += 1
    atr_pct = _f(tech.get("atr_pct"))
    if atr_pct >= 4: opp += 1
    elif atr_pct >= 2.5: opp += 0.5
    if _f(tech.get("hv20")) > _f(tech.get("hv60")) * 1.15: opp += 0.5
    opp = min(10.0, opp)

    # --- direction bias ------------------------------------------------------
    factors = []

    def add(name, contrib, detail):
        factors.append({"fattore": name, "contributo": round(float(contrib), 1), "dettaglio": detail})

    m5 = _f(tech.get("mom_5d"))
    if not math.isnan(m5):
        add("Momentum 5 giorni", np.clip(m5 / 5, -1, 1) * 15, f"{m5:+.1f}% negli ultimi 5 giorni")
    m20 = _f(tech.get("mom_20d"))
    if not math.isnan(m20):
        add("Momentum 20 giorni", np.clip(m20 / 10, -1, 1) * 10, f"{m20:+.1f}% negli ultimi 20 giorni")
    rsi = _f(tech.get("rsi14"))
    if not math.isnan(rsi):
        if rsi >= 75: add("RSI ipercomprato", -15, f"RSI {rsi:.0f}: aspettative alte, rischio sell-the-news")
        elif rsi <= 30: add("RSI ipervenduto", 10, f"RSI {rsi:.0f}: posizionamento già pessimista")
        else: add("RSI neutro", 0, f"RSI {rsi:.0f}")
    d52 = _f(tech.get("dist_52w_high_pct"))
    if not math.isnan(d52):
        if d52 >= -3: add("Vicino ai massimi 52w", -5, f"{d52:+.1f}% dal massimo: asticella alta")
        elif d52 <= -25: add("Lontano dai massimi 52w", 5, f"{d52:+.1f}% dal massimo: molto negativo già prezzato")
    if opt:
        pc = _f(opt.get("pc_ratio_volume"))
        if not math.isnan(pc):
            if pc < 0.6: add("Put/Call volume basso", 10, f"P/C {pc:.2f}: flusso su call")
            elif pc > 1.3: add("Put/Call volume alto", -10, f"P/C {pc:.2f}: flusso su put")
            else: add("Put/Call neutro", 0, f"P/C {pc:.2f}")
        sk = _f(opt.get("skew_put_minus_call"))
        if not math.isnan(sk):
            if sk >= 8: add("Skew di protezione", -10, f"put IV - call IV = {sk:+.0f} pt: domanda di copertura")
            elif sk <= -2: add("Skew su call", 10, f"put IV - call IV = {sk:+.0f} pt: domanda di call")
            else: add("Skew neutro", 0, f"put IV - call IV = {sk:+.0f} pt")
    pr = _f(rx_stats.get("positive_rate"))
    if not math.isnan(pr) and rx_stats.get("n", 0) >= 4:
        add("Storico reazioni", (pr - 50) / 50 * 20, f"{pr:.0f}% delle ultime {rx_stats['n']} trimestrali chiuse in rialzo")
    br = _f(rx_stats.get("beat_rate"))
    if not math.isnan(br) and br >= 75:
        add("Serie di beat EPS", 5, f"{br:.0f}% di beat sulle stime")

    bias = float(np.clip(sum(f["contributo"] for f in factors), -100, 100))
    if bias >= 25: direction = "LONG"
    elif bias <= -25: direction = "SHORT"
    else: direction = "NEUTRO"

    return {
        "opportunity": round(opp, 1),
        "bias": round(bias, 0),
        "direction": direction,
        "conviction": int(min(100, abs(bias))),
        "implied_move_pct": float(implied) if not math.isnan(implied) else np.nan,
        "implied_source": implied_source,
        "factors": factors,
    }


# =============================================================================
# Piano di posizione (perp)
# =============================================================================
def position_plan(price: float, notional: float, leverage: float, mmr: float,
                  side: str, implied_move_pct: float) -> dict:
    if math.isnan(price) or price <= 0:
        return {}
    em = _f(implied_move_pct, 0) / 100
    units = notional / price
    margin = notional / leverage
    if side == "SHORT":
        liq = price * (1 + 1 / leverage - mmr)
        liq_move = (liq / price - 1) * 100
    else:
        liq = price * (1 - 1 / leverage + mmr)
        liq_move = (liq / price - 1) * 100
    # leva massima "prudente": sopravvivere a 1.5x expected move contro
    safe_lev = math.floor(0.9 / (1.5 * em)) if em > 0 else leverage
    safe_lev = max(1, min(safe_lev, 50))
    return {
        "units": units,
        "margin_usd": margin,
        "liq_price": liq,
        "liq_move_pct": liq_move,
        "roe_at_implied_pct": leverage * em * 100,
        "usd_at_implied": notional * em,
        "usd_loss_at_1_5x_implied": notional * 1.5 * em,
        "roe_loss_at_1_5x_implied_pct": leverage * 1.5 * em * 100,
        "liquidated_at_1_5x_implied": (1.5 * em * 100) >= abs(liq_move),
        "max_safe_leverage": safe_lev,
        "target_price": price * (1 + em) if side != "SHORT" else price * (1 - em),
        "adverse_price": price * (1 - em) if side != "SHORT" else price * (1 + em),
    }


# =============================================================================
# Timing e stato gara
# =============================================================================
def competition_status(now: datetime | None = None) -> dict:
    now = now or now_utc()
    start, end = COMPETITION["positions_open_after"], COMPETITION["submission_deadline"]
    if now < start:
        phase = "pre"
    elif now < end:
        phase = "open"
    else:
        phase = "closed"
    return {"phase": phase, "now": now, "to_start": start - now, "to_deadline": end - now}


def current_day_key(now: datetime | None = None) -> str:
    now = now or now_utc()
    d = now.astimezone(ET).date()
    for day in DAYS:
        if d <= pd.Timestamp(day["date"]).date():
            return day["key"]
    return DAYS[-1]["key"]


def timing_guidance(stock: dict, day_date: str, now: datetime | None = None) -> dict:
    """Finestre operative (UTC) per il titolo."""
    now = now or now_utc()
    d = pd.Timestamp(day_date).date()
    rep_h, rep_m = map(int, stock.get("time_utc", "20:05").split(":"))
    report_at = datetime(d.year, d.month, d.day, rep_h, rep_m, tzinfo=UTC)
    close_at = datetime(d.year, d.month, d.day, 20, 0, tzinfo=UTC)
    if stock["report"] == "AMC":
        entry_window = (close_at - timedelta(hours=1), report_at)
        reaction_window = (report_at, report_at + timedelta(hours=2))
        card_hint = "Il movimento è in after-hours: la card migliore di solito è tra 20:10 e 22:00 UTC, oppure all'apertura del giorno dopo (13:30 UTC) se il mercato ha tempo di digerire."
    else:
        entry_window = (report_at - timedelta(hours=12), report_at)
        reaction_window = (report_at, report_at + timedelta(hours=6))
        card_hint = "Reazione prima dell'apertura USA: segui pre-market e apertura (13:30 UTC)."
    if now < entry_window[0]:
        state = "attesa"
    elif now < entry_window[1]:
        state = "finestra di ingresso"
    elif now < reaction_window[1]:
        state = "reazione in corso"
    else:
        state = "evento passato"
    return {
        "report_at": report_at,
        "entry_window": entry_window,
        "reaction_window": reaction_window,
        "state": state,
        "card_hint": card_hint,
        "to_report": report_at - now,
    }


# =============================================================================
# Analisi completa
# =============================================================================
def analyze_stock(stock: dict, day_date: str, notional: float = DEFAULTS["notional_usd"],
                  leverage: float = DEFAULTS["leverage"], mmr: float = DEFAULTS["maintenance_margin"],
                  lookback: int = DEFAULTS["lookback_quarters"]) -> dict:
    sym = yf_symbol(stock)
    hist = fetch_history(sym)
    lp = live_price(sym)
    price = lp["price"] if not math.isnan(lp["price"]) else (_f(hist["Close"].iloc[-1]) if not hist.empty else np.nan)

    tech = technicals(hist)
    rx = earnings_reactions(sym, hist, n=lookback)
    rxs = reaction_stats(rx)
    opt = options_snapshot(sym, price, pd.Timestamp(day_date).date())
    sc = score(opt, rxs, tech)
    yd = yahoo_earnings_date(sym)
    price_usd = price * lp.get("fx_to_usd", 1.0)
    plan = position_plan(price_usd, notional, leverage, mmr, sc["direction"] if sc["direction"] != "NEUTRO" else "LONG",
                         sc["implied_move_pct"])

    out = {
        "ticker": stock["ticker"],
        "name": stock.get("name", stock["ticker"]),
        "yf_symbol": sym,
        "report": stock["report"],
        "note": stock.get("note", ""),
        "price": price,
        "currency": lp.get("currency", "USD"),
        "price_usd": price_usd,
        "price_source": lp["source"],
        "price_ts": lp["ts"],
        "change_pct": lp["change_pct"],
        "yahoo_earnings_date": yd,
        "date_mismatch": (yd is not None and str(yd) != day_date),
        **{f"opt_{k}": v for k, v in (opt or {}).items()},
        **{f"rx_{k}": v for k, v in rxs.items()},
        **{f"tech_{k}": v for k, v in tech.items()},
        **{f"score_{k}": v for k, v in sc.items() if k != "factors"},
        "factors": sc["factors"],
        **{f"plan_{k}": v for k, v in plan.items()},
    }
    out["_reactions"] = rx
    out["_hist"] = hist
    return out


def analyze_day(day: dict, **kw) -> list[dict]:
    results = []
    for s in day["stocks"]:
        try:
            results.append(analyze_stock(s, day["date"], **kw))
        except Exception as e:  # un titolo rotto non deve bloccare il giorno
            results.append({"ticker": s["ticker"], "name": s.get("name", ""), "error": str(e),
                            "score_opportunity": np.nan, "score_implied_move_pct": np.nan})
    results.sort(key=lambda r: (-_f(r.get("score_opportunity"), -1), -_f(r.get("score_implied_move_pct"), -1)))
    return results


def picks(results: list[dict]) -> dict:
    """Scelte per i due premi (stesso notional: $ gain = notional × movimento)."""
    ok = [r for r in results if "error" not in r and not math.isnan(_f(r.get("score_implied_move_pct")))]
    if not ok:
        return {}
    pct_pick = max(ok, key=lambda r: _f(r["score_implied_move_pct"], 0))
    # premio in $: movimento atteso pesato sulla costanza storica (vuoi un movimento AFFIDABILE su size grande)
    def usd_key(r):
        em = _f(r["score_implied_move_pct"], 0)
        hist = _f(r.get("rx_avg_abs_move"), em)
        rel = _f(r.get("rx_big_move_rate"), 50) / 100
        return (0.5 * em + 0.5 * hist) * (0.5 + 0.5 * rel)
    usd_pick = max(ok, key=usd_key)
    best_dir = max(ok, key=lambda r: _f(r.get("score_conviction"), 0))
    return {"pct": pct_pick, "usd": usd_pick, "direction": best_dir}


def results_table(results: list[dict]) -> pd.DataFrame:
    rows = []
    for r in results:
        if "error" in r:
            rows.append({"Ticker": r["ticker"], "Errore": r["error"]})
            continue
        rows.append({
            "Ticker": r["ticker"],
            "Report": r["report"],
            "Prezzo": r["price"],
            "Var %": r["change_pct"],
            "Exp. move %": r["score_implied_move_pct"],
            "Fonte": r["score_implied_source"],
            "IV ATM %": r.get("opt_atm_iv_pct", np.nan),
            "Reaz. media %": r.get("rx_avg_abs_move", np.nan),
            "Reaz. max %": r.get("rx_max_abs_move", np.nan),
            "% rialzi": r.get("rx_positive_rate", np.nan),
            "P/C vol": r.get("opt_pc_ratio_volume", np.nan),
            "Mom 5d %": r.get("tech_mom_5d", np.nan),
            "RSI": r.get("tech_rsi14", np.nan),
            "Opportunità": r["score_opportunity"],
            "Direzione": r["score_direction"],
            "Convinzione": r["score_conviction"],
        })
    return pd.DataFrame(rows)


# =============================================================================
# CLI rapida
# =============================================================================
if __name__ == "__main__":
    import sys
    key = sys.argv[1] if len(sys.argv) > 1 else current_day_key()
    day = next(d for d in DAYS if d["key"] == key)
    print(f"\n== {day['label']} ({day['date']}) ==")
    res = analyze_day(day)
    pd.set_option("display.width", 200)
    print(results_table(res).round(2).to_string(index=False))
    p = picks(res)
    if p:
        print(f"\nPremio %  -> {p['pct']['ticker']}  (exp. move {p['pct']['score_implied_move_pct']:.1f}%)")
        print(f"Premio $  -> {p['usd']['ticker']}")
        print(f"Direzione più convinta -> {p['direction']['ticker']} {p['direction']['score_direction']} ({p['direction']['score_conviction']}/100)")
