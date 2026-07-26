# -*- coding: utf-8 -*-
"""
update-market.py — is-checker から相場を取得し market-live.js を再生成する

使い方:
  python update-market.py            # 取得→生成→変更があれば git commit & push
  python update-market.py --no-push  # 取得→生成のみ（ローカル確認用）

設計メモ:
- ブラウザからの直接fetchは is-checker が CORS ヘッダを返さないため恒久的に失敗する。
  そこでサーバーサイドで取得し、生成した market-live.js を GitHub Pages に配信する。
- 差益は「最高買取 − 定価」で自前再計算する。is-checker の差益列は 2026/07 の
  Apple値上げ後も旧定価ベースのままで信用できない（2026/07/26 実測で確認）。
- 取得元URL・世代ラベルは gen-config.js から読む（iPhone 18 移行時も本ファイルは無編集でよい）。
"""
import json
import re
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# data-shop 属性 → 表示名（未知のキーは属性値をそのまま使う）
SHOP_NAMES = {
    "mix": "モバイルミックス", "kaikyo": "海峡通信", "1chome": "買取一丁目",
    "homura": "買取ホムラ", "rudeya": "買取ルデヤ", "rakuen": "買取楽園",
    "wiki": "買取WIKI", "モバステ": "モバステ", "morimori": "森森買取",
    "shouten": "買取商店", "空間": "空間", "enoking": "エノキング",
    "somurie": "ソムリエ",
}
# is-checker の種別 → 買取パネルの variant 名
TYPE_LABELS = {"Max": "Pro Max", "Pro": "Pro", "無印": "無印", "Air": "Air"}


def clean(html_fragment):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html_fragment)).strip()


def first_num(text):
    m = re.search(r"-?[0-9][0-9,]*", text or "")
    return int(m.group(0).replace(",", "")) if m else 0


