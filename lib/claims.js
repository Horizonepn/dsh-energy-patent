const PROMO_TERMS = ['最先进', '国际领先', '完美', '革命性', '填补空白', '极大提高', '最佳', '万能', '百分之百', '显著优于所有']

function splitClaims(text) {
  const lines = String(text || '').replace(/\r\n?/g, '\n').split('\n')
  const claims = []
  let current = null
  for (const line of lines) {
    const m = line.match(/^\s*(\d+)\s*[.、．]\s*(.*)$/)
    if (m) {
      if (current) claims.push(current)
      current = { number: Number(m[1]), text: m[2].trim(), raw: [line.trim()] }
    } else if (current) {
      if (line.trim()) {
        current.text += `\n${line.trim()}`
        current.raw.push(line.trim())
      }
    }
  }
  if (current) claims.push(current)
  return claims
}

function expandReferences(fragment) {
  const numbers = new Set()
  const normalized = String(fragment || '').replace(/[，,]/g, '、')
  for (const part of normalized.split('、')) {
    const range = part.match(/(\d+)\s*(?:至|~|-|—)\s*(\d+)/)
    if (range) {
      const start = Number(range[1])
      const end = Number(range[2])
      for (let n = start; n <= end; n += 1) numbers.add(n)
      continue
    }
    for (const num of part.matchAll(/\d+/g)) numbers.add(Number(num[0]))
  }
  return [...numbers].sort((a, b) => a - b)
}

function referenceFragment(text) {
  const m = String(text || '').match(/根据(?:上述)?权利要求\s*([\d\s、,，或至~\-—]+?)\s*(?:所述|中|之)/)
  if (m) return m[1]
  const m2 = String(text || '').match(/如权利要求\s*([\d\s、,，或至~\-—]+?)\s*(?:所述|中|之)/)
  return m2 ? m2[1] : ''
}

const FUNCTION_STOPS = ['的', '在', '与', '和', '或', '中', '上', '下', '后', '前', '时', '为', '是', '由', '将', '对', '包括', '所述']
const VERB_STOPS = [
  '其特征在于', '进行', '确定', '调节', '启动', '根据', '按照', '通过', '设置',
  '连接', '控制', '采集', '计算', '判断', '获取', '采用', '具有', '位于', '安装', '形成',
]
const GENERIC_TERMS = new Set(['方法', '装置', '系统', '设备', '权利要求', '步骤'])

function claimTerm(raw) {
  let term = String(raw || '')
  for (const stop of FUNCTION_STOPS) {
    const index = term.indexOf(stop)
    if (index > 0) term = term.slice(0, index)
  }
  for (const stop of VERB_STOPS) {
    const index = term.indexOf(stop)
    if (index >= 2) term = term.slice(0, index)
  }
  term = term.replace(/^的+/, '').trim().slice(0, 16)
  return GENERIC_TERMS.has(term) ? '' : term
}

function findAntecedentIssues(claim, allClaims) {
  const issues = []
  const text = claim.text
  const priorText = [
    ...allClaims.filter((item) => claim.dependentOn.includes(item.number) && item.number < claim.number).map((item) => item.text),
  ].join('\n')
  for (const m of text.matchAll(/所述([\u4e00-\u9fa5A-Za-z0-9]{1,24})/g)) {
    const term = claimTerm(m[1])
    if (!term || term.length < 2) continue
    const index = m.index ?? 0
    const before = text.slice(0, index)
    if (before.includes(term) || priorText.includes(term)) continue
    issues.push({
      severity: 'warning',
      claim: claim.number,
      message: `「所述${term}」在本项及所引权项中未见首次出现，可能缺少引用基础`,
    })
  }
  return issues
}

