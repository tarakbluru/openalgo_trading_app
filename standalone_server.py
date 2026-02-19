#!/usr/bin/env python3
"""
Trading App — Standalone server
Zero external dependencies — Python 3.8+ stdlib only.
No pip installs needed: no Flask, no python-dotenv.
Port: 5003  (override with PORT env var or .env PORT=xxxx)
"""

import os, json, socketserver, urllib.request, urllib.error
from http.server  import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

# ── Bootstrap ──────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_env():
    """Minimal .env parser — no python-dotenv needed."""
    path = os.path.join(BASE_DIR, '.env')
    try:
        with open(path, encoding='utf-8') as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, _, val = line.partition('=')
                os.environ.setdefault(key.strip(), val.strip())
    except FileNotFoundError:
        pass


_load_env()

SETTINGS_FILE    = os.path.join(BASE_DIR, 'data', 'settings.json')
ORDERS_FILE      = os.path.join(BASE_DIR, 'data', 'orders.json')
OPENALGO_URL     = os.getenv('OPENALGO_URL', 'http://localhost:5000/api/v1').rstrip('/')
OPENALGO_API_KEY = os.getenv('OPENALGO_API_KEY', '')

DEFAULT_SETTINGS = {
    "nifty":     {"expiry": "17FEB26", "strike_ce": "25700", "strike_pe": "25600", "lot_size": 65},
    "banknifty": {"expiry": "24FEB26", "strike_ce": "60500", "strike_pe": "60600", "lot_size": 30},
    "common":    {"quantity_lots": 2, "product": "MIS"},
    "ui":        {"cards_layout": "horizontal"},
}


# ── Settings ───────────────────────────────────────────────────────────────

def load_settings():
    try:
        with open(SETTINGS_FILE, encoding='utf-8') as fh:
            on_disk = json.load(fh)
        merged = {}
        for sec in ('nifty', 'banknifty', 'common', 'ui'):
            merged[sec] = dict(DEFAULT_SETTINGS[sec])
            merged[sec].update(on_disk.get(sec, {}))
        return merged
    except (FileNotFoundError, json.JSONDecodeError):
        save_settings(DEFAULT_SETTINGS)
        return {k: dict(v) for k, v in DEFAULT_SETTINGS.items()}


def save_settings(s):
    os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as fh:
        json.dump(s, fh, indent=2)


# ── Order Management ─────────────────────────────────────────────────────

def load_orders():
    """Load stored order IDs. Auto-resets if orders are from a previous trading day."""
    try:
        with open(ORDERS_FILE, encoding='utf-8') as fh:
            orders = json.load(fh)
        # Reset if any orders exist from a previous day
        today = __import__('datetime').date.today().isoformat()
        if orders:
            import datetime
            oldest = min(
                (o.get('timestamp', 0) for o in orders),
                default=0
            )
            order_date = datetime.date.fromtimestamp(oldest).isoformat() if oldest else today
            if order_date != today:
                save_orders([])
                return []
        return orders
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_orders(orders):
    """Save order IDs to file."""
    os.makedirs(os.path.dirname(ORDERS_FILE), exist_ok=True)
    with open(ORDERS_FILE, 'w', encoding='utf-8') as fh:
        json.dump(orders, fh, indent=2)


def store_order(order_id, symbol=None, action=None, quantity=None, price=None, pricetype=None):
    """Store a new order ID with metadata."""
    orders = load_orders()
    order_data = {
        "order_id": order_id,
        "timestamp": int(__import__('time').time()),
        "symbol": symbol,
        "action": action,
        "quantity": quantity,
        "price": price,
        "pricetype": pricetype,
        "status": "pending"
    }
    orders.append(order_data)
    save_orders(orders)


def get_pending_orders():
    """Get all pending orders."""
    return [o for o in load_orders() if o.get("status") == "pending"]


def sync_order_status():
    """Check OpenAlgo orderbook and update local order statuses."""
    result = api_post('orderbook', {"apikey": OPENALGO_API_KEY})
    orderbook_orders = {}
    
    if result.get('status') == 'success':
        orders_data = result.get('data', {})
        if isinstance(orders_data, dict):
            orders_list = orders_data.get('orders', [])
        else:
            orders_list = orders_data
        for order in orders_list:
            orderbook_orders[order.get('orderid')] = order.get('status')
    
    orders = load_orders()
    updated = False
    
    for local_order in orders:
        if local_order.get('status') == 'pending':
            order_id = local_order.get('order_id')
            if order_id in orderbook_orders:
                broker_status = orderbook_orders[order_id]
                if broker_status in ['COMPLETE', 'REJECTED', 'CANCELLED', 'CLOSED']:
                    local_order['status'] = broker_status.lower()
                    updated = True
    
    if updated:
        save_orders(orders)
    
    return {"status": "success", "updated": updated}


def cancel_order_by_id(order_id):
    """Cancel a specific order via OpenAlgo API."""
    result = api_post('cancelorder', {
        "apikey": OPENALGO_API_KEY,
        "orderid": order_id,
        "strategy": "trading_app"
    })
    
    # Update order status in local storage
    if result.get('status') == 'success':
        orders = load_orders()
        for order in orders:
            if order.get("order_id") == order_id:
                order["status"] = "cancelled"
        save_orders(orders)
    
    return result


# ── OpenAlgo API ─────────────────────────────────────────────────────────

