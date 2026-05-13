"""
NAKSHATRA-KAVACH Backend Entry Point
Layer 1: Real-Time Data Ingestion Pipeline

Usage:
    python run.py                    # Development server on port 5000
    python run.py --port 8000       # Custom port
    python run.py --no-scheduler     # Run Flask without APScheduler
"""

import os
import argparse
import logging
from dotenv import load_dotenv

load_dotenv()

from app import create_app, socketio
from app.config import INGESTION_INTERVAL_SECONDS
from app.services.data_ingestion import get_ingestion_service

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description='NAKSHATRA-KAVACH Backend')
    parser.add_argument('--port', type=int, default=int(os.environ.get('PORT', 5000)),
                       help='Port to run the server on')
    parser.add_argument('--host', type=str, default=os.environ.get('HOST', '0.0.0.0'),
                       help='Host to bind to')
    parser.add_argument('--debug', action='store_true',
                       help='Enable debug mode')
    parser.add_argument('--no-scheduler', action='store_true',
                       help='Run without APScheduler background job')
    parser.add_argument('--ingestion-interval', type=int, default=INGESTION_INTERVAL_SECONDS,
                       help='Ingestion cycle interval in seconds')

    args = parser.parse_args()

    app = create_app()

    if not args.no_scheduler:
        ingestion_service = get_ingestion_service()
        ingestion_service.start_scheduler(interval_seconds=args.ingestion_interval)
        logger.info(f"APScheduler started with {args.ingestion_interval}s interval")
    else:
        logger.warning("Running without background scheduler")

    logger.info(f"Starting NAKSHATRA-KAVACH on {args.host}:{args.port}")

    socketio.run(
        app,
        host=args.host,
        port=args.port,
        debug=args.debug,
        allow_unsafe_werkzeug=True
    )


if __name__ == '__main__':
    main()