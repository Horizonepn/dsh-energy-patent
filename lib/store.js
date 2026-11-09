import path from 'node:path'
import {
  DATA_DIR_NAME, asciiSlug, addDays, addMonths, daysBetween, dateOnly,
  nowIso, readJson, safeFileName, table, writeJson, writeText, ensureDir, parseDate,
} from './util.js'

export const DEFAULT_CONFIG = {
  responseMonths: { invention: 4, utility: 2, design: 2 },
  serviceDays: 15,
  annuityWarningDays: 60,
  mailConfigPaths: ['patent-mail.config.json', 'outputs/patent-workbench/patent-mail.config.json'],
}

export function workbenchPaths(root) {
  const dataDir = path.join(root, 'outputs', DATA_DIR_NAME)
  return {
    dataDir,
    docketFile: path.join(dataDir, 'docket.json'),
    trackerFile: path.join(dataDir, 'TRACKER.md'),
    casesDir: path.join(dataDir, 'cases'),
    mailDir: path.join(dataDir, 'mail'),
    configFile: path.join(root, 'patent-workbench.config.json'),
  }
}

export function casePaths(root, caseId) {
  const dir = path.join(workbenchPaths(root).casesDir, caseId)
  return {
    dir,
    materials: path.join(dir, 'materials'),
    drafts: path.join(dir, 'drafts'),
    figures: path.join(dir, 'figures'),
    oa: path.join(dir, 'oa'),
    mail: path.join(dir, 'mail'),
    caseFile: path.join(dir, 'case.json'),
  }
}

export async function ensureCaseDirs(root, caseId) {
  const p = casePaths(root, caseId)
  await Promise.all([p.materials, p.drafts, p.figures, p.oa, p.mail].map((dir) => ensureDir(dir)))
  return p
}

export async function loadConfig(root) {
  const saved = await readJson(workbenchPaths(root).configFile, {})
  return {
    ...DEFAULT_CONFIG,
    ...saved,
    responseMonths: { ...DEFAULT_CONFIG.responseMonths, ...(saved.responseMonths || {}) },
    mailConfigPaths: Array.isArray(saved.mailConfigPaths) && saved.mailConfigPaths.length
      ? saved.mailConfigPaths
      : DEFAULT_CONFIG.mailConfigPaths,
  }
}

export async function loadDocket(root) {
  const docket = await readJson(workbenchPaths(root).docketFile, null)
  if (!docket || !Array.isArray(docket.cases)) {
    return { version: 1, updatedAt: nowIso(), cases: [] }
  }
  return docket
}

export async function saveDocket(root, docket) {
  docket.updatedAt = nowIso()
  const p = workbenchPaths(root)
  await writeJson(p.docketFile, docket)
  const config = await loadConfig(root)
  await writeText(p.trackerFile, renderTracker(docket, config))
  return docket
}

export function findCase(docket, caseId) {
  return docket.cases.find((item) => item.id === caseId)
}

export function newCaseId(title) {
  const stamp = dateOnly().replace(/-/g, '')
  return `${stamp}-${asciiSlug(title, 'case')}`.slice(0, 80)
}

export async function createCase(root, input) {
  const docket = await loadDocket(root)
  const autoId = !input.caseId
  let id = String(input.caseId || '').trim() || newCaseId(input.title)
  id = asciiSlug(id, newCaseId(input.title)).slice(0, 80)
  if (findCase(docket, id)) {
    if (!autoId) throw new Error(`案件已存在：${id}`)
    let n = 2
    while (findCase(docket, `${id}-${n}`)) n += 1
    id = `${id}-${n}`
  }
  const now = nowIso()
  const item = {
    id,
    title: String(input.title || '未命名案件').trim(),
    patentType: input.patentType || 'invention',
    status: input.status || 'drafting',
    applicant: input.applicant || '',
    inventors: Array.isArray(input.inventors) ? input.inventors.map(String) : [],
    applicationNo: input.applicationNo || '',
    publicationNo: input.publicationNo || '',
    filingDate: input.filingDate || '',
    examiner: input.examiner || '',
    notes: input.notes || '',
    createdAt: now,
    updatedAt: now,
    archived: false,
    materials: null,
    figures: [],
    drafts: [],
    oa: { receivedDate: '', responseDue: '', round: 0, statutes: [], noticePath: '' },
    dueDates: [],
    events: [{ at: now, type: 'created', note: `创建案件：${input.title || id}` }],
  }
  docket.cases.push(item)
  await saveDocket(root, docket)
  await ensureCaseDirs(root, id)
  return item
}

