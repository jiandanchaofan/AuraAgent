import { useState } from 'react'
import FootprintTimeline from '../graph/FootprintTimeline'
import PersonsSection from '../graph/PersonsSection'
import ProjectsSection from '../graph/ProjectsSection'
import TrashSection from '../graph/TrashSection'

// Top-level "Footprint" tab (App.jsx renders this when tab === 'graph'),
// mirroring ProjectDetailPanel.jsx's internal-tabbar pattern for the
// Timeline/Persons/Projects/Trash sections of the Personal Data Graph
// (tools/personal_graph/graph_store.py). Trash is deliberately the last,
// least-emphasized tab here -- never a sidebar item -- same treatment
// flomo gives its own trash.
export default function GraphPanel({ syncSignal }) {
  const [section, setSection] = useState('timeline')

  return (
    <div className="pd-root">
      <div className="pd-header">
        <div className="pd-header-top">
          <div className="pd-header-title-row">
            <span className="pd-header-icon">🗒️</span>
            <h1 className="pd-header-name">Footprint</h1>
          </div>
        </div>
      </div>

      <div className="pd-tabbar">
        <button type="button" className={`pd-tab ${section === 'timeline' ? 'active' : ''}`} onClick={() => setSection('timeline')}>
          🗒️ <span>时间线</span>
        </button>
        <button type="button" className={`pd-tab ${section === 'persons' ? 'active' : ''}`} onClick={() => setSection('persons')}>
          🧑 <span>人物</span>
        </button>
        <button type="button" className={`pd-tab ${section === 'projects' ? 'active' : ''}`} onClick={() => setSection('projects')}>
          🏷️ <span>专项</span>
        </button>
        <button type="button" className={`pd-tab ${section === 'trash' ? 'active' : ''}`} onClick={() => setSection('trash')}>
          🗑 <span>回收站</span>
        </button>
      </div>

      <div className="pd-body">
        {section === 'timeline' && <FootprintTimeline syncSignal={syncSignal} />}
        {section === 'persons' && <PersonsSection syncSignal={syncSignal} />}
        {section === 'projects' && <ProjectsSection syncSignal={syncSignal} />}
        {section === 'trash' && <TrashSection syncSignal={syncSignal} />}
      </div>
    </div>
  )
}
