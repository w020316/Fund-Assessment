"""多端多场景页面走查驱动脚本（只测不改）。

用法:
    python scripts/page_audit.py                      # 全量 7 视口 × 20 页
    python scripts/page_audit.py --viewports quick    # 375/768/1440 三视口，用于快速回归
    python scripts/page_audit.py --base http://127.0.0.1:8055/

输出（默认 .screenshots/page-audit/）:
    results.json   全量单元格结果 + axe + CWV + 键盘 + 200% 缩放
    *.png          ⚠/✘ 与代表图

方法说明:
- Playwright + channel=msedge（本机已安装 Edge，缺失时回退 Chromium）
- 360/375 使用 is_mobile=True + device_scale_factor=2 模拟移动端
- 页面进入一律走真实点击（一级 nav / 移动底栏 → 二级 subtab），不直接改 hash
- 横向滚动 = documentElement.scrollWidth > clientWidth + 1
- 溢出元素 = 可见元素 rect.right > innerWidth + 1 或 rect.left < -1
- LCP/CLS/TBT 用 PerformanceObserver 自采（TBT 用 longtask 时长近似，非 Lighthouse）
- axe-core 缺失（deliverables/ 被 gitignore，CI 无该文件）时跳过该项并打印告警

裁切判定口径（2026-11-05 修正）:
    旧版凡 overflow-x:hidden/clip 且 scrollWidth > clientWidth 即判"文本被裁切"，
    会把 CSS 动画/骨架屏用伪元素撑宽的容器误判为缺陷（实测 .news-ticker 跑马灯
    scrollWidth 17361 / clientWidth 314、.skeleton 微光 515/316，两者均无文本裁切）。
    现改为: 只判定"含直接文本节点"的元素，并对已知动画容器类名豁免。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import time
import traceback

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[1]

ALL_VIEWPORTS = [(360, 640), (375, 667), (768, 1024), (1024, 768), (1200, 800), (1440, 900), (1920, 1080)]
QUICK_VIEWPORTS = [(375, 667), (768, 1024), (1440, 900)]

# (页面容器 id, 中文名, 一级 tab, 二级 subtab 或 None)
PAGES = [
    ("hud-market", "行情·聚合容器", "hud-market", None),
    ("market", "行情总览", "hud-market", "market"),
    ("review", "短线复盘", "hud-market", "review"),
    ("global", "国际市场", "hud-market", "global"),
    ("data", "深度数据", "hud-market", "data"),
    ("hud-holdings", "持仓·聚合容器", "hud-holdings", None),
    ("position", "持仓管理", "hud-holdings", "position"),
    ("fund", "基金管理", "hud-holdings", "fund"),
    ("trade", "交易执行", "hud-holdings", "trade"),
    ("watchboard", "盯盘", "hud-holdings", "watchboard"),
    ("risk", "风险", "hud-holdings", "risk"),
    ("hud-strategy", "策略·聚合容器", "hud-strategy", None),
    ("strategy", "策略中心", "hud-strategy", "strategy"),
    ("ai", "智能分析", "hud-strategy", "ai"),
    ("gate", "出手三问", "hud-strategy", "gate"),
    ("dispatch", "一句话调度", "hud-strategy", "dispatch"),
    ("hud-monitor", "监控·聚合容器", "hud-monitor", None),
    ("monitor", "监控预警", "hud-monitor", "monitor"),
    ("hud-settings", "设置·聚合容器", "hud-settings", None),
    ("config", "系统配置", "hud-settings", "config"),
]

# 已知用伪元素撑宽、不承载文本裁切的动画容器（跑马灯 / 骨架屏微光）
CLIP_WHITELIST = ["news-ticker", "skeleton", "skeleton-line"]

MEASURE_JS = r"""
() => {
  const vw = window.innerWidth, vh = window.innerHeight;
  const de = document.documentElement;
  const sel = (el) => {
    if (!el || el.nodeType !== 1) return '';
    if (el.id) return '#' + el.id;
    let p = el.tagName.toLowerCase();
    if (el.className && typeof el.className === 'string') {
      const c = el.className.trim().split(/\s+/).slice(0, 2).join('.');
      if (c) p += '.' + c;
    }
    const parent = el.parentElement;
    if (parent && parent.id) return '#' + parent.id + ' > ' + p;
    return p;
  };
  const visible = (el) => {
    const cs = getComputedStyle(el);
    return cs.display !== 'none' && cs.visibility !== 'hidden' && cs.opacity !== '0';
  };
  // 只认"含直接文本节点"的元素：伪元素(滚动动画/骨架屏)撑大的 scrollWidth 不算文本裁切
  const hasDirectText = (el) => Array.from(el.childNodes).some(
    (n) => n.nodeType === 3 && (n.textContent || '').trim().length > 0);
  const WHITELIST = ['news-ticker', 'skeleton', 'skeleton-line'];
  const overflow = [], clipped = [], fixed = [];
  const all = document.querySelectorAll('body *');
  for (const el of all) {
    if (!visible(el)) continue;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    if (r.right > vw + 1 || r.left < -1) {
      let scrollableParent = false;
      let p = el.parentElement;
      while (p && p !== document.body) {
        const pcs = getComputedStyle(p);
        if (/(auto|scroll)/.test(pcs.overflowX)) { scrollableParent = true; break; }
        p = p.parentElement;
      }
      if (!scrollableParent && overflow.length < 12) {
        overflow.push({sel: sel(el), tag: el.tagName.toLowerCase(),
          left: Math.round(r.left), right: Math.round(r.right), w: Math.round(r.width),
          txt: (el.textContent || '').trim().slice(0, 24)});
      }
    }
    const cs = getComputedStyle(el);
    if (/hidden|clip/.test(cs.overflowX) && el.scrollWidth > el.clientWidth + 2 && r.width > 40) {
      if (WHITELIST.some((c) => el.classList.contains(c))) continue;
      if (!hasDirectText(el)) continue;
      if (clipped.length < 12) clipped.push({sel: sel(el), sw: el.scrollWidth, cw: el.clientWidth,
        txt: (el.textContent || '').trim().slice(0, 28)});
    }
    if (cs.position === 'fixed') {
      const ratio = (r.width * r.height) / (vw * vh);
      if (ratio > 0.08 && cs.pointerEvents !== 'none' && fixed.length < 8) {
        fixed.push({sel: sel(el), ratio: +ratio.toFixed(3), z: cs.zIndex,
          txt: (el.textContent || '').trim().slice(0, 20)});
      }
    }
  }
  const visCount = (s) => Array.from(document.querySelectorAll(s)).filter(el => {
    const r = el.getBoundingClientRect();
    return visible(el) && r.width > 0 && r.height > 0;
  }).length;
  const activePage = document.querySelector('.page.active');
  const pageBox = activePage ? activePage.getBoundingClientRect() : null;
  const titleEl = document.querySelector('.page.active h2, .page.active .page-title, .page.active h1');
  const titleBox = titleEl ? titleEl.getBoundingClientRect() : null;
  return {
    vw, vh,
    docW: de.scrollWidth, clientW: de.clientWidth,
    hscroll: de.scrollWidth > de.clientWidth + 1,
    overflow, clipped, fixed,
    navVisible: visCount('.nav-item'),
    bottomTabVisible: visCount('.bottom-tab-item'),
    subtabVisible: visCount('.hud-subtab'),
    activePage: activePage ? activePage.id : '',
    pageTop: pageBox ? Math.round(pageBox.top) : null,
    pageW: pageBox ? Math.round(pageBox.width) : null,
    titleVisible: titleBox ? (titleBox.top >= 0 && titleBox.top <= vh && titleBox.width > 0) : null,
    titleText: titleEl ? (titleEl.textContent || '').trim().slice(0, 30) : '',
  };
}
"""

CWVR_JS = r"""
window.__cwv = {lcp: 0, cls: 0, tbt: 0};
try {
  new PerformanceObserver((l) => {
    for (const e of l.getEntries()) window.__cwv.lcp = Math.round(e.startTime);
  }).observe({type: 'largest-contentful-paint', buffered: true});
  new PerformanceObserver((l) => {
    for (const e of l.getEntries()) if (!e.hadRecentInput) window.__cwv.cls += e.value;
  }).observe({type: 'layout-shift', buffered: true});
  new PerformanceObserver((l) => {
    for (const e of l.getEntries()) window.__cwv.tbt += (e.duration - 50);
  }).observe({type: 'longtask', buffered: true});
} catch (e) {}
"""

TOUCH_JS = r"""
() => {
  const out = [];
  const sels = 'a,button,input,select,textarea,[role="button"],[onclick],.nav-item,.bottom-tab-item,.hud-subtab,.tab-item';
  const seen = new Set();
  for (const el of document.querySelectorAll(sels)) {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') continue;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    if (r.bottom < 0 || r.top > window.innerHeight) continue;
    if (r.width >= 48 && r.height >= 48) continue;
    let key = (el.id ? '#' + el.id : el.tagName.toLowerCase() + '|' + (el.textContent || '').trim().slice(0, 18) + '|' + Math.round(r.top));
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({sel: el.id ? '#' + el.id : el.tagName.toLowerCase() + (el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\s+/)[0] : ''),
      w: +r.width.toFixed(1), h: +r.height.toFixed(1),
      txt: (el.textContent || el.getAttribute('aria-label') || '').trim().slice(0, 18)});
  }
  return out;
}
"""

AXE_JS = r"""
async () => {
  const res = await axe.run(document, {runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21a','wcag21aa']}});
  return res.violations.map(v => ({
    id: v.id, impact: v.impact, count: v.nodes.length,
    targets: v.nodes.slice(0, 3).map(n => n.target.join(' ')),
    detail: v.nodes.slice(0, 1).map(n => {
      const any = (n.any || []).find(a => a.id === 'color-contrast');
      return any && any.data ? {ratio: any.data.contrastRatio, fg: any.data.fgColor, bg: any.data.bgColor, font: any.data.fontSize} :
        {summary: (n.failureSummary || '').split('\n').slice(0, 2).join(' ')};
    })[0]
  }));
}
"""

KEYBOARD_JS = r"""
() => {
  const seq = [];
  const el = document.activeElement;
  if (!el) return seq;
  const r = el.getBoundingClientRect();
  const cs = getComputedStyle(el);
  seq.push({sel: el.id ? '#' + el.id : el.tagName.toLowerCase() + (el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\s+/)[0] : ''),
    outline: cs.outlineStyle + ' ' + cs.outlineWidth + ' ' + cs.outlineColor,
    boxShadow: cs.boxShadow.slice(0, 40),
    w: Math.round(r.width), h: Math.round(r.height)});
  return seq;
}
"""


def enter_page(page, base: str, main: str, sub):
    page.goto(base, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(900)
    clicked = None
    nav = page.locator(f'.nav-item[data-page="{main}"]')
    if nav.count() and nav.first.is_visible():
        nav.first.click(force=True, timeout=8000)
        clicked = "nav"
    else:
        bt = page.locator(f'.bottom-tab-item[data-page="{main}"]')
        bt.first.click(force=True, timeout=8000)
        clicked = "bottom-tab"
    page.wait_for_timeout(500)
    if sub:
        st = page.locator(f'.hud-subtab[data-page="{sub}"]')
        if st.count() and st.first.is_visible():
            st.first.click(force=True, timeout=8000)
            clicked += "+subtab"
    try:
        page.wait_for_load_state("networkidle", timeout=6000)
    except Exception:
        pass
    page.wait_for_timeout(1200)
    return clicked


def judge(m, touch):
    reasons = []
    if m["hscroll"]:
        reasons.append({"level": "error", "kind": "hscroll", "detail": f"scrollWidth {m['docW']} > clientWidth {m['clientW']}"})
    if m["overflow"]:
        reasons.append({"level": "error", "kind": "overflow", "detail": f"{len(m['overflow'])} 个元素越出视口"})
    if m["clipped"]:
        reasons.append({"level": "warn", "kind": "clipped", "detail": f"{len(m['clipped'])} 处文本被裁切"})
    if m["navVisible"] == 0 and m["bottomTabVisible"] == 0:
        reasons.append({"level": "error", "kind": "nav", "detail": "一级导航与移动底栏均不可见"})
    if m["titleVisible"] is False:
        reasons.append({"level": "warn", "kind": "title", "detail": "页面标题不在首屏"})
    if touch:
        reasons.append({"level": "warn", "kind": "touch", "detail": f"{len(touch)} 个触摸目标 <48px"})
    status = "✘" if any(r["level"] == "error" for r in reasons) else ("⚠" if reasons else "✔")
    return status, reasons


def main():
    ap = argparse.ArgumentParser(description="多端多场景页面走查（只测不改）")
    ap.add_argument("--base", default="http://127.0.0.1:8055/", help="被检站点的根地址")
    ap.add_argument("--out", default=str(ROOT / ".screenshots" / "page-audit"), help="截图与结果输出目录")
    ap.add_argument("--viewports", choices=["full", "quick"], default="full")
    ap.add_argument("--channel", default="msedge", help="Playwright 浏览器通道，如 msedge / chrome；留空用内置 Chromium")
    args = ap.parse_args()

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    viewports = ALL_VIEWPORTS if args.viewports == "full" else QUICK_VIEWPORTS

    axe_path = ROOT / "deliverables" / "gstack" / "vendor" / "axe.min.js"
    use_axe = axe_path.exists()
    if not use_axe:
        print(f"[warn] 未找到 {axe_path}（deliverables/ 被 gitignore），本轮跳过 axe 无障碍扫描")

    results, axe_results, cwv_results, kb_results, zoom_results = [], [], [], [], []
    t0 = time.time()
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(channel=args.channel or None, headless=True)
        except Exception:
            browser = pw.chromium.launch(headless=True)
        for (w, h) in viewports:
            ctx = browser.new_context(
                viewport={"width": w, "height": h},
                is_mobile=(w < 768),
                has_touch=(w < 768),
                device_scale_factor=2 if w < 768 else 1,
                user_agent=None,
            )
            ctx.add_init_script(CWVR_JS)
            page = ctx.new_page()
            console_errors = []
            page.on("pageerror", lambda e: console_errors.append(str(e)[:120]))
            page.on("console", lambda msg: console_errors.append(msg.text[:120]) if msg.type == "error" else None)
            for (pid, cn, main_tab, sub) in PAGES:
                try:
                    console_errors.clear()
                    path = enter_page(page, args.base, main_tab, sub)
                    m = page.evaluate(MEASURE_JS)
                    touch = page.evaluate(TOUCH_JS) if w in (360, 375) else []
                    status, reasons = judge(m, touch)
                    if console_errors:
                        status = "✘" if status == "✘" else "⚠"
                        reasons.append({"level": "warn", "kind": "js", "detail": f"{len(console_errors)} 条控制台错误: {console_errors[0][:60]}"})
                    cell = {
                        "page": pid, "cn": cn, "w": w, "h": h, "status": status,
                        "clicked": path, "reasons": reasons,
                        "hscroll": m["hscroll"], "docW": m["docW"], "clientW": m["clientW"],
                        "overflow": m["overflow"][:5], "clipped": m["clipped"][:5],
                        "fixed": m["fixed"], "navVisible": m["navVisible"],
                        "bottomTabVisible": m["bottomTabVisible"], "subtabVisible": m["subtabVisible"],
                        "activePage": m["activePage"], "titleVisible": m["titleVisible"],
                        "touch": touch[:10], "touch_total": len(touch),
                        "jsErrors": console_errors[:3],
                    }
                    shot = None
                    if (status != "✔" and w in (360, 1440)) or (w == 1440 and pid in ("market", "position", "config", "fund")):
                        shot = f"{pid}__{w}x{h}__{status.replace('✔','ok').replace('⚠','warn').replace('✘','bad')}.png"
                        page.screenshot(path=str(out / shot), full_page=False)
                        cell["shot"] = shot
                    results.append(cell)
                    cwv = page.evaluate("() => window.__cwv") if w in (375, 1440) else None
                    if cwv:
                        cwv_results.append({"page": pid, "w": w, **{k: round(v, 3) if k == 'cls' else v for k, v in cwv.items()}})
                    if use_axe and w in (375, 1440):
                        try:
                            page.add_script_tag(path=str(axe_path))
                            v = page.evaluate(AXE_JS)
                            axe_results.append({"page": pid, "w": w, "violations": v})
                        except Exception as e:
                            axe_results.append({"page": pid, "w": w, "error": str(e)[:100]})
                    if w == 1440 and pid in ("market", "position", "config"):
                        page.evaluate("() => window.scrollTo(0,0)")
                        seq = []
                        for _ in range(12):
                            page.keyboard.press("Tab")
                            got = page.evaluate(KEYBOARD_JS)
                            if got:
                                seq.append(got[0])
                        kb_results.append({"page": pid, "seq": seq})
                        page.evaluate("() => { document.documentElement.style.zoom = '2'; }")
                        page.wait_for_timeout(300)
                        zm = page.evaluate(MEASURE_JS)
                        zoom_results.append({"page": pid, "hscroll": zm["hscroll"], "docW": zm["docW"],
                                             "clientW": zm["clientW"], "overflow": zm["overflow"][:5],
                                             "clipped": zm["clipped"][:5]})
                        page.evaluate("() => { document.documentElement.style.zoom = ''; }")
                except Exception as e:
                    results.append({"page": pid, "cn": cn, "w": w, "h": h, "status": "✘",
                                    "reasons": [{"level": "error", "kind": "exception", "detail": str(e)[:160]}],
                                    "trace": traceback.format_exc()[-400:]})
            ctx.close()
        browser.close()
    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed_s": round(time.time() - t0, 1),
        "viewports": viewports,
        "cells": results,
        "axe": axe_results,
        "cwv": cwv_results,
        "keyboard": kb_results,
        "zoom200": zoom_results,
    }
    (out / "results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"elapsed_s": payload["elapsed_s"], "cells": len(results),
                      "shots": len(list(out.glob("*.png"))),
                      "size_mb": round(sum(f.stat().st_size for f in out.glob("*.png")) / 1e6, 2)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
