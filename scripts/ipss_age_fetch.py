#!/usr/bin/env python3
"""社人研の「男女・年齢（5歳）階級別将来推計人口」を取り込む。

都道府県ごとに1ファイル（47本）。各ファイルは市区町村ごとのシートに分かれていて、
2020年の実績を起点に2050年まで5年刻みの推計が入っている。
市区町村には5歳階級の実績統計がe-Stat側にないため、この推計表で補う。

  python3 scripts/ipss_age_fetch.py
  python3 scripts/ipss_age_fetch.py --no-download
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from pathlib import Path

import openpyxl
import requests

BASE = "https://www.ipss.go.jp/pp-shicyoson/j/shicyoson23/3kekka/Municipalities"
ROOT = Path(__file__).resolve().parent.parent
XLSX_DIR = ROOT / "data" / "ipss" / "age"
OUT = ROOT / "data" / "processed" / "age_bands_future.csv"
FIELDS = ["area_code", "area_name", "pref_name", "area_level", "year", "band", "value"]


def download(sleep: float = 1.5) -> None:
    XLSX_DIR.mkdir(parents=True, exist_ok=True)
    for i in range(1, 48):
        name = "%02d.xlsx" % i
        dest = XLSX_DIR / name
        if dest.exists():
            continue
        res = requests.get(f"{BASE}/{name}", timeout=180)
        res.raise_for_status()
        dest.write_bytes(res.content)
        print(f"  {name} を取得しました（{len(res.content)/1024:.0f}KB）")
        time.sleep(sleep)


def parse_sheet(ws) -> tuple[str, str, list[dict]]:
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    code, name = "", ""
    for r in rows[:5]:
        if r and r[0] and re.fullmatch(r"\d{4,5}", str(r[0]).strip()):
            code = str(r[0]).strip().zfill(5)
            name = str(r[1] or "").strip()
            break
    if not code:
        return "", "", []

    # 「男女計」の見出し行を探し、その行の年の並びを読む
    head = None
    for i, r in enumerate(rows):
        if r and str(r[0] or "").strip() == "男女計":
            head = i
            break
    if head is None:
        return code, name, []
    years = []
    for col, cell in enumerate(rows[head]):
        m = re.match(r"(\d{4})年", str(cell or ""))
        if m and col >= 1:
            years.append((col, int(m.group(1))))
            if len(years) >= 7:      # 男女計のぶんだけ（男・女の列は使わない）
                break

    out = []
    for r in rows[head + 1:]:
        if not r or not r[0]:
            continue
        band = str(r[0]).replace("\u3000", "").strip()
        if "再掲" in band:
            continue
        if band != "総数" and not re.match(r"^\d+[～~]\d+歳$|^\d+歳以上$|^\d+歳[～~]$", band):
            continue
        band = band.replace("歳～", "歳以上")
        for col, year in years:
            raw = r[col] if col < len(r) else None
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            out.append({"code": code, "name": name, "year": year,
                        "band": band.replace("~", "～"), "value": int(value)})
    return code, name, out


def main() -> None:
    ap = argparse.ArgumentParser(description="社人研 5歳階級別推計人口の取り込み")
    ap.add_argument("--no-download", action="store_true")
    args = ap.parse_args()

    if not args.no_download:
        print("社人研サイトから47都道府県分を取得します。")
        download()

    rows: list[dict] = []
    for i in range(1, 48):
        path = XLSX_DIR / ("%02d.xlsx" % i)
        if not path.exists():
            print(f"  {path.name} がありません。飛ばします。", file=sys.stderr)
            continue
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        pref_name = ""
        for sheet in wb.sheetnames:
            code, name, data = parse_sheet(wb[sheet])
            if not data:
                continue
            level = "pref" if code.endswith("000") else "city"
            if level == "pref":
                pref_name = name
            for d in data:
                rows.append({
                    "area_code": d["code"], "area_name": d["name"],
                    "pref_name": pref_name, "area_level": level,
                    "year": d["year"], "band": d["band"], "value": d["value"],
                })
        wb.close()
        print(f"  {path.name}: {pref_name}  累計 {len(rows):,}行")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    areas = len({r["area_code"] for r in rows})
    years = sorted({r["year"] for r in rows})
    bands = sorted({r["band"] for r in rows})
    print(f"\n{OUT.relative_to(ROOT)}  {len(rows):,}行 / 地域{areas}件 / "
          f"{years[0]}〜{years[-1]} / 階級{len(bands)}区分")


if __name__ == "__main__":
    main()
