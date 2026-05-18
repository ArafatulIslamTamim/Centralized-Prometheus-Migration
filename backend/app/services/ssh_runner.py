from __future__ import annotations

import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import paramiko

from app.models.migration_models import HostConfig


@dataclass
class SSHOutput:
    exit_code: int
    stdout: str
    stderr: str

    @property
    def combined(self) -> str:
        parts = []
        if self.stdout:
            parts.append(self.stdout)
        if self.stderr:
            parts.append("\n[stderr]\n" + self.stderr)
        return "".join(parts).strip()


class SSHRunner:
    """Small Paramiko wrapper for running migration scripts remotely.

    This implementation is intentionally simple for a lab/PoC.
    For production, use SSH keys, restricted users, audit logging, and a secret vault.
    """

    def __init__(self, host_config: HostConfig, timeout: int = 30):
        self.config = host_config
        self.timeout = timeout
        self.client: Optional[paramiko.SSHClient] = None

    def __enter__(self) -> "SSHRunner":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def connect(self) -> None:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        key_filename = self.config.ssh_key_path or None
        password = self.config.ssh_password or None

        client.connect(
            hostname=self.config.host,
            username=self.config.user,
            password=password,
            key_filename=key_filename,
            timeout=self.timeout,
            banner_timeout=self.timeout,
            auth_timeout=self.timeout,
            look_for_keys=True if not password else False,
            allow_agent=True,
        )
        self.client = client

    def close(self) -> None:
        if self.client:
            self.client.close()
            self.client = None

    def run(self, command: str, timeout: int = 600, get_pty: bool = True) -> SSHOutput:
        if not self.client:
            raise RuntimeError("SSH client is not connected")

        stdin, stdout, stderr = self.client.exec_command(command, get_pty=get_pty, timeout=timeout)
        exit_code = stdout.channel.recv_exit_status()
        out = stdout.read().decode(errors="replace")
        err = stderr.read().decode(errors="replace")
        return SSHOutput(exit_code=exit_code, stdout=out, stderr=err)

    def run_bash(self, script: str, timeout: int = 1800, get_pty: bool = True) -> SSHOutput:
        escaped = script.replace("'", "'\\''")
        return self.run(f"bash -lc '{escaped}'", timeout=timeout, get_pty=get_pty)

    def put_text(self, remote_path: str, content: str) -> None:
        if not self.client:
            raise RuntimeError("SSH client is not connected")
        sftp = self.client.open_sftp()
        try:
            with sftp.file(remote_path, "w") as f:
                f.write(content)
        finally:
            sftp.close()

    def get_file(self, remote_path: str, local_path: str) -> None:
        if not self.client:
            raise RuntimeError("SSH client is not connected")
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        sftp = self.client.open_sftp()
        try:
            sftp.get(remote_path, local_path)
        finally:
            sftp.close()

    def put_file(self, local_path: str, remote_path: str) -> None:
        if not self.client:
            raise RuntimeError("SSH client is not connected")
        sftp = self.client.open_sftp()
        try:
            sftp.put(local_path, remote_path)
        finally:
            sftp.close()


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def sudo_prefix(password: Optional[str]) -> str:
    """Return a shell snippet that authenticates sudo without printing the password."""
    if not password:
        return "sudo -v"
    safe_password = shell_quote(password)
    return f"printf '%s\\n' {safe_password} | sudo -S -v"


def check_port(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False
