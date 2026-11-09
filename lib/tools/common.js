import path from 'node:path'
import process from 'node:process'

export async function resolveRoot(ctx, exec, explicit) {
  if (explicit) {
    return path.isAbsolute(explicit)
      ? explicit
      : path.resolve(process.cwd(), explicit)
  }
  const wd = ctx.get('workingDirectory')
  if (wd && exec?.agent) {
    try {
      return await wd.ensure(exec.agent, exec.signal)
    } catch {
      // Fall back to the process directory.
    }
  }
  return process.cwd()
}

export function textOutput(renderText) {
  return {
    schema: { type: 'object', additionalProperties: true },
    render(_args, value) {
      try {
        return [{ type: 'text', text: String(renderText(value)) }]
      } catch {
        return [{ type: 'text', text: JSON.stringify(value) }]
      }
    },
  }
}

export function textBlocks(text) {
  return [{ type: 'text', text: String(text) }]
}

export function table(headers, rows) {
  const head = `| ${headers.join(' | ')} |`
  const sep = `| ${headers.map(() => '---').join(' | ')} |`
  const body = rows.map((row) => `| ${row.map((cell) => String(cell ?? '').replace(/\|/g, '\\|').replace(/\n/g, ' ')).join(' | ')} |`)
  return [head, sep, ...body].join('\n')
}

export function boolArg(value, fallback = false) {
  if (typeof value === 'boolean') return value
  if (typeof value === 'string') return ['1', 'true', 'yes', '是'].includes(value.toLowerCase())
  return fallback
}

export function intArg(value, fallback) {
  const n = Number(value)
  return Number.isFinite(n) ? Math.trunc(n) : fallback
}
