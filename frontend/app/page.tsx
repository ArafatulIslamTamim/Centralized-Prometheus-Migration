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
  source_env_old: "lm1",
  source_env_new: "lm2",
  source_prom_path: "/var/lib/prometheus",
  target_prom_path: "/var/lib/prometheus",
  target_receive_dir: "",
  target_backup_dir: "",
  prom_bin: "/usr/local/bin/prometheus",
  promtool_bin: "",
  prom_retention_time: "10y",
  exact_range_step_seconds: 15,
  lm1_data_start: "",
  lm1_data_end: "",
  date_preset: "custom",
  snapshot_name: "",
  cleanup_confirmation: "",
  grafana_url: "http://localhost:3000",
  grafana_user: "admin",
  grafana_password: "",
};

type FlowMode = "range" | "snapshot";

type StageKey =
  | "precheck"
  | "backup"
  | "rangeManifest"
  | "rangeTransfer"
  | "rangeMerge"
  | "snapshot"
  | "transfer"
  | "merge"
  | "cleanup"
  | "validate"
  | "grafana";

const stageLabels: Record<StageKey, string> = {
  precheck: "Pre-checks",
  backup: "Target backup",
  rangeManifest: "Range manifest",
  rangeTransfer: "Range transfer",
  rangeMerge: "Range merge",
  snapshot: "Source snapshot",
  transfer: "Snapshot transfer",
  merge: "Target merge",
  cleanup: "Target cleanup",
  validate: "Validation",
  grafana: "Grafana",
};

const rangeFlow: StageKey[] = [
  "precheck",
  "backup",
  "rangeManifest",
  "rangeTransfer",
  "rangeMerge",
  "validate",
  "grafana",
];

const snapshotFlow: StageKey[] = [
  "precheck",
  "backup",
  "snapshot",
  "transfer",
  "merge",
  "validate",
  "grafana",
];

const advancedFlow: StageKey[] = ["cleanup"];

function updateNested<T extends object>(obj: T, path: string[], value: string): T {
  const [head, ...tail] = path;
  if (!head) return obj;

  return {
    ...obj,
    [head]: tail.length ? updateNested((obj as any)[head] || {}, tail, value) : value,
  };
}

function toDateTimeLocal(value?: string | null) {
  if (!value) return "";
  const match = value.match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/);
  return match ? `${match[1]}-${match[2]}-${match[3]}T${match[4]}:${match[5]}` : "";
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
  const [date, time] = value.split("T");
  return `${date} ${time}:00 ${getLocalTimezoneOffset()}`;
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
  value?: string | number | null;
  onChange: (value: string) => void;
  type?: string;
  help?: string;
  placeholder?: string;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <input
        value={value ?? ""}
        type={type}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
      />
      {help ? <small>{help}</small> : null}
    </label>
  );
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

function StatusBadge({ result, running }: { result?: CommandResult; running?: boolean }) {
  if (running) {
    return (
      <span className="badge running">
        <span className="dot" /> Running
      </span>
    );
  }

  if (!result) return <span className="badge idle">Not run</span>;

  return result.ok ? (
    <span className="badge pass">
      <CheckCircle2 size={12} /> PASS
    </span>
  ) : (
    <span className="badge fail">
      <XCircle size={12} /> FAIL
    </span>
  );
}

