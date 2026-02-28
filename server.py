#!/usr/bin/env python3
"""
Trading App — OpenAlgo Web Interface
Flask-based, reads credentials from .env, instrument config from settings.json
Port: 5003  (override with PORT env var or .env PORT=xxxx)
"""

import os
import json
import datetime
import time
import urllib.request
import urllib.error

from flask import Flask, render_template, request, jsonify, redirect, url_for
from dotenv import load_dotenv

# ── Bootstrap ──────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

app = Flask(__name__, template_folder=os.path.join(BASE_DIR, 'templates'))

SETTINGS_FILE     = os.path.join(BASE_DIR, 'data', 'settings.json')
ORDERS_FILE       = os.path.join(BASE_DIR, 'data', 'orders.json')
OPENALGO_URL      = os.getenv('OPENALGO_URL',      'http://localhost:5000/api/v1').rstrip('/')
OPENALGO_API_KEY  = os.getenv('OPENALGO_API_KEY',  '')
MARKET_STATUS_URL = os.getenv('MARKET_STATUS_URL', 'http://host.docker.internal:5002/api/status')

DEFAULT_SETTINGS = {
    "nifty": {
        "expiry":    "17FEB26",
        "strike_ce": "25700",
        "strike_pe": "25600",
        "lot_size":  65
    },
    "banknifty": {
        "expiry":    "24FEB26",
        "strike_ce": "60500",
        "strike_pe": "60600",
        "lot_size":  30
    },
    "common": {
        "quantity_lots": 2,
        "product":       "MIS"
    },
    "ui": {
        "cards_layout": "horizontal"
    }
}


# ── Settings ───────────────────────────────────────────────────────────────

def load_settings():
    try:
        with open(SETTINGS_FILE, 'r') as f:
            on_disk = json.load(f)
        merged = {}
        for sec in ('nifty', 'banknifty', 'common', 'ui'):
            merged[sec] = dict(DEFAULT_SETTINGS[sec])
            merged[sec].update(on_disk.get(sec, {}))
        return merged
    except (FileNotFoundError, json.JSONDecodeError):
        save_settings(DEFAULT_SETTINGS)
        return {k: dict(v) for k, v in DEFAULT_SETTINGS.items()}


def save_settings(settings):
    os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
    with open(SETTINGS_FILE, 'w') as f:
        json.dump(settings, f, indent=2)


# ── Order Management ────────────────────────────────────────────────────────

def load_orders():
    """Load stored order IDs. Auto-resets if orders are from a previous trading day."""
    try:
        with open(ORDERS_FILE, encoding='utf-8') as fh:
            orders = json.load(fh)
        today = datetime.date.today().isoformat()
        if orders:
            oldest = min((o.get('timestamp', 0) for o in orders), default=0)
            order_date = datetime.date.fromtimestamp(oldest).isoformat() if oldest else today
            if order_date != today:
                save_orders([])
                return []
        return orders
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_orders(orders):
    os.makedirs(os.path.dirname(ORDERS_FILE), exist_ok=True)
    with open(ORDERS_FILE, 'w', encoding='utf-8') as fh:
        json.dump(orders, fh, indent=2)


def store_order(order_id, symbol=None, action=None, quantity=None,
                price=None, pricetype=None):
    orders = load_orders()
    orders.append({
        "order_id":  order_id,
        "timestamp": int(time.time()),
        "symbol":    symbol,
        "action":    action,
        "quantity":  quantity,
        "price":     price,
        "pricetype": pricetype,
        "status":    "pending"
    })
    save_orders(orders)


def get_pending_orders():
    return [o for o in load_orders() if o.get('status') == 'pending']


def sync_order_status():
    """Check OpenAlgo orderbook and update local order statuses."""
    result = api_post('orderbook', {"apikey": OPENALGO_API_KEY})
    orderbook_map = {}
    if result.get('status') == 'success':
        orders_data = result.get('data', {})
        orders_list = orders_data.get('orders', []) if isinstance(orders_data, dict) else orders_data
        for o in orders_list:
            orderbook_map[o.get('orderid')] = o.get('status')
    orders = load_orders()
    updated = False
    for local in orders:
        if local.get('status') == 'pending':
            broker_status = orderbook_map.get(local.get('order_id'))
            if broker_status in ['COMPLETE', 'REJECTED', 'CANCELLED', 'CLOSED']:
                local['status'] = broker_status.lower()
                updated = True
    if updated:
        save_orders(orders)
    return {"status": "success", "updated": updated}


