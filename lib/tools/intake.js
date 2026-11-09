import { intakeMaterials } from '../intake.js'
import { resolveRoot, table, textOutput } from './common.js'

export function createIntakeTool(ctx) {
  return {
    name: 'patent_intake',
    description:
      '读取发明人资料（文件夹或单个文件：md/txt/docx/pdf/pptx/xlsx/eml），抽取正文、标题、图片清单、储能领域分支与候选技术表述，建立案件并写出资料摘要。撰写专利或挖专利点之前先调用；返回的 digestPath 是后续撰写的事实底稿。',
    parameters: {
      type: 'object',
      additionalProperties: false,
      properties: {
        path: { type: 'string', description: '资料文件或文件夹的绝对路径；相对路径按当前工作区解析' },
        caseId: { type: 'string', description: '案件标识（英文/数字/连字符）；省略则自动生成' },
        title: { type: 'string', description: '案件名称；省略时取资料标题或文件夹名' },
        patentType: { type: 'string', enum: ['invention', 'utility', 'design'], description: '专利类型，默认发明' },
        maxFiles: { type: 'integer', minimum: 1, maximum: 120, description: '最多读取文件数，默认 40' },
        maxChars: { type: 'integer', minimum: 2000, maximum: 60000, description: '单文件最多抽取字符数，默认 20000' },
        focus: { type: 'string', description: '可选关注方向，如 固态电解质、热失控预警、液流电池电堆' },
      },
      required: ['path'],
    },
    output: textOutput((value) => {
      if (!value.ok) return `资料摄入失败：${value.error}`
      const lines = [
        `# 资料摄入完成：${value.title}`,
        '',
        `- 案件号：${value.caseId}`,
        `- 案件目录：${value.caseDir}`,
        `- 资料摘要：${value.digestPath}`,
        `- 文件数 / 总字数：${value.files.length} / ${value.totalChars}`,
        '',
        '## 文件',
        '',
        table(['文件', '类型', '字数', '提示'], value.files.map((file) => [
          file.name, file.kind, file.chars, (file.warnings || []).join('；') || '—',
        ])),
      ]
      if (value.domain?.length) {
        lines.push('', '## 储能领域分支', '')
        lines.push(table(['分支', '命中词', '分类号（须核验）'], value.domain.slice(0, 8).map((branch) => [
          branch.name, branch.hits.join('、'), branch.ipc.join('、'),
        ])))
      }
      if (value.candidates?.length) {
        lines.push('', '## 候选技术表述', '')
        value.candidates.slice(0, 10).forEach((sentence, index) => lines.push(`${index + 1}. ${sentence}`))
      }
      if (value.warnings?.length) {
        lines.push('', '## 警告', '')
        value.warnings.slice(0, 12).forEach((warning) => lines.push(`- ${warning}`))
      }
      lines.push('', '下一步：按 `energy-storage-patent-drafting` 技能挖点、查新，再写交底书与申请文件。')
      return lines.join('\n')
    }),
    async execute(args, exec) {
      const root = await resolveRoot(ctx, exec)
      return intakeMaterials(root, args)
    },
  }
}