function OutputPanel({ result }: { result?: CommandResult }) {
  if (!result) {
    return <pre className="terminal empty">No command output yet. Run a stage to see output here.</pre>;
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

function SectionHeader({ icon, title, subtitle }: { icon: ReactNode; title: string; subtitle?: string }) {
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
  if (preset === "poc_1h") {
    return {
      ...config,
      date_preset: preset,
      lm1_data_start: "2026-05-12 14:00:00 +0600",
      lm1_data_end: "2026-05-12 15:00:00 +0600",
    };
  }

  if (preset === "poc_may12") {
    return {
      ...config,
      date_preset: preset,
      lm1_data_start: "2026-05-12 00:00:00 +0600",
      lm1_data_end: "2026-05-13 00:00:00 +0600",
    };
  }

  return { ...config, date_preset: preset };
}

export default function Page() {
  const [config, setConfig] = useState<MigrationConfig>(defaultConfig);
  const [flowMode, setFlowMode] = useState<FlowMode>("range");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [running, setRunning] = useState<StageKey | null>(null);
  const [results, setResults] = useState<Partial<Record<StageKey, CommandResult>>>({});
  const [activeLog, setActiveLog] = useState<StageKey>("precheck");
  const [error, setError] = useState<string | null>(null);

  const sourceName = config.source.name || "Source";
  const targetName = config.target.name || "Target";
  const sourceLabel = config.source_env_old || "lm1";
  const targetLabel = config.source_env_new || "lm2";
  const cleanupConfirmText = `DELETE ${sourceLabel} FROM ${targetName}`;

  const visibleStages = flowMode === "range" ? rangeFlow : snapshotFlow;
  const tabStages = showAdvanced ? [...visibleStages, ...advancedFlow] : visibleStages;

  const overall = useMemo(() => {
    const activeResults = tabStages.map((stage) => results[stage]).filter(Boolean) as CommandResult[];
    if (!activeResults.length) return { label: "Not Started", className: "yellow" };
    if (activeResults.some((item) => !item.ok)) return { label: "Needs Review", className: "red" };
    if (results.validate?.ok && results.grafana?.ok) return { label: "Validated", className: "green" };
    if (running) return { label: "Running", className: "cyan" };
    return { label: "In Progress", className: "cyan" };
  }, [results, running, tabStages]);

  const rangeReady = Boolean(config.lm1_data_start && config.lm1_data_end);
  const proofLink = config.migration_id ? migrationApi.proofUrl(config.migration_id) : null;

  function set(path: string, value: string) {
    setConfig((previous) => updateNested(previous, path.split("."), value));
  }

  function switchFlow(next: FlowMode) {
    setFlowMode(next);
    setActiveLog(next === "range" ? "rangeManifest" : "snapshot");
  }

  async function runStage(stage: StageKey) {
    setError(null);
    setRunning(stage);

    try {
      const configToSend: MigrationConfig = {
        ...config,
        exact_range_step_seconds: Number(config.exact_range_step_seconds || 15),
        migration_id: config.migration_id || results.precheck?.migration_id || null,
      };

      let result: CommandResult;
      if (stage === "precheck") result = await migrationApi.precheck(configToSend);
      else if (stage === "backup") result = await migrationApi.backupCreate(configToSend);
      else if (stage === "rangeManifest") result = await migrationApi.lm1RangeManifest(configToSend);
      else if (stage === "rangeTransfer") result = await migrationApi.transferRangeBlocks(configToSend);
      else if (stage === "rangeMerge") result = await migrationApi.mergeRangeBlocks(configToSend);
      else if (stage === "snapshot") result = await migrationApi.lm1CreateSnapshot(configToSend);
      else if (stage === "transfer") result = await migrationApi.lm1TransferSnapshot(configToSend);
      else if (stage === "merge") result = await migrationApi.lm2Merge(configToSend);
      else if (stage === "cleanup") result = await migrationApi.lm2CleanupOldSource(configToSend);
      else if (stage === "validate") result = await migrationApi.validate(configToSend);
      else result = await migrationApi.grafanaCheck(configToSend);

      setConfig((previous) => ({ ...previous, migration_id: result.migration_id }));
      setResults((previous) => ({ ...previous, [stage]: result }));
      setActiveLog(stage);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRunning(null);
    }
  }

  function stageDisabled(stage: StageKey) {
    if (running) return true;
    if (stage === "rangeManifest") return !rangeReady;
    if (stage === "rangeTransfer") return !rangeReady || !results.rangeManifest?.ok;
    if (stage === "rangeMerge") return !rangeReady || !results.rangeTransfer?.ok;
    if (stage === "transfer") return !results.snapshot?.ok;
    if (stage === "merge") return !results.transfer?.ok;
    if (stage === "cleanup") return config.cleanup_confirmation !== cleanupConfirmText;
    return false;
  }

  return (
    <main className="page">
      <header className="hero premium-card">
        <div>
          <p className="eyebrow">
            <Sparkles size={14} /> Prometheus Migration GUI
          </p>
          <h1>Centralized Prometheus Migration Control Panel</h1>
          <p className="subtitle">
            Choose one workflow. For exact 1-hour migration, use the Exact Range flow only:
            range manifest, range transfer, then range merge.
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
          <span>Active workflow</span>
          <strong>{flowMode === "range" ? "Exact Range" : "Full Snapshot"}</strong>
          <small>{flowMode === "range" ? "Only selected start/end samples" : "Whole snapshot blocks"}</small>
        </div>
        <div className="summary-card">
          <span>Source</span>
          <strong>{sourceName}</strong>
          <small>{config.source.user || "source_user"}@{config.source.host || "source_host"}</small>
        </div>
        <div className="summary-card">
          <span>Target</span>
          <strong>{targetName}</strong>
          <small>{config.target.user || "target_user"}@{config.target.host || "target_host"}</small>
        </div>
        <div className="summary-card">
          <span>Labels</span>
          <strong>{sourceLabel} → {targetLabel}</strong>
          <small>source_env separation</small>
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
            subtitle="Enter source, target, paths, labels, exact date range, and Grafana details."
          />

          <div className="flow-switch">
            <button className={flowMode === "range" ? "active" : ""} onClick={() => switchFlow("range")}>
              Exact Range Migration
            </button>
            <button className={flowMode === "snapshot" ? "active" : ""} onClick={() => switchFlow("snapshot")}>
              Full Snapshot Migration
            </button>
          </div>

          <div className="workflow-hint">
            {flowMode === "range" ? (
              <p>
                Use this for exact 1-hour import. Run: <strong>Pre-checks → Target backup → Range manifest → Range transfer → Range merge</strong>.
                Do not run snapshot transfer or target merge in this mode.
              </p>
            ) : (
              <p>
                Use this only when you want the full LM1 Prometheus snapshot flow. Do not mix it with range manifest/transfer/merge.
              </p>
            )}
          </div>

          <div className="config-group">
            <h3>Source / Old Machine</h3>
            <div className="form-grid">
              <Field label="Source display name" value={config.source.name} placeholder="LM1" onChange={(v) => set("source.name", v)} />
              <Field label="Source IP / host" value={config.source.host} placeholder="192.168.1.160" onChange={(v) => set("source.host", v)} />
              <Field label="Source SSH user" value={config.source.user} placeholder="student2" onChange={(v) => set("source.user", v)} />
              <Field label="Expected source hostname" value={config.source.expected_hostname} placeholder="Optional" onChange={(v) => set("source.expected_hostname", v)} help="Optional safety check." />
              <Field label="Source SSH password" value={config.source.ssh_password} type="password" onChange={(v) => set("source.ssh_password", v)} />
              <Field label="Source sudo password" value={config.source.sudo_password} type="password" placeholder="Leave empty to reuse SSH password" onChange={(v) => set("source.sudo_password", v)} />
              <Field label="Source Prometheus path" value={config.source_prom_path} placeholder="/var/lib/prometheus" onChange={(v) => set("source_prom_path", v)} />
              <Field label="Source imported label" value={config.source_env_old} placeholder="lm1" onChange={(v) => set("source_env_old", v)} />
            </div>
          </div>

          <div className="config-group">
            <h3>Target / New Machine</h3>
            <div className="form-grid">
              <Field label="Target display name" value={config.target.name} placeholder="LM2" onChange={(v) => set("target.name", v)} />
              <Field label="Target IP / host" value={config.target.host} placeholder="192.168.1.102" onChange={(v) => set("target.host", v)} />
              <Field label="Target SSH user" value={config.target.user} placeholder="student3" onChange={(v) => set("target.user", v)} />
              <Field label="Expected target hostname" value={config.target.expected_hostname} placeholder="Optional" onChange={(v) => set("target.expected_hostname", v)} help="Optional safety check." />
              <Field label="Target SSH password" value={config.target.ssh_password} type="password" onChange={(v) => set("target.ssh_password", v)} />
              <Field label="Target sudo password" value={config.target.sudo_password} type="password" placeholder="Leave empty to reuse SSH password" onChange={(v) => set("target.sudo_password", v)} />
              <Field label="Target Prometheus path" value={config.target_prom_path} placeholder="/var/lib/prometheus or /data" onChange={(v) => set("target_prom_path", v)} help="Must match active LM2 storage.tsdb.path." />
              <Field label="Target live label" value={config.source_env_new} placeholder="lm2" onChange={(v) => set("source_env_new", v)} />
              <Field label="Target receive directory" value={config.target_receive_dir} placeholder="/home/student3/old_data_range" onChange={(v) => set("target_receive_dir", v)} help="Use a temporary folder, not the active Prometheus path." />
              <Field label="Target backup directory" value={config.target_backup_dir} placeholder="/home/student3/prometheus-migration-backups" onChange={(v) => set("target_backup_dir", v)} />
            </div>
          </div>

          <div className="config-group">
            <h3>Exact Time Range and Prometheus Settings</h3>
            <div className="form-grid">
              <SelectField
                label="Date preset"
                value={config.date_preset}
                onChange={(v) => setConfig((previous) => applyDatePreset(previous, v))}
                options={[
                  { label: "Custom", value: "custom" },
                  { label: "PoC exact 1h: 2026-05-12 14:00-15:00 +0600", value: "poc_1h" },
                  { label: "PoC full day: 2026-05-12", value: "poc_may12" },
                ]}
              />
              <Field
                label="Prometheus binary"
                value={config.prom_bin}
                placeholder="/usr/local/bin/prometheus"
                onChange={(v) => set("prom_bin", v)}
              />

              <Field
                label="Promtool binary path (optional)"
                value={config.promtool_bin}
                placeholder="/home/testhouse/promtool-new"
                onChange={(v) => set("promtool_bin", v)}
                help="Leave empty to auto-detect. Use this if system promtool is old."
              />

              <DateTimeField
                label="Historical data start"
                value={config.lm1_data_start}
                onChange={(v) => set("lm1_data_start", v)}
                help="Exact start time for LM1 export."
              />
              <DateTimeField label="Historical data end" value={config.lm1_data_end} onChange={(v) => set("lm1_data_end", v)} help="Exact end time for LM1 export." />
              <Field label="Exact export step seconds" value={config.exact_range_step_seconds} type="number" placeholder="15" onChange={(v) => set("exact_range_step_seconds", v)} help="Use your LM1 scrape interval, usually 15." />
              <Field label="Retention time" value={config.prom_retention_time} placeholder="10y" onChange={(v) => set("prom_retention_time", v)} />
              {flowMode === "snapshot" ? (
                <Field label="Snapshot name override" value={config.snapshot_name} placeholder="Optional" onChange={(v) => set("snapshot_name", v)} />
              ) : null}
            </div>
          </div>

          <div className="config-group">
            <h3>Grafana</h3>
            <div className="form-grid">
              <Field label="Grafana URL on target" value={config.grafana_url} placeholder="http://localhost:3000" onChange={(v) => set("grafana_url", v)} />
              <Field label="Grafana user" value={config.grafana_user} placeholder="admin" onChange={(v) => set("grafana_user", v)} />
              <Field label="Grafana password" value={config.grafana_password} type="password" onChange={(v) => set("grafana_password", v)} />
            </div>
          </div>
        </div>

        <aside className="card action-card">
          <SectionHeader
            icon={<Activity size={18} />}
            title={flowMode === "range" ? "Exact Range Actions" : "Full Snapshot Actions"}
            subtitle="Only buttons for the selected workflow are shown."
          />

          <div className="actions">
            <ActionButton variant="secondary" onClick={() => runStage("precheck")} disabled={stageDisabled("precheck")}>
              <ShieldCheck size={16} /> Run Pre-Checks
            </ActionButton>
            <ActionButton variant="secondary" onClick={() => runStage("backup")} disabled={stageDisabled("backup")}>
              <DatabaseBackup size={16} /> Create Target Backup
            </ActionButton>

            {flowMode === "range" ? (
              <div className="danger-zone range-zone">
                <div>
                  <strong>Exact range transfer</strong>
                  <p>Exports only the selected LM1 samples, creates fresh TSDB blocks, transfers them, then merges them into LM2.</p>
                </div>
                <ActionButton variant="secondary" onClick={() => runStage("rangeManifest")} disabled={stageDisabled("rangeManifest")}>
                  <DatabaseBackup size={16} /> 1. Create Range Manifest
                </ActionButton>
                <ActionButton variant="cyan" onClick={() => runStage("rangeTransfer")} disabled={stageDisabled("rangeTransfer")}>
                  <ArrowRightLeft size={16} /> 2. Transfer Range Blocks
                </ActionButton>
                <ActionButton variant="primary" onClick={() => runStage("rangeMerge")} disabled={stageDisabled("rangeMerge")}>
                  <Layers3 size={16} /> 3. Merge Range Blocks
                </ActionButton>
              </div>
            ) : (
              <div className="danger-zone snapshot-zone">
                <div>
                  <strong>Full snapshot transfer</strong>
                  <p>Copies LM1 Prometheus snapshot blocks. This is not exact 1-hour migration.</p>
                </div>
                <ActionButton variant="secondary" onClick={() => runStage("snapshot")} disabled={stageDisabled("snapshot")}>
                  <DatabaseBackup size={16} /> 1. Create Source Snapshot
                </ActionButton>
                <ActionButton variant="cyan" onClick={() => runStage("transfer")} disabled={stageDisabled("transfer")}>
                  <ArrowRightLeft size={16} /> 2. Transfer Snapshot
                </ActionButton>
                <ActionButton variant="primary" onClick={() => runStage("merge")} disabled={stageDisabled("merge")}>
                  <Layers3 size={16} /> 3. Run Target Merge
                </ActionButton>
              </div>
            )}

            <ActionButton variant="secondary" onClick={() => runStage("validate")} disabled={stageDisabled("validate")}>
              <BadgeCheck size={16} /> Run Validation
            </ActionButton>
            <ActionButton variant="violet" onClick={() => runStage("grafana")} disabled={stageDisabled("grafana")}>
              <BarChart3 size={16} /> Check Grafana
            </ActionButton>

            <button className="advanced-toggle" onClick={() => setShowAdvanced((value) => !value)}>
              {showAdvanced ? "Hide advanced cleanup" : "Show advanced cleanup"}
            </button>

            {showAdvanced ? (
              <div className="danger-zone">
                <div>
                  <strong>Danger: Target cleanup</strong>
                  <p>This deletes existing {sourceLabel}-labelled data from {targetName}. Do not run it after a successful exact-range import unless you intentionally want to remove imported LM1 data.</p>
                </div>
                <Field label={`Type exactly: ${cleanupConfirmText}`} value={config.cleanup_confirmation} onChange={(v) => set("cleanup_confirmation", v)} placeholder={cleanupConfirmText} />
                <ActionButton variant="danger" onClick={() => runStage("cleanup")} disabled={stageDisabled("cleanup")}>
                  <Trash2 size={16} /> Delete Existing {sourceLabel} Data
                </ActionButton>
              </div>
            ) : null}
          </div>

          <div className="stage-list">
            {tabStages.map((stage) => (
              <button key={stage} className={`stage-row ${activeLog === stage ? "selected" : ""}`} onClick={() => setActiveLog(stage)}>
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
          <h3>No mixed flows</h3>
          <p>The UI separates exact range and full snapshot buttons so you do not accidentally run both.</p>
        </div>
        <div className="card mini">
          <DatabaseBackup size={20} />
          <h3>Exact range export</h3>
          <p>The range workflow exports only selected samples through Prometheus API and creates a new TSDB block.</p>
        </div>
        <div className="card mini">
          <BarChart3 size={20} />
          <h3>Merge safety</h3>
          <p>Range merge checks active storage path and refuses overlapping TSDB blocks instead of breaking Prometheus.</p>
        </div>
      </section>

      <section className="card logs-card">
        <SectionHeader icon={<Terminal size={18} />} title="Logs / Proof Output" subtitle="Click a stage above or use the tabs below to inspect output." />
        <div className="tabs">
          {tabStages.map((stage) => (
            <button key={stage} className={activeLog === stage ? "active" : ""} onClick={() => setActiveLog(stage)}>
              {stageLabels[stage]}
            </button>
          ))}
        </div>
        <OutputPanel result={results[activeLog]} />
      </section>

      <section className="card query-card">
        <SectionHeader icon={<Download size={18} />} title="Grafana Verification Queries" subtitle="Use these in Grafana Explore or panels after range merge." />
        <div className="query-grid">
          <pre>{`up{source_env="${sourceLabel}"}`}</pre>
          <pre>{`up{source_env="${targetLabel}"}`}</pre>
          <pre>{`sum(count_over_time(up{source_env="${sourceLabel}"}[$__range]))`}</pre>
          <pre>{`sum by(job, instance) (count_over_time(up{source_env="${sourceLabel}"}[$__range]))`}</pre>
        </div>
      </section>
    </main>
  );
}
