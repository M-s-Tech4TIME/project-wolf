import { Suspense } from "react";

import { LoginForm } from "@/components/login-form";

export default function LoginPage() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-muted/30 px-4">
      <div className="w-full max-w-md">
        <div className="mb-8 text-center">
          <h1 className="text-3xl font-semibold tracking-tight">Wolf</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Agentic AI for Wazuh security analysts
          </p>
        </div>
        <Suspense fallback={<div className="text-center text-sm text-muted-foreground">Loading…</div>}>
          <LoginForm />
        </Suspense>
      </div>
    </main>
  );
}
