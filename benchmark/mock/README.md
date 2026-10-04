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

EC2 には Git と Python venv が導入され、結果用の暗号化 EBS が `/var/lib/benchmark` にマウントされる。リポジトリを clone して依存関係と証明書を準備した後、結果ディレクトリを指定して起動する。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r benchmark/requirements.txt
python -m benchmark.mock.gen_cert --suffix bench.internal
sudo .venv/bin/python -m benchmark.mock.server \
  --scenario benchmark/mock/scenarios/A_scaling.yaml \
  --run-label A-distributed-x2-run1 \
  --results-dir /var/lib/benchmark/results \
  --certfile benchmark/mock/certs/server.crt \
  --keyfile benchmark/mock/certs/server.key
```

結果は `/var/lib/benchmark/results/<run-label>/` に保存される。

### 片付け

計測用リソースは本番と同じ Terraform（同じ state）で管理している。**`terraform destroy` は本番の EC2・SQS・Valkey まで削除するため使わない。**
片付けるときは `terraform/terraform.tfvars` で `benchmark_enabled = false` にして plan を確認し、計測用リソースの削除だけになっていることを確かめてから apply する。結果用 EBS も削除されるため、必要な結果は事前に退避する。

## クローラー側の設定

- `*.bench.internal` を Mock Server のIPに名前解決させる（Route53 プライベートホストゾーン、`/etc/hosts`、docker の `extra_hosts` のいずれか）
- `SSL_CERT_FILE` と `REQUESTS_CA_BUNDLE` に `benchmark/mock/certs/ca-bundle.pem` を指定する
- 起点URLは `seed_urls.txt`（各ドメインのルート）を使う
