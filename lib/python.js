import { spawn } from 'node:child_process'
import { readdir } from 'node:fs/promises'
import path from 'node:path'
import process from 'node:process'

const cache = new Map()
let subprocessProvider = null

/**
 * Cordis plugins run inside the Host process. The Host's subprocess service
 * owns stdio without OS pipes, which is required under the Windows sandbox.
 */
export function setSubprocessProvider(provider) {
  subprocessProvider = provider || null
}

async function bundledPythons() {
  const home = process.env.DSH_HOME
  if (!home) return []
  const root = path.join(home, 'dsh-runtimes')
  const found = []
  try {
    for (const entry of await readdir(root, { withFileTypes: true })) {
      if (!entry.isDirectory()) continue
      const candidate = path.join(root, entry.name, 'dependencies', 'python', 'python.exe')
      found.push(candidate)
    }
  } catch {
    // No managed runtimes.
  }
  return found
}

function probe(cmd, args, requirements, timeoutMs = 15000) {
  return new Promise((resolve) => {
    const mods = requirements.length ? requirements : []
    const code = mods.length
      ? `import importlib.util,sys;m=${JSON.stringify(mods)};miss=[x for x in m if importlib.util.find_spec(x) is None];print('OK' if not miss else 'MISSING:'+','.join(miss))`
      : `import sys;print('OK')`
    const child = spawn(cmd, [...args, '-c', code], {
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'pipe'],
      env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' },
    })
    let out = ''
    const timer = setTimeout(() => child.kill(), timeoutMs)
    child.stdout.on('data', (chunk) => { out += chunk })
    child.stderr.on('data', () => {})
    child.on('error', () => {
      clearTimeout(timer)
      resolve(false)
    })
    child.on('close', (codeValue) => {
      clearTimeout(timer)
      if (codeValue !== 0) return resolve(false)
      resolve(out.includes('OK') && !out.includes('MISSING'))
    })
  })
}

async function probeViaSubprocess(cmd, args, requirements, timeoutMs = 15000) {
  if (!subprocessProvider) return false
  const mods = requirements.length ? requirements : []
  const code = mods.length
    ? `import importlib.util,sys;m=${JSON.stringify(mods)};miss=[x for x in m if importlib.util.find_spec(x) is None];print('OK' if not miss else 'MISSING:'+','.join(miss))`
    : `import sys;print('OK')`
  try {
    const handle = subprocessProvider.spawn({
      argv: [cmd, ...args, '-c', code],
      cwd: process.cwd(),
      stdio: {
        stdin: 'ignore',
        stdout: { maxBytes: 1024 * 1024 },
        stderr: { maxBytes: 1024 * 1024 },
      },
      graceMs: 1000,
    })
    const outcome = await Promise.race([
      handle.done,
      new Promise((resolve) => setTimeout(() => resolve({ exitCode: -1, signal: 'TIMEOUT' }), timeoutMs)),
    ])
    const text = handle.collected?.stdout?.readFrom(0)?.text || ''
    return outcome.exitCode === 0 && text.includes('OK') && !text.includes('MISSING')
  } catch {
    return false
  }
}

/**
 * Find a Python interpreter that can import the requested modules.
 * requirements: e.g. ['docx','PIL'].
 */
export async function findPython(requirements = []) {
  const key = `found:${requirements.join(',')}`
  if (cache.has(key)) return cache.get(key)
  const tried = []
  const envPython = process.env.PATENT_PYTHON || process.env.DSH_PYTHON
  if (envPython) tried.push({ cmd: envPython, args: [] })
  for (const candidate of await bundledPythons()) tried.push({ cmd: candidate, args: [] })
  if (process.platform === 'win32') {
    tried.push({ cmd: 'py', args: ['-3'] })
    tried.push({ cmd: 'python', args: [] })
  } else {
    tried.push({ cmd: 'python3', args: [] })
    tried.push({ cmd: 'python', args: [] })
  }
  for (const candidate of tried) {
    if (!candidate.cmd) continue
    const ok = subprocessProvider
      ? await probeViaSubprocess(candidate.cmd, candidate.args, requirements)
      : await probe(candidate.cmd, candidate.args, requirements)
    if (ok) {
      const result = { ...candidate, path: candidate.cmd }
      cache.set(key, result)
      return result
    }
  }
  cache.set(key, null)
  return null
}

