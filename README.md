# Prometheus Migration GUI 

A professional one-page control panel for controlled Prometheus TSDB migration from a source/old Prometheus machine to a target/central Prometheus machine.

The GUI separates the migration into safe manual stages: pre-checks, target backup, source snapshot, transfer, merge, validation, Grafana check, and proof JSON download.

---

## Features

- Modern dark observability-style UI
- Editable source and target machine details
- Editable Prometheus data path, binary path, receive directory, and backup directory
- Calendar-based date/time selection
- Safe step-by-step migration workflow
- Optional cleanup protected by typed confirmation
- Backend-generated proof JSON and logs
- Grafana validation support
- Local run scripts for backend and frontend

---

## Project Structure

```text
prometheus-migration-gui/
├── backend/
│   ├── app/
│   │   ├── api/migration_routes.py
│   │   ├── models/migration_models.py
│   │   └── services/
│   │       ├── migration_service.py
│   │       ├── proof_service.py
│   │       └── ssh_runner.py
│   ├── requirements.txt
│   └── .env.example
├── frontend/
│   ├── app/page.tsx
│   ├── app/globals.css
│   ├── lib/api.ts
│   └── lib/types.ts
├── run_backend.sh
└── run_frontend.sh
```

---

## Prerequisites

Check required tools:

```bash
git --version
python3 --version
node --version
npm --version
```

Required:

```text
Git
Python 3
Python venv
Node.js
npm
```

