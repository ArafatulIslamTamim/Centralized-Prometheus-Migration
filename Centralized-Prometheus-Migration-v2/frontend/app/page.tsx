"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  Activity,
  ArrowRightLeft,
  BadgeCheck,
  BarChart3,
  CheckCircle2,
  Database,
  DatabaseBackup,
  FileCheck2,
  FileJson,
  Layers3,
  List,
  Play,
  RefreshCw,
  Save,
  Server,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Terminal,
  UploadCloud,
  XCircle,
} from "lucide-react";

const API = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";

type AnyObj = Record<string, any>;

type StageKey =
  | "config"
  | "precheck"
  | "snapshot"
  | "transfer"
  | "scan"
  | "plan"
  | "execute"
  | "verify"
  | "annotations";

type ResultItem = {
  ok: boolean;
  title: string;
  output: string;
  proofFile?: string;
  raw?: any;
};

const defaultConfig: AnyObj = {
  source: { name: "Source", host: "", port: 22, user: "", ssh_password: "", ssh_key_path: "", ssh_key_passphrase: "", sudo_password: "" },
  target: { name: "Target", host: "", port: 22, user: "", ssh_password: "", ssh_key_path: "", ssh_key_passphrase: "", sudo_password: "" },
  prometheus: {
    source_temp_snapshot_port: 19090,
    source_url_from_source: "http://127.0.0.1:19090",
    source_tsdb_path: "/var/lib/prometheus/metrics2",
    source_snapshot_dir: "/var/lib/prometheus/metrics2/snapshots",
    target_url_from_target: "http://localhost:9090",
    target_data_path: "/data",
    target_staging_root: "",
    prometheus_service: "prometheus",
    prometheus_owner: "prometheus:prometheus",
  },
  grafana: {
    enabled: false,
    source_url_from_controller: "",
    source_user: "admin",
    source_password: "",
    target_url_from_controller: "",
    target_user: "admin",
    target_password: "",
    import_tag: "",
    extra_import_tags: [],
  },
  record_dir_on_controller: "./records",
  record_dir_on_target: "",
  transfer_mode: "controller_sftp_bridge",
  ssh_strict_host_key_checking: false,
};

function getPath(obj: AnyObj, path: string) {
  return path.split(".").reduce((a, k) => (a ? a[k] : undefined), obj);
}

function setPath(obj: AnyObj, path: string, value: any) {
  const copy = structuredClone(obj);
  let cur = copy;
  const parts = path.split(".");
  for (let i = 0; i < parts.length - 1; i += 1) {
    cur[parts[i]] = cur[parts[i]] || {};
    cur = cur[parts[i]];
  }
  cur[parts[parts.length - 1]] = value;
  return copy;
}

function ensureDerivedConfig(c: AnyObj) {
  const out = structuredClone(c);
  out.prometheus = out.prometheus || {};
  out.grafana = out.grafana || {};

  const targetUser = out?.target?.user || "";
  const targetHost = out?.target?.host || "";
  const sourceHost = out?.source?.host || "";
  const home = targetUser ? `/home/${targetUser}` : "/tmp";
  const tsdb = (out.prometheus.source_tsdb_path || "/var/lib/prometheus/metrics2").replace(/\/$/, "");
  const tempPort = Number(out.prometheus.source_temp_snapshot_port || 19090);

  out.prometheus.source_snapshot_dir = `${tsdb}/snapshots`;
  out.prometheus.source_url_from_source = `http://127.0.0.1:${tempPort}`;
  out.prometheus.target_url_from_target = out.prometheus.target_url_from_target || "http://localhost:9090";
  out.prometheus.target_staging_root = out.prometheus.target_staging_root || `${home}/prom_migration/staging`;
  out.record_dir_on_target = out.record_dir_on_target || `${home}/prom_migration/records`;
  out.transfer_mode = "controller_sftp_bridge";

  if (sourceHost && !out.grafana.source_url_from_controller) out.grafana.source_url_from_controller = `http://${sourceHost}:3000`;
  if (targetHost && !out.grafana.target_url_from_controller) out.grafana.target_url_from_controller = `http://${targetHost}:3000`;
  return out;
}