def cancel_order_by_id(order_id):
    result = api_post('cancelorder', {
        "apikey":    OPENALGO_API_KEY,
        "orderid":   order_id,
        "strategy":  "trading_app"
    })
    if result.get('status') == 'success':
        orders = load_orders()
        for o in orders:
            if o.get('order_id') == order_id:
                o['status'] = 'cancelled'
        save_orders(orders)
    return result


# ── OpenAlgo API ────────────────────────────────────────────────────────────

def api_post(endpoint, data):
    """POST to OpenAlgo REST API. Returns parsed JSON dict."""
    url = f"{OPENALGO_URL}/{endpoint}"
    try:
        body = json.dumps(data).encode('utf-8')
        req  = urllib.request.Request(url, data=body,
                                      headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            text = resp.read().decode('utf-8')
            return json.loads(text) if text.strip() else \
                   {"status": "error", "message": "Empty response from API"}
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode('utf-8'))
        except Exception:
            return {"status": "error", "message": f"HTTP {e.code}: {e.reason}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def get_positions():
    """Return non-zero positions from OpenAlgo positionbook."""
    result = api_post('positionbook', {"apikey": OPENALGO_API_KEY})
    if result.get('status') == 'success':
        return [p for p in result.get('data', [])
                if int(float(p.get('quantity', 0))) != 0]
    return []


def get_position_qty(symbol):
    """Net qty for a symbol: positive=long, negative=short, 0=flat."""
    for pos in get_positions():
        if pos.get('symbol') == symbol:
            return int(float(pos.get('quantity', 0)))
    return 0


def place_smart_order(symbol, target_position, pricetype='MARKET',
                      price=None, trigger_price=None):
    """
    Place a position-aware order via /placesmartorder.
    target_position: desired net qty (positive=long, negative=short, 0=close)
    """
    settings = load_settings()
    product  = settings['common'].get('product', 'MIS')

    if target_position == 0:
        current = get_position_qty(symbol)
        if current == 0:
            return {"status": "success", "message": "Already flat — no action needed"}
        qty    = abs(current)
        action = 'SELL' if current > 0 else 'BUY'
    else:
        qty    = abs(target_position)
        action = 'BUY' if target_position > 0 else 'SELL'

    data = {
        "apikey":             OPENALGO_API_KEY,
        "strategy":           "trading_app",
        "symbol":             symbol,
        "exchange":           "NFO",
        "action":             action,
        "quantity":           str(qty),
        "position_size":      str(target_position),
        "product":            product,
        "pricetype":          pricetype,
        "price":              str(price)         if price         else "0",
        "trigger_price":      str(trigger_price) if trigger_price else "0",
        "disclosed_quantity": "0"
    }
    result = api_post('placesmartorder', data)
    # Store order ID for limit/SL orders (so they can be tracked and cancelled)
    if result.get('status') == 'success' and pricetype != 'MARKET':
        order_id = result.get('orderid')
        if order_id:
            store_order(order_id, symbol, action, qty, price, pricetype)
    return result


def build_symbols(settings):
    n = settings['nifty']
    b = settings['banknifty']
    return {
        'nifty_ce':     f"NIFTY{n['expiry']}{n['strike_ce']}CE",
        'nifty_pe':     f"NIFTY{n['expiry']}{n['strike_pe']}PE",
        'banknifty_ce': f"BANKNIFTY{b['expiry']}{b['strike_ce']}CE",
        'banknifty_pe': f"BANKNIFTY{b['expiry']}{b['strike_pe']}PE",
    }


# ── Routes ──────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    settings = load_settings()
    syms     = build_symbols(settings)
    cards = [
        ('NIFTY CE',  syms['nifty_ce'],     settings['nifty']['lot_size']),
        ('NIFTY PE',  syms['nifty_pe'],     settings['nifty']['lot_size']),
        ('BNIFTY CE', syms['banknifty_ce'], settings['banknifty']['lot_size']),
        ('BNIFTY PE', syms['banknifty_pe'], settings['banknifty']['lot_size']),
    ]
    lot_sizes = {sym: ls for _, sym, ls in cards}
    symbols   = [sym for _, sym, _ in cards]
    return render_template('trading.html',
                           cards=cards,
                           lot_sizes=lot_sizes,
                           symbols=symbols,
                           qty_lots=settings['common']['quantity_lots'])


@app.route('/settings', methods=['GET'])
def settings_page():
    s      = load_settings()
    api_ok = bool(OPENALGO_API_KEY)
    return render_template('settings.html', s=s, api_ok=api_ok,
                           openalgo_url=OPENALGO_URL)


@app.route('/settings', methods=['POST'])
def update_settings():
    settings = {
        "nifty": {
            "expiry":    request.form['nifty_expiry'].strip().upper(),
            "strike_ce": request.form['nifty_strike_ce'].strip(),
            "strike_pe": request.form['nifty_strike_pe'].strip(),
            "lot_size":  int(request.form['nifty_lot_size']),
        },
        "banknifty": {
            "expiry":    request.form['banknifty_expiry'].strip().upper(),
            "strike_ce": request.form['banknifty_strike_ce'].strip(),
            "strike_pe": request.form['banknifty_strike_pe'].strip(),
            "lot_size":  int(request.form['banknifty_lot_size']),
        },
        "common": {
            "quantity_lots": int(request.form['quantity_lots']),
            "product":       request.form['product'],
        },
        "ui": {
            "cards_layout": request.form.get('cards_layout', 'horizontal'),
        }
    }
    save_settings(settings)
    return redirect(url_for('index'))


@app.route('/api/positions')
def api_positions():
    return jsonify(get_positions())


@app.route('/api/pending_orders')
def api_pending_orders():
    return jsonify(get_pending_orders())


@app.route('/api/sync_order_status', methods=['POST'])
def api_sync_order_status():
    return jsonify(sync_order_status())


@app.route('/api/cancel_order', methods=['POST'])
def api_cancel_order():
    try:
        data = request.get_json(force=True)
        return jsonify(cancel_order_by_id(data['order_id']))
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/update_strike', methods=['POST'])
def api_update_strike():
    try:
        data        = request.get_json(force=True)
        instrument  = data.get('instrument')
        option_type = data.get('option_type', 'ce')
        delta       = int(data.get('delta', 0))
        settings    = load_settings()

        if instrument == 'nifty':
            old_strike = int(settings['nifty'][f'strike_{option_type}'])
            new_strike = old_strike + delta
            settings['nifty'][f'strike_{option_type}'] = str(new_strike)
            expiry     = settings['nifty']['expiry']
            suffix     = 'CE' if option_type == 'ce' else 'PE'
            old_symbol = f"NIFTY{expiry}{old_strike}{suffix}"
            new_symbol = f"NIFTY{expiry}{new_strike}{suffix}"
        elif instrument == 'banknifty':
            old_strike = int(settings['banknifty'][f'strike_{option_type}'])
            new_strike = old_strike + delta
            settings['banknifty'][f'strike_{option_type}'] = str(new_strike)
            expiry     = settings['banknifty']['expiry']
            suffix     = 'CE' if option_type == 'ce' else 'PE'
            old_symbol = f"BANKNIFTY{expiry}{old_strike}{suffix}"
            new_symbol = f"BANKNIFTY{expiry}{new_strike}{suffix}"
        else:
            return jsonify({"status": "error", "message": "Unknown instrument"}), 400

        save_settings(settings)
        return jsonify({
            'status':     'success',
            'old_strike': str(old_strike),
            'new_strike': str(new_strike),
            'old_symbol': old_symbol,
            'new_symbol': new_symbol
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/smart_order', methods=['POST'])
def api_smart_order():
    try:
        data   = request.get_json(force=True)
        result = place_smart_order(
            symbol          = data['symbol'],
            target_position = int(data['target_position']),
            pricetype       = data.get('pricetype', 'MARKET'),
            price           = data.get('price'),
            trigger_price   = data.get('trigger_price'),
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ── Entry point ──────────────────────────────────────────────────────────────

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5003))
    print(f"Trading App  →  http://localhost:{port}")
    print(f"OpenAlgo URL →  {OPENALGO_URL}")
    print(f"API key      →  {'set' if OPENALGO_API_KEY else 'NOT SET — edit .env'}")
    print(f"Settings     →  {SETTINGS_FILE}\n")
    app.run(host='0.0.0.0', port=port,
            debug=os.getenv('FLASK_DEBUG', 'false').lower() == 'true',
            threaded=True)
