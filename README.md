# 全国人口・世帯データ取得ツール（e-Stat API）

政府統計の総合窓口（e-Stat）のAPIから、**全国計・47都道府県・約1,900市区町村**の
人口関連データをまとめて取得します。住宅・リフォーム・民泊事業の市場規模を
エリアごとに比べるための、元データづくりが目的です。

## 1. 準備：アプリケーションID（appId）

e-Stat APIは登録者ごとのIDが必須です。無料で、5分ほどで発行できます。

1. ユーザ登録 → https://www.e-stat.go.jp/mypage/user/preregister
2. ログイン → マイページの「API機能（アプリケーションID発行）」
3. 名称は任意、URLは公開サイトで使わないなら `http://test.localhost/` を入力して発行

発行されたIDを環境変数に入れます。

```bash
export ESTAT_APPID="発行されたID"        # macOS / Linux
setx ESTAT_APPID "発行されたID"          # Windows（PowerShell、設定後に再起動）
```

## 2. インストール

```bash
pip install -r requirements.txt
```

## 3. 使い方

```bash
# 設定済みの全指標を取得（都道府県版・市区町村版の両方）
python3 scripts/estat_fetch.py fetch-all

# 指標を絞って取得
python3 scripts/estat_fetch.py fetch --indicator population,households

# 都道府県までに限定して軽く回す
python3 scripts/estat_fetch.py fetch --indicator population --area-level national,pref

# 指標CSVを1本の分析用テーブルにまとめる
python3 scripts/estat_fetch.py build
```

統計表を探す・項目コードを確かめるとき:

```bash
python3 scripts/estat_fetch.py search --keyword "社会・人口統計体系 市区町村データ"
python3 scripts/estat_fetch.py meta --table-id 0000010102
```

## 4. 取得する指標と出典

| 指標 | 主な出典 | 粒度 |
| --- | --- | --- |
| 総人口 | 社会・人口統計体系（国勢調査ベース） | 全国・都道府県・市区町村 |
| 世帯数・平均世帯人員 | 社会・人口統計体系 | 全国・都道府県・市区町村 |
| 年齢3区分別人口・高齢化率 | 社会・人口統計体系 | 全国・都道府県・市区町村 |
| 転入者数・転出者数 | 住民基本台帳人口移動報告 | 全国・都道府県 |
| 将来推計人口（2050年まで） | 社人研「日本の地域別将来推計人口」 | 全国・都道府県 |
| 1世帯当たり消費支出 | 家計調査 | 全国・都道府県庁所在市 |
| 住宅ストック・空き家（任意） | 住宅・土地統計調査 | 全国・都道府県 |

指標の追加や絞り込みは `config/indicators.json` を編集します。
`enabled: false` の指標は `fetch-all` では取得しません。

### 粒度についての注意

- **消費支出**は市区町村別の公表がありません。都道府県庁所在市が最小単位です。
- **転入・転出**と**将来推計人口**は、市区町村別も公表がありますが表が分かれます。
  必要になったら `config/indicators.json` に市区町村版の系列を追加します。

## 5. 出力されるファイル

```
data/raw/        取得した生JSON（再実行時のキャッシュ、Git管理外）
data/processed/  指標ごとのCSVと、統合版 all_indicators.csv
```

CSVは1行1数値の縦持ち形式です。列は以下のとおりです。

`indicator, indicator_label, area_code, area_name, area_level, pref_code,
pref_name, time_code, year, cat_code, cat_name, unit, value, value_raw`

- `area_level` は `national` / `pref` / `city` のいずれかです。
- 秘匿・非該当（`-` や `…`）は `value` が空、`value_raw` に元の記号が残ります。

## 6. 統計表IDの自動解決について

統計表IDは改廃されることがあるため、`config/indicators.json` の `table_id` は
空にしてあります。実行時にキーワードで検索し、選んだ表を
`config/resolved_tables.json` に記録します。意図した表かどうかは、同ファイルの
`candidates`（上位10件）で確認できます。違う表が選ばれていたら、
`table_id` に正しいIDを直接書き込んでください。

## 7. 実行時の作法

e-Stat APIの利用規約第8条で「短時間における大量のアクセス」が禁止されています。
本ツールは既定でリクエスト間隔を2秒空け、1回のリクエストで最大10万件を
まとめて受け取ることで、呼び出し回数を抑えています。間隔は `--sleep` で変更できます。

取得した生JSONは `data/raw/` に残るため、2回目以降は通信せずに再集計できます。
最新値で取り直したいときは `--no-cache` を付けます。
