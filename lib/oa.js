import { addDays, addMonths, dateOnly, parseDate } from './util.js'

const STATUTE_MAP = {
  '22.2': { defect: 'novelty', defectZh: '新颖性', article: '专利法第22条第2款' },
  '22.3': { defect: 'inventiveness', defectZh: '创造性', article: '专利法第22条第3款' },
  '22.4': { defect: 'utility', defectZh: '实用性', article: '专利法第22条第4款' },
  '26.3': { defect: 'sufficiency', defectZh: '说明书充分公开', article: '专利法第26条第3款' },
  '26.4': { defect: 'clarity', defectZh: '清楚/支持', article: '专利法第26条第4款' },
  '33': { defect: 'amendment', defectZh: '修改超范围', article: '专利法第33条' },
  '2.2': { defect: 'subject', defectZh: '技术方案客体', article: '专利法第2条第2款' },
  '9.1': { defect: 'double-patenting', defectZh: '重复授权', article: '专利法第9条第1款' },
  '31.1': { defect: 'unity', defectZh: '单一性', article: '专利法第31条第1款' },
  'R20.2': { defect: 'essential-features', defectZh: '缺少必要技术特征', article: '专利法实施细则第20条第2款' },
  'R22': { defect: 'formal', defectZh: '形式缺陷', article: '专利法实施细则第22条' },
}

const DEFECT_KEYWORDS = [
  ['inventiveness', '创造性', ['创造性', '显而易见', '非显而易见', '突出的实质性特点', '显著的进步']],
  ['novelty', '新颖性', ['新颖性', '不具备新颖性', '没有公开', '未公开']],
  ['clarity', '清楚/支持', ['不清楚', '得不到支持', '得不到说明书支持', '权利要求不清楚', '保护范围不清楚', '缺少引用基础']],
  ['sufficiency', '充分公开', ['公开不充分', '无法实现', '说明书未充分公开', '不能实现']],
  ['amendment', '修改超范围', ['超出原说明书', '修改超范围', '超出原始']],
  ['essential-features', '缺少必要技术特征', ['必要技术特征', '缺少必要']],
  ['unity', '单一性', ['单一性', '不属于一个总的发明构思']],
  ['subject', '客体', ['不属于专利保护客体', '智力活动的规则', '技术方案']],
  ['formal', '形式缺陷', ['引用关系', '格式', '附图', '摘要', '标点', '编号']],
]

const NOTICE_KINDS = [
  ['first-oa', '第一次审查意见通知书'],
  ['second-oa', '第二次审查意见通知书'],
  ['oa', '审查意见通知书'],
  ['correction', '补正通知书'],
  ['rejection', '驳回决定'],
  ['grant', '授予发明专利权通知书'],
  ['formality', '办理登记手续通知书'],
  ['other', '其他通知书'],
]

const PROMO_TERMS = ['最先进', '国际领先', '完美', '革命性', '填补空白', '极大提高', '最佳', '万能', '百分之百']

function normalize(text) {
  return String(text || '')
    .replace(/\r\n?/g, '\n')
    .replace(/[０-９]/g, (ch) => String.fromCharCode(ch.charCodeAt(0) - 0xfee0))
    .replace(/[Ａ-Ｚａ-ｚ]/g, (ch) => String.fromCharCode(ch.charCodeAt(0) - 0xfee0))
    .replace(/\u3000/g, ' ')
    .replace(/[ \t]+/g, ' ')
}

function firstMatch(text, patterns) {
  for (const pattern of patterns) {
    const m = text.match(pattern)
    if (m) return m
  }
  return null
}

function cleanDate(raw) {
  if (!raw) return ''
  const d = parseDate(raw)
  return d ? dateOnly(d) : ''
}

function extractStatutes(text) {
  const found = []
  const push = (key) => {
    if (!STATUTE_MAP[key]) return
    if (!found.some((item) => item.key === key)) found.push({ key, ...STATUTE_MAP[key] })
  }
  for (const m of text.matchAll(/专利法(?:实施细则)?第\s*(\d+)\s*条(?:\s*第\s*(\d+)\s*款)?/g)) {
    if (m[2]) push(`${m[1]}.${m[2]}`)
    else push(String(m[1]))
  }
  for (const m of text.matchAll(/实施细则第\s*(\d+)\s*条(?:\s*第\s*(\d+)\s*款)?/g)) {
    push(`R${m[1]}${m[2] ? `.${m[2]}` : ''}`)
  }
  return found
}

