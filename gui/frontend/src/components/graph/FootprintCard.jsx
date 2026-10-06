import { useState } from 'react'
import { api } from '../../api/rest'
import TagChip from './TagChip'

function relativeTime(iso) {
  if (!iso) return ''
  const diffMs = Date.now() - new Date(iso).getTime()
  const mins = Math.round(diffMs / 60000)
  if (mins < 1) return '刚刚'
  if (mins < 60) return `${mins} 分钟前`
  const hours = Math.round(mins / 60)
  if (hours < 24) return `${hours} 小时前`
  const days = Math.round(hours / 24)
  if (days < 30) return `${days} 天前`
  return new Date(iso).toLocaleDateString()
}

// One timeline card (FootprintTimeline.jsx). Editing the text is a plain
// PATCH -- the backend deliberately never schedules AI re-enrichment for an
// edit (gui/graph_routes.py's update_footprint), so this never needs to
// show any "AI is re-checking..." state.
export default function FootprintCard({ footprint, onChanged, onFilterPerson, onFilterProject }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(footprint.text)

  const startEdit = () => {
    setDraft(footprint.text)
    setEditing(true)
  }

  const commitEdit = async () => {
    setEditing(false)
    const trimmed = draft.trim()
    if (!trimmed || trimmed === footprint.text) return
    try {
      await api.updateFootprint(footprint.id, { text: trimmed })
      onChanged?.()
    } catch {
      // Failed edit leaves the card showing its last-saved text.
    }
  }

  const remove = async () => {
    if (!window.confirm('删除这条足迹？可以在回收站里恢复。')) return
    try {
      await api.deleteFootprint(footprint.id)
      onChanged?.()
    } catch {
      // no-op on failure
    }
  }

  const dismiss = async (linkId) => {
    try {
      await api.dismissLink(linkId)
      onChanged?.()
    } catch {
      // no-op on failure
    }
  }

  const persons = footprint.links?.persons || []
  const projects = footprint.links?.projects || []

  return (
    <div className="footprint-card">
      <div className="footprint-card-top">
        <span className="footprint-card-time">{relativeTime(footprint.occurred_at)}</span>
        <div className="footprint-card-actions">
          <button type="button" className="pd-icon-btn" title="编辑" aria-label="编辑" onClick={startEdit}>
            ✎
          </button>
          <button type="button" className="pd-icon-btn" title="删除" aria-label="删除" onClick={remove}>
            🗑
          </button>
        </div>
      </div>

      {editing ? (
        <textarea
          autoFocus
          className="footprint-card-edit"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commitEdit}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) commitEdit()
            if (e.key === 'Escape') setEditing(false)
          }}
          rows={3}
        />
      ) : (
        <p className="footprint-card-text">{footprint.text}</p>
      )}

      {(persons.length > 0 || projects.length > 0) && (
        <div className="footprint-card-chips">
          {persons.map((p) => (
            <TagChip
              key={p.link_id}
              label={p.name}
              kind="person"
              source={p.link_source}
              onClick={() => onFilterPerson?.(p.id)}
              onDismiss={() => dismiss(p.link_id)}
            />
          ))}
          {projects.map((p) => (
            <TagChip
              key={p.link_id}
              label={p.tag}
              kind="project"
              source={p.link_source}
              onClick={() => onFilterProject?.(p.tag)}
              onDismiss={() => dismiss(p.link_id)}
            />
          ))}
        </div>
      )}
    </div>
  )
}
