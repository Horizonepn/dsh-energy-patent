import path from 'node:path'
import { readdir } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { exists, ensureDir, nowIso, nowStamp } from './util.js'
import { runPython } from './python.js'
import { addFigure, ensureCaseDirs, findCase, loadDocket } from './store.js'

const ENGINE = fileURLToPath(new URL('../engine/figures.py', import.meta.url))

const KINDS = new Set(['flow', 'block', 'chart'])

export function validateFigureSpec(kind, spec) {
  if (!KINDS.has(kind)) throw new Error(`不支持的图型：${kind}（可用 flow / block / chart）`)
  if (!spec || typeof spec !== 'object') throw new Error('spec 必须是对象')
  if (kind === 'flow') {
    if (!Array.isArray(spec.steps) || spec.steps.length < 2) throw new Error('flow.spec.steps 至少需要 2 个步骤')
    for (const [index, step] of spec.steps.entries()) {
      if (!step || !String(step.label || '').trim()) throw new Error(`flow.spec.steps[${index}].label 不能为空`)
    }
  }
  if (kind === 'block') {
    const columns = Array.isArray(spec.columns) ? spec.columns : []
    const extras = Array.isArray(spec.extras) ? spec.extras : []
    if (!columns.length && !extras.length) throw new Error('block.spec 需要 columns 或 extras')
    const ids = new Set()
    for (const group of [...columns, ...extras]) {
      if (!Array.isArray(group.nodes) || !group.nodes.length) throw new Error('每个分组至少需要一个节点')
      for (const node of group.nodes) {
        const id = String(node.id || node.label || '').trim()
        if (!id) throw new Error('节点缺少 id/label')
        if (ids.has(id)) throw new Error(`节点 id 重复：${id}`)
        ids.add(id)
      }
    }
    for (const link of spec.links || []) {
      for (const key of ['from', 'to']) {
        if (!ids.has(String(link[key] || '').trim())) {
          throw new Error(`links.${key} 指向不存在的节点：${link[key]}；可用节点：${[...ids].join('、')}`)
        }
      }
    }
  }
  if (kind === 'chart') {
    if (!Array.isArray(spec.categories) || !spec.categories.length) throw new Error('chart.spec.categories 不能为空')
    if (!Array.isArray(spec.series) || !spec.series.length) throw new Error('chart.spec.series 不能为空')
    for (const [index, series] of spec.series.entries()) {
      if (!Array.isArray(series.values) || series.values.length !== spec.categories.length) {
        throw new Error(`chart.spec.series[${index}].values 数量须与 categories 一致`)
      }
      if (series.values.some((value) => typeof value !== 'number' || Number.isNaN(value))) {
        throw new Error(`chart.spec.series[${index}].values 必须全是数字`)
      }
    }
  }
  return true
}

function nextFigureNumber(dirFiles, caseItem) {
  const used = new Set()
  for (const file of dirFiles) {
    const m = String(file).match(/^fig(\d+)\.(png|svg)$/i)
    if (m) used.add(Number(m[1]))
  }
  for (const figure of caseItem?.figures || []) {
    const m = String(figure.id || '').match(/^fig(\d+)$/i)
    if (m) used.add(Number(m[1]))
  }
  let n = 1
  while (used.has(n)) n += 1
  return `fig${n}`
}

export async function renderFigure(root, input) {
  const { kind, spec } = input
  validateFigureSpec(kind, spec)
  let caseId = input.caseId || ''
  let outDir = input.outDir ? path.resolve(root, input.outDir) : ''
  if (!outDir) {
    if (!caseId) {
      outDir = path.join(root, 'outputs', 'patent-workbench', 'figures')
    } else {
      const dirs = await ensureCaseDirs(root, caseId)
      outDir = dirs.figures
    }
  }
  await ensureDir(outDir)
  const filesInDir = await readdir(outDir).catch(() => [])
  let figureId = String(input.figureId || '').trim()
  if (!figureId) {
    let caseItem = null
    if (caseId) {
      const docket = await loadDocket(root)
      caseItem = findCase(docket, caseId)
    }
    figureId = nextFigureNumber(filesInDir, caseItem)
  }
  figureId = figureId.replace(/[^a-zA-Z0-9_-]/g, '') || `fig${Date.now()}`
  const widthMm = Number(input.widthMm || 150)
  const payload = {
    dpi: Number(input.dpi || 300),
    outDir,
    figures: [{ id: figureId, kind, widthMm, spec }],
  }
  const result = await runPython(ENGINE, payload, { requirements: ['PIL'], timeoutMs: 120000 })
  if (!result.ok) throw new Error(result.error || '附图渲染失败')
  const files = (result.files || []).map((file) => ({
    ...file,
    png: path.resolve(outDir, `${figureId}.png`),
    svg: path.resolve(outDir, `${figureId}.svg`),
  }))
  for (const file of files) {
    if (!(await exists(file.png))) throw new Error(`附图未生成：${file.png}`)
  }
  if (caseId) {
    const figure = {
      id: figureId,
      kind,
      png: path.relative(root, files[0]?.png || ''),
      svg: path.relative(root, files[0]?.svg || ''),
      widthMm,
      createdAt: nowIso(),
    }
    await addFigure(root, caseId, figure)
  }
  return {
    ok: true,
    figureId,
    kind,
    widthMm,
    dir: outDir,
    files,
    warnings: result.warnings || [],
    stamp: nowStamp(),
  }
}

export function figureHeuristics(kind, spec) {
  if (kind === 'flow') {
    const steps = spec.steps || []
    const numbered = steps.map((step) => String(step.label || '')).filter((label) => /步骤[一二三四五六七八九十]/.test(label))
    const warnings = []
    if (numbered.length !== steps.length) warnings.push('流程图主步骤建议统一写成「步骤一、步骤二…」，与独立权利要求同序同义')
    if (steps.length > 10) warnings.push('步骤超过 10 个，考虑把分支/细节移到从属权要和实施例')
    return warnings
  }
  if (kind === 'block') {
    const warnings = []
    for (const column of spec.columns || []) {
      const title = String(column.title || '')
      if (!title) warnings.push('列缺少分组标题；框图建议用虚线分组并标注层级名称')
      for (const node of column.nodes || []) {
        if (/^\d+$/.test(String(node.label || ''))) warnings.push('框图节点不应只写件号；写入模块名称')
        if (String(node.label || '').length > 18) warnings.push(`节点标签偏长：${node.label}`)
      }
    }
    return warnings
  }
  return []
}
