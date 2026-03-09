"""
Buteyko breath-hold training routes.

Endpoints:
  POST /api/buteyko/configure   – set BOLT + training params
  GET  /api/buteyko/config      – get current Buteyko config
  POST /api/buteyko/round_result – record one round's hold result
  POST /api/buteyko/save        – save session data to JSON
"""

import json
import time
from flask import Blueprint, request, jsonify
from routes.utils import get_current_session
from state import DATA_DIR

buteyko_bp = Blueprint('buteyko', __name__)


@buteyko_bp.route('/api/buteyko/configure', methods=['POST'])
def buteyko_configure():
    sess = get_current_session()
    data = request.get_json(force=True) or {}
    cfg = sess.buteyko_config
    username = str(data.get('username', '')).strip()[:64]
    if username:
        sess.session_config['username'] = username
    for key in ['bolt_seconds', 'target_hold', 'inhale_sec', 'exhale_sec',
                'pre_hold_breaths', 'post_hold_breaths', 'num_rounds']:
        if key in data:
            cfg[key] = data[key]
    # Auto-compute target_hold from bolt if not explicitly provided
    if cfg['bolt_seconds'] is not None and data.get('target_hold') is None:
        cfg['target_hold'] = round(cfg['bolt_seconds'] * 0.5, 1)
    sess.buteyko_rounds = []
    return jsonify({'ok': True, 'config': cfg})


@buteyko_bp.route('/api/buteyko/config', methods=['GET'])
def buteyko_get_config():
    sess = get_current_session()
    return jsonify(sess.buteyko_config)


@buteyko_bp.route('/api/buteyko/round_result', methods=['POST'])
def buteyko_round_result():
    sess = get_current_session()
    data = request.get_json(force=True) or {}
    result = {
        'round': data.get('round', len(sess.buteyko_rounds) + 1),
        'target': data.get('target', sess.buteyko_config.get('target_hold')),
        'actual': data.get('actual'),
        'emergency': data.get('emergency', False),
    }
    sess.buteyko_rounds.append(result)
    # If emergency, update target for subsequent rounds
    if result['emergency'] and result['actual']:
        sess.buteyko_config['target_hold'] = result['actual']
    return jsonify({'ok': True, 'result': result, 'new_target': sess.buteyko_config['target_hold']})


@buteyko_bp.route('/api/buteyko/save', methods=['POST'])
def buteyko_save():
    sess = get_current_session()
    data = request.get_json(force=True) or {}
    ts = time.strftime('%Y%m%d_%H%M%S')
    username = (data.get('username') or sess.session_config.get('username') or sess.session_id).strip()
    fname = f"buteyko_{username}_{ts}.json"
    payload = {
        'session_id': sess.session_id,
        'username': username,
        'timestamp': ts,
        'config': sess.buteyko_config,
        'rounds': sess.buteyko_rounds,
        'bpm_all': sess.bpm_all,
    }
    fpath = DATA_DIR / fname
    fpath.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    return jsonify({'ok': True, 'file': fname})
