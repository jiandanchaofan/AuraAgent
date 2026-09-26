import { useState } from 'react'

export default function OpenQuestionCard({ node, onRespond }) {
  const { payload, resolved, requestId } = node
  const [answer, setAnswer] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = (e) => {
    e.preventDefault()
    if (!answer.trim()) return
    setBusy(true)
    onRespond(requestId, answer.trim())
  }

  return (
    <div className="hitl-card question-card">
      <div className="hitl-header">
        <span className="hitl-badge question-badge">Question</span>
      </div>
      <p className="hitl-prompt">{payload.prompt}</p>
      {resolved ? (
        <div className="hitl-outcome answered">{'✓'} Answered</div>
      ) : (
        <form className="hitl-question-form" onSubmit={submit}>
          <input
            type="text"
            autoComplete="off"
            value={answer}
            onChange={(e) => setAnswer(e.target.value)}
            placeholder="Type your answer..."
            disabled={busy}
          />
          <button type="submit" className="btn btn-approve" disabled={busy || !answer.trim()}>
            Send
          </button>
        </form>
      )}
    </div>
  )
}
