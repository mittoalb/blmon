#!/usr/bin/env python3
"""Local status server for 32-ID tomography beamline.

Expose realtime EPICS scan/shutter status plus reconstruction status (from tomogui-style table)
via HTTP (JSON + simple web UI).

Usage:
  python blmon_status_server.py --data-folder /path/to/data

Dependencies:
  pip install flask epics

"""

import argparse
import glob
import os
import threading
import time
from datetime import datetime

from flask import Flask, jsonify, render_template_string, request

try:
    from epics import PV
except ImportError:
    PV = None

app = Flask(__name__)

# Default PV names (can be overridden by CLI args)
DEFAULT_SCAN_PV = "32id:TomoScan:ScanStatus"
DEFAULT_SHUTTER_A_PV = "PB:32ID:STA_A_FES_CLSD_PL"
DEFAULT_SHUTTER_B_PV = "PB:32ID:STA_B_SBS_CLSD_PL"

status_cache = {
    "scan_status": None,
    "shutter_A": None,
    "shutter_B": None,
    "reconstruction": None,
    "updated_at": None,
    "error": None,
}
status_lock = threading.Lock()


def read_pv_value(pv_name, timeout=2.0):
    if PV is None:
        return None, "epics module not installed"

    try:
        pv = PV(pv_name)
        if not pv.connect(timeout=timeout):
            return None, "pv connect timeout"

        value = pv.get(timeout=timeout)
        if value is None:
            return None, "no value"
        return value, "ok"

    except Exception as ex:
        return None, f"error: {ex}"


def compute_recon_status(data_folder):
    """Based on tomogui logic in refresh_main_table() from Software/tomogui."""
    path = os.path.expanduser(data_folder)
    if not path or not os.path.isdir(path):
        return {
            "available": False,
            "message": f"Invalid data folder: {path}",
            "files_total": 0,
            "files_full": 0,
            "files_try": 0,
            "files_ready": 0,
            "details": [],
        }

    h5_files = sorted(glob.glob(os.path.join(path, "*.h5")), key=os.path.getmtime, reverse=True)

    details = []
    full_count = 0
    try_count = 0
    ready_count = 0

    for f in h5_files:
        filename = os.path.basename(f)
        proj_name = os.path.splitext(filename)[0]

        try_dir = os.path.join(f"{path}_rec", "try_center", proj_name)
        full_dir = os.path.join(f"{path}_rec", f"{proj_name}_rec")

        has_try = os.path.isdir(try_dir) and len(glob.glob(os.path.join(try_dir, "*.tiff"))) > 0
        has_full = os.path.isdir(full_dir) and len(glob.glob(os.path.join(full_dir, "*.tiff"))) > 0

        if has_full:
            status_name = "full"
            full_count += 1
        elif has_try:
            status_name = "try"
            try_count += 1
        else:
            status_name = "ready"
            ready_count += 1

        details.append({
            "filename": filename,
            "proj_name": proj_name,
            "status": status_name,
            "has_try": has_try,
            "has_full": has_full,
            "try_dir": try_dir,
            "full_dir": full_dir,
        })

    return {
        "available": True,
        "files_total": len(h5_files),
        "files_full": full_count,
        "files_try": try_count,
        "files_ready": ready_count,
        "details": details,
    }


def refresh_status(scan_pv, shutter_a, shutter_b, data_folder):
    out = {
        "scan_status": {"value": None, "info": "not-set"},
        "shutter_A": {"value": None, "info": "not-set"},
        "shutter_B": {"value": None, "info": "not-set"},
        "reconstruction": None,
        "updated_at": datetime.utcnow().isoformat() + "Z",
        "error": None,
    }

    try:
        v, info = read_pv_value(scan_pv)
        out["scan_status"] = {"value": v, "info": info, "pv": scan_pv}

        v, info = read_pv_value(shutter_a)
        out["shutter_A"] = {"value": v, "info": info, "pv": shutter_a}

        v, info = read_pv_value(shutter_b)
        out["shutter_B"] = {"value": v, "info": info, "pv": shutter_b}

    except Exception as ex:
        out["error"] = f"EPICS query exception: {ex}"

    out["reconstruction"] = compute_recon_status(data_folder)
    out["updated_at"] = datetime.utcnow().isoformat() + "Z"

    with status_lock:
        status_cache.update(out)


def background_updater(scan_pv, shutter_a, shutter_b, data_folder, interval=5):
    while True:
        try:
            refresh_status(scan_pv, shutter_a, shutter_b, data_folder)
        except Exception as ex:
            with status_lock:
                status_cache["error"] = str(ex)
                status_cache["updated_at"] = datetime.utcnow().isoformat() + "Z"
        time.sleep(interval)


