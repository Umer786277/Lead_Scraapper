"use client";

import { api, getActiveOrgId, setActiveOrgId, type DeletionJob, type Organization, type PlatformDirectoryUser } from "@/lib/api";
import ConfirmDialog from "@/components/ConfirmDialog";
import { Building2, RefreshCw, ShieldCheck, Trash2, Users } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

const PAGE_SIZE = 100;

type DeletionTarget =
  | { kind: "organization"; organization: Organization }
  | { kind: "user"; user: PlatformDirectoryUser };

export default function PlatformPage() {
  const router = useRouter();
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [users, setUsers] = useState<PlatformDirectoryUser[]>([]);
  const [deletionJobs, setDeletionJobs] = useState<DeletionJob[]>([]);
  const [deletionTarget, setDeletionTarget] = useState<DeletionTarget | null>(null);
  const [totalUsers, setTotalUsers] = useState(0);
  const [offset, setOffset] = useState(0);
  const [currentUserId, setCurrentUserId] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function loadDirectory(nextOffset = offset) {
    setLoading(true);
    setError("");
    try {
      const result = await api.orgs.directory(PAGE_SIZE, nextOffset);
      setOrganizations(result.organizations);
      setUsers(result.users.items);
      setTotalUsers(result.users.total);
      setCurrentUserId(result.current_user_id);
      setOffset(nextOffset);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load platform directory.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    let cancelled = false;
    async function initializeDirectory() {
      try {
        const result = await api.orgs.directory(PAGE_SIZE, 0);
        if (cancelled) return;
        setOrganizations(result.organizations);
        setUsers(result.users.items);
        setTotalUsers(result.users.total);
        setCurrentUserId(result.current_user_id);
        setOffset(0);
      } catch (loadError) {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : "Unable to load platform directory.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void initializeDirectory();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function initializeDeletionJobs() {
      try {
        const result = await api.orgs.deletionJobs();
        if (!cancelled) setDeletionJobs(result.items);
      } catch (loadError) {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : "Unable to load deletion jobs.");
        }
      }
    }
    void initializeDeletionJobs();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!deletionJobs.some((job) => job.status === "queued" || job.status === "processing")) return;
    let cancelled = false;
    const timer = window.setInterval(async () => {
      try {
        const result = await api.orgs.deletionJobs();
        if (cancelled) return;
        const hasActiveJobs = result.items.some(
          (job) => job.status === "queued" || job.status === "processing"
        );
        setDeletionJobs(result.items);
        if (!hasActiveJobs) {
          const directory = await api.orgs.directory(PAGE_SIZE, offset);
          if (cancelled) return;
          setOrganizations(directory.organizations);
          setUsers(directory.users.items);
          setTotalUsers(directory.users.total);
          setCurrentUserId(directory.current_user_id);
        }
      } catch (loadError) {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : "Unable to refresh deletion status.");
        }
      }
    }, 3000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [deletionJobs, offset]);

  function openOrganization(org: Organization) {
    setActiveOrgId(org.id);
    router.push("/");
  }

  async function deleteOrganization(org: Organization) {
    setError("");
    setNotice("");
    const result = await api.orgs.deleteOrganization(org.id);
    setNotice(
      result.status === "deleted"
        ? `"${org.name}" was deleted.`
        : result.status === "already_queued"
          ? `Deletion of "${org.name}" is already queued.`
          : result.status === "failed"
            ? `Deletion of "${org.name}" failed. See the job details below.`
            : `Deletion of "${org.name}" is ${result.status}.`
    );
    try {
      const jobs = await api.orgs.deletionJobs();
      setDeletionJobs(jobs.items);
    } catch {
      setError("Deletion was queued, but its latest status could not be refreshed.");
    }
    if (result.status === "deleted") {
      await loadDirectory(offset);
    }
    if (getActiveOrgId() === org.id) {
      const fallback = organizations.find((item) => item.id !== org.id && item.status === "active");
      setActiveOrgId(fallback?.id ?? null);
      router.push("/");
    }
  }

  async function deleteUser(user: PlatformDirectoryUser) {
    const identity = user.email ?? user.id;
    setError("");
    setNotice("");
    const result = await api.orgs.deleteUser(user.id);
    setNotice(
      result.status === "deleted"
        ? `${identity} was deleted.`
        : result.status === "already_queued"
          ? `Deletion of ${identity} is already queued.`
          : result.status === "failed"
            ? `Deletion of ${identity} failed. See the job details below.`
            : `Deletion of ${identity} is ${result.status}.`
    );
    try {
      const jobs = await api.orgs.deletionJobs();
      setDeletionJobs(jobs.items);
    } catch {
      setError("Deletion was queued, but its latest status could not be refreshed.");
    }
    if (result.status === "deleted") {
      await loadDirectory(offset);
    }
  }

  async function confirmDeletion() {
    if (!deletionTarget) return false;
    try {
      if (deletionTarget.kind === "organization") {
        await deleteOrganization(deletionTarget.organization);
      } else {
        await deleteUser(deletionTarget.user);
      }
      return true;
    } catch (deleteError) {
      setError(
        deleteError instanceof Error
          ? deleteError.message
          : "Unable to queue this deletion."
      );
      return false;
    }
  }

  return (
    <div className="space-y-8">
      <header>
        <h1 className="flex items-center gap-2 text-2xl font-bold text-white">
          <ShieldCheck className="text-purple-300" size={24} /> Platform administration
        </h1>
        <p className="mt-1 text-sm text-slate-400">
          Global organization and account directory. This page is available only to platform super admins.
        </p>
      </header>

      {error && <p role="alert" className="text-sm text-red-400">{error}</p>}
      {notice && <p role="status" className="text-sm text-emerald-300">{notice}</p>}

      {deletionJobs.length > 0 && (
        <section className="overflow-hidden rounded-2xl border border-white/8 bg-white/3">
          <div className="border-b border-white/8 px-5 py-4">
            <h2 className="font-semibold text-white">Permanent deletion jobs</h2>
            <p className="mt-0.5 text-xs text-slate-500">Jobs refresh automatically while processing.</p>
          </div>
          <ul className="divide-y divide-white/5">
            {deletionJobs.map((job) => (
              <li key={job.id} className="flex flex-wrap items-start gap-3 px-5 py-3">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm text-slate-100">
                    {job.kind === "organization" ? "Organization" : "User"}: {job.target_label}
                  </p>
                  {job.error && <p className="mt-1 text-xs text-red-300">{job.error}</p>}
                </div>
                <span className={`rounded-full border px-2.5 py-1 text-xs capitalize ${
                  job.status === "failed"
                    ? "border-red-400/20 text-red-300"
                    : job.status === "completed"
                      ? "border-emerald-400/20 text-emerald-300"
                      : "border-amber-400/20 text-amber-200"
                }`}>
                  {job.status}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="overflow-hidden rounded-2xl border border-white/8 bg-white/3">
        <div className="flex items-center justify-between border-b border-white/8 px-5 py-4">
          <div>
            <h2 className="flex items-center gap-2 font-semibold text-white"><Building2 size={17} /> Organizations</h2>
            <p className="mt-0.5 text-xs text-slate-500">{organizations.length} organizations</p>
          </div>
          <button type="button" onClick={() => void loadDirectory()} disabled={loading} aria-label="Refresh directory" className="rounded-lg p-2 text-slate-400 hover:bg-white/5 hover:text-white disabled:opacity-50">
            <RefreshCw size={16} className={loading ? "animate-spin" : ""} />
          </button>
        </div>
        {loading ? (
          <p className="px-5 py-6 text-sm text-slate-400">Loading organizations...</p>
        ) : organizations.length === 0 ? (
          <p className="px-5 py-6 text-sm text-slate-400">No organizations found.</p>
        ) : (
          <ul className="divide-y divide-white/5">
            {organizations.map((org) => (
              <li key={org.id} className="flex flex-wrap items-center gap-3 px-5 py-3">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-slate-100">{org.name}</p>
                  <p className="truncate text-xs text-slate-500">{org.slug}</p>
                </div>
                <div className="flex items-center gap-2">
                  {org.status === "active" && (
                    <>
                      <button type="button" onClick={() => openOrganization(org)} className="rounded-lg border border-white/10 px-3 py-1.5 text-xs font-medium text-indigo-300 hover:bg-white/5">
                        Open organization
                      </button>
                      {org.deletion_protected ? (
                        <span className="px-2 text-xs text-slate-500" title="The configured system default organization cannot be deleted">
                          System default
                        </span>
                      ) : (
                        <button type="button" onClick={() => setDeletionTarget({ kind: "organization", organization: org })} aria-label={`Permanently delete ${org.name}`} title="Permanently delete organization and its data" className="rounded-lg border border-red-400/20 p-2 text-red-300 hover:bg-red-400/10">
                          <Trash2 size={15} />
                        </button>
                      )}
                    </>
                  )}
                  {org.status !== "active" && (
                    <>
                      <span className="rounded-full border border-white/10 px-2.5 py-1 text-xs capitalize text-slate-400">{org.status}</span>
                      {!org.deletion_protected && (
                        <button type="button" onClick={() => setDeletionTarget({ kind: "organization", organization: org })} aria-label={`Permanently delete ${org.name}`} title="Permanently delete organization and its data" className="rounded-lg border border-red-400/20 p-2 text-red-300 hover:bg-red-400/10">
                          <Trash2 size={15} />
                        </button>
                      )}
                    </>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="overflow-hidden rounded-2xl border border-white/8 bg-white/3">
        <div className="flex items-center justify-between border-b border-white/8 px-5 py-4">
          <div>
            <h2 className="flex items-center gap-2 font-semibold text-white"><Users size={17} /> All users</h2>
            <p className="mt-0.5 text-xs text-slate-500">{totalUsers} accounts across all organizations</p>
          </div>
          <span className="text-xs text-slate-500">
            {totalUsers ? `${offset + 1}-${Math.min(offset + users.length, totalUsers)} of ${totalUsers}` : ""}
          </span>
        </div>
        {loading ? (
          <p className="px-5 py-6 text-sm text-slate-400">Loading users...</p>
        ) : users.length === 0 ? (
          <p className="px-5 py-6 text-sm text-slate-400">No users found.</p>
        ) : (
          <ul className="divide-y divide-white/5">
            {users.map((user) => (
              <li key={user.id} className="flex flex-wrap items-start gap-3 px-5 py-4">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-slate-100">{user.email ?? "Email unavailable"}</p>
                  <p className="mt-1 text-xs text-slate-500">
                    {user.platform_role === "super_admin" ? "Platform Super Admin · " : ""}
                    {user.email_verified ? "Verified" : "Email not verified"} · Created {new Date(user.created_at).toLocaleDateString()}
                  </p>
                  <p className="mt-1 text-xs text-slate-400">
                    {user.organizations.length
                      ? user.organizations.map((membership) =>
                          `${membership.org_name} (${membership.access_status === "pending" ? "invited" : membership.role})`
                        ).join(", ")
                      : "No organization access"}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => setDeletionTarget({ kind: "user", user })}
                  disabled={user.id === currentUserId}
                  aria-label={`Permanently delete ${user.email ?? user.id}`}
                  title={user.id === currentUserId ? "You cannot delete your own account" : "Permanently delete user account"}
                  className="rounded-lg border border-red-400/20 p-2 text-red-300 hover:bg-red-400/10 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  <Trash2 size={15} />
                </button>
              </li>
            ))}
          </ul>
        )}
        {totalUsers > PAGE_SIZE && (
          <div className="flex justify-end gap-2 border-t border-white/8 px-5 py-3">
            <button type="button" onClick={() => void loadDirectory(Math.max(0, offset - PAGE_SIZE))} disabled={loading || offset === 0} className="rounded-lg border border-white/10 px-3 py-1.5 text-xs text-slate-300 disabled:opacity-40">Previous</button>
            <button type="button" onClick={() => void loadDirectory(offset + PAGE_SIZE)} disabled={loading || offset + PAGE_SIZE >= totalUsers} className="rounded-lg border border-white/10 px-3 py-1.5 text-xs text-slate-300 disabled:opacity-40">Next</button>
          </div>
        )}
      </section>

      <ConfirmDialog
        open={deletionTarget !== null}
        title={deletionTarget ? `Permanently delete ${deletionTarget.kind}?` : ""}
        description={deletionTarget
          ? `${deletionTarget.kind === "organization"
              ? `This permanently removes "${deletionTarget.organization.name}" and all of its data.`
              : `This permanently deletes ${deletionTarget.user.email ?? deletionTarget.user.id} from Supabase Auth and removes access from every organization.`} This action cannot be undone. The deletion will be processed immediately when the queue is idle, or queued while other deletions are active.`
          : ""}
        confirmLabel="Delete permanently"
        onConfirm={confirmDeletion}
        onError={(deleteError) => setError(deleteError instanceof Error ? deleteError.message : "Unable to queue this deletion.")}
        onClose={() => setDeletionTarget(null)}
      />
    </div>
  );
}
