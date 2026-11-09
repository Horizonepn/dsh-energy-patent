import { readFileSync, readdirSync, statSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createIntakeTool } from './lib/tools/intake.js'
import { createCaseTool } from './lib/tools/case.js'
import { createFigureTool } from './lib/tools/figure.js'
import { createDocxTool } from './lib/tools/docx.js'
import { createOaTool } from './lib/tools/oa.js'
import { createMailTool } from './lib/tools/mail.js'
import { createClaimCheckTool } from './lib/tools/claims.js'
import { setSubprocessProvider } from './lib/python.js'

const PACKAGE_DIR = path.dirname(fileURLToPath(import.meta.url))
const SKILLS_DIR = path.join(PACKAGE_DIR, 'skills')
const PROVIDER = 'energy-patent'

export const name = 'energy-patent'
export const inject = ['tools', 'skills']

function parseFrontmatter(raw) {
  const text = String(raw || '').replace(/^\uFEFF/, '')
  if (!text.startsWith('---')) return { meta: {}, body: text }
  const end = text.indexOf('\n---', 3)
  if (end === -1) return { meta: {}, body: text }
  const head = text.slice(3, end).trim()
  const body = text.slice(end + 4).replace(/^\r?\n/, '')
  const meta = {}
  for (const line of head.split(/\r?\n/)) {
    const m = line.match(/^([A-Za-z0-9_-]+):\s*(.*)$/)
    if (!m) continue
    let value = m[2].trim()
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1)
    }
    meta[m[1]] = value
  }
  return { meta, body }
}

function loadSkills() {
  let entries = []
  try {
    entries = readdirSync(SKILLS_DIR, { withFileTypes: true })
  } catch {
    return []
  }
  const skills = []
  for (const entry of entries) {
    if (!entry.isDirectory()) continue
    const dir = path.join(SKILLS_DIR, entry.name)
    const file = path.join(dir, 'SKILL.md')
    try {
      if (!statSync(file).isFile()) continue
    } catch {
      continue
    }
    const raw = readFileSync(file, 'utf8')
    const { meta, body } = parseFrontmatter(raw)
    const skillName = meta.name || entry.name
    if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(skillName)) continue
    skills.push({
      name: skillName,
      description: meta.description || '',
      whenToUse: meta.whenToUse || '',
      content: body.trim(),
      resourceBase: { kind: 'directory', path: dir },
    })
  }
  return skills
}

export function apply(ctx) {
  const disposers = []
  setSubprocessProvider(ctx.get('subprocess'))
  for (const skill of loadSkills()) {
    disposers.push(ctx.skills.register({
      ...skill,
      invocation: { modelInvocable: true, userInvocable: true },
      source: 'runtime',
      provider: PROVIDER,
    }))
  }
  const factories = [
    createIntakeTool,
    createCaseTool,
    createFigureTool,
    createDocxTool,
    createOaTool,
    createMailTool,
    createClaimCheckTool,
  ]
  for (const factory of factories) {
    disposers.push(ctx.tools.register(factory(ctx)))
  }
  return () => {
    setSubprocessProvider(null)
    for (const dispose of disposers.reverse()) {
      try {
        dispose()
      } catch {
        // Ignore teardown errors so one bad disposer cannot leak the rest.
      }
    }
  }
}
