#!/usr/bin/env python3
"""Generate PNG images showing TomoScan parameters and reconstruction status.

Generates two PNG files:
1. tomoscan_params.png - TomoScan configuration parameters
2. reconstruction_status.png - Reconstruction table

Usage:
  python blmon_png_generator.py --output-dir /path/to/output [--params] [--recon]

Dependencies:
  pip install matplotlib epics

"""

import argparse
import glob
import json
import os
from datetime import datetime

try:
    from epics import PV
except ImportError:
    PV = None

import matplotlib
matplotlib.use('Agg')  # Non-interactive backend for PNG generation
import matplotlib.pyplot as plt
import numpy as np


def read_pv_value(pv_name, timeout=2.0):
    if PV is None:
        return None, "epics not available"

    try:
        pv = PV(pv_name)
        if not pv.connect(timeout=timeout):
            return None, "timeout"

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


def get_tomoscan_params():
    """Fetch key TomoScan parameters from EPICS PVs.
    
    Common TomoScan PVs (adjust as needed for your setup):
    """
    params = {}
    
    # Scan parameters
    pv_list = {
        "Scan Status": "32id:TomoScan:ScanStatus",
        "File Path": "32idbSP1:HDF1:FullFileName_RBV",
        "Energy (keV)": "32id:TomoScan:EnergyValue",
        "Camera Binning": "32id:cam1:BinX_RBV",
        "Number Projs": "32id:TomoScan:NumProj",
        "Shutter A": "PB:32ID:STA_A_FES_CLSD_PL",
        "Shutter B": "PB:32ID:STA_B_SBS_CLSD_PL",
        "Scan Speed": "32id:TomoScan:RotationSpeed",
    }
    
    for label, pv_name in pv_list.items():
        value, info = read_pv_value(pv_name, timeout=1.5)
        if info == "ok":
            params[label] = str(value)
        else:
            params[label] = f"[{info}]"
    
    return params


def get_recon_status(data_folder, last_scan_file=None):
    """Get reconstruction status from filesystem."""
    path = None
    if last_scan_file:
        candidate = os.path.expanduser(str(last_scan_file))
        if os.path.isfile(candidate):
            path = os.path.dirname(candidate)

    if not path:
        path = os.path.expanduser(data_folder)

    if not path or not os.path.isdir(path):
        return {
            "available": False,
            "details": [],
            "message": f"Invalid folder: {path}",
        }

    h5_files = sorted(glob.glob(os.path.join(path, "*.h5")), key=os.path.getmtime, reverse=True)

    details = []
    full_count = 0
    try_count = 0
    ready_count = 0

    for f in h5_files[:20]:  # Limit to 20 most recent
        filename = os.path.basename(f)
        proj_name = os.path.splitext(filename)[0]

        try_dir = os.path.join(f"{path}_rec", "try_center", proj_name)
        full_dir = os.path.join(f"{path}_rec", f"{proj_name}_rec")

        has_try = os.path.isdir(try_dir) and len(glob.glob(os.path.join(try_dir, "*.tiff"))) > 0
        has_full = os.path.isdir(full_dir) and len(glob.glob(os.path.join(full_dir, "*.tiff"))) > 0

        if has_full:
            status_name = "FULL"
            full_count += 1
            color = "#22c55e"  # green
        elif has_try:
            status_name = "TRY"
            try_count += 1
            color = "#f59e0b"  # orange
        else:
            status_name = "READY"
            ready_count += 1
            color = "#ef4444"  # red

        details.append({
            "filename": filename,
            "status": status_name,
            "has_try": has_try,
            "has_full": has_full,
            "color": color,
        })

    return {
        "available": True,
        "total": len(h5_files),
        "full": full_count,
        "try": try_count,
        "ready": ready_count,
        "details": details,
        "folder": path,
    }


def generate_tomoscan_params_png(output_path):
    """Generate PNG showing TomoScan parameters."""
    params = get_tomoscan_params()
    
    # Increase figure height to accommodate wrapped text
    fig = plt.figure(figsize=(12, 10), dpi=96)
    ax = fig.add_subplot(111)
    ax.axis('tight')
    ax.axis('off')
    
    # Title
    fig.text(0.5, 0.96, 'TomoScan Parameters', ha='center', fontsize=18, fontweight='bold', color='#f8fafc')
    fig.patch.set_facecolor('#0f172a')
    
    # Timestamp
    timestamp = datetime.utcnow().isoformat() + 'Z'
    fig.text(0.5, 0.91, f'Updated: {timestamp}', ha='center', fontsize=10, color='#94a3b8')
    
    # Build table data with wrapped strings
    table_data = []
    for k, v in params.items():
        # Truncate very long strings and add ellipsis
        str_v = str(v)
        if len(str_v) > 70:
            str_v = str_v[:67] + '...'
        table_data.append([k, str_v])
    
    table = ax.table(
        cellText=table_data,
        colLabels=['Parameter', 'Value'],
        cellLoc='left',
        loc='center',
        bbox=[0.05, 0.08, 0.9, 0.80],
    )
    
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(1, 2.2)
    
    # Set column widths and style
    for i in range(len(table_data) + 1):
        for j in range(2):
            cell = table[(i, j)]
            if i == 0:
                # Header
                cell.set_facecolor('#1e293b')
                cell.set_text_props(weight='bold', color='#f8fafc', fontsize=10)
            else:
                # Rows
                cell.set_facecolor('#334155' if i % 2 == 0 else '#1e293b')
                cell.set_text_props(color='#e2e8f0', fontsize=9)
            cell.set_edgecolor('#334155')
            
            # Column width
            if j == 0:
                cell.set_width(0.25)
            else:
                cell.set_width(0.65)
    
    plt.tight_layout()
    plt.savefig(output_path, facecolor='#0f172a', edgecolor='none', bbox_inches='tight')
    plt.close()
    print(f"✓ Saved: {output_path}")


