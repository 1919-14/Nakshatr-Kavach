# NAKSHATRA-KAVACH Backend Application
from flask import Flask
from flask_cors import CORS
from flask_socketio import SocketIO

socketio = SocketIO(cors_allowed_origins="*", path="/realtime")


def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = 'nakshatra-kavach-secret-key'

    CORS(app)
    socketio.init_app(app, async_mode='threading')

    from app.db import init_db
    init_db()

    from app.routes import api_bp
    app.register_blueprint(api_bp, url_prefix='/api')

    return app
