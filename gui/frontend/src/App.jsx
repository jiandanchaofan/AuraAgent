import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useAuraSocket } from './api/useAuraSocket'
import { buildTurns } from './lib/buildTurns'
import { isSlashCommand, runSlashCommand } from './lib/slashCommands'
import { api } from './api/rest'
import ChatView from './components/ChatView'
import SessionList from './components/SessionList'
import ProjectList from './components/ProjectList'
import ProjectDetailPanel from './components/ProjectDetailPanel'
import SidebarSection from './components/SidebarSection'
import SettingsModal from './components/SettingsModal'
import ProjectPanel from './components/panels/ProjectPanel'
import GraphPanel from './components/panels/GraphPanel'
import CalendarPrototype from './components/panels/CalendarPrototype'
import TasksPrototype from './components/panels/TasksPrototype'

function readCollapsed() {
  try {
    return localStorage.getItem('aura.sidebarCollapsed') === '1'
  } catch {
    return false
  }
}

export default function App() {
  const [tab, setTab] = useState('chat')
  const [selectedProjectSlug, setSelectedProjectSlug] = useState(null)
  const [collapsed, setCollapsed] = useState(readCollapsed)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const {
    status,
    events,
    activeSession,
    syncSignal,
    sendUserMessage,
    respondConfirmation,
    respondOpenQuestion,
    newChat,
    switchSession,
  } = useAuraSocket()
  const [draft, setDraft] = useState('')
  const [resolvedRequests, setResolvedRequests] = useState(() => new Map())
  const [commandLog, setCommandLog] = useState([])
  const scrollRef = useRef(null)

  // Feeds the Project sidebar section's one-line status summary -- the
  // modal's own panels (SettingsModal.jsx) each fetch and display their
  // own data independently, so nothing else here needs to cache it.
  const [projectStatus, setProjectStatus] = useState(null)

  useEffect(() => {
    api.getProjects().then(setProjectStatus).catch(() => {})
  }, [])

  useEffect(() => {
    try {
      localStorage.setItem('aura.sidebarCollapsed', collapsed ? '1' : '0')
    } catch {
      // best-effort only -- a private window or blocked storage just means
      // the collapsed state won't survive a reload, nothing more.
    }
  }, [collapsed])

  // The server auto-syncs the active Project to match whichever chat just
  // became active (gui/server.py::_sync_active_project) -- this reflects
  // that back into the sidebar's Project section without it needing to
  // know WHY the active project might have changed.
  useEffect(() => {
    api.getProjects().then(setProjectStatus).catch(() => {})
  }, [activeSession?.id])

  const turns = useMemo(() => buildTurns(events, resolvedRequests), [events, resolvedRequests])
  const lastTurn = turns[turns.length - 1]
  const busy = Boolean(lastTurn && (!lastTurn.finished || lastTurn.hasPendingRequest))

  const activeProjectName = projectStatus?.projects?.find((p) => p.slug === projectStatus.active_slug)?.name

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [turns, commandLog])

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

  const runCommand = useCallback((line) => {
    const id = crypto.randomUUID()
    setCommandLog((prev) => [...prev, { id, command: line, text: '', isError: false, pending: true }])
    runSlashCommand(line, { api, setTab })
      .then((result) => {
        setCommandLog((prev) =>
          prev.map((e) => (e.id === id ? { ...e, text: result.text, isError: Boolean(result.isError), pending: false } : e)),
        )
        // A mutating command (e.g. /project use) may have changed the
        // Project sidebar section's summary.
        api.getProjects().then(setProjectStatus).catch(() => {})
      })
      .catch((err) => {
        setCommandLog((prev) => prev.map((e) => (e.id === id ? { ...e, text: err.message, isError: true, pending: false } : e)))
      })
  }, [])

  const submit = useCallback(
    (e) => {
      e.preventDefault()
      const text = draft.trim()
      if (!text) return
      if (isSlashCommand(text)) {
        runCommand(text)
        setDraft('')
        return
      }
      if (busy || status !== 'open') return
      sendUserMessage(text)
      setDraft('')
    },
    [draft, busy, status, sendUserMessage, runCommand],
  )

  const handleNewChat = useCallback(() => {
    newChat()
    setCommandLog([])
    setTab('chat')
  }, [newChat])

  const handleSwitchSession = useCallback(
    (sessionId) => {
      switchSession(sessionId)
      setCommandLog([])
      setTab('chat')
    },
    [switchSession],
  )

  const handleSelectProject = useCallback((slug) => {
    setSelectedProjectSlug(slug)
    setTab('project-detail')
  }, [])

  const handleLeaveProject = useCallback(() => {
    api
      .exitProject()
      .then(() => api.getProjects().then(setProjectStatus))
      .catch(() => {})
    setTab('chat')
  }, [])

  // These two stay ON the project-detail tab (unlike handleSwitchSession/
  // handleNewChat above, which are the sidebar's own triggers and always
  // navigate to the standalone chat tab) -- opening or starting a chat
  // from WITHIN a Project's own Chat tab should show it nested there
  // (ProjectDetailPanel's own "viewingChat" state), not jump out to the
  // full-page view.
  const handleOpenSessionInProject = useCallback(
    (sessionId) => {
      switchSession(sessionId)
      setCommandLog([])
    },
    [switchSession],
  )

  const handleNewChatInProject = useCallback(() => {
    newChat(selectedProjectSlug)
    setCommandLog([])
  }, [newChat, selectedProjectSlug])

  return (
    <div className={`app ${collapsed ? 'sidebar-collapsed' : ''}`}>
      <aside className="sidebar">
        <div className="sidebar-header">
          <span className="brand">
            <span className="brand-mark">✦</span>
            {!collapsed && 'AuraAgent'}
          </span>
          <button type="button" className="collapse-toggle" onClick={() => setCollapsed((c) => !c)} title="Toggle sidebar">
            {collapsed ? '»' : '«'}
          </button>
        </div>

        <button type="button" className="new-chat-btn" onClick={handleNewChat}>
          {collapsed ? '+' : '+ New chat'}
        </button>

        {!collapsed && (
          <div className="sidebar-sections">
            <SidebarSection id="project" icon="📁" title="Project" summary={activeProjectName || '(none)'}>
              <ProjectList
                activeSlug={projectStatus?.active_slug}
                onChanged={setProjectStatus}
                onOpenPanel={() => setTab('project')}
                onSelectProject={handleSelectProject}
              />
            </SidebarSection>

            <SidebarSection id="chat" icon="💬" title="Chat" summary={activeSession?.title} defaultOpen>
              <SessionList
                activeSessionId={activeSession?.id}
                activeSessionTitle={activeSession?.title}
                projects={projectStatus?.projects}
                onSwitch={handleSwitchSession}
                busy={busy}
                onProjectStatusChanged={() => api.getProjects().then(setProjectStatus).catch(() => {})}
              />
            </SidebarSection>

            <SidebarSection id="life" icon="🧭" title="Life">
              <ul className="session-list">
                <li className={`session-row ${tab === 'graph' ? 'active' : ''}`} onClick={() => setTab('graph')}>
                  <span className="session-row-icon">🗒️</span>
                  <span className="session-title">Footprint</span>
                </li>
                <li className={`session-row ${tab === 'calendar' ? 'active' : ''}`} onClick={() => setTab('calendar')}>
                  <span className="session-row-icon">📅</span>
                  <span className="session-title">Calendar</span>
                </li>
                <li className={`session-row ${tab === 'tasks' ? 'active' : ''}`} onClick={() => setTab('tasks')}>
                  <span className="session-row-icon">✅</span>
                  <span className="session-title">Tasks</span>
                </li>
              </ul>
            </SidebarSection>
          </div>
        )}

        {collapsed && (
          <nav className="sidebar-nav">
            <button type="button" className="nav-item" onClick={() => setCollapsed(false)} title="Project">
              📁
            </button>
            <button type="button" className="nav-item" onClick={() => setCollapsed(false)} title="Chat">
              💬
            </button>
            <button type="button" className="nav-item" onClick={() => setCollapsed(false)} title="Life">
              🧭
            </button>
          </nav>
        )}

        {/* Always rendered (unlike the old Team/Tools/Settings sections it
            replaces) so the Settings entry stays reachable even with the
            sidebar collapsed -- just the connection-status text drops out. */}
        <div className="sidebar-footer">
          <button type="button" className="nav-item sidebar-settings-link" onClick={() => setSettingsOpen(true)} title="Settings">
            <span>⚙️</span>
            {!collapsed && 'Settings'}
          </button>
          {!collapsed && (
            <span className="sidebar-footer-status">
              <span className={`status-dot status-${status === 'open' ? 'open' : 'closed'}`} />
              <span className="status-label">{status === 'open' ? 'connected' : status}</span>
            </span>
          )}
        </div>
      </aside>

      <div className="main">
        {tab === 'chat' ? (
          <ChatView
            turns={turns}
            commandLog={commandLog}
            onRespond={onRespond}
            scrollRef={scrollRef}
            draft={draft}
            setDraft={setDraft}
            onSubmit={submit}
            busy={busy}
            status={status}
          />
        ) : tab === 'project-detail' && selectedProjectSlug ? (
          <main className="chat project-detail-view">
            <ProjectDetailPanel
              key={selectedProjectSlug}
              slug={selectedProjectSlug}
              activeSessionId={activeSession?.id}
              projects={projectStatus?.projects}
              onLeave={handleLeaveProject}
              onOpenSession={handleOpenSessionInProject}
              onNewChat={handleNewChatInProject}
              onProjectChanged={setProjectStatus}
              chatView={
                <ChatView
                  turns={turns}
                  commandLog={commandLog}
                  onRespond={onRespond}
                  scrollRef={scrollRef}
                  draft={draft}
                  setDraft={setDraft}
                  onSubmit={submit}
                  busy={busy}
                  status={status}
                />
              }
            />
          </main>
        ) : tab === 'graph' ? (
          <main className="chat project-detail-view">
            <GraphPanel syncSignal={syncSignal} />
          </main>
        ) : tab === 'calendar' ? (
          <main className="chat project-detail-view">
            <CalendarPrototype />
          </main>
        ) : tab === 'tasks' ? (
          <main className="chat project-detail-view">
            <TasksPrototype />
          </main>
        ) : (
          <main className="chat panel-view">{tab === 'project' && <ProjectPanel onChanged={setProjectStatus} />}</main>
        )}
      </div>

      <SettingsModal open={settingsOpen} onClose={() => setSettingsOpen(false)} projects={projectStatus?.projects} />
    </div>
  )
}
