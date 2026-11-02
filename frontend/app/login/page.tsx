"use client";

import { api, setActiveOrgId } from "@/lib/api";
import { createClient } from "@/lib/supabase";
import { Target } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

export default function LoginPage() {
  const router = useRouter();
  const supabase = createClient();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [mode, setMode] = useState<"login" | "signup">("login");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [inviteToken, setInviteToken] = useState("");
  const [inviteOrganization, setInviteOrganization] = useState("");
  const [inviteAccountExists, setInviteAccountExists] = useState(false);
  const [inviteLoading, setInviteLoading] = useState(false);

  useEffect(() => {
    const urlToken = new URLSearchParams(window.location.search).get("invite");
    if (urlToken) {
      window.sessionStorage.setItem("pending_org_invitation_token", urlToken);
    }
    const invitationToken =
      urlToken ?? window.sessionStorage.getItem("pending_org_invitation_token");
    if (!invitationToken) return;
    const resolvedInvitationToken = invitationToken;
    window.history.replaceState({}, "", "/login");

    async function loadInvitation() {
      setInviteToken(resolvedInvitationToken);
      setMode("login");
      setInviteLoading(true);
      try {
        const invitation = await api.orgs.lookupInvitation(resolvedInvitationToken);
        setEmail(invitation.email);
        setInviteOrganization(invitation.organization_name);
        setInviteAccountExists(invitation.account_exists);
        setMode(invitation.account_exists ? "login" : "signup");
        const { data: { session }, error: sessionError } = await createClient().auth.getSession();
        if (sessionError) throw sessionError;
        if (session?.user.email?.toLowerCase() === invitation.email.toLowerCase()) {
          const result = await api.orgs.acceptInvitations(resolvedInvitationToken);
          window.sessionStorage.removeItem("pending_org_invitation_token");
          setActiveOrgId(result.items[0]?.org_id ?? null);
          router.replace("/");
          router.refresh();
        }
      } catch (lookupError) {
        window.sessionStorage.removeItem("pending_org_invitation_token");
        setError((lookupError as Error).message);
      } finally {
        setInviteLoading(false);
      }
    }

    void Promise.resolve().then(loadInvitation);
  }, [router]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError("");
    setMessage("");

    try {
      if (mode === "login") {
        const { error } = await supabase.auth.signInWithPassword({ email, password });
        if (error) throw error;
        if (inviteToken) {
          const result = await api.orgs.acceptInvitations(inviteToken);
          window.sessionStorage.removeItem("pending_org_invitation_token");
          setActiveOrgId(result.items[0]?.org_id ?? null);
        }
        router.push("/");
        router.refresh();
      } else {
        if (inviteToken) {
          await api.orgs.createInvitedAccount(inviteToken, password);
          const { error } = await supabase.auth.signInWithPassword({ email, password });
          if (error) {
            throw new Error(
              "Your account was created, but sign-in did not complete. Choose Sign in and try again."
            );
          }
          const result = await api.orgs.acceptInvitations(inviteToken);
          window.sessionStorage.removeItem("pending_org_invitation_token");
          setActiveOrgId(result.items[0]?.org_id ?? null);
          router.push("/");
          router.refresh();
        } else {
          const { error } = await supabase.auth.signUp({ email, password });
          if (error) throw error;
          setMessage("Check your email for a confirmation link.");
        }
      }
    } catch (submitError) {
      setError((submitError as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-[#0e1117]">
      <div className="w-full max-w-md px-8 py-10 rounded-2xl border border-white/10 bg-[#1a1d2e]/80 backdrop-blur shadow-2xl">

        {/* Logo / wordmark */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center gap-2 mb-3">
            <Target size={30} className="text-indigo-400" />
            <span className="text-2xl font-bold bg-gradient-to-r from-indigo-400 to-purple-400 bg-clip-text text-transparent">
              Missa
            </span>
          </div>
          <p className="text-slate-400 text-sm">AI-powered lead generation platform</p>
        </div>

        {/* Tab toggle */}
        <div className="flex bg-white/5 rounded-lg p-1 mb-6">
          {(["login", "signup"] as const).map((m) => (
            <button
              key={m}
              onClick={() => { setMode(m); setError(""); setMessage(""); }}
              className={`flex-1 py-2 text-sm font-medium rounded-md transition-all ${
                mode === m
                  ? "bg-indigo-600 text-white shadow"
                  : "text-slate-400 hover:text-white"
              }`}
            >
              {m === "login" ? "Sign in" : "Create account"}
            </button>
          ))}
        </div>

        {inviteToken && (
          <p role="status" className="mb-5 rounded-lg border border-indigo-400/20 bg-indigo-400/10 px-3 py-2 text-sm text-indigo-200">
            {inviteLoading
              ? "Verifying invitation..."
              : inviteOrganization
                ? inviteAccountExists
                  ? `You are invited to join ${inviteOrganization}. Sign in with the invited email address to accept.`
                  : `You are invited to join ${inviteOrganization}. Create your account to accept; this invitation verifies your email.`
                : "Complete sign in or account creation to accept this invitation."}
          </p>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm text-slate-400 mb-1.5">Email</label>
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              readOnly={Boolean(inviteToken)}
              placeholder="you@example.com"
              className="w-full bg-white/5 border border-white/10 rounded-lg px-4 py-2.5 text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/50 focus:border-indigo-500 transition"
            />
          </div>

          <div>
            <label className="block text-sm text-slate-400 mb-1.5">Password</label>
            <input
              type="password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
              className="w-full bg-white/5 border border-white/10 rounded-lg px-4 py-2.5 text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/50 focus:border-indigo-500 transition"
            />
          </div>

          {error && (
            <p className="text-red-400 text-sm bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">
              {error}
            </p>
          )}
          {message && (
            <p className="text-green-400 text-sm bg-green-500/10 border border-green-500/20 rounded-lg px-3 py-2">
              {message}
            </p>
          )}

          <button
            type="submit"
            disabled={loading || inviteLoading || Boolean(inviteToken && error)}
            className="w-full py-2.5 bg-gradient-to-r from-indigo-600 to-purple-600 hover:from-indigo-500 hover:to-purple-500 disabled:opacity-50 text-white font-semibold rounded-lg transition-all shadow-lg shadow-indigo-500/20 mt-2"
          >
            {loading ? "Please wait…" : mode === "login" ? "Sign in" : "Create account"}
          </button>
        </form>
      </div>
    </div>
  );
}
