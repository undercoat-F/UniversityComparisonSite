1. 目的

既存クローラーについて、分散化前後およびWorker数増加による性能変化を定量的に計測する。

本計測では、以下の2点を主な評価対象とする。

1. 分散化前のEC2単体構成から、現在の分散アーキテクチャへ変更したことによる性能変化
2. 分散アーキテクチャにおいてWorkerを水平スケールさせた際の性能向上

単純に「分散化した」ことを示すのではなく、

旧構成 → 分散化 → 水平スケール → ボトルネック分析 → 必要に応じた改善

という一連の性能改善過程を計測結果として残すことを目的とする。

  

2. 比較対象

以下の4構成を基本比較対象とする。

2.1 Legacy EC2

分散化前に使用していた旧クローラー。

EC2 x1(Legacy Async Crawler)

   |

Mock Web Servers

Git履歴から分散化直前のcommitを特定し、その時点のコードを基準とする。

必要に応じて以下を作成する。

tag:

legacy-pre-distributed

  

branch:

benchmark/legacy

benchmark/legacyには性能試験に必要な最小限の設定変更・計測処理のみを追加する。

元となったcommit hashを記録し、旧コードとの差分を追跡可能にする。

  

2.2 Distributed / 1 Worker

現在の分散アーキテクチャをWorker 1台で実行する。

Control Plane

      |

     SQS

      |

  Worker x1

      |

Async Crawler

      |

Mock Web Servers

Legacyとの比較によって、分散アーキテクチャそのもののオーバーヘッドまたは性能変化を確認する。

  

2.3 Distributed / 2 Workers

Control Plane

      |

     SQS

    /   \

  W1     W2

    \   /

Mock Web Servers

  

2.4 Distributed / 3 Workers

Control Plane

      |

     SQS

   /  |  \

 W1   W2   W3

   \  |  /

Mock Web Servers

必要性が認められた場合のみ、4 Worker以上の試験を追加する。

  

3. 比較条件

性能比較では、可能な限り以下の条件を統一する。

- 同一URL集合
- 同一URL件数
- 同一Mock Server
- 同一Mock Response
- 同一Crawl-delay
- 同一robots.txt
- 同一sitemap
- 同一失敗パターン
- 同等のEC2インスタンス性能
- 同一リージョン・ネットワーク条件
- 同一計測方法

特にLegacyとDistributedの比較では、実Webサイトの過去実績とMock環境の新しい実績を直接比較しない。

旧版も現在版も同じMock環境に対して実行する。

  

4. Mock Web Server

外部Webサイトの状態に性能試験結果が左右されないよう、テスト用Mock Serverを用意する。

Mock Serverは最低限、以下のレスポンスを提供する。

4.1 Success

正常なHTMLを返す。

確認項目：

- HTTP接続成功
- HTML取得成功
- HTML読み取り成功
- 正常終了として記録されること

  

4.2 Slow Response

ネットワークI/O待ちを再現するため、意図的にレスポンスを遅延させる。

例：

/slow -> 500 ms

必要に応じて複数の遅延パターンを設定する。

/fast      -> 10 ms

/slow      -> 500 ms

/very-slow -> 2000 ms

  

4.3 Failure

取得失敗を再現する。

最低限、以下のいずれかを実装する。

/error   -> HTTP 500

/timeout -> timeout

可能であれば両方実装する。

確認項目：

- 単一URLの失敗でWorker全体が停止しない
- エラー件数を記録できる
- 後続URLの処理が継続される
- Legacy / Distributed双方で同一失敗条件を与えられる

  

5. robots.txt / sitemap

Mock Server上に以下を用意する。

/robots.txt

/sitemap.xml

計測・確認対象：

- robots.txt取得時間
- sitemap取得時間
- sitemap解析時間
- sitemapから抽出されたURL件数
- Crawl-delay取得
- robots.txt取得失敗時の挙動
- sitemap取得失敗時の挙動

  

6. 複数ドメインの再現

