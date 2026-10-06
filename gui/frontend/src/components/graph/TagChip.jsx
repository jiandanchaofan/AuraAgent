// {label, kind: 'person'|'project', source: 'user'|'ai', onClick, onDismiss}
// AI-sourced chips get a muted/dashed style + "·AI" suffix and, if
// `onDismiss` is given, a "×" to reject a wrong guess (links.dismissed --
// see tools/personal_graph/graph_store.py's human-authority rules). A
// user-sourced chip never gets a dismiss affordance this round -- only an
// AI guess needs a correction path (user decision).
export default function TagChip({ label, kind, source, onClick, onDismiss }) {
  const marker = kind === 'person' ? '@' : '#'
  const isAi = source === 'ai'
  return (
    <span className={`graph-chip graph-chip-${kind} ${isAi ? 'graph-chip-ai' : ''}`}>
      <button type="button" className="graph-chip-label" onClick={onClick} disabled={!onClick}>
        {marker}
        {label}
        {isAi && <span className="graph-chip-ai-marker">·AI</span>}
      </button>
      {isAi && onDismiss && (
        <button type="button" className="graph-chip-remove" onClick={onDismiss} title="撤销 AI 建议的关联" aria-label="撤销">
          ×
        </button>
      )}
    </span>
  )
}
