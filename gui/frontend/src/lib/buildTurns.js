// Turns the flat, chronological event stream from /ws into a tree of
// "turns" (one per user_input) whose blocks nest a delegate_to_<worker>
// tool call around that worker's own full Thought/Tool Call/Observation
// sequence -- the tree the raw JSONL/event log doesn't literally contain
// (there's no parent_call_id field), but that can be reliably inferred:
// a tool_call's tool_name of the form delegate_to_<worker> means every
// subsequent event whose agent_name === <worker> belongs inside it, until
// the observation with the same call_id closes it. concurrent delegations
// (the Leader can fan out to multiple Workers in one turn) are tracked
// independently, keyed by worker name.
//
// Pure function: same input array always produces the same tree, so React
// state only ever needs to hold the raw `events` array from the socket;
// this runs again (cheaply) on every render via useMemo.

function newTurn(id, userText) {
  return {
    id,
    userText,
    blocks: [],
    finalAnswer: null,
    finished: false,
    hasPendingRequest: false,
  }
}

export function buildTurns(rawEvents, resolvedRequests) {
  const turns = []
  let turn = null
  let nodesByCallId = null
  const openDelegations = new Map() // workerName -> { callId, node }
  let lastContainer = null // the blocks[] array most recently appended to

  function resolveContainer(agentName) {
    if (agentName) {
      const open = openDelegations.get(agentName)
      if (open) return open.node.children
    }
    return turn.blocks
  }

  for (const event of rawEvents) {
    const { event_type: type, agent_name: agentName, payload = {} } = event

    if (type === 'user_input') {
      turn = newTurn(turns.length, payload.text)
      turns.push(turn)
      nodesByCallId = new Map()
      openDelegations.clear()
      lastContainer = turn.blocks
      continue
    }
    if (!turn) continue // stray event before any user_input; ignore

    if (type === 'calling_llm') {
      // Not rendered as a block -- see Turn.jsx's use of `activity` for
      // the transient "thinking" indicator this drives instead.
      continue
    }

    if (type === 'thought') {
      const container = resolveContainer(agentName)
      container.push({ type: 'thought', agentName, text: payload.text })
      lastContainer = container
      continue
    }

    if (type === 'tool_call') {
      const container = resolveContainer(agentName)
      const match = /^delegate_to_(.+)$/.exec(payload.tool_name)
      let node
      if (match) {
        const workerName = match[1]
        node = {
          type: 'delegation',
          agentName,
          workerName,
          callId: payload.call_id,
          arguments: payload.arguments,
          children: [],
          observation: null,
        }
        openDelegations.set(workerName, { callId: payload.call_id, node })
      } else {
        node = {
          type: 'tool_call',
          agentName,
          callId: payload.call_id,
          toolName: payload.tool_name,
          arguments: payload.arguments,
          observation: null,
        }
      }
      container.push(node)
      nodesByCallId.set(payload.call_id, node)
      lastContainer = container
      continue
    }

    if (type === 'observation') {
      const node = nodesByCallId.get(payload.call_id)
      if (node) {
        node.observation = { content: payload.content, isError: payload.is_error }
        if (node.type === 'delegation') {
          const open = openDelegations.get(node.workerName)
          if (open && open.callId === payload.call_id) openDelegations.delete(node.workerName)
        }
      }
      continue
    }

    if (type === 'final_answer') {
      if (openDelegations.has(agentName)) {
        openDelegations.get(agentName).node.finalAnswer = payload.text
      } else {
        turn.finalAnswer = payload.text
        turn.finished = true
      }
      continue
    }

    if (type === 'error') {
      const container = resolveContainer(agentName)
      container.push({ type: 'error', agentName, message: payload.message })
      lastContainer = container
      if (!openDelegations.has(agentName)) turn.finished = true
      continue
    }

    if (type === 'confirmation') {
      // Purely a JSONL-style audit record of a decision already surfaced
      // live via confirmation_request below -- nothing new to render.
      continue
    }

    if (type === 'confirmation_request' || type === 'open_question_request') {
      const container = lastContainer || turn.blocks
      const resolution = resolvedRequests.get(event.request_id)
      const resolved = resolution !== undefined
      container.push({
        type,
        requestId: event.request_id,
        payload,
        resolved,
        resolution,
      })
      if (!resolved) turn.hasPendingRequest = true
      lastContainer = container
      continue
    }
  }

  return turns
}
