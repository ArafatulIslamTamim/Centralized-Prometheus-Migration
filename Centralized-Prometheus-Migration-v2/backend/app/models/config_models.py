from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class HostSSHConfig(BaseModel):
    name: str = ""
    host: str = ""
    port: int = 22
    user: str = ""
    ssh_password: str = ""
    ssh_key_path: str = ""
    ssh_key_passphrase: str = ""
    sudo_password: str = ""

    def target(self) -> str:
        return f"{self.user}@{self.host}:{self.port}"


class PrometheusConfig(BaseModel):
    # User-friendly default workflow:
    # Create a safe source snapshot by starting a temporary Prometheus with scrape_configs: []
    # on 127.0.0.1:19090. The normal source Prometheus service should stay stopped.
    source_temp_snapshot_port: int = 19090
    source_url_from_source: str = "http://127.0.0.1:19090"
    source_tsdb_path: str = "/var/lib/prometheus/metrics2"
    source_snapshot_dir: str = "/var/lib/prometheus/metrics2/snapshots"
    target_url_from_target: str = "http://localhost:9090"
    target_data_path: str = "/data"
    target_staging_root: str = ""
    prometheus_service: str = "prometheus"
    prometheus_owner: str = "prometheus:prometheus"


class GrafanaConfig(BaseModel):
    enabled: bool = False
    source_url_from_controller: str = ""
    source_user: str = "admin"
    source_password: str = ""
    target_url_from_controller: str = ""
    target_user: str = "admin"
    target_password: str = ""
    import_tag: str = ""
    extra_import_tags: List[str] = Field(default_factory=list)


class AppConfig(BaseModel):
    source: HostSSHConfig = Field(default_factory=lambda: HostSSHConfig(name="Source"))
    target: HostSSHConfig = Field(default_factory=lambda: HostSSHConfig(name="Target"))
    prometheus: PrometheusConfig = Field(default_factory=PrometheusConfig)
    grafana: GrafanaConfig = Field(default_factory=GrafanaConfig)
    record_dir_on_controller: str = "./records"
    record_dir_on_target: str = ""
    rsync_args: str = "-aH --numeric-ids --info=progress2"
    transfer_mode: Literal["controller_sftp_bridge", "target_pull_rsync"] = "controller_sftp_bridge"
    ssh_strict_host_key_checking: bool = False

    def safe_dict(self) -> Dict[str, Any]:
        data = self.model_dump()
        for role in ("source", "target"):
            for key in ("ssh_password", "ssh_key_passphrase", "sudo_password"):
                if data.get(role, {}).get(key):
                    data[role][key] = "***"
        for key in ("source_password", "target_password"):
            if data.get("grafana", {}).get(key):
                data["grafana"][key] = "***"
        return data


class ConfigPayload(BaseModel):
    config: AppConfig


class RolePayload(BaseModel):
    role: Literal["source", "target"]


class SnapshotTransferPayload(BaseModel):
    snapshot_id: str
    snapshot_path: Optional[str] = None
    transfer_mode: Optional[Literal["controller_sftp_bridge", "target_pull_rsync"]] = None
    overwrite_staging: bool = False
    # Range-limited transfer: only block folders whose meta.json time range
    # matches the requested interval are copied to target staging. Default
    # overlap mode matches your manual rule: maxTime > START and minTime < END.
    start_utc: Optional[str] = None
    end_utc: Optional[str] = None
    selection: Literal["overlap", "inside"] = "overlap"


class BlockPlanPayload(BaseModel):
    snapshot_path_on_target: str
    start_utc: str
    end_utc: str
    label: str = "migration"
    mode: Literal["safe_merge", "replacement"] = "safe_merge"
    # This must match your old manual command:
    # select blocks where block.maxTime > START and block.minTime < END.
    # That sends only matching whole blocks that overlap the requested range.
    selection: Literal["overlap", "inside"] = "overlap"


class ExecutePlanPayload(BaseModel):
    plan_id: str
    confirmation: str = ""


class AnnotationRangePayload(BaseModel):
    from_utc: str
    to_utc: str


class ImportAnnotationPayload(BaseModel):
    export_file: str
    import_tag: Optional[str] = None


class OfflineTsdbTransferPayload(BaseModel):
    label: str = ""
    transfer_mode: Optional[Literal["controller_sftp_bridge", "target_pull_rsync"]] = None
    overwrite_staging: bool = False
    start_utc: Optional[str] = None
    end_utc: Optional[str] = None
    selection: Literal["overlap", "inside"] = "overlap"
