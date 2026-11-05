"""阶段三收尾闭环的修复复测探针（同一套口径，改前/改后可比）。

检查项:
  1. 交易页 .btn-danger / .btn-success 默认、hover、active、disabled 四态对比度实测
  2. #globalMarketSelect / #scriptCategorySelect / #scriptSceneSelect 的 axe select-name
  3. 375px 下触摸目标:原始矩形 <48px 数量,以及"透明热区后的有效命中区" <48px 数量
  4. 焦点可见性:#cfg-admin-token 与 position 页行内链接的 focus-visible 焦点环
  5. risk 页基金名省略号处 title 全名覆盖情况(1200 / 1920)

用法:
    python scripts/a11y_probe.py --base http://127.0.0.1:8055/ --out .screenshots/stage3-fix-2026-11-05
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

from playwright.sync_api import sync_playwright

# 允许以 `python scripts/a11y_probe.py` 直接运行（同目录模块可导入）
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from page_audit import PAGES, enter_page  # noqa: E402

# 关闭本轮新增热区,用于同一套探针测量"改前"基线
BASELINE_CSS = """
#connStatus::after, .st6-push-toggle::after, .st6-threshold-toggle::after { content: none !important; }
.notice-info a { padding: 0 !important; margin: 0 !important; }
"""

# 本轮加过透明热区的目标,逐个复核"≥48×48 且不遮挡相邻可点元素"
HOT_ZONE_SEL = {"#connStatus", ".notice-info a", "#st6PushEnabled", "#st6PushThreshold"}

CONTRAST_JS = r"""
(sel) => {
  const parse = (s) => {
    const m = String(s).match(/rgba?\(([^)]+)\)/);
    if (!m) return [0, 0, 0, 1];
    const p = m[1].split(',').map(Number);
    return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1];
  };
  const blend = (fg, bg) => {
    const a = fg[3];
    return [a * fg[0] + (1 - a) * bg[0], a * fg[1] + (1 - a) * bg[1], a * fg[2] + (1 - a) * bg[2], 1];
  };
  const lum = (c) => {
    const f = (v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]);
  };
  const ratio = (a, b) => {
    const la = lum(a), lb = lum(b);
    return +(((Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05)).toFixed(2));
  };
  // 祖先背景从外到内叠加;el 自身的 opacity 会把文字与底色一起压向该背景
  const backdropBehind = (el) => {
    const chain = [];
    let p = el.parentElement;
    while (p && p !== document.documentElement) { chain.push(p); p = p.parentElement; }
    let bg = [13, 17, 23, 1];
    for (const node of chain.reverse()) {
      const b = parse(getComputedStyle(node).backgroundColor);
      if (b[3] > 0) bg = blend(b, bg);
    }
    return bg;
  };
  const el = document.querySelector(sel);
  if (!el) return null;
  const cs = getComputedStyle(el);
  const op = parseFloat(cs.opacity);
  const behind = backdropBehind(el);
  const ownBg = blend(parse(cs.backgroundColor), behind);
  const bg = blend([ownBg[0], ownBg[1], ownBg[2], op], behind);
  const fg = blend([...parse(cs.color).slice(0, 3), op], behind);
  return {
    color: cs.color, background: cs.backgroundColor, opacity: op,
    fontSize: cs.fontSize, fontWeight: cs.fontWeight,
    backdrop: 'rgb(' + behind.slice(0, 3).map(Math.round).join(',') + ')',
    fg: 'rgb(' + fg.slice(0, 3).map(Math.round).join(',') + ')',
    bg: 'rgb(' + bg.slice(0, 3).map(Math.round).join(',') + ')',
    ratio: ratio(fg, bg),
  };
}
"""

TOUCH_EFFECTIVE_JS = r"""
() => {
  const vw = window.innerWidth, vh = window.innerHeight;
  const ACTIVE = 'a,button,input,select,textarea,[role="button"],[onclick],.nav-item,.bottom-tab-item,.hud-subtab,.tab-item';
  const visible = (el) => { const cs = getComputedStyle(el); return cs.display !== 'none' && cs.visibility !== 'hidden' && parseFloat(cs.opacity) !== 0; };
  const labelOf = (el) => el.closest('label');
  const isHit = (el, x, y) => {
    const t = document.elementFromPoint(x, y);
    if (!t) return false;
    if (t === el || el.contains(t)) return true;
    const lab = labelOf(el);
    return !!(lab && (t === lab || lab.contains(t)));
  };
  const isForeign = (el, x, y) => {
    const t = document.elementFromPoint(x, y);
    if (!t || t === el || el.contains(t)) return false;
    const lab = labelOf(el);
    if (lab && (t === lab || lab.contains(t))) return false;
    return !!(t.closest && t.closest(ACTIVE));
  };
  const out = [], seen = new Set();
  for (const el of document.querySelectorAll(ACTIVE)) {
    if (!visible(el)) continue;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    if (r.bottom < 0 || r.top > vh) continue;
    // 只统计原始矩形 <48 的目标（与阶段三 76 处同一口径），避免把已达标元素算进来
    if (r.width >= 48 && r.height >= 48) continue;
    const key = (el.id ? '#' + el.id : el.tagName.toLowerCase() + '|' + (el.textContent || '').trim().slice(0, 18) + '|' + Math.round(r.top));
    if (seen.has(key)) continue;
    seen.add(key);
    // ±24px 以 4px 步长采样:足够覆盖 48×48 热区,又能识别相邻元素被遮挡
    const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    let minX = null, maxX = null, minY = null, maxY = null, overlaps = 0;
    for (let dx = -24; dx <= 24; dx += 4) {
      for (let dy = -24; dy <= 24; dy += 4) {
        const x = cx + dx, y = cy + dy;
        if (x < 0 || y < 0 || x >= vw || y >= vh) continue;
        if (isHit(el, x, y)) {
          if (minX === null || x < minX) minX = x;
          if (maxX === null || x > maxX) maxX = x;
          if (minY === null || y < minY) minY = y;
          if (maxY === null || y > maxY) maxY = y;
        } else if (isForeign(el, x, y)) overlaps++;
      }
    }
    out.push({
      sel: el.id ? '#' + el.id : el.tagName.toLowerCase() + (el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\s+/)[0] : ''),
      tag: el.tagName.toLowerCase(),
      rawW: +r.width.toFixed(1), rawH: +r.height.toFixed(1),
      effW: minX === null ? 0 : +(maxX - minX + 4).toFixed(1),
      effH: minY === null ? 0 : +(maxY - minY + 4).toFixed(1),
      overlaps,
      txt: (el.textContent || el.getAttribute('aria-label') || '').trim().slice(0, 18),
    });
  }
  return out;
}
"""

FOCUS_JS = r"""
(sel) => {
  const el = document.querySelector(sel);
  if (!el) return null;
  const cs = getComputedStyle(el);
  return {
    sel: sel,
    focusVisible: el.matches(':focus-visible'),
    outlineStyle: cs.outlineStyle, outlineWidth: cs.outlineWidth,
    outlineColor: cs.outlineColor, outlineOffset: cs.outlineOffset,
  };
}
"""

TITLES_JS = r"""
() => {
  const items = Array.from(document.querySelectorAll('.st6-rb-name'));
  return {
    total: items.length,
    withTitle: items.filter((el) => (el.getAttribute('title') || '').trim().length > 0).length,
    truncated: items.filter((el) => el.scrollWidth > el.clientWidth + 1).length,
    sample: items.slice(0, 3).map((el) => ({
      text: (el.textContent || '').trim(), title: el.getAttribute('title') || '',
      truncated: el.scrollWidth > el.clientWidth + 1,
    })),
  };
}
"""


def goto_page(page, base, main, sub):
    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass
    return enter_page(page, base, main, sub)


def run_contrast(page, base):
    goto_page(page, base, "hud-holdings", "trade")
    page.wait_for_timeout(600)
    results = []
    for sel in (".btn-danger", ".btn-success"):
        target = f"#page-trade {sel}"
        btn = page.locator(target).first
        if not btn.count():
            results.append({"sel": sel, "error": "not found"})
            continue
        btn.scroll_into_view_if_needed()
        box = btn.bounding_box()
        states = {}
        states["default"] = page.evaluate(CONTRAST_JS, target)
        if box:
            page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            page.wait_for_timeout(400)
            states["hover"] = page.evaluate(CONTRAST_JS, target)
            page.mouse.down()
            page.wait_for_timeout(400)
            states["active"] = page.evaluate(CONTRAST_JS, target)
            page.mouse.up()
            page.mouse.move(0, 0)
            page.wait_for_timeout(400)
        # 模拟 withLoading():disabled + inline opacity .6
        page.evaluate("""(t) => { const el = document.querySelector(t); el.disabled = true; el.style.opacity = '0.6'; }""", target)
        page.wait_for_timeout(400)
        states["disabled"] = page.evaluate(CONTRAST_JS, target)
        page.evaluate("""(t) => { const el = document.querySelector(t); el.disabled = false; el.style.opacity = ''; }""", target)
        results.append({"sel": sel, "states": states})
    return results


def run_selects(page, base, axe_path):
    out = []
    targets = [("ai", "hud-strategy", "ai"), ("global", "hud-market", "global")]
    for pid, main, sub in targets:
        goto_page(page, base, main, sub)
        page.wait_for_timeout(400)
        if not axe_path.exists():
            out.append({"page": pid, "error": "axe missing"})
            continue
        page.add_script_tag(path=str(axe_path))
        res = page.evaluate("""async () => {
          const r = await axe.run(document, {runOnly: {type: 'rule', values: ['select-name']}});
          return r.violations.map(v => ({id: v.id, impact: v.impact, nodes: v.nodes.map(n => n.target.join(' '))}));
        }""")
        labels = page.evaluate("""(ids) => ids.map((id) => {
          const el = document.getElementById(id);
          return el ? {id, ariaLabel: el.getAttribute('aria-label') || '',
            labelledBy: el.getAttribute('aria-labelledby') || ''} : {id, missing: true};
        })""", ["globalMarketSelect", "scriptCategorySelect", "scriptSceneSelect"])
        out.append({"page": pid, "violations": res, "labels": labels})
    return out


TOUCH_VIEWPORTS = [(360, 640), (375, 667)]


def run_touch(browser, base, with_baseline):
    rows = []
    for (w, h) in TOUCH_VIEWPORTS:
        ctx = browser.new_context(viewport={"width": w, "height": h}, is_mobile=True, has_touch=True, device_scale_factor=2)
        page = ctx.new_page()
        for (pid, cn, main, sub) in PAGES:
            goto_page(page, base, main, sub)
            items = page.evaluate(TOUCH_EFFECTIVE_JS)
            rows.append({"page": pid, "cn": cn, "w": w, "items": items,
                         "raw_lt48": sum(1 for i in items if i["rawW"] < 48 or i["rawH"] < 48),
                         "eff_lt48": sum(1 for i in items if i["effW"] < 48 or i["effH"] < 48),
                         "overlaps": sum(i["overlaps"] for i in items)})
        ctx.close()
    baseline = None
    if with_baseline:
        rows_b = []
        for (w, h) in TOUCH_VIEWPORTS:
            ctx = browser.new_context(viewport={"width": w, "height": h}, is_mobile=True, has_touch=True, device_scale_factor=2)
            page = ctx.new_page()
            for (pid, cn, main, sub) in PAGES:
                goto_page(page, base, main, sub)
                page.add_style_tag(content=BASELINE_CSS)
                page.wait_for_timeout(50)
                items = page.evaluate(TOUCH_EFFECTIVE_JS)
                rows_b.append({"page": pid, "w": w,
                               "raw_lt48": sum(1 for i in items if i["rawW"] < 48 or i["rawH"] < 48),
                               "eff_lt48": sum(1 for i in items if i["effW"] < 48 or i["effH"] < 48)})
            ctx.close()
        baseline = {"raw_lt48": sum(r["raw_lt48"] for r in rows_b),
                    "eff_lt48": sum(r["eff_lt48"] for r in rows_b), "pages": rows_b}
    return {"pages": rows, "raw_lt48": sum(r["raw_lt48"] for r in rows),
            "eff_lt48": sum(r["eff_lt48"] for r in rows),
            "overlaps": sum(r["overlaps"] for r in rows), "baseline": baseline}


def run_focus(page, base):
    out = {}
    goto_page(page, base, "hud-settings", "config")
    page.wait_for_timeout(500)
    page.evaluate("() => document.getElementById('cfg-admin-token').focus()")
    page.wait_for_timeout(400)
    out["cfg-admin-token"] = page.evaluate(FOCUS_JS, "#cfg-admin-token")

    # #connStatus 的状态点只有键盘聚焦才触发 :focus-visible，用 Tab 循环命中
    page.evaluate("() => document.getElementById('cfg-admin-token').blur()")
    hit = None
    for _ in range(40):
        page.keyboard.press("Tab")
        if page.evaluate("() => document.activeElement && document.activeElement.id === 'connStatus'"):
            page.wait_for_timeout(350)
            hit = page.evaluate(FOCUS_JS, "#connStatus")
            break
    out["connStatus"] = hit or {"error": "未在 40 次 Tab 内命中"}

    goto_page(page, base, "hud-holdings", "position")
    page.wait_for_timeout(500)
    link = page.evaluate("""() => {
      const el = document.querySelector('.notice-info a');
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return {w: +r.width.toFixed(1), h: +r.height.toFixed(1)};
    }""")
    out["notice-link-rect"] = link
    style = page.add_style_tag(content=".notice-info a{padding:0 !important;margin:0 !important;}")
    page.wait_for_timeout(50)
    out["notice-link-rect-baseline"] = page.evaluate("""() => {
      const el = document.querySelector('.notice-info a');
      const r = el.getBoundingClientRect();
      return {w: +r.width.toFixed(1), h: +r.height.toFixed(1)};
    }""")
    style.evaluate("el => el.remove()")
    hit = None
    for _ in range(60):
        page.keyboard.press("Tab")
        info = page.evaluate("""() => {
          const el = document.activeElement;
          if (!el) return null;
          const a = el.closest && el.closest('.notice-info a');
          return a ? {ok: true} : null;
        }""")
        if info:
            page.wait_for_timeout(350)
            hit = page.evaluate("""() => {
              const el = document.activeElement.closest('.notice-info a');
              const cs = getComputedStyle(el);
              return {sel: '.notice-info a', focusVisible: el.matches(':focus-visible'),
                outlineStyle: cs.outlineStyle, outlineWidth: cs.outlineWidth,
                outlineColor: cs.outlineColor, outlineOffset: cs.outlineOffset};
            }""")
            break
    out["notice-info a"] = hit or {"error": "未在 60 次 Tab 内命中"}
    return out


def run_titles(browser, base):
    out = {}
    for (w, h) in ((1200, 800), (1920, 1080)):
        ctx = browser.new_context(viewport={"width": w, "height": h})
        page = ctx.new_page()
        goto_page(page, base, "hud-holdings", "risk")
        page.wait_for_timeout(1200)
        out[f"{w}x{h}"] = page.evaluate(TITLES_JS)
        ctx.close()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8055/")
    ap.add_argument("--out", default=str(ROOT / ".screenshots" / "stage3-fix-2026-11-05"))
    ap.add_argument("--skip-touch", action="store_true")
    args = ap.parse_args()

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    axe_path = ROOT / "deliverables" / "gstack" / "vendor" / "axe.min.js"
    payload = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "base": args.base}

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(channel="msedge", headless=True)
        except Exception:
            browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        payload["contrast"] = run_contrast(page, args.base)
        payload["selects"] = run_selects(page, args.base, axe_path)
        payload["focus"] = run_focus(page, args.base)
        page.close()
        if not args.skip_touch:
            payload["touch"] = run_touch(browser, args.base, with_baseline=True)
        payload["titles"] = run_titles(browser, args.base)
        browser.close()

    (out / "a11y.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    # 本轮新增热区的逐个复核:有效命中区是否 ≥48×48、是否遮挡相邻可点元素
    hot_zones = []
    for row in payload.get("touch", {}).get("pages", []):
        for i in row["items"]:
            if i["sel"] in HOT_ZONE_SEL:
                hot_zones.append({"page": row["page"], **i, "pass": i["effW"] >= 48 and i["effH"] >= 48 and i["overlaps"] == 0})
    payload["hot_zones"] = hot_zones
    (out / "a11y.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    # 打印摘要,便于直接贴进报告
    print(json.dumps({
        "contrast": payload["contrast"],
        "selects": payload["selects"],
        "touch_raw_lt48": payload.get("touch", {}).get("raw_lt48"),
        "touch_eff_lt48": payload.get("touch", {}).get("eff_lt48"),
        "touch_baseline": payload.get("touch", {}).get("baseline", {}).get("raw_lt48"),
        "touch_overlaps": payload.get("touch", {}).get("overlaps"),
        "hot_zones": hot_zones,
        "focus": payload["focus"],
        "titles": payload["titles"],
    }, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