export function checkClaims(rawText, options = {}) {
  const text = String(rawText || '')
  const claims = splitClaims(text)
  const issues = []
  if (!claims.length) {
    return {
      ok: false,
      claims: [],
      issues: [{ severity: 'error', claim: 0, message: '未识别到编号权利要求；请用「1. ……」逐项编号' }],
      summary: { total: 0, independent: 0, dependent: 0, errors: 1, warnings: 0 },
      markdown: '# 权利要求校验\n\n未识别到编号权利要求。\n',
    }
  }
  claims.forEach((claim, index) => {
    claim.dependentOn = []
    claim.isDependent = false
    claim.isMultipleDependent = false
    const fragment = referenceFragment(claim.text)
    if (fragment) {
      claim.isDependent = true
      claim.dependentOn = expandReferences(fragment)
      claim.isMultipleDependent = claim.dependentOn.length > 1
    }
    if (claim.number !== index + 1) {
      issues.push({ severity: 'error', claim: claim.number, message: `编号不连续：第 ${index + 1} 项应为 ${index + 1}，实际为 ${claim.number}` })
    }
    const hasCharacterizing = /其特征(?:在于|是)/.test(claim.text)
    if (!claim.isDependent && !hasCharacterizing) {
      issues.push({ severity: 'error', claim: claim.number, message: '独立权利要求缺少「其特征在于」区分部分' })
    }
    if (claim.isDependent) {
      if (!claim.dependentOn.length) {
        issues.push({ severity: 'error', claim: claim.number, message: '从属权利要求未能解析出所引权项编号' })
      }
      for (const ref of claim.dependentOn) {
        if (ref >= claim.number) {
          issues.push({ severity: 'error', claim: claim.number, message: `从属权利要求引用了在后的权利要求 ${ref}` })
        }
        if (!claims.some((item) => item.number === ref)) {
          issues.push({ severity: 'error', claim: claim.number, message: `引用了不存在的权利要求 ${ref}` })
        }
      }
    }
    for (const term of PROMO_TERMS) {
      if (claim.text.includes(term)) {
        issues.push({ severity: 'warning', claim: claim.number, message: `含宣传/绝对化用语「${term}」，权利要求中应删除` })
      }
    }
    if (/步骤\s*S?\d+|S\d+\s*[，,、]/.test(claim.text)) {
      issues.push({ severity: 'warning', claim: claim.number, message: '步骤编号出现 S1/S2 或阿拉伯数字，建议统一为「步骤一、步骤二…」' })
    }
    if (claim.number === 1 && claim.isDependent) {
      issues.push({ severity: 'error', claim: 1, message: '权利要求 1 必须为独立权利要求' })
    }
    if (!claim.isDependent && claim.text.length < 30) {
      issues.push({ severity: 'warning', claim: claim.number, message: '独立权利要求过短，可能缺少必要技术特征' })
    }
    issues.push(...findAntecedentIssues(claim, claims))
  })
  // 多项从属不得作为另一项多项从属的基础。
  for (const claim of claims) {
    if (!claim.isMultipleDependent) continue
    for (const other of claims) {
      if (other.number <= claim.number) continue
      if (other.dependentOn.includes(claim.number) && other.isMultipleDependent) {
        issues.push({
          severity: 'error',
          claim: other.number,
          message: `多项从属权利要求 ${claim.number} 被另一项多项从属权利要求 ${other.number} 引用；中国实践不允许`,
        })
      }
    }
  }
  const independent = claims.filter((claim) => !claim.isDependent)
  const dependent = claims.filter((claim) => claim.isDependent)
  if (claims.length > 10) {
    issues.push({ severity: 'info', claim: 0, message: `共 ${claims.length} 项权利要求，超过 10 项的部分可能产生附加费` })
  }
  for (const message of claim1StepCheck(claims[0])) {
    issues.push({ severity: 'warning', claim: 1, message })
  }
  const errors = issues.filter((item) => item.severity === 'error').length
  const warnings = issues.filter((item) => item.severity === 'warning').length
  const summary = {
    total: claims.length,
    independent: independent.length,
    dependent: dependent.length,
    multipleDependent: claims.filter((claim) => claim.isMultipleDependent).length,
    errors,
    warnings,
    characters: text.replace(/\s/g, '').length,
  }
  const lines = ['# 权利要求书机械校验', '']
  lines.push(`共 ${summary.total} 项：独立 ${summary.independent} 项，从属 ${summary.dependent} 项；错误 ${errors}，警告 ${warnings}。`)
  lines.push('')
  if (!issues.length) {
    lines.push('未发现机械性缺陷。仍须人工复核清楚性、支持与保护范围。')
  } else {
    lines.push('| 级别 | 权项 | 问题 |')
    lines.push('| --- | --- | --- |')
    for (const item of issues) {
      lines.push(`| ${item.severity} | ${item.claim || '—'} | ${item.message} |`)
    }
  }
  lines.push('')
  lines.push('> 本检查只覆盖编号、引用、形式用语与部分引用基础，不评价新颖性、创造性或授权前景。')
  return {
    ok: errors === 0,
    claims: claims.map((claim) => ({
      number: claim.number,
      dependentOn: claim.dependentOn,
      isDependent: claim.isDependent,
      isMultipleDependent: claim.isMultipleDependent,
      characters: claim.text.replace(/\s/g, '').length,
    })),
    issues,
    summary,
    markdown: `${lines.join('\n')}\n`,
  }
}

function claim1StepCheck(claim) {
  if (!claim) return []
  const steps = [...claim.text.matchAll(/步骤([一二三四五六七八九十]+)/g)].map((m) => m[1])
  const problems = []
  if (steps.length && steps[0] !== '一') problems.push('权利要求 1 的步骤编号未从「步骤一」开始')
  if (steps.length > 1) {
    const order = '一二三四五六七八九十'
    for (let i = 1; i < steps.length; i += 1) {
      if (order.indexOf(steps[i]) <= order.indexOf(steps[i - 1])) {
        problems.push(`权利要求 1 的步骤编号顺序异常：步骤${steps[i - 1]} 后出现 步骤${steps[i]}`)
        break
      }
    }
  }
  return problems
}
