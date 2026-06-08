# Safe Snapshot Prometheus/Grafana Migration GUI

This is the user-friendly first-style GUI for Prometheus TSDB block migration.

It uses the original safe snapshot idea:

1. Keep the normal source Prometheus service stopped.
2. Start a temporary Prometheus on the source with `scrape_configs: []` and admin API enabled.
3. Create a snapshot from the source TSDB.
4. Stop the temporary Prometheus immediately.
5. Transfer only the block folders selected by your UTC range to target staging by controller SSH/SFTP bridge.
6. Scan staged source blocks and target data blocks.
7. Build a safe merge/replacement plan for the same range.
8. Execute only after reviewing the plan.
9. Replacement mode moves target blocks to backup first; it never deletes them permanently.
10. Optionally migrate Grafana annotations.

## Run

```bash
unzip prometheus_safe_snapshot_gui_first_style.zip
cd prometheus_safe_snapshot_gui_first_style
./run_backend.sh
```

In another terminal:

```bash
./run_frontend.sh
```

Open:

```text
http://localhost:3001
```

Backend:

```text
http://localhost:8000
```

## Important safety behavior

- The source normal Prometheus service is not started by this GUI.
- The safe snapshot button refuses to run if the normal source Prometheus service is active.
- The temporary Prometheus uses this config:

```yaml
global:
  scrape_interval: 1h
  evaluation_interval: 1h
scrape_configs: []
```

- The temporary Prometheus listens only on source localhost, default `127.0.0.1:19090`.
- Transfer writes only selected range blocks to target staging. It does not merge the whole snapshot.
- Target data path is changed only when you click Execute.
- Replacement mode requires exact uppercase `YES`.

## Minimal fields to fill

Source:

- Source host/IP
- Source SSH user
- Source SSH password or SSH key
- Source sudo password
- Source TSDB path

Target:

- Target host/IP
- Target SSH user
- Target SSH password or SSH key
- Target sudo password
- Target Prometheus data path
- Target staging folder

Optional:

- Grafana source/target URL and credentials for annotation migration.

## Notes

The controller bridge transfer works from any PC that can SSH to both source and target. It does not require the target machine to SSH into the source machine.

For very large snapshots, controller bridge can be slower than rsync, but it is the most generalized and easiest to use safely.

## Specific range behavior

Prometheus raw TSDB blocks are whole folders and cannot be cut at an arbitrary second by the GUI.

The GUI therefore has a **Block selection rule**:

- **Inside range only**: copies only blocks fully inside your Start/End UTC range. This avoids bringing outside-time data, but may miss edge blocks.
- **Overlap range / cover edges**: copies any block that overlaps your Start/End UTC range. This covers the selected range, but may include some extra data just before/after the edges.

For safest partial migration, use **Inside range only** and check the actual coverage shown in the transfer/plan output before Execute.

## Latest range-matching behavior

This build does **not** transfer or merge the whole snapshot. It follows the same rule used in the manual migration documentation:

```bash
block.maxTime > START_MS && block.minTime < END_MS
```

That means only whole Prometheus TSDB block folders whose time range overlaps the requested UTC range are transferred to target staging. The GUI shows both the requested range and the actual selected block coverage before execution. Target `/data` is changed only after the final Execute step.

Recommended block rule: **Overlap requested range**. This covers the requested time range, but can include a little extra data at the edges because Prometheus blocks cannot be split by exact timestamps.
