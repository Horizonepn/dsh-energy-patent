import path from 'node:path'
import { readFile } from 'node:fs/promises'
import { checkClaims } from '../claims.js'
import { resolveRoot, textOutput } from './common.js'

export function createClaimCheckTool(ctx) {
  return {
    name: 'patent_claim_check',
    description:
      '机械校验权利要求书 Markdown：编号连续性、独立/从属引用、多项从属引用多项从属、引用基础（“所述…”）、宣传语、步骤编号与权项数量。只查形式与引用，不评价新颖性/创造性/授权前景。',
    parameters: {
      type: 'object',
      additionalProperties: false,
      properties: {
        claims: { type: 'string', description: '权利要求书全文（Markdown，1. 2. 3. 逐项）' },
        claimsPath: { type: 'string', description: '或权利要求书文件路径（相对工作区）' },
      },
    },
    output: textOutput((value) => {
      if (value.ok === false) return `权利要求校验失败：${value.error || '未知错误'}`
      return value.markdown || ''
    }),
    async execute(args, exec) {
      const root = await resolveRoot(ctx, exec)
      try {
        let text = String(args.claims || '')
        if (!text && args.claimsPath) {
          text = await readFile(path.resolve(root, args.claimsPath), 'utf8')
        }
        if (!text.trim()) return { ok: false, error: '请提供 claims 或 claimsPath' }
        const result = checkClaims(text)
        return { ...result, ok: true, validationOk: result.ok }
      } catch (error) {
        return { ok: false, error: error.message }
      }
    },
  }
}
