import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { runPython } from '../python.js'
import { extractOfficeAction, noticeMarkdown, oaResponseChecklist } from '../oa.js'
import { loadConfig, loadDocket, findCase, casePaths, ensureCaseDirs, workbenchPaths, updateCase } from '../store.js'
import { ensureDir, nowStamp, writeJson, writeText } from '../util.js'
import { resolveRoot, textOutput } from './common.js'

const EXTRACT = fileURLToPath(new URL('../../engine/extract.py', import.meta.url))

export function createOaTool(ctx) {
  return {
    name: 'patent_oa_extract',
    description:
      '从审查意见通知书/补正通知书/审查员邮件（PDF、DOCX、TXT、EML）提取申请号、发文日、审查员、对比文件、法条、缺陷类型、逐条意见与答复期限，落盘 notice.json / notice.md / 答复工作清单，并把案件转入审查状态。提取是机械辅助，必须对照原文复核。',
    parameters: {
      type: 'object',
      additionalProperties: false,
      properties: {
        path: { type: 'string', description: '通知书文件路径（pdf/docx/txt/eml）' },
        text: { type: 'string', description: '或直接给通知书正文；优先用 path' },
        caseId: { type: 'string', description: '关联案件号；提供后写案件 oa 目录并更新案卷' },
        patentType: { type: 'string', enum: ['invention', 'utility', 'design'], description: '专利类型；省略按通知书/案件推断' },
        noticeDate: { type: 'string', description: '发文日 YYYY-MM-DD；通知书未识别时用' },
        responseDue: { type: 'string', description: '通知书指定的答复截止日 YYYY-MM-DD' },
        save: { type: 'boolean', description: '是否落盘，默认 true' },
      },
    },
    output: textOutput((value) => {
      if (!value.ok) return `审查意见提取失败：${value.error}`
      const lines = [value.markdown || '']
      lines.push('')
      lines.push(`- 结构化结果：${value.noticePath || '未落盘'}`)
      lines.push(`- 答复清单：${value.checklistPath || '未落盘'}`)
      if (value.warnings?.length) {
        lines.push('', '## 抽取警告', '')
        value.warnings.forEach((warning) => lines.push(`- ${warning}`))
      }
      return lines.join('\n')
    }),
    async execute(args, exec) {
      const root = await resolveRoot(ctx, exec)
      try {
        const config = await loadConfig(root)
        let text = String(args.text || '')
        const warnings = []
        let sourcePath = ''
        if (!text && args.path) {
          sourcePath = path.resolve(root, args.path)
          const extracted = await runPython(EXTRACT, {
            action: 'file',
            path: sourcePath,
            maxChars: 200000,
          }, { timeoutMs: 120000 })
          const file = extracted.files?.[0]
          if (!file) throw new Error('文件没有可读文本')
          text = file.text || ''
          warnings.push(...(file.warnings || []))
        }
        if (!text.trim()) throw new Error('没有可提取的文本；扫描件请先 OCR 或提供可复制文字的 PDF/DOCX')
        const docket = await loadDocket(root)
        const caseItem = args.caseId ? findCase(docket, args.caseId) : null
        if (args.caseId && !caseItem) throw new Error(`未找到案件：${args.caseId}`)
        const patentType = args.patentType || caseItem?.patentType || undefined
        const result = extractOfficeAction(text, {
          patentType,
          responseMonths: config.responseMonths,
          serviceDays: config.serviceDays,
          responseDue: args.responseDue,
          noticeDate: args.noticeDate,
        })
        const markdown = noticeMarkdown(result)
        let noticePath = ''
        let checklistPath = ''
        let noticeJsonPath = ''
        if (args.save !== false) {
          let dir
          if (caseItem) {
            const dirs = await ensureCaseDirs(root, caseItem.id)
            dir = path.join(dirs.oa, nowStamp())
          } else {
            dir = path.join(workbenchPaths(root).dataDir, 'oa', nowStamp())
          }
          await ensureDir(dir)
          noticePath = path.join(dir, 'notice.md')
          noticeJsonPath = path.join(dir, 'notice.json')
          checklistPath = path.join(dir, '答复工作清单.md')
          await writeText(noticePath, markdown)
          await writeJson(noticeJsonPath, result)
          await writeText(checklistPath, oaResponseChecklist(result))
          if (caseItem) {
            await updateCase(root, caseItem.id, {
              status: 'oa',
              examiner: result.examiner || caseItem.examiner,
              oa: {
                receivedDate: result.noticeDate || caseItem.oa?.receivedDate || '',
                responseDue: args.responseDue || result.responseDue || caseItem.oa?.responseDue || '',
                round: Number(caseItem.oa?.round || 0) + 1,
                statutes: result.statutes.map((statute) => statute.article),
                noticePath: path.relative(root, noticePath),
              },
            }, { type: 'oa_received', note: `收到${result.noticeKindLabel}，提取 ${result.items.length} 条意见` })
          }
        }
        return {
          ok: true,
          ...result,
          markdown,
          sourcePath,
          noticePath,
          noticeJsonPath,
          checklistPath,
          warnings: [...warnings, ...result.reviewFlags],
        }
      } catch (error) {
        return { ok: false, error: error.message }
      }
    },
  }
}