HTML_TEMPLATE = """
<!doctype html>
<html><head><meta charset="utf-8"><title>Beamline Status</title>
<style>
body { font-family: Inter, Helvetica, Arial, sans-serif; margin: 0; background: #f5f7fa; color: #1f2937; }
.container { max-width: 1180px; margin: 1rem auto; padding: 0 12px; }
.page-title { margin-top: 1rem; margin-bottom: 0.5rem; font-size: 1.6rem; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 14px; }
.card { background: #fff; border: 1px solid #dbe0ea; border-radius: 10px; box-shadow: 0 1px 6px rgba(15, 23, 42, 0.06); padding: 14px; }
.card h2 { margin-top: 0; font-size: 1.05rem; border-bottom: 1px solid #e2e8f0; padding-bottom: 6px; }
.small-note { color: #374151; font-size: 0.88rem; margin: 0; }
.status-label { display: inline-block; min-width: 56px; font-weight: 700; }
.badge { padding: 2px 8px; border-radius: 999px; font-size: 0.8rem; font-weight: 700; color: #fff; }
.badge-green { background: #16a34a; }
.badge-orange { background: #f59e0b; }
.badge-red { background: #dc2626; }
.table-wrap { max-height: 360px; overflow: auto; border-radius: 8px; border: 1px solid #dbe0ea; }
table { border-collapse: collapse; width: 100%; }
th, td { padding: 8px 10px; text-align: left; border-bottom: 1px solid #e2e8f0; }
th { background: #f8fafc; font-size: 0.87rem; color: #334155; }
.tooltip-small { font-size: 0.8rem; color: #6b7280; }
#poll-interval { width: 75px; }
.err-text { color: #b91c1c; font-weight: 600; }
</style>
</head><body>
<div class="container">
  <h1 class="page-title">Beamline Monitor (local)</h1>
  <p class="small-note">Last update: <span id="updated_at">-</span> | Poll interval (s): <input id="poll-interval" type="number" min="1" max="60" value="5" step="1" /> </p>
  <div class="grid">
    <div class="card">
      <h2>Scan Engine</h2>
      <p><span class="status-label">PV</span><code>{{ scan_pv }}</code></p>
      <p><span class="status-label">Value</span><strong id="scan_value">-</strong> <span class="tooltip-small">(<span id="scan_info">-</span>)</span></p>
    </div>
    <div class="card">
      <h2>Shutters</h2>
      <p><span class="status-label">A</span><span id="shutterA" class="badge badge-red">-</span> <span class="tooltip-small">(<span id="shutterA_info">-</span>)</span></p>
      <p><span class="status-label">B</span><span id="shutterB" class="badge badge-red">-</span> <span class="tooltip-small">(<span id="shutterB_info">-</span>)</span></p>
    </div>
    <div class="card">
      <h2>Summary</h2>
      <p>Total files: <strong id="total">-</strong></p>
      <p>Full: <strong id="full">-</strong>, Try: <strong id="try">-</strong>, Ready: <strong id="ready">-</strong></p>
      <p class="small-note">Data folder: <code>{{ data_folder }}</code></p>
    </div>
  </div>

  <div class="card" style="margin-top: 8px;">
    <h2>Reconstruction details</h2>
    <div class="table-wrap">
      <table>
        <thead>
          <tr><th>Filename</th><th>Status</th><th>Try</th><th>Full</th></tr>
        </thead>
        <tbody id="detail_table_body"></tbody>
      </table>
    </div>
  </div>

  <div class="card" style="margin-top: 8px;">
    <h2>Error</h2>
    <p class="err-text" id="err">-</p>
  </div>
</div>

<script>
let pollTimer = null;

function statusBadge(value) {
  if (value === 'full') return '<span class="badge badge-green">FULL</span>';
  if (value === 'try') return '<span class="badge badge-orange">TRY</span>';
  if (value === 'ready') return '<span class="badge badge-red">READY</span>';
  return '<span class="badge badge-red">UNKNOWN</span>';
}

function updateShutterBadge(elementId, value) {
  const el = document.getElementById(elementId);
  if (!el) return;
  let cls='badge badge-red';
  let text = value;
  if (value===1 || value==='1' || value==='true' || value===true) { cls='badge badge-green'; text='OPEN'; }
  else if (value===0 || value==='0' || value==='false' || value===false) { cls='badge badge-red'; text='CLOSED'; }
  else { cls='badge badge-orange'; text=value; }
  el.className = cls;
  el.innerText = text;
}

async function refresh() {
  try {
    const resp = await fetch('/api/status');
    const j = await resp.json();
    document.getElementById('updated_at').innerText = j.updated_at || '-';
    document.getElementById('scan_value').innerText = j.scan_status.value || '-';
    document.getElementById('scan_info').innerText = j.scan_status.info || '-';
    updateShutterBadge('shutterA', j.shutter_A.value);
    document.getElementById('shutterA_info').innerText = j.shutter_A.info || '-';
    updateShutterBadge('shutterB', j.shutter_B.value);
    document.getElementById('shutterB_info').innerText = j.shutter_B.info || '-';
    document.getElementById('total').innerText = j.reconstruction.files_total;
    document.getElementById('full').innerText = j.reconstruction.files_full;
    document.getElementById('try').innerText = j.reconstruction.files_try;
    document.getElementById('ready').innerText = j.reconstruction.files_ready;

    const detailBody = document.getElementById('detail_table_body');
    detailBody.innerHTML = '';

    (j.reconstruction.details || []).forEach(item => {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td>${item.filename}</td>
        <td>${statusBadge(item.status)}</td>
        <td>${item.has_try ? 'Yes' : 'No'}</td>
        <td>${item.has_full ? 'Yes' : 'No'}</td>
      `;
      detailBody.appendChild(tr);
    });

    document.getElementById('err').innerText = j.error || '-';
  } catch (err) {
    document.getElementById('err').innerText = 'Communication error: ' + err;
  }
}

function scheduleRefresh() {
  if (pollTimer) clearInterval(pollTimer);
  const val = parseInt(document.getElementById('poll-interval').value, 10);
  const interval = Number.isFinite(val) && val >= 1 ? val * 1000 : 5000;
  pollTimer = setInterval(refresh, interval);
}

document.getElementById('poll-interval').addEventListener('change', scheduleRefresh);

scheduleRefresh();
refresh();
</script>
</body></html>
"""


