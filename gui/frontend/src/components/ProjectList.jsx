import { useEffect, useState } from 'react'
import { api } from '../api/rest'

// Sidebar Project list -- clicking a row opens the full Project detail
// view (ProjectDetailPanel.jsx: Chat/目录/Outputs), activating the project
// on the way in if it isn't already active. Renaming lives only in that
// detail view's own pencil icon now -- double-click-to-rename here was
// removed (user decision: too easy to trigger by accident). Creating one
// needs a slug + optional directory, which doesn't fit a one-click "+" the
// way a new chat does -- so "+ New project" opens the (separate, simpler)
// Project management panel's create form instead of trying to cram it in.
export default function ProjectList({ activeSlug, onChanged, onOpenPanel, onSelectProject }) {
  const [projects, setProjects] = useState([])

  const refresh = () =>
    api
      .getProjects()
      .then((s) => {
        setProjects(s.projects)
        onChanged?.(s)
      })
      .catch(() => {})

  useEffect(() => {
    refresh()
  }, [activeSlug])

  const open = async (slug) => {
    if (slug !== activeSlug) {
      try {
        await api.useProject(slug)
        refresh()
      } catch {
        // A failed switch leaves the current project active -- still navigate to the detail view either way.
      }
    }
    onSelectProject(slug)
  }

  return (
    <>
      {projects.length === 0 ? (
        <p className="session-list-empty">No projects yet.</p>
      ) : (
        <ul className="session-list">
          {projects.map((p) => (
            <li
              key={p.slug}
              className={`session-row ${p.slug === activeSlug ? 'active' : ''}`}
              onClick={() => open(p.slug)}
            >
              <span className="session-row-icon">📁</span>
              <span className="session-title" title={p.directory}>
                {p.name}
              </span>
            </li>
          ))}
        </ul>
      )}
      <button type="button" className="sidebar-link" onClick={onOpenPanel}>
        + New project
      </button>
    </>
  )
}
