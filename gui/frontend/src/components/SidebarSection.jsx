import { useState } from 'react'

function readOpen(storageKey, defaultOpen) {
  try {
    const stored = localStorage.getItem(storageKey)
    return stored === null ? defaultOpen : stored === '1'
  } catch {
    return defaultOpen
  }
}

// One collapsible top-level sidebar section (Project / Chat / Tools /
// Settings) -- each remembers its own open/closed state independently
// (localStorage, same mechanism as the sidebar's own collapsed state),
// and shows an optional one-line `summary` in its header so the current
// context (active project, active chat title, ...) is visible even while
// collapsed.
export default function SidebarSection({ id, icon, title, summary, defaultOpen = false, children }) {
  const storageKey = `aura.section.${id}`
  const [open, setOpen] = useState(() => readOpen(storageKey, defaultOpen))

  const toggle = () => {
    setOpen((prev) => {
      const next = !prev
      try {
        localStorage.setItem(storageKey, next ? '1' : '0')
      } catch {
        // best-effort only
      }
      return next
    })
  }

  return (
    <div className={`sidebar-section ${open ? 'open' : ''}`}>
      <button type="button" className="sidebar-section-header" onClick={toggle}>
        <span className={`chevron ${open ? 'open' : ''}`}>▸</span>
        <span className="sidebar-section-title">
          {icon && <span className="sidebar-section-icon">{icon}</span>}
          {title}
        </span>
        {summary && <span className="sidebar-section-summary">{summary}</span>}
      </button>
      {open && <div className="sidebar-section-body">{children}</div>}
    </div>
  )
}
