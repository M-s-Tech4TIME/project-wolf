"use client";

import { Cog, Settings as SettingsIcon, ShieldCheck, Sliders, UserCircle } from "lucide-react";

import { TenantSwitcher } from "@/components/tenant-switcher";
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
 * Top bar:
 *   left   — Wolf brand + tagline
 *   middle — Active conversation title (Slice 5.0c-g). Empty when no
 *            conversation is selected (the greeting screen is showing).
 *   right  — Tenant switcher · Settings gear (placeholder menu for the
 *            future User Settings + Wolf Configuration panels).
 *
 * The signed-in user's identity now lives in the sidebar footer (see
 * `ChatSidebar`) so the header's right side can be reserved for org-wide
 * controls — tenant choice and the configuration surface that will grow
 * as Wolf gains operator-tunable knobs.
 */
export function ChatHeader({ title }: { title?: string | null }) {
  return (
    <header className="relative flex h-14 items-center justify-between border-b border-border bg-card px-4">
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2 font-semibold tracking-tight">
          <ShieldCheck className="h-5 w-5 text-primary" />
          <span>Wolf</span>
        </div>
        <span className="hidden text-xs text-muted-foreground sm:inline">
          Agentic AI for Wazuh
        </span>
      </div>
      {/* Centered title — absolute-positioned so it lines up with the
          window centre regardless of left/right cluster widths. Truncates
          past ~50% of the bar width so it never collides with the
          tenant switcher on narrow screens. */}
      {title ? (
        <div
          className="pointer-events-none absolute left-1/2 top-1/2 hidden -translate-x-1/2 -translate-y-1/2 max-w-[40%] truncate text-sm font-medium md:block"
          title={title}
        >
          {title}
        </div>
      ) : null}
      <div className="flex items-center gap-2">
        <TenantSwitcher />
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost"
              size="sm"
              className="h-10 w-10 rounded-full p-0"
              aria-label="Settings"
              title="Settings"
            >
              <Cog className="!h-6 !w-6" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-56">
            <DropdownMenuLabel className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
              Settings
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            {/* Placeholder items — actual surfaces ship as a later slice */}
            <DropdownMenuItem disabled className="text-sm">
              <UserCircle className="mr-2 h-4 w-4" />
              <span>User Settings</span>
              <span className="ml-auto text-[10px] text-muted-foreground">
                soon
              </span>
            </DropdownMenuItem>
            <DropdownMenuItem disabled className="text-sm">
              <Sliders className="mr-2 h-4 w-4" />
              <span>Wolf Configuration</span>
              <span className="ml-auto text-[10px] text-muted-foreground">
                soon
              </span>
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem disabled className="text-[10px] text-muted-foreground">
              <SettingsIcon className="mr-2 h-3.5 w-3.5" />
              More settings coming as Wolf grows.
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
}