export async function updateCase(root, caseId, patch = {}, event = null) {
  const docket = await loadDocket(root)
  const item = findCase(docket, caseId)
  if (!item) throw new Error(`未找到案件：${caseId}`)
  const allowed = [
    'title', 'patentType', 'status', 'applicant', 'inventors', 'applicationNo',
    'publicationNo', 'filingDate', 'examiner', 'notes', 'archived', 'materials',
  ]
  for (const key of allowed) {
    if (patch[key] !== undefined) item[key] = patch[key]
  }
  if (patch.oa && typeof patch.oa === 'object') item.oa = { ...item.oa, ...patch.oa }
  if (Array.isArray(patch.dueDates)) item.dueDates = patch.dueDates
  item.updatedAt = nowIso()
  if (event) {
    item.events = Array.isArray(item.events) ? item.events : []
    item.events.push({ at: nowIso(), type: event.type || 'note', note: event.note || '' })
  }
  await saveDocket(root, docket)
  return item
}

export async function appendEvent(root, caseId, type, note) {
  return updateCase(root, caseId, {}, { type, note })
}

export function addFigure(root, caseId, figure) {
  return updateCase(root, caseId, {}, { type: 'figure', note: `生成附图 ${figure.id}` }).then(async (item) => {
    const docket = await loadDocket(root)
    const current = findCase(docket, caseId)
    if (current) {
      current.figures = Array.isArray(current.figures) ? current.figures : []
      current.figures = current.figures.filter((f) => f.id !== figure.id)
      current.figures.push(figure)
      await saveDocket(root, docket)
    }
    return item
  })
}

export function addDraft(root, caseId, draft) {
  return updateCase(root, caseId, {}, { type: 'draft', note: `生成 ${draft.kind}：${draft.name || ''}` }).then(async () => {
    const docket = await loadDocket(root)
    const current = findCase(docket, caseId)
    if (current) {
      current.drafts = Array.isArray(current.drafts) ? current.drafts : []
      current.drafts.push({ ...draft, createdAt: nowIso() })
      await saveDocket(root, docket)
    }
    return current
  })
}

export async function addDrafts(root, caseId, drafts) {
  if (!Array.isArray(drafts) || !drafts.length) return null
  const docket = await loadDocket(root)
  const current = findCase(docket, caseId)
  if (!current) throw new Error(`未找到案件：${caseId}`)
  current.drafts = Array.isArray(current.drafts) ? current.drafts : []
  for (const draft of drafts) {
    current.drafts.push({ ...draft, createdAt: nowIso() })
  }
  current.updatedAt = nowIso()
  current.events = Array.isArray(current.events) ? current.events : []
  current.events.push({
    at: nowIso(),
    type: 'draft',
    note: `生成 ${drafts.length} 份${drafts[0].kind || ''}文件：${drafts.map((draft) => draft.name).join('、')}`,
  })
  await saveDocket(root, docket)
  return current
}

