import { mkdir, readFile, writeFile, access } from 'node:fs/promises'
import path from 'node:path'

export const DATA_DIR_NAME = 'patent-workbench'

export function nowIso(date = new Date()) {
  const p = (n) => String(n).padStart(2, '0')
  const offset = -date.getTimezoneOffset()
  const sign = offset >= 0 ? '+' : '-'
  const oh = p(Math.floor(Math.abs(offset) / 60))
  const om = p(Math.abs(offset) % 60)
  return `${date.getFullYear()}-${p(date.getMonth() + 1)}-${p(date.getDate())}T${p(date.getHours())}:${p(date.getMinutes())}:${p(date.getSeconds())}${sign}${oh}:${om}`
}

export function nowStamp(date = new Date()) {
  const p = (n) => String(n).padStart(2, '0')
  return `${date.getFullYear()}${p(date.getMonth() + 1)}${p(date.getDate())}-${p(date.getHours())}${p(date.getMinutes())}${p(date.getSeconds())}`
}

export function dateOnly(value = new Date()) {
  const d = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(d.getTime())) return ''
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

export function parseDate(value) {
  if (!value) return null
  const text = String(value).trim().replace(/[年./]/g, '-').replace(/月/g, '-').replace(/日/g, '')
  const m = text.match(/^(\d{4})-(\d{1,2})-(\d{1,2})/)
  if (!m) {
    const d = new Date(value)
    return Number.isNaN(d.getTime()) ? null : d
  }
  return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]))
}

export function addDays(value, days) {
  const d = parseDate(value)
  if (!d) return ''
  d.setDate(d.getDate() + days)
  return dateOnly(d)
}

export function addMonths(value, months) {
  const d = parseDate(value)
  if (!d) return ''
  const day = d.getDate()
  d.setDate(1)
  d.setMonth(d.getMonth() + months)
  const last = new Date(d.getFullYear(), d.getMonth() + 1, 0).getDate()
  d.setDate(Math.min(day, last))
  return dateOnly(d)
}

export function daysBetween(from, to) {
  const a = parseDate(from)
  const b = parseDate(to)
  if (!a || !b) return null
  return Math.round((b.getTime() - a.getTime()) / 86400000)
}

export function asciiSlug(text, fallback = 'case') {
  const slug = String(text || '')
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[^a-zA-Z0-9_-]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 60)
  return slug || fallback
}

export function safeFileName(text, fallback = 'document') {
  const cleaned = String(text || '')
    .replace(/[\\/:*?"<>|\r\n\t]/g, '')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 80)
  return cleaned || fallback
}

export async function ensureDir(dir) {
  await mkdir(dir, { recursive: true })
  return dir
}

export async function exists(file) {
  try {
    await access(file)
    return true
  } catch {
    return false
  }
}

export async function readJson(file, fallback = null) {
  try {
    return JSON.parse(await readFile(file, 'utf8'))
  } catch {
    return fallback
  }
}

export async function writeJson(file, value) {
  await ensureDir(path.dirname(file))
  await writeFile(file, `${JSON.stringify(value, null, 2)}\n`, 'utf8')
}

export async function writeText(file, text) {
  await ensureDir(path.dirname(file))
  await writeFile(file, String(text), 'utf8')
}

export function clampText(text, max = 4000) {
  const value = String(text || '')
  return value.length <= max ? value : `${value.slice(0, max)}\n…（已截断）`
}

export function table(headers, rows) {
  const head = `| ${headers.join(' | ')} |`
  const sep = `| ${headers.map(() => '---').join(' | ')} |`
  const body = rows.map((row) => `| ${row.map((cell) => String(cell ?? '').replace(/\|/g, '\\|').replace(/\n/g, ' ')).join(' | ')} |`)
  return [head, sep, ...body].join('\n')
}

export function textBlock(text) {
  return [{ type: 'text', text: String(text) }]
}