def generate_recon_status_png(output_path, data_folder, last_scan_file=None):
    """Generate PNG showing reconstruction status table."""
    recon = get_recon_status(data_folder, last_scan_file)
    
    fig = plt.figure(figsize=(14, 11), dpi=96)
    ax = fig.add_subplot(111)
    ax.axis('tight')
    ax.axis('off')
    
    # Title
    fig.text(0.5, 0.97, 'Reconstruction Status', ha='center', fontsize=18, fontweight='bold', color='#f8fafc')
    fig.patch.set_facecolor('#0f172a')
    
    # Timestamp
    timestamp = datetime.utcnow().isoformat() + 'Z'
    fig.text(0.5, 0.93, f'Updated: {timestamp}', ha='center', fontsize=10, color='#94a3b8')
    
    # Summary
    if recon["available"]:
        summary = f"Folder: {recon['folder']} | Total: {recon['total']} | Full: {recon['full']} | Try: {recon['try']} | Ready: {recon['ready']}"
    else:
        summary = f"Error: {recon['message']}"
    
    fig.text(0.5, 0.90, summary, ha='center', fontsize=9, color='#cbd5e1', wrap=True)
    
    if not recon["available"] or not recon["details"]:
        fig.text(0.5, 0.5, "No reconstruction data available", ha='center', fontsize=12, color='#f87171')
        plt.savefig(output_path, facecolor='#0f172a', edgecolor='none', bbox_inches='tight')
        plt.close()
        print(f"✓ Saved (empty): {output_path}")
        return
    
    # Build table data with truncated filenames
    table_data = []
    for detail in recon["details"]:
        filename = detail["filename"]
        # Truncate long filenames
        if len(filename) > 50:
            filename = filename[:47] + '...'
        row = [
            filename,
            detail["status"],
            "✓" if detail["has_try"] else "✗",
            "✓" if detail["has_full"] else "✗",
        ]
        table_data.append(row)
    
    table = ax.table(
        cellText=table_data,
        colLabels=['Filename', 'Status', 'Try', 'Full'],
        cellLoc='left',
        loc='center',
        bbox=[0.02, 0.05, 0.96, 0.82],
    )
    
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(1, 1.9)
    
    # Style table
    for i in range(len(table_data) + 1):
        for j in range(4):
            cell = table[(i, j)]
            if i == 0:
                # Header
                cell.set_facecolor('#1e293b')
                cell.set_text_props(weight='bold', color='#f8fafc', fontsize=10)
            else:
                # Rows
                cell.set_facecolor('#334155' if i % 2 == 0 else '#1e293b')
                cell.set_text_props(color='#e2e8f0', fontsize=8.5)
                
                # Color status cell
                if j == 1:  # Status column
                    status = table_data[i - 1][1]
                    if status == "FULL":
                        cell.set_text_props(color='#22c55e', weight='bold', fontsize=9)
                    elif status == "TRY":
                        cell.set_text_props(color='#f59e0b', weight='bold', fontsize=9)
                    else:
                        cell.set_text_props(color='#ef4444', weight='bold', fontsize=9)
                
                # Check marks
                if j in [2, 3]:  # Try, Full columns
                    if table_data[i - 1][j] == "✓":
                        cell.set_text_props(color='#22c55e', weight='bold', fontsize=10)
                    else:
                        cell.set_text_props(color='#94a3b8', fontsize=10)
            
            cell.set_edgecolor('#334155')
    
    plt.tight_layout()
    plt.savefig(output_path, facecolor='#0f172a', edgecolor='none', bbox_inches='tight')
    plt.close()
    print(f"✓ Saved: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Generate PNG screenshots of TomoScan and reconstruction status")
    parser.add_argument('--output-dir', default='.', help='Directory to save PNG files')
    parser.add_argument('--data-folder', default=os.path.expanduser('~'), help='Scan data folder')
    parser.add_argument('--file-pv', default='32idbSP1:HDF1:FullFileName_RBV', help='Full file name PV')
    parser.add_argument('--params', action='store_true', default=False, help='Generate only params image')
    parser.add_argument('--recon', action='store_true', default=False, help='Generate only recon image')
    args = parser.parse_args()
    
    # Default: generate both
    gen_params = True
    gen_recon = True
    if args.params or args.recon:
        gen_params = args.params
        gen_recon = args.recon
    
    os.makedirs(args.output_dir, exist_ok=True)
    
    # Get last scan file from PV
    last_scan_file, _ = read_pv_value(args.file_pv, timeout=2.0)
    
    if gen_params:
        params_path = os.path.join(args.output_dir, 'tomoscan_params.png')
        print("Generating TomoScan parameters image...")
        generate_tomoscan_params_png(params_path)
    
    if gen_recon:
        recon_path = os.path.join(args.output_dir, 'reconstruction_status.png')
        print("Generating reconstruction status image...")
        generate_recon_status_png(recon_path, args.data_folder, last_scan_file)
    
    print("\nDone!")


if __name__ == '__main__':
    main()
