import { useEffect, useState } from 'react'
import { api } from '../../api/rest'

const ENTITIES = [
  { value: 'footprint', label: '足迹' },
  { value: 'person', label: '人物' },
  { value: 'project', label: '专项' },
]

function rowLabel(entity, row) {
  if (entity === 'footprint') return row.text
  if (entity === 'person') return row.name
  return `#${row.tag}`
}

// Minimal回收站 -- last/least-emphasized sub-tab in GraphPanel, same
// treatment flomo gives its own trash (a small corner, not a main nav
// item). Restore goes through GraphStore.restore_entity (a sibling of
// apply_op, see tools/personal_graph/graph_store.py).
export default function TrashSection({ syncSignal }) {
  const [entity, setEntity] = useState('footprint')
  const [rows, setRows] = useState([])
  const [error, setError] = useState(null)

  const refresh = () =>
    api
      .listTrash(entity)
      .then(setRows)
      .catch((e) => setError(e.message))

  useEffect(() => {
    refresh()
  }, [syncSignal, entity])

  const restore = async (id) => {
    try {
      await api.restoreEntity(entity, id)
      refresh()
    } catch (e) {
      setError(e.message)
    }
  }

  return (
    <div className="graph-section">
      <div className="trash-entity-switch">
        {ENTITIES.map((e) => (
          <button
            key={e.value}
            type="button"
            className={`trash-entity-btn ${entity === e.value ? 'active' : ''}`}
            onClick={() => setEntity(e.value)}
          >
            {e.label}
          </button>
        ))}
      </div>

      {error && <div className="panel-error">{error}</div>}

      {rows.length === 0 ? (
        <p className="session-list-empty">回收站是空的。</p>
      ) : (
        <div className="pd-memory-list">
          {rows.map((row) => (
            <div key={row.id} className="pd-memory-row">
              <span className="pd-memory-content">{rowLabel(entity, row)}</span>
              <div className="pd-memory-actions">
                <button type="button" className="btn" onClick={() => restore(row.id)}>
                  恢复
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
