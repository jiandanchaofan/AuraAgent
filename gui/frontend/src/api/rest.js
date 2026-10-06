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

  getWorkspace: () => request('GET', '/api/workspace'),
  setWorkspace: (path) => request('POST', '/api/workspace', { path }),

  getNotes: () => request('GET', '/api/notes'),
  setNotes: (path) => request('POST', '/api/notes', { path }),
  setQuickNotesDir: (subdir) => request('POST', '/api/notes/quick-notes-dir', { subdir }),

  getCalendar: () => request('GET', '/api/calendar'),
  connectCalendar: () => request('POST', '/api/calendar/connect'),
  disconnectCalendar: () => request('POST', '/api/calendar/disconnect'),

  // Connection status only -- AuraAgent writes tasks into the user's real
  // Google Tasks account; there's no in-app task list/CRUD to wire up.
  getTasksBackend: () => request('GET', '/api/tasks/backend'),
  connectTasksBackend: () => request('POST', '/api/tasks/backend/connect'),
  disconnectTasksBackend: () => request('POST', '/api/tasks/backend/disconnect'),

  getProjects: () => request('GET', '/api/projects'),
  createProject: (slug, directory) => request('POST', '/api/projects', { slug, directory: directory || null }),
  useProject: (slug) => request('POST', `/api/projects/${encodeURIComponent(slug)}/use`),
  exitProject: () => request('POST', '/api/projects/none'),
  renameProject: (slug, name) => request('PATCH', `/api/projects/${encodeURIComponent(slug)}`, { name }),
  getProjectFiles: (slug, path) =>
    request('GET', `/api/projects/${encodeURIComponent(slug)}/files${path ? `?path=${encodeURIComponent(path)}` : ''}`),
  uploadProjectFile: async (slug, path, file) => {
    const form = new FormData()
    form.append('path', path || '')
    form.append('file', file)
    const res = await fetch(`/api/projects/${encodeURIComponent(slug)}/files/upload`, { method: 'POST', body: form })
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `Upload failed (${res.status})`)
    return res.json()
  },
  mkdirProjectFile: (slug, path) => request('POST', `/api/projects/${encodeURIComponent(slug)}/files/mkdir`, { path }),
  renameProjectFile: (slug, path, newName) =>
    request('PATCH', `/api/projects/${encodeURIComponent(slug)}/files`, { path, new_name: newName }),
  deleteProjectFile: (slug, path) =>
    request('DELETE', `/api/projects/${encodeURIComponent(slug)}/files?path=${encodeURIComponent(path)}`),
  projectFileDownloadUrl: (slug, path) =>
    `/api/projects/${encodeURIComponent(slug)}/files/download?path=${encodeURIComponent(path)}`,
  getProjectSummary: (slug) => request('GET', `/api/projects/${encodeURIComponent(slug)}/summary`),
  setProjectRole: (slug, role) => request('PATCH', `/api/projects/${encodeURIComponent(slug)}/role`, { role }),
  setProjectCurrentState: (slug, currentState) =>
    request('PATCH', `/api/projects/${encodeURIComponent(slug)}/state`, { current_state: currentState }),
  getProjectMemory: (slug) => request('GET', `/api/projects/${encodeURIComponent(slug)}/memory`),
  addProjectMemory: (slug, content) => request('POST', `/api/projects/${encodeURIComponent(slug)}/memory`, { content }),
  updateProjectMemory: (slug, factId, content) =>
    request('PATCH', `/api/projects/${encodeURIComponent(slug)}/memory/${encodeURIComponent(factId)}`, { content }),
  deleteProjectMemory: (slug, factId) =>
    request('DELETE', `/api/projects/${encodeURIComponent(slug)}/memory/${encodeURIComponent(factId)}`),
  getProjectTools: (slug) => request('GET', `/api/projects/${encodeURIComponent(slug)}/tools`),
  setProjectTools: (slug, enabled) => request('PUT', `/api/projects/${encodeURIComponent(slug)}/tools`, { enabled }),

  getSessions: (projectSlug) => request('GET', `/api/sessions${projectSlug ? `?project=${encodeURIComponent(projectSlug)}` : ''}`),
  getUnaffiliatedSessions: () => request('GET', '/api/sessions?unaffiliated=true'),
  renameSession: (id, title) => request('PATCH', `/api/sessions/${encodeURIComponent(id)}`, { title }),
  deleteSession: (id) => request('DELETE', `/api/sessions/${encodeURIComponent(id)}`),
  pinSession: (id) => request('POST', `/api/sessions/${encodeURIComponent(id)}/pin`),
  unpinSession: (id) => request('POST', `/api/sessions/${encodeURIComponent(id)}/unpin`),
  setSessionProject: (id, slug) => request('POST', `/api/sessions/${encodeURIComponent(id)}/project`, { slug }),
  setSessionDirectory: (id, directory) => request('POST', `/api/sessions/${encodeURIComponent(id)}/directory`, { directory }),

  getMcpServers: () => request('GET', '/api/mcp'),

  getSchedules: (projectSlug) =>
    request('GET', `/api/schedules${projectSlug ? `?project=${encodeURIComponent(projectSlug)}` : ''}`),
  createSchedule: (body) => request('POST', '/api/schedules', body),
  updateSchedule: (id, body) => request('PATCH', `/api/schedules/${encodeURIComponent(id)}`, body),
  deleteSchedule: (id) => request('DELETE', `/api/schedules/${encodeURIComponent(id)}`),

  // --- Personal Data Graph (N15/the "足迹" tab) -- gui/graph_routes.py.
  // Names avoid getProjects/createProject/renameProject etc. above, which
  // belong to the unrelated tools/projects/ accumulation-container feature;
  // "GraphProject" here is Auralis's #tag concept.
  listFootprints: (params = {}) => {
    const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== ''))
    const suffix = qs.toString() ? `?${qs.toString()}` : ''
    return request('GET', `/api/graph/footprints${suffix}`)
  },
  createFootprint: (body) => request('POST', '/api/graph/footprints', body),
  updateFootprint: (id, body) => request('PATCH', `/api/graph/footprints/${encodeURIComponent(id)}`, body),
  deleteFootprint: (id) => request('DELETE', `/api/graph/footprints/${encodeURIComponent(id)}`),
  dismissLink: (linkId) => request('POST', `/api/graph/links/${encodeURIComponent(linkId)}/dismiss`),

  listPersons: (keyword) => request('GET', `/api/graph/persons${keyword ? `?keyword=${encodeURIComponent(keyword)}` : ''}`),
  searchPersons: (keyword) => request('GET', `/api/graph/persons?keyword=${encodeURIComponent(keyword)}&limit=8`),
  createPerson: (body) => request('POST', '/api/graph/persons', body),
  updatePerson: (id, body) => request('PATCH', `/api/graph/persons/${encodeURIComponent(id)}`, body),
  deletePerson: (id) => request('DELETE', `/api/graph/persons/${encodeURIComponent(id)}`),

  listGraphProjects: (keyword) => request('GET', `/api/graph/projects${keyword ? `?keyword=${encodeURIComponent(keyword)}` : ''}`),
  searchGraphProjects: (keyword) => request('GET', `/api/graph/projects?keyword=${encodeURIComponent(keyword)}&limit=8`),
  createGraphProject: (body) => request('POST', '/api/graph/projects', body),
  updateGraphProject: (id, body) => request('PATCH', `/api/graph/projects/${encodeURIComponent(id)}`, body),
  deleteGraphProject: (id) => request('DELETE', `/api/graph/projects/${encodeURIComponent(id)}`),

  listTrash: (entity) => request('GET', `/api/graph/trash?entity=${encodeURIComponent(entity)}`),
  restoreEntity: (entity, id) => request('POST', `/api/graph/trash/${encodeURIComponent(entity)}/${encodeURIComponent(id)}/restore`),
}
