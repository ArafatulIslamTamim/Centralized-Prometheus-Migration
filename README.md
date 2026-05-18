# Prometheus Migration GUI v2

A one-page professional control panel for controlled Prometheus TSDB migration from a source/old machine to a target/central machine.

## What changed in v2

- Modern dark observability-style UI.
- Editable source/target names.
- Editable Prometheus paths, binary path, receive directory, and backup directory.
- Date preset dropdown plus editable exact timestamps.
- Split workflow into safer stages:
  1. Run Pre-Checks
  2. Create LM2 Backup
  3. Create LM1 Snapshot
  4. Optional Delete Existing LM1 Data from LM2
  5. Transfer Snapshot to LM2
  6. Run LM2 Merge
  7. Run Validation
  8. Check Grafana
- Optional cleanup button protected by typed confirmation.
- Proof JSON and logs saved by the backend.

## Project structure

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

## Backend setup

Open terminal 1:

```bash
cd ~/Downloads/prometheus-migration-gui/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Backend URLs:

```text
http://localhost:8000
http://localhost:8000/docs
```

## Frontend setup

Open terminal 2:

```bash
cd ~/Downloads/prometheus-migration-gui/frontend
cp .env.local.example .env.local
npm install
npm run dev
```

Frontend URL:

```text
http://localhost:3001
```

Port `3001` is used so it does not conflict with Grafana on port `3000`.

## Required SSH setup

The backend machine must be able to SSH into both source and target machines.

Recommended:

```bash
ssh-copy-id student2@192.168.1.160
ssh-copy-id student3@192.168.1.102
```

For direct source-to-target snapshot transfer, the source machine must also be able to SSH into the target machine without an interactive password:

```bash
# Run on source/LM1
ssh-copy-id student3@192.168.1.102
```

The transfer stage uses `ssh -o BatchMode=yes`, so it fails fast if passwordless source-to-target SSH is not configured.

## Recommended GUI execution order

1. Fill the Migration Configuration form.
2. Click **Run Pre-Checks**.
3. Click **Create LM2 Backup**.
4. Click **Create LM1 Snapshot**.
5. Optional: type the required confirmation and click **Delete Existing LM1 Data from LM2**.
6. Click **Transfer Snapshot to LM2**.
7. Click **Run LM2 Merge**.
8. Click **Run Validation**.
9. Click **Check Grafana**.
10. Download Proof JSON.

## Important safety notes

- Do not run cleanup unless you are re-importing source data.
- The cleanup selector is based on `source_env=<old label>`.
- Cleanup does not delete target live data with `source_env=<new label>`.
- Cleanup does not delete Grafana annotations.
- Always create the target backup before cleanup or merge.
- Only TSDB block folders beginning with `01` are copied into Prometheus storage.
- WAL, `chunks_head`, `lock`, and `queries.active` are never copied.

## Proof and logs

The backend saves files under:

```text
backend/storage/logs/
backend/storage/proofs/
backend/storage/migration_history.json
```

Proof JSON is available through the GUI after a migration ID is created.
