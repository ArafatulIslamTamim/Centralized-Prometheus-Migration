export type HostConfig = {
  name: string;
  host: string;
  user: string;
  ssh_password?: string;
  ssh_key_path?: string;
  sudo_password?: string;
  expected_hostname?: string;
};

export type MigrationConfig = {
  migration_id?: string | null;
  source: HostConfig;
  target: HostConfig;
  source_env_old: string;
  source_env_new: string;
  source_prom_path: string;
  target_prom_path: string;
  target_receive_dir: string;
  target_backup_dir: string;
  prom_bin: string;
  prom_retention_time: string;
  exact_range_step_seconds: number | string;
  lm1_data_start: string;
  lm1_data_end: string;
  date_preset?: string;
  snapshot_name?: string;
  cleanup_confirmation?: string;
  grafana_url: string;
  grafana_user: string;
  grafana_password?: string;
};

export type CommandResult = {
  ok: boolean;
  title: string;
  output: string;
  started_at: string;
  finished_at: string;
  migration_id: string;
  proof: Record<string, unknown>;
};