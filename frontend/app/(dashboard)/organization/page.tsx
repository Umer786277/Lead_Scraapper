"use client";

import { api, type OrganizationMember } from "@/lib/api";
import ConfirmDialog from "@/components/ConfirmDialog";
import { Building2, MailPlus, RefreshCw, Shield, UserRound, X } from "lucide-react";
import { useEffect, useState } from "react";

export default function OrganizationPage() {
  const [members, setMembers] = useState<OrganizationMember[]>([]);
  const [canManage, setCanManage] = useState(false);
  const [canManageRoles, setCanManageRoles] = useState(false);
  const [isSuperAdmin, setIsSuperAdmin] = useState(false);
  const [email, setEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<OrganizationMember["role"]>("user");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [memberToRemove, setMemberToRemove] = useState<OrganizationMember | null>(null);

  async function loadMembers() {
    setLoading(true);
    setError("");
    try {
      const result = await api.orgs.members.list();
      setMembers(result.items);
      setCanManage(result.can_manage);
      setCanManageRoles(result.can_manage_roles);
      setIsSuperAdmin(result.is_super_admin);
    } catch (loadError) {
      setError((loadError as Error).message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    let cancelled = false;
    async function loadInitialMembers() {
      try {
        const result = await api.orgs.members.list();
        if (cancelled) return;
        setMembers(result.items);
        setCanManage(result.can_manage);
        setCanManageRoles(result.can_manage_roles);
        setIsSuperAdmin(result.is_super_admin);
      } catch (loadError) {
        if (!cancelled) setError((loadError as Error).message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void loadInitialMembers();
    return () => {
      cancelled = true;
    };
  }, []);

  async function inviteMember(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const result = await api.orgs.members.invite(email.trim(), inviteRole);
      setEmail("");
      setInviteRole("user");
      setNotice(
        result.invitation_required
          ? `Invitation sent to ${result.email} as ${result.role}. They will get organization access after accepting the email link.`
          : `${result.email} was added to this organization.`
      );
      await loadMembers();
    } catch (inviteError) {
      setError((inviteError as Error).message);
    } finally {
      setSaving(false);
    }
  }

  async function changeRole(member: OrganizationMember, role: OrganizationMember["role"]) {
    setError("");
    setNotice("");
    try {
      await api.orgs.members.updateRole(member.user_id, role);
      await loadMembers();
    } catch (updateError) {
      setError((updateError as Error).message);
    }
  }

  function removeMember(member: OrganizationMember) {
    setError("");
    setNotice("");
    setMemberToRemove(member);
  }

  async function confirmRemoveMember() {
    if (!memberToRemove) return false;
    const member = memberToRemove;
    const identity = member.email ?? member.user_id;
    const pending = member.access_status === "pending";
    setError("");
    setNotice("");
    try {
      if (pending) await api.orgs.members.revokeInvitation(member.user_id);
      else await api.orgs.members.remove(member.user_id);
      setNotice(pending ? `Invitation for ${identity} was revoked.` : `${identity} was removed.`);
      await loadMembers();
      return true;
    } catch (removeError) {
      setError((removeError as Error).message);
      setMemberToRemove(null);
      return false;
    }
  }

  return (
    <div className="max-w-4xl space-y-6">
      <header>
        <h1 className="flex items-center gap-2 text-2xl font-bold text-white">
          <Building2 className="text-indigo-400" size={24} /> Organization members
        </h1>
        <p className="mt-1 text-sm text-slate-400">
          Invite teammates and manage access to the currently selected organization.
        </p>
        {isSuperAdmin && (
          <p className="mt-2 inline-flex rounded-full border border-purple-400/20 bg-purple-400/10 px-2.5 py-1 text-xs font-medium text-purple-300">
            Platform Super Admin
          </p>
        )}
      </header>

      {canManage && (
        <form
          onSubmit={inviteMember}
          className="flex flex-col gap-3 rounded-2xl border border-white/8 bg-white/3 p-5 sm:flex-row"
        >
          <label className="sr-only" htmlFor="member-email">Teammate email address</label>
          <input
            id="member-email"
            type="email"
            required
            maxLength={254}
            autoComplete="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="teammate@company.com"
            className="min-w-0 flex-1 rounded-lg border border-white/10 bg-[#151827] px-3 py-2 text-sm text-slate-100 outline-none focus:border-indigo-500"
          />
          <label className="sr-only" htmlFor="member-role">Organization role</label>
          <select
            id="member-role"
            value={inviteRole}
            onChange={(event) => setInviteRole(event.target.value as OrganizationMember["role"])}
            className="rounded-lg border border-white/10 bg-[#151827] px-3 py-2 text-sm text-slate-100 outline-none focus:border-indigo-500"
          >
            <option value="user">User</option>
            <option value="admin">Admin</option>
          </select>
          <button
            type="submit"
            disabled={saving || !email.trim()}
            className="flex items-center justify-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-indigo-500 disabled:opacity-50"
          >
            <MailPlus size={16} />
            {saving ? "Adding..." : "Add user"}
          </button>
        </form>
      )}

      {notice && <p role="status" className="text-sm text-emerald-400">{notice}</p>}
      {error && <p role="alert" className="text-sm text-red-400">{error}</p>}

      <section className="overflow-hidden rounded-2xl border border-white/8 bg-white/3">
        <div className="flex items-center justify-between border-b border-white/8 px-5 py-4">
          <div>
            <h2 className="font-semibold text-white">Members</h2>
            <p className="mt-0.5 text-xs text-slate-500">
              {members.length} {members.length === 1 ? "member or invitation" : "members and invitations"}
            </p>
          </div>
          <button
            type="button"
            onClick={() => void loadMembers()}
            disabled={loading}
            aria-label="Refresh members"
            className="rounded-lg p-2 text-slate-400 transition hover:bg-white/5 hover:text-white disabled:opacity-50"
          >
            <RefreshCw size={16} className={loading ? "animate-spin" : ""} />
          </button>
        </div>

        {loading ? (
          <p className="px-5 py-8 text-sm text-slate-400">Loading members...</p>
        ) : members.length === 0 ? (
          <p className="px-5 py-8 text-sm text-slate-400">No members found.</p>
        ) : (
          <ul className="divide-y divide-white/5">
            {members.map((member) => (
              <li key={member.user_id} className="flex flex-wrap items-center gap-3 px-5 py-4">
                <div className="flex min-w-0 flex-1 items-center gap-3">
                  <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-indigo-500/10 text-indigo-300">
                    {member.role === "admin" ? <Shield size={17} /> : <UserRound size={17} />}
                  </span>
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-slate-100">
                      {member.email ?? "Email unavailable"}
                    </p>
                    <p className="truncate text-xs text-slate-500" title={member.user_id}>
                      {member.access_status === "pending"
                        ? "Invitation pending"
                        : member.email
                          ? `Member since ${new Date(member.created_at).toLocaleDateString()}`
                          : member.user_id}
                    </p>
                  </div>
                </div>

                <div className="flex items-center gap-2">
                  {canManageRoles && member.access_status !== "pending" ? (
                    <label className="sr-only" htmlFor={`role-${member.user_id}`}>
                      Role for {member.email ?? member.user_id}
                    </label>
                  ) : null}
                  {canManageRoles && member.access_status !== "pending" ? (
                    <select
                      id={`role-${member.user_id}`}
                      value={member.role}
                      onChange={(event) =>
                        void changeRole(member, event.target.value as OrganizationMember["role"])
                      }
                      className="rounded-lg border border-white/10 bg-[#151827] px-2.5 py-1.5 text-xs text-slate-200"
                    >
                      <option value="admin">Admin</option>
                      <option value="user">User</option>
                    </select>
                  ) : (
                    <span className="rounded-full border border-white/10 px-2.5 py-1 text-xs capitalize text-slate-300">
                      {member.access_status === "pending"
                        ? `${member.role} invitation pending`
                        : member.role}
                    </span>
                  )}
                  {canManage && (
                    <button
                      type="button"
                      onClick={() => void removeMember(member)}
                      aria-label={`Remove ${member.email ?? member.user_id}`}
                      className="rounded-lg p-2 text-slate-500 transition hover:bg-red-500/10 hover:text-red-400"
                    >
                      <X size={16} />
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
      {!canManage && !loading && !error && (
        <p className="text-xs text-slate-500">
          Only organization admins can invite or manage members.
        </p>
      )}
      <ConfirmDialog
        open={memberToRemove !== null}
        title={memberToRemove?.access_status === "pending" ? "Revoke invitation?" : "Remove organization member?"}
        description={memberToRemove
          ? memberToRemove.access_status === "pending"
            ? `Revoke the pending invitation for ${memberToRemove.email ?? memberToRemove.user_id}? They will not be able to join using that invitation.`
            : `Remove ${memberToRemove.email ?? memberToRemove.user_id} from this organization? Their account and access to other organizations will remain unchanged.`
          : ""}
        confirmLabel={memberToRemove?.access_status === "pending" ? "Revoke invitation" : "Remove member"}
        onConfirm={confirmRemoveMember}
        onError={(removeError) => setError(removeError instanceof Error ? removeError.message : "Unable to update organization access.")}
        onClose={() => setMemberToRemove(null)}
      />
    </div>
  );
}
