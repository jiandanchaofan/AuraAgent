import { useEffect, useState } from 'react'
import { api } from '../../api/rest'

export default function CalendarPanel() {
  const [status, setStatus] = useState(null)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [connecting, setConnecting] = useState(false)

  const refresh = () => api.getCalendar().then(setStatus).catch((e) => setError(e.message))

  useEffect(() => {
    refresh()
  }, [])

  const connect = async () => {
    setError(null)
    setNotice(null)
    setConnecting(true)
    try {
      const updated = await api.connectCalendar()
      setStatus(updated)
      setNotice('Connected to Google Calendar. Now active immediately — no restart needed.')
    } catch (err) {
      setError(err.message)
    } finally {
      setConnecting(false)
    }
  }

  const disconnect = async () => {
    setError(null)
    setNotice(null)
    try {
      const updated = await api.disconnectCalendar()
      setStatus(updated)
      setNotice('Disconnected. Back to the local calendar. (Saved token was kept, in case you reconnect later.)')
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <div className="panel">
      <h2>Calendar</h2>
      {status && (
        <p className="panel-status">
          Current backend: <code>{status.backend}</code>
        </p>
      )}
      {error && <div className="panel-error">{error}</div>}
      {notice && <div className="panel-notice">{notice}</div>}
      {connecting && <div className="panel-notice">Opening a browser to connect your Google Calendar — approve the consent screen there…</div>}

      <section className="panel-section">
        {status?.backend === 'google' ? (
          <button type="button" className="btn btn-decline" onClick={disconnect}>
            Disconnect
          </button>
        ) : (
          <button type="button" className="btn btn-approve" onClick={connect} disabled={connecting}>
            {connecting ? 'Waiting for browser…' : 'Connect Google Calendar'}
          </button>
        )}
        <p className="panel-hint">
          Requires a Google OAuth client secret set up first (Google Cloud Console → enable Calendar API → Desktop
          app OAuth client). If it isn't there yet, connecting will show step-by-step instructions.
        </p>
      </section>
    </div>
  )
}
