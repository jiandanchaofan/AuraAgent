import { useEffect, useState } from 'react'
import { api } from '../../api/rest'

// Simple searchable list + inline edit/delete -- deliberately NOT
// flomo-card styled (that treatment is for the footprint timeline only;
// person management is a plainer secondary view, per the user's own scoping
// of this feature).
export default function PersonsSection({ syncSignal }) {
  const [persons, setPersons] = useState([])
  const [keyword, setKeyword] = useState('')
  const [error, setError] = useState(null)
  const [expandedId, setExpandedId] = useState(null)
  const [draft, setDraft] = useState({ name: '', aliases: '', birthday: '', key_info: '' })
  const [newName, setNewName] = useState('')

  const refresh = () =>
    api
      .listPersons(keyword || undefined)
      .then(setPersons)
      .catch((e) => setError(e.message))

  useEffect(() => {
    refresh()
  }, [syncSignal, keyword])

  const expand = (person) => {
    setExpandedId(person.id)
    setDraft({
      name: person.name,
      aliases: (person.aliases || []).join(', '),
      birthday: person.birthday || '',
      key_info: person.key_info || '',
    })
  }

  const save = async (id) => {
    try {
      await api.updatePerson(id, {
        name: draft.name,
        aliases: draft.aliases ? draft.aliases.split(',').map((s) => s.trim()).filter(Boolean) : [],
        birthday: draft.birthday || undefined,
        key_info: draft.key_info || undefined,
      })
      setExpandedId(null)
      refresh()
    } catch (e) {
      setError(e.message)
    }
  }

  const remove = async (id) => {
    if (!window.confirm('删除这个人物？可以在回收站里恢复。')) return
    try {
      await api.deletePerson(id)
      refresh()
    } catch (e) {
      setError(e.message)
    }
  }

  const create = async (e) => {
    e.preventDefault()
    const name = newName.trim()
    if (!name) return
    try {
      await api.createPerson({ name })
      setNewName('')
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
        placeholder="搜索人物..."
        value={keyword}
        onChange={(e) => setKeyword(e.target.value)}
      />
      {error && <div className="panel-error">{error}</div>}

      {persons.length === 0 ? (
        <p className="session-list-empty">还没有人物记录。</p>
      ) : (
        <div className="pd-memory-list">
          {persons.map((p) => (
            <div key={p.id}>
              <div className="pd-memory-row" onClick={() => (expandedId === p.id ? setExpandedId(null) : expand(p))}>
                <span className="pd-memory-content">{p.name}</span>
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
                    placeholder="姓名"
                    value={draft.name}
                    onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                  />
                  <input
                    type="text"
                    placeholder="别名（逗号分隔）"
                    value={draft.aliases}
                    onChange={(e) => setDraft({ ...draft, aliases: e.target.value })}
                  />
                  <input
                    type="text"
                    placeholder="生日"
                    value={draft.birthday}
                    onChange={(e) => setDraft({ ...draft, birthday: e.target.value })}
                  />
                  <textarea
                    placeholder="关键信息"
                    value={draft.key_info}
                    onChange={(e) => setDraft({ ...draft, key_info: e.target.value })}
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
        <input type="text" placeholder="+ 新建人物" value={newName} onChange={(e) => setNewName(e.target.value)} />
        <button type="submit">添加</button>
      </form>
    </div>
  )
}
