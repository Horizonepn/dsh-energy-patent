import {
  computeDeadlines, createCase, findCase, loadConfig, loadDocket, renderTracker,
  statusZh, updateCase, workbenchPaths,
} from '../store.js'
import { writeText } from '../util.js'
import { resolveRoot, table, textOutput } from './common.js'

const ACTIONS = ['create', 'list', 'get', 'update', 'event', 'deadlines', 'tracker', 'archive']

function caseLine(item, config) {
  const next = computeDeadlines(item, config)[0]
  return {
    id: item.id,
    title: item.title,
    type: item.patentType === 'utility' ? '实用新型' : item.patentType === 'design' ? '外观' : '发明',
    status: statusZh(item.status),
    applicationNo: item.applicationNo || '—',
    examiner: item.examiner || '—',
    next: next ? `${next.date}（${next.daysLeft}天，${next.label}）` : '—',
    updatedAt: String(item.updatedAt || '').slice(0, 10),
  }
}

function caseMarkdown(item, config) {
  const deadlines = computeDeadlines(item, config)
  const lines = [
    `# ${item.title}`,
    '',
    `- 案件号：${item.id}`,
    `- 类型：${item.patentType === 'utility' ? '实用新型' : item.patentType === 'design' ? '外观设计' : '发明'}`,
    `- 状态：${statusZh(item.status)}`,
    `- 申请人：${item.applicant || '—'}`,
    `- 发明人：${(item.inventors || []).join('、') || '—'}`,
    `- 申请号：${item.applicationNo || '—'}`,
    `- 公布号：${item.publicationNo || '—'}`,
    `- 申请日：${item.filingDate || '—'}`,
    `- 审查员：${item.examiner || '—'}`,
    `- 审查轮次：${item.oa?.round || 0}`,
  ]
  if (deadlines.length) {
    lines.push('', '## 期限', '')
    lines.push(table(['期限', '日期', '剩余', '依据'], deadlines.map((due) => [
      due.label, due.date, due.daysLeft === null ? '—' : `${due.daysLeft} 天`, due.basis || '—',
    ])))
  }
  if (item.materials?.digestPath) {
    lines.push('', '## 资料', '')
    lines.push(`- 摘要：${item.materials.digestPath}（${item.materials.files || 0} 个文件，${item.materials.chars || 0} 字）`)
    if (item.materials.branches?.length) lines.push(`- 领域：${item.materials.branches.join('、')}`)
  }
  if (item.figures?.length || item.drafts?.length) {
    lines.push('', '## 产出', '')
    if (item.figures?.length) {
      lines.push(`- 附图：${item.figures.map((figure) => `${figure.id}（${figure.kind}）`).join('、')}`)
    }
    if (item.drafts?.length) {
      const recent = item.drafts.slice(-6).map((draft) => `${draft.name || draft.kind}`)
      lines.push(`- 文档：${recent.join('、')}${item.drafts.length > 6 ? ` 等 ${item.drafts.length} 份` : ''}`)
    }
  }
  if (item.notes) lines.push('', '## 备注', '', item.notes)
  if (item.events?.length) {
    lines.push('', '## 最近事件', '')
    item.events.slice(-8).reverse().forEach((event) => {
      lines.push(`- ${String(event.at || '').replace('T', ' ').slice(0, 16)}　${event.type}　${event.note}`)
    })
  }
  return lines.join('\n')
}

