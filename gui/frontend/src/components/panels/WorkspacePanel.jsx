import { useEffect, useState } from 'react'
import { api } from '../../api/rest'

export default function WorkspacePanel({ onChanged }) {
  const [status, setStatus] = useState(null)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [path, setPath] = useState('')

  const refresh = () => api.getWorkspace().then(setStatus).catch((e) => setError(e.message))

  useEffect(() => {
    refresh()
  }, [])

  const submit = async (e) => {
    e.preventDefault()
    setError(null)
    setNotice(null)
    try {
      const updated = await api.setWorkspace(path)
      setStatus(updated)
      setNotice(`Workspace set to '${updated.path}'. Saved — persists across restarts.`)
      setPath('')
      onChanged?.()
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <div className="panel">
      <h2>Workspace</h2>
      {status && (
        <p className="panel-status">
          Current: <code>{status.path}</code>
        </p>
      )}
      {error && <div className="panel-error">{error}</div>}
      {notice && <div className="panel-notice">{notice}</div>}

      <section className="panel-section">
        <h3>Switch workspace root</h3>
        <form className="panel-form" onSubmit={submit}>
          <input
            type="text"
            placeholder="e.g. C:\Users\me\Documents"
            value={path}
            onChange={(e) => setPath(e.target.value)}
            required
          />
          <button type="submit">Set</button>
        </form>
        <p className="panel-hint">
          Every workspace-scoped tool (files, screenshots, downloads, Skills) follows immediately — no restart needed.
        </p>
      </section>
    </div>
  )
}
