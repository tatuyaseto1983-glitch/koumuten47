#!/usr/bin/env python3
"""取得済みのCSVから、レポート用の集計表を作る。

  python3 scripts/build_summary.py

出力:
  data/processed/summary_pref.csv   全国＋47都道府県の主要指標
  data/processed/summary_city.csv   市区町村の主要指標
  data/processed/summary.json       レポート生成用のまとめ
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "processed"

# 使う項目コード
POP = "A1101"            # 総人口
POP_JP = "A1102"         # 日本人人口
HH = "A710101"           # 一般世帯数
HH_MEMBER = "A710201"    # 一般世帯人員数
HH_ALONE = "A810105"     # 単独世帯数
AGE_YOUNG = "A1301"      # 15歳未満
AGE_WORK = "A1302"       # 15〜64歳
AGE_OLD = "A1303"        # 65歳以上
AGE_OLD_RATE = "A1306"   # 65歳以上人口割合
IN_MIG = "A5103"         # 転入者数
OUT_MIG = "A5104"        # 転出者数
BIRTH = "A4101"          # 出生数
DEATH = "A4200"          # 死亡数
CONSUMPTION = "L3221"    # 消費支出（二人以上の世帯・家計調査）
HOUSING_COST = "L322102"  # 住居費
CENSUS_YEARS = ["1980", "1990", "2000", "2010", "2020"]
PROJ_YEARS = ["2020", "2030", "2040", "2050"]


def item_code(cat_code: str) -> str:
    return cat_code.split("|")[-1]


def load(name: str) -> list[dict]:
    path = OUT / f"{name}.csv"
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def index(rows: list[dict]) -> tuple[dict, dict]:
    """{(地域, 年, 項目): 値} と {地域: 名称} を作る。"""
    values: dict[tuple[str, str, str], float] = {}
    names: dict[str, dict[str, str]] = {}
    for r in rows:
        if r["value"] == "":
            continue
        values[(r["area_code"], r["year"], item_code(r["cat_code"]))] = float(r["value"])
        names[r["area_code"]] = {"name": r["area_name"], "pref": r["pref_name"]}
    return values, names


def index_future(rows: list[dict]) -> dict:
    """社人研データは項目名で持つ。"""
    values: dict[tuple[str, str, str], float] = {}
    for r in rows:
        if r["value"] == "":
            continue
        values[(r["area_code"], r["year"], r["cat_name"])] = float(r["value"])
    return values


def ratio(a: float | None, b: float | None) -> float | None:
    if a is None or b in (None, 0):
        return None
    return a / b


def change(new: float | None, old: float | None) -> float | None:
    """変化率（％）。"""
    if new is None or old in (None, 0):
        return None
    return (new / old - 1) * 100


def latest_year(values: dict, area: str, code: str, years: list[str]) -> str | None:
    for y in reversed(years):
        if (area, y, code) in values:
            return y
    return None


def build_level(level: str) -> tuple[list[dict], dict]:
    pop_rows = load(f"population_{level}")
    hh_rows = load(f"households_{level}")
    age_rows = load(f"age_structure_{level}")
    mig_rows = load(f"migration_{level}")
    con_rows = load("consumption_pref") if level == "pref" else []
    fut_rows = load(f"future_population_{level}")

    all_rows = pop_rows + hh_rows + age_rows + mig_rows + con_rows
    values, names = index(all_rows)
    future = index_future(fut_rows)
    years = sorted({r["year"] for r in all_rows})

    areas = sorted(names)
    out: list[dict] = []
    for area in areas:
        g = lambda year, code: values.get((area, year, code))
        pop_2020 = g("2020", POP)
        my = latest_year(values, area, POP, years)
        pop_latest = g(my, POP) if my else None

        # 将来推計（社人研）。市区町村は一部が広域で合算されているため無い場合がある
        f_pop = {y: future.get((area, y, "総人口")) for y in PROJ_YEARS}
        f_old = {y: future.get((area, y, "65歳以上人口")) for y in PROJ_YEARS}

        hh_2020 = g("2020", HH)
        member_2020 = g("2020", HH_MEMBER)

        # 社会増減・自然増減は直近の揃った年で見る
        mig_year = latest_year(values, area, IN_MIG, years)
        social = None
        if mig_year and g(mig_year, IN_MIG) is not None and g(mig_year, OUT_MIG) is not None:
            social = g(mig_year, IN_MIG) - g(mig_year, OUT_MIG)
        nat_year = latest_year(values, area, BIRTH, years)
        natural = None
        if nat_year and g(nat_year, BIRTH) is not None and g(nat_year, DEATH) is not None:
            natural = g(nat_year, BIRTH) - g(nat_year, DEATH)

        con_year = latest_year(values, area, CONSUMPTION, years)
        row = {
            "area_code": area,
            "area_name": names[area]["name"],
            "pref_name": names[area]["pref"],
            "area_level": "national" if area == "00000" else level,
            "pop_1980": g("1980", POP),
            "pop_2000": g("2000", POP),
            "pop_2010": g("2010", POP),
            "pop_2020": pop_2020,
            "pop_latest": pop_latest,
            "pop_latest_year": my,
            "pop_chg_1980_2020": change(pop_2020, g("1980", POP)),
            "pop_chg_2000_2020": change(pop_2020, g("2000", POP)),
            "pop_2030": f_pop["2030"],
            "pop_2040": f_pop["2040"],
            "pop_2050": f_pop["2050"],
            "pop_chg_2020_2050": change(f_pop["2050"], f_pop["2020"]),
            "households_2000": g("2000", HH),
            "households_2020": hh_2020,
            "households_chg_2000_2020": change(hh_2020, g("2000", HH)),
            "avg_household_size_2020": ratio(member_2020, hh_2020),
            "single_household_share_2020": (
                ratio(g("2020", HH_ALONE), hh_2020) * 100
                if ratio(g("2020", HH_ALONE), hh_2020) is not None else None),
            "young_2020": g("2020", AGE_YOUNG),
            "working_2020": g("2020", AGE_WORK),
            "old_2020": g("2020", AGE_OLD),
            "aging_rate_2020": g("2020", AGE_OLD_RATE) or (
                ratio(g("2020", AGE_OLD), pop_2020) * 100
                if ratio(g("2020", AGE_OLD), pop_2020) is not None else None),
            "old_2050_raw": f_old["2050"],
            "aging_rate_2050": (ratio(f_old["2050"], f_pop["2050"]) * 100
                                if ratio(f_old["2050"], f_pop["2050"]) is not None else None),
            "social_change": social,
            "social_change_year": mig_year,
            "natural_change": natural,
            "natural_change_year": nat_year,
            "consumption_month": g(con_year, CONSUMPTION) if con_year else None,
            "housing_cost_month": g(con_year, HOUSING_COST) if con_year else None,
            "consumption_year": con_year,
        }
        out.append(row)

    # 長期推移（国勢調査年）も別に持っておく
    series = {}
    for area in areas:
        series[area] = {y: values.get((area, y, POP)) for y in CENSUS_YEARS}
    return out, series


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if v is None else v) for k, v in r.items()})


def fill_national_future(rows: list[dict]) -> None:
    """社人研の表に全国計の行がないため、47都道府県の合計で補う。"""
    national = next((r for r in rows if r["area_code"] == "00000"), None)
    prefs = [r for r in rows if r["area_code"] not in ("00000",)]
    if national is None or not prefs:
        return
    for key in ("pop_2030", "pop_2040", "pop_2050"):
        vals = [r[key] for r in prefs if r[key] is not None]
        if len(vals) == 47:
            national[key] = sum(vals)
    base = [r["pop_2020"] for r in prefs if r["pop_2020"] is not None]
    if national["pop_2050"] is not None and len(base) == 47:
        national["pop_chg_2020_2050"] = change(national["pop_2050"], sum(base))
    # 2050年の高齢化率も合計から算出する
    old_2050 = [r["old_2050_raw"] for r in prefs if r.get("old_2050_raw") is not None]
    if len(old_2050) == 47 and national["pop_2050"]:
        national["aging_rate_2050"] = sum(old_2050) / national["pop_2050"] * 100


def main() -> None:
    pref, pref_series = build_level("pref")
    fill_national_future(pref)
    city, city_series = build_level("city")
    write_csv(OUT / "summary_pref.csv", pref)
    write_csv(OUT / "summary_city.csv", city)
    print(f"summary_pref.csv: {len(pref)}行")
    print(f"summary_city.csv: {len(city)}行")

    national = next((r for r in pref if r["area_code"] == "00000"), None)
    prefs = [r for r in pref if r["area_code"] != "00000"]
    bundle = {
        "national": national,
        "national_series": pref_series.get("00000", {}),
        "prefectures": prefs,
        "pref_series": {r["area_code"]: pref_series[r["area_code"]] for r in prefs},
        "cities": city,
    }
    (OUT / "summary.json").write_text(
        json.dumps(bundle, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"summary.json: 都道府県{len(prefs)}件 / 市区町村{len(city)}件")


if __name__ == "__main__":
    main()
