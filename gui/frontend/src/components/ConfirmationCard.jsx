import { useState } from 'react'
import { riskColor, riskLabel } from '../lib/riskLevel'

export default function ConfirmationCard({ node, onRespond }) {
  const { payload, resolved, resolution, requestId } = node
  const color = riskColor(payload.risk_level)
  const [busy, setBusy] = useState(false)

  const handle = (approved) => {
    setBusy(true)
    onRespond(requestId, approved)
  }

  return (
    <div className="hitl-card" style={{ borderColor: color }}>
      <div className="hitl-header" style={{ color }}>
        <span className="hitl-badge" style={{ background: color }}>
          {riskLabel(payload.risk_level)}
        </span>
        <span className="hitl-tool">{payload.tool_name}</span>
      </div>
      <pre className="hitl-reason">{payload.reason}</pre>
      {resolved ? (
        <div className={`hitl-outcome ${resolution?.approved ? 'approved' : 'declined'}`}>
          {resolution?.approved ? '✓ Approved' : '✗ Declined'}
        </div>
      ) : (
        <div className="hitl-actions">
          <button type="button" className="btn btn-decline" disabled={busy} onClick={() => handle(false)}>
            Decline
          </button>
          <button type="button" className="btn btn-approve" style={{ background: color }} disabled={busy} onClick={() => handle(true)}>
            Approve
          </button>
        </div>
      )}
    </div>
  )
}
