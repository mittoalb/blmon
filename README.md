# Beamline Monitor (blmon)

Local status server and PNG screenshot generator for 32-ID tomography beamline. Reports scan engine status, shutters, and reconstruction progress.

## Installation

```bash
conda env create -f environment.yml
conda activate blmon-status
```

Or with pip:

```bash
pip install flask epics matplotlib gunicorn requests watchdog
```

## Components

### 1. Web Status Server (blmon_status_server.py)

Live HTTP dashboard with auto-refreshing status display.

**Features:**
- Real-time EPICS PV polling (Scan Status, Shutters, Last Scan File)
- Reconstruction table showing file progress (Ready / Try / Full)
- Configurable polling interval
- Dark-themed responsive UI

**Usage:**

```bash
python3 blmon_status_server.py \
  --host 0.0.0.0 \
  --port 5000 \
  --file-pv 32idbSP1:HDF1:FullFileName_RBV \
  --data-folder /path/to/scan/base
```

Then open browser to: `http://localhost:5000/`

**API endpoint:** `GET /api/status` (JSON)

**CLI Options:**
- `--host` - web listen address (default `0.0.0.0`)
- `--port` - web listen port (default `8080`)
- `--scan-pv` - scan status PV (default `32id:TomoScan:ScanStatus`)
- `--shutter-a-pv` - shutter A PV (default `PB:32ID:STA_A_FES_CLSD_PL`)
- `--shutter-b-pv` - shutter B PV (default `PB:32ID:STA_B_SBS_CLSD_PL`)
- `--file-pv` - last scan file PV (default `32idbSP1:HDF1:FullFileName_RBV`)
- `--data-folder` - fallback scan data folder
- `--poll-interval` - refresh interval in seconds (default `5`)

---

### 2. PNG Generator (blmon_png_generator.py)

Generate static PNG images with current status snapshots. Useful for logging, reports, or display on non-web surfaces.

**Generates:**
1. `tomoscan_params.png` - TomoScan parameters table (Energy, Binning, Scan Speed, etc.)
2. `reconstruction_status.png` - Reconstruction status table (file list with status badges)

**Usage:**

```bash
# Generate both images
python3 blmon_png_generator.py \
  --output-dir ./images \
  --data-folder /path/to/scan/base

# Generate only params image
python3 blmon_png_generator.py --params --output-dir ./images

# Generate only recon image  
python3 blmon_png_generator.py --recon --output-dir ./images
```

**CLI Options:**
- `--output-dir` - directory to save PNG files (default: current dir)
- `--data-folder` - scan data folder
- `--file-pv` - last scan file PV (to determine active folder)
- `--params` - generate only TomoScan parameters image
- `--recon` - generate only reconstruction status image

**Output:** PNG files (96 DPI, dark theme) in specified output directory.

---

## Data Sources

### Scan Status
- **Scan Engine PV:** `32id:TomoScan:ScanStatus`
- **Last scan file:** `32idbSP1:HDF1:FullFileName_RBV` (from TomoScan HDF plugin)
- **Shutters:** `PB:32ID:STA_A_FES_CLSD_PL`, `PB:32ID:STA_B_SBS_CLSD_PL`

### Reconstruction Status
Derived from filesystem structure relative to last scan path (from PV):
```
/path/to/data/*.h5                          ← scan files
/path/to/data_rec/try_center/<proj_name>/   ← try reconstruction
/path/to/data_rec/<proj_name>_rec/          ← full reconstruction
```

Status determination:
- **FULL** - full reconstruction folder exists with `.tiff` files
- **TRY** - only try folder with `.tiff` files exists
- **READY** - no reconstruction yet

---

## Examples

### Systemd service (production)

Create `/etc/systemd/system/blmon.service`:

```ini
[Unit]
Description=Beamline Monitor 32-ID
After=network.target

[Service]
Type=simple
User=beams
WorkingDirectory=/home/beams0/AMITTONE/Software/blmon
ExecStart=/home/beams/AMITTONE/miniconda3/envs/blmon-status/bin/python3 blmon_status_server.py --host 0.0.0.0 --port 5000 --file-pv 32idbSP1:HDF1:FullFileName_RBV
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Then:

```bash
sudo systemctl enable blmon
sudo systemctl start blmon
```

### Cron job for periodic PNG updates

```bash
*/5 * * * * /home/beams/AMITTONE/miniconda3/envs/blmon-status/bin/python3 /home/beams0/AMITTONE/Software/blmon/blmon_png_generator.py --output-dir /data/blmon_snapshots
```

---

## Troubleshooting

### EPICS module not installed warning

**Issue:** Server starts but PVs show `[error: ...]` or `[not installed]`

**Fix:** Ensure you're using the same Python environment where `epics` is installed:

```bash
# Check if epics is available
python3 -c "import epics; print(epics.__file__)"

# If not found, install in active environment
pip install pyepics
```

### PNG generation takes too long

**Issue:** Script hangs when running `blmon_png_generator.py`

**Fix:** This typically means PV timeouts. Adjust with:

```bash
python3 blmon_png_generator.py --data-folder /known/data/path
```

(Avoids PV lookups if you provide explicit data folder)

---

## Customization

### Add custom TomoScan parameters to PNG

Edit `blmon_png_generator.py`, in `get_tomoscan_params()`, add more PVs to `pv_list`:

```python
pv_list = {
    "My Custom Param": "32id:MyPV:Value_RBV",
    # ... more params
}
```

### Change color scheme

Both scripts use dark theme by default:
- Background: `#0f172a`
- Cards: `#1e293b`, `#334155`
- Badges: green `#22c55e`, orange `#f59e0b`, red `#ef4444`

Edit CSS in `blmon_status_server.py` or Matplotlib colors in `blmon_png_generator.py`.

---

## License

Internal use. Based on tomogui reconstruction status conventions.

