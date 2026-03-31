#!/usr/bin/env python3
"""
Real-Time User Validation System
Queries Active Directory on-demand with live progress updates
"""

import os
os.environ["EVENTLET_NO_GREENDNS"] = "yes"

import json
import logging
from datetime import datetime
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_file
from flask_socketio import SocketIO, emit
from werkzeug.utils import secure_filename
from waitress import serve

from validator import RealtimeValidator
from config import Config

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('C:\\UserValidation\\logs\\app.log', encoding='utf-8'),
        logging.StreamHandler(open(__import__('sys').stdout.fileno(), mode='w', encoding='utf-8', closefd=False))
    ]
)
logger = logging.getLogger(__name__)

# Flask app
app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-change-this'
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024
app.config['UPLOAD_FOLDER'] = 'C:\\UserValidation\\uploads'

# ✅ FIX: Use threading instead of eventlet
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

config = Config()
validator = RealtimeValidator(config)

ALLOWED_EXTENSIONS = {'xlsx', 'xls', 'csv'}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/health')
def health():
    try:
        return jsonify({
            'status': 'healthy',
            'timestamp': datetime.now().isoformat(),
            'service': 'realtime-validation',
            'ad_connection': validator.test_ad_connection()
        }), 200
    except Exception as e:
        logger.error(f"Health check failed: {e}", exc_info=True)
        return jsonify({'status': 'unhealthy', 'error': str(e)}), 500


@socketio.on('connect')
def handle_connect():
    logger.info(f"Client connected: {request.sid}")
    emit('connection_response', {'status': 'connected'})


@socketio.on('disconnect')
def handle_disconnect():
    logger.info(f"Client disconnected: {request.sid}")


@socketio.on('validate_file')
def handle_validation(data):
    try:
        filepath = data.get('filepath')
        filename = data.get('filename')

        logger.info(f"Starting validation: {filename}")

        def progress_callback(current, total, user_email, status):
            socketio.emit('validation_progress', {
                'current': current,
                'total': total,
                'percentage': int((current / total) * 100),
                'user': user_email,
                'status': status
            }, room=request.sid)

        result = validator.validate_file(
            filepath,
            progress_callback=progress_callback
        )

        report_path = validator.generate_report(result, filename)
        result['report_url'] = f"/download/{os.path.basename(report_path)}"

        socketio.emit('validation_complete', result, room=request.sid)

        try:
            os.remove(filepath)
        except:
            pass

    except Exception as e:
        logger.error(f"Validation error: {e}", exc_info=True)
        socketio.emit('validation_error', {'error': str(e)}, room=request.sid)


@app.route('/upload', methods=['POST'])
def upload():
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file uploaded'}), 400

        file = request.files['file']

        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400

        if not allowed_file(file.filename):
            return jsonify({'error': 'Invalid file type'}), 400

        filename = secure_filename(file.filename)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        save_filename = f"{timestamp}_{filename}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], save_filename)
        file.save(filepath)

        return jsonify({
            'success': True,
            'filepath': filepath,
            'filename': filename
        }), 200

    except Exception as e:
        logger.error(f"Upload error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/download/<filename>')
def download(filename):
    try:
        report_path = os.path.join('C:\\UserValidation\\reports', filename)
        if os.path.exists(report_path):
            return send_file(report_path, as_attachment=True)
        return jsonify({'error': 'Report not found'}), 404
    except Exception as e:
        logger.error(f"Download error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/stats')
def stats():
    try:
        return jsonify(validator.get_stats()), 200
    except Exception as e:
        logger.error(f"Stats error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    try:
        logger.info("Starting Real-Time User Validation Service...")
        logger.info(f"Server: {config.server_ip}:{config.port}")

        if validator.test_ad_connection():
            logger.info("AD connection successful")
        else:
            logger.warning("AD connection failed - check credentials")

        socketio.run(
            app,
            host=config.server_ip,
            port=config.port,
            debug=False,
            use_reloader=False
        )

    except KeyboardInterrupt:
        logger.info("Service stopped by user")
    except Exception as e:
        logger.error(f"Service crashed: {e}", exc_info=True)
        raise