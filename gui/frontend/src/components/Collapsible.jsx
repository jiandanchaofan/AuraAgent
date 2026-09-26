import { useState } from 'react'

export default function Collapsible({ summary, tone, defaultOpen = false, children }) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className={`collapsible tone-${tone || 'default'}`}>
      <button type="button" className="collapsible-summary" onClick={() => setOpen((v) => !v)}>
        <span className={`chevron ${open ? 'open' : ''}`}>▸</span>
        {summary}
      </button>
      {open && <div className="collapsible-body">{children}</div>}
    </div>
  )
}
