import Collapsible from './Collapsible'
import ConfirmationCard from './ConfirmationCard'
import OpenQuestionCard from './OpenQuestionCard'

function ArgsPreview(args) {
  const s = JSON.stringify(args)
  return s.length > 60 ? `${s.slice(0, 60)}…` : s
}

function ThoughtBlock({ node }) {
  return (
    <div className="block thought-block">
      <span className="block-icon">💭</span>
      <span className="block-text">{node.text}</span>
    </div>
  )
}

function ObservationBody({ observation }) {
  if (!observation) return <div className="observation pending">…waiting for result</div>
  return (
    <pre className={`observation ${observation.isError ? 'is-error' : ''}`}>{observation.content}</pre>
  )
}

function ToolCallBlock({ node }) {
  return (
    <Collapsible
      tone={node.observation?.isError ? 'error' : 'tool'}
      summary={
        <span>
          <span className="block-icon">🔧</span>
          <code>{node.toolName}</code>
          <span className="args-preview">{ArgsPreview(node.arguments)}</span>
        </span>
      }
    >
      <pre className="arguments">{JSON.stringify(node.arguments, null, 2)}</pre>
      <ObservationBody observation={node.observation} />
    </Collapsible>
  )
}

function DelegationBlock({ node, onRespond }) {
  return (
    <Collapsible
      defaultOpen
      tone="delegation"
      summary={
        <span>
          <span className="block-icon">🤝</span>
          delegated to <strong>{node.workerName}</strong>
          {!node.observation && <span className="live-dot" />}
        </span>
      }
    >
      <div className="delegation-children">
        {node.children.map((child, i) => (
          <Block key={i} node={child} onRespond={onRespond} />
        ))}
      </div>
      {node.observation && (
        <div className="delegation-result">
          <span className="block-icon">✅</span>
          {node.finalAnswer || node.observation.content}
        </div>
      )}
    </Collapsible>
  )
}

function ErrorBlock({ node }) {
  return (
    <div className="block error-block">
      <span className="block-icon">⛔</span>
      <span className="block-text">{node.message}</span>
    </div>
  )
}

export default function Block({ node, onRespond }) {
  switch (node.type) {
    case 'thought':
      return <ThoughtBlock node={node} />
    case 'tool_call':
      return <ToolCallBlock node={node} />
    case 'delegation':
      return <DelegationBlock node={node} onRespond={onRespond} />
    case 'error':
      return <ErrorBlock node={node} />
    case 'confirmation_request':
      return <ConfirmationCard node={node} onRespond={onRespond.confirmation} />
    case 'open_question_request':
      return <OpenQuestionCard node={node} onRespond={onRespond.question} />
    default:
      return null
  }
}
