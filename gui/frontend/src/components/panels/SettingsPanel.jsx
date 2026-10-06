import { useEffect, useState } from 'react'
import { api } from '../../api/rest'
import { applyTheme, getStoredTheme } from '../../lib/theme'

export default function SettingsPanel() {
  const [config, setConfig] = useState(null)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)

  const [provider, setProvider] = useState('anthropic')
  const [model, setModel] = useState('')
  const [keyProvider, setKeyProvider] = useState('anthropic')
  const [keyValue, setKeyValue] = useState('')

  // Appearance: purely client-side (see lib/theme.js) -- no REST call, no
  // server-side state to keep in sync.
  const [theme, setTheme] = useState(getStoredTheme)
  const chooseTheme = (next) => {
    setTheme(next)
    applyTheme(next)
  }

  const refresh = () => api.getConfig().then(setConfig).catch((e) => setError(e.message))

  useEffect(() => {
    refresh()
  }, [])

  const submitUse = async (e) => {
    e.preventDefault()
    setError(null)
    setNotice(null)
    try {
      const updated = await api.useProvider(provider, model)
      setConfig(updated)
      setNotice(`Switched to ${updated.provider} / ${updated.model}.`)
    } catch (err) {
      setError(err.message)
    }
  }

  const submitKey = async (e) => {
    e.preventDefault()
    setError(null)
    setNotice(null)
    try {
      await api.setKey(keyProvider, keyValue)
      setKeyValue('')
      setNotice(`Saved API key for ${keyProvider}. Use the form above to switch to it.`)
      refresh()
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <div className="panel">
      <h2>Settings</h2>
      {config && (
        <p className="panel-status">
          Current: <code>{config.provider}</code> / <code>{config.model}</code> (key {config.key_masked})
        </p>
      )}
      {error && <div className="panel-error">{error}</div>}
      {notice && <div className="panel-notice">{notice}</div>}

      <section className="panel-section">
        <h3>Appearance</h3>
        <div className="theme-toggle">
          <button
            type="button"
            className={`theme-option ${theme === 'light' ? 'active' : ''}`}
            onClick={() => chooseTheme('light')}
          >
            Light
          </button>
          <button
            type="button"
            className={`theme-option ${theme === 'dark' ? 'active' : ''}`}
            onClick={() => chooseTheme('dark')}
          >
            Dark
          </button>
        </div>
        <p className="panel-hint">Saved in this browser only. Default is Light.</p>
      </section>

      <section className="panel-section">
        <h3>Switch provider</h3>
        <form className="panel-form" onSubmit={submitUse}>
          <select value={provider} onChange={(e) => setProvider(e.target.value)}>
            <option value="anthropic">anthropic</option>
            <option value="openai">openai (also serves DeepSeek etc.)</option>
          </select>
          <input
            type="text"
            placeholder="model id, e.g. claude-opus-5 or deepseek-chat"
            value={model}
            onChange={(e) => setModel(e.target.value)}
            required
          />
          <button type="submit">Switch</button>
        </form>
      </section>

      <section className="panel-section">
        <h3>Set / update an API key</h3>
        <form className="panel-form" onSubmit={submitKey}>
          <select value={keyProvider} onChange={(e) => setKeyProvider(e.target.value)}>
            <option value="anthropic">anthropic</option>
            <option value="openai">openai</option>
          </select>
          <input
            type="password"
            autoComplete="off"
            placeholder="API key"
            value={keyValue}
            onChange={(e) => setKeyValue(e.target.value)}
            required
          />
          <button type="submit">Save</button>
        </form>
        <p className="panel-hint">Written straight to .env and kept in memory — never sent to the LLM or logged.</p>
      </section>
    </div>
  )
}
