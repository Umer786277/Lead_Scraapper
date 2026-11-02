export const LEAD_STATUSES = [
  "new",
  "contacted",
  "snoozed",
  "replied",
  "converted",
  "dead",
] as const;

export type LeadStatus = (typeof LEAD_STATUSES)[number];

export function isLeadStatus(value: string | null): value is LeadStatus {
  return value !== null && LEAD_STATUSES.some((status) => status === value);
}
