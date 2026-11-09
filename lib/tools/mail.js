import { loadConfig, updateCase } from '../store.js'
import { mailAction } from '../mail.js'
import { resolveRoot, table, textOutput } from './common.js'

export function createMailTool(ctx) {
  return {
    name: 'patent_mail',
    description:
      '读取和回复专利邮箱（IMAP/SMTP）：status 检查配置，inbox 列邮件，read 看正文/附件，draft 生成回复草稿（.eml），send 发送。发送必须提供 confirm="SEND"；draft 不发送。先 status，再按需读取，禁止批量标记已读。',
    parameters: {
      type: 'object',
      additionalProperties: false,
      properties: {
        action: { type: 'string', enum: ['status', 'inbox', 'read', 'draft', 'send'], description: '操作' },
        uid: { type: 'string', description: '邮件 UID（inbox 返回）' },
        limit: { type: 'integer', minimum: 1, maximum: 100, description: 'inbox 返回条数，默认 20' },
        sinceDays: { type: 'integer', minimum: 1, maximum: 3650, description: '只查最近 N 天' },
        query: { type: 'string', description: '按主题/发件人/摘要过滤' },
        unseenOnly: { type: 'boolean', description: '只看未读' },
        saveAttachments: { type: 'boolean', description: 'read 时保存附件' },
        to: { type: 'string', description: 'draft 收件人；回复时可省略' },
        subject: { type: 'string', description: 'draft 主题；回复时可省略' },
        body: { type: 'string', description: 'draft 正文（不含签名）' },
        draftPath: { type: 'string', description: 'send 时的 .eml 草稿路径' },
        confirm: { type: 'string', description: 'send 必须为 SEND' },
        caseId: { type: 'string', description: '关联案件号；草稿/附件写入该案件 mail 目录' },
        configPath: { type: 'string', description: '可选：指定邮箱配置文件路径' },
      },
      required: ['action'],
    },
    output: textOutput((value) => {
      if (!value.ok) return `邮箱操作失败：${value.error}${value.code ? `（${value.code}）` : ''}`
      if (value.configured === false) return `邮箱未配置：${value.reason || ''}`
      if (value.configured === true && value.imap && value.smtp && !value.messages && !value.bodyText && !value.draftPath) {
        return [
          '# 邮箱配置',
          '',
          table(['通道', '服务器', '账号', '密码', '状态'], [
            ['IMAP', `${value.imap.host}:${value.imap.port}`, value.imap.user, value.imap.passwordSet ? '已设置' : '缺失', value.imap.secure ? 'SSL' : '普通'],
            ['SMTP', `${value.smtp.host}:${value.smtp.port}`, value.smtp.user, value.smtp.passwordSet ? '已设置' : '缺失', value.smtp.secure ? 'SSL' : '普通'],
          ]),
          '',
          `草稿目录：${value.draftDir || '—'}`,
          `配置文件：${value.configPath || '—'}`,
          '',
          '下一步：action=inbox 查看邮件，或 action=read + uid 读正文/附件。',
        ].join('\n')
      }
      if (value.messages) {
        return [
          `# 收件箱（${value.messages.length} 封）`,
          '',
          table(['UID', '日期', '发件人', '主题', '附件'], value.messages.map((msg) => [
            msg.uid, msg.date, `${msg.fromName || ''} <${msg.fromAddr || msg.from || ''}>`, msg.subject || '（无主题）', msg.hasAttachments ? '有' : '—',
          ])),
          '',
          '用 action=read + uid 查看正文；回复用 action=draft。',
        ].join('\n')
      }
      if (value.bodyText !== undefined) {
        const lines = [
          `# ${value.subject || '（无主题）'}`,
          '',
          `- 发件人：${value.from || '—'}`,
          `- 收件人：${value.to || '—'}`,
          `- 日期：${value.date || '—'}`,
          `- Message-ID：${value.messageId || '—'}`,
          '',
          '```text',
          String(value.bodyText || '').slice(0, 12000),
          '```',
        ]
        if (value.savedAttachments?.length) {
          lines.push('', '## 已保存附件', '')
          value.savedAttachments.forEach((att) => lines.push(`- ${att.filename} → ${att.savedPath}`))
        } else if (value.attachments?.length) {
          lines.push('', '## 附件', '')
          value.attachments.forEach((att) => lines.push(`- ${att.filename}（${att.contentType}，${att.size} bytes）`))
        }
        return lines.join('\n')
      }
      if (value.draftPath) {
        return [
          '# 回复草稿已生成（未发送）',
          '',
          `- 草稿：${value.draftPath}`,
          `- 收件人：${value.to || '—'}`,
          `- 主题：${value.subject || '—'}`,
          `- In-Reply-To：${value.inReplyTo || '—'}`,
          '',
          '```text',
          String(value.body || '').slice(0, 8000),
          '```',
          '',
          '确认后用 action=send + draftPath + confirm="SEND" 发送；发送前人工复核。',
        ].join('\n')
      }
      if (value.sent) {
        return `邮件已发送：${value.subject || ''}\n收件人：${(value.to || []).join('、')}\nMessage-ID：${value.messageId || '—'}`
      }
      return JSON.stringify(value, null, 2)
    }),
    async execute(args, exec) {
      const root = await resolveRoot(ctx, exec)
      const config = await loadConfig(root)
      try {
        const result = await mailAction(root, config, String(args.action), args)
        if (result.ok && args.action === 'send' && args.caseId) {
          try {
            await updateCase(root, args.caseId, {}, {
              type: 'mail_sent',
              note: `已发送邮件：${result.subject || ''} → ${(result.to || []).join('、')}`,
            })
          } catch {
            // The send succeeded; a docket bookkeeping failure must not mask it.
          }
        }
        return result
      } catch (error) {
        return { ok: false, error: error.message }
      }
    },
  }
}