For Ubuntu/Debian, install missing dependencies:

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip curl
```

Install Node.js and npm:

```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
```

Verify:

```bash
git --version
python3 --version
node --version
npm --version
```

---

## Download and Run Locally

Replace `<GITHUB_REPO_URL>` with the actual GitHub repository URL.

### Terminal 1 — Backend

```bash
cd ~/Downloads
git clone <GITHUB_REPO_URL> prometheus-migration-gui
cd prometheus-migration-gui
chmod +x run_backend.sh run_frontend.sh
./run_backend.sh
```

Backend URL:

```text
http://localhost:8000
```

Backend API docs:

```text
http://localhost:8000/docs
```

Keep this terminal open.

---

### Terminal 2 — Frontend

```bash
cd ~/Downloads/prometheus-migration-gui
./run_frontend.sh
```

Frontend URL:

```text
http://localhost:3001
```

Open this in the browser:

```text
http://localhost:3001
```

Port `3001` is used to avoid conflict with Grafana on port `3000`.

---

## If the Repository Is Already Downloaded

### Terminal 1 — Backend

```bash
cd ~/Downloads/prometheus-migration-gui
git pull
chmod +x run_backend.sh run_frontend.sh
./run_backend.sh
```

### Terminal 2 — Frontend

```bash
cd ~/Downloads/prometheus-migration-gui
./run_frontend.sh
```

---

## Two-Machine Migration Setup

This project is designed for two Prometheus machines:

```text
LM1 = Source / old Prometheus machine
LM2 = Target / central Prometheus machine
```

Recommended setup:

```text
Run the GUI/backend on LM1.
Migrate LM1 Prometheus data into LM2.
```

The GUI can use the entered SSH username and password for most operations.

However, the snapshot transfer step uses direct SSH/SCP from LM1 to LM2 with `BatchMode` enabled. Therefore, LM1 must have passwordless SSH access to LM2.

Run this on LM1:

```bash
ssh-copy-id <TARGET_USER>@<TARGET_IP>
```

Example:

```bash
ssh-copy-id student3@192.168.1.102
```

Test from LM1:

```bash
ssh -o BatchMode=yes <TARGET_USER>@<TARGET_IP> "echo SSH_OK"
```

Example:

```bash
ssh -o BatchMode=yes student3@192.168.1.102 "echo SSH_OK"
```

If the output is:

```text
SSH_OK
```

then SSH setup is correct.

---

## Find Prometheus Data Path

Run this command on each Prometheus machine.

On LM1, use the result as **Source Prometheus path**.

On LM2, use the result as **Target Prometheus path**.

```bash
systemctl cat prometheus 2>/dev/null | grep -oP -- '--storage\.tsdb\.path=\K\S+' || ps aux | grep prometheus | grep -v grep | grep -oP -- '--storage\.tsdb\.path=\K\S+'
```

Common result:

```text
/var/lib/prometheus
```

Use this in the GUI:

```text
Source Prometheus path = /var/lib/prometheus
Target Prometheus path = /var/lib/prometheus
```

Do not use `/etc/prometheus` as the data path. That is usually the Prometheus configuration directory.

---

## Find Prometheus Binary Path

Run this on the Prometheus machine:

```bash
systemctl cat prometheus | grep ExecStart
```

Example output:

```text
ExecStart=/usr/local/bin/prometheus \
```

Then use this in the GUI:

```text
Prometheus binary = /usr/local/bin/prometheus
```

Common binary paths:

```text
/usr/local/bin/prometheus
/usr/bin/prometheus
```

---

## GUI Input Guide

Fill the GUI using values from your own machines.

### Source / Old Machine

```text
Source display name: LM1
Source IP / host: <SOURCE_IP>
Source SSH user: <SOURCE_SSH_USER>
Expected source hostname: optional
Source SSH password: <SOURCE_SSH_PASSWORD>
Source sudo password: leave empty if same as SSH password
Source Prometheus path: <SOURCE_PROMETHEUS_DATA_PATH>
Source label: lm1
```

Example:

```text
Source display name: LM1
Source IP / host: 192.168.1.160
Source SSH user: student2
Expected source hostname: student2pc
Source Prometheus path: /var/lib/prometheus
Source label: lm1
```

---

### Target / New Machine

```text
Target display name: LM2
Target IP / host: <TARGET_IP>
Target SSH user: <TARGET_SSH_USER>
Expected target hostname: optional
Target SSH password: <TARGET_SSH_PASSWORD>
Target sudo password: leave empty if same as SSH password
Target Prometheus path: <TARGET_PROMETHEUS_DATA_PATH>
Target label: lm2
Target receive directory: /home/<TARGET_USER>/lm1-snapshot-direct
Target backup directory: /home/<TARGET_USER>/lm2-backup
```

Example:

```text
Target display name: LM2
Target IP / host: 192.168.1.102
Target SSH user: student3
Target Prometheus path: /var/lib/prometheus
Target label: lm2
Target receive directory: /home/student3/lm1-snapshot-direct
Target backup directory: /home/student3/lm2-backup
```

The backend creates the receive and backup directories automatically if the target user has permission.

---

### Time Range and Prometheus Settings

```text
Date preset: Custom
Prometheus binary: /usr/local/bin/prometheus
Historical data start: select from calendar
Historical data end / cutoff: select from calendar
Retention time: 5y or 10y
Snapshot name override: leave empty
```

---

### Grafana

If Grafana runs on LM2, use:

```text
Grafana URL on target: http://localhost:3000
Grafana user: admin
Grafana password: <GRAFANA_PASSWORD>
```

If opening Grafana from another machine/browser, use:

```text
http://<TARGET_IP>:3000
```

Example:

```text
http://192.168.1.102:3000
```

---

## Recommended GUI Execution Order

After opening:

```text
http://localhost:3001
```

Run the buttons in this order:

```text
1. Run Pre-Checks
2. Create Target Backup
3. Create Source Snapshot
4. Optional: Delete Existing Source Data from Target
5. Transfer Snapshot to Target
6. Run Target Merge
7. Run Validation
8. Check Grafana
9. Download Proof JSON
```

---

## Important Safety Notes

- Always create the target backup before cleanup or merge.
- Do not run cleanup unless re-importing source data.
- Cleanup is based on the old source label, for example `source_env=lm1`.
- Cleanup should not delete target live data with the new label, for example `source_env=lm2`.
- Cleanup does not delete Grafana annotations.
- Only TSDB block folders beginning with `01` are copied into Prometheus storage.
- WAL, `chunks_head`, `lock`, and `queries.active` are not copied.

---

## Proof and Logs

The backend saves logs and proof files under:

```text
backend/storage/logs/
backend/storage/proofs/
backend/storage/migration_history.json
```

Proof JSON is available from the GUI after a migration ID is created.

---

## Stop the Project

Press this in both backend and frontend terminals:

```text
CTRL + C
```

---

## Troubleshooting

### Permission denied when running scripts

```bash
chmod +x run_backend.sh run_frontend.sh
```

---

### Backend port already in use

```bash
sudo fuser -k 8000/tcp
./run_backend.sh
```

---

### Frontend port already in use

```bash
sudo fuser -k 3001/tcp
./run_frontend.sh
```

---

### CORS or Failed to fetch error

Restart backend:

```bash
sudo fuser -k 8000/tcp
./run_backend.sh
```

Test CORS:

```bash
curl -i -X OPTIONS http://localhost:8000/api/migration/lm2-backup \
  -H "Origin: http://localhost:3001" \
  -H "Access-Control-Request-Method: POST"
```

Expected output should include:

```text
access-control-allow-origin
```

---

### Check LM2 backup permission manually

Run on LM2:

```bash
sudo ls -lah /var/lib/prometheus
sudo tar -czf /home/<TARGET_USER>/lm2-backup/test-prometheus-backup.tar.gz -C /var/lib prometheus
ls -lh /home/<TARGET_USER>/lm2-backup
sudo rm /home/<TARGET_USER>/lm2-backup/test-prometheus-backup.tar.gz
```

Example:

```bash
sudo tar -czf /home/student3/lm2-backup/test-prometheus-backup.tar.gz -C /var/lib prometheus
```

If this works, the target backup path and permissions are correct.

---

## Developer Notes

Do not commit generated files.

Recommended `.gitignore` entries:

```gitignore
# Python cache
__pycache__/
*.py[cod]
*.pyo

# Python virtual environment
.venv/
venv/

# Node / Next.js
node_modules/
.next/

# Environment files
.env
.env.local

# Generated migration files
backend/storage/logs/
backend/storage/proofs/
backend/storage/migration_history.json
```

Before committing:

```bash
git status
```

---
