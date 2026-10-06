import { useEffect, useState } from 'react'
import { api } from '../../api/rest'

// MCP has no install/manage path from the GUI at all yet (see
// ToolsMenu.jsx's old equivalent, which this panel's content was lifted
// from verbatim) -- this is pure status, same as before, just given full
// room inside the Settings modal instead of a cramped sidebar summary.
export default function McpPanel() {
  const [servers, setServers] = useState([])
  const [error, setError] = useState(null)

  useEffect(() => {
    api.getMcpServers().then(setServers).catch((e) => setError(e.message))
  }, [])

  return (
    <div className="panel">
      <h2>MCP</h2>
      <p className="panel-status">Connected MCP servers and the tools they expose. No install flow here yet.</p>
      {error && <div className="panel-error">{error}</div>}

      <section className="panel-section">
        <h3>Connected servers</h3>
        {servers.length === 0 ? (
          <p className="session-list-empty">No MCP servers connected.</p>
        ) : (
          <ul className="tools-mini-list">
            {servers.map((s) => (
              <li key={s.name}>
                <code>{s.name}</code>
                <span className="tools-mini-hint">{s.tool_count} tool(s)</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}
