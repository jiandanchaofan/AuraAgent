import { useRef, useState } from 'react'
import { api } from '../../api/rest'
import { findActiveMentionTrigger } from '../../lib/mentionParser'
import MentionAutocomplete from './MentionAutocomplete'

// flomo-style single quick-input: plain textarea, `@`/`#` opens
// MentionAutocomplete inline while typing. `confirmedMentions` records what
// the user actually PICKED from the dropdown (never a re-parse of the raw
// text for substrings, which would risk false-positive matches on
// coincidental @/# characters) -- that's what gets sent as
// person_names/project_tag, the explicit Tier-1 mentions (source="user").
// Anything else in the text is left for the async AI-enrichment pass
// (source="ai") to find on its own.
export default function QuickEntryBox({ onCreated }) {
  const [text, setText] = useState('')
  const [confirmedMentions, setConfirmedMentions] = useState([])
  const [trigger, setTrigger] = useState(null)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState(null)
  const textareaRef = useRef(null)

  const recomputeTrigger = (value, caretIndex) => {
    setTrigger(findActiveMentionTrigger(value, caretIndex))
  }

  const handleChange = (e) => {
    setText(e.target.value)
    recomputeTrigger(e.target.value, e.target.selectionStart)
  }

  const handleSelect = (e) => {
    recomputeTrigger(e.target.value, e.target.selectionStart)
  }

  const pick = (row) => {
    if (!trigger) return
    const label = trigger.type === '@' ? row.name : row.tag
    const caret = textareaRef.current?.selectionStart ?? text.length
    const before = text.slice(0, trigger.start)
    const after = text.slice(caret)
    const inserted = `${trigger.type}${label} `
    const nextText = `${before}${inserted}${after}`
    setText(nextText)
    setConfirmedMentions((prev) => [...prev, { type: trigger.type === '@' ? 'person' : 'project', label }])
    setTrigger(null)
    requestAnimationFrame(() => {
      const pos = before.length + inserted.length
      textareaRef.current?.focus()
      textareaRef.current?.setSelectionRange(pos, pos)
    })
  }

  const removeMention = (index) => {
    setConfirmedMentions((prev) => prev.filter((_, i) => i !== index))
  }

  const submit = async () => {
    const trimmed = text.trim()
    if (!trimmed || submitting) return
    setSubmitting(true)
    setError(null)
    try {
      const personNames = confirmedMentions.filter((m) => m.type === 'person').map((m) => m.label)
      // Backend takes a single project_tag -- only the first #project mention
      // is used; this is a backend schema constraint (tools/personal_graph/
      // graph_tool.py's create_footprint), not a frontend simplification.
      const projectTag = confirmedMentions.find((m) => m.type === 'project')?.label
      const created = await api.createFootprint({
        text: trimmed,
        person_names: personNames.length ? personNames : undefined,
        project_tag: projectTag,
      })
      setText('')
      setConfirmedMentions([])
      setTrigger(null)
      onCreated?.(created)
    } catch (err) {
      setError(err.message)
    } finally {
      setSubmitting(false)
    }
  }

  const handleKeyDown = (e) => {
    if (trigger) return // let MentionAutocomplete's own listener handle it
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      e.preventDefault()
      submit()
    }
  }

  return (
    <div className="quick-entry">
      <textarea
        ref={textareaRef}
        className="quick-entry-input"
        placeholder="记录点什么... 用 @ 提到人物，# 关联专项"
        value={text}
        onChange={handleChange}
        onSelect={handleSelect}
        onKeyUp={handleSelect}
        onClick={handleSelect}
        onKeyDown={handleKeyDown}
        rows={3}
      />
      {trigger && (
        <MentionAutocomplete trigger={trigger} onPick={pick} onClose={() => setTrigger(null)} />
      )}
      {confirmedMentions.length > 0 && (
        <div className="quick-entry-mentions">
          {confirmedMentions.map((m, i) => (
            <span key={`${m.type}-${m.label}-${i}`} className={`graph-chip graph-chip-${m.type}`}>
              {m.type === 'person' ? '@' : '#'}
              {m.label}
              <button type="button" className="graph-chip-remove" onClick={() => removeMention(i)} aria-label="移除">
                ×
              </button>
            </span>
          ))}
        </div>
      )}
      {error && <div className="panel-error">{error}</div>}
      <div className="quick-entry-footer">
        <span className="quick-entry-hint">Ctrl/Cmd + Enter 发送</span>
        <button type="button" className="btn btn-accent" onClick={submit} disabled={!text.trim() || submitting}>
          记一笔
        </button>
      </div>
    </div>
  )
}