function extractCitedDocs(text) {
  const docs = []
  const add = (number, extra = '') => {
    const id = String(number || '').replace(/[.\s]/g, '').toUpperCase()
    if (!id) return
    if (docs.some((doc) => doc.number === id)) return
    docs.push({ number: id, role: extra || '', title: '', snippet: '' })
  }
  for (const m of text.matchAll(/(?:对比文件|对比文献|文献)\s*(\d+)\s*[：:]\s*((?:CN|US|EP|JP|KR|WO)\s?[A-Z0-9]{6,})/gi)) {
    add(m[2], `D${m[1]}`)
  }
  for (const m of text.matchAll(/\b((?:CN|US|EP|JP|KR|WO)\s?\d{6,}[A-Z]?\d?)\b/g)) {
    add(m[1])
  }
  for (const m of text.matchAll(/(?:对比文件|文献)\s*(\d+)[：:\s]+([^\n，。；]{4,40})/g)) {
    const label = m[2].trim()
    if (!/^(CN|US|EP|JP|KR|WO)/i.test(label)) {
      docs.push({ number: `D${m[1]}`, role: `D${m[1]}`, title: label, snippet: '' })
    }
  }
  return docs
}

function splitObjections(text) {
  const lines = text.split('\n')
  const blocks = []
  let current = null
  const headingRe = /^\s*(?:[一二三四五六七八九十]+\s*[、.．]|\d+\s*[、.．]|（\d+）|\(\d+\))\s*(.+)$/
  for (const line of lines) {
    const trimmed = line.trim()
    if (!trimmed) continue
    const m = trimmed.match(headingRe)
    if (m && trimmed.length < 90) {
      if (current) blocks.push(current)
      current = { heading: m[1].trim(), lines: [] }
    } else if (current) {
      current.lines.push(trimmed)
    } else {
      current = { heading: '', lines: [trimmed] }
    }
  }
  if (current) blocks.push(current)
  if (blocks.length === 0) return []
  // Merge tiny stray blocks into the previous one.
  const merged = []
  for (const block of blocks) {
    if (block.lines.length === 0 && !block.heading) continue
    if (merged.length && block.heading === '' && block.lines.length < 2) {
      merged[merged.length - 1].lines.push(...block.lines)
    } else {
      merged.push(block)
    }
  }
  return merged
}

function classifyBlock(block, statutes) {
  const text = `${block.heading}\n${block.lines.join('\n')}`
  const localStatutes = extractStatutes(text)
  const used = localStatutes.length ? localStatutes : (statutes.length === 1 ? statutes : [])
  let defect = used[0]?.defect || ''
  let defectZh = used[0]?.defectZh || ''
  if (!defect) {
    for (const [key, zh, words] of DEFECT_KEYWORDS) {
      if (words.some((word) => text.includes(word))) {
        defect = key
        defectZh = zh
        break
      }
    }
  }
  const bodyLines = block.lines.filter((line) => !/^(申请号|申请日|发明名称|实用新型名称|外观设计名称|发文日|发文日期|审查员|审查人|申请人|代理机构|地址|邮编|电话)[：:]/.test(line.trim()))
  const sentence = bodyLines.join(' ')
    .replace(/请?于收到本通知书之日起[^。；]{0,40}(?:答复|陈述意见|补正)[。；]?$/g, '')
    .trim()
  const view = sentence.length > 600 ? `${sentence.slice(0, 600)}…` : sentence
  return {
    statute: used[0]?.article || '',
    statuteKeys: used.map((item) => item.key),
    defect,
    defectZh,
    examinerView: view,
    citedDocs: extractCitedDocs(text).map((doc) => doc.number),
    text,
  }
}

function isPreambleBlock(block) {
  if (/^关于/.test(block.heading || '')) return false
  const text = block.lines.join(' ')
  if (/专利法|实施细则|不符合|不具备|权利要求|对比文件/.test(text)) return false
  if (block.heading && /通知书|国家知识产权局/.test(block.heading)) return true
  if (text.length > 320) return false
  return /国家知识产权局|审查意见通知书|申请号|发文日|审查员|发明名称/.test(text)
}

function extractReplyPeriod(text) {
  const m = firstMatch(text, [
    /(?:答复期限|期限)[：:\s]*([^\n。；]{2,40})/,
    /自收到本通知书之日起\s*(\d+)\s*(个月|日)内/,
    /(?:在|于)\s*(\d+)\s*(个月|日)内\s*(?:答复|陈述意见|补正)/,
  ])
  if (!m) return { text: '', months: null, days: null }
  if (m[2] === '个月') return { text: m[0].trim(), months: Number(m[1]), days: null }
  if (m[2] === '日') return { text: m[0].trim(), months: null, days: Number(m[1]) }
  return { text: m[0].trim(), months: null, days: null }
}

