"""
Configurazione della competizione Variational PnL Card (27-29 ottobre 2026).

Modifica qui ticker, giorni, orari e regole. Tutto il resto del bot legge da questo file.
Orari in UTC. A fine ottobre gli USA sono ancora in ora legale (EDT = UTC-4):
  - apertura regolare  13:30 UTC
  - chiusura regolare  20:00 UTC
  - after-hours fino a 00:00 UTC
"""

from datetime import datetime, timezone

# --- Regole della competizione -------------------------------------------------
COMPETITION = {
    "name": "Variational PnL Card Competition",
    "positions_open_after": datetime(2026, 10, 26, 15, 0, tzinfo=timezone.utc),
    "submission_deadline": datetime(2026, 10, 30, 15, 0, tzinfo=timezone.utc),
    "min_notional_usd": 1000,
    "x_handle": "@variational_io",
    "prizes_per_day": {
        "largest_pct_gain": 2500,
        "largest_usd_gain": 2500,
        "best_post": 1000,
    },
    "total_prize_pool": 18000,
}

# --- Calendario earnings per giorno di gara -----------------------------------
# report: "AMC" = dopo la chiusura (reazione in after-hours / giorno dopo)
#         "BMO" = prima dell'apertura (reazione in pre-market / apertura)
# yf_symbol: simbolo da usare su Yahoo Finance se diverso dal ticker mostrato
DAYS = [
    {
        "key": "day1",
        "label": "Giorno 1",
        "date": "2026-10-27",
        "stocks": [
            {"ticker": "HOOD", "name": "Robinhood", "report": "AMC", "time_utc": "20:05"},
            {"ticker": "V", "name": "Visa", "report": "AMC", "time_utc": "20:05"},
            {"ticker": "STX", "name": "Seagate", "report": "AMC", "time_utc": "20:05"},
            {"ticker": "TER", "name": "Teradyne", "report": "AMC", "time_utc": "20:05"},
            {"ticker": "SKHY", "name": "SK hynix", "report": "BMO", "time_utc": "23:00",
             "yf_symbol": "000660.KS", "note": "Riporta in Corea (~08:00 KST = 23:00 UTC del giorno prima). Nessuna opzione USA: expected move stimato dalla volatilità storica."},
        ],
    },
    {
        "key": "day2",
        "label": "Giorno 2",
        "date": "2026-10-28",
        "stocks": [
            {"ticker": "GOOGL", "name": "Alphabet", "report": "AMC", "time_utc": "20:05"},
            {"ticker": "MSFT", "name": "Microsoft", "report": "AMC", "time_utc": "20:05"},
            {"ticker": "KLAC", "name": "KLA Corp", "report": "AMC", "time_utc": "21:00"},
        ],
    },
    {
        "key": "day3",
        "label": "Giorno 3",
        "date": "2026-10-29",
        "stocks": [
            {"ticker": "AMZN", "name": "Amazon", "report": "AMC", "time_utc": "20:05"},
            {"ticker": "COIN", "name": "Coinbase", "report": "AMC", "time_utc": "20:05"},
            {"ticker": "MSTR", "name": "Strategy", "report": "AMC", "time_utc": "20:05"},
            {"ticker": "RDDT", "name": "Reddit", "report": "AMC", "time_utc": "20:05"},
            {"ticker": "SNDK", "name": "Sandisk", "report": "AMC", "time_utc": "20:05"},
        ],
    },
]

# --- Parametri di default del motore ------------------------------------------
DEFAULTS = {
    "notional_usd": 1000,        # notional minimo richiesto dalla gara
    "leverage": 3.0,             # leva usata per i calcoli di ROE / liquidazione
    "maintenance_margin": 0.01,  # 1% - APPROSSIMAZIONE, verifica i parametri reali su Variational
    "refresh_seconds": 30,       # frequenza di aggiornamento prezzi live
    "lookback_quarters": 8,      # quante trimestrali passate analizzare
    "peak_retrace_alert": 0.30,  # avvisa se il PnL ritraccia del 30% dal massimo
}

# Mercato USA (UTC, ora legale)
US_SESSION = {"open": "13:30", "close": "20:00", "afterhours_end": "00:00"}

# --- Alert (alert_monitor.py) ---------------------------------------------------
# Puoi mettere i valori qui, in un file alerts.json accanto a questo file, o come
# variabili d'ambiente DISCORD_WEBHOOK_URL / TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID.
import json as _json, os as _os

ALERTS = {
    "discord_webhook_url": _os.environ.get("DISCORD_WEBHOOK_URL", ""),
    "telegram_bot_token": _os.environ.get("TELEGRAM_BOT_TOKEN", ""),
    "telegram_chat_id": _os.environ.get("TELEGRAM_CHAT_ID", ""),
    "check_interval_seconds": 60,
    "report_warning_minutes": 15,     # avviso X minuti prima del report
    "move_thresholds_pct": [3, 5, 8, 12, 20],  # avvisi quando il movimento post-report supera queste soglie
    "peak_step_pct": 5,               # avvisa a ogni nuovo massimo di ROE di almeno +5 punti
    "liq_proximity": 0.30,            # avvisa se il prezzo ha percorso il 70% della strada verso la liquidazione
}
try:
    with open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "alerts.json"), encoding="utf-8") as _f:
        ALERTS.update({k: v for k, v in _json.load(_f).items() if v not in ("", None)})
except FileNotFoundError:
    pass
