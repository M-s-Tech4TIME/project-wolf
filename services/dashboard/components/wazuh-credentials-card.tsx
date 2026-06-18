"use client";

// Per-org Wazuh credentials — Phase 6.6-d (ADR 0020).
//
// Superuser-only. The credentials an organization uses to query the install's
// Wazuh ecosystem (whose URLs come from the install topology, 6.6-a/b). Save
// is SOFT-fail: it succeeds even when the probe fails, so the Superuser can
// save before the Wazuh-side user is provisioned. Passwords are write-only —
// usernames are shown, password fields are blank ("keep existing"). Requires a
// configured install topology (the backend 409s otherwise → we link to it).

import { CheckCircle2, History, KeyRound, XCircle } from "lucide-react";
import Link from "next/link";
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
import {
  ApiError,
  fetchOrgWazuhCredentials,
  fetchOrgWazuhCredentialHistory,
  saveOrgWazuhCredentials,
} from "@/lib/api";
import { absoluteTimeTitle, relativeTime } from "@/lib/format";
import type {
  WazuhCredentialHistoryEntry,
  WazuhCredentialsSaveResponse,
  WazuhCredentialsUpdate,
} from "@/lib/types";

export function WazuhCredentialsCard({
  orgId,
  orgActive,
}: {
  orgId: string;
  orgActive: boolean;
}) {
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [configured, setConfigured] = useState(false);
  const [validatedAt, setValidatedAt] = useState<string | null>(null);

  const [indexerUser, setIndexerUser] = useState("");
  const [indexerPassword, setIndexerPassword] = useState("");
  const [serverUser, setServerUser] = useState("");
  const [serverPassword, setServerPassword] = useState("");
  // The usernames last loaded from the server — a change to either with a blank
  // password is rejected (a password belongs to a specific user).
  const [loadedIndexerUser, setLoadedIndexerUser] = useState("");
  const [loadedServerUser, setLoadedServerUser] = useState("");
  const [indexFilter, setIndexFilter] = useState("wazuh-alerts-*");
  const [groupLabels, setGroupLabels] = useState(""); // comma-separated
  const [injectFilter, setInjectFilter] = useState(false);

  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [needsTopology, setNeedsTopology] = useState(false);
  const [result, setResult] = useState<WazuhCredentialsSaveResponse | null>(null);
  const [history, setHistory] = useState<WazuhCredentialHistoryEntry[]>([]);

  const loadHistory = useCallback(() => {
    fetchOrgWazuhCredentialHistory(orgId)
      .then(setHistory)
      .catch(() => setHistory([]));
  }, [orgId]);

  const load = useCallback(() => {
    fetchOrgWazuhCredentials(orgId)
      .then((c) => {
        setConfigured(c.configured);
        setValidatedAt(c.validated_at);
        if (c.indexer_user) {
          setIndexerUser(c.indexer_user);
          setLoadedIndexerUser(c.indexer_user);
        }
        if (c.server_api_user) {
          setServerUser(c.server_api_user);
          setLoadedServerUser(c.server_api_user);
        }
        if (c.wazuh_index_filter) setIndexFilter(c.wazuh_index_filter);
        if (c.agent_group_labels) setGroupLabels(c.agent_group_labels.join(", "));
        if (c.inject_group_label_filter !== null)
          setInjectFilter(c.inject_group_label_filter);
      })
      .catch((e) =>
        setLoadError(e instanceof ApiError ? e.message : "Failed to load credentials"),
      )
      .finally(() => setLoading(false));
  }, [orgId]);

  useEffect(() => {
    load();
    loadHistory();
  }, [load, loadHistory]);

  function parseLabels(): string[] | null {
    const labels = groupLabels
      .split(",")
      .map((g) => g.trim())
      .filter(Boolean);
    return labels.length ? labels : null;
  }

  function validate(): string | null {
    if (!indexerUser.trim()) return "Indexer username is required.";
    if (!serverUser.trim()) return "Server API username is required.";
    if (!indexFilter.trim()) return "Index filter is required (e.g. wazuh-alerts-*).";
    if (injectFilter && !parseLabels())
      return "Provide at least one agent group label, or uncheck the group-label filter.";
    if (!configured) {
      if (!indexerPassword) return "Indexer password is required on first save.";
      if (!serverPassword) return "Server API password is required on first save.";
    } else {
      // Changing a username requires its password (can't reuse the old user's).
      if (indexerUser.trim() !== loadedIndexerUser && !indexerPassword)
        return "Changing the indexer username requires its password.";
      if (serverUser.trim() !== loadedServerUser && !serverPassword)
        return "Changing the Server API username requires its password.";
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
    setNeedsTopology(false);
    setResult(null);
    const body: WazuhCredentialsUpdate = {
      indexer_user: indexerUser.trim(),
      indexer_password: indexerPassword ? indexerPassword : null,
      server_api_user: serverUser.trim(),
      server_api_password: serverPassword ? serverPassword : null,
      wazuh_index_filter: indexFilter.trim(),
      agent_group_labels: parseLabels(),
      inject_group_label_filter: injectFilter,
    };
    try {
      const res = await saveOrgWazuhCredentials(orgId, body);
      setResult(res);
      setConfigured(true);
      setValidatedAt(res.validated_at);
      // New baseline for the username-change-needs-password check.
      setLoadedIndexerUser(body.indexer_user);
      setLoadedServerUser(body.server_api_user);
      setIndexerPassword("");
      setServerPassword("");
      loadHistory();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setNeedsTopology(true);
      } else {
        setFormError(e instanceof ApiError ? e.message : "Failed to save credentials.");
      }
    } finally {
      setSaving(false);
    }
  }

  const pwPlaceholder = configured ? "•••••••• (unchanged)" : "";

  return (
    <Card className="px-5">
      <CardHeader className="px-0">
        <CardTitle className="flex items-center gap-2 text-base">
          <KeyRound className="h-5 w-5" />
          Wazuh credentials
        </CardTitle>
        <CardDescription>
          The credentials this organization uses to query the install&apos;s Wazuh
          ecosystem. Passwords are stored in the secrets backend — leave blank to
          keep the current value. Saving succeeds even if the test fails, so you
          can configure before the Wazuh-side user exists.
        </CardDescription>
      </CardHeader>
      <CardContent className="px-0">
        {loading ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : (
          <div className="space-y-4">
            {loadError ? (
              <Alert variant="destructive">
                <AlertTitle>Error</AlertTitle>
                <AlertDescription>{loadError}</AlertDescription>
              </Alert>
            ) : null}

            {configured ? (
              <p className="text-xs text-muted-foreground">
                {validatedAt ? (
                  <span title={absoluteTimeTitle(validatedAt)}>
                    Last verified {relativeTime(validatedAt)}.
                  </span>
                ) : (
                  "Saved, but the last connection test did not pass."
                )}
              </p>
            ) : null}

            <div className="grid gap-4 sm:grid-cols-2">
              <Field id="cred-idx-user" label="Indexer user" value={indexerUser}
                onChange={setIndexerUser} placeholder="wolf_ro" disabled={!orgActive} />
              <Field id="cred-idx-pw" label="Indexer password" value={indexerPassword}
                onChange={setIndexerPassword} placeholder={pwPlaceholder} type="password"
                disabled={!orgActive} />
              <Field id="cred-api-user" label="Server API user" value={serverUser}
                onChange={setServerUser} placeholder="wazuh-wui" disabled={!orgActive} />
              <Field id="cred-api-pw" label="Server API password" value={serverPassword}
                onChange={setServerPassword} placeholder={pwPlaceholder} type="password"
                disabled={!orgActive} />
              <Field id="cred-index" label="Index pattern(s) (comma-separated)"
                value={indexFilter} onChange={setIndexFilter}
                placeholder="wazuh-alerts-*, wazuh-archives-*" disabled={!orgActive} />
              <Field id="cred-groups" label="Agent group label(s) (comma-separated)"
                value={groupLabels} onChange={setGroupLabels} placeholder="acme"
                mono={false} disabled={!orgActive} />
            </div>

            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={injectFilter}
                onChange={(e) => setInjectFilter(e.target.checked)}
                disabled={!orgActive}
                className="h-4 w-4 rounded border-foreground/30"
              />
              Restrict indexer queries to these group label(s)
              <span className="text-xs text-muted-foreground">
                (adds agent.labels.group; only if this credential isn&apos;t already
                DLS-scoped in Wazuh)
              </span>
            </label>

            {needsTopology ? (
              <Alert variant="destructive">
                <AlertTitle>No Wazuh ecosystem configured</AlertTitle>
                <AlertDescription>
                  Configure the install&apos;s Wazuh ecosystem topology first, then
                  save this organization&apos;s credentials.{" "}
                  <Link href="/superuser/wazuh" className="underline">
                    Go to Wazuh ecosystem →
                  </Link>
                </AlertDescription>
              </Alert>
            ) : null}

            {formError ? <p className="text-sm text-destructive">{formError}</p> : null}

            {result ? (
              <Alert>
                <AlertTitle className="flex items-center gap-2">
                  {result.probe_ok ? (
                    <CheckCircle2 className="h-4 w-4 text-emerald-500" />
                  ) : (
                    <XCircle className="h-4 w-4 text-amber-500" />
                  )}
                  {result.probe_ok ? "Saved & verified" : "Saved (not yet verified)"}
                </AlertTitle>
                <AlertDescription className="space-y-1">
                  <div className="mt-1 space-y-1">
                    {result.probe_results.map((p, i) => (
                      <div key={i} className="flex items-center gap-2 text-sm">
                        {p.ok ? (
                          <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />
                        ) : (
                          <XCircle className="h-3.5 w-3.5 text-destructive" />
                        )}
                        <Badge variant="outline" className="font-mono">
                          {p.role}
                        </Badge>
                        <span className="text-muted-foreground">{p.detail}</span>
                      </div>
                    ))}
                  </div>
                  {result.index_results.length > 0 ? (
                    <div className="mt-1 space-y-1">
                      <p className="text-xs font-medium text-muted-foreground">
                        Index access
                      </p>
                      {result.index_results.map((ix) => (
                        <div key={ix.pattern} className="flex items-center gap-2 text-sm">
                          {ix.ok ? (
                            <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />
                          ) : (
                            <XCircle className="h-3.5 w-3.5 text-destructive" />
                          )}
                          <Badge variant="outline" className="font-mono">
                            {ix.pattern}
                          </Badge>
                          <span className="text-muted-foreground">{ix.detail}</span>
                        </div>
                      ))}
                    </div>
                  ) : null}
                  {result.scope_detail ? (
                    <p className="text-sm text-muted-foreground">{result.scope_detail}</p>
                  ) : null}
                  {result.groups && result.groups.length > 0 ? (
                    <div className="flex flex-wrap items-center gap-1 text-sm">
                      <span className="text-muted-foreground">Scoped to:</span>
                      {result.groups.map((g) => (
                        <Badge key={g} variant="outline" className="font-mono">
                          {g}
                        </Badge>
                      ))}
                    </div>
                  ) : null}
                  {result.warnings.length > 0 ? (
                    <ul className="list-disc pl-5 text-amber-600 dark:text-amber-400">
                      {result.warnings.map((w, i) => (
                        <li key={i}>{w}</li>
                      ))}
                    </ul>
                  ) : null}
                </AlertDescription>
              </Alert>
            ) : null}

            <Button onClick={onSave} disabled={saving || !orgActive}>
              {saving ? "Testing & saving…" : "Test & save credentials"}
            </Button>
            {!orgActive ? (
              <p className="text-sm text-muted-foreground">
                This organization is soft-deleted.
              </p>
            ) : null}

            {/* Rotation log */}
            {history.length > 0 ? (
              <div className="space-y-1 pt-2">
                <p className="flex items-center gap-2 text-sm font-medium">
                  <History className="h-4 w-4" />
                  Rotation log
                </p>
                <ul className="space-y-1 text-sm text-muted-foreground">
                  {history.map((h) => (
                    <li key={h.id} className="flex items-center gap-2">
                      {h.probe_ok ? (
                        <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />
                      ) : (
                        <XCircle className="h-3.5 w-3.5 text-amber-500" />
                      )}
                      <span title={absoluteTimeTitle(h.created_at)}>
                        {relativeTime(h.created_at)}
                      </span>
                      <span>
                        — {h.probe_ok ? "verified" : "saved (probe failed)"}
                        {h.index_filter ? `, filter ${h.index_filter}` : ""}
                        {h.agent_count !== null ? `, ${h.agent_count} agents` : ""}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function Field({
  id,
  label,
  value,
  onChange,
  placeholder,
  type = "text",
  mono = true,
  disabled = false,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  type?: string;
  mono?: boolean;
  disabled?: boolean;
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
        className={mono && type !== "password" ? "font-mono" : undefined}
        autoComplete="off"
        disabled={disabled}
      />
    </div>
  );
}
