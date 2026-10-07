# Benchmark Mock Server

性能試験用の Mock Web Server。設計は `benchmark/benchmark_concept_revisions.md`（R1〜R4）を参照。
コマンドはすべてリポジトリのルートで実行する。

## セットアップ

```bash
pip install -r benchmark/requirements.txt
python -m benchmark.mock.gen_cert --suffix bench.internal   # benchmark/mock/certs/ に証明書を生成
```

## シナリオの内容確認

```bash
# サマリー（種別ごとのドメイン数・Crawl-delay・応答内訳、理論上限スループット、警告）
python -m benchmark.mock.inspect_scenario benchmark/mock/scenarios/A_scaling.yaml

# domains.csv / pages.csv / seed_urls.txt / hosts.txt / scenario.resolved.json を出力
python -m benchmark.mock.inspect_scenario benchmark/mock/scenarios/A_scaling.yaml --out benchmark/results/A-inspect

# 1ドメインの全ページ一覧
python -m benchmark.mock.inspect_scenario benchmark/mock/scenarios/A_scaling.yaml --domain d003.bench.internal

# 1URLで実際に返す応答（ステータス・遅延・本文）
python -m benchmark.mock.inspect_scenario benchmark/mock/scenarios/A_scaling.yaml --url https://d003.bench.internal/robots.txt
```

ドメインの総数・種別の比率・Crawl-delay・応答内容はシナリオYAMLで変更する（各項目の説明はYAML内のコメント参照）。
HTML の見た目は `templates/page.html` を編集する。

## Mock Server の起動

```bash
python -m benchmark.mock.server \
  --scenario benchmark/mock/scenarios/A_scaling.yaml \
  --run-label A-distributed-x2-run1 \
  --certfile benchmark/mock/certs/server.crt --keyfile benchmark/mock/certs/server.key
```

- 443番ポートで待ち受ける（`--port` で変更可）
- `benchmark/results/<run-label>/` にアクセスログ `access.jsonl` と、起動時のシナリオ展開結果（`--out` と同じファイル）を出力する
- 同じ run-label のログが既にある場合は起動しない（上書き防止）

## EC2 での起動

`terraform/terraform.tfvars` に `benchmark_enabled = true` を設定し、Terraform の plan を確認してから apply する。作成される Mock Server は既存 VPC 内の専用 EC2 で、Route 53 プライベートホストゾーンの `*.bench.internal` がそのプライベート IP を指す。443/tcp は Crawler 用 Security Group から、SSH は `ssh_allowed_cidr` からのみ許可される。

EC2 には Git と Python venv が導入され、結果用の暗号化 EBS が `/var/lib/benchmark` にマウントされる。

### 証明書の配布（手元の PC で実行）

証明書は手元で1回だけ作り、同じものを全インスタンスに配る（インスタンスごとに作ると CA が食い違う）。
Mock には `server.crt` / `server.key`、Control Plane・Worker・Legacy には `ca-bundle.pem` が `/etc/benchmark/` に置かれる。
Worker は停止中だと配布できないため、起動してから実行する（パブリック IP は実行時に AWS から取得する）。

```bash
python -m benchmark.mock.gen_cert --suffix bench.internal          # 未作成の場合のみ
python -m benchmark.distribute_certs --key ~/UniversityComparisonKey.pem
python -m benchmark.distribute_certs --key ~/UniversityComparisonKey.pem --check   # Mock 起動後に接続確認
```

クローラーのコンテナには `docker-compose.bench.yml` で CA をマウントする（`.env` は変更しない）。

```bash
docker compose -f docker-compose.yml -f docker-compose.bench.yml --profile distributed up -d worker
```

### Mock Server の起動（Mock の EC2 で実行）

コードは手元の `benchmark/` を `~/bench-mock/benchmark` にコピーして使う（未コミットの変更も反映でき、GitHub の認証も不要）。
SSH を切っても動き続け、止めやすいよう systemd のサービス `bench-mock` として起動する。

