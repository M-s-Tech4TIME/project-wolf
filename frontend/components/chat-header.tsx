"use client";

import { Building2, LogOut, Mail, ShieldCheck, UserCircle } from "lucide-react";

import { TenantSwitcher } from "@/components/tenant-switcher";
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

/**
 * Two-letter initials for the avatar bubble — first letter of the first
 * two words of display_name, falling back to the first two letters of
 * the email local-part, then a generic fallback.
 */
function initialsOf(displayName?: string, email?: string): string {
  const trimmed = (displayName ?? "").trim();
  if (trimmed) {
    const parts = trimmed.split(/\s+/);
    const first = parts[0]?.[0] ?? "";
    const second = parts[1]?.[0] ?? parts[0]?.[1] ?? "";
    return (first + second).toUpperCase() || "U";
  }
  const local = (email ?? "").split("@")[0];
  return (local.slice(0, 2) || "U").toUpperCase();
}

export function ChatHeader() {
  const { me, tenants, signOut } = useAuth();
  const initials = initialsOf(me?.display_name, me?.email);
  // The user's current tenant is whichever membership matches me.tenant_id.
  const currentTenant = tenants.find((t) => t.id === me?.tenant_id);

  return (
    <header className="flex h-14 items-center justify-between border-b border-border bg-card px-4">
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2 font-semibold tracking-tight">
          <ShieldCheck className="h-5 w-5 text-primary" />
          <span>Wolf</span>
        </div>
        <span className="text-xs text-muted-foreground">
          Agentic AI for Wazuh
        </span>
      </div>
      <div className="flex items-center gap-3">
        <TenantSwitcher />
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost"
              size="sm"
              className="h-9 w-9 rounded-full p-0"
              aria-label="Account menu"
            >
              <span className="flex h-8 w-8 items-center justify-center rounded-full bg-primary/15 text-xs font-semibold text-primary">
                {initials}
              </span>
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-64">
            <DropdownMenuLabel className="flex items-center gap-2 py-2">
              <span className="flex h-9 w-9 items-center justify-center rounded-full bg-primary/15 text-xs font-semibold text-primary">
                {initials}
              </span>
              <span className="flex flex-col">
                <span className="text-sm">
                  {me?.display_name?.trim() || "Signed in"}
                </span>
                <span className="text-[10px] font-normal text-muted-foreground">
                  Role: {me?.role ?? "—"}
                </span>
              </span>
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem disabled className="text-xs">
              <Mail className="mr-2 h-3.5 w-3.5" />
              <span className="truncate">{me?.email ?? "—"}</span>
            </DropdownMenuItem>
            <DropdownMenuItem disabled className="text-xs">
              <Building2 className="mr-2 h-3.5 w-3.5" />
              <span className="truncate">
                {currentTenant?.name ?? currentTenant?.slug ?? "—"}
              </span>
            </DropdownMenuItem>
            <DropdownMenuItem disabled className="text-xs">
              <UserCircle className="mr-2 h-3.5 w-3.5" />
              <span className="truncate font-mono">
                {me?.user_id?.slice(0, 8) ?? "—"}
              </span>
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={() => void signOut()}>
              <LogOut className="mr-2 h-4 w-4" />
              Sign out
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
}
