"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { useAuth } from "@/components/auth-provider";

export default function HomePage() {
  const router = useRouter();
  const { isLoading, me } = useAuth();

  useEffect(() => {
    if (isLoading) return;
    router.replace(me ? "/chat" : "/login");
  }, [isLoading, me, router]);

  return (
    <div className="flex h-screen items-center justify-center text-muted-foreground">
      Loading…
    </div>
  );
}
