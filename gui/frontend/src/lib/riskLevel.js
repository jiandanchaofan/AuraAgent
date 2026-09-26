// The four self-extension risk tiers, light to heavy (see README's
// "Security Boundaries at a Glance"): capability_grant < code_execution <
// scope_expansion < arbitrary_execution.
export const RISK_LABELS = {
  capability_grant: 'Capability Grant',
  code_execution: 'Code Execution',
  scope_expansion: 'Scope Expansion',
  arbitrary_execution: 'Arbitrary Execution',
}

export const RISK_COLORS = {
  capability_grant: '#8b8f9b',
  code_execution: '#d98c2b',
  scope_expansion: '#d9642b',
  arbitrary_execution: '#d43f3f',
  destructive: '#d43f3f',
}

export function riskColor(riskLevel) {
  return RISK_COLORS[riskLevel] || '#8b8f9b'
}

export function riskLabel(riskLevel) {
  return RISK_LABELS[riskLevel] || riskLevel || 'Confirmation'
}