export function extractOfficeAction(rawText, options = {}) {
  const text = normalize(rawText)
  const compact = text.replace(/\s+/g, ' ')
  const appMatch = firstMatch(compact, [
    /申请号[：:\s]*([0-9]{4,}[0-9.\s]{4,}[0-9Xx])/,
    /申请专利号[：:\s]*([0-9]{4,}[0-9.\s]{4,}[0-9Xx])/,
  ])
  const applicationNo = appMatch ? appMatch[1].replace(/[.\s]+$/g, '').trim() : ''
  const noticeDateRaw = firstMatch(compact, [
    /发文日[：:\s]*(\d{4}\s*[年./-]\s*\d{1,2}\s*[月./-]\s*\d{1,2}\s*日?)/,
    /发文日期[：:\s]*(\d{4}\s*[年./-]\s*\d{1,2}\s*[月./-]\s*\d{1,2}\s*日?)/,
  ])
  const noticeDate = options.noticeDate
    ? cleanDate(options.noticeDate)
    : noticeDateRaw ? cleanDate(noticeDateRaw[1]) : ''
  const examinerMatch = firstMatch(compact, [
    /(?:审查员|审查人)[：:\s]*([\u4e00-\u9fa5]{2,4})/,
    /(?:审查员|审查人)[：:\s]*([A-Za-z][A-Za-z .]{1,30})/,
  ])
  const examiner = examinerMatch ? examinerMatch[1].trim() : ''
  const titleMatch = firstMatch(compact, [
    /(?:发明名称|实用新型名称|外观设计名称|申请名称)[：:\s]*([^\s，。；]{2,40})/,
  ])
  const title = titleMatch ? titleMatch[1].trim() : ''
  const kindMatch = NOTICE_KINDS.find(([, label]) => compact.includes(label))
  const looksLikeNotice = Boolean(applicationNo || noticeDate || examiner || /申请号[：:]/.test(compact) || /发文日[：:]/.test(compact))
  const kind = looksLikeNotice
    ? (kindMatch || NOTICE_KINDS[NOTICE_KINDS.length - 1])
    : ['email-reply', '未识别（可能为邮件/回复，非通知书）']
  const statutes = extractStatutes(compact)
  const citedDocs = extractCitedDocs(text)
  const period = extractReplyPeriod(compact)
  const patentType = options.patentType || (/实用新型|实用专利/.test(compact) ? 'utility' : /外观设计/.test(compact) ? 'design' : 'invention')
  const serviceDays = Number(options.serviceDays ?? 15)
  const months = period.months ?? Number(options.responseMonths?.[patentType] ?? (patentType === 'invention' ? 4 : 2))
  let responseDue = options.responseDue || ''
  if (!responseDue && noticeDate) {
    responseDue = period.days
      ? addDays(noticeDate, serviceDays + period.days)
      : addMonths(addDays(noticeDate, serviceDays), months)
  }
  const blocks = splitObjections(text)
  const items = blocks
    .filter((block) => block.lines.join('').length > 8 && !isPreambleBlock(block))
    .slice(0, 40)
    .map((block, index) => {
      const classified = classifyBlock(block, statutes)
      const { text: _text, ...rest } = classified
      return { index: index + 1, heading: block.heading, ...rest }
    })
  const defects = []
  for (const item of items) {
    if (item.defect && !defects.includes(item.defect)) defects.push(item.defect)
  }
  for (const statute of statutes) {
    if (!defects.includes(statute.defect)) defects.push(statute.defect)
  }
  const quoteLines = compact.match(/[^。；]{0,80}(?:不符合|不具备|没有公开|未公开|显而易见|得不到支持|不清楚|超出)[^。；]{0,120}[。；]?/g) || []
  const reviewFlags = []
  if (!applicationNo) reviewFlags.push('未识别申请号')
  if (!noticeDate) reviewFlags.push('未识别发文日')
  if (!examiner) reviewFlags.push('未识别审查员')
  if (!items.length) reviewFlags.push('未拆分出逐条审查意见')
  if (items.length && items.every((item) => !item.statute)) reviewFlags.push('未识别法条')
  const promoHits = PROMO_TERMS.filter((term) => compact.includes(term))
  if (promoHits.length) reviewFlags.push(`申请文件中可能含宣传语：${promoHits.join('、')}`)
  const confidence = (applicationNo ? 1 : 0) + (noticeDate ? 1 : 0) + (items.length ? 1 : 0) + (statutes.length ? 1 : 0)
  return {
    ok: true,
    noticeKind: kind[0],
    noticeKindLabel: kind[1],
    applicationNo,
    title,
    examiner,
    noticeDate,
    responseDue,
    deadlineBasis: period.text || (noticeDate ? `发文日+${serviceDays}日推定送达+${months}个月（估算）` : ''),
    patentType,
    statutes,
    defects,
    defectLabels: defects.map((key) => DEFECT_KEYWORDS.find(([k]) => k === key)?.[1] || key),
    citedDocs,
    replyPeriod: period,
    items,
    quotes: [...new Set(quoteLines)].slice(0, 12),
    reviewFlags,
    confidence: confidence >= 3 ? 'high' : confidence >= 2 ? 'medium' : 'low',
    sourceChars: text.length,
    extractedAt: new Date().toISOString(),
  }
}

