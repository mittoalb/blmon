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
except ImportError as ex:
    PV = None
    import traceback
    print('WARNING: epics PV class unavailable -- got ImportError:', ex)
    print(traceback.format_exc())

app = Flask(__name__)

# Default PV names (can be overridden by CLI args)
DEFAULT_SCAN_PV = "32id:TomoScan:ScanStatus"
DEFAULT_SHUTTER_A_PV = "PB:32ID:STA_A_FES_CLSD_PL"
DEFAULT_SHUTTER_B_PV = "PB:32ID:STA_B_SBS_CLSD_PL"
DEFAULT_FILE_PV = "32idbSP1:HDF1:FullFileName_RBV"  # name/path of the last scan

status_cache = {
    "scan_status": None,
    "shutter_A": None,
    "shutter_B": None,
    "current_file": None,
    "reconstruction": None,
    "updated_at": None,
    "error": None,
}
status_lock = threading.Lock()


def read_pv_value(pv_name, timeout=2.0):
    if PV is None:
        return None, "epics module not installed in this Python environment; install pyepics (pip install pyepics) or use the same env as your beamline stack."

    try:
        pv = PV(pv_name)
        if not pv.connect(timeout=timeout):
            return None, "pv connect timeout"

        # Try to read as string first (for char arrays and string PVs)
        try:
            value = pv.get(as_string=True, timeout=timeout)
            if value is not None:
                # Clean up any null terminators
                if isinstance(value, bytes):
                    value = value.decode('utf-8', errors='ignore').rstrip('\x00')
                else:
                    value = str(value).rstrip('\x00')
                return value, "ok"
        except:
            pass

        # Fall back to normal get
        value = pv.get(timeout=timeout)
        if value is None:
            return None, "no value"
        return value, "ok"

    except Exception as ex:
        return None, f"error: {ex}"


def compute_recon_status(data_folder, last_scan_file=None):
    """Based on tomogui logic in refresh_main_table() from Software/tomogui.

    If `last_scan_file` is provided and points to a valid file, derive the data folder
    from its directory; otherwise use explicit `data_folder`.
    """
    path = None
    if last_scan_file:
        candidate = os.path.expanduser(str(last_scan_file))
        if os.path.isfile(candidate):
            path = os.path.dirname(candidate)
        else:
            path = None

    if not path:
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


def refresh_status(scan_pv, shutter_a, shutter_b, data_folder, file_pv):
    out = {
        "scan_status": {"value": None, "info": "not-set"},
        "shutter_A": {"value": None, "info": "not-set"},
        "shutter_B": {"value": None, "info": "not-set"},
        "current_file": {"value": None, "info": "not-set", "pv": file_pv},
        "reconstruction": None,
        "updated_at": datetime.utcnow().isoformat() + "Z",
        "error": None,
    }

    last_scan_file = None

    try:
        v, info = read_pv_value(scan_pv)
        out["scan_status"] = {"value": v, "info": info, "pv": scan_pv}

        v, info = read_pv_value(shutter_a)
        out["shutter_A"] = {"value": v, "info": info, "pv": shutter_a}

        v, info = read_pv_value(shutter_b)
        out["shutter_B"] = {"value": v, "info": info, "pv": shutter_b}

        v, info = read_pv_value(file_pv)
        last_scan_file = v if v else None
        out["current_file"] = {"value": v, "info": info, "pv": file_pv}

    except Exception as ex:
        out["error"] = f"EPICS query exception: {ex}"

    out["reconstruction"] = compute_recon_status(data_folder, last_scan_file=last_scan_file)
    out["updated_at"] = datetime.utcnow().isoformat() + "Z"

    with status_lock:
        status_cache.update(out)


def background_updater(scan_pv, shutter_a, shutter_b, data_folder, file_pv, interval=5):
    while True:
        try:
            refresh_status(scan_pv, shutter_a, shutter_b, data_folder, file_pv)
        except Exception as ex:
            with status_lock:
                status_cache["error"] = str(ex)
                status_cache["updated_at"] = datetime.utcnow().isoformat() + "Z"
        time.sleep(interval)


