# benchmark_concept.md 修正事項

本ファイルは `benchmark_concept.md` に対する修正・追記事項をまとめる。
記載のない項目は `benchmark_concept.md` の内容を維持する。

| No | 修正事項 | 影響する章 |
|---|---|---|
| R1 | 仮想ドメインを多数用意し、Crawl-delayがスループットの上限にならないようにする | 4, 6, 7, 8.1, 9, 11.2 |
| R2 | 計測は原則としてMock Server側のアクセスログで行う | 7, 8, 11 |
| R3 | ドメイン構成・Crawl-delay・レスポンス内容を設定ファイルで変更可能にし、内容を一覧で確認できるようにする | 4, 5, 6 |
| R4 | Mock実装時の動作確認で判明した事項（HTTPS必須、計測時の注意点） | 4, 5, 7 |

---

## R1. 仮想ドメイン方式（Crawl-delayによるスループット上限の回避）

### R1.1 背景

Crawl-delayはドメイン単位の制約であるため、クローラー全体のスループットには理論上限がある。

```
理論上限 [URLs/sec] = Σ (1 / Crawl-delay_i)    ※ i = 各ドメイン
```

元案の例（3ドメイン、Crawl-delay 1 / 2 / 3 秒）では、

```
1/1 + 1/2 + 1/3 ≒ 1.83 URLs/sec
```

となり、Worker数を増やしてもこれ以上速くならない。
この条件では「Workerの処理能力」ではなく「Crawl-delay」がボトルネックとなり、x1 / x2 / x3 の差が現れない。

分散化によって同一時間内に処理できるURL数が増えるのは、

- ドメイン数が多く、
- 単一Workerではすべてのドメインの枠（1 / Crawl-delay）を使い切れない

場合である。実運用（`ControlPlane/URLs.txt` は約70ドメイン）はこの状態であり、Mock環境でもこの状態を再現する必要がある。

また、現在の分散版はSQS FIFOキューの `MessageGroupId = domain` を使っているため、同一ドメインのメッセージは同時に1件しか処理されない。
したがってWorkerを増やして得られる並列性は「ドメイン数」に依存し、ドメイン数が少ないとスケールしない。

### R1.2 方針

1台のMock Serverで多数の仮想ドメインを提供する。
各仮想ドメインは、共通のテンプレート（ページ構成、robots.txt、sitemap.xml）をコピーしたものとする。

```
d001.bench.internal
d002.bench.internal
...
dNNN.bench.internal
```

- Mock Serverは `Host` ヘッダーでドメインを判別する（プロセスは1つ）
- 名前解決は Route53 プライベートホストゾーンのワイルドカード `*.bench.internal → Mock ServerのプライベートIP` とする
  - 代替：各Worker / Legacy EC2 の `/etc/hosts`、または docker の `extra_hosts`
- ドメインごとの設定（Crawl-delay、ページ数、遅延、失敗パターン）は設定ファイルで与える
- Legacy / Distributed の双方に、同一のドメイン一覧・同一の設定ファイルを与える

### R1.3 ドメイン構成

仮想ドメインを以下の役割に分ける。

| 種別 | 役割 | 例 |
|---|---|---|
| 通常ドメイン（コピー） | スループット計測の主対象。テンプレートの単純コピー | 大多数 |
| Slowドメイン | `/fast` `/slow` `/very-slow` の比率を変えたドメイン | 数ドメイン |
| Failureドメイン | `/error` (HTTP 500)、`/timeout` を含むドメイン | 数ドメイン |
| robots / sitemap 異常ドメイン | robots.txt 取得失敗、sitemap 取得失敗 | 各1ドメイン |

各ドメインのページ数・レスポンス遅延の分布は全ドメインで固定の乱数seedから生成し、試験ごとに同一とする。

### R1.4 試験の分離

性能計測とCrawl-delay遵守確認では必要な条件が異なるため、試験を2種類に分ける。

**試験A：スケーリング試験（性能計測用）**

- 目的：Worker数によるスループットの変化を測る
- 条件：
  - 仮想ドメイン数を多くする（初期値の目安：100ドメイン）
  - Crawl-delayは全ドメイン共通の整数秒（初期値の目安：1秒）
  - 理論上限（上の例では 100 URLs/sec）が、Distributed x3 の実測値より十分大きいことを確認する
  - 実測値が理論上限に近づいた場合は、ドメイン数を増やして再計測する
