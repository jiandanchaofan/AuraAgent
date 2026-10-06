import { useEffect, useRef, useState } from 'react'

// The three-dot dropdown on a Chat row (sidebar SessionList and the
// Project detail view's Chat tab share this same component/behavior) --
// Rename / Pin / Add to Project (submenu) / set a working directory /
// Delete. Click-outside and Escape both close it; "Add to Project" opens
// a second flyout listing every known project rather than a picker
// dialog, since the list is already available (passed in as a prop, not
// re-fetched here).
//
// "设置工作目录" only shows for a chat with NO Project tag -- once a
// Project is attached, its own directory always takes priority (see
// cli/service.py::sync_active_directory()'s priority chain), so offering
// the per-chat setting here too would just be confusing dead weight, not
// a second thing that also takes effect. The chat's own directory value
// (if it had one before joining a Project) is NOT cleared by joining one
// -- it silently resumes the moment the chat leaves the Project again --
// so hiding the menu item is purely about not showing a control that
// wouldn't currently do anything, not about the underlying value.
export default function SessionRowMenu({ session, projects, onRename, onDelete, onTogglePin, onSetProject, onSetDirectory }) {
  const [open, setOpen] = useState(false)
  const [projectSubmenuOpen, setProjectSubmenuOpen] = useState(false)
  const rootRef = useRef(null)

  useEffect(() => {
    if (!open) return
    const onDocMouseDown = (e) => {
      if (rootRef.current && !rootRef.current.contains(e.target)) {
        setOpen(false)
        setProjectSubmenuOpen(false)
      }
    }
    const onKeyDown = (e) => {
      if (e.key === 'Escape') {
        setOpen(false)
        setProjectSubmenuOpen(false)
      }
    }
    document.addEventListener('mousedown', onDocMouseDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('mousedown', onDocMouseDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  const close = () => {
    setOpen(false)
    setProjectSubmenuOpen(false)
  }

  return (
    <div className="session-menu-root" ref={rootRef} onClick={(e) => e.stopPropagation()}>
      <button
        type="button"
        className="session-menu-trigger"
        title="More"
        onClick={() => setOpen((v) => !v)}
      >
        ⋯
      </button>
      {open && (
        <div className="session-menu">
          <button
            type="button"
            className="session-menu-item"
            onClick={() => {
              close()
              onRename()
            }}
          >
            重命名
          </button>
          <button
            type="button"
            className="session-menu-item"
            onClick={() => {
              close()
              onTogglePin()
            }}
          >
            {session.pinned ? '取消固定' : '📌 固定'}
          </button>
          <div
            className="session-menu-submenu-root"
            onMouseEnter={() => setProjectSubmenuOpen(true)}
            onMouseLeave={() => setProjectSubmenuOpen(false)}
          >
            <button
              type="button"
              className="session-menu-item"
              onClick={() => setProjectSubmenuOpen((v) => !v)}
            >
              加入到 Project <span className="session-menu-caret">▸</span>
            </button>
            {projectSubmenuOpen && (
              <div className="session-menu session-menu-flyout">
                {session.project_slug && (
                  <button
                    type="button"
                    className="session-menu-item"
                    onClick={() => {
                      close()
                      onSetProject(null)
                    }}
                  >
                    移出 Project
                  </button>
                )}
                {projects.length === 0 ? (
                  <p className="session-menu-empty">还没有 Project</p>
                ) : (
                  projects
                    .filter((p) => p.slug !== session.project_slug)
                    .map((p) => (
                      <button
                        type="button"
                        key={p.slug}
                        className="session-menu-item"
                        onClick={() => {
                          close()
                          onSetProject(p.slug)
                        }}
                      >
                        {p.name}
                      </button>
                    ))
                )}
              </div>
            )}
          </div>
          {!session.project_slug && onSetDirectory && (
            <button
              type="button"
              className="session-menu-item"
              onClick={() => {
                close()
                const next = window.prompt(
                  '设置这个 Chat 的工作目录（任意真实目录，留空则清除、改用默认目录）：',
                  session.directory || ''
                )
                if (next === null) return // Cancel
                onSetDirectory(next.trim() || null)
              }}
            >
              📁 设置工作目录{session.directory ? `（${session.directory}）` : ''}
            </button>
          )}
          <div className="session-menu-divider" />
          <button
            type="button"
            className="session-menu-item session-menu-item-danger"
            onClick={() => {
              close()
              onDelete()
            }}
          >
            删除
          </button>
        </div>
      )}
    </div>
  )
}
