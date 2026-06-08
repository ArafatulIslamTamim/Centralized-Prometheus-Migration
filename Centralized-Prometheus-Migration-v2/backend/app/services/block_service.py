from __future__ import annotations

import json
from typing import Any, Dict, List

from app.services.shell_utils import q
from app.services.ssh_runner import SSHRunner
from app.services.time_utils import utc_iso_from_ms

REMOTE_SCAN_SCRIPT = r'''
import json, os, sys
from pathlib import Path
base = Path(sys.argv[1])
blocks=[]
if base.exists():
    for child in sorted(base.iterdir()):
        if not child.is_dir() or not child.name.startswith("01"):
            continue
        meta = child / "meta.json"
        if not meta.exists():
            continue
        try:
            m=json.loads(meta.read_text())
            size=0
            for root, dirs, files in os.walk(child):
                for f in files:
                    try:
                        size += os.path.getsize(os.path.join(root, f))
                    except OSError:
                        pass
            blocks.append({
                "id": child.name,
                "path": str(child),
                "minTime": int(m["minTime"]),
                "maxTime": int(m["maxTime"]),
                "sizeBytes": size,
            })
        except Exception as e:
            pass
print(json.dumps(blocks))
'''

REMOTE_OVERLAP_SCRIPT = r'''
import json, os, sys, datetime
from pathlib import Path
base=Path(sys.argv[1])
blocks=[]
if base.exists():
    for child in sorted(base.iterdir()):
        meta=child/'meta.json'
        if child.is_dir() and child.name.startswith('01') and meta.exists():
            try:
                m=json.loads(meta.read_text())
                blocks.append({'id': child.name, 'minTime': int(m['minTime']), 'maxTime': int(m['maxTime'])})
            except Exception:
                pass
blocks.sort(key=lambda x:(x['minTime'], x['maxTime'], x['id']))
over=[]
for a,b in zip(blocks, blocks[1:]):
    if b['minTime'] < a['maxTime']:
        over.append({'a':a,'b':b})
print(json.dumps({'blockCount':len(blocks), 'overlapCount':len(over), 'overlaps':over[:50]}))
'''


def _add_iso(block: Dict[str, Any]) -> Dict[str, Any]:
    b = dict(block)
    b["minIso"] = utc_iso_from_ms(int(b["minTime"]))
    b["maxIso"] = utc_iso_from_ms(int(b["maxTime"]))
    return b


def scan_remote_blocks(runner: SSHRunner, path: str, use_sudo: bool = False) -> List[Dict[str, Any]]:
    cmd = "python3 - " + q(path) + " <<'PY'\n" + REMOTE_SCAN_SCRIPT + "\nPY"
    res = runner.run_sudo(cmd, timeout=180) if use_sudo else runner.run(cmd, timeout=180)
    if not res.ok:
        raise RuntimeError(f"scan failed on {runner.role}: {res.stderr or res.stdout}")
    try:
        raw = json.loads(res.stdout.strip() or "[]")
    except Exception as e:
        raise RuntimeError(f"could not parse block scan JSON: {e}; output={res.stdout[:1000]}")
    return [_add_iso(b) for b in raw]


def check_remote_overlaps(runner: SSHRunner, path: str, use_sudo: bool = False) -> Dict[str, Any]:
    cmd = "python3 - " + q(path) + " <<'PY'\n" + REMOTE_OVERLAP_SCRIPT + "\nPY"
    res = runner.run_sudo(cmd, timeout=180) if use_sudo else runner.run(cmd, timeout=180)
    if not res.ok:
        raise RuntimeError(f"overlap scan failed on {runner.role}: {res.stderr or res.stdout}")
    data = json.loads(res.stdout.strip() or "{}")
    for item in data.get("overlaps", []):
        for key in ("a", "b"):
            item[key]["minIso"] = utc_iso_from_ms(int(item[key]["minTime"]))
            item[key]["maxIso"] = utc_iso_from_ms(int(item[key]["maxTime"]))
    return data


def classify_selected_blocks(source_blocks: List[Dict[str, Any]], target_blocks: List[Dict[str, Any]], start_ms: int, end_ms: int, selection: str = "overlap") -> Dict[str, Any]:
    selection = selection or "overlap"
    if selection not in ("overlap", "inside"):
        raise ValueError("selection must be overlap or inside")
    if selection == "inside":
        selected = [b for b in source_blocks if int(b["minTime"]) >= start_ms and int(b["maxTime"]) <= end_ms]
    else:
        # This matches the manual migration command:
        # if block.maxTime > START_MS and block.minTime < END_MS, transfer/copy it.
        selected = [b for b in source_blocks if int(b["maxTime"]) > start_ms and int(b["minTime"]) < end_ms]
    selected.sort(key=lambda b: (b["minTime"], b["maxTime"], b["id"]))
    target_by_id = {b["id"]: b for b in target_blocks}
    already = []
    safe = []
    overlap = []
    target_to_backup_by_id: Dict[str, Dict[str, Any]] = {}

    for src in selected:
        if src["id"] in target_by_id:
            item = dict(src)
            item["alreadyTarget"] = target_by_id[src["id"]]
            already.append(item)
            continue
        hits = []
        for tgt in target_blocks:
            if int(src["maxTime"]) > int(tgt["minTime"]) and int(src["minTime"]) < int(tgt["maxTime"]):
                hits.append(tgt)
                target_to_backup_by_id[tgt["id"]] = tgt
        if hits:
            item = dict(src)
            item["overlapsWith"] = hits
            overlap.append(item)
        else:
            safe.append(src)

    copy_source_blocks = [b for b in selected if b["id"] not in target_by_id]
    target_to_backup = sorted(target_to_backup_by_id.values(), key=lambda b: (b["minTime"], b["maxTime"], b["id"]))
    actual_start = min([b["minTime"] for b in selected], default=None)
    actual_end = max([b["maxTime"] for b in selected], default=None)

    return {
        "selected": selected,
        "copySourceBlocks": copy_source_blocks,
        "already": already,
        "safe": safe,
        "overlap": overlap,
        "targetBlocksToBackup": target_to_backup,
        "selectedCount": len(selected),
        "copySourceCount": len(copy_source_blocks),
        "alreadyCount": len(already),
        "safeCount": len(safe),
        "overlapCount": len(overlap),
        "targetBackupCount": len(target_to_backup),
        "selectedSizeBytes": sum(int(b.get("sizeBytes") or 0) for b in selected),
        "selection": selection,
        "actualStartIso": utc_iso_from_ms(actual_start) if actual_start is not None else None,
        "actualEndIso": utc_iso_from_ms(actual_end) if actual_end is not None else None,
    }
