import Turn from './Turn'
import { isSlashCommand } from '../lib/slashCommands'

// The live conversation UI (turns + composer) -- extracted out of App.jsx
// so the exact same view can render either as the standalone main-area
// "chat" tab, or nested inside a Project's own detail view (see
// ProjectDetailPanel.jsx's "viewingChat" state) once a chat belonging to
// that Project is opened. There is only ever ONE live WS connection/turn
// stream per browser tab (App.jsx's useAuraSocket()), so this is never
// mounted twice at once -- App.jsx just decides WHERE to put it.
export default function ChatView({ turns, commandLog, onRespond, scrollRef, draft, setDraft, onSubmit, busy, status }) {
  return (
    <>
      <main className="chat" ref={scrollRef}>
        {turns.length === 0 && commandLog.length === 0 && (
          <div className="empty-state">Say something, or type / for commands, to get started.</div>
        )}
        {turns.map((turn) => (
          <Turn key={turn.id} turn={turn} onRespond={onRespond} />
        ))}
        {commandLog.map((entry) => (
          <div key={entry.id} className={`command-entry ${entry.isError ? 'is-error' : ''}`}>
            <div className="command-typed">{entry.command}</div>
            <pre className="command-result">{entry.pending ? 'Running…' : entry.text}</pre>
          </div>
        ))}
      </main>

      <form className="composer" onSubmit={onSubmit}>
        <input
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={busy ? 'Waiting for the current turn to finish…' : 'Message AuraAgent, or type / for commands…'}
          disabled={(busy && !isSlashCommand(draft)) || status !== 'open'}
        />
        <button type="submit" disabled={!draft.trim() || (status !== 'open' && !isSlashCommand(draft)) || (busy && !isSlashCommand(draft))}>
          Send
        </button>
      </form>
    </>
  )
}