export function noticeMarkdown(result) {
  const lines = []
  lines.push('# 审查意见通知书提取结果')
  lines.push('')
  lines.push('| 项目 | 内容 |')
  lines.push('| --- | --- |')
  const rows = [
    ['通知书类型', result.noticeKindLabel],
    ['申请号', result.applicationNo || '未识别'],
    ['发明名称', result.title || '未识别'],
    ['发文日', result.noticeDate || '未识别'],
    ['审查员', result.examiner || '未识别'],
    ['答复期限（估算）', result.responseDue || '未识别'],
    ['期限依据', result.deadlineBasis || '—'],
    ['识别置信度', result.confidence],
    ['缺陷标签', result.defectLabels.join('、') || '未识别'],
  ]
  for (const [k, v] of rows) lines.push(`| ${k} | ${v} |`)
  lines.push('')
  if (result.citedDocs.length) {
    lines.push('## 对比文件')
    lines.push('')
    lines.push('| 编号 | 公开号/标识 | 说明 |')
    lines.push('| --- | --- | --- |')
    result.citedDocs.forEach((doc) => lines.push(`| ${doc.role || '—'} | ${doc.number} | ${doc.title || ''} |`))
    lines.push('')
  }
  lines.push('## 逐条审查意见')
  lines.push('')
  if (!result.items.length) {
    lines.push('（未能自动拆分，请人工按通知书条目整理。）')
  } else {
    for (const item of result.items) {
      lines.push(`### 第 ${item.index} 条${item.heading ? `：${item.heading}` : ''}`)
      lines.push('')
      lines.push(`- 法条：${item.statute || '未识别'}`)
      lines.push(`- 缺陷：${item.defectZh || '未识别'}`)
      if (item.citedDocs.length) lines.push(`- 对比文件：${item.citedDocs.join('、')}`)
      lines.push('')
      lines.push(`> ${item.examinerView || '（无正文）'}`)
      lines.push('')
    }
  }
  if (result.quotes.length) {
    lines.push('## 原文关键句')
    lines.push('')
    result.quotes.forEach((quote) => lines.push(`- ${quote.trim()}`))
    lines.push('')
  }
  if (result.reviewFlags.length) {
    lines.push('## 待人工确认')
    lines.push('')
    result.reviewFlags.forEach((flag) => lines.push(`- ${flag}`))
    lines.push('')
  }
  lines.push('> 本提取为机械辅助结果，须对照通知书原文逐条复核；期限以通知书指定日期为准。')
  return `${lines.join('\n')}\n`
}

export function oaResponseChecklist(result) {
  const lines = ['# 审查答复工作清单', '']
  lines.push(`- [ ] 核对通知书类型与发文日：${result.noticeKindLabel} / ${result.noticeDate || '未识别'}`)
  lines.push(`- [ ] 核对答复期限：${result.responseDue || '未识别'}`)
  result.items.forEach((item) => {
    lines.push(`- [ ] 第${item.index}条（${item.defectZh || '未分类'}，${item.statute || '无法条'}）：确定仅意见陈述/修改权要/修改说明书`)
  })
  if (result.citedDocs.length) {
    lines.push(`- [ ] 对对比文件逐篇做特征对照：${result.citedDocs.map((doc) => doc.number).join('、')}`)
  }
  lines.push('- [ ] 修改处逐项标注说明书支持段落')
  lines.push('- [ ] 意见陈述 Word 内部复核后再递交，不代替代理师签字')
  return `${lines.join('\n')}\n`
}
