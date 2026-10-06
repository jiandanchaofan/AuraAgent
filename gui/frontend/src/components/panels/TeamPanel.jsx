import { useEffect, useState } from 'react'
import { api } from '../../api/rest'

export default function TeamPanel({ onChanged }) {
  const [agents, setAgents] = useState([])
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)

  const [name, setName] = useState('')
  const [systemPrompt, setSystemPrompt] = useState('')
  const [capsRaw, setCapsRaw] = useState('')

  const refresh = () =>
    api
      .getAgents()
      .then((a) => {
        setAgents(a)
        onChanged?.(a)
      })
      .catch((e) => setError(e.message))

  useEffect(() => {
    refresh()
  }, [])

  const submitAdd = async (e) => {
    e.preventDefault()
    setError(null)
    setNotice(null)
    const capabilities = capsRaw
      .split(',')
      .map((c) => c.trim())
      .filter(Boolean)
    try {
      const created = await api.addAgent(name, systemPrompt, capabilities)
      setNotice(`Added '${created.name}'. delegate_to_${created.name} is now available.`)
      setName('')
      setSystemPrompt('')
      setCapsRaw('')
      refresh()
    } catch (err) {
      setError(err.message)
    }
  }

  const remove = async (agentName) => {
    setError(null)
    setNotice(null)
    try {
      await api.removeAgent(agentName)
      setNotice(`Removed '${agentName}'.`)
      refresh()
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <div className="panel">
      <h2>Team</h2>
      {error && <div className="panel-error">{error}</div>}
      {notice && <div className="panel-notice">{notice}</div>}

      <section className="panel-section">
        <table className="roster">
          <thead>
            <tr>
              <th>Name</th>
              <th>Role</th>
              <th>Capabilities</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {agents.map((a) => (
              <tr key={a.name}>
                <td>{a.name}</td>
                <td>{a.role}</td>
                <td className="roster-caps">{a.capabilities.join(', ')}</td>
                <td>
                  {a.role !== 'leader' && (
                    <button type="button" className="btn btn-decline" onClick={() => remove(a.name)}>
                      Remove
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="panel-section">
        <h3>Add a worker</h3>
        <form className="panel-form panel-form-stacked" onSubmit={submitAdd}>
          <input type="text" placeholder="name" value={name} onChange={(e) => setName(e.target.value)} required />
          <textarea
            placeholder="system prompt"
            rows={3}
            value={systemPrompt}
            onChange={(e) => setSystemPrompt(e.target.value)}
            required
          />
          <input
            type="text"
            placeholder="capabilities, comma-separated (e.g. calculate,*task*)"
            value={capsRaw}
            onChange={(e) => setCapsRaw(e.target.value)}
            required
          />
          <button type="submit">Add worker</button>
        </form>
      </section>
    </div>
  )
}
