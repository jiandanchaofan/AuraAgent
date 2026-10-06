// The four self-extension risk tiers, light to heavy (see README's
// "Security Boundaries at a Glance"): capability_grant < code_execution <
// scope_expansion < arbitrary_execution. `destructive` (delete_file,
// kill_process, ...) and `privacy_exposure` (take_screenshot, Epic L2 --
// a screen capture exposes whatever's on screen, a different risk shape
// than data loss, hence its own color rather than reusing destructive's)
// are separate, non-self-extension tiers used elsewhere in the codebase.
// `recurring_autonomy` (propose_scheduled_task, N8) is a third: not "will
// this one action cause harm" but "should this run unattended, possibly
// repeatedly, without asking again each time" -- its own color, distinct
// from both the danger-red tiers and privacy_exposure's purple.
export const RISK_LABELS = {
  capability_grant: 'Capability Grant',
  code_execution: 'Code Execution',
  scope_expansion: 'Scope Expansion',
  arbitrary_execution: 'Arbitrary Execution',
  destructive: 'Destructive',
  privacy_exposure: 'Privacy Exposure',
  recurring_autonomy: 'Recurring Autonomy',
}

export const RISK_COLORS = {
  capability_grant: '#8b8f9b',
  code_execution: '#d98c2b',
  scope_expansion: '#d9642b',
  arbitrary_execution: '#d43f3f',
  destructive: '#d43f3f',
  privacy_exposure: '#8b5cf6',
  recurring_autonomy: '#0e9488',
}

export function riskColor(riskLevel) {
  return RISK_COLORS[riskLevel] || '#8b8f9b'
}

export function riskLabel(riskLevel) {
  return RISK_LABELS[riskLevel] || riskLevel || 'Confirmation'
}
