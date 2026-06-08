from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

import requests

from app.models.config_models import AppConfig
from app.services.time_utils import parse_utc_to_ms, now_id


def _record_dir(cfg: AppConfig) -> Path:
    p = Path(cfg.record_dir_on_controller).expanduser().resolve() / "annotations"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _get_annotations(url: str, user: str, password: str, from_ms: int, to_ms: int, tag: str | None = None) -> List[Dict[str, Any]]:
    params = {"from": from_ms, "to": to_ms, "limit": 10000}
    if tag:
        params["tags"] = tag
    r = requests.get(url.rstrip("/") + "/api/annotations", auth=(user, password), params=params, timeout=60)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        raise RuntimeError(f"Grafana annotation API did not return a list: {data}")
    return data


def test_grafana(cfg: AppConfig, which: str) -> Dict[str, Any]:
    if which == "source":
        url, user, pw = cfg.grafana.source_url_from_controller, cfg.grafana.source_user, cfg.grafana.source_password
    else:
        url, user, pw = cfg.grafana.target_url_from_controller, cfg.grafana.target_user, cfg.grafana.target_password
    r = requests.get(url.rstrip("/") + "/api/user", auth=(user, pw), timeout=15)
    return {"ok": r.ok, "status_code": r.status_code, "body": r.text[:500]}


def export_annotations(cfg: AppConfig, from_utc: str, to_utc: str) -> Dict[str, Any]:
    from_ms = parse_utc_to_ms(from_utc)
    to_ms = parse_utc_to_ms(to_utc)
    data = _get_annotations(cfg.grafana.source_url_from_controller, cfg.grafana.source_user, cfg.grafana.source_password, from_ms, to_ms)
    path = _record_dir(cfg) / f"source_annotations_{now_id()}.json"
    path.write_text(json.dumps(data, indent=2))
    return {
        "file": str(path),
        "export_file": str(path),
        "count": len(data),
        "fromMs": from_ms,
        "toMs": to_ms,
        "preview": data[:20],
    }


def import_annotations(cfg: AppConfig, export_file: str, import_tag: str | None = None) -> Dict[str, Any]:
    # Simple mode:
    # Transfer all source Grafana annotations in the export file.
    # Preserve original time, timeEnd, text, and original tags.
    # Do NOT add import tags such as testhouse, lm1-imported, migrated-from-lm1, etc.
    data = json.loads(Path(export_file).read_text())

    if not isinstance(data, list):
        raise RuntimeError("export file is not a JSON list")

    if not data:
        return {
            "sourceCount": 0,
            "created": 0,
            "skippedDuplicate": 0,
            "failed": 0,
            "mode": "preserve_original_annotations_no_extra_tags",
        }

    times = [int(a.get("time")) for a in data if a.get("time") is not None]
    from_ms, to_ms = min(times), max(times) + 1

    existing = _get_annotations(
        cfg.grafana.target_url_from_controller,
        cfg.grafana.target_user,
        cfg.grafana.target_password,
        from_ms,
        to_ms,
    )

    existing_keys: set[Tuple[int, str]] = set()
    for a in existing:
        if a.get("time") is not None:
            existing_keys.add((int(a.get("time")), a.get("text") or ""))

    created = 0
    skipped = 0
    failed = 0
    errors = []
    created_ids = []

    for a in data:
        try:
            t = a.get("time")
            if t is None:
                skipped += 1
                continue

            text = a.get("text") or ""
            key = (int(t), text)

            if key in existing_keys:
                skipped += 1
                continue

            # Preserve original source tags only.
            # Do not add any new tag.
            tags = []
            for x in a.get("tags") or []:
                if isinstance(x, str) and x not in tags:
                    tags.append(x)

            payload = {
                "time": int(t),
                "text": text,
                "tags": tags,
            }

            if a.get("timeEnd") is not None and int(a.get("timeEnd")) > int(t):
                payload["timeEnd"] = int(a.get("timeEnd"))

            # Do not send dashboardId/dashboardUID/panelId.
            # This avoids wrong dashboard/panel collisions on target Grafana.
            r = requests.post(
                cfg.grafana.target_url_from_controller.rstrip("/") + "/api/annotations",
                auth=(cfg.grafana.target_user, cfg.grafana.target_password),
                json=payload,
                timeout=30,
            )

            if not r.ok:
                failed += 1
                errors.append({"time": t, "status": r.status_code, "body": r.text[:500]})
                continue

            created += 1
            existing_keys.add(key)

            try:
                created_ids.append(r.json().get("id"))
            except Exception:
                pass

        except Exception as e:
            failed += 1
            errors.append({"error": str(e)})

    result = {
        "sourceCount": len(data),
        "existingTargetInRangeBefore": len(existing),
        "created": created,
        "skippedDuplicate": skipped,
        "failed": failed,
        "mode": "preserve_original_annotations_no_extra_tags",
        "createdIdsSample": created_ids[:20],
        "errorsSample": errors[:20],
    }

    result_path = _record_dir(cfg) / f"import_result_{now_id()}.json"
    result_path.write_text(json.dumps(result, indent=2))
    result["resultFile"] = str(result_path)

    return result


def verify_annotations(cfg: AppConfig, from_utc: str, to_utc: str, tag: str | None = None) -> Dict[str, Any]:
    # Verify all annotations in the selected time range.
    # No tag filter is used unless explicitly provided.
    from_ms = parse_utc_to_ms(from_utc)
    to_ms = parse_utc_to_ms(to_utc)

    use_tag = tag if tag else None

    data = _get_annotations(
        cfg.grafana.target_url_from_controller,
        cfg.grafana.target_user,
        cfg.grafana.target_password,
        from_ms,
        to_ms,
        use_tag,
    )

    return {
        "count": len(data),
        "tag_filter": use_tag,
        "mode": "all_annotations_in_range" if use_tag is None else "tag_filtered",
        "preview": data[:20],
    }
