import { useEffect, useState } from 'react'
import { api } from '../../api/rest'

// The sidebar's "Tasks" destination -- connection configuration ONLY, no
// task list/CRUD here (explicit product decision: AuraAgent writes tasks
// into the user's real Google Tasks account, under a dedicated "AuraAgent"
// list it finds-or-creates itself; the user views/manages tasks in the
// real Google Tasks app, not a second list UI duplicated here). Connect/
// disconnect UI shape mirrors CalendarPanel.jsx's (the Settings modal's
// Calendar panel) exactly.
export default function TasksPrototype() {
  const [status, setStatus] = useState(null)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [connecting, setConnecting] = useState(false)

  const refresh = () => api.getTasksBackend().then(setStatus).catch((e) => setError(e.message))

  useEffect(() => {
    refresh()
  }, [])

  const connect = async () => {
    setError(null)
    setNotice(null)
    setConnecting(true)
    try {
      const updated = await api.connectTasksBackend()
      setStatus(updated)
      setNotice("Connected to Google Tasks. New tasks now go into your 'AuraAgent' list immediately — no restart needed.")
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
      const updated = await api.disconnectTasksBackend()
      setStatus(updated)
      setNotice('Disconnected. Back to the local task list. (Saved token was kept, in case you reconnect later.)')
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <div className="pd-root">
      <div className="pd-header">
        <div className="pd-header-top">
          <div className="pd-header-title-row">
            <span className="pd-header-icon">✅</span>
            <h1 className="pd-header-name">Tasks</h1>
          </div>
        </div>
      </div>

      <div className="pd-body">
        <p className="panel-hint">
          View and manage your tasks directly in the Google Tasks app — AuraAgent only writes new tasks into your
          own dedicated "AuraAgent" list there, it doesn't keep a separate list here.
        </p>

        {status && (
          <p className="panel-status">
            Current backend: <code>{status.backend}</code>
          </p>
        )}
        {error && <div className="panel-error">{error}</div>}
        {notice && <div className="panel-notice">{notice}</div>}
        {connecting && <div className="panel-notice">Opening a browser to connect your Google Tasks account…</div>}

        <section className="panel-section">
          {status?.backend === 'google' ? (
            <button type="button" className="btn btn-decline" onClick={disconnect}>
              Disconnect
            </button>
          ) : (
            <button type="button" className="btn btn-approve" onClick={connect} disabled={connecting}>
              {connecting ? 'Waiting for browser…' : 'Connect Google Tasks'}
            </button>
          )}
          <p className="panel-hint">
            Requires a Google OAuth client secret set up first (same one Calendar uses, or set one up fresh — Google
            Cloud Console → enable the Tasks API → Desktop app OAuth client). If it isn't there yet, connecting will
            show step-by-step instructions.
          </p>
        </section>
      </div>
    </div>
  )
}
