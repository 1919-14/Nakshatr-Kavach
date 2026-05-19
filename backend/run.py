# backend/run.py
"""
NAKSHATRA-KAVACH - application entry point.

Dev:        python run.py
Production: gunicorn -k eventlet -w 1 "run:app"
"""

from __future__ import annotations

import os
import socket
import sys

from app import create_app, socketio


def _port_available(port: int, host: str = "0.0.0.0") -> bool:
    probe_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return sock.connect_ex((probe_host, port)) != 0


def _select_port() -> int:
    explicit = os.getenv("PORT") or os.getenv("NAKSHATRA_PORT")
    base_port = int(explicit or 5000)
    if _port_available(base_port):
        return base_port
    if explicit:
        print(f"Port {base_port} is already in use. Stop the old backend or choose another PORT.", file=sys.stderr)
        sys.exit(98)
    for port in range(5001, 5011):
        if _port_available(port):
            print(
                f"Port 5000 is already in use; starting backend on {port}. "
                f"Set frontend VITE_API_BASE=http://localhost:{port} if needed.",
                file=sys.stderr,
            )
            return port
    print("Ports 5000-5010 are busy. Stop an old backend process and run again.", file=sys.stderr)
    sys.exit(98)


app = None if __name__ == "__main__" else create_app()

if __name__ == "__main__":
    selected_port = _select_port()
    app = create_app()
    socketio.run(
        app,
        host="0.0.0.0",
        port=selected_port,
        debug=app.config.get("DEBUG", False),
        use_reloader=False,
        log_output=True,
        allow_unsafe_werkzeug=True,
    )
