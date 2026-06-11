"use client";

import { Building2, Check, ChevronsUpDown } from "lucide-react";
import { useRouter } from "next/navigation";

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
import { logout } from "@/lib/api";

/**
 * Organization switcher.  The JWT cookie pins organization at login time, so switching
 * organizations requires a re-login.  Selecting a different organization signs out,
 * then sends the user to /login with the desired organization prefilled.
 */
export function OrganizationSwitcher() {
  const { me, organizations, refresh } = useAuth();
  const router = useRouter();
  const current = organizations.find((t) => t.id === me?.organization_id);

  const handleSwitch = async (organizationId: string) => {
    if (organizationId === me?.organization_id) return;
    await logout();
    await refresh();
    router.push(`/login?organization=${organizationId}`);
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
                {t.id === me?.organization_id ? (
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
