#!/usr/bin/env python3
"""レポートのテンプレートにデータを差し込んで、配布用のHTMLを書き出す。

  python3 scripts/build_report_data.py   # 先にデータを作る
  python3 scripts/build_report.py
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "reports" / "report_template.html"
DATA = ROOT / "data" / "processed" / "report_data.json"
OUTPUT = ROOT / "reports" / "national_population_report.html"

def main() -> None:
    html = TEMPLATE.read_text(encoding="utf-8")
    data = DATA.read_text(encoding="utf-8")
    if "__DATA__" not in html:
        raise SystemExit("テンプレートに差し込み位置（__DATA__）がありません。")
    # </script> がJSON内に現れるとscriptタグが途中で閉じてしまうため保険をかける
    data = data.replace("</", "<\\/")
    OUTPUT.write_text(html.replace("__DATA__", data), encoding="utf-8")
    print(f"{OUTPUT.relative_to(ROOT)}  {OUTPUT.stat().st_size/1024:.0f}KB")

if __name__ == "__main__":
    main()
