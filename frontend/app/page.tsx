"use client";

import { useMemo, useState, type ReactNode } from "react";
import {
  Activity,
  ArrowRightLeft,
  BadgeCheck,
  BarChart3,
  CheckCircle2,
  DatabaseBackup,
  Download,
  FileJson,
  Layers3,
  Server,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Terminal,
  Trash2,
  XCircle,
} from "lucide-react";
import { migrationApi } from "../lib/api";
import type { CommandResult, MigrationConfig } from "../lib/types";

const defaultConfig: MigrationConfig = {
  migration_id: null,

  source: {
    name: "",
    host: "",
    user: "",
    ssh_password: "",
    ssh_key_path: "",
    sudo_password: "",
    expected_hostname: "",
  },

  target: {
    name: "",
    host: "",
    user: "",
    ssh_password: "",
    ssh_key_path: "",
    sudo_password: "",
    expected_hostname: "",
  },

  source_env_old: "",
  source_env_new: "",

  source_prom_path: "",
  target_prom_path: "",

  target_receive_dir: "",
  target_backup_dir: "",

  prom_bin: "",
  prom_retention_time: "",

  lm1_data_start: "",
  lm1_data_end: "",
  date_preset: "custom",

  snapshot_name: "",
  cleanup_confirmation: "",

  grafana_url: "",
  grafana_user: "",
  grafana_password: "",
};

type StageKey =
  | "precheck"
  | "backup"
  | "snapshot"
  | "cleanup"
  | "transfer"
  | "merge"
  | "validate"
  | "grafana";

const stageLabels: Record<StageKey, string> = {
  precheck: "Pre-checks",
  backup: "Target backup",
  snapshot: "Source snapshot",
  cleanup: "Target cleanup",
  transfer: "Snapshot transfer",
  merge: "Target merge",
  validate: "Validation",
  grafana: "Grafana",
};

function updateNested<T extends object>(obj: T, path: string[], value: string): T {
  const [head, ...tail] = path;
  if (!head) return obj;

  return {
    ...obj,
    [head]: tail.length
      ? updateNested((obj as any)[head] || {}, tail, value)
      : value,
  };
}

function toDateTimeLocal(value?: string | null) {
  if (!value) return "";

  // Converts: 2026-05-12 00:00:00 +0600
  // To:       2026-05-12T00:00
  const match = value.match(
    /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/
  );

  if (!match) return "";

  return `${match[1]}-${match[2]}-${match[3]}T${match[4]}:${match[5]}`;
}

function getLocalTimezoneOffset() {
  const offsetMinutes = -new Date().getTimezoneOffset();
  const sign = offsetMinutes >= 0 ? "+" : "-";
  const abs = Math.abs(offsetMinutes);
  const hours = String(Math.floor(abs / 60)).padStart(2, "0");
  const minutes = String(abs % 60).padStart(2, "0");

  return `${sign}${hours}${minutes}`;
}

function fromDateTimeLocal(value: string) {
  if (!value) return "";

  // Converts: 2026-05-12T00:00
  // To:       2026-05-12 00:00:00 +0600
  const [date, time] = value.split("T");

  return `${date} ${time}:00 ${getLocalTimezoneOffset()}`;
}

function DateTimeField({
  label,
  value,
  onChange,
  help,
}: {
  label: string;
  value?: string | null;
  onChange: (value: string) => void;
  help?: string;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <input
        type="datetime-local"
        value={toDateTimeLocal(value)}
        onChange={(event) => onChange(fromDateTimeLocal(event.target.value))}
      />
      {help ? <small>{help}</small> : null}
    </label>
  );
}

function Field({
  label,
  value,
  onChange,
  type = "text",
  help,
  placeholder,
}: {
  label: string;
  value?: string | null;
  onChange: (value: string) => void;
  type?: string;
  help?: string;
  placeholder?: string;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <input
        value={value || ""}
        type={type}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
      />
      {help ? <small>{help}</small> : null}
    </label>
  );
}

function SelectField({
  label,
  value,
  onChange,
  options,
  help,
}: {
  label: string;
  value?: string | null;
  onChange: (value: string) => void;
  options: { label: string; value: string }[];
  help?: string;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <select value={value || ""} onChange={(event) => onChange(event.target.value)}>
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      {help ? <small>{help}</small> : null}
    </label>
  );
}

function StatusBadge({
  result,
  running,
}: {
  result?: CommandResult;
  running?: boolean;
}) {
  if (running) {
    return (
      <span className="badge running">
        <span className="dot" />
        Running
      </span>
    );
  }

  if (!result) {
    return <span className="badge idle">Not run</span>;
  }

  return result.ok ? (
    <span className="badge pass">
      <CheckCircle2 size={12} />
      PASS
    </span>
  ) : (
    <span className="badge fail">
      <XCircle size={12} />
      FAIL
    </span>
  );
}

