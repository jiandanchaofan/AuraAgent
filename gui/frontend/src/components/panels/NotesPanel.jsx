import { useEffect, useState } from 'react'
import { api } from '../../api/rest'

export default function NotesPanel() {
  const [status, setStatus] = useState(null)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [path, setPath] = useState('')
  const [quickDir, setQuickDir] = useState('')

  const refresh = () => api.getNotes().then(setStatus).catch((e) => setError(e.message))

  useEffect(() => {
    refresh()
  }, [])

  const submit = async (e) => {
    e.preventDefault()
    setError(null)
    setNotice(null)
    try {
      const updated = await api.setNotes(path)
      setStatus((prev) => ({ ...prev, ...updated }))
      setNotice(`Notes root set to '${updated.path}'. Saved — persists across restarts.`)
      setPath('')
    } catch (err) {
      setError(err.message)
    }
  }

  const submitQuickDir = async (e) => {
    e.preventDefault()
    setError(null)
    setNotice(null)
    try {
      const updated = await api.setQuickNotesDir(quickDir)
      setStatus((prev) => ({ ...prev, ...updated }))
      setNotice(`Quick notes subdirectory set to '${updated.quick_notes_subdir}'. Saved — persists across restarts.`)
      setQuickDir('')
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <div className="panel">
      <h2>Notes</h2>
      {status && (
        <p className="panel-status">
          Current: <code>{status.path}</code> — quick notes subdirectory: <code>{status.quick_notes_subdir}</code>
        </p>
      )}
      {error && <div className="panel-error">{error}</div>}
      {notice && <div className="panel-notice">{notice}</div>}

      <section className="panel-section">
        <h3>Switch notes root</h3>
        <form className="panel-form" onSubmit={submit}>
          <input
            type="text"
            placeholder="e.g. a real Obsidian vault path"
            value={path}
            onChange={(e) => setPath(e.target.value)}
            required
          />
          <button type="submit">Set</button>
        </form>
        <p className="panel-hint">
          Independent from the workspace sandbox above — search_notes/read_note/create_note/update_note follow this
          instead.
        </p>
      </section>

      <section className="panel-section">
        <h3>Quick notes directory</h3>
        <form className="panel-form" onSubmit={submitQuickDir}>
          <input
            type="text"
            placeholder="e.g. Daily Notes"
            value={quickDir}
            onChange={(e) => setQuickDir(e.target.value)}
            required
          />
          <button type="submit">Set</button>
        </form>
        <p className="panel-hint">
          Which subdirectory under the notes root above holds daily note files — quick notes synced from a phone
          land there as Thino-style bullets (<code>- HH:MM:SS text</code>) under each day's "Today's Thino" heading.
        </p>
      </section>
    </div>
  )
}
