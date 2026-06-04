from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class HostConfig(BaseModel):
    name: str = ""
    host: str = ""
    user: str = ""
    ssh_password: Optional[str] = None
    sudo_password: Optional[str] = None
    ssh_key_path: Optional[str] = None
    expected_hostname: Optional[str] = None

    @field_validator("name", "host", "user")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("ssh_password", "sudo_password", "ssh_key_path", "expected_hostname")
    @classmethod
    def normalize_optional_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = value.strip()
        return value or None


class CommandResult(BaseModel):
    ok: bool
    title: str
    output: str
    started_at: datetime
    finished_at: datetime
    migration_id: str
    proof: dict[str, Any] = Field(default_factory=dict)


class MigrationConfig(BaseModel):
    migration_id: Optional[str] = None

    source: HostConfig = Field(
        default_factory=lambda: HostConfig(
            name="LM1",
            host="",
            user="",
        )
    )
    target: HostConfig = Field(
        default_factory=lambda: HostConfig(
            name="LM2",
            host="",
            user="",
        )
    )

    # Labels used to identify imported data.
    # source_env_old is added to imported LM1 data.
    # source_env_new is optional/general metadata for target/live side.
    source_env_old: str = "lm1"
    source_env_new: str = "lm2"

    # Optional extra imported-data labels.
    # These make future cleanup safer.
    imported_migration_origin: str = "legacy"
    imported_source_host: str = ""

    # Prometheus paths.
    # These must be configured per machine.
    # Example LM1 real path: /var/lib/prometheus/metrics2
    # Example LM2 path:      /var/lib/prometheus
    source_prom_path: str = ""
    target_prom_path: str = ""
    target_receive_dir: str = ""
    target_backup_dir: str = ""

    # Prometheus binary/settings.
    prom_bin: str = "/usr/bin/prometheus"
    promtool_bin: str = ""
    prom_retention_time: str = "10y"

    # Prometheus local API URLs as seen from each SSH host.
    source_prometheus_url: str = "http://localhost:9090"
    target_prometheus_url: str = "http://localhost:9090"

    # Linux service/user names.
    prometheus_service_name: str = "prometheus"
    prometheus_system_user: str = "prometheus"

    # Temporary no-scrape Prometheus used only for reading LM1 TSDB
    # when normal LM1 Prometheus is not running.
    exact_range_temp_prometheus_url: str = "http://127.0.0.1:9090"
    exact_range_temp_listen_address: str = "127.0.0.1:9090"

    # Exact range export settings.
    # Use LM1 scrape interval for raw-like migration.
    exact_range_step_seconds: int = 15

    # 0 means unlimited. Example: 86400 means max 24 hours.
    exact_range_max_seconds: int = 0

    # Prometheus API export chunk size.
    # 3600 = 1-hour chunks.
    exact_range_chunk_seconds: int = 3600

    # Metric selector for exact-range export.
    # Default exports all metrics.
    # Faster examples:
    #   up
    #   {job="node_exporter"}
    #   {job="Node"}
    exact_range_match_selector: str = '{__name__!=""}'

    # Skip full TSDB analyze during merge. Full analyze can be slow.
    exact_range_analyze_after_merge: bool = False

    # User-defined historical data range.
    # Must come from GUI.
    lm1_data_start: str = ""
    lm1_data_end: str = ""

    date_preset: Literal[
        "custom",
        "last_24h",
        "last_7d",
        "last_30d",
        "production_2021_to_2024_march",
    ] = "custom"

    snapshot_name: Optional[str] = ""
    cleanup_confirmation: Optional[str] = None

    grafana_url: str = "http://localhost:3000"
    grafana_user: str = "admin"
    grafana_password: Optional[str] = None

    @staticmethod
    def parse_datetime_flexible(value: str) -> datetime:
        value = value.strip()

        formats = [
            "%Y-%m-%d %H:%M:%S %z",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
        ]

        for fmt in formats:
            try:
                parsed = datetime.strptime(value, fmt)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed
            except ValueError:
                continue

        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        except ValueError as exc:
            raise ValueError(
                "Invalid date format. Supported formats include: "
                "YYYY-MM-DD HH:MM:SS +0600, YYYY-MM-DDTHH:MM, "
                "YYYY-MM-DDTHH:MM:SS, or YYYY-MM-DD."
            ) from exc

    @field_validator(
        "source_env_old",
        "source_env_new",
        "imported_migration_origin",
        "prom_bin",
        "prom_retention_time",
        "source_prometheus_url",
        "target_prometheus_url",
        "prometheus_service_name",
        "prometheus_system_user",
        "exact_range_temp_prometheus_url",
        "exact_range_temp_listen_address",
        "exact_range_match_selector",
        "grafana_url",
        "grafana_user",
    )
    @classmethod
    def not_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be empty")
        return value

    @field_validator(
        "source_prom_path",
        "target_prom_path",
        "target_receive_dir",
        "target_backup_dir",
        "lm1_data_start",
        "lm1_data_end",
        "imported_source_host",
        "promtool_bin",
    )
    @classmethod
    def strip_paths_and_range(cls, value: str) -> str:
        return value.strip()

    @field_validator("snapshot_name", "cleanup_confirmation", "grafana_password")
    @classmethod
    def normalize_optional_fields(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @model_validator(mode="after")
    def validate_config(self) -> "MigrationConfig":
        if not self.source.name:
            raise ValueError("Source name is required")

        if not self.source.host:
            raise ValueError("Source SSH host is required")

        if not self.source.user:
            raise ValueError("Source SSH user is required")

        if not self.target.name:
            raise ValueError("Target name is required")

        if not self.target.host:
            raise ValueError("Target SSH host is required")

        if not self.target.user:
            raise ValueError("Target SSH user is required")

        if self.source_env_old == self.source_env_new:
            raise ValueError("source_env_old and source_env_new must be different")

        if self.source.host == self.target.host and self.source.user == self.target.user:
            raise ValueError("Source and target SSH destinations should not be identical")

        if not self.source_prom_path:
            raise ValueError("Source Prometheus path is required")

        if not self.target_prom_path:
            raise ValueError("Target Prometheus path is required")

        if not self.target_receive_dir:
            raise ValueError("Target receive directory is required")

        if not self.target_backup_dir:
            raise ValueError("Target backup directory is required")

        if not self.lm1_data_start:
            raise ValueError("Please select LM1 data start time in the GUI")

        if not self.lm1_data_end:
            raise ValueError("Please select LM1 data end time in the GUI")

        start_dt = self.parse_datetime_flexible(self.lm1_data_start)
        end_dt = self.parse_datetime_flexible(self.lm1_data_end)

        if start_dt >= end_dt:
            raise ValueError("lm1_data_start must be before lm1_data_end")

        if self.exact_range_step_seconds <= 0:
            raise ValueError("exact_range_step_seconds must be positive")

        if self.exact_range_max_seconds < 0:
            raise ValueError("exact_range_max_seconds cannot be negative")

        if self.exact_range_chunk_seconds <= 0:
            raise ValueError("exact_range_chunk_seconds must be positive")

        if not self.source_prometheus_url.startswith(("http://", "https://")):
            raise ValueError("source_prometheus_url must start with http:// or https://")

        if not self.target_prometheus_url.startswith(("http://", "https://")):
            raise ValueError("target_prometheus_url must start with http:// or https://")

        if not self.exact_range_temp_prometheus_url.startswith(("http://", "https://")):
            raise ValueError("exact_range_temp_prometheus_url must start with http:// or https://")

        dangerous_retention_values = {"15d", "7d", "1d"}
        if self.prom_retention_time.strip() in dangerous_retention_values:
            raise ValueError(
                "Retention time is too short for historical migration. "
                "Use 5y, 10y, or another value suitable for historical data."
            )

        return self

    def source_sudo_password(self) -> Optional[str]:
        return self.source.sudo_password or self.source.ssh_password

    def target_sudo_password(self) -> Optional[str]:
        return self.target.sudo_password or self.target.ssh_password

    def safe_dict(self) -> dict[str, Any]:
        data = self.model_dump()

        for key in ("source", "target"):
            data[key]["ssh_password"] = None
            data[key]["sudo_password"] = None
            data[key]["ssh_key_path"] = "***" if data[key].get("ssh_key_path") else None

        data["grafana_password"] = None
        data["cleanup_confirmation"] = None

        return data