function OutputPanel({ result }: { result?: CommandResult }) {
  if (!result) {
    return (
      <pre className="terminal empty">
        No command output yet. Run a stage to see output here.
      </pre>
    );
  }

  return <pre className="terminal">{result.output || "No output returned."}</pre>;
}

function ActionButton({
  children,
  onClick,
  disabled,
  variant = "secondary",
}: {
  children: ReactNode;
  onClick: () => void;
  disabled?: boolean;
  variant?: "primary" | "secondary" | "danger" | "cyan" | "violet";
}) {
  return (
    <button className={`action-button ${variant}`} onClick={onClick} disabled={disabled}>
      {children}
    </button>
  );
}

function SectionHeader({
  icon,
  title,
  subtitle,
}: {
  icon: ReactNode;
  title: string;
  subtitle?: string;
}) {
  return (
    <div className="section-header">
      <div className="section-icon">{icon}</div>
      <div>
        <h2>{title}</h2>
        {subtitle ? <p>{subtitle}</p> : null}
      </div>
    </div>
  );
}

function applyDatePreset(config: MigrationConfig, preset: string): MigrationConfig {
  if (preset === "poc_may12") {
    return {
      ...config,
      date_preset: preset,
      lm1_data_start: "2026-05-12 00:00:00 +0600",
      lm1_data_end: "2026-05-13 00:00:00 +0600",
    };
  }

  if (preset === "prod_2021_mar2024") {
    return {
      ...config,
      date_preset: preset,
      lm1_data_start: "2021-01-01 00:00:00 +0000",
      lm1_data_end: "2024-03-31 23:59:59 +0000",
    };
  }

  if (preset === "last_24h") {
    return {
      ...config,
      date_preset: preset,
      lm1_data_start: "",
      lm1_data_end: "",
    };
  }

  if (preset === "last_7d") {
    return {
      ...config,
      date_preset: preset,
      lm1_data_start: "",
      lm1_data_end: "",
    };
  }

  return {
    ...config,
    date_preset: preset,
    lm1_data_start: "",
    lm1_data_end: "",
  };
}

