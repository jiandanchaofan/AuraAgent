import { useEffect, useState } from 'react'
import { api } from '../../api/rest'

// Anchored directly below QuickEntryBox's textarea (not pixel-positioned at
// the caret -- flomo's own dropdown is similarly anchored to the input, not
// glued to the exact caret coordinate, and that's far simpler to get right
// than caret-position measurement for a plain <textarea>). Debounced search
// against persons (`@`) or graph projects (`#`) by prefix.
export default function MentionAutocomplete({ trigger, onPick, onClose }) {
  const [options, setOptions] = useState([])
  const [activeIndex, setActiveIndex] = useState(0)

  useEffect(() => {
    setActiveIndex(0)
    const timer = setTimeout(() => {
      const search = trigger.type === '@' ? api.searchPersons : api.searchGraphProjects
      search(trigger.query)
        .then((rows) => setOptions(rows))
        .catch(() => setOptions([]))
    }, 250)
    return () => clearTimeout(timer)
  }, [trigger.type, trigger.query])

  useEffect(() => {
    const handleKeyDown = (e) => {
      if (e.key === 'Escape') {
        onClose()
      } else if (e.key === 'ArrowDown') {
        e.preventDefault()
        setActiveIndex((i) => Math.min(i + 1, Math.max(options.length - 1, 0)))
      } else if (e.key === 'ArrowUp') {
        e.preventDefault()
        setActiveIndex((i) => Math.max(i - 1, 0))
      } else if (e.key === 'Enter' || e.key === 'Tab') {
        if (options[activeIndex]) {
          e.preventDefault()
          onPick(options[activeIndex])
        } else if (trigger.query) {
          // No match -- create as typed (same auto-stub-creation behavior
          // the backend already does for an unmatched @/# in chat-typed
          // create_footprint calls; see tools/personal_graph/graph_links.py).
          e.preventDefault()
          onPick({ id: null, name: trigger.query, tag: trigger.query })
        }
      }
    }
    window.addEventListener('keydown', handleKeyDown, true)
    return () => window.removeEventListener('keydown', handleKeyDown, true)
  }, [options, activeIndex, onPick, onClose])

  const label = (row) => (trigger.type === '@' ? row.name : row.tag)

  if (trigger.query && options.length === 0) {
    return (
      <div className="mention-autocomplete">
        <div className="mention-autocomplete-empty">
          没有匹配的{trigger.type === '@' ? '人物' : '专项'}，回车直接创建「{trigger.type}
          {trigger.query}」
        </div>
      </div>
    )
  }

  return (
    <div className="mention-autocomplete">
      {options.map((row, i) => (
        <div
          key={row.id}
          className={`mention-autocomplete-row ${i === activeIndex ? 'active' : ''}`}
          onMouseDown={(e) => {
            e.preventDefault()
            onPick(row)
          }}
          onMouseEnter={() => setActiveIndex(i)}
        >
          <span className="mention-autocomplete-marker">{trigger.type}</span>
          {label(row)}
        </div>
      ))}
    </div>
  )
}
