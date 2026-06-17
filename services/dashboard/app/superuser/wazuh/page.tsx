"use client";

// Install-level Wazuh Ecosystem — Phase 6.6-b (+ 6.6-b.1 refinement, ADR 0020).
//
// Superuser-only page to configure where the install's Wazuh indexer(s),
// manager(s) and dashboard(s) physically live. Two shapes: single-host and
// distributed. Distributed components each carry an OPTIONAL friendly name
// (Indexer name / Master node name / Worker node name / Dashboard name) and a
// cluster may declare multiple dashboards. Save is validate-before-persist
// with a HARD fail — the backend probes every required endpoint and rejects
// the save if any blocker fails (distributed worker probes are warnings).
// Credentials are write-only: usernames shown, passwords blank = keep.

import {
  CheckCircle2,
  Network,
  Plus,
  Save,
  Trash2,
  XCircle,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError, fetchWazuhTopology, saveWazuhTopology } from "@/lib/api";
import { absoluteTimeTitle, relativeTime } from "@/lib/format";
import type {
  WazuhNode,
  WazuhProbeResult,
  WazuhTopologyShape,
  WazuhTopologyUpdate,
} from "@/lib/types";

type Kind = "single" | "distributed";
// Form-state node: name is a plain string ("" = no name → null on save).
type NodeForm = { url: string; name: string };

const HTTP_RE = /^https?:\/\/.+/;
const isUrl = (v: string) => HTTP_RE.test(v.trim());
const toNode = (n: NodeForm): WazuhNode => ({ url: n.url.trim(), name: n.name.trim() || null });
const fromNode = (n: WazuhNode): NodeForm => ({ url: n.url, name: n.name ?? "" });

