#!/usr/bin/env python3
"""アプリに埋め込むJSONを作る。地域（全国・都道府県・市区町村）ごとに
時系列と要約値をまとめ、画面側で切り替えられる形にする。

  python3 scripts/build_report_data.py
"""
from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "processed"

CENSUS = [1980, 1985, 1990, 1995, 2000, 2005, 2010, 2015, 2020]
PROJ = [2020, 2025, 2030, 2035, 2040, 2045, 2050]
AGE_PROJ = [2030, 2040, 2050]

# 社会・人口統計体系の項目コード
POP, HH, HH_MEMBER, HH_ALONE = "A1101", "A710101", "A710201", "A810105"
AGE_Y, AGE_W, AGE_O, AGE_RATE = "A1301", "A1302", "A1303", "A1306"
IN_MIG, OUT_MIG, BIRTH, DEATH = "A5103", "A5104", "A4101", "A4200"
ST_TOTAL, ST_OWNER, ST_RENT, ST_SALE = "H1800", "H1801", "H1802", "H1803"
STOCK_TOTAL, STOCK_VACANT, STOCK_OWNED = "H1100", "H110202", "H1310"
SP_TOTAL, SP_HOUSING = "L3221", "L322102"
SPEND_ITEMS = {
    "food": "L322101", "housing": "L322102", "utility": "L322103",
    "furniture": "L322104", "clothing": "L322105", "health": "L322106",
    "transport": "L322107", "education": "L322108", "leisure": "L322109",
    "other": "L322110",
}
SAVINGS, DEBT_HOUSING = "L730101", "L740102"
WAGE_M, WAGE_F, GRAD_UNI, GRAD_HIGH = "F620217", "F620218", "F6411", "F6407"
MIN_WAGE, JOB_RATIO = "F6501", "F310301"
IND = {"primary": "F2201", "secondary": "F2211", "tertiary": "F2221"}
MONTHLY_CAT = {"total": "11", "owner": "12", "rent": "13", "sale": "15",
               "mansion": "16", "detached": "17"}


def load(name: str) -> list[dict]:
    path = OUT / f"{name}.csv"
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def code_of(cat_code: str) -> str:
    return cat_code.split("|")[-1]


def rnd(v, n=1):
    return None if v is None else round(v, n)


def chg(new, old):
    if new is None or old in (None, 0):
        return None
    return (new / old - 1) * 100


def per(a, b, scale=1000.0):
    if a is None or b in (None, 0):
        return None
    return a / b * scale


def ym_of(time_code: str):
    m = re.match(r"^(\d{4})00(\d{2})", str(time_code))
    return (int(m.group(1)), int(m.group(2))) if m else None


class Store:
    """{地域, 年, 項目} → 値 を引ける入れ物。"""

    def __init__(self):
        self.v: dict[tuple[str, int, str], float] = {}
        self.names: dict[str, dict] = {}

    def add(self, rows: list[dict]):
        for r in rows:
            self.names.setdefault(r["area_code"],
                                  {"name": r["area_name"], "pref": r["pref_name"]})
            if r["value"] == "" or not r["year"]:
                continue
            self.v[(r["area_code"], int(r["year"]), code_of(r["cat_code"]))] = float(r["value"])

    def get(self, area, year, code):
        return self.v.get((area, year, code))

    def series(self, area, code, years):
        return [self.get(area, y, code) for y in years]

    def latest(self, area, code, years):
        for y in reversed(years):
            val = self.get(area, y, code)
            if val is not None:
                return y, val
        return None, None


def clean(seq):
    """JSONを軽くするため、整数になる値は整数にする。"""
    out = []
    for v in seq:
        if v is None:
            out.append(None)
        elif abs(v - round(v)) < 1e-9:
            out.append(int(round(v)))
        else:
            out.append(round(v, 2))
    return out


