"use client";

import { Building2, Check, ChevronsUpDown } from "lucide-react";

import { useAuth } from "@/components/auth-provider";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { switchOrganization } from "@/lib/api";

/**
 * Organization switcher — Phase 6.5-c-ii (ADR 0018 Round 3).
 *
 * Switching is per-tab state, not a re-login: the auth cookie identifies
 * the user and stays untouched; this tab's X-Organization-Id header simply
 * changes. Other tabs keep their own active org. The switch is recorded in
 * the audit trail via the optional switch-organization endpoint.
 */
export function OrganizationSwitcher() {
  const { organizations, activeOrganizationId, setActiveOrganization, refresh } = useAuth();
  const current = organizations.find((t) => t.id === activeOrganizationId);

  const handleSwitch = async (organizationId: string) => {
    if (organizationId === activeOrganizationId) return;
    setActiveOrganization(organizationId);
    try {
      // Audit-only (ADR 0018): a failure here must never block the switch.
      await switchOrganization(organizationId);
    } catch (err) {
      console.warn("switch-organization audit call failed", err);
    }
    // Re-fetch /me (and memberships) under the new header so org-scoped
    // UI (profile chip, sidebar) reflects this tab's new context.
    await refresh();
  };

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="sm" className="h-9 min-w-[180px] justify-between">
          <span className="flex items-center gap-2 truncate">
            <Building2 className="h-4 w-4" />
            <span className="truncate">
              {current?.name ?? "Select organization"}
            </span>
          </span>
          <ChevronsUpDown className="h-4 w-4 opacity-60" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-72">
        <DropdownMenuLabel>Your organizations</DropdownMenuLabel>
        <DropdownMenuSeparator />
        {organizations.length === 0 ? (
          <DropdownMenuItem disabled>(no memberships)</DropdownMenuItem>
        ) : (
          organizations.map((t) => (
            <DropdownMenuItem
              key={t.id}
              onSelect={() => void handleSwitch(t.id)}
              className="flex flex-col items-start gap-0.5"
            >
              <div className="flex w-full items-center justify-between">
                <span className="font-medium">{t.name}</span>
                {t.id === activeOrganizationId ? (
                  <Check className="h-4 w-4 text-primary" />
                ) : null}
              </div>
              <div className="text-xs text-muted-foreground">
                {t.slug} · {t.role}
              </div>
            </DropdownMenuItem>
          ))
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
