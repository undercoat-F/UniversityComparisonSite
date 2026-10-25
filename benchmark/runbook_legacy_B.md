# Legacy（分散化前）で試験B を実行する 手順書

目的: 分散化前のクローラー（コミット `20ca461`、タグ `legacy-pre-distributed`）を、分散版と同じ条件（試験B、Mock、Neon の bench-run）で動かし、比較の基準値を取る。

Legacy の特徴（分散版との違い）:

- 1 台の EC2 で `python -m ETL.ETL` を実行する。起点の読み込み → sitemap → クロール（同時に4ドメイン）→ クロール終了後に DB へ一括投入、の順に進み、終わると終了する
- SQS・Valkey は使わない（片付けは Neon のリセットだけ）
- コードはブランチ `benchmark/legacy`（`20ca461` + 計測に必要な最小限の変更。下記）

| `benchmark/legacy` の変更 | 理由 |
|---|---|
| `ETL/schedular.py`: seed が読めないときに `URLs.txt` へ切り替えない | 設定ミスで本物の大学サイトをクロールしないため |
| `db/db_saver.py`: DB 保存の不具合2件の修正を移植（R6.4 ほか） | クロール後の DB 投入が失敗しないよう、分散版と条件をそろえるため |
| `docker-compose.bench.yml` を追加 | Mock の CA をコンテナに読み込ませるため |

---

## 0. 手元の PC

```bash
# Legacy のブランチとタグを GitHub に送る（EC2 で clone するため）
git push origin benchmark/legacy legacy-pre-distributed

# sqs-distributed 側の変更（Terraform の Legacy 用 EC2、手順書など）もコミットして push
git status
```

## 1. Legacy 用 EC2 を作る（手元の PC）

`terraform/terraform.tfvars` に次の1行を追加する。

```hcl
benchmark_legacy_enabled = true
```

```bash
terraform -chdir=terraform plan     # 「1 to add, 0 to change, 0 to destroy」（aws_instance.benchmark_legacy）であること
terraform -chdir=terraform apply
terraform -chdir=terraform output benchmark_legacy_public_ip
```

## 2. Legacy EC2 の準備

### 2-1. 起動時の初期設定（Docker のインストール）の完了を待つ（Legacy EC2）

```bash
ssh -i ~/UniversityComparisonKey.pem ubuntu@<Legacy の IP>
cloud-init status --wait        # status: done になるまで待つ
sudo docker --version
```

### 2-2. コードを取得する（Legacy EC2）

```bash
git clone -b benchmark/legacy https://github.com/undercoat-F/UniversityComparisonSite.git ~/legacy-app
cd ~/legacy-app && git log --oneline -2      # 1行目が「Legacy計測用の最小限の変更」、2行目が 20ca461
```

### 2-3. .env を用意する（手元の PC から）

Control Plane の計測用 `.env`（bench-run 向けに編集済み）をそのまま使う。`scp -3` は手元の PC を経由して中身を変換せずに転送する（手元にファイルは残らない）。

**Git Bash で実行すること。** PowerShell やコマンドプロンプトでパイプ（`ssh ... | ssh ...`）を使うと、日本語のコメントが文字化けし、改行が崩れて設定が消える（2026-10-12 に発生）。

```bash
scp -3 -i ~/UniversityComparisonKey.pem \
  ubuntu@35.73.202.127:/home/ubuntu/bench-app/.env ubuntu@<Legacy の IP>:/home/ubuntu/legacy-app/.env

# 2台のファイルが同一であること（ハッシュ値が一致すること）を確認する
for h in 35.73.202.127 <Legacy の IP>; do
  ssh -i ~/UniversityComparisonKey.pem ubuntu@$h 'sha256sum ~/*-app/.env | cut -c1-12'
done
```

`<Legacy の IP>` は実際の IP に置き換える（`<` を残したままだと、Git Bash がファイルからの入力と解釈して接続先が空になる）。

Legacy で効く設定（Control Plane の `.env` で設定済みのはず）:

| 変数 | 値 |
|---|---|
| `DB_HOST` | bench-run（`ep-aged-rice-aovzws60-pooler...`） |
| `SEED_URLS_TABLE` | `observer.bench_seed_urls` |
| `ETL_RECENT_SKIP_MONTHS` | `0` |
| `QUEUE_LOG_ENABLED` | `0`（タグ収集・URL ごとのログを書かない） |
| `ETL_TAG_CLASS_LOG_URL_LIMIT_PER_DOMAIN` | `0` |
| `CRAWL_RESOURCE_SAMPLES_TABLE` | 空 |

### 2-4. 証明書を配る（手元の PC）

```bash
python -m benchmark.distribute_certs --key ~/UniversityComparisonKey.pem            # legacy も配布先に入る
python -m benchmark.distribute_certs --key ~/UniversityComparisonKey.pem --check    # legacy で HTTP 404 になること
```

## 3. 計測前の初期化

```bash
# 手元の PC: Neon の bench-run を初期状態に戻す（Legacy は SQS・Valkey を使わないので片付けはこれだけ）
python -m benchmark.neon_branch reset
```

```bash
# Mock の EC2: 新しい run-label で起動し直す
sudo systemctl stop bench-mock; sudo systemctl reset-failed bench-mock 2>/dev/null; cd ~/bench-mock
sudo systemd-run --unit bench-mock --working-directory "$HOME/bench-mock" "$HOME/bench-mock/.venv/bin/python" \
  -m benchmark.mock.server --scenario benchmark/mock/scenarios/B_crawl_delay.yaml --run-label B-legacy-x1-run1 \
  --results-dir /var/lib/benchmark/results --certfile /etc/benchmark/server.crt --keyfile /etc/benchmark/server.key
systemctl is-active bench-mock
```

## 4. Legacy を実行する（Legacy EC2）

```bash
cd ~/legacy-app
sudo docker compose -p bench -f docker-compose.yml -f docker-compose.bench.yml --profile etl run --rm --build etl
```

終わると自動で終了する。確認ポイント:

- `[SCHEDULER] ETL start targets=3`（0 なら `DB_HOST` か seed テーブルの問題）
- `[DISPATCHER] sitemap seeding done candidates=...`（0 より大きい）
- `[ETL] Stage 1/3 complete: records=... degrees=...`（クロール終了）
- `[ETL] Stage 3/3 complete (direct streaming)` と `[ETL] loaded totals: ...`（DB 投入まで成功）

## 5. 結果を回収して比較する（手元の PC）

```bash
# Mock の EC2 で sudo systemctl stop bench-mock してから
scp -i ~/UniversityComparisonKey.pem -r ubuntu@13.196.23.137:/var/lib/benchmark/results/B-legacy-x1-run1 benchmark/results/
python -m benchmark.analysis.report run benchmark/results/B-legacy-x1-run1
python -m benchmark.analysis.report compare \
  benchmark/results/B-legacy-x1-run1 benchmark/results/B-smoke-x1-run1 benchmark/results/B-fifofix-x1-run1 \
  --baseline B-legacy-x1
```

期待: coverage 100%、Crawl-delay 違反 0。所要時間は、Legacy は4ドメインまで並行に処理するため、Crawl-delay から求めた最短（90 秒）に近い値になる見込み。

所要時間は Mock のアクセスログから測るため、クロール後の DB 投入（Stage 2/3）の時間は含まれない。DB 投入まで含めた時間は手順 4 のログの時刻で確認する。