def main():
    push = "--no-push" not in sys.argv

    gen_src = (ROOT / "gen-config.js").read_text(encoding="utf-8")
    url = re.search(r"isCheckerUrl:\s*'([^']+)'", gen_src).group(1)
    label = re.search(r"label:\s*'([^']+)'", gen_src).group(1)

    html = urllib.request.urlopen(
        urllib.request.Request(url, headers=UA), timeout=30
    ).read().decode("utf-8", "ignore")

    table = re.search(r"<table[^>]*>(.*?)</table>", html, re.S).group(1)
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.S)

    # 店舗列の順序（最初のデータ行の data-shop 属性から）
    shop_keys = re.findall(r'data-shop="([^"]+)"', rows[2])
    stores = [SHOP_NAMES.get(k, k) for k in shop_keys]

    market = {}          # "Max|256|シルバー" → {type,cap,color,teika,margin,best}
    variants = {}        # "Pro Max" → {colors:[], rows:{cap: {apple, byColor}}}
    for row in rows[2:]:
        cells = re.findall(r"<td([^>]*)>(.*?)</td>", row, re.S)
        if len(cells) < 6:
            continue
        typ = clean(cells[0][1])
        if typ not in TYPE_LABELS:
            continue
        cap, color = clean(cells[1][1]), clean(cells[2][1])
        teika = first_num(clean(cells[3][1]))
        margin_site = first_num(clean(cells[4][1]))
        prices = {}
        for attrs, body in cells:
            m = re.search(r'data-shop="([^"]+)"', attrs)
            if m:
                main_m = re.search(r'<span class="main"[^>]*>(.*?)</span>', body, re.S)
                prices[m.group(1)] = first_num(clean(main_m.group(1) if main_m else body))
        ordered = [prices.get(k, 0) for k in shop_keys]
        valid = [p for p in ordered if 10000 < p < 1000000]
        best = max(valid) if valid else 0
        margin = best - teika if best else margin_site  # ← 自前再計算（サイトの差益列は使わない）
        market[f"{typ}|{cap}|{color}"] = {
            "type": typ, "cap": cap, "color": color,
            "teika": teika, "margin": margin, "best": best,
        }
        v_label = TYPE_LABELS[typ]
        v = variants.setdefault(v_label, {"colors": [], "rows": {}})
        if color not in v["colors"]:
            v["colors"].append(color)
        cap_full = cap if cap.upper().endswith("TB") else f"{cap}GB"
        r = v["rows"].setdefault(cap_full, {"cap": cap_full, "apple": teika, "byColor": {}})
        r["byColor"][color] = ordered

    if not market:
        print("ERROR: 相場テーブルを1行も解析できませんでした。ページ構造が変わった可能性があります。")
        sys.exit(1)

    m_upd = re.search(r"ストア最終確認：\s*([0-9/]+)\s*\(([0-9:]+)\)", html)
    updated = f"{m_upd.group(1)} {m_upd.group(2)}" if m_upd else datetime.now().strftime("%Y/%m/%d %H:%M")

    buyback = {
        "updated": updated,
        "stores": stores,
        "variants": {k: {"colors": v["colors"], "rows": list(v["rows"].values())}
                     for k, v in variants.items()},
    }

    def js(obj):
        return json.dumps(obj, ensure_ascii=False, indent=1)

    out = f"""/* ============================================================================
 * market-live.js — update-market.py が自動生成（手編集禁止）
 * 取得元: {url}
 * 生成: {datetime.now().strftime("%Y/%m/%d %H:%M")} ／ 店舗最終確認: {updated}
 * 差益は「最高買取 − 定価」で再計算済み（サイトの差益列は旧定価ベースのため不使用）
 * ========================================================================== */
(function () {{
  if (!window.GEN) return;
  window.GEN.marketAsOf = {json.dumps(updated, ensure_ascii=False)};
  window.GEN.marketFallback = {js(market)};
  window.GEN.buyback = {js(buyback)};
  window.GEN.buybackData = Object.fromEntries(
    Object.entries(window.GEN.buyback.variants).map(function (e) {{
      return [window.GEN.label + " " + e[0], e[1]];
    }})
  );
  window.GEN.buybackDefault = window.GEN.label + " " + Object.keys(window.GEN.buyback.variants)[0];
  /* モデルタグ更新（同型・同容量の色違い中の最大差益を代表値に） */
  (window.GEN.models || []).forEach(function (m) {{
    var typ = m.n === "Pro Max" ? "Max" : m.n;
    var mg = null;
    Object.keys(window.GEN.marketFallback).forEach(function (k) {{
      var e = window.GEN.marketFallback[k];
      if (e.type !== typ) return;
      if (String(m.c).toLowerCase().indexOf(String(e.cap).toLowerCase()) !== 0) return;
      if (mg === null || e.margin > mg) mg = e.margin;
    }});
    if (mg !== null) {{
      m.ok = mg > 0;
      m.tag = (mg > 0 ? "+" : "") + mg.toLocaleString() + "円";
    }}
  }});
}})();
"""
    target = ROOT / "market-live.js"
    old = target.read_text(encoding="utf-8") if target.exists() else ""
    target.write_text(out, encoding="utf-8", newline="\n")

    top = sorted(market.values(), key=lambda e: e["margin"], reverse=True)[:3]
    print(f"解析 {len(market)} 件 ／ 店舗 {len(stores)} ／ 最終確認 {updated}")
    for e in top:
        print(f"  {e['type']} {e['cap']} {e['color']}: 差益 {e['margin']:+,}円 (定価 {e['teika']:,} / 最高 {e['best']:,})")

    # 実質変化なし（生成時刻コメント行のみの差分）ならコミットしない
    strip_ts = lambda s: re.sub(r" \* 生成: [^\n]+\n", "", s)
    if strip_ts(old) == strip_ts(out):
        print("相場に変化なし — コミットしません。")
        return
    if not push:
        print("--no-push 指定のためコミットせず終了。")
        return
    subprocess.run(["git", "add", "market-live.js"], cwd=ROOT, check=True)
    subprocess.run(
        ["git", "commit", "-m", f"chore: 相場データ自動更新（{updated}）"],
        cwd=ROOT, check=True,
    )
    subprocess.run(["git", "push"], cwd=ROOT, check=True)
    print("GitHub Pages へ push 済み。")


if __name__ == "__main__":
    main()
