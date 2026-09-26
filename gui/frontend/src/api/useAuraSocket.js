import { useCallback, useEffect, useRef, useState } from 'react'

// Talks to gui/server.py's single /ws endpoint (see its module docstring
// for the wire protocol). Same-origin in production (served by
// `uvicorn gui.server:app` alongside the built frontend); in dev, Vite's
// proxy (vite.config.js) forwards /ws to the backend on :8000.
function socketUrl() {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${window.location.host}/ws`
}

export function useAuraSocket() {
  const [status, setStatus] = useState('connecting') // connecting | open | closed
  const [events, setEvents] = useState([])
  const socketRef = useRef(null)

  useEffect(() => {
    const socket = new WebSocket(socketUrl())
    socketRef.current = socket

    socket.onopen = () => setStatus('open')
    socket.onclose = () => setStatus('closed')
    socket.onerror = () => setStatus('closed')
    socket.onmessage = (raw) => {
      let event
      try {
        event = JSON.parse(raw.data)
      } catch {
        return
      }
      setEvents((prev) => [...prev, event])
    }

    return () => {
      socket.close()
      socketRef.current = null
    }
  }, [])

  const send = useCallback((message) => {
    const socket = socketRef.current
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify(message))
    }
  }, [])

  const sendUserMessage = useCallback((text) => send({ type: 'user_message', text }), [send])

  const respondConfirmation = useCallback(
    (requestId, approved) => send({ type: 'confirmation_response', request_id: requestId, approved }),
    [send],
  )

  const respondOpenQuestion = useCallback(
    (requestId, answer) => send({ type: 'open_question_response', request_id: requestId, answer }),
    [send],
  )

  return { status, events, sendUserMessage, respondConfirmation, respondOpenQuestion }
}