function parseEngineOutput(stdout, stderr, code, scriptPath, allowFailure) {
  const trimmed = String(stdout || '').trim()
  const line = trimmed.split(/\r?\n/).filter(Boolean).pop() || ''
  let parsed = null
  try {
    parsed = JSON.parse(line)
  } catch {
    // Diagnostics below.
  }
  if (parsed && parsed.ok === false && !allowFailure) {
    throw new Error(parsed.error || `${path.basename(scriptPath)} 执行失败`)
  }
  if (!parsed) {
    const detail = String(stderr || '').trim() || trimmed
    throw new Error(
      `${path.basename(scriptPath)} 未返回 JSON（exit=${code}）。` +
      (detail ? `\n${detail.slice(-2000)}` : ''),
    )
  }
  if (code !== 0 && !allowFailure) {
    throw new Error(parsed.error || `${path.basename(scriptPath)} 退出码 ${code}`)
  }
  return parsed
}

/**
 * Run a bundled Python engine with a JSON payload on stdin.
 * Resolves to the parsed JSON printed on stdout.
 */
export async function runPython(scriptPath, payload, options = {}) {
  const requirements = options.requirements || []
  const python = await findPython(requirements)
  if (!python) {
    throw new Error(
      `未找到可用的 Python（需要模块：${requirements.join(', ') || '无'}）。` +
      '请安装 Python 3.10+ 及所需模块，或设置 PATENT_PYTHON 指向解释器。',
    )
  }
  const timeoutMs = options.timeoutMs || 180000
  const json = JSON.stringify(payload ?? {})
  const argv = [...python.args, '-X', 'utf8', scriptPath, ...(options.args || [])]
  if (subprocessProvider) {
    let handle
    try {
      handle = subprocessProvider.spawn({
        argv: [python.cmd, ...argv],
        cwd: options.cwd || process.cwd(),
        stdio: {
          stdin: { data: json },
          stdout: { maxBytes: 64 * 1024 * 1024 },
          stderr: { maxBytes: 2 * 1024 * 1024 },
        },
        graceMs: 2000,
        signal: options.signal,
      })
    } catch (error) {
      throw new Error(`无法启动 Python 引擎：${error.message}`)
    }
    const timeout = new Promise((resolve) => {
      setTimeout(() => resolve({ exitCode: null, signal: 'TIMEOUT' }), timeoutMs).unref?.()
    })
    const outcome = await Promise.race([handle.done, timeout])
    if (outcome.signal === 'TIMEOUT') {
      try { handle.terminate() } catch { /* already gone */ }
      throw new Error(`Python 引擎超时（${timeoutMs}ms）：${path.basename(scriptPath)}`)
    }
    const stdout = handle.collected?.stdout?.readFrom(0)?.text || ''
    const stderr = handle.collected?.stderr?.readFrom(0)?.text || ''
    return parseEngineOutput(stdout, stderr, outcome.exitCode, scriptPath, options.allowFailure)
  }
  return new Promise((resolve, reject) => {
    const child = spawn(python.cmd, argv, {
      cwd: options.cwd || process.cwd(),
      windowsHide: true,
      env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' },
    })
    let stdout = ''
    let stderr = ''
    let settled = false
    const timer = setTimeout(() => {
      if (settled) return
      settled = true
      child.kill()
      reject(new Error(`Python 引擎超时（${timeoutMs}ms）：${path.basename(scriptPath)}`))
    }, timeoutMs)
    child.stdout.on('data', (chunk) => { stdout += chunk })
    child.stderr.on('data', (chunk) => { stderr += chunk })
    child.on('error', (error) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      reject(error)
    })
    child.on('close', (code) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      try {
        resolve(parseEngineOutput(stdout, stderr, code, scriptPath, options.allowFailure))
      } catch (error) {
        reject(error)
      }
    })
    child.stdin.on('error', () => {})
    child.stdin.end(json)
  })
}

export async function pythonAvailability() {
  const docx = await findPython(['docx'])
  const pillow = await findPython(['PIL'])
  return {
    docx: Boolean(docx),
    pillow: Boolean(pillow),
    docxPath: docx ? docx.cmd : null,
    pillowPath: pillow ? pillow.cmd : null,
  }
}
