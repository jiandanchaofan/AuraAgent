import { useEffect, useRef, useState } from 'react'
import { api } from '../../api/rest'

function arrayBufferToBase64(buffer) {
  let binary = ''
  const bytes = new Uint8Array(buffer)
  for (let i = 0; i < bytes.byteLength; i++) binary += String.fromCharCode(bytes[i])
  return btoa(binary)
}

export default function SkillsPanel() {
  const [skills, setSkills] = useState([])
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [staged, setStaged] = useState(null) // {staging_id, name, skill_md_text, run_py_text, warnings}
  const [url, setUrl] = useState('')
  const fileInputRef = useRef(null)

  const refresh = () => api.getSkills().then(setSkills).catch((e) => setError(e.message))

  useEffect(() => {
    refresh()
  }, [])

  const stageFromUrl = async (e) => {
    e.preventDefault()
    setError(null)
    setNotice(null)
    try {
      setStaged(await api.stageSkillFromUrl(url))
      setUrl('')
    } catch (err) {
      setError(err.message)
    }
  }

  const stageFromUpload = async (e) => {
    const file = e.target.files?.[0]
    if (!file) return
    setError(null)
    setNotice(null)
    try {
      const buffer = await file.arrayBuffer()
      setStaged(await api.stageSkillFromUpload(arrayBufferToBase64(buffer)))
    } catch (err) {
      setError(err.message)
    } finally {
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  const commit = async () => {
    if (!staged) return
    setError(null)
    try {
      const result = await api.commitSkill(staged.staging_id)
      setNotice(`Installed and loaded '${result.name}'. It persists across restarts.`)
      setStaged(null)
      refresh()
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <div className="panel">
      <h2>Skills</h2>
      {error && <div className="panel-error">{error}</div>}
      {notice && <div className="panel-notice">{notice}</div>}

      <section className="panel-section">
        <ul className="skill-list">
          {skills.map((s) => (
            <li key={s.name}>
              <code>{s.name}</code>
              <span className="skill-desc">{s.description}</span>
            </li>
          ))}
          {skills.length === 0 && <li className="skill-empty">No skills installed yet.</li>}
        </ul>
      </section>

      {!staged && (
        <section className="panel-section">
          <h3>Install from a URL</h3>
          <form className="panel-form" onSubmit={stageFromUrl}>
            <input
              type="text"
              placeholder="https://example.com/skill.zip"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              required
            />
            <button type="submit">Fetch &amp; review</button>
          </form>

          <h3>Or upload a local .zip</h3>
          <input ref={fileInputRef} type="file" accept=".zip" onChange={stageFromUpload} />
        </section>
      )}

      {staged && (
        <section className="panel-section">
          <div className="hitl-card skill-review-card">
            <div className="hitl-header">
              <span className="hitl-badge" style={{ background: '#d98c2b' }}>
                Code Execution
              </span>
              <span className="hitl-tool">{staged.name}</span>
            </div>
            <p className="panel-hint">
              This code will run on your machine with the SAME PERMISSIONS as AuraAgent itself, every time this
              skill is called, with NO sandbox. Read it before installing.
            </p>
            {staged.warnings.length > 0 && (
              <div className="skill-warnings">
                <strong>Static scan found potentially risky patterns:</strong>
                <ul>
                  {staged.warnings.map((w, i) => (
                    <li key={i}>{w}</li>
                  ))}
                </ul>
              </div>
            )}
            <details open>
              <summary>SKILL.md</summary>
              <pre className="hitl-reason">{staged.skill_md_text}</pre>
            </details>
            <details open>
              <summary>run.py</summary>
              <pre className="hitl-reason">{staged.run_py_text}</pre>
            </details>
            <div className="hitl-actions">
              <button type="button" className="btn btn-decline" onClick={() => setStaged(null)}>
                Cancel
              </button>
              <button type="button" className="btn btn-approve" onClick={commit}>
                Install
              </button>
            </div>
          </div>
        </section>
      )}
    </div>
  )
}