実際のクローラーに近い状態を作るため、複数ドメイン相当のMockを用意する。

例：

domain-a

domain-b

domain-c

各ドメインには異なるCrawl-delayを設定可能とする。

例：

domain-a: 1 sec

domain-b: 2 sec

domain-c: 3 sec

目的は、

同一ドメインへのアクセス間隔を守りながら、異なるドメインを並行処理できること

を確認することである。

  

7. Crawl-delay検証

各HTTPリクエストについて最低限以下を記録する。

timestamp

domain

URL

worker_id

response_time

status

Legacy版ではworker_idが存在しない場合、固定値またはLegacyを示す識別子を使用してよい。

同一ドメインへの連続リクエストについてtimestamp差を計算する。

例：

Crawl-delay: 2 sec

  

10:00:00.000 request

10:00:02.013 request

10:00:04.021 request

以下を満たすことを確認する。

actual interval >= required Crawl-delay

特にDistributed構成では、Worker数を増加させてもCrawl-delay違反が発生しないことを確認する。

  

8. 性能指標

8.1 Throughput

単位時間あたりの処理URL数。

URLs/sec

今回の主要性能指標とする。

  

8.2 Total Processing Time

対象URLをすべて処理するまでの総時間。

単位：

seconds

  

8.3 Latency

各リクエストの処理時間を記録する。

最低限、

p50

p95

を算出する。

p50

全リクエストの50%がこの時間以内に完了したことを示す。

p95

全リクエストの95%がこの時間以内に完了したことを示す。

  

8.4 Error Rate

以下を算出する。

failed requests / total requests

意図的に発生させたエラーが正しく検出され、後続処理を妨げていないことを確認する。

  

8.5 Resource Usage

各EC2 / Workerについて最低限、

CPU usage

Memory usage

を取得する。

AWS環境ではCloudWatch等を利用して取得することを検討する。

  

9. 性能試験

Test 1: Legacy Baseline

分散化前EC2単体版を実行する。

Legacy EC2 x1

この結果を全比較の基準として保存する。

  

Test 2: Distributed Baseline

現在の分散版をWorker 1台で実行する。

Distributed Worker x1

Test 1との比較によって、

分散アーキテクチャへ変更したこと自体の性能差

を確認する。

SQS / Valkey / Control Plane等が追加されたことによるオーバーヘッドが発生しても問題ない。

  

Test 3: Distributed x2

Distributed Worker x2

Worker追加による性能向上を測定する。

  

Test 4: Distributed x3

Distributed Worker x3

さらにWorkerを追加した場合のスケール性能を測定する。

  

10. スケーリング評価

以下の2種類の比較を行う。

10.1 Legacy → Distributed

例：

Legacy EC2       100 URL/s

Distributed x1    92 URL/s

この場合、

分散化によって単一Worker性能にはオーバーヘッドが発生していることが分かる。

  

10.2 Distributed Scale-out

例：

Distributed x1     92 URL/s

Distributed x2    175 URL/s

Distributed x3    240 URL/s

Distributed x1を基準として、

x2 = 1.90x

x3 = 2.61x

のように性能向上率を算出する。

Worker数増加率と実際の性能向上率との差を確認する。

  

11. 計測結果

11.1 Performance

| Architecture | Workers | URLs | Total Time | URLs/sec | p50 | p95 | Error Rate | CPU | Memory |  
|—|—:|—:|—:|—:|—:|—:|—:|—:|  
| Legacy EC2 | 1 | - | - | - | - | - | - | - | - |  
| Distributed | 1 | - | - | - | - | - | - | - | - |  
| Distributed | 2 | - | - | - | - | - | - | - | - |  
| Distributed | 3 | - | - | - | - | - | - | - | - |

  

11.2 Crawl-delay

|   |   |   |   |   |
|---|---|---|---|---|
|Architecture|Domain|Required Delay|Minimum Observed|Violations|
|Legacy|domain-a|-|-|-|
|Distributed x1|domain-a|-|-|-|
|Distributed x2|domain-a|-|-|-|
|Distributed x3|domain-a|-|-|-|

