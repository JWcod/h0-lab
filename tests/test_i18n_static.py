"""英文版的靜態檢查:新增介面文字沒有翻譯就會在這裡失敗。

2026-11-03 起不再用字串比對擋買賣用語(技術面的支撐壓力、技術訊號是資料頁的統計描述);
不提供內建評等與交易計畫改由 API 層保證(lab/public.py,tests/test_public_api.py)。
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from lab import i18n
from scripts import i18n_scan as scan

ROOT = Path(__file__).resolve().parent.parent
CATALOG = i18n._catalog()
NODE = shutil.which("node")

PH = re.compile(r"\{\w+\}")


def test_every_ui_string_is_translated():
    miss = scan.missing()
    assert not miss, f"這些介面文字沒有英文(加進 web/js/i18n/catalog.en.js):{miss}"


def test_js_chinese_literals_are_tagged():
    left = scan.js_untagged()
    assert not left, f"JS 中文字面值要包 ZH(資料鍵用 KEY):{left[:20]}"


def test_catalog_values_are_english_with_same_placeholders():
    assert not [k for k, v in CATALOG.items() if i18n.CJK.search(v)], "譯文不得含中文"
    bad = [k for k, v in CATALOG.items() if sorted(PH.findall(k)) != sorted(PH.findall(v)) and "{變數名}" not in k]
    assert not bad, f"佔位符要一致:{bad[:20]}"


def test_html_has_disclaimer_and_language_switch():
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert "navLang" in html
    assert "非投資建議" in html or "不是投資建議" in html


def test_translate_patterns_and_fallback():
    assert i18n.translate("模型等級 A") == CATALOG["模型等級 A"]
    assert i18n.translate("買進") == CATALOG["買進"], "舊紀錄的等級名稱也要有英文"
    assert i18n.translate("Already English") == "Already English"
    assert i18n.translate("完全不在目錄裡的句子") == "完全不在目錄裡的句子", "翻不到要保留原文"
    assert i18n.translate("存貨\u3002") == CATALOG["存貨"] + "."


def test_localize_leaves_external_text_and_keys_alone():
    rep = {"外資": {"name": "存貨"},
           "news": {"items": [{"title": "存貨大增。", "link": "http://x", "publisher": "存貨", "events": ["存貨"]}]},
           "meta": {"symbol": "2330.TW", "name": "存貨", "summary": "存貨"},
           "notes": "存貨"}
    out = i18n.localize(rep)
    en = CATALOG["存貨"]
    assert out["外資"]["name"] == en and "外資" in out, "字典的鍵不動,值要翻"
    item = out["news"]["items"][0]
    assert item["title"] == "存貨大增。" and item["publisher"] == "存貨", "新聞標題與來源不翻"
    assert item["events"] == [en]
    assert out["meta"]["name"] == "存貨" and out["meta"]["summary"] == "存貨", "公司名稱與簡介不翻"
    assert out["notes"] == "存貨"
    assert i18n.localize({"zh-TW": "存貨"})["en"] == en


_NODE_EN = r"""
// 模擬瀏覽器英文環境,確認模板標籤真的換成英文、語序與代入值正確
const store = {};
globalThis.localStorage = { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = v; } };
globalThis.document = { cookie: "", documentElement: {} };
Object.defineProperty(globalThis, "navigator", { value: { languages: ["en-US"] }, configurable: true });
const I = await import(process.argv[2]);
const n = 3;
console.log(JSON.stringify({
  isEn: I.isEn,
  cookie: document.cookie,
  plain: I.ZH`存貨`,
  html: I.ZH`<b title="存貨">存貨</b>`,
  val: I.ZH`近 14 日`,

  key: I.KEY`存貨`,
  dyn: I.dyn("存貨"),
  unknown: I.dyn("目錄沒有的字"),
  t: I.t("wizard.not.a.key"),
  loc: I.loc({ "zh-TW": "中", en: "En" }),
}));
"""


@pytest.mark.skipif(NODE is None, reason="沒有 node")
def test_frontend_tag_in_english_browser(tmp_path):
    script = tmp_path / "run.mjs"
    script.write_text(_NODE_EN, encoding="utf-8")
    r = subprocess.run([NODE, str(script), (ROOT / "web/js/i18n/index.js").as_uri()],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    o = json.loads(r.stdout)
    en = CATALOG["存貨"]
    assert o["isEn"] is True and "h0lab_lang=en" in o["cookie"]
    assert o["plain"] == en and o["html"] == f'<b title="{en}">{en}</b>'
    assert o["val"] == CATALOG["近 14 日"]
    assert o["key"] == "存貨" and o["dyn"] == en and o["unknown"] == "目錄沒有的字"
    assert o["t"] == "wizard.not.a.key" and o["loc"] == "En"
