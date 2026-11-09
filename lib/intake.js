import path from 'node:path'
import { stat as statFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { ensureDir, nowIso, nowStamp, table, writeJson, writeText } from './util.js'
import { runPython } from './python.js'
import { analyzeDomain } from './domain.js'
import {
  createCase, ensureCaseDirs, findCase, loadDocket, updateCase,
} from './store.js'

const ENGINE = fileURLToPath(new URL('../engine/extract.py', import.meta.url))

const VERBS = ['包括', '采用', '通过', '基于', '根据', '生成', '确定', '控制', '调节', '采集', '计算', '预测', '优化', '注入', '设置', '连接', '布置']

async function statSafe(target) {
  try {
    return await statFile(target)
  } catch {
    return null
  }
}

function candidatesFrom(text) {
  const sentences = String(text || '')
    .split(/[。；\n]/)
    .map((s) => s.trim())
    .filter((s) => s.length >= 18 && s.length <= 180)
  const scored = []
  const seen = new Set()
  for (const sentence of sentences) {
    const hits = VERBS.filter((verb) => sentence.includes(verb)).length
    const domainHits = analyzeDomain(sentence).length
    if (hits === 0 && domainHits === 0) continue
    const key = sentence.slice(0, 24)
    if (seen.has(key)) continue
    seen.add(key)
    scored.push({ sentence, score: hits + domainHits * 2 })
  }
  scored.sort((a, b) => b.score - a.score)
  return scored.slice(0, 16).map((item) => item.sentence)
}

export async function intakeMaterials(root, input) {
  const target = path.resolve(root, input.path || '.')
  const stat = await statSafe(target)
  if (!stat) throw new Error(`路径不存在：${target}`)
  const result = await runPython(ENGINE, {
    action: stat.isDirectory() ? 'dir' : 'file',
    path: target,
    maxChars: Number(input.maxChars || 20000),
    maxFiles: Number(input.maxFiles || 40),
  }, { timeoutMs: 180000 })
  if (!result.ok) throw new Error(result.error || '资料抽取失败')
  const files = Array.isArray(result.files) ? result.files : []
  if (!files.length) throw new Error('目录中没有可读文件；请确认路径或先转换扫描件')
  const docket = await loadDocket(root)
  let caseItem = input.caseId ? findCase(docket, input.caseId) : null
  if (!caseItem) {
    const title = input.title || files.find((file) => file.title)?.title || path.basename(target) || '未命名案件'
    caseItem = await createCase(root, {
      caseId: input.caseId,
      title,
      patentType: input.patentType || 'invention',
    })
  }
  const dirs = await ensureCaseDirs(root, caseItem.id)
  const combined = files.map((file) => file.text || '').join('\n\n')
  const branches = analyzeDomain(combined)
  const candidates = candidatesFrom(combined)
  const totalChars = files.reduce((sum, file) => sum + (file.chars || 0), 0)
  const digest = {
    caseId: caseItem.id,
    title: caseItem.title,
    generatedAt: nowIso(),
    sourcePath: target,
    focus: input.focus || '',
    totalChars,
    files: files.map((file) => ({
      path: file.path,
      name: file.name,
      kind: file.kind,
      chars: file.chars,
      truncated: file.truncated,
      title: file.title,
      headings: file.headings,
      images: file.images,
      warnings: file.warnings,
      text: file.text,
    })),
    domain: branches,
    candidates,
    warnings: [...(result.warnings || []), ...files.flatMap((file) => (file.warnings || []).map((w) => `${file.name}: ${w}`))],
  }
  await ensureDir(dirs.materials)
  await writeJson(path.join(dirs.materials, 'digest.json'), digest)
  await writeText(path.join(dirs.materials, 'digest.md'), digestMarkdown(digest))
  const updated = await updateCase(root, caseItem.id, {
    materials: {
      ingestedAt: nowIso(),
      digestPath: path.relative(root, path.join(dirs.materials, 'digest.md')),
      files: files.length,
      chars: totalChars,
      branches: branches.map((branch) => branch.name),
    },
  }, { type: 'intake', note: `摄入资料 ${files.length} 个文件，${totalChars} 字` })
  return {
    ok: true,
    caseId: caseItem.id,
    title: caseItem.title,
    caseDir: dirs.dir,
    digestPath: path.join(dirs.materials, 'digest.md'),
    digestJsonPath: path.join(dirs.materials, 'digest.json'),
    totalChars,
    files: files.map((file) => ({
      name: file.name,
      kind: file.kind,
      chars: file.chars,
      truncated: file.truncated,
      title: file.title,
      warnings: file.warnings,
    })),
    domain: branches,
    candidates,
    warnings: digest.warnings,
    stamp: nowStamp(),
    case: updated,
  }
}

export function digestMarkdown(digest) {
  const lines = [`# 资料摘要：${digest.title}`, '']
  lines.push(`- 案件号：${digest.caseId}`)
  lines.push(`- 生成时间：${digest.generatedAt.replace('T', ' ').slice(0, 19)}`)
  lines.push(`- 资料路径：${digest.sourcePath}`)
  lines.push(`- 文件数：${digest.files.length}　总字数：${digest.totalChars}`)
  if (digest.focus) lines.push(`- 关注方向：${digest.focus}`)
  lines.push('')
  lines.push('## 储能领域线索')
  lines.push('')
  if (digest.domain.length) {
    lines.push(table(['技术分支', '命中关键词', '常用分类号（须核验）'], digest.domain.slice(0, 8).map((branch) => [
      branch.name,
      branch.hits.join('、'),
      branch.ipc.join('、'),
    ])))
  } else {
    lines.push('未命中储能领域词表；请确认材料是否属于储能领域，或补充技术术语。')
  }
  lines.push('')
  lines.push('## 文件清单')
  lines.push('')
  lines.push(table(['文件', '类型', '字数', '截断', '提示'], digest.files.map((file) => [
    file.name,
    file.kind,
    file.chars,
    file.truncated ? '是' : '否',
    (file.warnings || []).join('；'),
  ])))
  lines.push('')
  if (digest.candidates.length) {
    lines.push('## 可挖的候选技术表述')
    lines.push('')
    digest.candidates.forEach((sentence, index) => lines.push(`${index + 1}. ${sentence}`))
    lines.push('')
  }
  lines.push('## 文件内容摘录')
  for (const file of digest.files) {
    lines.push('')
    lines.push(`### ${file.name}`)
    if (file.title) lines.push(`- 标题：${file.title}`)
    if (file.headings?.length) lines.push(`- 结构：${file.headings.slice(0, 12).join(' / ')}`)
    lines.push('')
    lines.push('```text')
    lines.push(String(file.text || '').slice(0, 1800))
    lines.push('```')
  }
  lines.push('')
  lines.push('## 待确认')
  lines.push('')
  const questions = [
    '本发明要解决的技术问题是否已在材料中明确（不是“效率低”这类空泛表述）？',
    '独立方案的必要技术特征是否都有材料依据，哪些属于推测？',
    '是否已有最接近的对比文件（公开号/标题），没有则先做查新？',
    '发明人、申请人、技术联系人是否齐全？',
    '所有数值、参数、实验条件能否追溯到一个材料文件？',
  ]
  questions.forEach((question) => lines.push(`- ${question}`))
  if (digest.warnings.length) {
    lines.push('')
    lines.push('### 抽取警告')
    digest.warnings.forEach((warning) => lines.push(`- ${warning}`))
  }
  lines.push('')
  return `${lines.join('\n')}\n`
}
