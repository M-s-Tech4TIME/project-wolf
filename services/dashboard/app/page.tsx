"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { useAuth } from "@/components/auth-provider";

export default function HomePage() {
  const router = useRouter();
  const { isLoading, me } = useAuth();

  useEffect(() => {
    if (isLoading) return;
    if (!me) {
      router.replace("/login");
    } else if (me.role === "superuser") {
      // Install-admin sessions land on their own surface (6.5-c-ii).
      router.replace("/superuser/dashboard");
    } else {
      router.replace("/chat");
    }
  }, [isLoading, me, router]);

  return (
    <div className="flex h-screen items-center justify-center text-muted-foreground">
      Loading…
    </div>
  );
}
