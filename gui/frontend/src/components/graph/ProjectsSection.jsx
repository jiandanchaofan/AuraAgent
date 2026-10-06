import { useEffect, useState } from 'react'
import { api } from '../../api/rest'

// Same shape as PersonsSection.jsx, for Auralis's #tag "project"
// concept -- unrelated to this repo's own tools/projects/ accumulation
// containers (see api/rest.js's naming note).
export default function ProjectsSection({ syncSignal }) {
  const [projects, setProjects] = useState([])
  const [keyword, setKeyword] = useState('')
  const [error, setError] = useState(null)
  const [expandedId, setExpandedId] = useState(null)
  const [draft, setDraft] = useState({ tag: '', name: '', goal: '' })
  const [newTag, setNewTag] = useState('')

  const refresh = () =>
    api
      .listGraphProjects(keyword || undefined)
      .then(setProjects)
      .catch((e) => setError(e.message))

  useEffect(() => {
    refresh()
  }, [syncSignal, keyword])

  const expand = (project) => {
    setExpandedId(project.id)
    setDraft({ tag: project.tag, name: project.name || '', goal: project.goal || '' })
  }

  const save = async (id) => {
    try {
      await api.updateGraphProject(id, { name: draft.name || undefined, goal: draft.goal || undefined })
      setExpandedId(null)
      refresh()
    } catch (e) {
      setError(e.message)
    }
  }

  const remove = async (id) => {
    if (!window.confirm('删除这个专项？可以在回收站里恢复。')) return
    try {
      await api.deleteGraphProject(id)
      refresh()
    } catch (e) {
      setError(e.message)
    }
  }

  const create = async (e) => {
    e.preventDefault()
    const tag = newTag.trim()
    if (!tag) return
    try {
      await api.createGraphProject({ tag })
      setNewTag('')
      refresh()
    } catch (e2) {
      setError(e2.message)
    }
  }

  return (
    <div className="graph-section">
      <input
        type="text"
        className="footprint-search"
        placeholder="搜索专项..."
        value={keyword}
        onChange={(e) => setKeyword(e.target.value)}
      />
      {error && <div className="panel-error">{error}</div>}

      {projects.length === 0 ? (
        <p className="session-list-empty">还没有专项记录。</p>
      ) : (
        <div className="pd-memory-list">
          {projects.map((p) => (
            <div key={p.id}>
              <div className="pd-memory-row" onClick={() => (expandedId === p.id ? setExpandedId(null) : expand(p))}>
                <span className="pd-memory-content">
                  #{p.tag} {p.name && <span className="footprint-card-time">({p.name})</span>}
                </span>
                <div className="pd-memory-actions">
                  <button
                    type="button"
                    className="pd-icon-btn"
                    title="删除"
                    aria-label="删除"
                    onClick={(e) => {
                      e.stopPropagation()
                      remove(p.id)
                    }}
                  >
                    🗑
                  </button>
                </div>
              </div>
              {expandedId === p.id && (
                <div className="graph-detail-form">
                  <input
                    type="text"
                    placeholder="名称"
                    value={draft.name}
                    onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                  />
                  <textarea
                    placeholder="目标"
                    value={draft.goal}
                    onChange={(e) => setDraft({ ...draft, goal: e.target.value })}
                    rows={2}
                  />
                  <button type="button" className="btn btn-accent" onClick={() => save(p.id)}>
                    保存
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      <form className="panel-form" onSubmit={create}>
        <input type="text" placeholder="+ 新建专项 (#tag)" value={newTag} onChange={(e) => setNewTag(e.target.value)} />
        <button type="submit">添加</button>
      </form>
    </div>
  )
}