```bash
# 手元の PC（リポジトリのルート）
tar --force-local --exclude=benchmark/mock/certs --exclude=benchmark/results --exclude=__pycache__ -czf bench-mock.tgz benchmark
scp -i ~/UniversityComparisonKey.pem bench-mock.tgz ubuntu@<Mock のパブリック IP>:/tmp/

# Mock の EC2
mkdir -p ~/bench-mock && rm -rf ~/bench-mock/benchmark && tar -xzf /tmp/bench-mock.tgz -C ~/bench-mock
cd ~/bench-mock && python3 -m venv .venv && .venv/bin/pip install "pyyaml>=6.0" "uvicorn>=0.30"
sudo systemd-run --unit bench-mock --working-directory "$HOME/bench-mock" "$HOME/bench-mock/.venv/bin/python" \
  -m benchmark.mock.server --scenario benchmark/mock/scenarios/B_crawl_delay.yaml --run-label <run-label> \
  --results-dir /var/lib/benchmark/results --certfile /etc/benchmark/server.crt --keyfile /etc/benchmark/server.key

systemctl is-active bench-mock                       # 状態
sudo journalctl -u bench-mock -n 20 --no-pager       # ログ
sudo systemctl stop bench-mock; sudo systemctl reset-failed bench-mock   # 停止（次の run-label で起動し直す前に）
```

接続確認のアクセスもアクセスログに記録されるため、確認に使ったログは退避し、計測は新しい run-label で起動し直してから始める。

venv を使って手動で起動する場合は次のとおり。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r benchmark/requirements.txt
sudo .venv/bin/python -m benchmark.mock.server \
  --scenario benchmark/mock/scenarios/A_scaling.yaml \
  --run-label A-distributed-x2-run1 \
  --results-dir /var/lib/benchmark/results \
  --certfile /etc/benchmark/server.crt \
  --keyfile /etc/benchmark/server.key
```

結果は `/var/lib/benchmark/results/<run-label>/` に保存される。

### 計測ごとの片付け（Valkey・SQS）

前回の計測で残った状態を、計測の前に毎回片付ける。Valkey は VPC 内からしか接続できないため、Worker と同じ EC2 上で、Worker と同じ `.env` を読み込んで実行する。

```bash
# 確認のみ（何も消さない）
python -m benchmark.reset_state --pages /path/to/pages.csv
# 実際に片付ける。前回の access.jsonl を渡すと、そこに出てきた URL も対象にする
python -m benchmark.reset_state --pages /path/to/pages.csv \
  --access-log /var/lib/benchmark/results/<前回の run-label>/access.jsonl --execute
```

| 対象 | 片付けないと |
|---|---|
| Valkey の重複排除キー（Mock の URL 分だけ） | 次の計測で全 URL が「処理済み」になり、何もクロールされない |
| Valkey の Crawl-delay 予約キー（Mock のドメイン分だけ） | 次の計測の最初のアクセスが待たされる |
| SQS のメインキュー・DLQ | 中断した計測のタスクや失敗が次の計測に混ざる |

- Valkey・SQS は本番と共用。Valkey は Mock のキーだけを消す。SQS はキューごと空にするため、本番のクロールが動いていないことを確認してから実行する
- 片付けの後、**Worker のコンテナを再起動する**。Worker は処理済み URL や前回の run_id をメモリに持っているため、再起動しないと次の計測でリンクが無視される
- SQS のキューを空にする操作は 60 秒に 1 回まで

### 片付け

計測用リソースは本番と同じ Terraform（同じ state）で管理している。**`terraform destroy` は本番の EC2・SQS・Valkey まで削除するため使わない。**
片付けるときは `terraform/terraform.tfvars` で `benchmark_enabled = false` にして plan を確認し、計測用リソースの削除だけになっていることを確かめてから apply する。結果用 EBS も削除されるため、必要な結果は事前に退避する。

## クローラー側の設定

- `*.bench.internal` を Mock Server のIPに名前解決させる（Route53 プライベートホストゾーン、`/etc/hosts`、docker の `extra_hosts` のいずれか）
- `SSL_CERT_FILE` と `REQUESTS_CA_BUNDLE` に `benchmark/mock/certs/ca-bundle.pem` を指定する
- 起点URLは `seed_urls.txt`（各ドメインのルート）を使う

#注意点
Neon のコンピュートは、しばらくアクセスがないと停止します。停止後の最初のクエリは起動待ちで遅くなるので、計測の直前に軽いクエリで起こしておくと条件がそろいます。