#!/usr/bin/env python3
\"\"\"
Printer Agent - Lightweight HTTP service to receive print jobs and send to thermal printer.

Features:
- HTTP API: /print , /health, /status, /reprint, /queue
- Supports raw TCP printing to network printers (port 9100) and fallback to file output.
- Local queue with retries and persistence (sqlite3).
- Simple auth token support (optional) via PRINT_AGENT_TOKEN env var.
- Designed to be packaged into a Windows .exe using PyInstaller (instructions provided).
\"\"\"

import os
import sys
import time
import json
import socket
import sqlite3
import threading
import queue as pyqueue
from http import HTTPStatus
from flask import Flask, request, jsonify, abort

DB_PATH = os.environ.get("PRINTER_AGENT_DB", "printer_agent.db")
DEFAULT_PRINTER_HOST = os.environ.get("PRINTER_HOST", "")  # e.g. 192.168.1.50
DEFAULT_PRINTER_PORT = int(os.environ.get("PRINTER_PORT", "9100"))
PRINT_AGENT_TOKEN = os.environ.get("PRINT_AGENT_TOKEN", "")  # optional simple token

# Create DB and queue table
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(\"\"\"
    CREATE TABLE IF NOT EXISTS print_jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        payload TEXT,
        status TEXT,
        attempts INTEGER DEFAULT 0,
        last_error TEXT,
        created_at INTEGER,
        updated_at INTEGER
    )
    \"\"\")
    conn.commit()
    conn.close()

init_db()

app = Flask(__name__)
job_queue = pyqueue.Queue()

# Worker thread to process jobs from sqlite + in-memory queue
STOP_WORKER = threading.Event()

def enqueue_job_db(payload):
    now = int(time.time())
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT INTO print_jobs (payload, status, attempts, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (json.dumps(payload, ensure_ascii=False), 'queued', 0, now, now))
    job_id = cur.lastrowid
    conn.commit()
    conn.close()
    job_queue.put(job_id)
    return job_id

