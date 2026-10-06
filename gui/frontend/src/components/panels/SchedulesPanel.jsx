import { useEffect, useState } from 'react'
import { api } from '../../api/rest'

const WEEKDAY_LABELS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']

// Client-side recurrence -> cron translation, covering exactly the
// granularities the friendly builder below exposes (hourly/daily/weekly
// incl. multiple weekdays/monthly/yearly). The backend only ever stores
// the resulting cron string -- see tools/scheduler/schedule_store.py --
// so this is purely a UI convenience, not a second schema.
function buildCron({ frequency, time, weekdays, dayOfMonth, month }) {
  const [hh, mm] = (time || '09:00').split(':').map(Number)
  switch (frequency) {
    case 'hourly':
      return `${mm} * * * *`
    case 'weekly':
      return `${mm} ${hh} * * ${weekdays.length ? [...weekdays].sort().join(',') : '1'}`
    case 'monthly':
      return `${mm} ${hh} ${dayOfMonth || 1} * *`
    case 'yearly':
      return `${mm} ${hh} ${dayOfMonth || 1} ${month || 1} *`
    case 'daily':
    default:
      return `${mm} ${hh} * * *`
  }
}

// This is the centralized management surface -- ALL schedules across
// every project, plus unaffiliated ones, in one place (not nested inside
// any single Project's detail page). Reachable from the sidebar's Tools
// section ("Schedules" subgroup -> Manage).
export default function SchedulesPanel({ projects }) {
  const [schedules, setSchedules] = useState([])
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)

  const [task, setTask] = useState('')
  const [triggerType, setTriggerType] = useState('recurring')
  const [runAt, setRunAt] = useState('')
  const [frequency, setFrequency] = useState('daily')
  const [time, setTime] = useState('09:00')
  const [weekdays, setWeekdays] = useState([1])
  const [dayOfMonth, setDayOfMonth] = useState(1)
  const [month, setMonth] = useState(1)
  const [projectSlug, setProjectSlug] = useState('')

  const refresh = () => api.getSchedules().then(setSchedules).catch((e) => setError(e.message))

  useEffect(() => {
    refresh()
  }, [])

  const toggleWeekday = (d) => {
    setWeekdays((prev) => (prev.includes(d) ? prev.filter((x) => x !== d) : [...prev, d]))
  }

  const submitCreate = async (e) => {
    e.preventDefault()
    setError(null)
    setNotice(null)
    const body = { task: task.trim(), trigger_type: triggerType, project_slug: projectSlug || null }
    if (triggerType === 'once') {
      if (!runAt) {
        setError('请选择执行时间。')
        return
      }
      body.run_at = runAt
    } else {
      body.cron_expression = buildCron({ frequency, time, weekdays, dayOfMonth, month })
    }
    try {
      await api.createSchedule(body)
      setNotice('已创建。')
      setTask('')
      refresh()
    } catch (err) {
      setError(err.message)
    }
  }

  const toggleEnabled = async (s) => {
    try {
      await api.updateSchedule(s.id, { enabled: !s.enabled })
      refresh()
    } catch (err) {
      setError(err.message)
    }
  }

  const remove = async (s) => {
    if (!window.confirm(`删除这条任务？\n${s.task}`)) return
    try {
      await api.deleteSchedule(s.id)
      refresh()
    } catch (err) {
      setError(err.message)
    }
  }

  const editTask = async (s) => {
    const next = window.prompt('新的任务内容：', s.task)
    if (!next || !next.trim() || next.trim() === s.task) return
    try {
      await api.updateSchedule(s.id, { task: next.trim() })
      refresh()
    } catch (err) {
      setError(err.message)
    }
  }

  const projectLabel = (slug) => {
    if (!slug) return '（未关联）'
    const p = projects?.find((x) => x.slug === slug)
    return p ? p.name || p.slug : slug
  }

  return (
    <div className="panel">
      <h2>Schedules</h2>
      {error && <div className="panel-error">{error}</div>}
      {notice && <div className="panel-notice">{notice}</div>}

      <section className="panel-section">
        {schedules.length === 0 ? (
          <p className="panel-hint">还没有任何定时任务——在下面创建一个。</p>
        ) : (
          <table className="roster">
            <thead>
              <tr>
                <th>任务</th>
                <th>时间</th>
                <th>Project</th>
                <th>下次执行</th>
                <th>上次结果</th>
                <th>启用</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {schedules.map((s) => (
                <tr key={s.id}>
                  <td className="roster-caps schedule-task-cell" title={`点击编辑：${s.task}`} onClick={() => editTask(s)}>
                    {s.task}
                  </td>
                  <td>{s.description}</td>
                  <td>{projectLabel(s.project_slug)}</td>
                  <td>{s.next_run_at ? new Date(s.next_run_at).toLocaleString() : '—'}</td>
                  <td className="roster-caps" title={s.last_result_summary || ''}>
                    {s.last_result_summary ? s.last_result_summary.slice(0, 40) : '—'}
                  </td>
                  <td>
                    <input type="checkbox" checked={s.enabled} onChange={() => toggleEnabled(s)} />
                  </td>
                  <td>
                    <button type="button" className="btn btn-decline" onClick={() => remove(s)}>
                      删除
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="panel-section">
        <h3>新建任务</h3>
        <form className="panel-form panel-form-stacked" onSubmit={submitCreate}>
          <input
            type="text"
            placeholder="任务内容，例如：调研 OpenAI 最近一周的动态，写成一份洞察文档"
            value={task}
            onChange={(e) => setTask(e.target.value)}
            required
          />

          <div className="schedule-trigger-row">
            <label>
              <input type="radio" checked={triggerType === 'once'} onChange={() => setTriggerType('once')} /> 一次性
            </label>
            <label>
              <input type="radio" checked={triggerType === 'recurring'} onChange={() => setTriggerType('recurring')} /> 重复
            </label>
          </div>

          {triggerType === 'once' ? (
            <input type="datetime-local" value={runAt} onChange={(e) => setRunAt(e.target.value)} required />
          ) : (
            <div className="schedule-recurrence-builder">
              <select value={frequency} onChange={(e) => setFrequency(e.target.value)}>
                <option value="hourly">每小时</option>
                <option value="daily">每天</option>
                <option value="weekly">每周</option>
                <option value="monthly">每月</option>
                <option value="yearly">每年</option>
              </select>
              {frequency !== 'hourly' && <input type="time" value={time} onChange={(e) => setTime(e.target.value)} />}
              {frequency === 'weekly' && (
                <div className="schedule-weekday-picker">
                  {WEEKDAY_LABELS.map((label, i) => (
                    <label key={i}>
                      <input type="checkbox" checked={weekdays.includes(i)} onChange={() => toggleWeekday(i)} />
                      {label}
                    </label>
                  ))}
                </div>
              )}
              {(frequency === 'monthly' || frequency === 'yearly') && (
                <input
                  type="number"
                  min="1"
                  max="31"
                  value={dayOfMonth}
                  onChange={(e) => setDayOfMonth(Number(e.target.value))}
                  placeholder="几号"
                />
              )}
              {frequency === 'yearly' && (
                <input
                  type="number"
                  min="1"
                  max="12"
                  value={month}
                  onChange={(e) => setMonth(Number(e.target.value))}
                  placeholder="几月"
                />
              )}
            </div>
          )}

          <select value={projectSlug} onChange={(e) => setProjectSlug(e.target.value)}>
            <option value="">（不关联任何 Project）</option>
            {(projects || []).map((p) => (
              <option key={p.slug} value={p.slug}>
                {p.name || p.slug}
              </option>
            ))}
          </select>

          <button type="submit">创建</button>
        </form>
        <p className="panel-hint">这个任务会在没有你在场的情况下执行；执行过程中任何原本需要确认的操作都会被自动拒绝。</p>
      </section>
    </div>
  )
}
