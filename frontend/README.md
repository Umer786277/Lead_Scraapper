This is a [Next.js](https://nextjs.org) project bootstrapped with [`create-next-app`](https://nextjs.org/docs/app/api-reference/cli/create-next-app).

## Getting Started

First, run the development server:

```bash
npm run dev
# or
yarn dev
# or
pnpm dev
# or
bun dev
```

Open [http://localhost:3000](http://localhost:3000) with your browser to see the result.

## Organization members

Organization admins and platform super admins can manage teammates from
**Members** in the sidebar. Organization roles are `admin` and `user`; admins
can invite users and manage organization members, but each organization must
retain at least one admin. When inviting a teammate, choose `admin` or `user`;
the selection defaults to `user` and takes effect after the invitee accepts the
emailed link. Existing accounts also receive an invitation and gain access only
after accepting it. Pending
invitations appear in the member list and can be revoked before acceptance.
Admins can remove users from their organization without deleting
the user's account. Super admins can permanently delete organizations and
Auth user accounts from the Platform page. When no deletion is active, a new
deletion is processed immediately and its result is shown before the request
completes. If other deletion jobs are active, it is queued in PostgreSQL for
the backend worker; the Platform page shows queued, processing, completed, and
failed jobs. Transient errors retry automatically up to five times with
backoff. Organization deletion permanently removes its tenant data in a
database transaction. User deletion removes the Supabase Auth account and then
clears local access, retrying local cleanup if the external deletion succeeded
but the database transaction failed. The configured system-default
organization, last active organization, last admin of an active organization,
last platform super admin, and signed-in super admin are protected from deletion.
Super admins can select any active organization and access its data without
becoming a member.

Only platform super admins can create organizations. Creating an organization
does not automatically add the super admin as an organization member; platform
super admins can still select and manage organizations without membership. The
**Platform** sidebar page lists all organizations and all Supabase Auth users,
including
their memberships and pending invitations. Organization admins can only list
and manage users for the currently selected organization.

Configure `SUPER_ADMIN_USER_IDS` on the backend as a comma-separated list of
trusted Supabase Auth user UUIDs. This variable grants platform-wide access;
never derive it from user-editable metadata. To revoke access, remove the UUID
from the environment setting and delete its row from `platform_roles`.
Organization admins can manage members and sender accounts; organization users
can access organization data and perform normal lead, campaign, and outreach
work, but cannot administer members, sender credentials, recurring scrape
schedules, or the organization-wide outreach pause.

Organization invitations use the backend SMTP settings (`SMTP_HOST`,
`SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, and `SMTP_FROM`) and
`APP_BASE_URL` (the frontend origin). Configure the Supabase Auth redirect
allowlist to include the callback URL with query parameters, such as
`http://localhost:3000/auth/callback**` and the deployed HTTPS equivalent.
Invitations store the recipient email and a hashed, single-use token; they do
not create a Supabase Auth user. The Auth account and organization membership
are created only when the invitee follows the link and signs in or creates an
account using that email. For a new invitee, the backend uses its
`SUPABASE_SERVICE_ROLE_KEY` to create an email-confirmed Auth account after
validating the invitation token; the invitation email itself verifies control
of the invited address. Existing accounts sign in normally. Regular, non-invite
signups still use Supabase's email-confirmation flow. Membership is granted
only after the authenticated invitee completes acceptance. Invitation links
expire after seven days. Keep the service-role key backend-only.

Background worker jobs coordinate through renewable PostgreSQL leases, so API
and standalone worker processes can share one database without duplicating
scheduled work. Scrape rotation acquires a separate lease per organization and
runs up to `SCRAPE_WORKER_CONCURRENCY` organizations at once (default: `2`);
set this according to the memory available to the backend.

Outreach dispatch has a tenant hourly allocation cap configurable by an
organization admin on the Outreach page (1–3,600 emails per hour; default: 60).
The cap divides each database-clock hour into fixed slots (60/hour gives a
60-second slot). PostgreSQL-backed coordination ensures concurrent API and
worker processes share the allocation; a separate per-minute cap may further
reduce the effective rate. SMTP 421/451 responses pause that organization's
dispatch for five minutes and leave scheduled messages pending for retry.
Slot reservations use a short transaction with a row lock and a 10-second
acquisition timeout; workers wait outside the transaction. An expired slot is
deferred rather than retried in an unbounded loop. Long waits refresh queue
claims through a separate database connection. Slot reservations and pause
timestamps use the PostgreSQL clock, so worker-machine clock synchronization
is not part of rate enforcement.

You can start editing the page by modifying `app/page.tsx`. The page auto-updates as you edit the file.

This project uses [`next/font`](https://nextjs.org/docs/app/building-your-application/optimizing/fonts) to automatically optimize and load [Geist](https://vercel.com/font), a new font family for Vercel.

## Learn More

To learn more about Next.js, take a look at the following resources:

- [Next.js Documentation](https://nextjs.org/docs) - learn about Next.js features and API.
- [Learn Next.js](https://nextjs.org/learn) - an interactive Next.js tutorial.

You can check out [the Next.js GitHub repository](https://github.com/vercel/next.js) - your feedback and contributions are welcome!

## Deploy on Vercel

The easiest way to deploy your Next.js app is to use the [Vercel Platform](https://vercel.com/new?utm_medium=default-template&filter=next.js&utm_source=create-next-app&utm_campaign=create-next-app-readme) from the creators of Next.js.

Check out our [Next.js deployment documentation](https://nextjs.org/docs/app/building-your-application/deploying) for more details.
