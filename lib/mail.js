import path from 'node:path'
import { homedir } from 'node:os'
import { fileURLToPath } from 'node:url'
import { exists, ensureDir, readJson } from './util.js'
import { runPython } from './python.js'
import { ensureCaseDirs, workbenchPaths } from './store.js'

const ENGINE = fileURLToPath(new URL('../engine/mail.py', import.meta.url))

export async function resolveMailConfig(root, config, explicitPath) {
  const candidates = []
  if (explicitPath) candidates.push(path.resolve(root, explicitPath))
  for (const rel of config.mailConfigPaths || []) {
    candidates.push(path.isAbsolute(rel) ? rel : path.join(root, rel))
  }
  candidates.push(path.join(homedir(), '.dsh', 'patent-mail.config.json'))
  const seen = new Set()
  for (const candidate of candidates) {
    if (seen.has(candidate)) continue
    seen.add(candidate)
    if (!(await exists(candidate))) continue
    const data = await readJson(candidate, null)
    if (data) return { path: candidate, config: data }
  }
  return { path: '', config: null, searched: [...seen] }
}

function resolveConfigDirs(root, base, rawConfig) {
  const dataDir = workbenchPaths(root).dataDir
  const resolve = (value, fallback) => {
    if (!value) return fallback
    return path.isAbsolute(value) ? value : path.resolve(path.dirname(base), value)
  }
  return {
    ...rawConfig,
    draftDir: resolve(rawConfig.draftDir, path.join(dataDir, 'mail', 'drafts')),
    attachmentDir: resolve(rawConfig.attachmentDir, path.join(dataDir, 'mail', 'attachments')),
  }
}

export async function mailAction(root, config, action, input = {}) {
  const found = await resolveMailConfig(root, config, input.configPath)
  if (!found.config) {
    if (action === 'status') {
      return {
        ok: true,
        configured: false,
        reason: `未找到邮箱配置。请在工作区或 ~/.dsh 下创建 patent-mail.config.json。已查找：\n${(found.searched || []).join('\n')}`,
        searched: found.searched,
      }
    }
    return {
      ok: false,
      code: 'MAIL_CONFIG_MISSING',
      error: `未找到邮箱配置。请在工作区或 ~/.dsh 下创建 patent-mail.config.json。已查找：\n${(found.searched || []).join('\n')}`,
      searched: found.searched,
    }
  }
  const runtimeConfig = resolveConfigDirs(root, found.path, found.config)
  if (action === 'draft' && input.caseId) {
    const dirs = await ensureCaseDirs(root, input.caseId)
    runtimeConfig.draftDir = path.join(dirs.mail, 'drafts')
  }
  if (action === 'read' && input.caseId && input.saveAttachments) {
    const dirs = await ensureCaseDirs(root, input.caseId)
    runtimeConfig.attachmentDir = path.join(dirs.mail, 'attachments')
  }
  if (runtimeConfig.draftDir) await ensureDir(runtimeConfig.draftDir)
  if (action === 'send' && input.confirm !== 'SEND') {
    return { ok: false, code: 'CONFIRM_REQUIRED', error: '发送邮件须显式提供 confirm="SEND"；请先 review 草稿。' }
  }
  const payload = {
    ...input,
    draftDir: runtimeConfig.draftDir,
    attachmentDir: runtimeConfig.attachmentDir,
    saveDir: input.saveDir || runtimeConfig.attachmentDir,
  }
  delete payload.configPath
  delete payload.caseId
  const args = ['--config', found.path, '--action', action, '--input', '-']
  try {
    const result = await runPython(ENGINE, payload, {
      args,
      timeoutMs: 60000,
      allowFailure: true,
    })
    return { ...result, configPath: found.path, draftDir: runtimeConfig.draftDir }
  } catch (error) {
    return { ok: false, code: 'MAIL_ERROR', error: error.message, configPath: found.path }
  }
}

export async function mailStatus(root, config, input = {}) {
  return mailAction(root, config, 'status', input)
}
