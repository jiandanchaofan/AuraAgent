import { useEffect, useRef, useState } from 'react'
import { api } from '../api/rest'
import SessionRowMenu from './SessionRowMenu'

function relativeTime(iso) {
  const diffMs = Date.now() - new Date(iso).getTime()
  const mins = Math.round(diffMs / 60000)
  if (mins < 1) return '刚刚'
  if (mins < 60) return `${mins} 分钟前`
  const hours = Math.round(mins / 60)
  if (hours < 24) return `${hours} 小时前`
  const days = Math.round(hours / 24)
  if (days < 30) return `${days} 天前`
  return new Date(iso).toLocaleDateString()
}

function formatBytes(bytes) {
  if (bytes == null) return '—'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

// The Project detail view (approved design: see the conversation this was
// built from) -- clicking a Project in the sidebar lands here instead of
// silently switching. Chat/Context/Tools/目录 are views onto the SAME
// project: its own chats (SessionInfo.project_slug tag), its accumulated
// Role/current-state/open-questions/Memory, its per-project Skill/MCP
// grants, and its own directory (read-write, tools/sandbox_path.py-
// guarded). There used to be a separate Outputs tab/field -- retired, see
// tools/projects/project_store.py's module docstring for why (it was pure
// duplication of what the 目录 tab already shows accurately). Entering
// this view IS entering the project (mirrors the header's own "离开项目"
// framing) -- App.jsx's onSelectProject activates it before rendering this.
export default function ProjectDetailPanel({ slug, activeSessionId, projects, onLeave, onOpenSession, onNewChat, onProjectChanged, chatView }) {
  const [tab, setTab] = useState('chat')
  // Whether the Chat tab is showing the live conversation (nested
  // `chatView`, passed in from App.jsx -- there's only ever one real WS
  // connection/turn stream, so this just decides WHERE to render it) or
  // the card grid. Set by clicking a card / "+ 新建对话"; cleared by "←
  // 返回对话列表" -- that only changes what THIS view shows, the
  // underlying chat stays active either way, so clicking the same card
  // again just resumes looking at it.
  const [viewingChat, setViewingChat] = useState(false)
  const [project, setProject] = useState(null)
  const [summary, setSummary] = useState(null)
  const [sessions, setSessions] = useState([])
  const [files, setFiles] = useState(null)
  const [filesPath, setFilesPath] = useState('')
  const [filesError, setFilesError] = useState(null)
  // Bumped after any write op (upload/mkdir/rename/delete) to force the
  // files-fetch effect below to re-run without needing filesPath itself
  // to change.
  const [filesRefreshKey, setFilesRefreshKey] = useState(0)
  const fileInputRef = useRef(null)
  const [editingName, setEditingName] = useState(false)
  const [nameValue, setNameValue] = useState('')
  const [roleDraft, setRoleDraft] = useState('')
  const [stateDraft, setStateDraft] = useState('')
  const [memory, setMemory] = useState([])
  const [memoryDraft, setMemoryDraft] = useState('')
  const [toolsInfo, setToolsInfo] = useState(null)

  const refreshProject = () =>
    api
      .getProjects()
      .then((s) => {
        setProject(s.projects.find((p) => p.slug === slug) || null)
        onProjectChanged?.(s)
      })
      .catch(() => {})
  // Also syncs the Role/Current-state textarea drafts here (not via a
  // separate effect) -- this is the only place a fresh summary ever
  // lands, and doing it right in the same .then() (an event handler, not
  // a React effect) avoids a "setState in effect" render cascade and the
  // "missing dependency: summary" complaint a
  // `useEffect(() => setRoleDraft(...), [summary?.role])` would otherwise
  // need. Nothing else refetches summary on an interval, so this never
  // clobbers an in-progress, unsaved edit.
  const refreshSummary = () =>
    api
      .getProjectSummary(slug)
      .then((s) => {
        setSummary(s)
        setRoleDraft(s.role || '')
        setStateDraft(s.current_state || '')
      })
      .catch(() => {})
  const refreshSessions = () => api.getSessions(slug).then(setSessions).catch(() => {})
  const refreshMemory = () => api.getProjectMemory(slug).then(setMemory).catch(() => {})
  const refreshTools = () => api.getProjectTools(slug).then(setToolsInfo).catch(() => {})

  // App.jsx mounts this with `key={slug}`, so a different project is a
  // fresh instance (all state, including `tab`/`filesPath` below,
  // naturally resets) rather than this needing to manually reset them on
  // a `slug` change -- React's own recommended pattern for "reset state
  // when a prop changes." This effect only ever needs to run once.
  useEffect(() => {
    refreshProject()
    refreshSummary()
    refreshSessions()
    refreshMemory()
    refreshTools()
  }, [])

  useEffect(() => {
    if (tab !== 'files') return
    // `cancelled` guards against a stale response landing after a newer
    // request already started (rapid folder clicks) -- also sidesteps
    // ever calling setState synchronously at the top of the effect body,
    // clearing the previous error only once a real outcome is in hand.
    let cancelled = false
    api
      .getProjectFiles(slug, filesPath)
      .then((data) => {
        if (cancelled) return
        setFiles(data)
        setFilesError(null)
      })
      .catch((e) => {
        if (cancelled) return
        setFilesError(e.message)
      })
    return () => {
      cancelled = true
    }
  }, [tab, slug, filesPath, filesRefreshKey])

  const joinPath = (name) => (filesPath ? `${filesPath}/${name}` : name)

  const handleFileSelected = async (e) => {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    try {
      await api.uploadProjectFile(slug, filesPath, file)
      setFilesRefreshKey((k) => k + 1)
    } catch (err) {
      setFilesError(err.message)
    }
  }
  const createFolder = async () => {
    const name = window.prompt('新建文件夹名称：')
    if (!name || !name.trim()) return
    try {
      await api.mkdirProjectFile(slug, joinPath(name.trim()))
      setFilesRefreshKey((k) => k + 1)
    } catch (err) {
      setFilesError(err.message)
    }
  }
  const renameFile = async (f) => {
    const newName = window.prompt('新名称：', f.name)
    if (!newName || !newName.trim() || newName.trim() === f.name) return
    try {
      await api.renameProjectFile(slug, joinPath(f.name), newName.trim())
      setFilesRefreshKey((k) => k + 1)
    } catch (err) {
      setFilesError(err.message)
    }
  }
  const deleteFile = async (f) => {
    if (!window.confirm(`Delete '${f.name}'? This cannot be undone.`)) return
    try {
      await api.deleteProjectFile(slug, joinPath(f.name))
      setFilesRefreshKey((k) => k + 1)
    } catch (err) {
      setFilesError(err.message)
    }
  }

  const startRename = () => {
    setNameValue(project?.name || '')
    setEditingName(true)
  }
  const commitRename = async () => {
    const name = nameValue.trim()
    setEditingName(false)
    if (!name || name === project?.name) return
    try {
      await api.renameProject(slug, name)
      refreshProject()
    } catch {
      // Failed rename leaves the old name in place.
    }
  }

  const saveRole = async () => {
    try {
      await api.setProjectRole(slug, roleDraft)
      refreshSummary()
    } catch {
      // Failed save leaves the draft as-is so the user can retry.
    }
  }
  const saveState = async () => {
    try {
      await api.setProjectCurrentState(slug, stateDraft)
      refreshSummary()
    } catch {
      // Failed save leaves the draft as-is so the user can retry.
    }
  }
  const addMemory = async () => {
    const content = memoryDraft.trim()
    if (!content) return
    try {
      await api.addProjectMemory(slug, content)
      setMemoryDraft('')
      refreshMemory()
    } catch {
      // no-op on failure
    }
  }
  const editMemory = async (factId, content) => {
    try {
      await api.updateProjectMemory(slug, factId, content)
      refreshMemory()
    } catch {
      // no-op on failure
    }
  }
  const deleteMemory = async (factId) => {
    if (!window.confirm('Delete this memory? This cannot be undone.')) return
    try {
      await api.deleteProjectMemory(slug, factId)
      refreshMemory()
    } catch {
      // no-op on failure
    }
  }

  const toggleTool = async (pattern) => {
    if (!toolsInfo) return
    const enabled = toolsInfo.enabled.includes(pattern)
      ? toolsInfo.enabled.filter((p) => p !== pattern)
      : [...toolsInfo.enabled, pattern]
    try {
      await api.setProjectTools(slug, enabled)
      refreshTools()
    } catch {
      // no-op on failure
    }
  }

  const renameSession = async (id, title) => {
    try {
      await api.renameSession(id, title)
      refreshSessions()
    } catch {
      // no-op on failure
    }
  }
  const deleteSession = async (id) => {
    if (id === activeSessionId) return
    if (!window.confirm('Delete this chat? This cannot be undone.')) return
    try {
      await api.deleteSession(id)
      refreshSessions()
    } catch {
      // no-op on failure
    }
  }
  const togglePinSession = async (s) => {
    try {
      await (s.pinned ? api.unpinSession(s.id) : api.pinSession(s.id))
      refreshSessions()
    } catch {
      // no-op on failure
    }
  }
  const setSessionProject = async (id, targetSlug) => {
    try {
      await api.setSessionProject(id, targetSlug)
      refreshSessions()
      // Retagging the ACTIVE chat also syncs the Leader's active Project
      // server-side (gui/routes.py's set_session_project) -- refresh so
      // e.g. reassigning the current chat OUT of this project is
      // reflected immediately (the sidebar's Project summary included,
      // via onProjectChanged).
      refreshProject()
    } catch {
      // no-op on failure
    }
  }

  const openCard = (id) => {
    onOpenSession(id)
    setViewingChat(true)
  }
  const startNewChat = () => {
    onNewChat()
    setViewingChat(true)
  }

  const pathSegments = filesPath.split('/').filter(Boolean)

  // Rows to render in the Tools tab: every candidate the backend still
  // considers "available" (not yet globally granted), PLUS any pattern
  // this project already enabled that has since dropped out of that list
  // (e.g. it became globally granted in the meantime) -- shown so it can
  // still be seen/disabled rather than silently disappearing.
  const toolRows = toolsInfo
    ? [
        ...toolsInfo.available,
        ...toolsInfo.enabled
          .filter((pattern) => !toolsInfo.available.some((c) => c.pattern === pattern))
          .map((pattern) => ({ pattern, label: pattern, source: 'other' })),
      ]
    : []

  return (
    <div className="pd-root">
      <div className="pd-header">
        <div className="pd-header-top">
          <div className="pd-header-title-row">
            <span className="pd-header-icon">📁</span>
            <div style={{ minWidth: 0 }}>
              <div className="pd-header-name-row">
                {editingName ? (
                  <input
                    autoFocus
                    className="session-rename-input"
                    value={nameValue}
                    onChange={(e) => setNameValue(e.target.value)}
                    onBlur={commitRename}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') commitRename()
                      if (e.key === 'Escape') setEditingName(false)
                    }}
                  />
                ) : (
                  <h1 className="pd-header-name">{project?.name || slug}</h1>
                )}
                <button type="button" className="pd-icon-btn" onClick={startRename} title="重命名项目" aria-label="重命名项目">
                  ✎
                </button>
              </div>
              {project && <p className="pd-header-dir">{project.directory}</p>}
            </div>
          </div>
          <button type="button" className="btn btn-decline" onClick={onLeave}>
            离开项目
          </button>
        </div>
        {summary?.current_state && <p className="pd-header-summary">最近进展：{summary.current_state}</p>}
      </div>

      <div className="pd-tabbar">
        <button type="button" className={`pd-tab ${tab === 'chat' ? 'active' : ''}`} onClick={() => setTab('chat')}>
          💬 <span>Chat</span>
        </button>
        <button type="button" className={`pd-tab ${tab === 'context' ? 'active' : ''}`} onClick={() => setTab('context')}>
          🧠 <span>Context</span>
        </button>
        <button type="button" className={`pd-tab ${tab === 'tools' ? 'active' : ''}`} onClick={() => setTab('tools')}>
          🧩 <span>Tools</span>
        </button>
        <button type="button" className={`pd-tab ${tab === 'files' ? 'active' : ''}`} onClick={() => setTab('files')}>
          📁 <span>目录</span>
        </button>
      </div>

      <div className={`pd-body ${tab === 'chat' && viewingChat ? 'pd-body-chat-live' : ''}`}>
        {tab === 'chat' &&
          (viewingChat ? (
            <div className="pd-chat-live">
              <div className="pd-chat-live-header">
                <button type="button" className="pd-back-link" onClick={() => setViewingChat(false)}>
                  ← 返回对话列表
                </button>
              </div>
              {chatView}
            </div>
          ) : (
            <div>
              <div className="pd-chat-tab-header">
                <p className="pd-chat-count">{sessions.length} 个对话属于这个 Project</p>
                <button type="button" className="btn-accent" onClick={startNewChat}>
                  + 新建对话
                </button>
              </div>
              {sessions.length === 0 ? (
                <p className="session-list-empty">还没有对话，点击"+ 新建对话"开始。</p>
              ) : (
                <div className="pd-chat-grid">
                  {sessions.map((s) => (
                    <div
                      key={s.id}
                      className={`pd-chat-card ${s.id === activeSessionId ? 'active' : ''}`}
                      onClick={() => openCard(s.id)}
                    >
                      <div className="pd-chat-card-top">
                        {s.pinned && <span title="已固定">📌</span>}
                        <span className="pd-chat-card-title">{s.title}</span>
                        <SessionRowMenu
                          session={s}
                          projects={projects || []}
                          onRename={() => {
                            const title = window.prompt('新的标题：', s.title)
                            if (title && title.trim()) renameSession(s.id, title.trim())
                          }}
                          onDelete={() => deleteSession(s.id)}
                          onTogglePin={() => togglePinSession(s)}
                          onSetProject={(targetSlug) => setSessionProject(s.id, targetSlug)}
                        />
                      </div>
                      <span className="pd-chat-card-time">{relativeTime(s.updated_at)}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}

        {tab === 'context' && (
          <div className="pd-context">
            <div className="pd-context-section">
              <h3 className="pd-context-heading">Role</h3>
              <p className="pd-context-hint">
                How the AI should act in this project. AI-drafted (via update_project_summary) but always yours to edit.
              </p>
              <textarea
                className="pd-role-textarea"
                value={roleDraft}
                onChange={(e) => setRoleDraft(e.target.value)}
                placeholder="e.g. Act as a compliance research assistant focused on Southeast Asia..."
                rows={4}
              />
              <button type="button" className="btn btn-accent" onClick={saveRole} disabled={roleDraft === (summary?.role || '')}>
                保存 Role
              </button>
            </div>

            <div className="pd-context-section">
              <h3 className="pd-context-heading">Current State</h3>
              <p className="pd-context-hint">
                A short, standing snapshot of where this project stands -- replaced wholesale each update, not a log.
                Mention a notable produced file here in passing if relevant; the project's own 目录 tab is always the
                accurate, complete file list, so this never needs to duplicate it.
              </p>
              <textarea
                className="pd-role-textarea"
                value={stateDraft}
                onChange={(e) => setStateDraft(e.target.value)}
                placeholder="e.g. Drafted the initial compliance report, now reviewing Annex III scope..."
                rows={3}
              />
              <button
                type="button"
                className="btn btn-accent"
                onClick={saveState}
                disabled={stateDraft === (summary?.current_state || '')}
              >
                保存 Current State
              </button>
            </div>

            <div className="pd-context-section">
              <h3 className="pd-context-heading">Memory</h3>
              <p className="pd-context-hint">
                Durable facts, decisions, and context the AI (remember_project_fact) or you have saved -- not files or
                deliverables (those belong in the 目录 tab and get mentioned in Current State instead, not duplicated
                here). Not auto-injected -- the AI calls recall_project_facts when it needs one.
              </p>
              {memory.length === 0 ? (
                <p className="session-list-empty">还没有记忆条目。</p>
              ) : (
                <div className="pd-memory-list">
                  {memory.map((fact) => (
                    <div key={fact.id} className="pd-memory-row">
                      <span className="pd-memory-content">{fact.content}</span>
                      <div className="pd-memory-actions">
                        <button
                          type="button"
                          className="pd-icon-btn"
                          title="编辑"
                          aria-label="编辑"
                          onClick={() => {
                            const next = window.prompt('编辑记忆：', fact.content)
                            if (next && next.trim() && next.trim() !== fact.content) editMemory(fact.id, next.trim())
                          }}
                        >
                          ✎
                        </button>
                        <button
                          type="button"
                          className="pd-icon-btn"
                          title="删除"
                          aria-label="删除"
                          onClick={() => deleteMemory(fact.id)}
                        >
                          🗑
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
              <form
                className="pd-memory-add-form"
                onSubmit={(e) => {
                  e.preventDefault()
                  addMemory()
                }}
              >
                <input
                  type="text"
                  value={memoryDraft}
                  onChange={(e) => setMemoryDraft(e.target.value)}
                  placeholder="+ 添加一条记忆..."
                />
                <button type="submit" className="btn btn-accent" disabled={!memoryDraft.trim()}>
                  添加
                </button>
              </form>
            </div>
          </div>
        )}

        {tab === 'tools' && (
          <div className="pd-tools">
            <p className="pd-context-hint">
              Skill/MCP tools enabled for THIS project only, visible to the AI in addition to its usual tools while this
              project is active. Purely additive -- nothing here removes what's already available everywhere.
            </p>
            {toolRows.length === 0 ? (
              <p className="session-list-empty">
                没有可加载的 Skill/MCP（已安装的都已经全局授权，或者还没有安装任何新的）。
              </p>
            ) : (
              <div className="pd-tools-list">
                {toolRows.map((row) => (
                  <label key={row.pattern} className="pd-tools-row">
                    <input
                      type="checkbox"
                      checked={toolsInfo.enabled.includes(row.pattern)}
                      onChange={() => toggleTool(row.pattern)}
                    />
                    <span className="pd-tools-label">{row.label}</span>
                    <span className="pd-tools-source">{row.source}</span>
                  </label>
                ))}
              </div>
            )}
          </div>
        )}

        {tab === 'files' && (
          <div>
            <div className="pd-files-toolbar">
              <p className="pd-files-breadcrumb">
                <button type="button" className="pd-breadcrumb-link" onClick={() => setFilesPath('')}>
                  {project?.name || slug}
                </button>
                {pathSegments.map((seg, i) => (
                  <span key={i}>
                    {' '}
                    /{' '}
                    <button type="button" className="pd-breadcrumb-link" onClick={() => setFilesPath(pathSegments.slice(0, i + 1).join('/'))}>
                      {seg}
                    </button>
                  </span>
                ))}
              </p>
              <div className="pd-files-actions">
                <input ref={fileInputRef} type="file" style={{ display: 'none' }} onChange={handleFileSelected} />
                <button type="button" className="btn" onClick={() => fileInputRef.current?.click()}>
                  上传文件
                </button>
                <button type="button" className="btn" onClick={createFolder}>
                  新建文件夹
                </button>
              </div>
            </div>
            {filesError && <div className="panel-error">{filesError}</div>}
            {files && (
              <div className="pd-files-list">
                {files.length === 0 ? (
                  <p className="session-list-empty">空文件夹</p>
                ) : (
                  files.map((f) => (
                    <div
                      key={f.name}
                      className={`pd-file-row ${f.is_dir ? 'is-dir' : ''}`}
                      onClick={() => f.is_dir && setFilesPath(filesPath ? `${filesPath}/${f.name}` : f.name)}
                    >
                      <span className="pd-file-icon">{f.is_dir ? '📁' : '📄'}</span>
                      {f.is_dir ? (
                        <span className="pd-file-name">{f.name}</span>
                      ) : (
                        <a
                          className="pd-file-name pd-file-download"
                          href={api.projectFileDownloadUrl(slug, joinPath(f.name))}
                          onClick={(e) => e.stopPropagation()}
                        >
                          {f.name}
                        </a>
                      )}
                      <span className="pd-file-meta">{formatBytes(f.size)}</span>
                      <span className="pd-file-meta">{relativeTime(f.modified)}</span>
                      <button
                        type="button"
                        className="pd-icon-btn"
                        title="重命名"
                        aria-label="重命名"
                        onClick={(e) => {
                          e.stopPropagation()
                          renameFile(f)
                        }}
                      >
                        ✎
                      </button>
                      <button
                        type="button"
                        className="pd-icon-btn"
                        title="删除"
                        aria-label="删除"
                        onClick={(e) => {
                          e.stopPropagation()
                          deleteFile(f)
                        }}
                      >
                        🗑
                      </button>
                    </div>
                  ))
                )}
              </div>
            )}
          </div>
        )}

      </div>
    </div>
  )
}
