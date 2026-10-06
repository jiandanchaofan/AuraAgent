import { useEffect, useState } from 'react'
import { api } from '../../api/rest'

// Simple prototype page for the sidebar's "Life" section -- real-wires to
// the existing /api/calendar status endpoint (already used by the
// Settings modal's CalendarPanel.jsx) since that's free, but the actual
// event browsing/editing UI below is a placeholder sketch, not a real
// feature yet.
export default function CalendarPrototype() {
  const [status, setStatus] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    api.getCalendar().then(setStatus).catch((e) => setError(e.message))
  }, [])

  const today = new Date().toLocaleDateString(undefined, { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' })

  return (
    <div className="pd-root">
      <div className="pd-header">
        <div className="pd-header-top">
          <div className="pd-header-title-row">
            <span className="pd-header-icon">📅</span>
            <h1 className="pd-header-name">Calendar</h1>
          </div>
        </div>
        <p className="pd-header-summary">{today}</p>
      </div>

      <div className="pd-body">
        <div className="prototype-notice">🚧 Prototype -- a placeholder for what this page will become, not a finished feature yet.</div>

        {error && <div className="panel-error">{error}</div>}
        {status && (
          <p className="panel-status">
            Current backend: <code>{status.backend}</code> (manage the connection in Settings → Calendar)
          </p>
        )}

        <div className="prototype-mock-list">
          <div className="prototype-mock-row">
            <span className="prototype-mock-time">09:00</span>
            <span className="prototype-mock-title">(example) Team standup</span>
          </div>
          <div className="prototype-mock-row">
            <span className="prototype-mock-time">14:00</span>
            <span className="prototype-mock-title">(example) Dentist appointment</span>
          </div>
        </div>
        <p className="panel-hint">Real event listing/creation from this page is not wired up yet -- these two rows are static examples.</p>
      </div>
    </div>
  )
}