必要に応じてdomain-b / domain-cについても記録する。

  

12. ボトルネック分析

Worker数増加に対して性能が比例して向上しない場合、その原因を調査する。

候補：

- CPU
- Memory
- Network I/O
- HTTP connection pool
- SQS
- Valkey
- PostgreSQL
- Crawl-delay
- ドメイン単位の処理制限
- Worker内部の並行数

原因を事前に決めつけず、計測結果から調査対象を絞る。

  

13. 改善後の再計測

明確なボトルネックが発見され、短時間で改善可能な場合のみ修正する。

修正後、同一条件で再度性能試験を行う。

例：

Distributed x3 Before

        ↓

Bottleneck Analysis

        ↓

Improvement

        ↓

Distributed x3 After

改善前後のThroughput、Latency、Resource Usage等を比較する。

  

14. 今回の対象外

スコープ拡大を防ぐため、以下は原則として今回の対象外とする。

- requests同期版の新規実装
- 同期 vs 非同期の詳細比較
- SQS単体の詳細ベンチマーク
- Valkey単体の詳細ベンチマーク
- PostgreSQL単体の詳細ベンチマーク
- 大規模障害復旧試験
- 本番Webサイトを利用した性能試験
- AgentTraceとの統合
- 必要性が確認される前の4台以上へのWorker増設

ボトルネック分析の結果、必要になった場合のみ追加する。

  

15. 完了条件

Test Environment

- ☐ 分散化直前のGit commitを特定した
- ☐ Legacy baselineのcommit hashを記録した
- ☐ 必要に応じてLegacy用tagを作成した
- ☐ Legacy benchmark branchを作成した
- ☐ Legacy版をEC2上で実行できる
- ☐ Mock Serverを実行できる

Mock

- ☐ 正常HTMLを取得できる
- ☐ Slow Responseを再現できる
- ☐ HTTP ErrorまたはTimeoutを再現できる
- ☐ robots.txtを取得できる
- ☐ sitemapを取得できる
- ☐ Crawl-delayを取得できる
- ☐ 複数ドメイン相当のテストができる

Functional Verification

- ☐ 取得失敗後もクロールを継続できる
- ☐ 異なるドメインを並行処理できる
- ☐ 同一ドメインのCrawl-delayを遵守できる
- ☐ Worker増加時にもCrawl-delayを遵守できる

Performance

- ☐ Legacy EC2 x1を計測した
- ☐ Distributed x1を計測した
- ☐ Distributed x2を計測した
- ☐ Distributed x3を計測した
- ☐ Total Processing Timeを比較した
- ☐ Throughputを比較した
- ☐ p50 / p95を比較した
- ☐ Error Rateを比較した
- ☐ CPU / Memoryを比較した
- ☐ Legacy → Distributedの性能差を算出した
- ☐ Distributed x1 → x2 → x3のスケール率を算出した

Analysis

- ☐ Worker数に対する性能向上率を確認した
- ☐ スケーリングが頭打ちになる場合、その原因候補を調査した
- ☐ 必要な場合のみ改善を実施した
- ☐ 改善した場合、同一条件で再計測した

  

16. 最終成果物

最終的に以下を残す。

1. 性能試験用Mock Server
2. Legacy benchmark環境
3. 性能計測プログラム
4. Legacy / Distributedの性能比較結果
5. Worker数別の性能比較結果
6. Crawl-delay遵守結果
7. CPU / Memory等のリソース計測結果
8. ボトルネック分析
9. 改善を行った場合は改善前後の比較結果

最終的に、

分散化前のEC2単体構成をベースラインとして同一条件で再現し、現在の分散アーキテクチャと比較した。さらにWorker数を1台、2台、3台と増加させ、Throughput、Latency、Error Rate、Resource UsageおよびCrawl-delay遵守状況を測定した。計測結果から水平スケールの効果とボトルネックを分析し、必要に応じて改善と再計測を実施した。

と説明できる状態を完了地点とする。