export default function Page() {
  const [config, setConfig] = useState<MigrationConfig>(defaultConfig);
  const [running, setRunning] = useState<StageKey | null>(null);
  const [results, setResults] = useState<Partial<Record<StageKey, CommandResult>>>({});
  const [activeLog, setActiveLog] = useState<StageKey>("precheck");
  const [error, setError] = useState<string | null>(null);

  const sourceName = config.source.name || "Source";
  const targetName = config.target.name || "Target";
  const sourceLabel = config.source_env_old || "source_label";
  const targetLabel = config.source_env_new || "target_label";

  const cleanupConfirmText = `DELETE ${sourceLabel} FROM ${targetName}`;

  const overall = useMemo(() => {
    const values = Object.values(results);

    if (!values.length) {
      return { label: "Not Started", className: "yellow" };
    }

    if (values.some((item) => item && !item.ok)) {
      return { label: "Needs Review", className: "red" };
    }

    if (results.validate?.ok && results.grafana?.ok) {
      return { label: "Validated", className: "green" };
    }

    if (running) {
      return { label: "Running", className: "cyan" };
    }

    return { label: "In Progress", className: "cyan" };
  }, [results, running]);

  function set(path: string, value: string) {
    setConfig((previous) => updateNested(previous, path.split("."), value));
  }

  async function runStage(stage: StageKey) {
    setError(null);
    setRunning(stage);

    try {
      const configToSend = {
        ...config,
        migration_id: config.migration_id || results.precheck?.migration_id || null,
      };

      let result: CommandResult;

      if (stage === "precheck") {
        result = await migrationApi.precheck(configToSend);
      } else if (stage === "backup") {
        result = await migrationApi.backupCreate(configToSend);
      } else if (stage === "snapshot") {
        result = await migrationApi.lm1CreateSnapshot(configToSend);
      } else if (stage === "cleanup") {
        result = await migrationApi.lm2CleanupOldSource(configToSend);
      } else if (stage === "transfer") {
        result = await migrationApi.lm1TransferSnapshot(configToSend);
      } else if (stage === "merge") {
        result = await migrationApi.lm2Merge(configToSend);
      } else if (stage === "validate") {
        result = await migrationApi.validate(configToSend);
      } else {
        result = await migrationApi.grafanaCheck(configToSend);
      }

      setConfig((previous) => ({
        ...previous,
        migration_id: result.migration_id,
      }));

      setResults((previous) => ({
        ...previous,
        [stage]: result,
      }));

      setActiveLog(stage);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRunning(null);
    }
  }

  const proofLink = config.migration_id
    ? migrationApi.proofUrl(config.migration_id)
    : null;

  return (
    <main className="page">
      <header className="hero premium-card">
        <div>
          <p className="eyebrow">
            <Sparkles size={14} /> Prometheus Migration GUI
          </p>
          <h1>Centralized Prometheus Migration Control Panel</h1>
          <p className="subtitle">
            One-page control panel for source snapshots, target cleanup, TSDB merge,
            validation, and Grafana proof.
          </p>
        </div>

        <div className="overall-card">
          <span>Overall status</span>
          <strong className={overall.className}>{overall.label}</strong>
          <small>Migration ID: {config.migration_id || "not created"}</small>
        </div>
      </header>

      <section className="summary-grid">
        <div className="summary-card">
          <span>Source</span>
          <strong>{sourceName}</strong>
          <small>
            {config.source.user || "source_user"}@
            {config.source.host || "source_host"}
          </small>
        </div>

        <div className="summary-card">
          <span>Target</span>
          <strong>{targetName}</strong>
          <small>
            {config.target.user || "target_user"}@
            {config.target.host || "target_host"}
          </small>
        </div>

        <div className="summary-card">
          <span>Labels</span>
          <strong>
            {sourceLabel} to {targetLabel}
          </strong>
          <small>source_env separation</small>
        </div>

        <div className="summary-card">
          <span>Proof</span>
          <strong>{results.validate?.ok ? "Available" : "Pending"}</strong>
          <small>JSON and logs saved by backend</small>
        </div>
      </section>

      {error ? (
        <div className="error">
          <ShieldAlert size={18} /> {error}
        </div>
      ) : null}

      <section className="grid two">
        <div className="card config-card">
          <SectionHeader
            icon={<Server size={18} />}
            title="Migration Configuration"
            subtitle="Enter source, target, paths, labels, date range, and Grafana details."
          />

          <div className="config-group">
            <h3>Source / Old Machine</h3>

            <div className="form-grid">
              <Field
                label="Source display name"
                value={config.source.name}
                placeholder="Example: LM1 or Old Prometheus"
                onChange={(v) => set("source.name", v)}
              />

              <Field
                label="Source IP / host"
                value={config.source.host}
                placeholder="Example: localhost, 127.0.0.1, or 192.168.1.160"
                onChange={(v) => set("source.host", v)}
              />

              <Field
                label="Source SSH user"
                value={config.source.user}
                placeholder="Example: student2 or prometheus"
                onChange={(v) => set("source.user", v)}
              />

              <Field
                label="Expected source hostname"
                value={config.source.expected_hostname}
                placeholder="Optional. Example: lm1-server"
                onChange={(v) => set("source.expected_hostname", v)}
                help="Optional safety note to prevent wrong-machine mistakes."
              />

              <Field
                label="Source SSH password"
                value={config.source.ssh_password}
                type="password"
                placeholder="Enter source SSH password"
                onChange={(v) => set("source.ssh_password", v)}
              />

              <Field
                label="Source sudo password"
                value={config.source.sudo_password}
                type="password"
                placeholder="Leave empty to reuse SSH password"
                onChange={(v) => set("source.sudo_password", v)}
                help="Leave empty if sudo password is same as SSH password."
              />

              <Field
                label="Source Prometheus path"
                value={config.source_prom_path}
                placeholder="Example: /var/lib/prometheus"
                onChange={(v) => set("source_prom_path", v)}
              />

              <Field
                label="Source label"
                value={config.source_env_old}
                placeholder="Example: lm1 or old_prometheus"
                onChange={(v) => set("source_env_old", v)}
              />
            </div>
          </div>

          <div className="config-group">
            <h3>Target / New Machine</h3>

            <div className="form-grid">
              <Field
                label="Target display name"
                value={config.target.name}
                placeholder="Example: LM2 or Central Prometheus"
                onChange={(v) => set("target.name", v)}
              />

              <Field
                label="Target IP / host"
                value={config.target.host}
                placeholder="Example: 192.168.1.102 or target-server.local"
                onChange={(v) => set("target.host", v)}
              />

              <Field
                label="Target SSH user"
                value={config.target.user}
                placeholder="Example: student3 or prometheus"
                onChange={(v) => set("target.user", v)}
              />

              <Field
                label="Expected target hostname"
                value={config.target.expected_hostname}
                placeholder="Optional. Example: lm2-server"
                onChange={(v) => set("target.expected_hostname", v)}
                help="Optional safety note to prevent wrong-machine mistakes."
              />

              <Field
                label="Target SSH password"
                value={config.target.ssh_password}
                type="password"
                placeholder="Enter target SSH password"
                onChange={(v) => set("target.ssh_password", v)}
              />

              <Field
                label="Target sudo password"
                value={config.target.sudo_password}
                type="password"
                placeholder="Leave empty to reuse SSH password"
                onChange={(v) => set("target.sudo_password", v)}
                help="Leave empty if sudo password is same as SSH password."
              />

              <Field
                label="Target Prometheus path"
                value={config.target_prom_path}
                placeholder="Example: /var/lib/prometheus"
                onChange={(v) => set("target_prom_path", v)}
              />

              <Field
                label="Target label"
                value={config.source_env_new}
                placeholder="Example: lm2 or central_prometheus"
                onChange={(v) => set("source_env_new", v)}
              />

              <Field
                label="Target receive directory"
                value={config.target_receive_dir}
                placeholder="Example: /home/<TARGET_USER>/lm1-snapshot-direct"
                onChange={(v) => set("target_receive_dir", v)}
              />

              <Field
                label="Target backup directory"
                value={config.target_backup_dir}
                placeholder="Example: /home/<TARGET_USER>/prometheus-migration-backups"
                onChange={(v) => set("target_backup_dir", v)}
              />
            </div>
          </div>

          <div className="config-group">
            <h3>Time Range and Prometheus Settings</h3>

            <div className="form-grid">
              <SelectField
                label="Date preset"
                value={config.date_preset}
                onChange={(v) =>
                  setConfig((previous) => applyDatePreset(previous, v))
                }
                options={[
                  { label: "Custom", value: "custom" },
                  { label: "PoC example: 2026-05-12 to 2026-05-13", value: "poc_may12" },
                  { label: "Production example: 2021 to March 2024", value: "prod_2021_mar2024" },
                  { label: "Last 24 hours - manually enter timestamps", value: "last_24h" },
                  { label: "Last 7 days - manually enter timestamps", value: "last_7d" },
                ]}
                help="Choose a preset or keep Custom and enter exact timestamps."
              />

              <Field
                label="Prometheus binary"
                value={config.prom_bin}
                placeholder="Example: /usr/local/bin/prometheus"
                onChange={(v) => set("prom_bin", v)}
              />

              <DateTimeField
                label="Historical data start"
                value={config.lm1_data_start}
                onChange={(v) => set("lm1_data_start", v)}
                help="Select the start date and time from the calendar."
              />

              <DateTimeField
                label="Historical data end / cutoff"
                value={config.lm1_data_end}
                onChange={(v) => set("lm1_data_end", v)}
                help="Select the end date and time from the calendar."
              />

              <Field
                label="Retention time"
                value={config.prom_retention_time}
                placeholder="Example: 5y or 10y"
                onChange={(v) => set("prom_retention_time", v)}
                help="Use long retention for old historical data."
              />

              <Field
                label="Snapshot name override"
                value={config.snapshot_name}
                placeholder="Optional. Leave empty to use backend-generated snapshot name"
                onChange={(v) => set("snapshot_name", v)}
                help="Optional. Empty means the backend will use the default snapshot name."
              />
            </div>
          </div>

          <div className="config-group">
            <h3>Grafana</h3>

            <div className="form-grid">
              <Field
                label="Grafana URL on target"
                value={config.grafana_url}
                placeholder="Example: http://localhost:3000"
                onChange={(v) => set("grafana_url", v)}
              />

              <Field
                label="Grafana user"
                value={config.grafana_user}
                placeholder="Example: admin"
                onChange={(v) => set("grafana_user", v)}
              />

              <Field
                label="Grafana password"
                value={config.grafana_password}
                type="password"
                placeholder="Enter Grafana password"
                onChange={(v) => set("grafana_password", v)}
              />
            </div>
          </div>
        </div>

        <aside className="card action-card">
          <SectionHeader
            icon={<Activity size={18} />}
            title="Controlled Actions"
            subtitle="Run each stage manually. Risky steps are separated and protected."
          />

          <div className="actions">
            <ActionButton
              variant="secondary"
              onClick={() => runStage("precheck")}
              disabled={!!running}
            >
              <ShieldCheck size={16} /> Run Pre-Checks
            </ActionButton>

            <ActionButton
              variant="secondary"
              onClick={() => runStage("backup")}
              disabled={!!running}
            >
              <DatabaseBackup size={16} /> Create Target Backup
            </ActionButton>

            <ActionButton
              variant="secondary"
              onClick={() => runStage("snapshot")}
              disabled={!!running}
            >
              <DatabaseBackup size={16} /> Create Source Snapshot
            </ActionButton>

            <div className="danger-zone">
              <div>
                <strong>Optional cleanup before re-import</strong>
                <p>
                  This deletes existing {sourceLabel}-labelled data from {targetName}.
                  It does not delete {targetLabel} live data or Grafana annotations.
                </p>
              </div>

              <Field
                label={`Type exactly: ${cleanupConfirmText}`}
                value={config.cleanup_confirmation}
                onChange={(v) => set("cleanup_confirmation", v)}
                placeholder={cleanupConfirmText}
              />

              <ActionButton
                variant="danger"
                onClick={() => runStage("cleanup")}
                disabled={
                  !!running ||
                  !config.source_env_old ||
                  !config.target.name ||
                  config.cleanup_confirmation !== cleanupConfirmText
                }
              >
                <Trash2 size={16} /> Delete Existing {sourceLabel} Data from {targetName}
              </ActionButton>
            </div>

            <ActionButton
              variant="cyan"
              onClick={() => runStage("transfer")}
              disabled={!!running}
            >
              <ArrowRightLeft size={16} /> Transfer Snapshot to Target
            </ActionButton>

            <ActionButton
              variant="primary"
              onClick={() => runStage("merge")}
              disabled={!!running}
            >
              <Layers3 size={16} /> Run Target Merge
            </ActionButton>

            <ActionButton
              variant="secondary"
              onClick={() => runStage("validate")}
              disabled={!!running}
            >
              <BadgeCheck size={16} /> Run Validation
            </ActionButton>

            <ActionButton
              variant="violet"
              onClick={() => runStage("grafana")}
              disabled={!!running}
            >
              <BarChart3 size={16} /> Check Grafana
            </ActionButton>
          </div>

          <div className="stage-list">
            {(Object.keys(stageLabels) as StageKey[]).map((stage) => (
              <button
                key={stage}
                className={`stage-row ${activeLog === stage ? "selected" : ""}`}
                onClick={() => setActiveLog(stage)}
              >
                <span>{stageLabels[stage]}</span>
                <StatusBadge result={results[stage]} running={running === stage} />
              </button>
            ))}
          </div>

          {proofLink ? (
            <a className="download" href={proofLink} target="_blank" rel="noreferrer">
              <FileJson size={18} /> Download Proof JSON
            </a>
          ) : null}
        </aside>
      </section>

      <section className="grid three">
        <div className="card mini">
          <ShieldCheck size={20} />
          <h3>Safety-first flow</h3>
          <p>
            Backup, cleanup, transfer, and merge are separate actions to prevent
            accidental destructive execution.
          </p>
        </div>

        <div className="card mini">
          <DatabaseBackup size={20} />
          <h3>Sample-count proof</h3>
          <p>
            The backend stores before and after counts and compares source-labelled
            samples after the merge.
          </p>
        </div>

        <div className="card mini">
          <BarChart3 size={20} />
          <h3>Grafana proof</h3>
          <p>
            Confirm one Prometheus datasource and query both source_env labels from
            the target.
          </p>
        </div>
      </section>

      <section className="card logs-card">
        <SectionHeader
          icon={<Terminal size={18} />}
          title="Logs / Proof Output"
          subtitle="Click a stage above or use the tabs below to inspect output."
        />

        <div className="tabs">
          {(Object.keys(stageLabels) as StageKey[]).map((stage) => (
            <button
              key={stage}
              className={activeLog === stage ? "active" : ""}
              onClick={() => setActiveLog(stage)}
            >
              {stageLabels[stage]}
            </button>
          ))}
        </div>

        <OutputPanel result={results[activeLog]} />
      </section>

      <section className="card query-card">
        <SectionHeader
          icon={<Download size={18} />}
          title="Grafana Verification Queries"
          subtitle="Use these in Grafana Explore or panels after validation passes."
        />

        <div className="query-grid">
          <pre>{`up{source_env="${sourceLabel}"}`}</pre>
          <pre>{`up{source_env="${targetLabel}"}`}</pre>
          <pre>{`max by(job, instance) (
  count_over_time(up{source_env="${sourceLabel}"}[$__range])
)`}</pre>
          <pre>{`max by(job, instance) (
  count_over_time(up{source_env="${targetLabel}"}[$__range])
)`}</pre>
        </div>
      </section>
    </main>
  );
}