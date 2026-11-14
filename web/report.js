const api = new DineLensClient();
const params = new URLSearchParams(location.hash.slice(1));
const reportId = params.get('report');
const shareToken = params.get('share');
const statusElement = document.getElementById('status');
const container = document.getElementById('report');
const aspectLabels = { food: '食物', service: '服务', price: '价格', atmosphere: '环境', other: '其他' };
const polarityLabels = { positive: '正面', negative: '负面', mixed: '褒贬并存', neutral: '中性', unknown: '未确定' };
const methodLabels = { mean_v1: '平均分', bayesian_v1: '考虑样本量', bayesian_time_v1: '考虑样本量与评论时间' };
const reasonLabels = { PROMOTIONAL_REVIEW_OFFER: '评论推广或有偿评价邀约', NO_TEXT: '无可分析正文' };
const warningLabels = { LOW_RATING_SAMPLE: '有效评分少于 5 条，分数受样本量限制。', NO_NUMERIC_RATINGS: '没有可用于计算的数字评分。',
  NO_USABLE_TEXT: '没有可用于生成摘要的评论文本。', NO_USABLE_EVIDENCE: '本次数据没有可用的评分或文本证据。',
  EVIDENCE_EXPIRED: '原文证据已到期，引用停止展示。', REJECTED_SOURCE_RECORDS: '部分记录因分店或来源不匹配被排除。' };

function element(tag, text) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  return node;
}

function render(report) {
  container.replaceChildren();
  const overview = element('section');
  overview.append(element('h2', report.branch.name || '餐厅'), element('p', [report.branch.city, report.branch.address].filter(Boolean).join(' · ')));
  overview.append(element('p', report.score.value === null ? '综合分暂无数据' : report.score.value.toFixed(2) + ' / 5'));
  overview.lastChild.className = 'score';
  overview.append(element('p', '有效评分 ' + report.score.valid_count + ' 条 · 保留评论 ' + report.counts.retained + ' 条 · 标记可疑 ' + report.counts.flagged + ' 条'));
  overview.append(element('small', '评分方法 ' + (methodLabels[report.score.method] || report.score.method) + ' · 分析时间 ' + new Date(report.generated_at).toLocaleString()));
  overview.append(element('p', '评分分布 ' + (Object.entries(report.rating_distribution || {}).map(([score, count]) => score + ' 分 ' + count + ' 条').join('，') || '暂无数字评分')));
  if (report.score.raw_mean !== null) overview.append(element('p', '原始平均分 ' + report.score.raw_mean.toFixed(2) + '。综合分考虑样本量' + (report.score.method === 'bayesian_time_v1' ? '和评论时间。' : '。')));
  if (report.source_mode === 'fixture') {
    const fixture = element('p', '本报告使用自动化测试样本。');
    fixture.className = 'warning';
    overview.append(fixture);
  }
  for (const warning of report.warnings) {
    const message = element('p', warningLabels[warning] || warning);
    message.className = 'warning';
    overview.append(message);
  }
  const interval = report.negative_rate_interval;
  if (interval) overview.append(element('p', '已分类正负面评论中的负面比例 95% Wilson 区间 ' + (interval.lower * 100).toFixed(1) + '%–' + (interval.upper * 100).toFixed(1) + '%'));
  overview.append(element('p', '分析版本 ' + report.versions.pipeline + ' · 模型或规则 ' + report.versions.model));
  container.append(overview);

  const aspects = element('section');
  aspects.append(element('h2', '各方面观点'));
  const wrap = element('div');
  wrap.className = 'table-wrap';
  const table = element('table');
  const headings = element('tr');
  for (const value of ['方面', '正面', '负面', '褒贬并存', '中性', '未确定']) headings.append(element('th', value));
  table.append(headings);
  for (const aspect of report.aspects) {
    const row = element('tr');
    for (const value of [aspectLabels[aspect.aspect], aspect.positive, aspect.negative, aspect.mixed, aspect.neutral, aspect.unknown]) row.append(element('td', String(value)));
    table.append(row);
  }
  wrap.append(table);
  aspects.append(wrap);
  container.append(aspects);

  const quotes = element('section');
  quotes.append(element('h2', '评论原文依据'));
  if (!report.summary.length) quotes.append(element('p', '暂无可用的原文摘要。'));
  for (const item of report.summary) {
    quotes.append(element('h3', aspectLabels[item.aspect] + ' · ' + polarityLabels[item.polarity]));
    quotes.append(element('blockquote', item.quote ?? '这条原文证据已到期。'));
    quotes.append(element('small', '评论标识 ' + item.review_id));
  }
  container.append(quotes);
  if (report.screening) {
    const screening = element('section');
    screening.append(element('h2', '筛查记录'));
    for (const item of report.screening.assessments) {
      screening.append(element('p', item.review_id + ' · ' + (item.decision === 'flagged' ? '已标记可疑' : '已保留') +
        (item.reasons.length ? ' · ' + item.reasons.map(r => reasonLabels[r] || r).join('，') : '')));
    }
    container.append(screening);
  }
}

async function shareList() {
  const list = document.getElementById('share-list');
  const data = await api.request('/reports/' + encodeURIComponent(reportId) + '/shares');
  list.replaceChildren();
  for (const share of data.items) {
    const row = element('article');
    row.append(element('p', '到期时间 ' + new Date(share.expires_at).toLocaleString() + (share.revoked_at ? ' · 已撤销' : '')));
    if (!share.revoked_at) {
      const button = element('button', '撤销分享');
      button.className = 'secondary';
      button.addEventListener('click', async () => {
        try { await api.request('/shares/' + share.share_id, { method: 'DELETE' }); await shareList(); }
        catch (error) { statusElement.textContent = error.message; }
      });
      row.append(button);
    }
    list.append(row);
  }
}

document.getElementById('create-share').addEventListener('click', async () => {
  const button = document.getElementById('create-share');
  button.disabled = true;
  try {
    const data = await api.request('/reports/' + encodeURIComponent(reportId) + '/shares', { method: 'POST' });
    const link = element('a', new URL(data.share_url, location.href).href);
    link.href = link.textContent;
    document.getElementById('share-result').replaceChildren(link);
    await shareList();
  } catch (error) { statusElement.textContent = error.message; }
  finally { button.disabled = false; }
});

(async () => {
  try {
    let data;
    if (shareToken) {
      const response = await fetch(api.base + '/shared-report', { headers: { Authorization: 'Bearer ' + shareToken }, cache: 'no-store' });
      if (!response.ok) throw new Error('分享链接无效、已撤销、已过期或报告已删除。');
      data = await response.json();
    } else if (reportId) {
      data = await api.request('/reports/' + encodeURIComponent(reportId));
      document.getElementById('sharing').hidden = false;
      await shareList();
    } else throw new Error('缺少报告标识或分享凭证。');
    render(data);
    statusElement.textContent = shareToken ? '只读分享报告' : '我的报告';
  } catch (error) {
    statusElement.textContent = error.message;
    statusElement.className = 'error';
  }
})();