- `benchmark_concept.md` 9章の Test 1〜4 はこの試験Aで実施する

**試験B：Crawl-delay遵守試験（機能確認用）**

- 目的：Worker数を増やしても同一ドメインへのアクセス間隔が守られることを確認する
- 条件：
  - 少数ドメイン（例：domain-a / b / c）に Crawl-delay 1 / 2 / 3 秒を設定する（元案の6章の構成）
  - Legacy、Distributed x1 / x2 / x3 で実施する
- 計測結果は `benchmark_concept.md` 11.2 に記録する
- 試験Aのアクセスログに対しても同じ違反チェックを行う

### R1.5 Crawl-delayの値に関する注意

- Crawl-delayは**整数秒**とする。分散版はCrawl-delayをSQSの `DelaySeconds`（整数）として扱うため、小数を与えると切り捨てられ、Legacyと条件が揃わない
- AWSのドキュメント上、FIFOキューではメッセージ単位の `DelaySeconds` を指定できない（キュー単位のみ）。Crawl-delay ≥ 1 秒のときの分散版の挙動（送信エラー、または遅延が効かない可能性）は、試験Bの最初に確認する

---

## R2. 計測はMock Server側で行う

### R2.1 背景

`benchmark_concept.md` 7章ではリクエストごとの記録をクローラー側で取る前提だったが、以下の問題がある。

- Legacy と Distributed で計測コードを別々に実装する必要があり、計測方法が揃わない
- Legacyには worker_id が存在しない
- 複数Workerの時計のずれにより、Crawl-delayの判定が不正確になる

### R2.2 方針

Mock Server が受信したすべてのリクエストをアクセスログとして記録し、これを計測の一次データとする。

- クローラー側（Legacy / Distributed）に計測用の変更を加えずに済む
- 時刻は Mock Server の単一の時計で記録されるため、時刻同期の問題がない
- 送信元IPでWorkerを識別する（1 EC2 = 1 Worker とする）。Legacyは送信元IPが1つになる

### R2.3 アクセスログ項目

JSON Lines 形式で1リクエスト1行を出力する。

| 項目 | 内容 |
|---|---|
| `recv_ts` | リクエスト受信時刻（epoch秒、ミリ秒以上の精度） |
| `send_ts` | レスポンス送信完了時刻 |
| `host` | Hostヘッダー（仮想ドメイン名） |
| `path` | リクエストパス |
| `client_ip` | 送信元IP（→ worker_id に対応付ける） |
| `user_agent` | User-Agent |
| `status` | 返したHTTPステータス（timeoutの場合はその旨） |
| `injected_delay_ms` | 意図的に入れた遅延 |
| `response_bytes` | レスポンスサイズ |
| `run_label` | 試験名（例：`A-distributed-x2-run1`）。Mock Server起動時に指定 |

`client_ip → worker_id` の対応表は試験ごとに別ファイルで保存する。

### R2.4 アクセスログから算出する指標

| 指標 | 算出方法 |
|---|---|
| Total Processing Time | 対象ドメインへの最初のリクエスト〜最後のリクエストの時刻差 |
| Throughput | 処理URL数 / Total Processing Time |
| Error Rate | 意図的な失敗（500 / timeout）を返した件数 / 総リクエスト数 |
| Crawl-delay違反 | 同一 `host` の連続リクエストについて `recv_ts` の差 < Crawl-delay となった件数、および最小間隔 |
| Worker別処理件数 | `client_ip` ごとのリクエスト数（負荷の偏りの確認） |

robots.txt / sitemap.xml へのリクエストもログに含まれるため、ページ取得とは分けて集計する。

### R2.5 Mock Server側で取れないもの

以下はクローラー側の情報が必要なため、Mock Serverのログだけでは得られない。

| 指標 | 取得方法 |
|---|---|
| Latency p50 / p95（クライアント側から見た応答時間） | Mock Server側では `send_ts - recv_ts`（サーバー処理時間）のみ得られる。ネットワーク往復を含む値が必要な場合は、クローラーの既存ログ（queue_log等）から取れる範囲で補う |
| 取得失敗後もクロールが継続したこと | 失敗URLの後も同一Workerからのリクエストが続いていることをアクセスログで確認する |
| CPU / Memory | CloudWatch 等（`benchmark_concept.md` 8.5 のとおり） |

