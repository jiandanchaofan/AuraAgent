import Block from './Block'
import Markdown from './Markdown'

export default function Turn({ turn, onRespond }) {
  return (
    <div className="turn">
      <div className="user-message">
        <span className="user-label">You</span>
        <p>{turn.userText}</p>
      </div>
      <div className="agent-blocks">
        {turn.blocks.map((node, i) => (
          <Block key={i} node={node} onRespond={onRespond} />
        ))}
        {turn.finalAnswer && (
          <div className="final-answer">
            <span className="agent-label">orchestrator</span>
            <Markdown className="final-answer-text">{turn.finalAnswer}</Markdown>
          </div>
        )}
        {!turn.finished && !turn.hasPendingRequest && <div className="live-typing">● thinking…</div>}
      </div>
    </div>
  )
}
