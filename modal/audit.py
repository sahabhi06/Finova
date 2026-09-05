"""
audit.py

Every loop iteration gets logged here -- not just trades. A bot that
only logs successful trades hides the 99% of decisions where it
correctly did nothing, which is exactly the information you need when
debugging "why didn't it buy there?" or "why did it stop trading?"
"""

import sqlite3
import json
import time


SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    close_price REAL,
    signal_action TEXT,
    signal_reason TEXT,
    risk_passed INTEGER,
    risk_checks_json TEXT,
    order_id TEXT,
    order_qty REAL,
    order_price REAL,
    stop_loss_price REAL,
    outcome TEXT,
    session_pnl_after REAL
);
"""


def init_db(db_path: str):
    conn = sqlite3.connect(db_path)
    conn.execute(SCHEMA)
    conn.commit()
    return conn


def log_event(conn, symbol, close_price, signal, risk_result,
              order=None, stop_loss_price=None, outcome="", session_pnl_after=0.0):
    conn.execute(
        """
        INSERT INTO audit_log
        (timestamp, symbol, close_price, signal_action, signal_reason,
         risk_passed, risk_checks_json, order_id, order_qty, order_price,
         stop_loss_price, outcome, session_pnl_after)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            symbol,
            close_price,
            signal["action"],
            signal["reason"],
            int(risk_result["passed"]),
            json.dumps(risk_result["checks"]),
            order.get("orderId") if order else None,
            order.get("executedQty") if order else None,
            order.get("price") if order else None,
            stop_loss_price,
            outcome,
            session_pnl_after,
        ),
    )
    conn.commit()


def get_recent_events(conn, limit=100):
    cur = conn.execute(
        "SELECT * FROM audit_log ORDER BY event_id DESC LIMIT ?", (limit,)
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]
