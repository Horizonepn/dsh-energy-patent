import { figureHeuristics, renderFigure } from '../figures.js'
import { resolveRoot, textOutput } from './common.js'

export function createFigureTool(ctx) {
  return {
    name: 'patent_figure',
    description:
      '直接生成中国专利黑白附图（PNG 300dpi + SVG）：flow=方法流程图（框内写“步骤一…”），block=系统/装置框图（虚线分组+模块），chart=黑白数据图。图号与图题不画进图里，由说明书“附图说明”编号。生成后自动登记到案件附图清单。',
    parameters: {
      type: 'object',
      additionalProperties: false,
      properties: {
        kind: { type: 'string', enum: ['flow', 'block', 'chart'], description: '图型' },
        spec: {
          type: 'object',
          additionalProperties: true,
          description:
            '图稿规格。flow: {steps:[{label,detail?}],start?,end?}；block: {columns:[{title,nodes:[{id,label,detail?}]}],links:[{from,to,label?,style?}],extras?}；chart: {chartType:"bar"|"line",categories:[],series:[{name,values:[]}],xLabel,yLabel,yMin?,yMax?}',
        },
        caseId: { type: 'string', description: '案件号；提供后写入该案件 figures 目录并登记' },
        figureId: { type: 'string', description: '如 fig1；省略自动取下一个编号' },
        widthMm: { type: 'number', minimum: 60, maximum: 180, description: '成图宽度毫米，默认 150' },
        outDir: { type: 'string', description: '可选输出目录；省略用案件 figures 目录' },
      },
      required: ['kind', 'spec'],
    },
    output: textOutput((value) => {
      if (!value.ok) return `附图生成失败：${value.error}`
      const lines = [
        `# 附图已生成：${value.figureId}`,
        '',
        `- 类型：${value.kind}`,
        `- 画布宽度：${value.widthMm} mm（按内容紧裁后 ${value.files?.[0]?.widthMm ?? '—'} mm）`,
        `- PNG：${value.files?.[0]?.png || '—'}`,
        `- SVG：${value.files?.[0]?.svg || '—'}`,
      ]
      if (value.warnings?.length) {
        lines.push('', '## 提示', '')
        value.warnings.forEach((warning) => lines.push(`- ${warning}`))
      }
      lines.push('', '说明书“附图说明”写「图N为…示意图」，图号不要画进图片；插入 Word 用 patent_docx。')
      return lines.join('\n')
    }),
    async execute(args, exec) {
      const root = await resolveRoot(ctx, exec)
      try {
        const value = await renderFigure(root, args)
        const warnings = [...(value.warnings || []), ...figureHeuristics(args.kind, args.spec)]
        return { ...value, warnings: [...new Set(warnings)] }
      } catch (error) {
        return { ok: false, error: error.message }
      }
    },
  }
}