export function createCaseTool(ctx) {
  return {
    name: 'patent_case',
    description:
      '维护专利案卷台账：创建/查询/更新案件、记录事件、计算审查答复与年费期限、生成 TRACKER.md。每次撰写、递交、收到通知书或答复后都应更新案件状态与事件，让“自动维护专利”有据可查。',
    parameters: {
      type: 'object',
      additionalProperties: false,
      properties: {
        action: { type: 'string', enum: ACTIONS, description: '操作类型' },
        caseId: { type: 'string', description: '案件号（create 可省略，自动生成）' },
        title: { type: 'string', description: '案件名称' },
        patentType: { type: 'string', enum: ['invention', 'utility', 'design'] },
        status: {
          type: 'string',
          enum: ['drafting', 'review', 'filed', 'oa', 'granted', 'rejected', 'abandoned', 'archived'],
          description: '撰写中/内部复核/已申请/审查中/已授权/已驳回/已放弃/已归档',
        },
        applicant: { type: 'string' },
        inventors: { type: 'array', items: { type: 'string' } },
        applicationNo: { type: 'string' },
        publicationNo: { type: 'string' },
        filingDate: { type: 'string', description: 'YYYY-MM-DD' },
        examiner: { type: 'string' },
        notes: { type: 'string' },
        eventType: { type: 'string', description: '事件类型，如 draft / filed / oa_received / response_sent / mail' },
        eventNote: { type: 'string', description: '事件说明' },
        oaReceivedDate: { type: 'string', description: '审查意见发文日 YYYY-MM-DD' },
        oaResponseDue: { type: 'string', description: '通知书指定答复期限 YYYY-MM-DD' },
        oaRound: { type: 'integer', minimum: 1, description: '审查轮次' },
        query: { type: 'string', description: 'list 时的名称/申请号过滤词' },
        includeArchived: { type: 'boolean', description: 'list 是否包含归档案件' },
        limit: { type: 'integer', minimum: 1, maximum: 200 },
      },
      required: ['action'],
    },
    output: textOutput((value) => {
      if (!value.ok) return `案卷操作失败：${value.error}`
      if (value.markdown) return value.markdown
      if (value.rows) {
        return [`# 专利案卷（${value.rows.length} 件）`, '', table(
          ['案件号', '名称', '类型', '状态', '申请号', '下一期限'],
          value.rows.map((row) => [row.id, row.title, row.type, row.status, row.applicationNo, row.next]),
        )].join('\n')
      }
      return JSON.stringify(value, null, 2)
    }),
    async execute(args, exec) {
      const root = await resolveRoot(ctx, exec)
      const config = await loadConfig(root)
      const action = String(args.action || '').toLowerCase()
      try {
        if (action === 'create') {
          if (!args.title) throw new Error('create 需要 title')
          const item = await createCase(root, args)
          return { ok: true, case: item, markdown: caseMarkdown(item, config) }
        }
        if (action === 'list') {
          const docket = await loadDocket(root)
          const query = String(args.query || '').toLowerCase()
          const limit = Number(args.limit || 100)
          let cases = docket.cases.filter((item) => args.includeArchived || !item.archived)
          if (args.status) cases = cases.filter((item) => item.status === args.status)
          if (query) {
            cases = cases.filter((item) => `${item.id} ${item.title} ${item.applicationNo}`.toLowerCase().includes(query))
          }
          const rows = cases.map((item) => caseLine(item, config)).slice(0, limit)
          return { ok: true, rows, markdown: [`# 专利案卷（${rows.length} 件）`, '', table(
            ['案件号', '名称', '类型', '状态', '申请号', '下一期限'],
            rows.map((row) => [row.id, row.title, row.type, row.status, row.applicationNo, row.next]),
          )].join('\n') }
        }
        if (action === 'get') {
          const docket = await loadDocket(root)
          const item = findCase(docket, args.caseId)
          if (!item) throw new Error(`未找到案件：${args.caseId}`)
          return { ok: true, case: item, deadlines: computeDeadlines(item, config), markdown: caseMarkdown(item, config) }
        }
        if (action === 'update') {
          const patch = {}
          for (const key of [
            'title', 'patentType', 'status', 'applicant', 'inventors', 'applicationNo',
            'publicationNo', 'filingDate', 'examiner', 'notes',
          ]) {
            if (args[key] !== undefined) patch[key] = args[key]
          }
          if (args.oaReceivedDate || args.oaResponseDue || args.oaRound) {
            patch.oa = {
              ...(args.oaReceivedDate ? { receivedDate: args.oaReceivedDate } : {}),
              ...(args.oaResponseDue ? { responseDue: args.oaResponseDue } : {}),
              ...(args.oaRound ? { round: Number(args.oaRound) } : {}),
            }
          }
          const item = await updateCase(root, args.caseId, patch, args.eventType || args.eventNote
            ? { type: args.eventType || 'update', note: args.eventNote || '更新案件' }
            : null)
          return { ok: true, case: item, deadlines: computeDeadlines(item, config), markdown: caseMarkdown(item, config) }
        }
        if (action === 'event') {
          if (!args.eventType) throw new Error('event 需要 eventType')
          const item = await updateCase(root, args.caseId, {}, { type: args.eventType, note: args.eventNote || '' })
          return { ok: true, case: item, markdown: caseMarkdown(item, config) }
        }
        if (action === 'deadlines') {
          const docket = await loadDocket(root)
          const rows = []
          for (const item of docket.cases) {
            if (item.archived) continue
            for (const due of computeDeadlines(item, config)) {
              if (due.daysLeft === null) continue
              if (due.daysLeft < -30) continue
              rows.push({
                caseId: item.id,
                title: item.title,
                label: due.label,
                date: due.date,
                daysLeft: due.daysLeft,
                level: due.level,
              })
            }
          }
          rows.sort((a, b) => a.daysLeft - b.daysLeft)
          const lines = [
            '# 专利期限雷达',
            '',
            table(['案件', '期限', '日期', '剩余', '级别'], rows.map((row) => [
              `${row.caseId} ${row.title}`, row.label, row.date, `${row.daysLeft} 天`, row.level,
            ])),
            '',
            '期限为规则估算，务必以国知局通知书/缴费通知为准。',
          ]
          return { ok: true, rows, markdown: lines.join('\n') }
        }
        if (action === 'tracker') {
          const docket = await loadDocket(root)
          const content = renderTracker(docket, config)
          const file = workbenchPaths(root).trackerFile
          await writeText(file, content)
          return { ok: true, path: file, markdown: content }
        }
        if (action === 'archive') {
          const item = await updateCase(root, args.caseId, { archived: true, status: 'archived' }, {
            type: 'archive', note: args.eventNote || '归档',
          })
          return { ok: true, case: item, markdown: caseMarkdown(item, config) }
        }
        throw new Error(`未知 action：${action}`)
      } catch (error) {
        return { ok: false, error: error.message }
      }
    },
  }
}

export { caseMarkdown, caseLine }