export function computeDeadlines(caseItem, config) {
  const today = dateOnly()
  const items = []
  const oa = caseItem.oa || {}
  if (oa.responseDue) {
    items.push({ label: '审查意见答复期限', date: oa.responseDue, source: 'oa', basis: '已记录/按通知书计算' })
  } else if (oa.receivedDate) {
    const months = config.responseMonths[caseItem.patentType] ?? 4
    const due = addMonths(addDays(oa.receivedDate, config.serviceDays), months)
    items.push({
      label: '审查意见答复期限（估算）',
      date: due,
      source: 'oa',
      basis: `发文日+${config.serviceDays}日推定送达+${months}个月；以通知书指定期限为准`,
    })
  }
  if (caseItem.filingDate) {
    const filing = parseDate(caseItem.filingDate)
    if (filing) {
      const now = parseDate(today)
      let year = now.getFullYear()
      let anniversary = new Date(year, filing.getMonth(), filing.getDate())
      if (anniversary < now) anniversary = new Date(year + 1, filing.getMonth(), filing.getDate())
      items.push({
        label: '下一年度年费（申请日周年）',
        date: dateOnly(anniversary),
        source: 'annuity',
        basis: '按申请日周年估算；缴费窗口与滞纳金以国知局缴费通知为准',
      })
    }
    if (!caseItem.applicationNo) {
      const priority = addMonths(caseItem.filingDate, 12)
      if (priority) items.push({ label: '优先权期限（12个月）', date: priority, source: 'priority', basis: '自申请日起12个月' })
    }
  }
  for (const due of caseItem.dueDates || []) {
    if (due && due.date) {
      items.push({ label: due.label || '自定义期限', date: due.date, source: 'custom', basis: due.note || '' })
    }
  }
  for (const item of items) {
    const days = daysBetween(today, item.date)
    item.daysLeft = days
    item.level = days === null ? 'unknown' : days < 0 ? 'overdue' : days <= 30 ? 'urgent' : days <= 90 ? 'soon' : 'later'
  }
  items.sort((a, b) => String(a.date).localeCompare(String(b.date)))
  return items
}

export function nextDeadline(caseItem, config) {
  const items = computeDeadlines(caseItem, config)
  return items.find((item) => item.daysLeft !== null && item.daysLeft >= 0) || items[0] || null
}

export function renderTracker(docket, config = DEFAULT_CONFIG) {
  const rows = []
  const cases = [...docket.cases].sort((a, b) => String(a.id).localeCompare(String(b.id)))
  for (const item of cases) {
    if (item.archived) continue
    const next = nextDeadline(item, config)
    rows.push([
      item.id,
      item.title,
      item.patentType === 'utility' ? '实用新型' : item.patentType === 'design' ? '外观' : '发明',
      statusZh(item.status),
      item.applicationNo || '—',
      item.examiner || '—',
      next ? `${next.date}（${next.daysLeft}天）` : '—',
    ])
  }
  const lines = [
    '# 专利案卷台账',
    '',
    `> 生成时间：${nowIso().replace('T', ' ').slice(0, 19)}　案件数：${rows.length}`,
    '',
    rows.length ? table(['案件号', '名称', '类型', '状态', '申请号', '审查员', '下一期限'], rows) : '（暂无案件）',
    '',
    '期限为按规则估算的提醒，不替代国知局通知书与代理机构期限管理。',
  ]
  return `${lines.join('\n')}\n`
}

export function statusZh(status) {
  const map = {
    drafting: '撰写中', review: '内部复核', filed: '已申请', oa: '审查中',
    granted: '已授权', rejected: '已驳回', abandoned: '已放弃', archived: '已归档',
  }
  return map[status] || status || '—'
}

export function caseSummary(item, config) {
  const next = nextDeadline(item, config)
  return {
    id: item.id,
    title: item.title,
    patentType: item.patentType,
    status: item.status,
    statusZh: statusZh(item.status),
    applicationNo: item.applicationNo,
    examiner: item.examiner,
    nextDeadline: next,
    updatedAt: item.updatedAt,
  }
}

export function draftFileName(title, stamp) {
  return `${safeFileName(title, 'patent')}_${stamp}`
}
