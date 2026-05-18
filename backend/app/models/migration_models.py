from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class HostConfig(BaseModel):
    name: str = Field(
        default="LM1",
        description="Friendly machine name, for example LM1, LM2, Server 107, or Server 105",
    )
    host: str = Field(
        default="192.168.1.160",
        description="IP address or DNS hostname",
    )
    user: str = Field(
        default="student2",
        description="SSH username",
    )
    ssh_password: Optional[str] = Field(
        default=None,
        description="SSH password. Prefer SSH keys in production.",
    )
    ssh_key_path: Optional[str] = Field(
        default=None,
        description="Private SSH key path on the backend server",
    )
    sudo_password: Optional[str] = Field(
        default=None,
        description="Sudo password. If empty, ssh_password is reused when available.",
    )
    expected_hostname: Optional[str] = Field(
        default="",
        description="Optional hostname safety check before risky commands",
    )

    @field_validator("name", "host", "user")
    @classmethod
    def required_string(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be empty")
        return value

    @field_validator("expected_hostname")
    @classmethod
    def normalize_optional_string(cls, value: Optional[str]) -> str:
        return (value or "").strip()


class MigrationConfig(BaseModel):
    migration_id: Optional[str] = None

    source: HostConfig = Field(default_factory=HostConfig)
    target: HostConfig = Field(
        default_factory=lambda: HostConfig(
            name="LM2",
            host="192.168.1.102",
            user="student3",
        )
    )

    # Prometheus labels
    source_env_old: str = "lm1"
    source_env_new: str = "lm2"

    # Prometheus paths
    source_prom_path: str = "/var/lib/prometheus"
    target_prom_path: str = "/var/lib/prometheus"
    target_receive_dir: str = "/home/student3/lm1-snapshot-direct"
    target_backup_dir: str = "/home/student3/prometheus-migration-backups"

    # Prometheus binary/settings
    prom_bin: str = "/usr/local/bin/prometheus"
    prom_retention_time: str = "10y"

    # Historical data range
    lm1_data_start: str = "2026-05-12 00:00:00 +0600"
    lm1_data_end: str = "2026-05-13 00:00:00 +0600"

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
        """
        Accepts both backend-friendly and browser datetime-local formats.

        Supported examples:
        - 2026-05-12 00:00:00 +0600
        - 2026-05-12T00:00
        - 2026-05-12T00:00:00
        - 2026-05-12
        """
        value = value.strip()

        formats = [
            "%Y-%m-%d %H:%M:%S %z",
            "%Y-%m-%dT%H:%M",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d",
        ]

        for fmt in formats:
            try:
                parsed = datetime.strptime(value, fmt)

                # Browser datetime-local values usually have no timezone.
                # Use UTC internally for validation only.
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)

                return parsed
            except ValueError:
                continue

        raise ValueError(
            "Invalid date format. Supported formats include: "
            "YYYY-MM-DD HH:MM:SS +0600, YYYY-MM-DDTHH:MM, "
            "YYYY-MM-DDTHH:MM:SS, or YYYY-MM-DD."
        )

    @field_validator(
        "source_env_old",
        "source_env_new",
        "source_prom_path",
        "target_prom_path",
        "target_receive_dir",
        "target_backup_dir",
        "prom_bin",
        "prom_retention_time",
        "lm1_data_start",
        "lm1_data_end",
        "grafana_url",
        "grafana_user",
    )
    @classmethod
    def not_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be empty")
        return value

    @field_validator("snapshot_name", "cleanup_confirmation")
    @classmethod
    def normalize_optional_fields(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return value.strip()

    @model_validator(mode="after")
    def validate_config(self) -> "MigrationConfig":
        if self.source_env_old == self.source_env_new:
            raise ValueError("source_env_old and source_env_new must be different")

        if self.source.host == self.target.host and self.source.user == self.target.user:
            raise ValueError("Source and target SSH destinations should not be identical")

        start_dt = self.parse_datetime_flexible(self.lm1_data_start)
        end_dt = self.parse_datetime_flexible(self.lm1_data_end)

        if start_dt >= end_dt:
            raise ValueError("lm1_data_start must be before lm1_data_end")

        dangerous_retention_values = {"15d", "7d", "1d"}
        if self.prom_retention_time.strip() in dangerous_retention_values:
            raise ValueError(
                "Retention time is too short for historical migration. "
                "Use 5y, 10y, or another value suitable for multi-year data."
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


class CommandResult(BaseModel):
    ok: bool
    title: str
    output: str
    started_at: datetime
    finished_at: datetime
    migration_id: str
    proof: dict[str, Any] = Field(default_factory=dict)


class MigrationHistoryItem(BaseModel):
    migration_id: str
    created_at: datetime
    stage: str
    ok: bool
    proof: dict[str, Any] = Field(default_factory=dict)