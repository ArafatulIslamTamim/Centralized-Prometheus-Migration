import { CommandResult, MigrationConfig } from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function postStage(path: string, config: MigrationConfig): Promise<CommandResult> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(`API error ${response.status}: ${text}`);
  }

  return response.json();
}

export const migrationApi = {
  precheck: (config: MigrationConfig) => postStage("/api/migration/precheck", config),
  backupCreate: (config: MigrationConfig) => postStage("/api/migration/backup-create", config),
  lm1CreateSnapshot: (config: MigrationConfig) => postStage("/api/migration/lm1-create-snapshot", config),
  lm2CleanupOldSource: (config: MigrationConfig) => postStage("/api/migration/lm2-cleanup-old-source", config),
  lm1TransferSnapshot: (config: MigrationConfig) => postStage("/api/migration/lm1-transfer-snapshot", config),
  lm2Merge: (config: MigrationConfig) => postStage("/api/migration/lm2-merge", config),
  validate: (config: MigrationConfig) => postStage("/api/migration/validate", config),
  grafanaCheck: (config: MigrationConfig) => postStage("/api/migration/grafana-check", config),
  proofUrl: (migrationId: string) => `${API_BASE}/api/migration/proof/${migrationId}`,
};