def main() -> None:
    # ---- 都道府県（全国を含む）
    pref_store = Store()
    for name in ["housing_starts_pref", "housing_stock_pref", "consumption_pref",
                 "labour_pref", "population_pref", "households_pref",
                 "migration_pref", "age_structure_pref"]:
        pref_store.add(load(name))

    pop_years = list(range(1975, 2025))
    starts_years = list(range(1975, 2025))
    spend_years = list(range(2000, 2026))

    # ---- 市区町村
    city_store = Store()
    for name in ["housing_starts_city", "housing_stock_city", "population_city",
                 "households_city", "migration_city", "age_structure_city"]:
        city_store.add(load(name))
    city_starts_years = list(range(2000, 2025))

    # ---- 将来推計
    fut: dict[tuple[str, int, str], float] = {}
    for name in ["future_population_pref", "future_population_city"]:
        for r in load(name):
            if r["value"]:
                fut[(r["area_code"], int(r["year"]), r["cat_name"])] = float(r["value"])

    pref_codes = sorted({k[0] for k in fut if len(k[0]) == 5 and k[0].endswith("000")})

    def fut_national(year, item):
        vals = [fut.get((c, year, item)) for c in pref_codes]
        return sum(v for v in vals if v is not None) if all(v is not None for v in vals) else None

    # ---- 月次の着工
    monthly_raw = defaultdict(lambda: defaultdict(dict))
    inv = {v: k for k, v in MONTHLY_CAT.items()}
    months_set = set()
    for r in load("housing_starts_monthly_pref"):
        if r["value"] == "":
            continue
        ym = ym_of(r["time_code"])
        key = inv.get(code_of(r["cat_code"]))
        if not ym or not key:
            continue
        months_set.add(ym)
        monthly_raw[r["area_code"]][key][ym] = float(r["value"])
    months = sorted(months_set)
    month_labels = ["%d-%02d" % ym for ym in months]

    areas = []
    series = {}

    # ---------- 全国・都道府県 ----------
    for code in sorted(pref_store.names):
        nm = pref_store.names[code]["name"]
        level = "national" if code == "00000" else "pref"
        g = lambda y, c: pref_store.get(code, y, c)

        sy, starts = pref_store.latest(code, ST_TOTAL, starts_years)
        stock_y, stock_total = pref_store.latest(code, STOCK_TOTAL, list(range(1978, 2025)))
        spend_y, spend = pref_store.latest(code, SP_TOTAL, spend_years)
        wage_y, wage_m = pref_store.latest(code, WAGE_M, list(range(2015, 2025)))
        job_y, job = pref_store.latest(code, JOB_RATIO, list(range(2015, 2025)))
        mig_y, in_mig = pref_store.latest(code, IN_MIG, pop_years)
        out_mig = g(mig_y, OUT_MIG) if mig_y else None
        social = in_mig - out_mig if (in_mig is not None and out_mig is not None) else None
        birth = g(mig_y, BIRTH) if mig_y else None
        death = g(mig_y, DEATH) if mig_y else None

        hh2020 = g(2020, HH)
        pop2020 = g(2020, POP)
        pop2050 = fut.get((code, 2050, "総人口")) if level == "pref" else fut_national(2050, "総人口")
        old2050 = fut.get((code, 2050, "65歳以上人口")) if level == "pref" else fut_national(2050, "65歳以上人口")

        summary = {
            "code": code, "name": nm, "level": level,
            "prefCode": "" if level == "national" else code[:2],
            "prefName": "" if level == "national" else nm,
            "startsYear": sy, "starts": starts,
            "startsChg": rnd(chg(starts, g(sy - 1, ST_TOTAL) if sy else None)),
            "owner": g(sy, ST_OWNER) if sy else None,
            "ownerChg": rnd(chg(g(sy, ST_OWNER) if sy else None,
                                g(sy - 1, ST_OWNER) if sy else None)),
            "rent": g(sy, ST_RENT) if sy else None,
            "sale": g(sy, ST_SALE) if sy else None,
            "startsPer1k": rnd(per(starts, hh2020), 2),
            "stockYear": stock_y, "stockTotal": stock_total,
            "vacantRate": rnd(per(g(stock_y, STOCK_VACANT) if stock_y else None, stock_total, 100)),
            "ownedRate": rnd(per(g(stock_y, STOCK_OWNED) if stock_y else None, stock_total, 100)),
            "spendYear": spend_y, "spend": spend,
            "spendHousing": g(spend_y, SP_HOUSING) if spend_y else None,
            "spendFurniture": g(spend_y, SPEND_ITEMS["furniture"]) if spend_y else None,
            "spendUtility": g(spend_y, SPEND_ITEMS["utility"]) if spend_y else None,
            "savings": pref_store.latest(code, SAVINGS, list(range(1979, 2025)))[1],
            "debtHousing": pref_store.latest(code, DEBT_HOUSING, list(range(1979, 2025)))[1],
            "wageYear": wage_y, "wageM": wage_m,
            "wageF": g(wage_y, WAGE_F) if wage_y else None,
            "gradUni": g(wage_y, GRAD_UNI) if wage_y else None,
            "gradHigh": g(wage_y, GRAD_HIGH) if wage_y else None,
            "minWage": pref_store.latest(code, MIN_WAGE, list(range(2010, 2025)))[1],
            "jobRatio": job, "jobYear": job_y,
            "primary": g(2020, IND["primary"]), "secondary": g(2020, IND["secondary"]),
            "tertiary": g(2020, IND["tertiary"]),
            "pop2020": pop2020, "pop2000": g(2000, POP),
            "hh2020": hh2020, "hh2000": g(2000, HH),
            "size2020": rnd(per(g(2020, HH_MEMBER), hh2020, 1), 2),
            "single2020": rnd(per(g(2020, HH_ALONE), hh2020, 100)),
            "aging2020": rnd(g(2020, AGE_RATE)),
            "social": social, "socialYear": mig_y,
            "natural": birth - death if (birth is not None and death is not None) else None,
            "pop2050": pop2050,
            "aging2050": rnd(per(old2050, pop2050, 100)),
            "socialPer1k": rnd(per(social, hh2020), 2),
        }
        summary["chg2050"] = rnd(chg(pop2050, pop2020))
        summary["hhChg"] = rnd(chg(hh2020, g(2000, HH)))
        summary["popChg"] = rnd(chg(pop2020, g(2000, POP)))
        summary["score"] = (rnd(summary["socialPer1k"] - summary["startsPer1k"], 2)
                            if summary["socialPer1k"] is not None
                            and summary["startsPer1k"] is not None else None)
        areas.append(summary)

        # 時系列
        mo = monthly_raw.get(code, {})
        age_rows = []
        for y in CENSUS:
            tri = [g(y, AGE_Y), g(y, AGE_W), g(y, AGE_O)]
            tot = sum(v for v in tri if v is not None)
            if all(v is not None for v in tri) and tot > 0:
                age_rows.append([y, rnd(tri[0] / tot * 100), rnd(tri[1] / tot * 100),
                                 rnd(tri[2] / tot * 100), 0])
        for y in AGE_PROJ:
            if level == "national":
                tri = [fut_national(y, i) for i in ("0～14歳人口", "15～64歳人口", "65歳以上人口")]
            else:
                tri = [fut.get((code, y, i)) for i in ("0～14歳人口", "15～64歳人口", "65歳以上人口")]
            tot = sum(v for v in tri if v is not None)
            if all(v is not None for v in tri) and tot > 0:
                age_rows.append([y, rnd(tri[0] / tot * 100), rnd(tri[1] / tot * 100),
                                 rnd(tri[2] / tot * 100), 1])

        if level == "national":
            fut_series = [fut_national(y, "総人口") for y in PROJ]
        else:
            fut_series = [fut.get((code, y, "総人口")) for y in PROJ]

        series[code] = {
            "m": clean([mo.get("total", {}).get(ym) for ym in months]),
            "mo": clean([mo.get("owner", {}).get(ym) for ym in months]),
            "st": clean(pref_store.series(code, ST_TOTAL, starts_years)),
            "ow": clean(pref_store.series(code, ST_OWNER, starts_years)),
            "re": clean(pref_store.series(code, ST_RENT, starts_years)),
            "sa": clean(pref_store.series(code, ST_SALE, starts_years)),
            "pop": clean(pref_store.series(code, POP, pop_years)),
            "hh": clean([g(y, HH) for y in CENSUS]),
            "old": clean([g(y, AGE_RATE) for y in pop_years]),
            "sp": clean(pref_store.series(code, SP_TOTAL, spend_years)),
            "sph": clean(pref_store.series(code, SP_HOUSING, spend_years)),
            "fut": clean(fut_series),
            "age": age_rows,
            "mix": clean([g(spend_y, c) for c in SPEND_ITEMS.values()]) if spend_y else [],
        }

    # ---------- 市区町村 ----------
    # 市区町村名は「滋賀県 大津市」の形で入っているため、県名を切り離す
    pref_by_code = {a["code"][:2]: a["name"] for a in areas if a["level"] == "pref"}

    for code in sorted(city_store.names):
        info = city_store.names[code]
        pref_name = pref_by_code.get(code[:2], info["pref"])
        city_name = info["name"]
        if pref_name and city_name.startswith(pref_name):
            city_name = city_name[len(pref_name):].strip()
        city_name = city_name.replace("\u3000", " ").strip() or info["name"]
        g = lambda y, c: city_store.get(code, y, c)
        sy, starts = city_store.latest(code, ST_TOTAL, city_starts_years)
        stock_y, stock_total = city_store.latest(code, STOCK_TOTAL, list(range(1983, 2025)))
        mig_y, in_mig = city_store.latest(code, IN_MIG, pop_years)
        out_mig = g(mig_y, OUT_MIG) if mig_y else None
        social = in_mig - out_mig if (in_mig is not None and out_mig is not None) else None
        hh2020 = g(2020, HH)
        pop2020 = g(2020, POP)
        pop2050 = fut.get((code, 2050, "総人口"))
        old2050 = fut.get((code, 2050, "65歳以上人口"))

        summary = {
            "code": code, "name": city_name, "level": "city",
            "prefCode": code[:2], "prefName": pref_name,
            "startsYear": sy, "starts": starts,
            "startsChg": rnd(chg(starts, g(sy - 1, ST_TOTAL) if sy else None)),
            "owner": g(sy, ST_OWNER) if sy else None,
            "rent": g(sy, ST_RENT) if sy else None,
            "sale": g(sy, ST_SALE) if sy else None,
            "startsPer1k": rnd(per(starts, hh2020), 2),
            "stockYear": stock_y, "stockTotal": stock_total,
            "vacantRate": rnd(per(g(stock_y, STOCK_VACANT) if stock_y else None, stock_total, 100)),
            "ownedRate": rnd(per(g(stock_y, STOCK_OWNED) if stock_y else None, stock_total, 100)),
            "pop2020": pop2020, "pop2000": g(2000, POP),
            "hh2020": hh2020, "hh2000": g(2000, HH),
            "size2020": rnd(per(g(2020, HH_MEMBER), hh2020, 1), 2),
            "single2020": rnd(per(g(2020, HH_ALONE), hh2020, 100)),
            "aging2020": rnd(g(2020, AGE_RATE) or per(g(2020, AGE_O), pop2020, 100)),
            "social": social, "socialYear": mig_y,
            "socialPer1k": rnd(per(social, hh2020), 2),
            "pop2050": pop2050,
            "aging2050": rnd(per(old2050, pop2050, 100)),
        }
        summary["chg2050"] = rnd(chg(pop2050, pop2020))
        summary["hhChg"] = rnd(chg(hh2020, g(2000, HH)))
        summary["ownerShare"] = rnd(per(summary["owner"], starts, 100))
        summary["score"] = (rnd(summary["socialPer1k"] - summary["startsPer1k"], 2)
                            if summary["socialPer1k"] is not None
                            and summary["startsPer1k"] is not None else None)
        areas.append(summary)

        age_rows = []
        for y in CENSUS:
            tri = [g(y, AGE_Y), g(y, AGE_W), g(y, AGE_O)]
            tot = sum(v for v in tri if v is not None)
            if all(v is not None for v in tri) and tot > 0:
                age_rows.append([y, rnd(tri[0] / tot * 100), rnd(tri[1] / tot * 100),
                                 rnd(tri[2] / tot * 100), 0])
        for y in AGE_PROJ:
            tri = [fut.get((code, y, i)) for i in ("0～14歳人口", "15～64歳人口", "65歳以上人口")]
            tot = sum(v for v in tri if v is not None)
            if all(v is not None for v in tri) and tot > 0:
                age_rows.append([y, rnd(tri[0] / tot * 100), rnd(tri[1] / tot * 100),
                                 rnd(tri[2] / tot * 100), 1])

        series[code] = {
            "st": clean(city_store.series(code, ST_TOTAL, city_starts_years)),
            "ow": clean(city_store.series(code, ST_OWNER, city_starts_years)),
            "re": clean(city_store.series(code, ST_RENT, city_starts_years)),
            "sa": clean(city_store.series(code, ST_SALE, city_starts_years)),
            "pop": clean([g(y, POP) for y in CENSUS]),
            "hh": clean([g(y, HH) for y in CENSUS]),
            "fut": clean([fut.get((code, y, "総人口")) for y in PROJ]),
            "age": age_rows,
        }

    # 中身が空の系列は落として、埋め込むJSONを軽くする
    for code, sr in series.items():
        for key in list(sr):
            val = sr[key]
            if isinstance(val, list) and (not val or all(v is None for v in val)):
                del sr[key]

    # ---- 4区分の境目（着工が公表されている市区町村の中央値）
    def median(xs):
        xs = sorted(x for x in xs if x is not None)
        if not xs:
            return None
        n = len(xs)
        return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2

    scored = [a for a in areas if a["level"] == "city" and a.get("score") is not None]
    dmid = median([a["socialPer1k"] for a in scored])
    smid = median([a["startsPer1k"] for a in scored])

    def quadrant(a):
        d, s = a.get("socialPer1k"), a.get("startsPer1k")
        if d is None or s is None:
            return None
        if d >= dmid and s < smid:
            return "狙い目"
        if d >= dmid and s >= smid:
            return "過熱気味"
        if d < dmid and s >= smid:
            return "供給過剰の芽"
        return "冷え込み"

    counts = defaultdict(int)
    for a in areas:
        if a["level"] in ("city", "pref"):
            a["quadrant"] = quadrant(a)
            if a["level"] == "city" and a["quadrant"]:
                counts[a["quadrant"]] += 1

    # 値のない項目は書き出さない（画面側では未定義を「—」として扱う）
    areas = [{k: v for k, v in a.items() if v is not None} for a in areas]

    bundle = {
        "meta": {
            "months": month_labels,
            "startsYears": starts_years,
            "cityStartsYears": city_starts_years,
            "popYears": pop_years,
            "censusYears": CENSUS,
            "spendYears": spend_years,
            "projYears": PROJ,
            "spendKeys": list(SPEND_ITEMS.keys()),
            "quadrantMid": {"demand": rnd(dmid, 2), "supply": rnd(smid, 2)},
            "quadrantCounts": dict(counts),
            "cityWithStarts": len([a for a in areas if a["level"] == "city" and a.get("starts") is not None]),
            "cityTotal": len([a for a in areas if a["level"] == "city"]),
            "built": "2026-09-11",
        },
        "areas": areas,
        "series": series,
    }
    path = OUT / "report_data.json"
    path.write_text(json.dumps(bundle, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8")
    print(f"{path.relative_to(ROOT)}  {path.stat().st_size/1024:.0f}KB")
    print(f"  地域 {len(areas)}件（全国1 / 都道府県47 / 市区町村"
          f"{len([a for a in areas if a['level'] == 'city'])}）")
    print(f"  月次 {len(months)}か月 {month_labels[0]}〜{month_labels[-1]}")
    print(f"  着工の公表がある市区町村 {bundle['meta']['cityWithStarts']}件")
    print(f"  4区分 {dict(counts)}  境目 需要{rnd(dmid,2)} 供給{rnd(smid,2)}")


if __name__ == "__main__":
    main()
