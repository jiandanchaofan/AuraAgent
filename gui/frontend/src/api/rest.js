// Thin fetch wrapper for gui/routes.py's REST endpoints (Epic M3). Same
// origin as the page in production (gui/server.py serves both); proxied
// by Vite in dev (see vite.config.js's server.proxy).
async function request(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  const data = await res.json().catch(() => null)
  if (!res.ok) {
    throw new Error(data?.detail || `${method} ${path} failed (${res.status})`)
  }
  return data
}

export const api = {
  getConfig: () => request('GET', '/api/config'),
  useProvider: (provider, model) => request('POST', '/api/config/use', { provider, model }),
  setKey: (provider, value) => request('POST', '/api/config/set-key', { provider, value }),

  getAgents: () => request('GET', '/api/agents'),
  addAgent: (name, systemPrompt, capabilities) =>
    request('POST', '/api/agents', { name, system_prompt: systemPrompt, capabilities }),
  removeAgent: (name) => request('DELETE', `/api/agents/${encodeURIComponent(name)}`),

  getSkills: () => request('GET', '/api/skills'),
  stageSkillFromUrl: (url) => request('POST', '/api/skills/stage', { source: 'url', url }),
  stageSkillFromUpload: (contentBase64) =>
    request('POST', '/api/skills/stage', { source: 'upload', content_base64: contentBase64 }),
  commitSkill: (stagingId) => request('POST', '/api/skills/commit', { staging_id: stagingId }),
}
