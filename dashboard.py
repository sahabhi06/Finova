import sqlite3
import os
import subprocess
from flask import Flask, jsonify, render_template

app = Flask(__name__)
DB_PATH = os.environ.get("BOT_DB_PATH", "audit_log.db")

# Global reference to the bot process
bot_process = None

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/bot/status")
def bot_status():
    global bot_process
    is_running = bot_process is not None and bot_process.poll() is None
    return jsonify({"running": is_running})

@app.route("/api/bot/start", methods=["POST"])
def bot_start():
    global bot_process
    if bot_process is None or bot_process.poll() is not None:
        # Start the scheduler
        bot_process = subprocess.Popen(
            ["python", "scheduler.py"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT
        )
        return jsonify({"success": True, "message": "Bot started"})
    return jsonify({"success": False, "message": "Bot is already running"})

@app.route("/api/bot/stop", methods=["POST"])
def bot_stop():
    global bot_process
    if bot_process is not None and bot_process.poll() is None:
        bot_process.terminate()
        bot_process.wait()
        bot_process = None
        return jsonify({"success": True, "message": "Bot stopped"})
    return jsonify({"success": False, "message": "Bot is not running"})

@app.route("/api/stats")
def stats():
    try:
        conn = get_db_connection()
        
        # Get total realized PNL and trade stats
        # PNL is tracked in session_pnl_after
        # We can find the latest PNL.
        cur = conn.execute("SELECT session_pnl_after FROM audit_log ORDER BY event_id DESC LIMIT 1")
        row = cur.fetchone()
        current_pnl = row["session_pnl_after"] if row else 0.0

        # Trades: count where outcome is 'order_placed'
        cur = conn.execute("SELECT count(*) as total_trades FROM audit_log WHERE outcome LIKE 'order_placed%'")
        row = cur.fetchone()
        total_trades = row["total_trades"] if row else 0

        # We can also get the latest close price
        cur = conn.execute("SELECT close_price FROM audit_log ORDER BY event_id DESC LIMIT 1")
        row = cur.fetchone()
        current_price = row["close_price"] if row else 0.0

        conn.close()

        return jsonify({
            "current_pnl": current_pnl,
            "total_trades": total_trades,
            "current_price": current_price
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/logs")
def logs():
    try:
        conn = get_db_connection()
        cur = conn.execute("SELECT * FROM audit_log ORDER BY event_id DESC LIMIT 50")
        rows = cur.fetchall()
        
        logs = []
        for row in rows:
            logs.append({
                "event_id": row["event_id"],
                "timestamp": row["timestamp"],
                "symbol": row["symbol"],
                "close_price": row["close_price"],
                "signal_action": row["signal_action"],
                "outcome": row["outcome"],
                "pnl": row["session_pnl_after"]
            })
            
        conn.close()
        return jsonify(logs)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True, port=5000)
