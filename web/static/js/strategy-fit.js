/**
 * 策略适配自检（三问定位）
 *
 * 来源：散户做量化，「钱 / 时间 / 回撤」三问答完取交集，比追求“最好的策略”更有用。
 * 约束：
 * - 纯本机规则计算，不调用 LLM、不上传服务端；输入只存 localStorage；
 * - 阈值是固定的、可解释的，不做收益预测，不构成投资建议；
 * - 只推荐本仓库 /api/strategy/list 中真实存在的 6 个策略。
 */
(function () {
  'use strict';

  var LS_KEY = 'qf_strategy_fit_v1';

  /* time: 每日需要的盯盘分钟数; risk: 建议能承受的回撤(%); minCap: 建议最低资金(万元) */
  var POOL = [
    {
      id: 'comprehensive', name: '综合分析', time: 30, risk: 8, minCap: 0, tag: '盘后研究',
      fit: '基本面 + 技术面 + 行业面综合评分，适合用固定流程做减法。'
    },
    {
      id: 'factor', name: '因子量化', time: 30, risk: 10, minCap: 1, tag: '分散持仓',
      fit: '多因子分散选股、换手相对低，对盯盘时间要求最低。'
    },
    {
      id: 'trading_quant', name: '量化交易', time: 60, risk: 15, minCap: 1, tag: '规则执行',
      fit: '多维度评分与信号，需要按规则执行并接受中等波动。'
    },
    {
      id: 'new_high', name: '创业板新高', time: 60, risk: 15, minCap: 1, tag: '趋势突破',
      fit: '新高突破扫描，趋势行情弹性大，震荡期假突破也多。'
    },
    {
      id: 'limit_up', name: '涨停板', time: 180, risk: 20, minCap: 2, tag: '高强度盯盘',
      fit: '涨停板扫描与晋级预测，需要盘中全程盯盘和快速决策。'
    },
    {
      id: 'cb_t0', name: '可转债T+0', time: 240, risk: 12, minCap: 0, tag: '日内交易',
      fit: 'T+0 日内狙击，对盯盘时间与执行速度要求最高。'
    }
  ];

  function $(id) { return document.getElementById(id); }
  function esc(s) { return (typeof escapeHtml === 'function') ? escapeHtml(s == null ? '' : String(s)) : String(s == null ? '' : s); }
  function num(v) { var n = Number(v); return isFinite(n) ? n : 0; }

  function position(capital, time, drawdown) {
    var timeLabel = time < 60 ? '盘后研究型' : (time >= 150 ? '盘中盯盘型' : '规则执行型');
    var capLabel = capital < 2 ? '小资金' : (capital < 20 ? '中等资金' : '较大资金');
    var riskLabel = drawdown <= 10 ? '低回撤' : (drawdown <= 20 ? '中回撤' : '高回撤');
    return timeLabel + ' · ' + capLabel + ' · ' + riskLabel;
  }

  function scoreOf(s, capital, time, drawdown) {
    var reasons = [];
    var blocks = [];
    var score = 30;

    /* 时间：够用给满 30，不足按比例打折 */
    if (time >= s.time) {
      score += 30;
      reasons.push('每天约需 ' + s.time + ' 分钟，你的时间够用');
    } else {
      score += 30 * (time / s.time) * 0.6;
      blocks.push('每天只有 ' + time + ' 分钟，低于该策略约 ' + s.time + ' 分钟的盯盘要求');
    }

    /* 回撤：能扛住给满 25，不足按比例打折 */
    if (drawdown >= s.risk) {
      score += 25;
      reasons.push('波动上限约 ' + s.risk + '%，在你的回撤承受内');
    } else {
      score += 25 * (drawdown / s.risk) * 0.5;
      blocks.push('策略波动可能超过你 ' + drawdown + '% 的回撤上限');
    }

    /* 资金：达到建议门槛给满 15 */
    if (capital >= s.minCap) {
      score += 15;
      if (s.minCap > 0) reasons.push('建议资金 ' + s.minCap + ' 万起，已满足');
    } else {
      score += 15 * (capital / s.minCap) * 0.6;
      blocks.push('建议至少 ' + s.minCap + ' 万做分散，当前 ' + capital + ' 万');
    }

    var hardBlock = (time < s.time * 0.4)
      || (drawdown < s.risk * 0.6)
      || (s.minCap > 0 && capital < s.minCap * 0.5);

    return {
      strategy: s,
      score: Math.max(0, Math.min(100, Math.round(score))),
      reasons: reasons,
      blocks: blocks,
      hardBlock: hardBlock
    };
  }

  function render(rows, capital, time, drawdown) {
    var box = $('sfResult');
    if (!box) return;

    var ranked = rows.slice().sort(function (a, b) { return b.score - a.score; });
    var fit = ranked.filter(function (r) { return !r.hardBlock; }).slice(0, 2);
    var rest = ranked.filter(function (r) { return fit.indexOf(r) < 0; });

    if (!fit.length) {
      box.innerHTML = '<div class="sf-empty sf-empty-warn">当前三问组合过于保守，暂无硬性匹配的策略；建议先降低目标收益或增加研究时间。</div>';
      return;
    }

    var cards = fit.map(function (r, i) {
      var s = r.strategy;
      var reasons = r.reasons.concat(r.blocks).slice(0, 3);
      return '<article class="sf-card">'
        + '<div class="sf-card-top">'
        + '<span class="sf-rank">0' + (i + 1) + '</span>'
        + '<span class="sf-name">' + esc(s.name) + '</span>'
        + '<span class="sf-tag">' + esc(s.tag) + '</span>'
        + '<span class="sf-score">' + r.score + '</span>'
        + '</div>'
        + '<div class="sf-meter"><i style="width:' + r.score + '%"></i></div>'
        + '<p class="sf-fit">' + esc(s.fit) + '</p>'
        + '<ul class="sf-reasons">' + reasons.map(function (x) {
          return '<li' + (r.blocks.indexOf(x) >= 0 ? ' class="is-warn"' : '') + '>' + esc(x) + '</li>';
        }).join('') + '</ul>'
        + '</article>';
    }).join('');

    var excluded = rest.length
      ? '<div class="sf-excluded"><b>暂不建议</b>' + rest.slice(0, 3).map(function (r) {
        var why = r.blocks[0] || '综合匹配度偏低';
        return '<span><em>' + esc(r.strategy.name) + '</em>：' + esc(why) + '</span>';
      }).join('') + '</div>'
      : '';

    box.innerHTML = '<div class="sf-head">'
      + '<span class="sf-pos">' + esc(position(capital, time, drawdown)) + '</span>'
      + '<span class="sf-note">答案的交集，就是你的策略；没有最好的，只有合脚的。</span>'
      + '</div>'
      + '<div class="sf-cards">' + cards + '</div>'
      + excluded
      + '<div class="sf-foot">规则引擎按固定阈值计算，只做定位参考，不预测收益、不构成投资建议；输入仅保存在本机浏览器。</div>';
  }

  function run() {
    var capital = Math.max(0, num(($('sfCapital') || {}).value));
    var time = Math.max(1, num(($('sfTime') || {}).value));
    var drawdown = Math.max(1, num(($('sfDrawdown') || {}).value));
    var rows = POOL.map(function (s) { return scoreOf(s, capital, time, drawdown); });
    render(rows, capital, time, drawdown);
    try {
      localStorage.setItem(LS_KEY, JSON.stringify({ capital: capital, time: time, drawdown: drawdown }));
    } catch (e) { /* 隐私模式忽略 */ }
  }

  function restore() {
    try {
      var raw = localStorage.getItem(LS_KEY);
      if (!raw) return;
      var v = JSON.parse(raw) || {};
      if ($('sfCapital') && isFinite(Number(v.capital))) $('sfCapital').value = v.capital;
      if ($('sfTime') && isFinite(Number(v.time))) $('sfTime').value = v.time;
      if ($('sfDrawdown') && isFinite(Number(v.drawdown))) $('sfDrawdown').value = v.drawdown;
    } catch (e) { /* 忽略损坏的本地配置 */ }
  }

  function init() {
    if (!$('sfResult')) return;
    restore();
    var btn = $('sfRunBtn');
    if (btn && !btn.dataset.sfBound) {
      btn.dataset.sfBound = '1';
      btn.addEventListener('click', run);
    }
    ['sfCapital', 'sfTime', 'sfDrawdown'].forEach(function (id) {
      var el = $(id);
      if (el && !el.dataset.sfBound) {
        el.dataset.sfBound = '1';
        el.addEventListener('keydown', function (ev) { if (ev.key === 'Enter') run(); });
      }
    });
    run();
  }

  window.StrategyFit = { run: run };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
