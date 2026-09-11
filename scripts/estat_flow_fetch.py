#!/usr/bin/env python3
"""住民基本台帳人口移動報告から「どこから来て、どこへ出て行ったか」を取得する。

この統計表は地域の持ち方が特殊で、
  area  = 移動前の住所地（転入元）
  cat01 = 移動後の住所地（転入先）
という組み合わせで1行になっている。ほかの取得スクリプトとは形が違うため分けてある。

  python3 scripts/estat_flow_fetch.py            # 設定した全年を取得
  python3 scripts/estat_flow_fetch.py --year 2025
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path

import requests

API = "https://api.e-stat.go.jp/rest/3.0/app/json/getStatsData"
ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "processed"

# 「移動前の住所地別転入者数 －都道府県，市区町村」の年別の統計表ID
TABLES = {
    2020: "0003420513",
    2021: "0003448460",
    2022: "0004003462",
    2023: "0004014382",
    2024: "0004026702",
    2025: "0004044330",
}
NATIONALITY_ALL = "60000"   # 国籍を分けない「移動者」
MAX_LIMIT = 100_000
FIELDS = ["year", "dest_code", "origin_code", "value"]
AREA_FIELDS = ["code", "name", "level", "parent"]


def as_list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def fetch_year(app_id: str, year: int, table_id: str, sleep: float,
               use_cache: bool) -> list[dict]:
    values, names = [], {}
    start = None
    page = 1
    while True:
        cache = RAW / f"flow_{year}_{table_id}_p{page}.json"
        if use_cache and cache.exists():
            data = json.loads(cache.read_text(encoding="utf-8"))
        else:
            params = {"appId": app_id, "statsDataId": table_id, "limit": MAX_LIMIT,
                      "cdCat02": NATIONALITY_ALL, "metaGetFlg": "Y", "cntGetFlg": "N"}
            if start:
                params["startPosition"] = start
            for attempt in range(5):
                time.sleep(sleep)
                try:
                    res = requests.get(API, params=params, timeout=180)
                    res.raise_for_status()
                    data = res.json()
                    break
                except (requests.RequestException, ValueError) as exc:
                    wait = 2 ** (attempt + 1)
                    print(f"    通信に失敗（{exc}）。{wait}秒待って再試行します。", file=sys.stderr)
                    time.sleep(wait)
            else:
                raise SystemExit("通信に繰り返し失敗しました。")
            result = data["GET_STATS_DATA"]["RESULT"]
            status = int(result.get("STATUS", 0))
            if status not in (0, 1):
                raise SystemExit(f"e-Stat API エラー (STATUS={status}): {result.get('ERROR_MSG')}")
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        stat = data["GET_STATS_DATA"]["STATISTICAL_DATA"]
        if not names:
            for obj in as_list(stat.get("CLASS_INF", {}).get("CLASS_OBJ")):
                names[obj.get("@id")] = {
                    c.get("@code"): {"name": c.get("@name", ""),
                                     "level": c.get("@level", ""),
                                     "parent": c.get("@parentCode", "")}
                    for c in as_list(obj.get("CLASS"))}
        values.extend(as_list(stat.get("DATA_INF", {}).get("VALUE")))
        nxt = stat.get("RESULT_INF", {}).get("NEXT_KEY")
        print(f"    取得 {len(values):,} 件 (page {page})")
        if not nxt:
            break
        start = int(nxt)
        page += 1

    rows = []
    for v in values:
        raw = v.get("$", "")
        try:
            val = float(raw)
        except (TypeError, ValueError):
            continue
        if val <= 0:
            continue
        origin = str(v.get("@area", ""))
        dest = str(v.get("@cat01", ""))
        rows.append({"year": year, "dest_code": dest,
                     "origin_code": origin, "value": int(val)})
    return rows, names


def main() -> None:
    ap = argparse.ArgumentParser(description="転入元・転出先の取得")
    ap.add_argument("--app-id", default=os.environ.get("ESTAT_APPID"))
    ap.add_argument("--year", type=int, action="append",
                    help="取得する年（複数指定可）。未指定なら設定済みの全年")
    ap.add_argument("--sleep", type=float, default=2.0)
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()
    if not args.app_id:
        raise SystemExit("ESTAT_APPID が設定されていません。")

    years = sorted(args.year or TABLES)
    all_rows = []
    areas: dict[str, dict] = {}
    for y in years:
        table = TABLES.get(y)
        if not table:
            print(f"  {y}年の統計表IDが設定にありません。飛ばします。", file=sys.stderr)
            continue
        print(f"### {y}年（{table}）")
        rows, names = fetch_year(args.app_id, y, table, args.sleep, not args.no_cache)
        print(f"  → {len(rows):,}行")
        all_rows.extend(rows)
        for dim in ("area", "cat01"):
            for code, info in names.get(dim, {}).items():
                areas.setdefault(code, info)

    out = OUT / "migration_flow.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(all_rows)
    area_out = OUT / "migration_flow_areas.csv"
    with area_out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=AREA_FIELDS)
        w.writeheader()
        for code, info in sorted(areas.items()):
            w.writerow({"code": code, "name": info["name"],
                        "level": info["level"], "parent": info["parent"]})
    print(f"\n{out.relative_to(ROOT)}  {len(all_rows):,}行 / "
          f"{out.stat().st_size/1024/1024:.1f}MB")
    print(f"{area_out.relative_to(ROOT)}  {len(areas):,}件"
          f"（レベル1=総数／2=都道府県／3=市町村（政令市は市単位）／4=政令市の区）")


if __name__ == "__main__":
    main()
