// Composer-side "/" command support -- the GUI's counterpart to
// cli/commands.py's dispatch_command(), calling the exact same REST
// endpoints (gui/routes.py) the sidebar panels use, so a typed command and
// its panel counterpart always do the same work.
//
// Deliberately NOT full 1:1 parity: anything that needs hidden input
// (/config set-key) or a mandatory "read the full source before approving"
// review step (/skills load|install) is redirected to its panel instead of
// accepted as plain composer text -- typing a secret or an install source
// into a chat box would be a real regression versus the CLI's getpass()/
// two-phase review, not an improvement. Simple read/write commands (view
// status, /workspace set, /agents remove, /project use, ...) work exactly
// like the CLI, including partition-not-split parsing so a Windows path
// containing spaces still works as one argument.

const HELP_TEXT = `Available commands:
  /help                            Show this help
  /config                          Show the current provider/model (key masked)
  /agents                          List the current team
  /agents remove <name>            Remove a worker agent
  /skills                          List installed skills
  /workspace                       Show the current workspace root directory
  /workspace set <path>            Switch the workspace root (persists to .env)
  /notes                           Show the current notes root directory
  /notes set <path>                Switch the notes root, e.g. an Obsidian vault
  /calendar                        Show the current calendar backend (local or google)
  /calendar connect                Connect a real Google Calendar (opens a browser for OAuth consent)
  /calendar disconnect             Switch back to the local JSON calendar
  /project                         Show the active project (if any) and list all projects
  /project create <slug> [path]    Create a project (auto directory, or an existing path)
  /project use <slug>              Enter a project -- session-only
  /project none                    Leave the active project
Anything needing hidden input or a source-code review (/config use|set-key,
/agents add, /skills load|install) opens the matching panel instead.`

function partition(s, sep) {
  const idx = s.indexOf(sep)
  if (idx === -1) return [s, '']
  return [s.slice(0, idx), s.slice(idx + sep.length)]
}

export function isSlashCommand(line) {
  return line.trim().startsWith('/')
}

/**
 * Runs one typed "/" command. Returns { text, isError }. May throw if the
 * underlying REST call fails with something other than a clean 4xx (the
 * caller is expected to catch and render that as an error entry too).
 */
export async function runSlashCommand(line, { api, setTab }) {
  const stripped = line.trim()
  const [command, restRaw] = partition(stripped, ' ')
  const rest = restRaw.trim()

  switch (command) {
    case '/help':
      return { text: HELP_TEXT }

    case '/config': {
      if (!rest) {
        const c = await api.getConfig()
        return { text: `provider=${c.provider} model=${c.model} key=${c.key_masked}` }
      }
      setTab('settings')
      return { text: 'Switching provider or setting a key needs a form (hidden input) — opened Settings for you.' }
    }

    case '/agents': {
      if (!rest) {
        const list = await api.getAgents()
        const lines = list.map((a) => `${a.role === 'leader' ? '*' : ' '} ${a.name} (${a.role}) -- ${a.capabilities.join(', ')}`)
        return { text: lines.join('\n') }
      }
      const [sub, arg] = partition(rest, ' ')
      if (sub === 'remove' && arg.trim()) {
        await api.removeAgent(arg.trim())
        return { text: `Removed '${arg.trim()}'.` }
      }
      setTab('team')
      return { text: 'Adding a worker needs a form — opened Team for you.' }
    }

    case '/skills': {
      if (!rest) {
        const list = await api.getSkills()
        return { text: list.length ? list.map((s) => `${s.name} -- ${s.description}`).join('\n') : '(no skills installed)' }
      }
      setTab('skills')
      return { text: 'Installing a Skill needs a source review — opened Skills for you.' }
    }

    case '/workspace': {
      if (!rest) {
        const s = await api.getWorkspace()
        return { text: `workspace=${s.path}` }
      }
      const [sub, arg] = partition(rest, ' ')
      if (sub === 'set' && arg.trim()) {
        const s = await api.setWorkspace(arg.trim())
        return { text: `Workspace set to '${s.path}'. Saved — persists across restarts.` }
      }
      return { text: 'Usage: /workspace | /workspace set <path>', isError: true }
    }

    case '/notes': {
      if (!rest) {
        const s = await api.getNotes()
        return { text: `notes=${s.path}` }
      }
      const [sub, arg] = partition(rest, ' ')
      if (sub === 'set' && arg.trim()) {
        const s = await api.setNotes(arg.trim())
        return { text: `Notes root set to '${s.path}'. Saved — persists across restarts.` }
      }
      return { text: 'Usage: /notes | /notes set <path>', isError: true }
    }

    case '/calendar': {
      if (!rest) {
        const s = await api.getCalendar()
        return { text: `backend=${s.backend}` }
      }
      if (rest === 'connect') {
        const s = await api.connectCalendar()
        return { text: `Connected to Google Calendar (backend=${s.backend}). Active immediately.` }
      }
      if (rest === 'disconnect') {
        const s = await api.disconnectCalendar()
        return { text: `Disconnected. Back to the local calendar (backend=${s.backend}).` }
      }
      return { text: 'Usage: /calendar | /calendar connect | /calendar disconnect', isError: true }
    }

    case '/project': {
      if (!rest || rest === 'list') {
        const s = await api.getProjects()
        if (!s.projects.length) return { text: 'No projects yet. Use /project create <slug> to make one.' }
        const lines = s.projects.map((p) => `${p.slug === s.active_slug ? '*' : ' '} ${p.slug} -- ${p.directory}`)
        if (!s.active_slug) lines.push('(no project active)')
        return { text: lines.join('\n') }
      }
      const [sub, arg] = partition(rest, ' ')
      if (sub === 'create' && arg.trim()) {
        const [slug, path] = partition(arg.trim(), ' ')
        const project = await api.createProject(slug, path.trim() || undefined)
        return { text: `Created project '${project.slug}' at '${project.directory}'. Use /project use ${project.slug} to enter it.` }
      }
      if (sub === 'use' && arg.trim()) {
        const project = await api.useProject(arg.trim())
        return { text: `Entered project '${project.slug}'. Active immediately, no restart needed.` }
      }
      if (rest === 'none') {
        await api.exitProject()
        return { text: 'Left the active project. Workspace restored to what it was before.' }
      }
      return { text: 'Usage: /project | /project list | /project create <slug> [path] | /project use <slug> | /project none', isError: true }
    }

    default:
      return { text: `Unknown command '${command}'. Type /help for a list of commands.`, isError: true }
  }
}
