import { docxHeuristics, renderDocx } from '../docx.js'
import { resolveRoot, table, textOutput } from './common.js'

export function createDocxTool(ctx) {
  return {
    name: 'patent_docx',
    description:
      '把撰写好的 Markdown 排成 CNIPA 风格 Word（A4、宋体小四、1.5 倍行距、标题黑体、首行缩进），可嵌入附图并自动分页。kind=application 时可按目录中的 权利要求书.md / 说明书.md / 说明书摘要.md / figures/*.png 一次出四件套；kind=disclosure 出交底书；kind=opinion 出意见陈述。',
    parameters: {
      type: 'object',
      additionalProperties: false,
      properties: {
        kind: { type: 'string', enum: ['disclosure', 'application', 'opinion'], description: '文档类型' },
        caseId: { type: 'string', description: '案件号；提供后输出到案件 drafts 时间戳目录并登记' },
        dir: { type: 'string', description: 'kind=application 时的申请文件目录（含三份 md 与 figures/）' },
        outDir: { type: 'string', description: '可选输出目录；省略按案件自动建时间戳目录' },
        documents: {
          type: 'array',
          description: '直接提供文档（kind 非 application 或未给 dir 时）',
          items: {
            type: 'object',
            additionalProperties: true,
            properties: {
              name: { type: 'string', description: '文件名，如 说明书 / 权利要求书 / 意见陈述' },
              title: { type: 'string', description: '封面标题' },
              markdown: { type: 'string', description: 'Markdown 正文' },
              markdownPath: { type: 'string', description: '或从文件读取 Markdown' },
              indentFirstLine: { type: 'boolean', description: '正文首行缩进，默认 true；权利要求书设 false' },
              pageNumbers: { type: 'boolean', description: '页脚页码，默认 true' },
              meta: { type: 'array', items: { type: 'object', additionalProperties: true }, description: '著录项 [{label,value}]' },
              figures: {
                type: 'array',
                items: {
                  type: 'object',
                  additionalProperties: true,
                  properties: {
                    path: { type: 'string' },
                    caption: { type: 'string' },
                    widthMm: { type: 'number' },
                  },
                },
              },
            },
          },
        },
      },
      required: ['kind'],
    },
    output: textOutput((value) => {
      if (!value.ok) return `Word 生成失败：${value.error}`
      const lines = [
        `# Word 已生成（${value.kind}）`,
        '',
        `输出目录：${value.outDir}`,
        '',
        table(['文件', '段落', '图片', '路径'], (value.files || []).map((file) => [
          file.name, file.paragraphs ?? '—', file.images ?? 0, file.path,
        ])),
      ]
      if (value.warnings?.length) {
        lines.push('', '## 提示', '')
        value.warnings.slice(0, 12).forEach((warning) => lines.push(`- ${warning}`))
      }
      return lines.join('\n')
    }),
    async execute(args, exec) {
      const root = await resolveRoot(ctx, exec)
      try {
        const value = await renderDocx(root, args)
        const warnings = [...(value.warnings || []), ...docxHeuristics(args.documents || [])]
        return { ...value, warnings: [...new Set(warnings)] }
      } catch (error) {
        return { ok: false, error: error.message }
      }
    },
  }
}
