import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useAuraSocket } from './api/useAuraSocket'
import { buildTurns } from './lib/buildTurns'
import Turn from './components/Turn'
import SettingsPanel from './components/panels/SettingsPanel'
import TeamPanel from './components/panels/TeamPanel'
import SkillsPanel from './components/panels/SkillsPanel'

const TABS = [
  { id: 'chat', label: 'Chat' },
  { id: 'team', label: 'Team' },
  { id: 'settings', label: 'Settings' },
  { id: 'skills', label: 'Skills' },
]

export default function App() {
  const [tab, setTab] = useState('chat')
  const { status, events, sendUserMessage, respondConfirmation, respondOpenQuestion } = useAuraSocket()
  const [draft, setDraft] = useState('')
  const [resolvedRequests, setResolvedRequests] = useState(() => new Map())
  const scrollRef = useRef(null)

  const turns = useMemo(() => buildTurns(events, resolvedRequests), [events, resolvedRequests])
  const lastTurn = turns[turns.length - 1]
  const busy = Boolean(lastTurn && (!lastTurn.finished || lastTurn.hasPendingRequest))

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [turns])

  const onRespond = useMemo(
    () => ({
      confirmation: (requestId, approved) => {
        setResolvedRequests((prev) => new Map(prev).set(requestId, { approved }))
        respondConfirmation(requestId, approved)
      },
      question: (requestId, answer) => {
        setResolvedRequests((prev) => new Map(prev).set(requestId, { answer }))
        respondOpenQuestion(requestId, answer)
      },
    }),
    [respondConfirmation, respondOpenQuestion],
  )

  const submit = useCallback(
    (e) => {
      e.preventDefault()
      const text = draft.trim()
      if (!text || busy || status !== 'open') return
      sendUserMessage(text)
      setDraft('')
    },
    [draft, busy, status, sendUserMessage],
  )

  return (
    <div className="app">
      <header className="topbar">
        <span className="brand">AuraAgent</span>
        <nav className="tabs">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              className={`tab ${tab === t.id ? 'active' : ''}`}
              onClick={() => setTab(t.id)}
            >
              {t.label}
            </button>
          ))}
        </nav>
        <span className={`status-dot status-${status}`} title={status} />
        <span className="status-label">{status === 'open' ? 'connected' : status}</span>
      </header>

      {tab === 'chat' ? (
        <>
          <main className="chat" ref={scrollRef}>
            {turns.length === 0 && <div className="empty-state">Say something to get started.</div>}
            {turns.map((turn) => (
              <Turn key={turn.id} turn={turn} onRespond={onRespond} />
            ))}
          </main>

          <form className="composer" onSubmit={submit}>
            <input
              type="text"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder={busy ? 'Waiting for the current turn to finish…' : 'Message AuraAgent…'}
              disabled={busy || status !== 'open'}
            />
            <button type="submit" disabled={busy || status !== 'open' || !draft.trim()}>
              Send
            </button>
          </form>
        </>
      ) : (
        <main className="chat panel-view">
          {tab === 'team' && <TeamPanel />}
          {tab === 'settings' && <SettingsPanel />}
          {tab === 'skills' && <SkillsPanel />}
        </main>
      )}
    </div>
  )
}