Mock Server自体がボトルネックになっていないことを確認するため、Mock ServerのEC2についてもCPU / Memoryを記録する。

---

## R3. シナリオ設定ファイルと内容確認ツール

### R3.1 目的

- ドメインの総数と、ドメイン種別ごとの比率（または件数）を試験ごとに変更できるようにする
- 各ドメインの Crawl-delay、ページ数、レスポンス内容（遅延・ステータス）を試験前に一覧で把握できるようにする
- 試験に使った設定と展開後のドメイン一覧を計測結果と一緒に保存し、再現できるようにする

### R3.2 構成

```
benchmark/mock/
  scenarios/
    A_scaling.yaml        # 試験A：スケーリング試験
    B_crawl_delay.yaml    # 試験B：Crawl-delay遵守試験
  templates/
    page.html             # 通常ページのHTMLテンプレート
    robots.txt            # robots.txtテンプレート
    sitemap.xml           # sitemapテンプレート
  scenario.py             # シナリオの読み込み・展開（server / inspect 共通）
  inspect_scenario.py     # 設定を展開して内容を表示・出力するツール
  server.py               # Mock Server本体（シナリオファイルを読んで起動）
  gen_cert.py             # TLS用の自己署名CA・サーバー証明書の生成
```

※ 当初 `inspect.py` としていたが、Python標準ライブラリの `inspect` と名前が衝突するため `inspect_scenario.py` とした。

Mock Server と確認ツールは同じ展開処理を使う。確認ツールで見た内容と実際に Mock Server が返す内容は必ず一致させる。

### R3.3 シナリオ設定ファイル（例：試験A）

```yaml
scenario: A-scaling
seed: 42                      # ページ構成・応答パターンの割り当てに使う乱数seed
domain_suffix: bench.internal

# ドメイン数の指定：total + mix（比率） または counts（件数）のどちらか一方
total_domains: 100
mix:                          # 比率の合計は1.0。端数は最大剰余法で配分する
  normal: 0.90
  slow: 0.04
  failure: 0.04
  robots_error: 0.01
  sitemap_error: 0.01
# counts:                     # 件数で直接指定する場合
#   normal: 90
#   slow: 4
#   ...

# ドメイン種別ごとの設定
profiles:
  normal:
    crawl_delay: 1            # 整数秒（R1.5）
    pages: 50                 # 1ドメインあたりのページ数
    links_per_page: 5         # 各ページに含める同一ドメイン内リンク数
    responses: {fast: 1.0}    # ページごとの応答種別の比率
  slow:
    inherit: normal
    responses: {fast: 0.5, slow: 0.4, very_slow: 0.1}
  failure:
    inherit: normal
    responses: {fast: 0.8, error_500: 0.1, timeout: 0.1}
  robots_error:
    inherit: normal
    robots: error_500         # robots.txt の取得失敗
  sitemap_error:
    inherit: normal
    sitemap: error_404        # sitemap.xml の取得失敗

# 応答種別の定義
response_types:
  fast:      {status: 200, delay_ms: 10}
  slow:      {status: 200, delay_ms: 500}
  very_slow: {status: 200, delay_ms: 2000}
  error_500: {status: 500, delay_ms: 0}
  error_404: {status: 404, delay_ms: 0}
  timeout:   {status: none, delay_ms: 60000}   # 応答を返さず接続を保持する
```

試験Bのように少数のドメインを個別に定義したい場合は、`domains:` にドメイン名と種別・Crawl-delay を直接列挙できるようにする。

```yaml
scenario: B-crawl-delay
domains:
  - {name: domain-a, profile: normal, crawl_delay: 1}
  - {name: domain-b, profile: normal, crawl_delay: 2}
  - {name: domain-c, profile: normal, crawl_delay: 3}
```

### R3.4 内容確認ツール（inspect_scenario.py）

シナリオファイルを展開し、以下を出力する。

**1. サマリー（標準出力）**

```
scenario: A-scaling  (seed=42)
profile        domains  crawl_delay  pages/domain  responses
normal              90            1            50  fast 100%
slow                 4            1            50  fast 50% / slow 40% / very_slow 10%
failure              4            1            50  fast 80% / error_500 10% / timeout 10%
robots_error         1            1            50  fast 100%  (robots.txt -> 500)
sitemap_error        1            1            50  fast 100%  (sitemap.xml -> 404)
---------------------------------------------------------------
total domains: 100   total pages: 5000
theoretical max throughput (Σ 1/crawl_delay): 100.0 URLs/sec
lower bound of total time (Crawl-delayのみ考慮): 50 sec
```

