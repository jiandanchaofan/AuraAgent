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
  // The chat session currently loaded into `events` -- set from the
  // server's session_loaded push (on connect, and after new_chat/
  // set_active_session), kept live by session_renamed (auto-naming or a
  // manual rename elsewhere, e.g. the sidebar's own PATCH call landing
  // back here isn't needed since that flow updates the sidebar directly --
  // this is specifically for staying in sync with the auto-naming push).
  const [activeSession, setActiveSession] = useState(null) // {id, title} | null
  // Bumped on every `sync_signal` push (N15/the Personal Data Graph's
  // footprints/persons/projects) -- just a "something changed" counter, per
  // Auralis's own "REST carries data, WS carries only signals" design (see
  // gui/graph_routes.py/gui/sync_routes.py). A consumer (GraphPanel) watches
  // this via useEffect and always re-pulls via REST, never reads anything
  // off the event itself.
  const [syncSignal, setSyncSignal] = useState(0)
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
      if (event.event_type === 'session_loaded') {
        setActiveSession({ id: event.payload.id, title: event.payload.title })
        setEvents(event.payload.events)
        return
      }
      if (event.event_type === 'session_renamed') {
        setActiveSession((prev) => (prev && prev.id === event.payload.id ? { ...prev, title: event.payload.title } : prev))
        return
      }
      if (event.event_type === 'sync_signal') {
        setSyncSignal((n) => n + 1)
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

  // Sidebar "New chat": the server creates a brand-new saved session and
  // pushes it back as session_loaded (handled above), which is what
  // actually updates `events`/`activeSession` -- this just sends the
  // request. `projectSlug` tags the new chat to that Project (the Project
  // detail view's own "+ New chat"); omitted, the server tags it to
  // whichever Project is currently active, if any (gui/server.py).
  const newChat = useCallback((projectSlug) => send({ type: 'new_chat', project_slug: projectSlug || undefined }), [send])

  // Sidebar session list: switch to a previously-saved chat. Same
  // session_loaded round trip restores both the display and (server-side)
  // the Leader's own memory of it.
  const switchSession = useCallback((sessionId) => send({ type: 'set_active_session', session_id: sessionId }), [send])

  return {
    status,
    events,
    activeSession,
    syncSignal,
    sendUserMessage,
    respondConfirmation,
    respondOpenQuestion,
    newChat,
    switchSession,
  }
}
