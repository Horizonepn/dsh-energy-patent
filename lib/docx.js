import path from 'node:path'
import { readFile, readdir } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { ensureDir, exists, nowStamp, safeFileName } from './util.js'
import { runPython } from './python.js'
import { addDrafts, ensureCaseDirs, findCase, loadDocket } from './store.js'

const ENGINE = fileURLToPath(new URL('../engine/build_docx.py', import.meta.url))
const APPLICATION_STEMS = ['权利要求书', '说明书', '说明书摘要', '说明书附图']

function naturalCompare(a, b) {
  return String(a).localeCompare(String(b), 'zh-Hans-CN', { numeric: true })
}

async function readMaybe(file) {
  try {
    return await readFile(file, 'utf8')
  } catch {
    return null
  }
}

function absolutizeImages(markdown, baseDir) {
  return String(markdown || '').replace(/!\[([^\]]*)\]\(([^)\s]+)(\s+"[^"]*")?\)/g, (match, alt, src, title) => {
    if (/^(https?:|data:|file:)/i.test(src) || path.isAbsolute(src)) return match
    const resolved = path.resolve(baseDir, src)
    return `![${alt}](${resolved}${title || ''})`
  })
}

async function normalizeDocuments(root, input, caseItem) {
  const docs = []
  if (input.kind === 'application' && input.dir) {
    const dir = path.resolve(root, input.dir)
    if (!(await exists(dir))) throw new Error(`申请文件目录不存在：${dir}`)
    for (const stem of APPLICATION_STEMS) {
      if (stem === '说明书附图') continue
      const file = path.join(dir, `${stem}.md`)
      const markdown = await readMaybe(file)
      if (markdown === null) continue
      docs.push({
        name: stem,
        markdown: absolutizeImages(markdown, dir),
        title: stem === '说明书' ? caseItem?.title || '' : '',
        indentFirstLine: stem !== '权利要求书',
        pageNumbers: true,
      })
    }
    if (!docs.length) throw new Error('目录中没有 权利要求书.md / 说明书.md / 说明书摘要.md')
    const figureDir = path.join(dir, 'figures')
    let images = []
    if (await exists(figureDir)) {
      images = (await readdir(figureDir)).filter((name) => /\.(png|jpe?g)$/i.test(name)).sort(naturalCompare)
    }
    const figures = images.map((name, index) => ({
      path: path.join(figureDir, name),
      caption: `图${index + 1}`,
      widthMm: 150,
    }))
    const figureMd = await readMaybe(path.join(dir, '说明书附图.md'))
    docs.push({
      name: '说明书附图',
      markdown: figureMd ? absolutizeImages(figureMd, dir) : '',
      indentFirstLine: false,
      pageNumbers: true,
      figures,
    })
    return docs
  }

  const provided = Array.isArray(input.documents) ? input.documents : []
  for (const doc of provided) {
    let markdown = String(doc.markdown || '')
    let baseDir = root
    if (!markdown && doc.markdownPath) {
      const source = path.resolve(root, doc.markdownPath)
      baseDir = path.dirname(source)
      markdown = (await readMaybe(source)) || ''
    }
    markdown = absolutizeImages(markdown, baseDir)
    if (!markdown.trim() && !(doc.figures || []).length) continue
    docs.push({
      name: safeFileName(doc.name || '文件', '文件'),
      markdown,
      title: doc.title || '',
      indentFirstLine: doc.indentFirstLine !== false,
      pageNumbers: doc.pageNumbers !== false,
      meta: Array.isArray(doc.meta) ? doc.meta : [],
      figures: (doc.figures || []).map((figure) => ({
        path: path.resolve(root, figure.path),
        caption: figure.caption || '',
        widthMm: Number(figure.widthMm || 150),
      })),
    })
  }
  if (!docs.length) throw new Error('没有可转换的 Markdown：请提供 documents[].markdown 或 markdownPath')
  if (input.kind === 'disclosure' && docs.length === 1 && !docs[0].title) {
    docs[0].title = caseItem?.title || docs[0].name
  }
  return docs
}

export async function renderDocx(root, input) {
  let caseItem = null
  const caseId = input.caseId || ''
  if (caseId) caseItem = findCase(await loadDocket(root), caseId)
  const documents = await normalizeDocuments(root, input, caseItem)
  let outDir = input.outDir ? path.resolve(root, input.outDir) : ''
  if (!outDir) {
    if (caseId) {
      const dirs = await ensureCaseDirs(root, caseId)
      outDir = path.join(dirs.drafts, nowStamp())
    } else {
      outDir = path.join(root, 'outputs', 'patent-workbench', 'drafts', nowStamp())
    }
  }
  await ensureDir(outDir)
  const payload = {
    outDir,
    documents: documents.map((doc) => ({
      ...doc,
      title: doc.title || (input.kind === 'disclosure' ? caseItem?.title || '技术交底书' : ''),
    })),
  }
  const result = await runPython(ENGINE, payload, { requirements: ['docx'], timeoutMs: 120000, args: ['--input', '-'] })
  if (!result.ok) throw new Error(result.error || 'Word 生成失败')
  if (caseId) {
    await addDrafts(root, caseId, (result.files || []).map((file) => ({
      kind: input.kind || 'document',
      name: file.name,
      path: path.relative(root, file.path),
      paragraphs: file.paragraphs,
      images: file.images,
    })))
  }
  return {
    ok: true,
    kind: input.kind || 'document',
    outDir,
    files: result.files || [],
    warnings: result.warnings || [],
  }
}

export function docxHeuristics(documents) {
  const warnings = []
  for (const doc of documents) {
    const markdown = String(doc.markdown || '')
    if (/\([^)]*\\[a-zA-Z]+[^)]*\)/.test(markdown)) {
      warnings.push(`${doc.name || '文档'}：行内公式疑似用普通括号包 LaTeX，Word 中会变成纯文本；请改用 \\(...\\)`)
    }
    if (doc.name === '说明书摘要') {
      const plain = markdown.replace(/!\[[^\]]*\]\([^)]*\)/g, '').replace(/[#>*`\s]/g, '')
      if (plain.length > 300) warnings.push(`说明书摘要约 ${plain.length} 字，超过 300 字，建议压缩背景与效果形容词`)
    }
  }
  return warnings
}