HTML_TEMPLATE = """
<!doctype html>
<html><head><meta charset="utf-8"><title>Beamline Status</title>
<style>
body { font-family: Inter, Helvetica, Arial, sans-serif; margin: 0; background: #0f172a; color: #e2e8f0; }
.container { max-width: 1180px; margin: 1rem auto; padding: 0 12px; }
.page-title { margin-top: 1rem; margin-bottom: 0.5rem; font-size: 1.75rem; color: #f8fafc; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 14px; }
.card { background: #1e293b; border: 1px solid #334155; border-radius: 10px; box-shadow: 0 6px 18px rgba(0, 0, 0, 0.35); padding: 16px; }
.card h2 { margin-top: 0; font-size: 1.05rem; border-bottom: 1px solid #334155; padding-bottom: 6px; color: #f8fafc; }
.small-note { color: #94a3b8; font-size: 0.9rem; margin: 0; }
.status-label { display: inline-block; min-width: 56px; font-weight: 700; color: #e2e8f0; }
.badge { padding: 4px 10px; border-radius: 999px; font-size: 0.82rem; font-weight: 700; color: #fff; }
.badge-green { background: #22c55e; }
.badge-orange { background: #f59e0b; }
.badge-red { background: #ef4444; }
.table-wrap { max-height: 410px; overflow: auto; border-radius: 8px; border: 1px solid #334155; }
table { border-collapse: collapse; width: 100%; background: #0f172a; }
th, td { padding: 10px 12px; text-align: left; border-bottom: 1px solid #334155; color: #e2e8f0; }
th { background: #1e293b; font-size: 0.88rem; color: #e2e8f0; }
.tooltip-small { font-size: 0.8rem; color: #94a3b8; }
#poll-interval { width: 88px; background: #0f172a; color: #e2e8f0; border: 1px solid #334155; border-radius: 5px; }
.err-text { color: #f87171; font-weight: 600; }
</style>
</head><body>
<div class="container">
  <h1 class="page-title">32ID Beamline Monitor</h1>
  <p class="small-note">Last update: <span id="updated_at">-</span> | Poll interval (s): <input id="poll-interval" type="number" min="1" max="60" value="5" step="1" /> </p>
  <div class="grid">
    <div class="card">
      <h2>Scan Engine</h2>
      <p><span class="status-label">PV</span><code>{{ scan_pv }}</code></p>
      <p><span class="status-label">Value</span><strong id="scan_value">-</strong> <span class="tooltip-small">(<span id="scan_info">-</span>)</span></p>
    </div>
    <div class="card">
      <h2>Last scan file</h2>
      <p><span class="status-label">PV</span><code>{{ file_pv }}</code></p>
      <p><span class="status-label">Path</span><span id="current_file">-</span></p>
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
    document.getElementById('current_file').innerText = j.current_file?.value || '-';
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
         file_pv=app.config.get('FILE_PV', DEFAULT_FILE_PV),
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
    parser.add_argument('--file-pv', default=DEFAULT_FILE_PV, help='Full file name PV (last scan path)')
    parser.add_argument('--poll-interval', default=5, type=float, help='status refresh interval seconds')
    args = parser.parse_args()

    app.config['SCAN_PV'] = args.scan_pv
    app.config['SHUTTER_A_PV'] = args.shutter_a_pv
    app.config['SHUTTER_B_PV'] = args.shutter_b_pv
    app.config['FILE_PV'] = args.file_pv
    app.config['DATA_FOLDER'] = args.data_folder

    refresh_status(args.scan_pv, args.shutter_a_pv, args.shutter_b_pv, args.data_folder, args.file_pv)
    t = threading.Thread(target=background_updater, args=(args.scan_pv, args.shutter_a_pv, args.shutter_b_pv, args.data_folder, args.file_pv, args.poll_interval), daemon=True)
    t.start()

    print(f"Starting beamline status server on http://{args.host}:{args.port}/")
    print(f"scan PV: {args.scan_pv}")
    print(f"shutter A PV: {args.shutter_a_pv}")
    print(f"shutter B PV: {args.shutter_b_pv}")
    print(f"data folder: {args.data_folder}")

    app.run(host=args.host, port=args.port, debug=False)


if __name__ == '__main__':
    main()