def fetch_job(job_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, payload, status, attempts FROM print_jobs WHERE id=?", (job_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return {"id": row[0], "payload": json.loads(row[1]), "status": row[2], "attempts": row[3]}

def update_job(job_id, **kwargs):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    now = int(time.time())
    fields = []
    values = []
    for k,v in kwargs.items():
        fields.append(f\"{k}=?\")
        values.append(v)
    fields.append(\"updated_at=?\")
    values.append(now)
    values.append(job_id)
    cur.execute(f\"UPDATE print_jobs SET {', '.join(fields)} WHERE id=?\", values)
    conn.commit()
    conn.close()

def list_jobs(limit=50):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, payload, status, attempts, created_at, updated_at FROM print_jobs ORDER BY created_at DESC LIMIT ?", (limit,))
    rows = cur.fetchall()
    conn.close()
    res = []
    for r in rows:
        res.append({"id": r[0], "payload": json.loads(r[1]), "status": r[2], "attempts": r[3], "created_at": r[4], "updated_at": r[5]})
    return res

def raw_tcp_print(host, port, data_bytes, timeout=5):
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.sendall(data_bytes)
        return True, None
    except Exception as e:
        return False, str(e)

def render_text_to_escpos_bytes(text):
    # Very simple converter: text lines + line feed, and cut command.
    if isinstance(text, dict):
        text = json.dumps(text, ensure_ascii=False)
    if not isinstance(text, (str, bytes)):
        text = str(text)
    if isinstance(text, str):
        data = text.encode('utf-8')
    else:
        data = text
    # ESC/POS cut paper (partial) = b'\\x1dV1'
    cut = b'\\x1dV1'
    return data + b'\\n' + cut

def process_job(job_id):
    job = fetch_job(job_id)
    if not job:
        return
    payload = job["payload"]
    attempts = job.get("attempts", 0)
    # payload: { "mode": "raw"|"text", "printer": {"host":"1.2.3.4","port":9100}, "text": "...", "raw_bytes_base64": "..." }
    printer = payload.get("printer") or {"host": DEFAULT_PRINTER_HOST, "port": DEFAULT_PRINTER_PORT}
    host = printer.get("host") or DEFAULT_PRINTER_HOST
    port = int(printer.get("port") or DEFAULT_PRINTER_PORT)
    mode = payload.get("mode", "text")
    try:
        update_job(job_id, status='processing', attempts=attempts+1)
        if mode == "raw" and payload.get("raw_bytes_base64"):
            import base64
            data_bytes = base64.b64decode(payload["raw_bytes_base64"])
        else:
            text = payload.get("text", "<no text>")
            data_bytes = render_text_to_escpos_bytes(text)
        if host:
            ok, err = raw_tcp_print(host, port, data_bytes)
            if not ok:
                raise RuntimeError(f"Network print failed: {err}")
        else:
            # fallback to write file for manual printing
            outdir = os.environ.get("PRINTER_OUTDIR", "/tmp/printer_agent_out")
            os.makedirs(outdir, exist_ok=True)
            fn = os.path.join(outdir, f"ticket_{job_id}.txt")
            with open(fn, "wb") as f:
                f.write(data_bytes)
        update_job(job_id, status='done', last_error=None)
    except Exception as e:
        update_job(job_id, status='error', last_error=str(e))
        # retry policy: re-enqueue up to 3 attempts
        if attempts < 3:
            time.sleep(1)
            job_queue.put(job_id)

def worker_loop():
    while not STOP_WORKER.is_set():
        try:
            job_id = job_queue.get(timeout=1)
            process_job(job_id)
            job_queue.task_done()
        except Exception as e:
            # timeout or error - continue
            pass

# Load queued jobs from DB at startup (persisted)
def load_pending_jobs_into_queue():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id FROM print_jobs WHERE status IN ('queued','error') ORDER BY created_at ASC")
    rows = cur.fetchall()
    conn.close()
    for r in rows:
        job_queue.put(r[0])

worker_thread = threading.Thread(target=worker_loop, daemon=True)
worker_thread.start()
load_pending_jobs_into_queue()

# Simple token auth
def check_auth(req):
    if PRINT_AGENT_TOKEN:
        token = req.headers.get("Authorization") or req.args.get("token") or req.json.get("token") if req.json else None
        if not token or token != PRINT_AGENT_TOKEN:
            abort(HTTPStatus.UNAUTHORIZED)

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "queue_size": job_queue.qsize()})

@app.route("/status", methods=["GET"])
def status():
    return jsonify({"ok": True, "queue_size": job_queue.qsize(), "db_path": DB_PATH})

@app.route("/queue", methods=["GET"])
def get_queue():
    limit = int(request.args.get("limit", "50"))
    return jsonify({"jobs": list_jobs(limit)})

@app.route("/print", methods=["POST"])
def print_endpoint():
    if request.is_json:
        if PRINT_AGENT_TOKEN:
            check_auth(request)
        payload = request.get_json()
        # minimal validation
        if not payload.get("text") and not payload.get("raw_bytes_base64"):
            return jsonify({"error": "no text or raw bytes provided"}), 400
        job_id = enqueue_job_db(payload)
        return jsonify({"ok": True, "job_id": job_id}), 201
    return jsonify({"error": "expected JSON payload"}), 400

@app.route("/reprint/<int:job_id>", methods=["POST"])
def reprint(job_id):
    if PRINT_AGENT_TOKEN:
        check_auth(request)
    job = fetch_job(job_id)
    if not job:
        return jsonify({"error": "not found"}), 404
    # create a new job with same payload
    payload = job["payload"]
    new_id = enqueue_job_db(payload)
    return jsonify({"ok": True, "new_job_id": new_id}), 201

@app.route("/stop", methods=["POST"])
def stop_agent():
    # only local calls allowed (for safety)
    if request.remote_addr not in ("127.0.0.1","::1","localhost"):
        return jsonify({"error": "forbidden"}), 403
    STOP_WORKER.set()
    return jsonify({"ok": True})

if __name__ == "__main__":
    port = int(os.environ.get("PRINTER_AGENT_PORT", "9100"))
    host = os.environ.get("PRINTER_AGENT_BIND", "0.0.0.0")
    try:
        # load pending jobs queue again
        load_pending_jobs_into_queue()
        # worker_thread already started above
    except Exception:
        pass
    app.run(host=host, port=port)
