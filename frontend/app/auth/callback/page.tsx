"use client";

import { api, setActiveOrgId } from "@/lib/api";
import { createClient } from "@/lib/supabase";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

export default function AuthCallbackPage() {
  const router = useRouter();
  const started = useRef(false);
  const [message, setMessage] = useState("Verifying your email link...");
  const [error, setError] = useState("");

  useEffect(() => {
    if (started.current) return;
    started.current = true;

    async function finishAuthentication() {
      try {
        const supabase = createClient();
        const callbackParams = new URLSearchParams(window.location.search);
        const authCode = callbackParams.get("code");
        const { data: { session }, error: sessionError } = authCode
          ? await supabase.auth.exchangeCodeForSession(authCode)
          : await supabase.auth.getSession();
        if (sessionError) throw sessionError;
        if (!session) throw new Error("This link is invalid or has expired. Ask an administrator to send a new invitation.");

        const invitationToken = callbackParams.get("token");
        if (callbackParams.get("invite") === "1" || invitationToken) {
          window.history.replaceState({}, "", "/auth/callback");
          setMessage("Accepting your organization invitation...");
          let result: Awaited<ReturnType<typeof api.orgs.acceptInvitations>>;
          try {
            result = await api.orgs.acceptInvitations(invitationToken ?? undefined);
          } catch (acceptError) {
            if (acceptError instanceof Error && acceptError.message.startsWith("API ")) {
              const responseText = acceptError.message.slice(acceptError.message.indexOf(":") + 1).trim();
              let detail = "";
              try {
                const response = JSON.parse(responseText);
                if (typeof response.detail === "string") detail = response.detail;
              } catch {
                detail = "";
              }
              throw new Error(
                detail ||
                  "The organization invitation could not be accepted. Sign in with the invited email address or ask an organization admin for a new invitation."
              );
            }
            throw acceptError;
          }
          setActiveOrgId(result.items[0]?.org_id ?? null);
        }

        router.replace("/");
        router.refresh();
      } catch (callbackError) {
        setError(callbackError instanceof Error ? callbackError.message : "Unable to complete sign-in.");
      }
    }

    void finishAuthentication();
  }, [router]);

  return (
    <main className="flex min-h-screen items-center justify-center bg-[#0e1117] px-6">
      <div className="w-full max-w-md rounded-2xl border border-white/10 bg-[#1a1d2e]/80 p-8 text-center">
        <h1 className="text-xl font-semibold text-white">Account verification</h1>
        {error ? (
          <>
            <p role="alert" className="mt-4 text-sm text-red-400">{error}</p>
            <a href="/login" className="mt-6 inline-block text-sm text-indigo-300 hover:text-indigo-200">
              Return to sign in
            </a>
          </>
        ) : (
          <p role="status" className="mt-4 text-sm text-slate-400">{message}</p>
        )}
      </div>
    </main>
  );
}
