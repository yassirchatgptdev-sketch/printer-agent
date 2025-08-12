# Printer Agent

This package contains a lightweight `printer-agent` HTTP service to receive print jobs (from the backend) and send them to a thermal printer.

## Features
- HTTP API: `/print`, `/health`, `/status`, `/queue`, `/reprint/<id>`
- Supports network (raw TCP) printers and fallback file output
- Local persistent queue (sqlite3)
- Simple token-based auth via env var `PRINT_AGENT_TOKEN`

## Quick start (Linux)

1. Install Python 3.8+
2. Create and activate a venv

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python agent.py
```

The service will run on `http://0.0.0.0:9100` by default.

## Quick start (Windows)

1. Install Python 3.8+ and pip
2. Optionally create a venv

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python agent.py
```

## Building a Windows .exe (optional)

You can bundle the agent into a single `.exe` with PyInstaller.

```powershell
pip install pyinstaller
pyinstaller --onefile --add-data "printer_agent.db;." agent.py
```

This creates a `dist\\agent.exe` that you can deploy to Windows workstations. Make sure to ship `printer_agent.db` if you want preloaded jobs.

## Configuration (environment variables)
- `PRINTER_HOST` default network printer IP (optional)
- `PRINTER_PORT` default network printer port (default 9100)
- `PRINTER_AGENT_DB` path to sqlite DB (default printer_agent.db)
- `PRINT_AGENT_TOKEN` optional token required in Authorization header
- `PRINTER_OUTDIR` fallback output directory for generated files

## API examples

Create a print job (text):

```bash
curl -X POST http://localhost:9100/print -H "Content-Type: application/json" -d '{"text":"Ticket #42\\nMerci"}'
```

Create a print job for network printer:

```bash
curl -X POST http://localhost:9100/print -H "Content-Type: application/json" -d '{"text":"Hello","printer":{"host":"192.168.1.55","port":9100}}'
```

Check health:

```bash
curl http://localhost:9100/health
```

## Notes
- For direct USB thermal printers on Windows you may prefer to use ESC/POS libraries or native print APIs. This agent focuses on network/raw TCP printers which are common for thermal devices.
- The `/print` endpoint requires either `text` or `raw_bytes_base64` in the JSON payload.

