"use client";

import { LogOut, ShieldCheck } from "lucide-react";

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

export function ChatHeader() {
  const { me, signOut } = useAuth();

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
            <Button variant="outline" size="sm" className="h-9">
              {me?.user_id?.slice(0, 8) ?? "account"}
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuLabel>
              Role: {me?.role ?? "—"}
            </DropdownMenuLabel>
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