- 理論上限が小さすぎる場合（試験Aで想定Worker性能を下回りそうな場合）は警告を出す

**2. ドメイン一覧・ページ一覧（ファイル出力）**

- `domains.csv`：ドメイン名、種別、Crawl-delay、ページ数、robots / sitemap の応答
- `pages.csv`：ドメイン名、パス、応答種別、ステータス、遅延
- `--domain d003.bench.internal --path /page/12` のように指定すると、そのURLで実際に返すレスポンス（ステータス、遅延、HTML本文）を表示する

これらのファイルは計測結果と同じディレクトリに保存し、どの設定で計測したかを後から追えるようにする。

### R3.5 レスポンス内容（HTML）について

- 通常ページのHTMLは `templates/page.html` に置き、中身をそのまま確認・編集できるようにする
- ページには同一ドメイン内のリンクを `links_per_page` 件含め、sitemap に載っていないページもリンクをたどって発見される構成とする
- HTMLの内容は Legacy / Distributed の抽出処理が通常どおり動き、かつ Playwright fallback が発動しない内容にする（具体的な条件は実装時にクローラーのコードを確認して決める）

---

## R4. Mock実装時の動作確認で判明した事項

Mock Server に対して現在のクローラー関数（`crawler/crawlworker.py` の `ensure_robots` / `seed_sitemap_candidates` / `worker`）をローカルで実行し、以下を確認した。

### R4.1 Mock Server は HTTPS で待ち受ける必要がある

- クローラーは robots.txt / sitemap.xml を `https://{domain}/...` 固定で取得する
- そのため Mock Server は 443 番ポートで TLS 待ち受けとし、`gen_cert.py` で作成した自己署名CAをクローラー側に信頼させる
- クローラー側の設定（Legacy EC2 / Worker 共通）:
  - `SSL_CERT_FILE` と `REQUESTS_CA_BUNDLE` に `ca-bundle.pem`（certifi のCA一覧 + Mock CA）を指定する
  - httpx / requests / urllib（RobotFileParser）のいずれもこの設定で検証が通ることを確認した
  - 分散版Worker（docker）ではコンテナに `ca-bundle.pem` をマウントし、同じ環境変数を与える

### R4.2 取得失敗時は同一URLへ2回リクエストされる

- httpx で失敗（HTTP 500 / timeout）すると、直後に requests で同じURLを再取得する（`fetch_with_fallback`）
- アクセスログ上は1ページにつき2リクエストとなり、2回目は Crawl-delay を待たずに送られる
- 集計時の扱い:
  - Throughput / Error Rate は「URL単位」（同一URLの連続リクエストを1件とする）と「リクエスト単位」の両方を算出する
  - Crawl-delay違反の判定では、同一URLへの連続リクエストを1回の取得とみなし、間隔は各URLの最初のリクエストどうしで測る（クローラーは最初のリクエスト開始時に最終アクセス時刻を記録するため）。再取得は別途件数を記録する
- この挙動は Legacy / Distributed で共通であり、クローラー側の修正は今回の対象外とする（必要ならボトルネック分析で扱う）

### R4.3 robots.txt 取得失敗ドメインでは Crawl-delay が 0 になる

- robots.txt が 404 の場合、クローラーは Crawl-delay なし（0秒）として扱い、間隔を空けずにアクセスする
- シナリオ上の `crawl_delay` はこのドメインには適用されないため、Crawl-delay違反の判定対象から除外する（`domains.csv` の `robots_status` で判別する）
- robots.txt が 5xx の場合は、urllib の RobotFileParser が全URLを不許可と判定し、ページが取得されない

### R4.4 Crawl-delay 判定の許容誤差

- 間隔はクライアントの送信時刻ではなく Mock Server の受信時刻で測るため、ネットワークの揺らぎで数ms〜数十ms短く観測されることがある（ローカル確認では Crawl-delay 1秒に対し最小 0.989秒）
- 違反判定は `間隔 < Crawl-delay - 許容誤差` とし、許容誤差の初期値は 50ms とする。最小間隔は誤差を含めた生の値も記録する