export default function WazuhEcosystemPage() {
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [configured, setConfigured] = useState(false);
  const [validatedAt, setValidatedAt] = useState<string | null>(null);

  const [kind, setKind] = useState<Kind>("single");

  // Single-host
  const [indexerUrl, setIndexerUrl] = useState("");
  const [managerUrl, setManagerUrl] = useState("");
  const [dashboardUrl, setDashboardUrl] = useState("");

  // Distributed
  const [indexerNodes, setIndexerNodes] = useState<NodeForm[]>([{ url: "", name: "" }]);
  const [masterUrl, setMasterUrl] = useState("");
  const [masterName, setMasterName] = useState("");
  const [workers, setWorkers] = useState<NodeForm[]>([]);
  const [dashboards, setDashboards] = useState<NodeForm[]>([{ url: "", name: "" }]);

  // Shared credentials
  const [indexerUser, setIndexerUser] = useState("");
  const [indexerPassword, setIndexerPassword] = useState("");
  const [managerApiUser, setManagerApiUser] = useState("");
  const [managerApiPassword, setManagerApiPassword] = useState("");
  const [verifyTls, setVerifyTls] = useState(true);

  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [probeResults, setProbeResults] = useState<WazuhProbeResult[] | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [savedOk, setSavedOk] = useState(false);

  const load = useCallback(() => {
    fetchWazuhTopology()
      .then((t) => {
        setConfigured(t.configured);
        setValidatedAt(t.validated_at);
        if (t.verify_tls !== null) setVerifyTls(t.verify_tls);
        if (t.indexer_admin_user) setIndexerUser(t.indexer_admin_user);
        if (t.manager_api_user) setManagerApiUser(t.manager_api_user);
        const shape = t.topology;
        if (shape) {
          setKind(shape.kind);
          if (shape.kind === "single") {
            setIndexerUrl(shape.indexer_url);
            setManagerUrl(shape.manager_url);
            setDashboardUrl(shape.dashboard_url);
          } else {
            setIndexerNodes(
              shape.indexer_nodes.length
                ? shape.indexer_nodes.map(fromNode)
                : [{ url: "", name: "" }],
            );
            setMasterUrl(shape.manager_master.url);
            setMasterName(shape.manager_master.name ?? "");
            setWorkers(shape.manager_workers.map(fromNode));
            setDashboards(
              shape.dashboards.length
                ? shape.dashboards.map(fromNode)
                : [{ url: "", name: "" }],
            );
          }
        }
      })
      .catch((e) =>
        setLoadError(e instanceof ApiError ? e.message : "Failed to load topology"),
      )
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  function buildShape(): WazuhTopologyShape {
    if (kind === "single") {
      return {
        kind: "single",
        indexer_url: indexerUrl.trim(),
        manager_url: managerUrl.trim(),
        dashboard_url: dashboardUrl.trim(),
      };
    }
    return {
      kind: "distributed",
      indexer_nodes: indexerNodes.map(toNode),
      manager_master: { url: masterUrl.trim(), name: masterName.trim() || null },
      manager_workers: workers.filter((w) => w.url.trim()).map(toNode),
      dashboards: dashboards.filter((d) => d.url.trim()).map(toNode),
    };
  }

  /** Client-side validation mirroring the backend (guided messages). */
  function validate(): string | null {
    if (!indexerUser.trim()) return "Indexer admin username is required.";
    if (!managerApiUser.trim()) return "Manager API username is required.";
    if (!configured) {
      if (!indexerPassword) return "Indexer admin password is required on first save.";
      if (!managerApiPassword) return "Manager API password is required on first save.";
    }
    if (kind === "single") {
      if (!isUrl(indexerUrl)) return "Indexer URL must start with http:// or https://.";
      if (!isUrl(managerUrl)) return "Manager URL must start with http:// or https://.";
      if (!isUrl(dashboardUrl)) return "Dashboard URL must start with http:// or https://.";
    } else {
      if (indexerNodes.length === 0) return "Add at least one indexer node.";
      for (const n of indexerNodes) {
        if (!isUrl(n.url)) return "Each indexer node needs a valid http(s) URL.";
      }
      if (!isUrl(masterUrl)) return "Manager master URL must start with http:// or https://.";
      for (const w of workers) {
        if (w.url.trim() && !isUrl(w.url)) return "Each worker URL must be valid http(s).";
      }
      const dash = dashboards.filter((d) => d.url.trim());
      if (dash.length === 0) return "Add at least one dashboard.";
      for (const d of dash) {
        if (!isUrl(d.url)) return "Each dashboard URL must be valid http(s).";
      }
    }
    return null;
  }

  async function onSave() {
    const err = validate();
    if (err) {
      setFormError(err);
      return;
    }
    setSaving(true);
    setFormError(null);
    setSavedOk(false);
    setProbeResults(null);
    setWarnings([]);
    const body: WazuhTopologyUpdate = {
      topology: buildShape(),
      indexer_admin_user: indexerUser.trim(),
      indexer_admin_password: indexerPassword ? indexerPassword : null,
      manager_api_user: managerApiUser.trim(),
      manager_api_password: managerApiPassword ? managerApiPassword : null,
      verify_tls: verifyTls,
    };
    try {
      const res = await saveWazuhTopology(body);
      setConfigured(true);
      setValidatedAt(res.validated_at);
      setProbeResults(res.probe_results);
      setWarnings(res.warnings);
      setSavedOk(true);
      setIndexerPassword("");
      setManagerApiPassword("");
    } catch (e) {
      setFormError(e instanceof ApiError ? e.message : "Failed to save topology.");
    } finally {
      setSaving(false);
    }
  }

  const pwPlaceholder = configured ? "•••••••• (unchanged)" : "";

  if (loading) {
    return <div className="text-sm text-muted-foreground">Loading…</div>;
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="flex items-center gap-2 text-xl font-semibold">
          <Network className="h-5 w-5" />
          Wazuh ecosystem
        </h1>
        <p className="text-sm text-muted-foreground">
          Configure where the install&apos;s Wazuh indexer(s), manager(s), and
          dashboard(s) live. Per-organization credentials that query this
          ecosystem are set on each organization&apos;s page.
        </p>
        {configured ? (
          <p className="mt-1 text-xs text-muted-foreground">
            {validatedAt ? (
              <span title={absoluteTimeTitle(validatedAt)}>
                Last verified {relativeTime(validatedAt)}.
              </span>
            ) : (
              "Saved, but the last probe did not pass."
            )}
          </p>
        ) : null}
      </div>

      {loadError ? (
        <Alert variant="destructive">
          <AlertTitle>Error</AlertTitle>
          <AlertDescription>{loadError}</AlertDescription>
        </Alert>
      ) : null}

      {/* Deployment shape */}
      <div className="flex items-center gap-2">
        <Button
          variant={kind === "single" ? "default" : "outline"}
          size="sm"
          onClick={() => setKind("single")}
        >
          Single host
        </Button>
        <Button
          variant={kind === "distributed" ? "default" : "outline"}
          size="sm"
          onClick={() => setKind("distributed")}
        >
          Distributed
        </Button>
      </div>

      {/* Endpoints */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Endpoints</CardTitle>
          <CardDescription>
            {kind === "single"
              ? "All components on one host."
              : "Indexer cluster + manager master/workers + one or more dashboards. Names are optional labels."}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          {kind === "single" ? (
            <>
              <Field id="indexer-url" label="Indexer URL" value={indexerUrl}
                onChange={setIndexerUrl} placeholder="https://wazuh.example:9200" />
              <Field id="manager-url" label="Manager URL" value={managerUrl}
                onChange={setManagerUrl} placeholder="https://wazuh.example:55000" />
              <Field id="dashboard-url" label="Dashboard URL" value={dashboardUrl}
                onChange={setDashboardUrl} placeholder="https://wazuh.example" />
            </>
          ) : (
            <>
              <NodeList
                label="Indexer nodes"
                nodes={indexerNodes}
                setNodes={setIndexerNodes}
                urlPlaceholder="https://idx-1:9200"
                namePlaceholder="Indexer name (optional)"
                addLabel="Add indexer node"
                minOne
              />
              <div className="space-y-1.5">
                <Label>Manager master</Label>
                <div className="flex items-center gap-2">
                  <Input value={masterUrl} onChange={(e) => setMasterUrl(e.target.value)}
                    placeholder="https://master:55000" className="font-mono" autoComplete="off" />
                  <Input value={masterName} onChange={(e) => setMasterName(e.target.value)}
                    placeholder="Master node name (optional)" className="max-w-[14rem]"
                    autoComplete="off" />
                </div>
              </div>
              <NodeList
                label="Manager workers (optional)"
                nodes={workers}
                setNodes={setWorkers}
                urlPlaceholder="https://worker:55000"
                namePlaceholder="Worker node name (optional)"
                addLabel="Add worker"
                hint="A worker that fails the probe is a warning, not a blocker."
              />
              <NodeList
                label="Dashboards"
                nodes={dashboards}
                setNodes={setDashboards}
                urlPlaceholder="https://dashboard:443"
                namePlaceholder="Dashboard name (optional)"
                addLabel="Add dashboard"
                minOne
              />
            </>
          )}
        </CardContent>
      </Card>

      {/* Credentials */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Credentials</CardTitle>
          <CardDescription>
            Indexer admin + Manager API users. Passwords are stored in the
            secrets backend — leave blank to keep the current value.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-2">
          <Field id="indexer-user" label="Indexer admin user" value={indexerUser}
            onChange={setIndexerUser} placeholder="admin" />
          <Field id="indexer-pw" label="Indexer admin password" value={indexerPassword}
            onChange={setIndexerPassword} placeholder={pwPlaceholder} type="password" />
          <Field id="manager-user" label="Manager API user" value={managerApiUser}
            onChange={setManagerApiUser} placeholder="wazuh-wui" />
          <Field id="manager-pw" label="Manager API password" value={managerApiPassword}
            onChange={setManagerApiPassword} placeholder={pwPlaceholder} type="password" />
          <label className="flex items-center gap-2 text-sm sm:col-span-2">
            <input
              type="checkbox"
              checked={verifyTls}
              onChange={(e) => setVerifyTls(e.target.checked)}
              className="h-4 w-4 rounded border-foreground/30"
            />
            Verify TLS certificates (uncheck only for self-signed Wazuh certs)
          </label>
        </CardContent>
      </Card>

      {formError ? (
        <Alert variant="destructive">
          <AlertTitle>Not saved</AlertTitle>
          <AlertDescription>{formError}</AlertDescription>
        </Alert>
      ) : null}

      {savedOk ? (
        <Alert>
          <AlertTitle className="flex items-center gap-2">
            <CheckCircle2 className="h-4 w-4 text-emerald-500" />
            Saved
          </AlertTitle>
          <AlertDescription>
            All required endpoints passed the connection probe.
            {warnings.length > 0 ? (
              <ul className="mt-2 list-disc pl-5 text-amber-600 dark:text-amber-400">
                {warnings.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            ) : null}
          </AlertDescription>
        </Alert>
      ) : null}

      {probeResults && probeResults.length > 0 ? (
        <div className="space-y-1 rounded-xl ring-1 ring-foreground/10 p-3 text-sm">
          {probeResults.map((p, i) => (
            <div key={i} className="flex items-center gap-2">
              {p.ok ? (
                <CheckCircle2 className="h-4 w-4 text-emerald-500" />
              ) : (
                <XCircle className="h-4 w-4 text-destructive" />
              )}
              <Badge variant="outline" className="font-mono">
                {p.role}
              </Badge>
              <span className="text-muted-foreground">{p.detail}</span>
            </div>
          ))}
        </div>
      ) : null}

      <div className="flex justify-end">
        <Button onClick={onSave} disabled={saving}>
          <Save className="h-4 w-4" />
          {saving ? "Testing & saving…" : "Test & save"}
        </Button>
      </div>
    </div>
  );
}

/** A dynamic list of {url, name} rows with add/remove. */
function NodeList({
  label,
  nodes,
  setNodes,
  urlPlaceholder,
  namePlaceholder,
  addLabel,
  hint,
  minOne = false,
}: {
  label: string;
  nodes: NodeForm[];
  setNodes: React.Dispatch<React.SetStateAction<NodeForm[]>>;
  urlPlaceholder: string;
  namePlaceholder: string;
  addLabel: string;
  hint?: string;
  minOne?: boolean;
}) {
  return (
    <div className="space-y-2">
      <Label>{label}</Label>
      {nodes.map((node, i) => (
        <div key={i} className="flex items-center gap-2">
          <Input
            value={node.url}
            onChange={(e) =>
              setNodes((ns) => ns.map((n, j) => (j === i ? { ...n, url: e.target.value } : n)))
            }
            placeholder={urlPlaceholder}
            className="font-mono"
            autoComplete="off"
          />
          <Input
            value={node.name}
            onChange={(e) =>
              setNodes((ns) => ns.map((n, j) => (j === i ? { ...n, name: e.target.value } : n)))
            }
            placeholder={namePlaceholder}
            className="max-w-[14rem]"
            autoComplete="off"
          />
          <Button
            variant="ghost"
            size="icon"
            onClick={() =>
              setNodes((ns) =>
                minOne && ns.length <= 1 ? ns : ns.filter((_, j) => j !== i),
              )
            }
            disabled={minOne && nodes.length <= 1}
            title="Remove"
          >
            <Trash2 className="h-4 w-4" />
          </Button>
        </div>
      ))}
      <Button
        variant="outline"
        size="sm"
        onClick={() => setNodes((ns) => [...ns, { url: "", name: "" }])}
      >
        <Plus className="h-4 w-4" />
        {addLabel}
      </Button>
      {hint ? <p className="text-xs text-muted-foreground">{hint}</p> : null}
    </div>
  );
}

function Field({
  id,
  label,
  value,
  onChange,
  placeholder,
  type = "text",
}: {
  id: string;
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  type?: string;
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className={type === "text" ? "font-mono" : undefined}
        autoComplete="off"
      />
    </div>
  );
}
