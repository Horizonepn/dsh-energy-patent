/**
 * Lightweight energy-storage taxonomy used to tag source material and to
 * surface relevant IPC/CPC areas. This is a drafting aid, not a legal
 * classification opinion; the agent must verify codes against the current
 * IPC/CPC scheme before filing.
 */
export const BRANCHES = [
  {
    id: 'li-ion',
    name: '锂离子电池',
    keywords: ['锂离子', '磷酸铁锂', '三元', 'NCM', 'NCA', '钴酸锂', '锰酸锂', '石墨负极', '硅碳', '硅基负极', '电解液', '隔膜', '极片', '叠片', '卷绕', '预锂化', '补锂'],
    ipc: ['H01M4/00', 'H01M10/0525', 'H01M50/00', 'H01M10/058'],
  },
  {
    id: 'na-ion',
    name: '钠离子电池',
    keywords: ['钠离子', '钠电', '硬碳', '普鲁士蓝', '层状氧化物', '钠盐'],
    ipc: ['H01M10/054', 'H01M4/00'],
  },
  {
    id: 'solid-state',
    name: '固态电池',
    keywords: ['固态电池', '固态电解质', '硫化物', '氧化物电解质', '聚合物电解质', '半固态', '全固态', '界面阻抗', '锂枝晶'],
    ipc: ['H01M10/0562', 'H01M10/0585', 'H01M4/62'],
  },
  {
    id: 'flow',
    name: '液流电池',
    keywords: ['液流电池', '全钒', '钒电池', '铁铬', '锌铁', '电解液储罐', '电堆', '离子交换膜', '双极板'],
    ipc: ['H01M8/18', 'H01M8/20', 'H01M8/24'],
  },
  {
    id: 'supercap',
    name: '超级电容器',
    keywords: ['超级电容', '双电层', '赝电容', '活性炭', '比电容'],
    ipc: ['H01G11/00', 'H01G11/24'],
  },
  {
    id: 'bms',
    name: '电池管理与均衡',
    keywords: ['BMS', '电池管理', 'SOC', 'SOH', 'SOP', '均衡', '采样', 'AFE', '绝缘检测', '故障诊断', '析锂', '内短路', '热失控预警', '状态估计', '卡尔曼'],
    ipc: ['H01M10/48', 'G01R31/36', 'G01R31/382', 'H02J7/00'],
  },
  {
    id: 'thermal',
    name: '热管理与安全',
    keywords: ['热管理', '液冷', '风冷', '相变材料', '冷板', '热失控', '热扩散', '消防', '泄压', '防爆', '隔热', '阻燃', '温度均匀'],
    ipc: ['H01M10/613', 'H01M10/617', 'H01M10/625', 'H01M10/655', 'A62C3/16'],
  },
  {
    id: 'pack',
    name: '电芯结构与成组',
    keywords: ['电芯', '模组', 'PACK', '电池包', 'CTP', 'CTC', '刀片电池', '大圆柱', '壳体', '顶盖', '防爆阀', '汇流排', 'busbar', '箱体', '梁', '集成母排'],
    ipc: ['H01M50/00', 'H01M50/20', 'H01M50/30', 'H01M10/04'],
  },
  {
    id: 'pcs',
    name: '储能变流与并网',
    keywords: ['PCS', '储能变流器', '并网', '构网型', '跟网型', '虚拟同步机', '一次调频', '惯量', '黑启动', '直流母线', 'DC/AC', '簇级', '组串式'],
    ipc: ['H02J3/32', 'H02J3/38', 'H02J3/24', 'H02M7/00'],
  },
  {
    id: 'ems',
    name: '能量管理与调度',
    keywords: ['EMS', '能量管理', '调度', '峰谷', '需量', '现货', '辅助服务', '调频', '调峰', '虚拟电厂', '共享储能', '光储充', '微电网', '优化', '预测', '收益'],
    ipc: ['H02J3/00', 'G06Q50/06', 'G06Q10/04', 'H02J3/28'],
  },
  {
    id: 'long-duration',
    name: '长时与物理储能',
    keywords: ['压缩空气', '飞轮', '抽水蓄能', '重力储能', '储热', '熔盐', '相变储热', '氢储能', '电解水', '储氢', '液氢', '氨'],
    ipc: ['F01D', 'F03G', 'F28D20/00', 'C25B', 'F17C', 'H02K7/02'],
  },
  {
    id: 'recycle',
    name: '回收与梯次利用',
    keywords: ['回收', '梯次利用', '退役电池', '拆解', '湿法', '火法', '浸出', '再生', '生命周期', '碳足迹'],
    ipc: ['H01M10/54', 'B09B3/00', 'C22B'],
  },
]

export const PATENT_STRATEGY_HINTS = [
  ['方法', '步骤一…步骤N 与流程图逐字同义；独权只放必要步骤'],
  ['系统/装置', '模块名称 + 连接/功能关系；框图不标件号、不拉引出线'],
  ['结构', '部件 + 连接关系 + 位置/配合；件号与附图、说明书一致'],
  ['控制', '采样量 → 判定条件 → 控制动作；阈值与公式放说明书'],
  ['材料/配方', '组分 + 含量范围 + 制备步骤；参数表放说明书'],
]

export function analyzeDomain(rawText) {
  const text = String(rawText || '').toUpperCase()
  const lower = String(rawText || '')
  const results = []
  for (const branch of BRANCHES) {
    const hits = branch.keywords.filter((word) => lower.includes(word) || text.includes(word.toUpperCase()))
    if (hits.length) {
      results.push({ id: branch.id, name: branch.name, hits, ipc: branch.ipc, score: hits.length })
    }
  }
  results.sort((a, b) => b.score - a.score)
  return results
}

export function domainBrief() {
  const lines = ['# 储能领域撰写提示', '']
  lines.push('| 技术分支 | 常用 IPC/CPC（须核验） | 撰写抓手 |')
  lines.push('| --- | --- | --- |')
  for (const branch of BRANCHES) {
    lines.push(`| ${branch.name} | ${branch.ipc.join('、')} | ${branch.keywords.slice(0, 6).join('、')} |`)
  }
  lines.push('')
  lines.push('分类号仅作检索与撰写提示，正式分类以国知局/CPC 最新版本为准。')
  return `${lines.join('\n')}\n`
}