async function callApi(path: string, opts: RequestInit = {}) {
  const res = await fetch(API + path, {
    ...opts,
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
  });
  const text = await res.text();
  let data: any = text;
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    // keep raw
  }
  if (!res.ok) {
    throw new Error(typeof data?.detail === "string" ? data.detail : JSON.stringify(data, null, 2));
  }
  return data;
}

function fmtBytes(n?: number) {
  if (!n) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = n;
  let index = 0;
  while (value > 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(index ? 2 : 0)} ${units[index]}`;
}

function Field({ label, value, onChange, type = "text", help, placeholder }: { label: string; value?: any; onChange: (value: string) => void; type?: string; help?: string; placeholder?: string }) {
  return (
    <label className="field">
      <span>{label}</span>
      <input type={type} value={value ?? ""} placeholder={placeholder} onChange={(event) => onChange(event.target.value)} />
      {help ? <small>{help}</small> : null}
    </label>
  );
}

function CheckboxField({ label, checked, onChange, help }: { label: string; checked?: boolean; onChange: (value: boolean) => void; help?: string }) {
  return (
    <label className="field checkbox-line">
      <span>{label}</span>
      <input type="checkbox" checked={!!checked} onChange={(event) => onChange(event.target.checked)} />
      {help ? <small>{help}</small> : null}
    </label>
  );
}

function SelectField({ label, value, onChange, options, help }: { label: string; value?: any; onChange: (value: string) => void; options: { label: string; value: string }[]; help?: string }) {
  return (
    <label className="field">
      <span>{label}</span>
      <select value={value ?? ""} onChange={(event) => onChange(event.target.value)}>
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

function ActionButton({ children, onClick, disabled, variant = "secondary" }: { children: ReactNode; onClick: () => void; disabled?: boolean; variant?: "primary" | "secondary" | "danger" | "cyan" | "violet" | "safe" }) {
  return (
    <button className={`action-button ${variant}`} onClick={onClick} disabled={disabled}>
      {children}
    </button>
  );
}

function StatusBadge({ result, running }: { result?: ResultItem; running?: boolean }) {
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

function OutputPanel({ result }: { result?: ResultItem }) {
  if (result) return <pre className="terminal">{result.output || "No output returned."}</pre>;
  return <pre className="terminal empty">No command output yet. Run a stage to see output here.</pre>;
}

function normalizeResult(title: string, out: any): ResultItem {
  const ok = out?.ok !== false;
  return {
    ok,
    title,
    output: JSON.stringify(out, null, 2),
    proofFile: out?.proofFile,
    raw: out,
  };
}

export default function Page() {
  const [cfg, setCfg] = useState<AnyObj>(defaultConfig);
  const [activeStage, setActiveStage] = useState<StageKey>("config");
  const [results, setResults] = useState<Partial<Record<StageKey, ResultItem>>>({});
  const [running, setRunning] = useState<StageKey | "">("");
  const [error, setError] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [snapshots, setSnapshots] = useState<any[]>([]);
  const [snapshotId, setSnapshotId] = useState("");
  const [snapshotSourcePath, setSnapshotSourcePath] = useState("");
  const [snapshotTargetPath, setSnapshotTargetPath] = useState("");
  const [range, setRange] = useState({ start: "2023-03-24 00:00:00", end: "2024-03-02 06:00:00", label: "migration", mode: "safe_merge", selection: "overlap" });
  const [planId, setPlanId] = useState("");
  const [confirm, setConfirm] = useState("");
  const [annFile, setAnnFile] = useState("");

  useEffect(() => {
    callApi("/api/config")
      .then((data) => setCfg(ensureDerivedConfig({ ...defaultConfig, ...(data.config || {}) })))
      .catch(() => setCfg(defaultConfig));
  }, []);

  const stageRows: { key: StageKey; label: string }[] = [
    { key: "config", label: "Configuration" },
    { key: "precheck", label: "Precheck" },
    { key: "snapshot", label: "Safe source snapshot" },
    { key: "transfer", label: "Transfer to target staging" },
    { key: "scan", label: "Scan blocks" },
    { key: "plan", label: "Build plan" },
    { key: "execute", label: "Execute" },
    { key: "verify", label: "Verify" },
    { key: "annotations", label: "Optional annotations" },
  ];

  const plan = results.plan?.raw;
  const planSummary = useMemo(() => {
    if (!plan) return null;
    return {
      copy: plan.selectedCount ?? plan.sourceBlockIdsToCopy?.length ?? 0,
      overlap: plan.overlapCount ?? 0,
      backup: plan.targetBackupCount ?? plan.targetBlockIdsToBackup?.length ?? 0,
      ok: !!plan.canExecute,
    };
  }, [plan]);

  function setCfgPath(path: string, value: any) {
    setCfg((old) => setPath(old, path, value));
  }

  async function saveConfig(showResult = true) {
    const fixed = ensureDerivedConfig(cfg);
    setCfg(fixed);
    const out = await callApi("/api/config", { method: "POST", body: JSON.stringify({ config: fixed }) });
    if (showResult) setResults((r) => ({ ...r, config: normalizeResult("Save config", out) }));
    return out.config || fixed;
  }

  async function runStage(stage: StageKey, title: string, fn: () => Promise<any>) {
    setError("");
    setRunning(stage);
    setActiveStage(stage);
    try {
      await saveConfig(false);
      const out = await fn();
      setResults((r) => ({ ...r, [stage]: normalizeResult(title, out) }));
      return out;
    } catch (e: any) {
      const message = e?.message || String(e);
      setError(message);
      setResults((r) => ({ ...r, [stage]: { ok: false, title, output: message } }));
      return null;
    } finally {
      setRunning("");
    }
  }

  async function createSnapshot() {
    return runStage("snapshot", "Create safe no-scrape snapshot", async () => {
      const out = await callApi("/api/snapshot/create", { method: "POST", body: JSON.stringify({}) });
      setSnapshotId(out.snapshot_id || "");
      setSnapshotSourcePath(out.snapshot_path || "");
      return out;
    });
  }

  async function listSnapshots() {
    return runStage("snapshot", "List source snapshots", async () => {
      const out = await callApi("/api/snapshot/list");
      setSnapshots(out.snapshots || []);
      const latest = (out.snapshots || []).slice(-1)[0];
      if (latest && !snapshotId) {
        setSnapshotId(latest.id);
        setSnapshotSourcePath(latest.path);
      }
      return out;
    });
  }

  async function transferSnapshot() {
    return runStage("transfer", "Transfer matching overlap blocks only to target staging", async () => {
      const out = await callApi("/api/snapshot/transfer", {
        method: "POST",
        body: JSON.stringify({
          snapshot_id: snapshotId,
          snapshot_path: snapshotSourcePath || undefined,
          transfer_mode: "controller_sftp_bridge",
          overwrite_staging: false,
          start_utc: range.start,
          end_utc: range.end,
          selection: range.selection,
        }),
      });
      setSnapshotTargetPath(out.snapshot_path_on_target || "");
      return out;
    });
  }

  async function scanBlocks() {
    return runStage("scan", "Scan staged source and target blocks", async () => {
      return callApi(`/api/blocks/scan?snapshot_path_on_target=${encodeURIComponent(snapshotTargetPath)}`);
    });
  }

  async function buildPlan() {
    return runStage("plan", "Build merge/replacement plan", async () => {
      const out = await callApi("/api/blocks/plan", {
        method: "POST",
        body: JSON.stringify({ snapshot_path_on_target: snapshotTargetPath, start_utc: range.start, end_utc: range.end, label: range.label || "migration", mode: range.mode, selection: range.selection }),
      });
      setPlanId(out.planId || "");
      return out;
    });
  }

  async function executePlan() {
    return runStage("execute", "Execute plan", async () => {
      return callApi("/api/blocks/execute", { method: "POST", body: JSON.stringify({ plan_id: planId, confirmation: confirm }) });
    });
  }

  async function verify() {
    return runStage("verify", "Verify target Prometheus", async () => {
      return callApi("/api/prometheus/verify", { method: "POST", body: JSON.stringify({}) });
    });
  }

  async function exportAnnotations() {
    return runStage("annotations", "Export source Grafana annotations", async () => {
      const out = await callApi("/api/annotations/export", { method: "POST", body: JSON.stringify({ from_utc: range.start, to_utc: range.end }) });
      setAnnFile(out.export_file || out.file || "");
      return out;
    });
  }

  async function importAnnotations() {
    return runStage("annotations", "Import annotations to target Grafana", async () => {
      return callApi("/api/annotations/import", { method: "POST", body: JSON.stringify({ export_file: annFile }) });
    });
  }

  async function verifyAnnotations() {
    return runStage("annotations", "Verify imported annotations", async () => {
      return callApi("/api/annotations/verify", { method: "POST", body: JSON.stringify({ from_utc: range.start, to_utc: range.end }) });
    });
  }

  const visibleResult = results[activeStage];

  return (
    <main className="page">
      <section className="hero premium-card">
        <div>
          <p className="eyebrow"><Sparkles size={16} /> Prometheus Migration Controller</p>
          <h1>Safe Snapshot Migration GUI</h1>
          <p className="subtitle">
            First-GUI style workflow: create a temporary no-scrape snapshot, transfer only the selected time-range blocks to target staging, build a raw-block plan, then safely merge or replace with backup.
          </p>
        </div>
        <div className="overall-card">
          <span>Current workflow</span>
          <strong className="cyan">No-scrape snapshot</strong>
          <small>Normal source Prometheus must stay stopped. Target blocks are moved to backup before replacement.</small>
        </div>
      </section>

      {error ? <div className="error"><ShieldAlert size={18} /> {error}</div> : null}

      <section className="summary-grid">
        <div className="summary-card"><span>Snapshot</span><strong>{snapshotId || "Not created"}</strong><small>{snapshotSourcePath || "Source snapshot path will appear here"}</small></div>
        <div className="summary-card"><span>Target staging</span><strong>{snapshotTargetPath ? "Ready" : "Empty"}</strong><small>{snapshotTargetPath || "Transfer snapshot first"}</small></div>
        <div className="summary-card"><span>Plan</span><strong>{planId || "No plan"}</strong><small>{planSummary ? `${planSummary.copy} source blocks, ${planSummary.overlap} overlaps` : "Build plan after scan"}</small></div>
        <div className="summary-card"><span>Mode</span><strong className={range.mode === "replacement" ? "yellow" : "green"}>{range.mode === "replacement" ? "Replacement" : "Safe merge"}</strong><small>{range.mode === "replacement" ? "Requires uppercase YES" : "No target blocks moved"}</small></div>
      </section>

      <section className="grid two">
        <div className="card">
          <SectionHeader icon={<Server size={18} />} title="Simple configuration" subtitle="Only the required fields are shown. Advanced values are derived automatically." />

          <div className="config-group">
            <h3>1. Source machine</h3>
            <div className="form-grid">
              <Field label="Source host/IP" value={cfg.source.host} onChange={(v) => setCfgPath("source.host", v)} placeholder="192.168.1.160" />
              <Field label="Source SSH user" value={cfg.source.user} onChange={(v) => setCfgPath("source.user", v)} placeholder="student2" />
              <Field label="Source SSH password" type="password" value={cfg.source.ssh_password} onChange={(v) => setCfgPath("source.ssh_password", v)} help="Leave empty if key/agent works." />
              <Field label="Source sudo password" type="password" value={cfg.source.sudo_password} onChange={(v) => setCfgPath("source.sudo_password", v)} help="Needed to start temporary no-scrape Prometheus for snapshot." />
              <Field label="Source TSDB path" value={cfg.prometheus.source_tsdb_path} onChange={(v) => setCfgPath("prometheus.source_tsdb_path", v)} help="Example: /var/lib/prometheus/metrics2" />
              <Field label="Temporary snapshot port" value={cfg.prometheus.source_temp_snapshot_port} onChange={(v) => setCfgPath("prometheus.source_temp_snapshot_port", Number(v || 19090))} help="Used only on source localhost, default 19090." />
            </div>
          </div>

          <div className="config-group">
            <h3>2. Target machine</h3>
            <div className="form-grid">
              <Field label="Target host/IP" value={cfg.target.host} onChange={(v) => setCfgPath("target.host", v)} placeholder="192.168.1.102" />
              <Field label="Target SSH user" value={cfg.target.user} onChange={(v) => setCfgPath("target.user", v)} placeholder="student3" />
              <Field label="Target SSH password" type="password" value={cfg.target.ssh_password} onChange={(v) => setCfgPath("target.ssh_password", v)} help="Leave empty if key/agent works." />
              <Field label="Target sudo password" type="password" value={cfg.target.sudo_password} onChange={(v) => setCfgPath("target.sudo_password", v)} help="Needed to stop/start Prometheus and modify target data path." />
              <Field label="Target Prometheus data path" value={cfg.prometheus.target_data_path} onChange={(v) => setCfgPath("prometheus.target_data_path", v)} help="Example: /data or /var/lib/prometheus" />
              <Field label="Target staging folder" value={cfg.prometheus.target_staging_root} onChange={(v) => setCfgPath("prometheus.target_staging_root", v)} help="Auto: /home/<target-user>/prom_migration/staging" />
            </div>
          </div>

          <div className="config-group range-zone">
            <h3>3. Time range and plan mode</h3>
            <div className="form-grid">
              <Field label="Start UTC" value={range.start} onChange={(v) => setRange((r) => ({ ...r, start: v }))} help="Example: 2023-03-24 00:00:00" />
              <Field label="End UTC" value={range.end} onChange={(v) => setRange((r) => ({ ...r, end: v }))} help="Example: 2024-03-02 06:00:00" />
              <Field label="Plan label" value={range.label} onChange={(v) => setRange((r) => ({ ...r, label: v }))} />
              <SelectField label="Execution mode" value={range.mode} onChange={(v) => setRange((r) => ({ ...r, mode: v }))} options={[{ label: "Safe merge only", value: "safe_merge" }, { label: "Replacement with backup", value: "replacement" }]} />
              <SelectField label="Matching block rule" value={range.selection} onChange={(v) => setRange((r) => ({ ...r, selection: v }))} help="Default matches your manual command: send only source blocks whose time range overlaps the requested range. Inside mode is stricter and may miss edge blocks." options={[{ label: "Overlap requested range (recommended)", value: "overlap" }, { label: "Inside range only (advanced)", value: "inside" }]} />
            </div>
          </div>

          <div className="config-group">
            <h3>4. Optional Grafana annotations</h3>
            <CheckboxField label="Migrate Grafana annotations" checked={cfg.grafana.enabled} onChange={(v) => setCfgPath("grafana.enabled", v)} help="Leave off if you only want Prometheus data migration." />
            {cfg.grafana.enabled ? (
              <div className="form-grid">
                <Field label="Source Grafana URL" value={cfg.grafana.source_url_from_controller} onChange={(v) => setCfgPath("grafana.source_url_from_controller", v)} placeholder="http://source-ip:3000" />
                <Field label="Target Grafana URL" value={cfg.grafana.target_url_from_controller} onChange={(v) => setCfgPath("grafana.target_url_from_controller", v)} placeholder="http://target-ip:3000" />
                <Field label="Source Grafana user" value={cfg.grafana.source_user} onChange={(v) => setCfgPath("grafana.source_user", v)} />
                <Field label="Source Grafana password" type="password" value={cfg.grafana.source_password} onChange={(v) => setCfgPath("grafana.source_password", v)} />
                <Field label="Target Grafana user" value={cfg.grafana.target_user} onChange={(v) => setCfgPath("grafana.target_user", v)} />
                <Field label="Target Grafana password" type="password" value={cfg.grafana.target_password} onChange={(v) => setCfgPath("grafana.target_password", v)} />
                
              </div>
            ) : null}
          </div>

          <button className="advanced-toggle" onClick={() => setShowAdvanced((v) => !v)}>{showAdvanced ? "Hide advanced fields" : "Show advanced fields"}</button>
          {showAdvanced ? (
            <div className="config-group">
              <h3>Advanced</h3>
              <div className="form-grid">
                <Field label="Source SSH key path" value={cfg.source.ssh_key_path} onChange={(v) => setCfgPath("source.ssh_key_path", v)} />
                <Field label="Target SSH key path" value={cfg.target.ssh_key_path} onChange={(v) => setCfgPath("target.ssh_key_path", v)} />
                <Field label="Prometheus service name" value={cfg.prometheus.prometheus_service} onChange={(v) => setCfgPath("prometheus.prometheus_service", v)} />
                <Field label="Target owner" value={cfg.prometheus.prometheus_owner} onChange={(v) => setCfgPath("prometheus.prometheus_owner", v)} />
                <Field label="Target record dir" value={cfg.record_dir_on_target} onChange={(v) => setCfgPath("record_dir_on_target", v)} />
                <Field label="Controller record dir" value={cfg.record_dir_on_controller} onChange={(v) => setCfgPath("record_dir_on_controller", v)} />
              </div>
            </div>
          ) : null}
        </div>

        <aside className="card action-card">
          <SectionHeader icon={<Activity size={18} />} title="Run workflow" subtitle="Run in order. Plan and scan steps do not modify target /data." />
          <div className="actions">
            <ActionButton variant="primary" onClick={() => runStage("config", "Save config", () => saveConfig(true))} disabled={!!running}><Save size={17} /> Save config</ActionButton>
            <ActionButton onClick={() => runStage("precheck", "Full precheck", () => callApi("/api/precheck", { method: "POST", body: JSON.stringify({}) }))} disabled={!!running}><ShieldCheck size={17} /> 1. Full precheck</ActionButton>
            <ActionButton variant="violet" onClick={createSnapshot} disabled={!!running}><DatabaseBackup size={17} /> 2. Create safe snapshot</ActionButton>
            <ActionButton onClick={listSnapshots} disabled={!!running}><List size={17} /> List snapshots</ActionButton>
            <ActionButton variant="cyan" onClick={transferSnapshot} disabled={!!running || !snapshotId}><UploadCloud size={17} /> 3. Transfer matching blocks only</ActionButton>
            <ActionButton onClick={scanBlocks} disabled={!!running || !snapshotTargetPath}><Layers3 size={17} /> 4. Scan staged + target blocks</ActionButton>
            <ActionButton variant="safe" onClick={buildPlan} disabled={!!running || !snapshotTargetPath}><FileCheck2 size={17} /> 5. Build plan</ActionButton>
          </div>

          {snapshots.length ? (
            <div className="config-group snapshot-zone">
              <h3>Select snapshot</h3>
              <SelectField label="Snapshot" value={snapshotId} onChange={(id) => {
                const s = snapshots.find((x) => x.id === id);
                setSnapshotId(id);
                setSnapshotSourcePath(s?.path || `${cfg.prometheus.source_snapshot_dir}/${id}`);
              }} options={snapshots.map((s) => ({ label: `${s.id} (${s.blockCount || 0} blocks)`, value: s.id }))} />
            </div>
          ) : null}

          {planSummary ? (
            <div className="config-group">
              <h3>Plan summary</h3>
              <div className="stats">
                <div className="stat"><span>Copy blocks</span><strong>{planSummary.copy}</strong></div>
                <div className="stat"><span>Overlaps</span><strong className={planSummary.overlap ? "yellow" : "green"}>{planSummary.overlap}</strong></div>
                <div className="stat"><span>Backups</span><strong>{planSummary.backup}</strong></div>
                <div className="stat"><span>Executable</span><strong className={planSummary.ok ? "green" : "red"}>{planSummary.ok ? "Yes" : "No"}</strong></div>
              </div>
              {plan?.reasons?.length ? <p className="note">{plan.reasons.join(" | ")}</p> : null}
            </div>
          ) : null}

          <div className="danger-zone">
            <div>
              <strong>6. Execute data change</strong>
              <p>Safe merge adds non-overlapping blocks. Replacement moves overlapping target blocks to backup first. It never deletes them permanently.</p>
            </div>
            <Field label="Plan ID" value={planId} onChange={setPlanId} />
            {range.mode === "replacement" ? <Field label="Type YES for replacement" value={confirm} onChange={setConfirm} help="Exact uppercase YES is required." /> : null}
            <ActionButton variant={range.mode === "replacement" ? "danger" : "safe"} onClick={executePlan} disabled={!!running || !planId || (range.mode === "replacement" && confirm !== "YES")}><Play size={17} /> Execute plan</ActionButton>
          </div>

          <div className="actions">
            <ActionButton onClick={verify} disabled={!!running}><BadgeCheck size={17} /> 7. Verify target</ActionButton>
          </div>

          {cfg.grafana.enabled ? (
            <div className="config-group">
              <h3>8. Grafana annotations</h3>
              <div className="actions">
                <ActionButton onClick={exportAnnotations} disabled={!!running}><FileJson size={17} /> Export annotations</ActionButton>
                <ActionButton onClick={importAnnotations} disabled={!!running || !annFile}><ArrowRightLeft size={17} /> Import annotations</ActionButton>
                <ActionButton onClick={verifyAnnotations} disabled={!!running}><BarChart3 size={17} /> Verify annotations</ActionButton>
              </div>
            </div>
          ) : null}

          <div className="stage-list">
            {stageRows.map((row) => (
              <button key={row.key} className={`stage-row ${activeStage === row.key ? "selected" : ""}`} onClick={() => setActiveStage(row.key)}>
                <span>{row.label}</span>
                <StatusBadge result={results[row.key]} running={running === row.key} />
              </button>
            ))}
          </div>
        </aside>
      </section>

      <section className="grid two">
        <div className="card">
          <SectionHeader icon={<Terminal size={18} />} title={visibleResult?.title || "Command output"} subtitle="Proof files are saved on the controller and target record folders." />
          <OutputPanel result={visibleResult} />
        </div>
        <div className="card">
          <SectionHeader icon={<Database size={18} />} title="Important safety notes" />
          <div className="mini">
            <h3 className="green">Source safety</h3>
            <p>The snapshot step starts only a temporary Prometheus with <code>scrape_configs: []</code>. It refuses to run if the normal source Prometheus service is active.</p>
            <h3 className="cyan">Target safety</h3>
            <p>Transfer sends only matching whole blocks to staging. Build plan only reads block metadata. Target <code>/data</code> changes only when you click Execute.</p>
            <h3 className="yellow">Replacement safety</h3>
            <p>Replacement mode moves overlapping target blocks to a backup folder before copying source blocks. It requires exact uppercase <code>YES</code>.</p>
          </div>
        </div>
      </section>
    </main>
  );
}
