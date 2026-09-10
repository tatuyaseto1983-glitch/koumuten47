#!/usr/bin/env python3
"""ダッシュボード（HTML）に埋め込むJSONを作る。数値はすべて取得済みCSVから拾う。

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

CENSUS = ["1980", "1985", "1990", "1995", "2000", "2005", "2010", "2015", "2020"]
PROJ = ["2025", "2030", "2035", "2040", "2045", "2050"]

# 社会・人口統計体系の項目コード
POP, HH, HH_MEMBER, HH_ALONE = "A1101", "A710101", "A710201", "A810105"
AGE = {"young": "A1301", "work": "A1302", "old": "A1303", "oldRate": "A1306"}
IN_MIG, OUT_MIG, BIRTH, DEATH = "A5103", "A5104", "A4101", "A4200"
STARTS = {"total": "H1800", "owner": "H1801", "rent": "H1802", "sale": "H1803",
          "company": "H1804", "buildings": "H1700", "floor": "H2500"}
STOCK = {"total": "H1100", "vacant": "H110202", "owned": "H1310", "rented": "H1320",
         "detached": "H1401", "apartment": "H1403", "areaOwned": "H213010"}
SPEND = {"total": "L3221", "food": "L322101", "housing": "L322102", "utility": "L322103",
         "furniture": "L322104", "clothing": "L322105", "health": "L322106",
         "transport": "L322107", "education": "L322108", "leisure": "L322109",
         "other": "L322110"}
ASSETS = {"savings": "L730101", "debtHousing": "L740102",
          "savingsOld": "L430101", "debtOld": "L440101", "debtHousingOld": "L440102"}
LABOUR = {"workers": "F1102", "primary": "F2201", "secondary": "F2211", "tertiary": "F2221",
          "jobRatio": "F310301", "wageM": "F620217", "wageF": "F620218",
          "gradUniM": "F6411", "gradUniF": "F6412", "gradHighM": "F6407",
          "minWage": "F6501"}
# 月次の利用関係コード
MONTHLY = {"total": "11", "owner": "12", "rent": "13", "company": "14",
           "sale": "15", "mansion": "16", "detached": "17"}


def load(name: str) -> list[dict]:
    path = OUT / f"{name}.csv"
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def code_of(cat_code: str) -> str:
    return cat_code.split("|")[-1]


def index(rows: list[dict]) -> tuple[dict, dict]:
    values: dict[tuple[str, str, str], float] = {}
    names: dict[str, dict[str, str]] = {}
    for r in rows:
        names.setdefault(r["area_code"], {"name": r["area_name"], "pref": r["pref_name"]})
        if r["value"] == "":
            continue
        values[(r["area_code"], r["year"], code_of(r["cat_code"]))] = float(r["value"])
    return values, names


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


def latest_with(values: dict, area: str, code: str, years: list[str]) -> str | None:
    for y in reversed(years):
        if (area, y, code) in values:
            return y
    return None


def ym_of(time_code: str) -> tuple[int, int] | None:
    """月次の時間軸コード（例 2024001212）から年と月を取り出す。"""
    m = re.match(r"^(\d{4})00(\d{2})", str(time_code))
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


# ---------------------------------------------------------------------------
def build_monthly() -> tuple[list[dict], list[dict], dict]:
    rows = load("housing_starts_monthly_pref")
    series: dict[tuple[int, int], dict[str, float]] = defaultdict(dict)
    pref_year: dict[tuple[str, int], dict[str, float]] = defaultdict(lambda: defaultdict(float))
    inv = {v: k for k, v in MONTHLY.items()}
    for r in rows:
        if r["value"] == "":
            continue
        ym = ym_of(r["time_code"])
        if not ym:
            continue
        key = inv.get(code_of(r["cat_code"]))
        if not key:
            continue
        v = float(r["value"])
        if r["area_code"] == "00000":
            series[ym][key] = v
        pref_year[(r["area_code"], ym[0])][key] += v

    monthly = [{"y": ym[0], "m": ym[1], **vals} for ym, vals in sorted(series.items())]
    # 通年でそろっている年だけを年次系列にする
    counts: dict[int, int] = defaultdict(int)
    for ym in series:
        counts[ym[0]] += 1
    full_years = sorted(y for y, c in counts.items() if c == 12)
    annual = []
    for y in full_years:
        agg = defaultdict(float)
        for ym, vals in series.items():
            if ym[0] != y:
                continue
            for k, v in vals.items():
                agg[k] += v
        annual.append({"year": y, **{k: round(v) for k, v in agg.items()}})
    return monthly, annual, pref_year


def build_pref() -> tuple[list[dict], dict, dict]:
    starts = load("housing_starts_pref")
    stock = load("housing_stock_pref")
    spend = load("consumption_pref")
    labour = load("labour_pref")
    pop = load("population_pref")
    hh = load("households_pref")
    mig = load("migration_pref")
    age = load("age_structure_pref")

    all_rows = starts + stock + spend + labour + pop + hh + mig + age
    values, names = index(all_rows)
    years = sorted({r["year"] for r in all_rows})

    fut = {}
    for r in load("future_population_pref"):
        if r["value"]:
            fut[(r["area_code"], r["year"], r["cat_name"])] = float(r["value"])

    out = []
    for area in sorted(names):
        g = lambda y, c: values.get((area, y, c))
        sy = latest_with(values, area, STARTS["total"], years)
        prev = str(int(sy) - 1) if sy else None
        hh_now = g("2020", HH)
        stock_year = latest_with(values, area, STOCK["total"], years)
        spend_year = latest_with(values, area, SPEND["total"], years)
        wage_year = latest_with(values, area, LABOUR["wageM"], years)
        job_year = latest_with(values, area, LABOUR["jobRatio"], years)
        mig_year = latest_with(values, area, IN_MIG, years)
        social = None
        if mig_year and g(mig_year, IN_MIG) is not None and g(mig_year, OUT_MIG) is not None:
            social = g(mig_year, IN_MIG) - g(mig_year, OUT_MIG)
        nat_year = latest_with(values, area, BIRTH, years)
        natural = None
        if nat_year and g(nat_year, BIRTH) is not None and g(nat_year, DEATH) is not None:
            natural = g(nat_year, BIRTH) - g(nat_year, DEATH)

        row = {
            "code": area,
            "name": names[area]["name"],
            # --- 着工
            "startsYear": sy,
            "starts": g(sy, STARTS["total"]) if sy else None,
            "startsPrev": g(prev, STARTS["total"]) if prev else None,
            "owner": g(sy, STARTS["owner"]) if sy else None,
            "ownerPrev": g(prev, STARTS["owner"]) if prev else None,
            "rent": g(sy, STARTS["rent"]) if sy else None,
            "sale": g(sy, STARTS["sale"]) if sy else None,
            "buildings": g(sy, STARTS["buildings"]) if sy else None,
            # --- 住宅ストック
            "stockYear": stock_year,
            "stockTotal": g(stock_year, STOCK["total"]) if stock_year else None,
            "vacant": g(stock_year, STOCK["vacant"]) if stock_year else None,
            "owned": g(stock_year, STOCK["owned"]) if stock_year else None,
            "rented": g(stock_year, STOCK["rented"]) if stock_year else None,
            "areaOwned": g(stock_year, STOCK["areaOwned"]) if stock_year else None,
            # --- 家計
            "spendYear": spend_year,
            "spend": g(spend_year, SPEND["total"]) if spend_year else None,
            "spendHousing": g(spend_year, SPEND["housing"]) if spend_year else None,
            "spendFurniture": g(spend_year, SPEND["furniture"]) if spend_year else None,
            "spendUtility": g(spend_year, SPEND["utility"]) if spend_year else None,
            "savings": g(latest_with(values, area, ASSETS["savings"], years), ASSETS["savings"]),
            "debtHousing": g(latest_with(values, area, ASSETS["debtHousing"], years),
                             ASSETS["debtHousing"]),
            # --- 働く人
            "wageYear": wage_year,
            "wageM": g(wage_year, LABOUR["wageM"]) if wage_year else None,
            "wageF": g(wage_year, LABOUR["wageF"]) if wage_year else None,
            "gradUniM": g(wage_year, LABOUR["gradUniM"]) if wage_year else None,
            "gradHighM": g(wage_year, LABOUR["gradHighM"]) if wage_year else None,
            "minWage": g(latest_with(values, area, LABOUR["minWage"], years), LABOUR["minWage"]),
            "jobRatio": g(job_year, LABOUR["jobRatio"]) if job_year else None,
            "jobYear": job_year,
            "primary": g("2020", LABOUR["primary"]),
            "secondary": g("2020", LABOUR["secondary"]),
            "tertiary": g("2020", LABOUR["tertiary"]),
            # --- 人口・世帯
            "pop2020": g("2020", POP),
            "pop2000": g("2000", POP),
            "hh2020": hh_now,
            "hh2000": g("2000", HH),
            "size2020": rnd(per(g("2020", HH_MEMBER), hh_now, 1), 2),
            "single2020": rnd(per(g("2020", HH_ALONE), hh_now, 100)),
            "aging2020": rnd(g("2020", AGE["oldRate"])),
            "social": social,
            "socialYear": mig_year,
            "natural": natural,
            "pop2050": fut.get((area, "2050", "総人口")),
            "old2050": fut.get((area, "2050", "65歳以上人口")),
        }
        row["startsChg"] = rnd(chg(row["starts"], row["startsPrev"]))
        row["ownerChg"] = rnd(chg(row["owner"], row["ownerPrev"]))
        row["startsPer1k"] = rnd(per(row["starts"], hh_now), 2)
        row["socialPer1k"] = rnd(per(social, hh_now), 2)
        row["vacantRate"] = rnd(per(row["vacant"], row["stockTotal"], 100))
        row["ownedRate"] = rnd(per(row["owned"], row["stockTotal"], 100))
        row["chg2050"] = rnd(chg(row["pop2050"], row["pop2020"]))
        row["aging2050"] = rnd(per(row["old2050"], row["pop2050"], 100))
        row["hhChg"] = rnd(chg(hh_now, row["hh2000"]))
        row["popChg"] = rnd(chg(row["pop2020"], row["pop2000"]))
        out.append(row)

    # 全国の費目別支出と長期推移
    nat = "00000"
    spend_year = latest_with(values, nat, SPEND["total"], years)
    breakdown = [{"key": k, "value": values.get((nat, spend_year, c))}
                 for k, c in SPEND.items() if k != "total"]
    spend_series = []
    for y in sorted({r["year"] for r in spend}):
        t = values.get((nat, y, SPEND["total"]))
        h = values.get((nat, y, SPEND["housing"]))
        if t:
            spend_series.append({"year": int(y), "total": t, "housing": h})

    indexed = []
    base_pop = values.get((nat, "1980", POP))
    base_hh = values.get((nat, "1980", HH))
    for y in CENSUS:
        p, h = values.get((nat, y, POP)), values.get((nat, y, HH))
        if p and h:
            indexed.append({"year": int(y), "pop": round(p / base_pop * 100, 1),
                            "hh": round(h / base_hh * 100, 1), "popRaw": p, "hhRaw": h,
                            "size": round(values.get((nat, y, HH_MEMBER), 0) / h, 2)})

    age_mix = []
    for y in CENSUS:
        tri = [values.get((nat, y, AGE[k])) for k in ("young", "work", "old")]
        if all(tri):
            tot = sum(tri)
            age_mix.append({"year": int(y), "kind": "実績",
                            "young": round(tri[0] / tot * 100, 1),
                            "work": round(tri[1] / tot * 100, 1),
                            "old": round(tri[2] / tot * 100, 1)})
    pref_codes = sorted({r["area_code"] for r in load("future_population_pref")})

    def fut_sum(year, item):
        vals = [fut.get((c, year, item)) for c in pref_codes]
        return sum(v for v in vals if v is not None) if all(v is not None for v in vals) else None

    for y in ["2030", "2040", "2050"]:
        tri = [fut_sum(y, i) for i in ("0～14歳人口", "15～64歳人口", "65歳以上人口")]
        if all(tri):
            tot = sum(tri)
            age_mix.append({"year": int(y), "kind": "推計",
                            "young": round(tri[0] / tot * 100, 1),
                            "work": round(tri[1] / tot * 100, 1),
                            "old": round(tri[2] / tot * 100, 1)})

    starts_series = []
    for y in sorted({r["year"] for r in starts}):
        t = values.get((nat, y, STARTS["total"]))
        if t:
            starts_series.append({"year": int(y), "total": t,
                                  "owner": values.get((nat, y, STARTS["owner"])),
                                  "rent": values.get((nat, y, STARTS["rent"])),
                                  "sale": values.get((nat, y, STARTS["sale"]))})

    extras = {
        "spendBreakdown": breakdown,
        "spendBreakdownYear": spend_year,
        "spendSeries": spend_series,
        "indexed": indexed,
        "ageMix": age_mix,
        "startsSeries": starts_series,
        "futureNational": [{"year": int(y), "value": fut_sum(y, "総人口")}
                           for y in ["2020"] + PROJ if fut_sum(y, "総人口")],
    }
    return out, extras, fut


def build_city() -> list[dict]:
    starts = load("housing_starts_city")
    hh = load("households_city")
    pop = load("population_city")
    mig = load("migration_city")
    stock = load("housing_stock_city")
    values, names = index(starts + hh + pop + mig + stock)
    years = sorted({r["year"] for r in starts + hh + pop + mig + stock})

    fut = {}
    for r in load("future_population_city"):
        if r["value"]:
            fut[(r["area_code"], r["year"], r["cat_name"])] = float(r["value"])

    out = []
    for area in sorted(names):
        g = lambda y, c: values.get((area, y, c))
        sy = latest_with(values, area, STARTS["total"], years)
        if not sy:
            continue  # 着工の公表がない町村は対象外
        prev = str(int(sy) - 1)
        hh_now = g("2020", HH)
        mig_year = latest_with(values, area, IN_MIG, years)
        social = None
        if mig_year and g(mig_year, IN_MIG) is not None and g(mig_year, OUT_MIG) is not None:
            social = g(mig_year, IN_MIG) - g(mig_year, OUT_MIG)
        stock_year = latest_with(values, area, STOCK["total"], years)
        row = {
            "code": area,
            "name": names[area]["name"],
            "pref": names[area]["pref"],
            "startsYear": sy,
            "starts": g(sy, STARTS["total"]),
            "startsPrev": g(prev, STARTS["total"]),
            "owner": g(sy, STARTS["owner"]),
            "hh2020": hh_now,
            "pop2020": g("2020", POP),
            "social": social,
            "socialYear": mig_year,
            "vacant": g(stock_year, STOCK["vacant"]) if stock_year else None,
            "stockTotal": g(stock_year, STOCK["total"]) if stock_year else None,
            "pop2050": fut.get((area, "2050", "総人口")),
        }
        row["startsChg"] = rnd(chg(row["starts"], row["startsPrev"]))
        row["startsPer1k"] = rnd(per(row["starts"], hh_now), 2)
        row["socialPer1k"] = rnd(per(social, hh_now), 2)
        row["ownerShare"] = rnd(per(row["owner"], row["starts"], 100))
        row["vacantRate"] = rnd(per(row["vacant"], row["stockTotal"], 100))
        row["chg2050"] = rnd(chg(row["pop2050"], row["pop2020"]))
        # 狙い目スコア = 需要（転入超過）− 供給（着工）。どちらも世帯千あたりに直して比べる
        if row["socialPer1k"] is not None and row["startsPer1k"] is not None:
            row["score"] = rnd(row["socialPer1k"] - row["startsPer1k"], 2)
        else:
            row["score"] = None
        out.append(row)
    return out


def quadrant(demand, supply, dmid, smid):
    if demand is None or supply is None:
        return None
    if demand >= dmid and supply < smid:
        return "狙い目"
    if demand >= dmid and supply >= smid:
        return "過熱気味"
    if demand < dmid and supply >= smid:
        return "供給過剰の芽"
    return "冷え込み"


def median(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def main() -> None:
    monthly, annual_types, _ = build_monthly()
    prefs, extras, _ = build_pref()
    cities = build_city()

    national = next(p for p in prefs if p["code"] == "00000")
    prefs = [p for p in prefs if p["code"] != "00000"]

    # 社人研の表に全国計の行がないため、47都道府県の合計で補う
    pop2050 = [p["pop2050"] for p in prefs if p["pop2050"] is not None]
    old2050 = [p["old2050"] for p in prefs if p["old2050"] is not None]
    if len(pop2050) == 47:
        national["pop2050"] = sum(pop2050)
        national["chg2050"] = rnd(chg(national["pop2050"], national["pop2020"]))
        if len(old2050) == 47:
            national["aging2050"] = rnd(per(sum(old2050), national["pop2050"], 100))

    dmid = median([c["socialPer1k"] for c in cities])
    smid = median([c["startsPer1k"] for c in cities])
    for c in cities:
        c["quadrant"] = quadrant(c["socialPer1k"], c["startsPer1k"], dmid, smid)
    for p in prefs:
        p["quadrant"] = quadrant(p["socialPer1k"], p["startsPer1k"], dmid, smid)

    scored = [c for c in cities if c["score"] is not None and c["hh2020"] and c["hh2020"] >= 10000]
    scored.sort(key=lambda x: -x["score"])
    counts = defaultdict(int)
    for c in cities:
        if c["quadrant"]:
            counts[c["quadrant"]] += 1

    # 埋め込みを軽くするため、ページで使う列だけに絞る
    keep = ["code", "name", "pref", "starts", "startsChg", "startsPer1k", "socialPer1k",
            "score", "hh2020", "pop2020", "quadrant", "chg2050", "vacantRate", "ownerShare"]
    slim = [{k: c.get(k) for k in keep} for c in cities]
    slim_top = [{k: c.get(k) for k in keep} for c in scored[:15]]
    slim_bottom = [{k: c.get(k) for k in keep} for c in scored[-15:][::-1]]

    bundle = {
        "national": national,
        "cityStartsYear": cities[0]["startsYear"] if cities else None,
        "citySocialYear": cities[0]["socialYear"] if cities else None,
        "prefectures": prefs,
        "monthly": monthly,
        "annualTypes": annual_types,
        "cities": slim,
        "cityTop": slim_top,
        "cityBottom": slim_bottom,
        "quadrantCounts": dict(counts),
        "quadrantMid": {"demand": rnd(dmid, 2), "supply": rnd(smid, 2)},
        "cityScoredCount": len(scored),
        **extras,
    }
    path = OUT / "report_data.json"
    path.write_text(json.dumps(bundle, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8")
    print(f"{path.relative_to(ROOT)}  {path.stat().st_size/1024:.0f}KB")
    print(f"  月次 {len(monthly)}か月（{monthly[0]['y']}年{monthly[0]['m']}月〜"
          f"{monthly[-1]['y']}年{monthly[-1]['m']}月）")
    print(f"  年次（通年そろう年）{len(annual_types)}年 / 都道府県 {len(prefs)} / "
          f"市区町村 {len(cities)}（スコア対象 {len(scored)}）")
    print(f"  4区分の内訳: {dict(counts)}  中央値 需要{rnd(dmid,2)} 供給{rnd(smid,2)}")


if __name__ == "__main__":
    main()
