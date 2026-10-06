import { useEffect, useState } from 'react'
import { api } from '../../api/rest'
import QuickEntryBox from './QuickEntryBox'
import FootprintCard from './FootprintCard'

// The flomo-style main view: quick-entry box on top, reverse-chronological
// card feed below. Re-pulls via REST on every `syncSignal` bump (the
// AI-enrichment task's own completion signal included) -- never reads data
// off the WS event itself, per Auralis's "REST carries data, WS carries
// only signals" design (see gui/graph_routes.py).
export default function FootprintTimeline({ syncSignal }) {
  const [footprints, setFootprints] = useState([])
  const [error, setError] = useState(null)
  const [keyword, setKeyword] = useState('')
  const [personFilter, setPersonFilter] = useState(null) // {id, name} | null
  const [projectFilter, setProjectFilter] = useState(null) // tag | null

  const refresh = () =>
    api
      .listFootprints({ person_id: personFilter?.id, project_tag: projectFilter, keyword: keyword || undefined })
      .then(setFootprints)
      .catch((e) => setError(e.message))

  useEffect(() => {
    refresh()
  }, [syncSignal, personFilter, projectFilter, keyword])

  const filterByPerson = (personId) => {
    const match = footprints.flatMap((f) => f.links?.persons || []).find((p) => p.id === personId)
    setPersonFilter({ id: personId, name: match?.name || personId })
    setProjectFilter(null)
  }

  const filterByProject = (tag) => {
    setProjectFilter(tag)
    setPersonFilter(null)
  }

  const clearFilter = () => {
    setPersonFilter(null)
    setProjectFilter(null)
  }

  return (
    <div className="footprint-timeline">
      <QuickEntryBox onCreated={refresh} />

      <div className="footprint-timeline-toolbar">
        <input
          type="text"
          className="footprint-search"
          placeholder="搜索足迹..."
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
        />
        {(personFilter || projectFilter) && (
          <span className="footprint-active-filter">
            筛选：{personFilter ? `@${personFilter.name}` : `#${projectFilter}`}
            <button type="button" className="graph-chip-remove" onClick={clearFilter} aria-label="清除筛选">
              ×
            </button>
          </span>
        )}
      </div>

      {error && <div className="panel-error">{error}</div>}

      {footprints.length === 0 ? (
        <p className="session-list-empty">还没有足迹，在上面记一笔吧。</p>
      ) : (
        <div className="footprint-feed">
          {footprints.map((f) => (
            <FootprintCard
              key={f.id}
              footprint={f}
              onChanged={refresh}
              onFilterPerson={filterByPerson}
              onFilterProject={filterByProject}
            />
          ))}
        </div>
      )}
    </div>
  )
}