def api_post(endpoint, data):
    url  = f"{OPENALGO_URL}/{endpoint}"
    body = json.dumps(data).encode('utf-8')
    req  = urllib.request.Request(url, data=body,
                                  headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            text = resp.read().decode('utf-8')
            return json.loads(text) if text.strip() else \
                   {"status": "error", "message": "Empty response from API"}
    except urllib.error.HTTPError as e:
        try:    return json.loads(e.read().decode('utf-8'))
        except: return {"status": "error", "message": f"HTTP {e.code}: {e.reason}"}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def get_positions():
    result = api_post('positionbook', {"apikey": OPENALGO_API_KEY})
    if result.get('status') == 'success':
        return [p for p in result.get('data', [])
                if int(float(p.get('quantity', 0))) != 0]
    return []


def get_position_qty(symbol):
    for pos in get_positions():
        if pos.get('symbol') == symbol:
            return int(float(pos.get('quantity', 0)))
    return 0


def place_smart_order(symbol, target_position, pricetype='MARKET',
                      price=None, trigger_price=None):
    settings = load_settings()
    product  = settings['common'].get('product', 'MIS')
    if target_position == 0:
        current = get_position_qty(symbol)
        if current == 0:
            return {"status": "success", "message": "Already flat \u2014 no action needed"}
        qty, action = abs(current), ('SELL' if current > 0 else 'BUY')
    else:
        qty, action = abs(target_position), ('BUY' if target_position > 0 else 'SELL')
    result = api_post('placesmartorder', {
        "apikey":             OPENALGO_API_KEY,
        "strategy":           "trading_app",
        "symbol":             symbol,
        "exchange":           "NFO",
        "action":             action,
        "quantity":           str(qty),
        "position_size":      str(target_position),
        "product":            product,
        "pricetype":          pricetype,
        "price":              str(price) if price else "0",
        "trigger_price":      str(trigger_price) if trigger_price else "0",
        "disclosed_quantity": "0",
    })
    
    # Store order ID for potential cancellation (only for non-market orders)
    if result.get('status') == 'success' and pricetype != 'MARKET':
        order_id = result.get('orderid')
        if order_id:
            store_order(order_id, symbol, action, qty, price, pricetype)
    
    return result


def build_symbols(s):
    n, b = s['nifty'], s['banknifty']
    return {
        'nifty_ce':     f"NIFTY{n['expiry']}{n['strike_ce']}CE",
        'nifty_pe':     f"NIFTY{n['expiry']}{n['strike_pe']}PE",
        'banknifty_ce': f"BANKNIFTY{b['expiry']}{b['strike_ce']}CE",
        'banknifty_pe': f"BANKNIFTY{b['expiry']}{b['strike_pe']}PE",
    }


# ── HTML — stored as plain strings so CSS braces never conflict ───────────

_TRADING_CSS = """\
:root {
  --bg:#0f172a; --surface:#1e293b; --border:#334155; --text:#e2e8f0;
  --text-muted:#64748b; --placeholder:#475569; --input-bg:#0f172a;
  --accent:#f97316; --btn-neutral-bg:#334155; --btn-neutral-text:#e2e8f0;
  --s-ok-bg:#14532d; --s-ok-text:#86efac;
  --s-err-bg:#450a0a; --s-err-text:#fca5a5;
  --chip-long-bg:#14532d; --chip-long-text:#86efac; --chip-long-border:#166534;
  --chip-short-bg:#450a0a; --chip-short-text:#fca5a5; --chip-short-border:#7f1d1d;
  --pnl-pos:#4ade80; --pnl-neg:#f87171;
  --badge-flat-bg:#1e293b; --badge-flat-text:#64748b; --badge-flat-border:#334155;
  --badge-long-bg:#14532d; --badge-long-text:#86efac;
  --badge-short-bg:#450a0a; --badge-short-text:#fca5a5;
  --entry-bg:rgba(21,128,61,0.12); --entry-border:#166534;
  --exit-bg:rgba(185,28,28,0.10); --exit-border:#7f1d1d;
}
body.light {
  --bg:#f1f5f9; --surface:#ffffff; --border:#cbd5e1; --text:#1e293b;
  --text-muted:#64748b; --placeholder:#94a3b8; --input-bg:#f8fafc;
  --accent:#ea580c; --btn-neutral-bg:#e2e8f0; --btn-neutral-text:#1e293b;
  --s-ok-bg:#dcfce7; --s-ok-text:#166534;
  --s-err-bg:#fee2e2; --s-err-text:#991b1b;
  --chip-long-bg:#dcfce7; --chip-long-text:#166534; --chip-long-border:#bbf7d0;
  --chip-short-bg:#fee2e2; --chip-short-text:#991b1b; --chip-short-border:#fecaca;
  --pnl-pos:#16a34a; --pnl-neg:#dc2626;
  --badge-flat-bg:#f1f5f9; --badge-flat-text:#64748b; --badge-flat-border:#cbd5e1;
  --badge-long-bg:#dcfce7; --badge-long-text:#166534;
  --badge-short-bg:#fee2e2; --badge-short-text:#991b1b;
  --entry-bg:rgba(21,128,61,0.08); --entry-border:#166534;
  --exit-bg:rgba(185,28,28,0.06); --exit-border:#dc2626;
}
*{box-sizing:border-box;margin:0;padding:0;}
html,body{height:100%;overflow:hidden;}
body{font-family:'Segoe UI',Arial,sans-serif;background:var(--bg);color:var(--text);
  transition:background .2s,color .2s;}
#app-wrapper{width:100%;height:100%;overflow-y:auto;}
.topbar{display:flex;align-items:center;justify-content:space-between;
  background:#1e293b;padding:clamp(4px,1vw,10px) clamp(6px,2vw,16px);
  border-bottom:2px solid #0f3460;position:sticky;top:0;z-index:100;}
.topbar-right{display:flex;gap:clamp(3px,1vw,8px);align-items:center;flex-wrap:wrap;}
.api-dot{width:8px;height:8px;border-radius:50%;background:#475569;
  display:inline-block;transition:background .3s;}
#status-bar{padding:clamp(4px,1vw,7px) clamp(6px,2vw,16px);
  font-size:clamp(13px,2.5vw,16px);display:none;border-bottom:1px solid var(--border);}
.s-ok{background:var(--s-ok-bg);color:var(--s-ok-text);}
.s-err{background:var(--s-err-bg);color:var(--s-err-text);}
.pos-bar{background:var(--surface);padding:clamp(4px,1vw,8px) clamp(6px,2vw,16px);
  font-size:clamp(13px,2.5vw,15px);border-bottom:1px solid var(--border);
  min-height:28px;display:flex;flex-wrap:wrap;align-items:center;gap:clamp(3px,1vw,8px);}
.pos-chip{padding:2px clamp(4px,1.5vw,10px);border-radius:12px;
  font-size:clamp(13px,2.5vw,15px);font-weight:600;white-space:nowrap;}
.chip-long{background:var(--chip-long-bg);color:var(--chip-long-text);
  border:1px solid var(--chip-long-border);}
.chip-short{background:var(--chip-short-bg);color:var(--chip-short-text);
  border:1px solid var(--chip-short-border);}
.cards-row{display:flex;gap:clamp(4px,1vw,8px);padding:clamp(4px,1vw,8px);overflow-x:auto;}
.cards-row.vertical{flex-direction:column;overflow-x:visible;}
.card{background:var(--surface);border-radius:8px;padding:clamp(4px,1vw,8px);flex:1;
  min-width:0;max-width:290px;display:flex;flex-direction:column;gap:clamp(3px,0.8vw,6px);
  border:1px solid var(--border);transition:background .2s,border-color .2s;}
.cards-row.vertical .card{max-width:100%;min-width:100%;}
.card-title{font-size:clamp(13px,2.5vw,16px);font-weight:bold;color:var(--text);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.card-sym{font-size:clamp(12px,2vw,14px);color:var(--text-dim);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.pos-badge{font-size:clamp(13px,2.5vw,15px);font-weight:700;
  padding:2px clamp(4px,1.5vw,8px);border-radius:4px;display:inline-block;}
.badge-flat{background:var(--badge-flat-bg);color:var(--badge-flat-text);
  border:1px solid var(--badge-flat-border);}
.badge-long{background:var(--badge-long-bg);color:var(--badge-long-text);}
.badge-short{background:var(--badge-short-bg);color:var(--badge-short-text);}
.btn-row{display:flex;gap:clamp(3px,0.8vw,5px);flex-wrap:wrap;}
button{border:none;border-radius:4px;cursor:pointer;
  font-size:clamp(13px,2.5vw,15px);
  padding:clamp(3px,0.8vw,6px) clamp(5px,1.5vw,11px);
  font-weight:600;transition:opacity .15s;}
button:hover{opacity:.82;}
button:disabled{opacity:.35;cursor:not-allowed;}
.btn-long{background:#15803d;color:#fff;}
.btn-short{background:#b91c1c;color:#fff;}
.btn-close{background:#475569;color:#fff;width:100%;
  padding:clamp(3px,0.8vw,5px);font-size:clamp(13px,2.5vw,15px);}
.btn-half{background:#0e7490;color:#fff;}
.btn-rev{background:#92400e;color:#fff;}
.btn-add{background:#4c1d95;color:#fff;}
.btn-lim{background:#1d4ed8;color:#fff;flex-shrink:0;}
.btn-sl{background:#7c3aed;color:#fff;flex-shrink:0;}
.btn-refresh{background:var(--btn-neutral-bg);color:var(--btn-neutral-text);
  font-size:clamp(12px,2vw,14px);padding:clamp(2px,0.5vw,4px) clamp(4px,1vw,8px);}
.btn-theme{background:var(--btn-neutral-bg);color:var(--btn-neutral-text);
  padding:clamp(2px,0.5vw,4px) clamp(4px,1vw,7px);
  font-size:clamp(12px,2vw,14px);border:1px solid rgba(255,255,255,.15);}
.btn-settings{background:#1e40af;color:#e2e8f0;
  padding:clamp(2px,0.5vw,4px) clamp(4px,1vw,7px);
  font-size:clamp(12px,2vw,14px);text-decoration:none;border-radius:4px;font-weight:600;}
.entry-sec,.exit-sec{display:flex;flex-direction:column;gap:clamp(3px,0.8vw,5px);
  padding:clamp(3px,0.8vw,6px);border-radius:5px;}
.entry-sec{background:var(--entry-bg);border:1px solid var(--entry-border);}
.exit-sec{background:var(--exit-bg);border:1px solid var(--exit-border);}
.pending-orders-sec{background:rgba(30,64,175,0.1);border:1px solid #1e40af;}
.strike-nav{display:flex;align-items:center;justify-content:center;
  gap:clamp(2px,0.5vw,4px);margin:clamp(2px,0.5vw,4px) 0;
  padding:clamp(2px,0.5vw,4px);background:var(--surface);
  border-radius:5px;border:1px solid var(--border);}
.btn-nav{background:#3b82f6;color:#fff;border:none;border-radius:3px;cursor:pointer;
  font-size:clamp(12px,2vw,14px);padding:clamp(2px,0.5vw,3px) clamp(3px,0.8vw,5px);
  font-weight:600;line-height:1;}
.btn-nav:hover{background:#2563eb;}
.strike-display{font-weight:bold;color:var(--text);
  font-size:clamp(13px,2.5vw,16px);min-width:clamp(25px,4vw,40px);text-align:center;}
.sec-label{font-size:clamp(12px,2vw,14px);font-weight:700;letter-spacing:1px;
  color:var(--text-muted);text-transform:uppercase;}
input[type="number"]{width:100%;padding:clamp(3px,0.8vw,5px) clamp(4px,1vw,8px);
  background:var(--input-bg);border:1px solid var(--border);border-radius:4px;
  color:var(--text);font-size:clamp(13px,2.5vw,15px);
  transition:background .2s,border-color .2s;}
input[type="number"]::placeholder{color:var(--placeholder);}
.input-row{display:flex;gap:clamp(3px,0.8vw,5px);align-items:stretch;}
.input-row input{flex:1;min-width:0;width:1px;}
@media (max-width:640px){
  .cards-row{padding:10px;gap:10px;overflow-x:scroll;
    scroll-snap-type:x mandatory;-webkit-overflow-scrolling:touch;
    scrollbar-width:none;scroll-padding-left:10px;}
  .cards-row::-webkit-scrollbar{display:none;}
  .card{flex:0 0 calc(100vw - 20px);min-width:calc(100vw - 20px);
    max-width:none;scroll-snap-align:start;}
  button{padding:10px 14px;font-size:13px;}
  .btn-close{padding:12px;}
  input[type="number"]{padding:8px 10px;font-size:14px;}
}"""

_TRADING_JS = """\
function applyTheme(t){
  document.body.classList.toggle('light',t==='light');
  document.getElementById('theme-btn').textContent=t==='light'?'\u263d':'\u2600';
  document.getElementById('theme-btn').title=t==='light'?'Switch to Dark':'Switch to Light';
}
function toggleTheme(){
  const next=document.body.classList.contains('light')?'dark':'light';
  localStorage.setItem('theme',next);applyTheme(next);
}
function applyLayout(l){
  const container=document.getElementById('cards-container');
  container.classList.toggle('vertical',l==='vertical');
  document.getElementById('layout-btn').textContent=l==='vertical'?'\u2b0c':'\u2b0d';
  document.getElementById('layout-btn').title=l==='vertical'?'Switch to Horizontal':'Switch to Vertical';
}
function toggleLayout(){
  const container=document.getElementById('cards-container');
  const next=container.classList.contains('vertical')?'horizontal':'vertical';
  localStorage.setItem('layout',next);applyLayout(next);
}
applyTheme(localStorage.getItem('theme')||'dark');
applyLayout(localStorage.getItem('layout')||'vertical');


// width controlled by browser window resize

function showStatus(msg,isErr){
  const bar=document.getElementById('status-bar');
  bar.textContent=msg;bar.className=isErr?'s-err':'s-ok';
  bar.style.display='block';clearTimeout(bar._t);
  bar._t=setTimeout(()=>{bar.style.display='none';},5000);
}

async function refreshPositions(){
  try{
    const r=await fetch('/api/positions');
    const data=await r.json();
    renderPosBar(data);renderCards(data);
    await refreshPendingOrders();
    document.getElementById('api-dot').style.background='#4ade80';
  }catch(e){
    showStatus('Cannot reach server: '+e.message,true);
    document.getElementById('api-dot').style.background='#f87171';
  }
}

let pendingPollInterval=null;

async function syncAndRefreshPending(){
  try{
    await fetch('/api/sync_order_status',{method:'POST'});
    await refreshPendingOrders();
  }catch(e){
    console.error('Sync failed:',e);
  }
}

function startPendingOrderPolling(){
  if(pendingPollInterval)return;
  syncAndRefreshPending();
  pendingPollInterval=setInterval(syncAndRefreshPending,2000);
}

function stopPendingOrderPolling(){
  if(pendingPollInterval){
    clearInterval(pendingPollInterval);
    pendingPollInterval=null;
  }
}

async function refreshPendingOrders(){
  try{
    const r=await fetch('/api/pending_orders');
    const orders=await r.json();
    renderPendingOrdersInCards(orders);
    if(orders.length>0)startPendingOrderPolling();
    else stopPendingOrderPolling();
  }catch(e){
    console.error('Failed to load pending orders:',e);
  }
}

function renderPendingOrdersInCards(orders){
  SYMBOLS.forEach(sym=>{
    const symbolOrders=orders.filter(o=>o.symbol===sym);
    const container=document.getElementById('pending_'+sym);
    const list=document.getElementById('pending_list_'+sym);
    if(!symbolOrders.length){
      container.style.display='none';return;
    }
    container.style.display='block';
    list.innerHTML=symbolOrders.map(o=>{
      const priceText=o.pricetype==='MARKET'?'MKT':(o.pricetype==='SL'?`SL@${o.price}`:o.price);
      return `<div style="background:#1e40af;color:#e2e8f0;padding:4px 8px;border-radius:4px;margin-bottom:4px;font-size:11px;display:flex;justify-content:space-between;align-items:center;">`+
      `<span>${o.action} ${o.quantity} @ ${priceText}</span>`+
      `<button onclick="cancelOrder('${o.order_id}')" style="background:#dc2626;border:none;color:white;cursor:pointer;font-size:10px;padding:2px 6px;border-radius:3px;">✕</button>`+
      `</div>`;
    }).join('');
  });
}

function renderPosBar(positions){
  const bar=document.getElementById('pos-bar');
  if(!positions.length){
    bar.innerHTML='<span style="color:var(--text-muted);font-size:12px">No open positions</span>';
    return;
  }
  const isLight=document.body.classList.contains('light');
  const pnlPos=isLight?'#16a34a':'#4ade80';
  const pnlNeg=isLight?'#dc2626':'#f87171';
  bar.innerHTML=positions.map(p=>{
    const qty=parseInt(p.quantity||0);
    const pnl=parseFloat(p.pnl||0);
    const cls=qty>0?'chip-long':'chip-short';
    const pCol=pnl>=0?pnlPos:pnlNeg;
    const sign=qty>0?'+':'';
    return `<span class="pos-chip ${cls}">${p.symbol} ${sign}${qty}`+
           ` <span style="color:${pCol}">&#x20B9;${pnl.toFixed(2)}</span></span>`;
  }).join('');
}

function renderCards(positions){
  const posMap={};
  positions.forEach(p=>{posMap[p.symbol]=parseInt(p.quantity||0);});
  SYMBOLS.forEach(sym=>renderCard(sym,posMap[sym]||0));
}

function renderCard(sym,qty){
  const ls=LOT_SIZES[sym];
  const badge=document.getElementById('badge_'+sym);
  const btns=document.getElementById('btns_'+sym);
  if(qty===0){
    badge.textContent='FLAT';badge.className='pos-badge badge-flat';
    btns.innerHTML='';
  }else{
    const dir=qty>0?'LONG':'SHORT';
    const lots=(Math.abs(qty)/ls).toFixed(1);
    const sign=qty>0?'+':'';
    badge.textContent=`${dir} ${lots}L (${sign}${qty})`;
    badge.className=`pos-badge badge-${dir.toLowerCase()}`;
    const half=Math.trunc(qty/2);
    const rev=-qty;
    const addQ=qty+(qty>0?ls*QTY_LOTS:-(ls*QTY_LOTS));
    btns.innerHTML=
      `<button class="btn-half" onclick="smartOrder('${sym}',${half})">HALF</button>`+
      `<button class="btn-rev"  onclick="smartOrder('${sym}',${rev})">REVERSE</button>`+
      `<button class="btn-add"  onclick="smartOrder('${sym}',${addQ})">+${QTY_LOTS}L</button>`;
  }
}

async function sendOrder(payload){
  const allBtns=document.querySelectorAll('button');
  allBtns.forEach(b=>b.disabled=true);
  showStatus('Sending order\u2026',false);
  try{
    const r=await fetch('/api/smart_order',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(payload)
    });
    const d=await r.json();
    if(d.status==='success'){
      const detail=d.orderid?` \u2014 ID: ${d.orderid}`:d.message?`: ${d.message}`:'';
      showStatus('Order placed'+detail,false);
      setTimeout(refreshPositions,1500);
    }else{
      showStatus('Order failed: '+(d.message||JSON.stringify(d)),true);
    }
  }catch(e){
    showStatus('Network error: '+e.message,true);
  }finally{
    allBtns.forEach(b=>b.disabled=false);
  }
}

function smartOrder(sym,targetPos){
  sendOrder({symbol:sym,target_position:targetPos,pricetype:'MARKET'});
}
function smartEntry(sym,direction){
  const target=direction*LOT_SIZES[sym]*QTY_LOTS;
  const priceVal=document.getElementById('ep_'+sym).value.trim();
  if(priceVal&&parseFloat(priceVal)>0)
    sendOrder({symbol:sym,target_position:target,pricetype:'LIMIT',price:priceVal});
  else
    sendOrder({symbol:sym,target_position:target,pricetype:'MARKET'});
}
function smartClose(sym){
  sendOrder({symbol:sym,target_position:0,pricetype:'MARKET'});
}
function smartLimitExit(sym){
  const price=document.getElementById('lx_'+sym).value.trim();
  if(!price||parseFloat(price)<=0){showStatus('Enter a limit exit price first',true);return;}
  sendOrder({symbol:sym,target_position:0,pricetype:'LIMIT',price});
}
function smartSL(sym){
  const trig=document.getElementById('slt_'+sym).value.trim();
  const lim=document.getElementById('sll_'+sym).value.trim();
  if(!trig||parseFloat(trig)<=0){showStatus('Enter SL trigger price',true);return;}
  if(!lim||parseFloat(lim)<=0){showStatus('Enter SL limit price',true);return;}
  sendOrder({symbol:sym,target_position:0,pricetype:'SL',price:lim,trigger_price:trig});
}

async function changeStrike(instrument, optionType, delta){
  try{
    const r=await fetch('/api/update_strike',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({instrument:instrument,option_type:optionType,delta:delta})
    });
    const d=await r.json();
    if(d.status==='success'){
      const card=document.getElementById('card_'+d.old_symbol);
      if(card){
        // Replace all references to old symbol with new symbol in card HTML
        let newHTML=card.outerHTML.replaceAll(d.old_symbol,d.new_symbol);
        // Update the strike display text
        newHTML=newHTML.replace(
          'id="strike_disp_'+d.new_symbol+'">'+d.old_strike,
          'id="strike_disp_'+d.new_symbol+'">'+d.new_strike
        );
        card.outerHTML=newHTML;
        // Update LOT_SIZES so smartEntry works with the new symbol
        LOT_SIZES[d.new_symbol]=LOT_SIZES[d.old_symbol];
        delete LOT_SIZES[d.old_symbol];
        // Update SYMBOLS so refreshPositions/renderCards targets the new symbol
        const idx=SYMBOLS.indexOf(d.old_symbol);
        if(idx!==-1)SYMBOLS[idx]=d.new_symbol;
      }
    }else{
      showStatus('Strike update failed: '+(d.message||'Unknown'),true);
    }
  }catch(e){
    showStatus('Network error: '+e.message,true);
  }
}

async function cancelOrder(orderId){
  if(!confirm('Cancel this order?'))return;
  try{
    const r=await fetch('/api/cancel_order',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({order_id:orderId})
    });
    const d=await r.json();
    if(d.status==='success'){
      showStatus('Order cancelled',false);
      setTimeout(()=>{refreshPositions();refreshPendingOrders();},1000);
    }else{
      showStatus('Cancel failed: '+(d.message||'Unknown error'),true);
    }
  }catch(e){
    showStatus('Network error: '+e.message,true);
  }
}
refreshPositions();"""


def _card_html(label, sym, qty_lots, instrument_type):
    # Determine strike step based on instrument type
    is_banknifty = instrument_type == 'banknifty'
    step_small = 100 if is_banknifty else 50
    step_big = 500 if is_banknifty else 100
    # Detect if this is CE or PE from the symbol
    option_type = 'ce' if 'CE' in sym else 'pe'
    
    return (
        f'<div class="card" id="card_{sym}">\n'
        f'  <div class="card-title">{label}</div>\n'
        f'  <div class="strike-nav">\n'
        f'    <button class="btn-nav" onclick="changeStrike(\'{instrument_type}\', \'{option_type}\', -{step_big})" title="-{step_big}">\u25c0\u25c0</button>\n'
        f'    <button class="btn-nav" onclick="changeStrike(\'{instrument_type}\', \'{option_type}\', -{step_small})" title="-{step_small}">\u25c0</button>\n'
        f'    <span class="strike-display" id="strike_disp_{sym}">{sym[-7:-2]}</span>\n'
        f'    <button class="btn-nav" onclick="changeStrike(\'{instrument_type}\', \'{option_type}\', +{step_small})" title="+{step_small}">\u25b6</button>\n'
        f'    <button class="btn-nav" onclick="changeStrike(\'{instrument_type}\', \'{option_type}\', +{step_big})" title="+{step_big}">\u25b6\u25b6</button>\n'
        f'  </div>\n'
        f'  <div class="card-sym" title="{sym}" id="sym_{sym}">{sym}</div>\n'
        f'  <div class="pos-badge badge-flat" id="badge_{sym}">loading\u2026</div>\n'
        f'  <div class="btn-row" id="btns_{sym}"></div>\n'
        f'  <div id="pending_{sym}" class="pending-orders-sec" style="display:none;">\n'
        f'    <div class="sec-label">Pending Orders</div>\n'
        f'    <div id="pending_list_{sym}"></div>\n'
        f'  </div>\n'
        f'  <div class="entry-sec" id="entry_{sym}">\n'
        f'    <div class="sec-label">Entry</div>\n'
        f'    <input type="number" id="ep_{sym}" placeholder="Price (mkt if blank)" step="0.05">\n'
        f'    <div class="btn-row">\n'
        f'      <button class="btn-long" onclick="smartEntry(\'{sym}\',1)">LONG {qty_lots}L</button>\n'
        f'      <button class="btn-short" onclick="smartEntry(\'{sym}\',-1)">SHORT {qty_lots}L</button>\n'
        f'    </div>\n'
        f'  </div>\n'
        f'  <div class="exit-sec" id="exit_{sym}">\n'
        f'    <div class="sec-label">Exit / Manage</div>\n'
        f'    <button class="btn-close" onclick="smartClose(\'{sym}\')">CLOSE (Market)</button>\n'
        f'    <div class="input-row">\n'
        f'      <input type="number" id="lx_{sym}" placeholder="Exit px" step="0.05">\n'
        f'      <button class="btn-lim" onclick="smartLimitExit(\'{sym}\')">LIMIT EXIT</button>\n'
        f'    </div>\n'
        f'    <div class="input-row">\n'
        f'      <input type="number" id="slt_{sym}" placeholder="Trigger" step="0.05">\n'
        f'      <input type="number" id="sll_{sym}" placeholder="SL px" step="0.05">\n'
        f'      <button class="btn-sl" onclick="smartSL(\'{sym}\')">SET SL</button>\n'
        f'    </div>\n'
        f'  </div>\n'
        f'</div>\n'
    )


def render_trading(cards, lot_sizes, symbols, qty_lots):
    # Map symbols to instrument types
    sym_to_instrument = {}
    for label, sym, _ in cards:
        if 'banknifty' in sym.lower():
            sym_to_instrument[sym] = 'banknifty'
        else:
            sym_to_instrument[sym] = 'nifty'
    cards_html = ''.join(_card_html(label, sym, qty_lots, sym_to_instrument.get(sym, 'nifty')) for label, sym, _ in cards)
    return (
        '<!DOCTYPE html><html lang="en"><head>'
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>Trading App</title>'
        '<style>' + _TRADING_CSS + '</style>'
        '</head><body>\n'
        '<div id="app-wrapper">'
        '<div class="topbar">'
        ''
        '<div class="topbar-right">'
        '<span class="api-dot" id="api-dot" title="API status"></span>'
        '<button class="btn-theme" id="theme-btn" onclick="toggleTheme()" title="Toggle theme">\u263d</button>'
        '<button class="btn-theme" id="layout-btn" onclick="toggleLayout()" title="Switch to Horizontal">\u2b0c</button>'
        '<button class="btn-refresh" onclick="refreshPositions()" title="Refresh">\u21ba</button>'
        '<a href="/settings" class="btn-settings" title="Settings">\u2699\ufe0f</a>'
        '</div></div>\n'
        '<div id="status-bar"></div>\n'
        '<div class="pos-bar" id="pos-bar">'
        '<span style="color:var(--text-muted)">Loading positions\u2026</span>'
        '</div>\n'
        '<div class="cards-row" id="cards-container">\n' +
        cards_html +
        '</div>\n'
        '</div>\n'
        '<script>\n'
        'const LOT_SIZES=' + json.dumps(lot_sizes) + ';\n'
        'const SYMBOLS='   + json.dumps(symbols)   + ';\n'
        'const QTY_LOTS='  + str(qty_lots)          + ';\n' +
        _TRADING_JS +
        '\n</script></body></html>'
    )


# ── Settings page ─────────────────────────────────────────────────────────

_SETTINGS_CSS = """\
:root {
  --bg:#0f172a; --surface:#1e293b; --border:#334155; --text:#e2e8f0;
  --text-muted:#64748b; --text-dim:#94a3b8; --input-bg:#1e293b;
  --accent:#f97316; --btn-neutral-bg:#334155; --btn-neutral-text:#e2e8f0;
  --hint:#475569; --sep:#1e293b;
}
body.light {
  --bg:#f1f5f9; --surface:#ffffff; --border:#cbd5e1; --text:#1e293b;
  --text-muted:#64748b; --text-dim:#94a3b8; --input-bg:#ffffff;
  --accent:#ea580c; --btn-neutral-bg:#e2e8f0; --btn-neutral-text:#1e293b;
  --hint:#94a3b8; --sep:#e2e8f0;
}
*{box-sizing:border-box;margin:0;padding:0;}
body{font-family:'Segoe UI',Arial,sans-serif;background:var(--bg);
  color:var(--text);min-height:100vh;transition:background .2s,color .2s;}
.topbar{display:flex;align-items:center;justify-content:space-between;
  background:#1e293b;padding:10px 16px;border-bottom:2px solid #0f3460;
  margin-bottom:24px;}
.topbar h1{font-size:17px;color:#f97316;}
.topbar-right{display:flex;gap:8px;align-items:center;}
.btn-theme{background:#334155;color:#e2e8f0;border:1px solid rgba(255,255,255,.15);
  padding:5px 10px;font-size:11px;border-radius:5px;cursor:pointer;font-weight:600;}
.btn-theme:hover{opacity:.82;}
.container{max-width:580px;margin:0 auto;padding:0 16px 40px;}
.info-box{padding:10px 14px;border-radius:6px;font-size:13px;
  margin-bottom:16px;line-height:1.6;}
.info-ok{background:#14532d;color:#86efac;border:1px solid #166534;}
.info-warn{background:#431407;color:#fdba74;border:1px solid #7c2d12;}
body.light .info-ok{background:#dcfce7;color:#166534;border-color:#bbf7d0;}
body.light .info-warn{background:#fff7ed;color:#9a3412;border-color:#fed7aa;}
h2{font-size:11px;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;
  color:var(--text-muted);margin:20px 0 10px;}
.field{margin-bottom:12px;}
label{display:block;font-size:12px;color:var(--text-dim);margin-bottom:4px;}
input[type="text"],input[type="number"],input[type="date"],select{
  width:100%;padding:8px 10px;background:var(--input-bg);
  border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:14px;
  transition:background .2s,border-color .2s;}
input:focus,select:focus{outline:none;border-color:#3b82f6;}
select option{background:var(--input-bg);color:var(--text);}
.row{display:flex;gap:10px;}
.row .field{flex:1;}
.hint{font-size:11px;color:var(--hint);margin-top:3px;}
.sep{height:1px;background:var(--sep);margin:16px 0;}
.actions{display:flex;gap:10px;margin-top:24px;}
.btn-save{flex:1;padding:10px;background:#15803d;color:#fff;
  border:none;border-radius:6px;font-size:15px;font-weight:bold;cursor:pointer;}
.btn-save:hover{background:#166534;}
.btn-back{padding:10px 18px;background:var(--btn-neutral-bg);
  color:var(--btn-neutral-text);border:none;border-radius:6px;font-size:14px;
  cursor:pointer;text-decoration:none;display:flex;align-items:center;
  transition:background .2s,color .2s;}"""

_SETTINGS_JS = """\
function applyTheme(t){
  document.body.classList.toggle('light',t==='light');
  document.getElementById('theme-btn').textContent=t==='light'?'Dark':'Light';
}
function toggleTheme(){
  const next=document.body.classList.contains('light')?'dark':'light';
  localStorage.setItem('theme',next);applyTheme(next);
}
applyTheme(localStorage.getItem('theme')||'dark');

// Date conversion for expiry fields
function convertToExpiryFormat(prefix) {
  const datePicker = document.getElementById(prefix + '_expiry_picker');
  const textField = document.getElementById(prefix + '_expiry');
  
  if (!datePicker.value) {
    textField.value = '';
    return;
  }
  
  const date = new Date(datePicker.value);
  const months = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 
                  'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'];
  
  const day = date.getDate().toString().padStart(2, '0');
  const month = months[date.getMonth()];
  const year = date.getFullYear().toString().slice(-2);
  
  textField.value = day + month + year;
}

// Initialize date pickers with current expiry values
function parseExpiryToDate(expiryStr) {
  if (!expiryStr || expiryStr.length !== 7) return '';
  
  const day = expiryStr.substring(0, 2);
  const monthStr = expiryStr.substring(2, 5);
  const year = '20' + expiryStr.substring(5, 7);
  
  const months = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 
                  'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'];
  const monthNum = months.indexOf(monthStr) + 1;
  
  if (monthNum === 0) return '';
  
  return `${year}-${monthNum.toString().padStart(2, '0')}-${day}`;
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
  const niftyExpiry = document.getElementById('nifty_expiry').value;
  const bankniftyExpiry = document.getElementById('banknifty_expiry').value;
  
  document.getElementById('nifty_expiry_picker').value = parseExpiryToDate(niftyExpiry);
  document.getElementById('banknifty_expiry_picker').value = parseExpiryToDate(bankniftyExpiry);
});"""


def render_settings(s, api_ok, openalgo_url):
    n, b, c, ui = s['nifty'], s['banknifty'], s['common'], s['ui']

    if api_ok:
        banner = (f'<div class="info-box info-ok">'
                  f'API key configured &nbsp;&middot;&nbsp; {openalgo_url}'
                  f'</div>\n')
    else:
        banner = (f'<div class="info-box info-warn">'
                  f'API key not set. Edit <strong>.env</strong> in the app folder:<br>'
                  f'<code style="font-size:12px">OPENALGO_API_KEY=your_key_here</code><br>'
                  f'<code style="font-size:12px">OPENALGO_URL={openalgo_url}</code>'
                  f'</div>\n')

    mis_sel  = 'selected' if c['product'] == 'MIS'  else ''
    nrml_sel = 'selected' if c['product'] == 'NRML' else ''
    horz_sel = 'selected' if ui['cards_layout'] == 'horizontal' else ''
    vert_sel = 'selected' if ui['cards_layout'] == 'vertical' else ''

    form = (
        '<form method="POST" action="/settings">\n'
        '<h2>NIFTY</h2>\n'
        '<div class="field"><label>Expiry</label>'
        '<input type="date" id="nifty_expiry_picker" onchange="convertToExpiryFormat(\'nifty\')" style="margin-bottom: 8px;">'
        f'<input type="text" name="nifty_expiry" id="nifty_expiry" value="{n["expiry"]}" readonly style="background: var(--hint); cursor: not-allowed;" required>'
        '<div class="hint">Auto-converted from date picker above (DDMMMYY format)</div></div>\n'
        '<div class="row">'
        '<div class="field"><label>CE Strike</label>'
        f'<input type="text" name="nifty_strike_ce" value="{n["strike_ce"]}" required></div>'
        '<div class="field"><label>PE Strike</label>'
        f'<input type="text" name="nifty_strike_pe" value="{n["strike_pe"]}" required></div>'
        '<div class="field"><label>Lot Size</label>'
        f'<input type="number" name="nifty_lot_size" value="{n["lot_size"]}" min="1" required></div>'
        '</div>\n'
        '<div class="sep"></div>\n'
        '<h2>BANKNIFTY</h2>\n'
        '<div class="field"><label>Expiry</label>'
        '<input type="date" id="banknifty_expiry_picker" onchange="convertToExpiryFormat(\'banknifty\')" style="margin-bottom: 8px;">'
        f'<input type="text" name="banknifty_expiry" id="banknifty_expiry" value="{b["expiry"]}" readonly style="background: var(--hint); cursor: not-allowed;" required>'
        '<div class="hint">Auto-converted from date picker above (DDMMMYY format)</div></div>\n'
        '<div class="row">'
        '<div class="field"><label>CE Strike</label>'
        f'<input type="text" name="banknifty_strike_ce" value="{b["strike_ce"]}" required></div>'
        '<div class="field"><label>PE Strike</label>'
        f'<input type="text" name="banknifty_strike_pe" value="{b["strike_pe"]}" required></div>'
        '<div class="field"><label>Lot Size</label>'
        f'<input type="number" name="banknifty_lot_size" value="{b["lot_size"]}" min="1" required></div>'
        '</div>\n'
        '<div class="sep"></div>\n'
        '<h2>Order Defaults</h2>\n'
        '<div class="row">'
        '<div class="field"><label>Default Lots</label>'
        f'<input type="number" name="quantity_lots" value="{c["quantity_lots"]}" min="1" required>'
        '<div class="hint">Used for LONG / SHORT entry buttons</div></div>'
        '<div class="field"><label>Product Type</label>'
        '<select name="product">'
        f'<option value="MIS" {mis_sel}>MIS (Intraday)</option>'
        f'<option value="NRML" {nrml_sel}>NRML (Overnight F&amp;O)</option>'
        '</select></div>'
        '</div>\n'
        '<div class="sep"></div>\n'
        '<h2>User Interface</h2>\n'
        '<div class="field"><label>Cards Layout</label>'
        '<select name="cards_layout">'
        f'<option value="horizontal" {horz_sel}>Horizontal (side by side)</option>'
        f'<option value="vertical" {vert_sel}>Vertical (stacked)</option>'
        '</select>'
        '<div class="hint">How trading cards are arranged on screen</div></div>\n'
        ''
        '<div class="actions">'
        '<a href="/" class="btn-back">Back</a>'
        '<button type="submit" class="btn-save">Save Settings</button>'
        '</div>\n'
        '</form>\n'
    )

    return (
        '<!DOCTYPE html><html lang="en"><head>'
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>Settings \u2014 Trading App</title>'
        '<style>' + _SETTINGS_CSS + '</style>'
        '</head><body>\n'
        '<div class="topbar"><h1>Settings</h1>'
        '<div class="topbar-right">'
        '<button class="btn-theme" id="theme-btn" onclick="toggleTheme()">Light</button>'
        '</div></div>\n'
        '<div class="container">\n' +
        banner + form +
        '</div>\n'
        '<script>' + _SETTINGS_JS + '</script>'
        '</body></html>'
    )


# ── HTTP Handler ──────────────────────────────────────────────────────────

class _Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):  # noqa: keep default logging
        print(f"  [{self.address_string()}] {fmt % args}")

    # ── helpers ──

    def _send_html(self, html, code=200):
        body = html.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, data, code=200):
        body = json.dumps(data).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location):
        self.send_response(302)
        self.send_header('Location', location)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def _read_body(self):
        length = int(self.headers.get('Content-Length', 0))
        return self.rfile.read(length) if length else b''

    # ── routes ──

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == '/':
                settings  = load_settings()
                syms      = build_symbols(settings)
                cards = [
                    ('NIFTY CE',  syms['nifty_ce'],     settings['nifty']['lot_size']),
                    ('NIFTY PE',  syms['nifty_pe'],     settings['nifty']['lot_size']),
                    ('BNIFTY CE', syms['banknifty_ce'], settings['banknifty']['lot_size']),
                    ('BNIFTY PE', syms['banknifty_pe'], settings['banknifty']['lot_size']),
                ]
                lot_sizes = {sym: ls for _, sym, ls in cards}
                symbols   = [sym for _, sym, _ in cards]
                self._send_html(render_trading(
                    cards, lot_sizes, symbols,
                    settings['common']['quantity_lots']))

            elif path == '/settings':
                s = load_settings()
                self._send_html(render_settings(s, bool(OPENALGO_API_KEY), OPENALGO_URL))

            elif path == '/api/positions':
                self._send_json(get_positions())

            elif path == '/api/pending_orders':
                self._send_json(get_pending_orders())

            elif path == '/api/sync_order_status':
                self._send_json(sync_order_status())

            else:
                self._send_html('<h3>404 Not Found</h3>', 404)

        except Exception as exc:
            self._send_html(f'<h3>Server Error</h3><pre>{exc}</pre>', 500)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if path == '/settings':
                raw  = self._read_body().decode('utf-8')
                form = parse_qs(raw)
                def fg(k, default=''): return form.get(k, [default])[0]
                save_settings({
                    'nifty': {
                        'expiry':    fg('nifty_expiry').strip().upper(),
                        'strike_ce': fg('nifty_strike_ce').strip(),
                        'strike_pe': fg('nifty_strike_pe').strip(),
                        'lot_size':  int(fg('nifty_lot_size', '65')),
                    },
                    'banknifty': {
                        'expiry':    fg('banknifty_expiry').strip().upper(),
                        'strike_ce': fg('banknifty_strike_ce').strip(),
                        'strike_pe': fg('banknifty_strike_pe').strip(),
                        'lot_size':  int(fg('banknifty_lot_size', '30')),
                    },
                    'common': {
                        'quantity_lots': int(fg('quantity_lots', '2')),
                        'product':       fg('product', 'MIS'),
                    },
                    'ui': {
                        'cards_layout': fg('cards_layout', 'horizontal'),
                    },
                })
                self._redirect('/')

            elif path == '/api/smart_order':
                data   = json.loads(self._read_body().decode('utf-8'))
                result = place_smart_order(
                    symbol          = data['symbol'],
                    target_position = int(data['target_position']),
                    pricetype       = data.get('pricetype', 'MARKET'),
                    price           = data.get('price'),
                    trigger_price   = data.get('trigger_price'),
                )
                self._send_json(result)

            elif path == '/api/cancel_order':
                data   = json.loads(self._read_body().decode('utf-8'))
                result = cancel_order_by_id(data['order_id'])
                self._send_json(result)

            elif path == '/api/update_strike':
                data = json.loads(self._read_body().decode('utf-8'))
                instrument = data.get('instrument')
                option_type = data.get('option_type', 'ce')
                delta = int(data.get('delta', 0))
                
                settings = load_settings()
                syms = build_symbols(settings)
                
                if instrument == 'nifty':
                    old_strike = int(settings['nifty'][f'strike_{option_type}'])
                    new_strike = old_strike + delta
                    settings['nifty'][f'strike_{option_type}'] = str(new_strike)
                    expiry = settings['nifty']['expiry']
                    suffix = 'CE' if option_type == 'ce' else 'PE'
                    old_symbol = f"NIFTY{expiry}{old_strike}{suffix}"
                    new_symbol = f"NIFTY{expiry}{new_strike}{suffix}"
                elif instrument == 'banknifty':
                    old_strike = int(settings['banknifty'][f'strike_{option_type}'])
                    new_strike = old_strike + delta
                    settings['banknifty'][f'strike_{option_type}'] = str(new_strike)
                    expiry = settings['banknifty']['expiry']
                    suffix = 'CE' if option_type == 'ce' else 'PE'
                    old_symbol = f"BANKNIFTY{expiry}{old_strike}{suffix}"
                    new_symbol = f"BANKNIFTY{expiry}{new_strike}{suffix}"
                
                save_settings(settings)
                self._send_json({
                    'status': 'success',
                    'old_strike': str(old_strike),
                    'new_strike': str(new_strike),
                    'old_symbol': old_symbol,
                    'new_symbol': new_symbol
                })

            else:
                self._send_html('<h3>404 Not Found</h3>', 404)

        except Exception as exc:
            self._send_json({'status': 'error', 'message': str(exc)}, 500)


class _ThreadingServer(socketserver.ThreadingMixIn, HTTPServer):
    """Handle each request in a separate thread."""
    daemon_threads = True


# ── Entry point ──────────────────────────────────────────────────────────

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5003))
    api_status = 'set' if OPENALGO_API_KEY else 'NOT SET \u2014 edit .env'
    print(f"Trading App  \u2192  http://localhost:{port}")
    print(f"OpenAlgo URL \u2192  {OPENALGO_URL}")
    print(f"API key      \u2192  {api_status}")
    print(f"Settings     \u2192  {SETTINGS_FILE}\n")
    server = _ThreadingServer(('0.0.0.0', port), _Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
