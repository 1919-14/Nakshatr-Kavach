# backend/run.py
"""
NAKSHATRA-KAVACH — Layer 1: Application Entry Point
Dev:        python run.py
Production: gunicorn -k eventlet -w 1 "run:app"
"""

from app import create_app, socketio

app = create_app()

if __name__ == "__main__":
    socketio.run(
        app,
        host="0.0.0.0",
        port=5000,
        debug=app.config.get("DEBUG", False),
        use_reloader=False,   # APScheduler conflicts with Flask reloader
        log_output=True,
    )