import { useEffect, useState } from 'react'
import { api } from '../../api/rest'

export default function ProjectPanel({ onChanged }) {
  const [status, setStatus] = useState(null)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)

  const [slug, setSlug] = useState('')
  const [directory, setDirectory] = useState('')

  const refresh = () =>
    api
      .getProjects()
      .then((s) => {
        setStatus(s)
        onChanged?.(s)
      })
      .catch((e) => setError(e.message))

  useEffect(() => {
    refresh()
  }, [])

  const submitCreate = async (e) => {
    e.preventDefault()
    setError(null)
    setNotice(null)
    try {
      const created = await api.createProject(slug, directory.trim() || undefined)
      setNotice(`Created project '${created.slug}' at '${created.directory}'. Use it below to enter it.`)
      setSlug('')
      setDirectory('')
      refresh()
    } catch (err) {
      setError(err.message)
    }
  }

  const use = async (targetSlug) => {
    setError(null)
    setNotice(null)
    try {
      const project = await api.useProject(targetSlug)
      setNotice(`Entered project '${project.slug}'. Active immediately, no restart needed.`)
      refresh()
    } catch (err) {
      setError(err.message)
    }
  }

  const exit = async () => {
    setError(null)
    setNotice(null)
    try {
      await api.exitProject()
      setNotice('Left the active project. Workspace restored to what it was before.')
      refresh()
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <div className="panel">
      <h2>Project</h2>
      {status && (
        <p className="panel-status">
          Active: <code>{status.active_slug || '(none)'}</code>
        </p>
      )}
      {error && <div className="panel-error">{error}</div>}
      {notice && <div className="panel-notice">{notice}</div>}

      <section className="panel-section">
        {status?.projects.length ? (
          <table className="roster">
            <thead>
              <tr>
                <th>Slug</th>
                <th>Directory</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {status.projects.map((p) => (
                <tr key={p.slug}>
                  <td>{p.slug === status.active_slug ? <b>{p.slug}</b> : p.slug}</td>
                  <td className="roster-caps">{p.directory}</td>
                  <td>
                    {p.slug === status.active_slug ? (
                      <button type="button" className="btn btn-decline" onClick={exit}>
                        Leave
                      </button>
                    ) : (
                      <button type="button" className="btn btn-approve" onClick={() => use(p.slug)}>
                        Use
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="panel-hint">No projects yet — create one below.</p>
        )}
      </section>

      <section className="panel-section">
        <h3>Create a project</h3>
        <form className="panel-form" onSubmit={submitCreate}>
          <input type="text" placeholder="slug" value={slug} onChange={(e) => setSlug(e.target.value)} required />
          <input
            type="text"
            placeholder="existing directory (optional — otherwise auto-created)"
            value={directory}
            onChange={(e) => setDirectory(e.target.value)}
          />
          <button type="submit">Create</button>
        </form>
        <p className="panel-hint">
          Entering a project repoints the workspace and loads its summary into the Leader's context — session-only,
          not saved to .env.
        </p>
      </section>
    </div>
  )
}
