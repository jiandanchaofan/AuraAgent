import { useEffect, useRef, useState } from 'react'
import TeamPanel from './panels/TeamPanel'
import SkillsPanel from './panels/SkillsPanel'
import McpPanel from './panels/McpPanel'
import SchedulesPanel from './panels/SchedulesPanel'
import WorkspacePanel from './panels/WorkspacePanel'
import NotesPanel from './panels/NotesPanel'
import CalendarPanel from './panels/CalendarPanel'
import SettingsPanel from './panels/SettingsPanel'

// Consolidates what used to be three separate sidebar areas (the
// always-visible Team roster strip, the Tools accordion section, and the
// Settings accordion section) into one modal, opened from the single
// "⚙️ Settings" link at the bottom of the sidebar (App.jsx). Each left-nav
// entry below renders the exact same panels/* component those sidebar
// sections used to link out to -- this modal is just a different shell
// around them, none of their own logic changed.
const SECTIONS = [
  { id: 'team', icon: '👥', label: 'Team' },
  { id: 'skills', icon: '🎓', label: 'Skills' },
  { id: 'mcp', icon: '🔌', label: 'MCP' },
  { id: 'schedules', icon: '⏰', label: 'Schedules' },
  { id: 'workspace', icon: '📂', label: 'Workspace' },
  { id: 'notes', icon: '📝', label: 'Notes' },
  { id: 'calendar', icon: '📅', label: 'Calendar' },
  { id: 'settings', icon: '⚙️', label: 'Settings' },
]

export default function SettingsModal({ open, onClose, projects }) {
  const [section, setSection] = useState('team')
  const modalRef = useRef(null)

  // Escape + click-outside, same pattern as SessionRowMenu.jsx's dropdown
  // -- only registered while actually open, so there's no stray
  // document-level listener sitting around the rest of the time.
  useEffect(() => {
    if (!open) return
    const onKeyDown = (e) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [open, onClose])

  if (!open) return null

  return (
    <div
      className="modal-backdrop"
      onMouseDown={(e) => {
        if (modalRef.current && !modalRef.current.contains(e.target)) onClose()
      }}
    >
      <div className="modal modal-settings" ref={modalRef} onMouseDown={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Settings</h2>
          <button type="button" className="modal-close" onClick={onClose} title="Close">
            ✕
          </button>
        </div>
        <div className="modal-body">
          <nav className="modal-nav">
            {SECTIONS.map((s) => (
              <button
                key={s.id}
                type="button"
                className={`modal-nav-item ${section === s.id ? 'active' : ''}`}
                onClick={() => setSection(s.id)}
              >
                <span className="modal-nav-icon">{s.icon}</span>
                {s.label}
              </button>
            ))}
          </nav>
          <div className="modal-content">
            {section === 'team' && <TeamPanel />}
            {section === 'skills' && <SkillsPanel />}
            {section === 'mcp' && <McpPanel />}
            {section === 'schedules' && <SchedulesPanel projects={projects} />}
            {section === 'workspace' && <WorkspacePanel />}
            {section === 'notes' && <NotesPanel />}
            {section === 'calendar' && <CalendarPanel />}
            {section === 'settings' && <SettingsPanel />}
          </div>
        </div>
      </div>
    </div>
  )
}
