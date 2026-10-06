# 計測時の環境変数チェックリスト

計測では、本番の `.env` を元にした計測用の env ファイルを使う（docker compose は `COMPOSE_ENV_FILE` で切り替えられる）。
本番の Neon・seed・タグ収集テーブルに書き込まないよう、計測の前に以下を確認する。

## 1. 接続先（全ロール共通）

| 変数 | 計測時の値 | 理由 |
| `DB_HOST` | bench-run のホスト（`python -m benchmark.neon_branch status`） | 本番 DB に書き込まない。ユーザー名・パスワード・DB 名は本番と同じ |
| `PARENT_DB_OWNER_CONNECTION` | ホスト部分を bench-run に置き換えたもの（Worker は空、下記 3） | 同上 |
| `SEED_URLS_TABLE` | `observer.bench_seed_urls` | Mock の起点 URL を読む |
| `ETL_RECENT_SKIP_MONTHS` | `0` | 最近登録した大学のドメインを除外する処理を止める |
| `SSL_CERT_FILE` / `REQUESTS_CA_BUNDLE` | Mock の `ca-bundle.pem` のパス | Mock の HTTPS を信頼させる（R4.1） |

seed が読めないときに本物の大学サイト（`URLs.txt`）をクロールしないこと:

- 分散版（現在のコード）: 対応済み（`URLs.txt` への切り替え処理はコメントアウトした）
- Legacy（`20ca461`）: チェックアウトした `ETL/URLs.txt` を空にする（この設定がないため）

## 2. タグ収集・ログの書き込みを止める

タグ収集テーブル（`crawl_tag_keyword_hits`, `crawl_domain_tag_scores`, `crawl_tag_class_counts`, `crawl_domain_class_counts`）と、URL ごとのログ（`crawl_queue_state`, `crawl_attempts`, `crawl_edges`）は、キューログが有効なときだけ書き込まれる。

| ロール | 止め方 |
| Legacy | `QUEUE_LOG_ENABLED=0` |
| 分散版 Worker | `PARENT_DB_OWNER_CONNECTION` を設定しない（空にする） |
| 分散版 Control Plane（producer） | 止められない。実行記録（`crawl_runs`）と起点・sitemap 候補の `crawl_queue_state` を書く（必須）。書き込み先が bench-run であることだけ確認する |

念のため両方に `ETL_TAG_CLASS_LOG_URL_LIMIT_PER_DOMAIN=0` も設定する。

リソース記録: `CRAWL_RESOURCE_SAMPLES_TABLE` は空にする（設定すると現在のコードは DB に書き込む）。

## 3. 分散版だけの設定

| ロール | 変数 | 値 |
| Control Plane | `RECORD_DB_ENABLED` | `1`（スキーマ作成・ID シーケンス同期を実行開始時に1回行う） |
| Control Plane / Worker | `REDIS_URL` / `SQS_QUEUE_URL` / `AWS_REGION` | 本番と同じ（共用）。計測前に `python -m benchmark.reset_state` で片付ける |
| Worker | `RECORD_DB_ENABLED` | `1`（抽出結果を bench-run に書き込む） |
| Worker | `PARENT_DB_OWNER_CONNECTION` | **空**（上記 2） |
| Worker | `ETL_WORKER_MAX_DEPTH` | `5`（seed の depth と揃える） |
| Worker | `SQS_VISIBILITY_TIMEOUT_SEC` | `180`（`terraform/sqs.tf` と揃える） |

## 4. Legacy と分散版で揃える設定

| 変数 | 値 | 備考 |
| `ETL_HTTPX_TIMEOUT_SEC` / `ETL_REQUESTS_TIMEOUT_SEC` | 本番と同じ（30） | タイムアウト応答の待ち時間に影響する |
| `ETL_ROBOTS_READ_TIMEOUT_SEC` | 本番と同じ | |
| 同時に処理するドメイン数 | Legacy `max_active_sites=4` / Worker `max_concurrency=4` | どちらもコード内の固定値 |

## 5. Neon（無料プラン）

- bench-run のコンピュートは 2 CU に固定する（`python -m benchmark.neon_branch setup --cu 2`）
- 5 分間アクセスがないとコンピュートが停止する（無料プランでは無効にできない）。計測の直前に軽いクエリで起こしておく
- 計測ごとに `python -m benchmark.neon_branch reset` で bench-run を bench-base の状態に戻す
