// Light/dark mode -- a pure client-side preference (like the sidebar's
// collapsed state), not routed through cli/service.py/REST the way the
// rest of Settings is: there's nothing server-side to keep in sync, it's
// just which CSS variables index.css's `:root[data-theme="dark"]` block
// applies. Default is light (see index.css: the base `:root` IS the light
// palette, dark is the override) -- applyTheme() is still called once on
// startup so a returning user who picked dark doesn't see a light flash.
const STORAGE_KEY = 'aura.theme'

export function getStoredTheme() {
  try {
    const value = localStorage.getItem(STORAGE_KEY)
    return value === 'dark' ? 'dark' : 'light'
  } catch {
    return 'light'
  }
}

export function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme === 'dark' ? 'dark' : 'light')
  try {
    localStorage.setItem(STORAGE_KEY, theme)
  } catch {
    // Best-effort only -- a private window or blocked storage just means
    // the choice won't survive a reload, nothing more.
  }
}
