import { useEffect, useState } from 'react'
import { api } from '../api/rest'
import SessionRowMenu from './SessionRowMenu'

// Sidebar chat-session list -- the persistence/naming feature: every saved
// conversation shows up here, click to resume it (gui/server.py restores
// both the display and the Leader's own memory of it -- including
// re-activating this chat's Project, if it has one), auto-named after its
// first exchange otherwise (see gui/server.py's _maybe_autoname_session),
// or use the ⋯ menu for rename/pin/delete/Project assignment
// (SessionRowMenu.jsx) -- double-click-to-rename was removed (user
// decision: too easy to trigger by accident), the menu is the only
// rename entry point now.
//
// Only UNAFFILIATED chats (no Project tag) show here -- once a chat is
// added to a Project it's only ever visible in that Project's own Chat tab
// (ProjectDetailPanel.jsx), not duplicated into this general list too.
//
// Pinned chats sort first (the backend already returns them that way --
// see ChatSessionStore.list_sessions()); a "Pinned" divider marks the
// boundary so the grouping reads at a glance instead of just "some order".
//
// Refetches whenever `activeSessionId`/`activeSessionTitle` change -- that
// covers every way the active session can change server-side (switching,
// starting a new one, or the auto-naming push after the first exchange)
// without this component needing to know which one just happened.
export default function SessionList({ activeSessionId, activeSessionTitle, projects, onSwitch, busy, onProjectStatusChanged }) {
  const [sessions, setSessions] = useState([])
  const [editingId, setEditingId] = useState(null)
  const [editValue, setEditValue] = useState('')

  const refresh = () => api.getUnaffiliatedSessions().then(setSessions).catch(() => {})

  useEffect(() => {
    refresh()
  }, [activeSessionId, activeSessionTitle])

  const startRename = (session) => {
    setEditingId(session.id)
    setEditValue(session.title)
  }

  const commitRename = async (id) => {
    const title = editValue.trim()
    setEditingId(null)
    if (!title) return
    try {
      await api.renameSession(id, title)
      refresh()
    } catch {
      // A failed rename just leaves the old title in place -- nothing
      // destructive happened, not worth a dedicated error UI here.
    }
  }

  const remove = async (id) => {
    if (id === activeSessionId) return // backend rejects this too; menu already omits Delete for the active row's data flow below is defensive
    if (!window.confirm('Delete this chat? This cannot be undone.')) return
    try {
      await api.deleteSession(id)
      refresh()
    } catch {
      // Nothing to recover -- refresh() on the next natural trigger will reflect reality either way.
    }
  }

  const togglePin = async (session) => {
    try {
      await (session.pinned ? api.unpinSession(session.id) : api.pinSession(session.id))
      refresh()
    } catch {
      // Leaves the old pinned state in place -- nothing destructive.
    }
  }

  const setProject = async (id, slug) => {
    try {
      await api.setSessionProject(id, slug)
      refresh()
      // Retagging the ACTIVE chat also syncs the Leader's active Project
      // server-side (gui/routes.py's set_session_project) -- refresh the
      // sidebar's own Project summary/roster so that's visible right away
      // instead of only after the next unrelated trigger.
      onProjectStatusChanged?.()
    } catch {
      // Leaves the old tag in place -- nothing destructive.
    }
  }

  const setDirectory = async (id, directory) => {
    try {
      await api.setSessionDirectory(id, directory)
      refresh()
    } catch (err) {
      // Validation failure (e.g. a Windows system directory) -- this one
      // IS worth surfacing, since it's the only feedback the human gets
      // that their input was rejected rather than silently ignored.
      window.alert(err?.message || '设置工作目录失败。')
    }
  }

  if (sessions.length === 0) {
    return <p className="session-list-empty">No saved chats yet.</p>
  }

  // "其他对话" only earns a header when there's a pinned group above it to
  // contrast with -- with nothing pinned yet (the common starting state),
  // the whole list is just "other chats" and doesn't need to say so.
  // Computed as fixed indices before the render loop (not mutated during
  // it) so this stays a pure function of `sessions`.
  const firstPinnedIndex = sessions.findIndex((s) => s.pinned)
  const firstUnpinnedIndex = firstPinnedIndex === -1 ? -1 : sessions.findIndex((s) => !s.pinned)

  return (
    <ul className="session-list">
      {sessions.map((s, index) => {
        const showPinnedDivider = index === firstPinnedIndex
        const showUnpinnedDivider = index === firstUnpinnedIndex

        return (
          <li key={s.id}>
            {showPinnedDivider && <div className="session-group-label">已固定</div>}
            {showUnpinnedDivider && <div className="session-group-label">其他对话</div>}
            <div
              className={`session-row ${s.id === activeSessionId ? 'active' : ''}`}
              onClick={() => !busy && s.id !== activeSessionId && onSwitch(s.id)}
            >
              <span className="session-row-icon">💬</span>
              {s.pinned && <span className="session-pin-indicator" title="已固定">📌</span>}
              {editingId === s.id ? (
                <input
                  autoFocus
                  className="session-rename-input"
                  value={editValue}
                  onChange={(e) => setEditValue(e.target.value)}
                  onClick={(e) => e.stopPropagation()}
                  onBlur={() => commitRename(s.id)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') commitRename(s.id)
                    if (e.key === 'Escape') setEditingId(null)
                  }}
                />
              ) : (
                <span className="session-title" title={s.title}>
                  {s.title}
                </span>
              )}
              <SessionRowMenu
                session={s}
                projects={projects || []}
                onRename={() => startRename(s)}
                onDelete={() => remove(s.id)}
                onTogglePin={() => togglePin(s)}
                onSetProject={(slug) => setProject(s.id, slug)}
                onSetDirectory={(directory) => setDirectory(s.id, directory)}
              />
            </div>
          </li>
        )
      })}
    </ul>
  )
}
