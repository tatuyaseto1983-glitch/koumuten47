#!/usr/bin/env python3
"""e-Stat API から全国（全国計・47都道府県・市区町村）の人口関連データを取得する。

前提: 環境変数 ESTAT_APPID に e-Stat のアプリケーションIDを設定しておくこと。

  python3 scripts/estat_fetch.py search --keyword "社会・人口統計体系"
  python3 scripts/estat_fetch.py meta --table-id 0000010101
  python3 scripts/estat_fetch.py fetch --indicator population
  python3 scripts/estat_fetch.py fetch-all
  python3 scripts/estat_fetch.py build
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Iterator

import requests

API_BASE = "https://api.e-stat.go.jp/rest/3.0/app/json"
ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "indicators.json"
RESOLVED_PATH = ROOT / "config" / "resolved_tables.json"
RAW_DIR = ROOT / "data" / "raw"
OUT_DIR = ROOT / "data" / "processed"

# 1リクエストで受け取れる上限（e-Stat 仕様）
MAX_LIMIT = 100_000
class EstatError(RuntimeError):
    """1つの指標だけ失敗したときに使う。認証失敗は SystemExit で全体を止める。"""


# 47都道府県コード（全国計 00000 を含む）
PREF_CODES = ["00000"] + [f"{i:02d}000" for i in range(1, 48)]


# --------------------------------------------------------------------------
# APIクライアント
# --------------------------------------------------------------------------
class EstatClient:
    """取得間隔を空けて e-Stat API を呼ぶ。生JSONはファイルに残して再利用する。"""

    def __init__(self, app_id: str, sleep: float = 2.0, use_cache: bool = True):
        self.app_id = app_id
        self.sleep = sleep
        self.use_cache = use_cache
        self.session = requests.Session()
        self._last_call = 0.0

    def _wait(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.sleep:
            time.sleep(self.sleep - elapsed)
        self._last_call = time.monotonic()

    def get(self, endpoint: str, params: dict[str, Any], cache_key: str | None = None) -> dict:
        cache_path = RAW_DIR / f"{cache_key}.json" if cache_key else None
        if cache_path and self.use_cache and cache_path.exists():
            return json.loads(cache_path.read_text(encoding="utf-8"))

        payload = {"appId": self.app_id, **{k: v for k, v in params.items() if v is not None}}
        last_error: Exception | None = None
        for attempt in range(5):
            self._wait()
            try:
                res = self.session.get(f"{API_BASE}/{endpoint}", params=payload, timeout=120)
                if res.status_code >= 500:
                    raise requests.HTTPError(f"HTTP {res.status_code}")
                res.raise_for_status()
                data = res.json()
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                wait = 2 ** (attempt + 1)
                print(f"  通信に失敗しました（{exc}）。{wait}秒待って再試行します。", file=sys.stderr)
                time.sleep(wait)
                continue

            root = next(iter(data.values()))
            result = root.get("RESULT", {})
            status = int(result.get("STATUS", 0))
            # STATUS=1 は「正常終了だが該当データなし」。呼び出し側で空として扱う
            if status not in (0, 1):
                msg = result.get("ERROR_MSG", "不明なエラー")
                if status == 100:  # 認証失敗はappIdの問題なので続行しない
                    raise SystemExit(f"認証に失敗しました: {msg}\n"
                                     "  ESTAT_APPID の値を確認してください。")
                raise EstatError(f"e-Stat API エラー (STATUS={status}): {msg}")

            if cache_path:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            return data

        raise SystemExit(f"通信に繰り返し失敗しました: {last_error}")


def as_list(value: Any) -> list:
    """e-Stat は要素が1件だと配列ではなく辞書を返すため、常に配列に揃える。"""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


# --------------------------------------------------------------------------
# 地域コードの扱い
# --------------------------------------------------------------------------
def area_level(code: str) -> str:
    """地域コードから 全国 / 都道府県 / 市区町村 を判定する。"""
    code = str(code)
    if code in ("00000", "00"):
        return "national"
    if len(code) == 2:
        return "pref"
    if len(code) == 5 and code.endswith("000"):
        return "pref"
    if len(code) == 5:
        return "city"
    return "other"


def pref_code_of(code: str) -> str:
    code = str(code)
    return code[:2] if len(code) >= 2 and code != "00000" else ""


def year_of(time_code: str) -> str:
    m = re.match(r"(\d{4})", str(time_code))
    return m.group(1) if m else ""


# --------------------------------------------------------------------------
# 統計表IDと項目コードの解決
# --------------------------------------------------------------------------
def search_tables(client: EstatClient, keyword: str, stats_code: str | None = None,
                  limit: int = 30) -> list[dict]:
    data = client.get(
        "getStatsList",
        {"searchWord": keyword, "statsCode": stats_code, "limit": limit,
         "searchKind": 1, "explanationGetFlg": "N"},
        cache_key=None,
    )
    tables = as_list(data["GET_STATS_LIST"].get("DATALIST_INF", {}).get("TABLE_INF"))
    if not tables:
        return []
    rows = []
    for t in tables:
        title = t.get("TITLE")
        rows.append({
            "table_id": t.get("@id"),
            "stat_name": (t.get("STAT_NAME") or {}).get("$", ""),
            # 「都道府県データ 基礎データ」などの区分。地域粒度の判別に使う
            "statistics_name": t.get("STATISTICS_NAME", ""),
            "title": title.get("$", "") if isinstance(title, dict) else (title or ""),
            "collect_area": t.get("COLLECT_AREA", ""),
            "cycle": t.get("CYCLE", ""),
            "survey_date": t.get("SURVEY_DATE", ""),
            "updated": t.get("UPDATED_DATE", ""),
            "total": t.get("OVERALL_TOTAL_NUMBER", ""),
        })
    # 同一IDが複数返ることがあるため重複を除く
    seen, uniq = set(), []
    for r in rows:
        if r["table_id"] in seen:
            continue
        seen.add(r["table_id"])
        uniq.append(r)
    return uniq


def get_meta(client: EstatClient, table_id: str) -> list[dict]:
    data = client.get("getMetaInfo", {"statsDataId": table_id},
                      cache_key=f"meta_{table_id}")
    return as_list(data["GET_META_INFO"]["METADATA_INF"]["CLASS_INF"]["CLASS_OBJ"])


def match_item_codes(meta: list[dict], patterns: list[str]) -> dict[str, list[str]]:
    """分類事項の名称を正規表現で照合し、{分類ID: [コード, ...]} を返す。"""
    hits: dict[str, list[str]] = {}
    regexes = [re.compile(p) for p in patterns]
    for obj in meta:
        obj_id = obj.get("@id", "")
        if obj_id in ("area", "time"):
            continue
        for cls in as_list(obj.get("CLASS")):
            name = cls.get("@name", "")
            if any(r.search(name) for r in regexes):
                hits.setdefault(obj_id, []).append(cls.get("@code"))
    return hits


def resolve_table(client: EstatClient, key: str, spec: dict, resolved: dict) -> str:
    """設定に table_id があればそれを使い、無ければ検索して候補を確定する。"""
    if spec.get("table_id"):
        return spec["table_id"]
    if key in resolved and resolved[key].get("table_id"):
        return resolved[key]["table_id"]

    search = spec.get("table_search", {})
    rows = search_tables(client, search.get("keyword", spec["label"]),
                         search.get("stats_code"), limit=30)
    if not rows:
        raise EstatError(f"[{key}] 統計表が見つかりませんでした。keyword を見直してください。")

    prefer = [re.compile(p) for p in search.get("title_prefer", [])]
    best = rows[0]
    if prefer:
        for row in rows:
            text = f"{row['stat_name']} {row['statistics_name']} {row['title']} {row['collect_area']}"
            if all(r.search(text) for r in prefer):
                best = row
                break
    print(f"[{key}] 統計表を自動選定: {best['table_id']} / "
          f"{best['statistics_name']} / {best['title']}")
    resolved[key] = {"table_id": best["table_id"], "stat_name": best["stat_name"],
                     "statistics_name": best["statistics_name"],
                     "title": best["title"], "collect_area": best["collect_area"],
                     "candidates": rows[:10]}
    return best["table_id"]


# --------------------------------------------------------------------------
# データ取得
# --------------------------------------------------------------------------
def fetch_values(client: EstatClient, table_id: str, cache_prefix: str,
                 cd_area: list[str] | None = None,
                 cat_filter: dict[str, list[str]] | None = None) -> tuple[list[dict], dict]:
    """統計表の数値を全ページ取得して (VALUE配列, CLASS_OBJ) を返す。"""
    params: dict[str, Any] = {"statsDataId": table_id, "limit": MAX_LIMIT,
                              "metaGetFlg": "Y", "cntGetFlg": "N"}
    if cd_area:
        params["cdArea"] = ",".join(cd_area)
    for obj_id, codes in (cat_filter or {}).items():
        if obj_id == "tab":
            params["cdTab"] = ",".join(codes)
        elif obj_id.startswith("cat"):
            # cat01 -> cdCat01
            params["cd" + obj_id[0].upper() + obj_id[1:]] = ",".join(codes)

    sig = hashlib.md5(json.dumps(params, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:8]
    values: list[dict] = []
    class_obj: list[dict] = []
    page = 1
    while True:
        data = client.get("getStatsData", params, cache_key=f"{cache_prefix}_{sig}_p{page}")
        stat = data["GET_STATS_DATA"]["STATISTICAL_DATA"]
        if not class_obj:
            class_obj = as_list(stat.get("CLASS_INF", {}).get("CLASS_OBJ"))
        values.extend(as_list(stat.get("DATA_INF", {}).get("VALUE")))
        next_key = stat.get("RESULT_INF", {}).get("NEXT_KEY")
        print(f"  取得 {len(values):,} 件 (page {page})")
        if not next_key:
            break
        params["startPosition"] = int(next_key)
        page += 1
    return values, class_obj


def build_code_names(class_obj: list[dict]) -> dict[str, dict[str, str]]:
    names: dict[str, dict[str, str]] = {}
    for obj in class_obj:
        obj_id = obj.get("@id", "")
        names[obj_id] = {c.get("@code"): c.get("@name", "") for c in as_list(obj.get("CLASS"))}
    return names


def tidy_rows(key: str, label: str, values: list[dict], class_obj: list[dict],
              levels: list[str], from_year: int | None) -> Iterator[dict]:
    names = build_code_names(class_obj)
    cat_ids = [o.get("@id") for o in class_obj if o.get("@id") not in ("area", "time")]
    pref_names = {}
    for code, name in names.get("area", {}).items():
        if area_level(code) == "pref":
            pref_names[pref_code_of(code)] = name

    for v in values:
        area = str(v.get("@area", ""))
        lvl = area_level(area)
        if lvl not in levels:
            continue
        year = year_of(v.get("@time", ""))
        if from_year and year and int(year) < from_year:
            continue
        raw = v.get("$", "")
        try:
            num = float(raw)
        except (TypeError, ValueError):
            num = None  # "-" や "…" など秘匿・非該当
        cat_code = "|".join(str(v.get(f"@{c}", "")) for c in cat_ids)
        cat_name = "|".join(names.get(c, {}).get(str(v.get(f"@{c}", "")), "") for c in cat_ids)
        yield {
            "indicator": key,
            "indicator_label": label,
            "area_code": area,
            "area_name": names.get("area", {}).get(area, ""),
            "area_level": lvl,
            "pref_code": pref_code_of(area),
            "pref_name": pref_names.get(pref_code_of(area), ""),
            "time_code": v.get("@time", ""),
            "year": year,
            "cat_code": cat_code,
            "cat_name": cat_name,
            "unit": v.get("@unit", ""),
            "value": "" if num is None else num,
            "value_raw": raw,
        }


FIELDS = ["indicator", "indicator_label", "area_code", "area_name", "area_level",
          "pref_code", "pref_name", "time_code", "year", "cat_code", "cat_name",
          "unit", "value", "value_raw"]


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


# --------------------------------------------------------------------------
# コマンド
# --------------------------------------------------------------------------
def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def load_resolved() -> dict:
    if RESOLVED_PATH.exists():
        return json.loads(RESOLVED_PATH.read_text(encoding="utf-8"))
    return {}


def save_resolved(resolved: dict) -> None:
    RESOLVED_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESOLVED_PATH.write_text(json.dumps(resolved, ensure_ascii=False, indent=2),
                             encoding="utf-8")


def cmd_search(client: EstatClient, args) -> None:
    rows = search_tables(client, args.keyword, args.stats_code, args.limit)
    if not rows:
        print("該当する統計表がありませんでした。キーワードを変えてお試しください。")
        return
    for r in rows:
        print(f"{r['table_id']}  {str(r['total']):>10}件  {r['statistics_name']} / "
              f"{r['title']}  [地域:{r['collect_area']} / {r['cycle']}]")
    print(f"\n{len(rows)} 件表示しました。")


def cmd_meta(client: EstatClient, args) -> None:
    meta = get_meta(client, args.table_id)
    for obj in meta:
        classes = as_list(obj.get("CLASS"))
        print(f"\n== {obj.get('@id')} : {obj.get('@name')}（{len(classes)}件）")
        for c in classes[: args.limit]:
            print(f"   {c.get('@code'):<12} {c.get('@name')}  [{c.get('@unit','')}]")
        if len(classes) > args.limit:
            print(f"   ... ほか {len(classes) - args.limit} 件")


def fetch_source(client: EstatClient, key: str, label: str, source: dict,
                 resolved: dict, args) -> Path | None:
    """指標の1系列（都道府県版／市区町村版など）を取得してCSVに書き出す。"""
    name = source.get("name", "main")
    slug = f"{key}_{name}"
    print(f"\n### {slug}: {label}")
    table_id = resolve_table(client, slug, source, resolved)
    declared = source.get("area_levels", ["national", "pref"])
    if args.area_level:
        # 表に無い地域区分を指定しても空振りするだけなので、重なる部分だけ使う
        levels = [lv for lv in args.area_level.split(",") if lv in declared]
        if not levels:
            print(f"  この表は {declared} のみのため飛ばします。")
            return None
    else:
        levels = declared

    cat_filter = None
    if source.get("item_patterns"):
        meta = get_meta(client, table_id)
        cat_filter = match_item_codes(meta, source["item_patterns"])
        if not cat_filter:
            print(f"  [注意] 項目パターン {source['item_patterns']} に一致する分類がありません。"
                  f"\n         `meta --table-id {table_id}` で名称を確認してください。")
            return None
        for obj_id, codes in cat_filter.items():
            print(f"  対象項目 {obj_id}: {len(codes)}件 {codes[:6]}")

    # 市区町村まで取る場合のみ地域指定なしで全件取得する
    cd_area = None if "city" in levels else PREF_CODES
    values, class_obj = fetch_values(client, table_id, f"{slug}_{table_id}",
                                     cd_area=cd_area, cat_filter=cat_filter)
    rows = list(tidy_rows(key, label, values, class_obj, levels, args.from_year))
    if not rows:
        print("  該当データがありませんでした（地域区分または年次の条件を確認してください）。")
        return None
    out = OUT_DIR / f"{slug}.csv"
    write_csv(out, rows)
    n_area = len({r["area_code"] for r in rows})
    years = sorted({r["year"] for r in rows if r["year"]})
    span = f" / {years[0]}〜{years[-1]}" if years else ""
    print(f"  → {out.relative_to(ROOT)}  {len(rows):,}行 / 地域{n_area}件{span}")
    return out


def fetch_indicator(client: EstatClient, key: str, spec: dict, resolved: dict,
                    args) -> None:
    sources = spec.get("sources")
    if sources is None:
        sources = [spec]
    if not sources:
        print(f"\n### {key}: {spec['label']} — 取得元の設定がないため飛ばします。"
              f"\n    {spec.get('note', '')}")
        return
    for source in sources:
        if args.area_level is None and source.get("enabled") is False:
            continue
        try:
            fetch_source(client, key, spec["label"], source, resolved, args)
        except EstatError as exc:
            print(f"  [失敗] {exc}", file=sys.stderr)


def cmd_fetch(client: EstatClient, args) -> None:
    config = load_config()
    resolved = load_resolved()
    targets = args.indicator.split(",") if args.indicator else [
        k for k, v in config["indicators"].items() if v.get("enabled", True)
    ]
    try:
        for key in targets:
            spec = config["indicators"].get(key)
            if spec is None:
                print(f"[{key}] 設定にありません。config/indicators.json を確認してください。",
                      file=sys.stderr)
                continue
            fetch_indicator(client, key, spec, resolved, args)
    finally:
        save_resolved(resolved)
    print("\n完了しました。data/processed/ を確認してください。")


def cmd_build(client: EstatClient, args) -> None:
    """指標別CSVを1本の分析用テーブルにまとめる。"""
    files = sorted(OUT_DIR.glob("*.csv"))
    files = [f for f in files if f.name != "all_indicators.csv"]
    if not files:
        raise SystemExit("data/processed/ に指標CSVがありません。先に fetch を実行してください。")
    merged: list[dict] = []
    for f in files:
        with f.open(encoding="utf-8-sig") as fh:
            merged.extend(csv.DictReader(fh))
    write_csv(OUT_DIR / "all_indicators.csv", merged)
    print(f"data/processed/all_indicators.csv  {len(merged):,}行 "
          f"（{len(files)}指標を統合）")


def main() -> None:
    parser = argparse.ArgumentParser(description="e-Stat 全国データ取得ツール")
    parser.add_argument("--app-id", default=os.environ.get("ESTAT_APPID"),
                        help="e-Stat アプリケーションID（既定は環境変数 ESTAT_APPID）")
    parser.add_argument("--sleep", type=float, default=2.0,
                        help="リクエスト間隔の秒数（既定2.0秒／規約の大量アクセス禁止に配慮）")
    parser.add_argument("--no-cache", action="store_true", help="保存済みの生JSONを使わない")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("search", help="統計表を検索する")
    p.add_argument("--keyword", required=True)
    p.add_argument("--stats-code")
    p.add_argument("--limit", type=int, default=30)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("meta", help="統計表の分類事項（項目コード）を表示する")
    p.add_argument("--table-id", required=True)
    p.add_argument("--limit", type=int, default=40)
    p.set_defaults(func=cmd_meta)

    for name, help_text in (("fetch", "指標を取得する"), ("fetch-all", "設定の全指標を取得する")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--indicator", help="カンマ区切りの指標キー（未指定なら有効な全指標）")
        p.add_argument("--area-level", help="national,pref,city のカンマ区切りで上書き")
        p.add_argument("--from-year", type=int, default=1975)
        p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("build", help="指標CSVを1本にまとめる")
    p.set_defaults(func=cmd_build)

    args = parser.parse_args()
    if args.command != "build" and not args.app_id:
        raise SystemExit(
            "アプリケーションID（appId）が設定されていません。\n"
            "  export ESTAT_APPID=\"発行されたID\"  を実行してから再度お試しください。"
        )
    client = EstatClient(args.app_id or "", sleep=args.sleep, use_cache=not args.no_cache)
    args.func(client, args)


if __name__ == "__main__":
    main()
