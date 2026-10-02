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

## クローラー側の設定

- `*.bench.internal` を Mock Server のIPに名前解決させる（Route53 プライベートホストゾーン、`/etc/hosts`、docker の `extra_hosts` のいずれか）
- `SSL_CERT_FILE` と `REQUESTS_CA_BUNDLE` に `benchmark/mock/certs/ca-bundle.pem` を指定する
- 起点URLは `seed_urls.txt`（各ドメインのルート）を使う