@app.route('/')
def index():
    args = request.args
    return render_template_string(HTML_TEMPLATE,
         scan_pv=app.config.get('SCAN_PV', DEFAULT_SCAN_PV),
         shutter_a_pv=app.config.get('SHUTTER_A_PV', DEFAULT_SHUTTER_A_PV),
         shutter_b_pv=app.config.get('SHUTTER_B_PV', DEFAULT_SHUTTER_B_PV),
         data_folder=app.config.get('DATA_FOLDER', '(not configured)'),
    )


@app.route('/api/status')
def api_status():
    with status_lock:
        data = dict(status_cache)
    return jsonify(data)


def main():
    parser = argparse.ArgumentParser(description="Beamline status server for 32id tomographic scan and reconstruction")
    parser.add_argument('--host', default='0.0.0.0', help='web host')
    parser.add_argument('--port', default=8080, type=int, help='web port')
    parser.add_argument('--data-folder', default=os.path.expanduser('~'), help='data folder (tomogui table root, containing .h5 files)')
    parser.add_argument('--scan-pv', default=DEFAULT_SCAN_PV, help='Scan status PV')
    parser.add_argument('--shutter-a-pv', default=DEFAULT_SHUTTER_A_PV, help='Shutter A PV')
    parser.add_argument('--shutter-b-pv', default=DEFAULT_SHUTTER_B_PV, help='Shutter B PV')
    parser.add_argument('--poll-interval', default=5, type=float, help='status refresh interval seconds')
    args = parser.parse_args()

    app.config['SCAN_PV'] = args.scan_pv
    app.config['SHUTTER_A_PV'] = args.shutter_a_pv
    app.config['SHUTTER_B_PV'] = args.shutter_b_pv
    app.config['DATA_FOLDER'] = args.data_folder

    refresh_status(args.scan_pv, args.shutter_a_pv, args.shutter_b_pv, args.data_folder)
    t = threading.Thread(target=background_updater, args=(args.scan_pv, args.shutter_a_pv, args.shutter_b_pv, args.data_folder, args.poll_interval), daemon=True)
    t.start()

    print(f"Starting beamline status server on http://{args.host}:{args.port}/")
    print(f"scan PV: {args.scan_pv}")
    print(f"shutter A PV: {args.shutter_a_pv}")
    print(f"shutter B PV: {args.shutter_b_pv}")
    print(f"data folder: {args.data_folder}")

    app.run(host=args.host, port=args.port, debug=False)


if __name__ == '__main__':
    main()
