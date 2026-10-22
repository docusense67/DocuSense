import type { ClientStatus, RequestStatus } from '../api'

/**
 * Request status wording (change S-11, approve-first order).
 * Registrar screens show the sub-steps; students see only the client's four words
 * (Pending / Approved / Rejected / Completed) plus the end states that exist.
 */
export const REQUEST_LABEL: Record<RequestStatus, string> = {
  requested: 'Pending approval',
  approved: 'Approved · generating',
  for_verification: 'Ready for verification',
  verified: 'Verified · awaiting release',
  released: 'Released',
  rejected: 'Rejected',
  cancelled: 'Cancelled',
  expired: 'Expired',
  revoked: 'Revoked',
}

export const CLIENT_LABEL: Record<ClientStatus, string> = {
  pending: 'Pending',
  approved: 'Approved',
  rejected: 'Rejected',
  completed: 'Completed',
  cancelled: 'Cancelled',
  expired: 'Expired',
  revoked: 'Revoked',
}

/** Registrar filter order: the order a request moves through. */
export const REQUEST_STATUSES: RequestStatus[] = ['requested', 'approved', 'for_verification', 'verified', 'released',
  'rejected', 'cancelled', 'expired', 'revoked']

/** Open (not yet verified) requests the student or registrar may still cancel. */
export const CANCELLABLE: RequestStatus[] = ['requested', 'approved', 'for_verification']
