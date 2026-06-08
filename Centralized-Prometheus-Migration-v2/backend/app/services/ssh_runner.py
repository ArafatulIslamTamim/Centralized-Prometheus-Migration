from __future__ import annotations

import os
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import paramiko

from app.models.config_models import HostSSHConfig
from app.models.result_models import CommandResult
from app.services.shell_utils import mask_secrets, q


@dataclass
class SSHRunner:
    role: str
    cfg: HostSSHConfig
    strict_host_key_checking: bool = False

    def _client(self) -> paramiko.SSHClient:
        client = paramiko.SSHClient()
        if self.strict_host_key_checking:
            client.load_system_host_keys()
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
        else:
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        kwargs = {
            "hostname": self.cfg.host,
            "port": int(self.cfg.port or 22),
            "username": self.cfg.user,
            "timeout": 15,
            "banner_timeout": 20,
            "auth_timeout": 20,
            "look_for_keys": True,
            "allow_agent": True,
        }
        if self.cfg.ssh_key_path:
            kwargs["key_filename"] = os.path.expanduser(self.cfg.ssh_key_path)
            if self.cfg.ssh_key_passphrase:
                kwargs["passphrase"] = self.cfg.ssh_key_passphrase
        if self.cfg.ssh_password:
            kwargs["password"] = self.cfg.ssh_password
        client.connect(**kwargs)
        return client

    def secrets(self) -> list[str]:
        return [self.cfg.ssh_password, self.cfg.ssh_key_passphrase, self.cfg.sudo_password]

    def run(self, command: str, timeout: int = 120) -> CommandResult:
        start = time.time()
        try:
            with self._client() as client:
                stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
                rc = stdout.channel.recv_exit_status()
                out = stdout.read().decode(errors="replace")
                err = stderr.read().decode(errors="replace")
                return CommandResult(
                    ok=rc == 0,
                    role=self.role,
                    host=self.cfg.host,
                    command=mask_secrets(command, self.secrets()),
                    returncode=rc,
                    stdout=mask_secrets(out, self.secrets()),
                    stderr=mask_secrets(err, self.secrets()),
                    elapsed_seconds=round(time.time() - start, 3),
                )
        except socket.timeout as e:
            return CommandResult(ok=False, role=self.role, host=self.cfg.host, command=mask_secrets(command, self.secrets()), returncode=124, stderr=f"SSH timeout: {e}", elapsed_seconds=round(time.time() - start, 3))
        except Exception as e:
            return CommandResult(ok=False, role=self.role, host=self.cfg.host, command=mask_secrets(command, self.secrets()), returncode=255, stderr=f"SSH error: {e}", elapsed_seconds=round(time.time() - start, 3))

    def run_sudo(self, command: str, timeout: int = 120) -> CommandResult:
        if self.cfg.sudo_password:
            sudo_cmd = "printf %s " + q(self.cfg.sudo_password + "\n") + " | sudo -S -p '' bash -lc " + q(command)
        else:
            sudo_cmd = "sudo -n bash -lc " + q(command)
        return self.run(sudo_cmd, timeout=timeout)

    def sftp_client(self):
        client = self._client()
        sftp = client.open_sftp()
        return client, sftp

    def upload_text(self, remote_path: str, content: str, mode: int = 0o644) -> None:
        client, sftp = self.sftp_client()
        try:
            parent = str(Path(remote_path).parent)
            self.run("mkdir -p " + q(parent), timeout=30)
            with sftp.file(remote_path, "w") as f:
                f.write(content)
            sftp.chmod(remote_path, mode)
        finally:
            sftp.close()
            client.close()

    def download_text(self, remote_path: str) -> str:
        client, sftp = self.sftp_client()
        try:
            with sftp.file(remote_path, "r") as f:
                data = f.read()
            return data.decode(errors="replace") if isinstance(data, bytes) else data
        finally:
            sftp.close()
            client.close()
