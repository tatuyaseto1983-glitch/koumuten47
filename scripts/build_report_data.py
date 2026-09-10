#!/usr/bin/env python3
"""レポート（HTML）に埋め込むJSONを作る。数値はすべて取得済みCSVから拾う。"""
from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "processed"

CENSUS = ["1980", "1985", "1990", "1995", "2000", "2005", "2010", "2015", "2020"]
PROJ = ["2025", "2030", "2035", "2040", "2045", "2050"]


def load(name):
    p = OUT / f"{name}.csv"
    with p.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def idx(rows, code_from_cat=True):
    d = {}
    for r in rows:
        if r["value"] == "":
            continue
        key = r["cat_code"].split("|")[-1] if code_from_cat else r["cat_name"]
        d[(r["area_code"], r["year"], key)] = float(r["value"])
    return d


def rnd(v, n=1):
    return None if v is None else round(v, n)


def main() -> None:
    pop = idx(load("population_pref"))
    hh = idx(load("households_pref"))
    age = idx(load("age_structure_pref"))
    mig = idx(load("migration_pref"))
    fut_rows = load("future_population_pref")
    fut = idx(fut_rows, code_from_cat=False)
    summary = json.loads((OUT / "summary.json").read_text(encoding="utf-8"))

    pref_codes = sorted({r["area_code"] for r in fut_rows})

    def fut_national(year, item):
        vals = [fut.get((c, year, item)) for c in pref_codes]
        return sum(v for v in vals if v is not None) if all(v is not None for v in vals) else None

    # --- 全国の人口: 実績（年次）と推計（5年ごと）
    actual_years = [str(y) for y in range(1980, 2025)]
    national_actual = [{"year": int(y), "value": pop[("00000", y, "A1101")]}
                       for y in actual_years if ("00000", y, "A1101") in pop]
    national_proj = [{"year": 2020, "value": fut_national("2020", "総人口")}]
    national_proj += [{"year": int(y), "value": fut_national(y, "総人口")} for y in PROJ]

    # --- 人口と世帯数の推移（1980年＝100）
    base_pop = pop[("00000", "1980", "A1101")]
    base_hh = hh[("00000", "1980", "A710101")]
    indexed = []
    for y in CENSUS:
        p = pop.get(("00000", y, "A1101"))
        h = hh.get(("00000", y, "A710101"))
        if p and h:
            indexed.append({"year": int(y), "pop": round(p / base_pop * 100, 1),
                            "hh": round(h / base_hh * 100, 1),
                            "pop_raw": p, "hh_raw": h,
                            "size": round(hh.get(("00000", y, "A710201"), 0) / h, 2)})

    # --- 年齢3区分の構成比（実績＋推計）
    age_mix = []
    for y in CENSUS:
        tri = [age.get(("00000", y, c)) for c in ("A1301", "A1302", "A1303")]
        if all(tri):
            total = sum(tri)
            age_mix.append({"year": int(y), "kind": "実績",
                            "young": round(tri[0] / total * 100, 1),
                            "work": round(tri[1] / total * 100, 1),
                            "old": round(tri[2] / total * 100, 1)})
    for y in ["2030", "2040", "2050"]:
        tri = [fut_national(y, i) for i in ("0～14歳人口", "15～64歳人口", "65歳以上人口")]
        if all(tri):
            total = sum(tri)
            age_mix.append({"year": int(y), "kind": "推計",
                            "young": round(tri[0] / total * 100, 1),
                            "work": round(tri[1] / total * 100, 1),
                            "old": round(tri[2] / total * 100, 1)})

    # --- 都道府県
    prefs = []
    for r in summary["prefectures"]:
        code = r["area_code"]
        prefs.append({
            "code": code,
            "name": r["area_name"],
            "pop2020": r["pop_2020"],
            "pop2050": r["pop_2050"],
            "chg2050": rnd(r["pop_chg_2020_2050"]),
            "chg2000_2020": rnd(r["pop_chg_2000_2020"]),
            "hh2020": r["households_2020"],
            "hhChg": rnd(r["households_chg_2000_2020"]),
            "size": rnd(r["avg_household_size_2020"], 2),
            "single": rnd(r["single_household_share_2020"]),
            "aging2020": rnd(r["aging_rate_2020"]),
            "aging2050": rnd(r["aging_rate_2050"]),
            "social": r["social_change"],
            "socialYear": r["social_change_year"],
            "natural": r["natural_change"],
            "naturalYear": r["natural_change_year"],
            "consumption": r["consumption_month"],
            "housingCost": r["housing_cost_month"],
            "consumptionYear": r["consumption_year"],
        })
    prefs.sort(key=lambda x: (x["chg2050"] is None, -(x["chg2050"] or 0)))

    # --- 市区町村ランキング（人口2万人以上に限定して実務で使える規模に絞る）
    cities = [c for c in summary["cities"]
              if c["pop_2020"] and c["pop_2020"] >= 20000 and c["pop_chg_2020_2050"] is not None]

    def city_row(c):
        return {
            "name": c["area_name"], "pref": c["pref_name"],
            "pop2020": c["pop_2020"], "pop2050": c["pop_2050"],
            "chg2050": rnd(c["pop_chg_2020_2050"]),
            "hhChg": rnd(c["households_chg_2000_2020"]),
            "aging2020": rnd(c["aging_rate_2020"]),
        }

    growing = [city_row(c) for c in sorted(cities, key=lambda x: -x["pop_chg_2020_2050"])[:12]]
    shrinking = [city_row(c) for c in sorted(cities, key=lambda x: x["pop_chg_2020_2050"])[:12]]

    n = summary["national"]
    national = {
        "pop2020": n["pop_2020"], "pop2050": n["pop_2050"],
        "chg2050": rnd(n["pop_chg_2020_2050"]),
        "chg2000_2020": rnd(n["pop_chg_2000_2020"]),
        "popLatest": n["pop_latest"], "popLatestYear": n["pop_latest_year"],
        "hh2020": n["households_2020"], "hhChg": rnd(n["households_chg_2000_2020"]),
        "size2020": rnd(n["avg_household_size_2020"], 2),
        "single2020": rnd(n["single_household_share_2020"]),
        "aging2020": rnd(n["aging_rate_2020"]), "aging2050": rnd(n["aging_rate_2050"]),
        "natural": n["natural_change"], "naturalYear": n["natural_change_year"],
        "consumption": n["consumption_month"], "housingCost": n["housing_cost_month"],
        "consumptionYear": n["consumption_year"],
        "citiesTotal": len(summary["cities"]),
        "citiesShrinking": sum(1 for c in summary["cities"]
                               if c["pop_chg_2020_2050"] is not None and c["pop_chg_2020_2050"] < 0),
        "citiesWithProj": sum(1 for c in summary["cities"] if c["pop_chg_2020_2050"] is not None),
        "citiesHalving": sum(1 for c in summary["cities"]
                             if c["pop_chg_2020_2050"] is not None and c["pop_chg_2020_2050"] <= -50),
        "citiesLarge": len(cities),
    }

    bundle = {
        "national": national,
        "nationalActual": national_actual,
        "nationalProjection": national_proj,
        "indexed": indexed,
        "ageMix": age_mix,
        "prefectures": prefs,
        "growing": growing,
        "shrinking": shrinking,
    }
    path = OUT / "report_data.json"
    path.write_text(json.dumps(bundle, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8")
    print(f"{path.relative_to(ROOT)}  {path.stat().st_size/1024:.0f}KB")
    print(f"  全国実績 {len(national_actual)}点 / 推計 {len(national_proj)}点 / "
          f"都道府県 {len(prefs)} / 伸びる市区町村 {len(growing)} / 縮む市区町村 {len(shrinking)}")
    print(f"  市区町村: 推計あり{national['citiesWithProj']}件 うち減少{national['citiesShrinking']}件 "
          f"半減以下{national['citiesHalving']}件")


if __name__ == "__main__":
    main()
