# Queue Log Schema Overview

## Relationship Diagram

```mermaid
erDiagram
    crawl_runs ||--o{ crawl_queue_state : has
    crawl_queue_state ||--o{ crawl_attempts : has
    crawl_runs ||--o{ crawl_edges : has
    crawl_runs ||--o{ crawl_failures : has
```

## Table Roles

### crawl_runs
- 1回のクロール実行を表します。
- `root_seed_count`: 実行開始時の種URL数
- `notes`: 実行メモ

### crawl_queue_state
- URLごとの現在状態を表します。
- `run_id` で `crawl_runs` に紐づきます。
- `status`, `depth`, `retry_count`, `started_at`, `finished_at` などを持ちます。

### crawl_attempts
- 1 URL に対する取得試行の履歴です。
- `queue_state_id` で `crawl_queue_state` に紐づきます。
- `ok`, `status_code`, `error_type`, `error_message`, `connection_log` を持ちます。

### crawl_edges
- 親URLから子URLを発見した関係を表します。
- どのページからどのページへ辿ったかを保存します。

### crawl_failures
- 失敗ログをまとめて残す軽量テーブルです。
- `QUEUE_LOG_FAILURES_ONLY=1` のときに主に使います。

## Write Flow

1. `dispatcher.py` が `QueueLogStore` を作成します。
2. `create_run()` で `crawl_runs` に 1 行作ります。
3. `upsert_queue_state()` / `add_attempt()` / `add_edge()` がメモリバッファに積まれます。
4. `flush()` でまとめて PostgreSQL に書き込みます。
5. `finish_run()` で `crawl_runs` の終了状態を更新します。

## Notes

- この構成は「実行単位の親 + 状態 + 履歴 + 関係 + 失敗」を分けて管理する設計です。
- まず Markdown として保存し、必要ならあとで PNG / SVG にもできます。

## Schema Split Proposal

Neon の branch を分けるのではなく、1つのDB内で schema を分ける方針。

### 1. `public`
- `universities`
- `degree_programs`
- `tuition_patterns`
- `program_tuition_map`
- seed URL 系をここに置くか、用途に応じて `etl` 側に寄せる

### 2. `etl`
- `crawl_runs`
- `crawl_queue_state`
- `crawl_attempts`
- `crawl_edges`
- `crawl_failures`
- `crawl_tag_keyword_hits`
- `crawl_domain_tag_scores`
- `crawl_tag_class_counts`
- `crawl_domain_class_counts`
- `seed_urls`
- ETL 実行の一時集計・投入制御に関わるテーブル

### 3. `observer`
- `seed_observe_runs`
- `seed_observe_results`
- `seed_search_runs`
- observer 系の監視・検索・変換ログ

## Migration Notes

- テーブル定義はそのまま維持し、`CREATE TABLE` の先頭に schema 名を付けるだけで移行可能。
- 例: `CREATE TABLE IF NOT EXISTS etl.crawl_runs (...)`
- 既存コードは `search_path` に依存しないよう、最終的には schema 修飾で参照する。
- 接続先の集約後は、`ETL_DSN` / `QUEUE_LOG_POSTGRES_DSN` / `SEARCH_LOG_POSTGRES_DSN` を用途別に整理しつつ、最終的には1DB・複数schemaへ寄せる。

---------------------------------

大学認定サイト監視クローラー

認定データを監視し、シードURLを取得することを目的とする

認定データURL→大学名→検索API→シードURL→抽出ワークフローのシードURLに追加

認定サイト
↓
大学発見
↓
既知？
↓ yes
無視

↓ no
Seed投入

ーーーーーーー
１．seed_observer
(大学認定サイトを監視 また、ここが情報を持っているためログ記録も担当する方が良いと思っている) 

２．observe_supervisor
（１サイトごとに監視しているworkerを管理する）

３．seed_searcher
（検索APIを用いてsupervisorから渡ってくる情報から検索　大学サイトを見つける　なるべくここでコース一覧ページを見つけたい　検索結果をここで軽く探索したりsitemap見てコース一覧のURLを見つけるのも良い） 

2.5．observe_dedupe
重複データを監視して、既存の情報をsearcherに渡さないようにすることでAPI使用料等を節約する。×
　既存 seed_urls のユニーク制約は (domain, root_url) です。そのため dedupe は root_url 単位で判定するのが自然ですが、同一ドメインの別パス（例：/undergraduate と /postgraduate）は別レコードとして許容される設計です。これが意図通りか確認が必要です。


４．seed_transformer
(supervisorから渡ってくるURLを整形してシードURLDBへ投入できる形へ変換　ここで、３の段階でコース一覧ページ等の物が見つかっていた場合はdepthを１等浅く、見つかっていなかった場合は少し深くして探索するプログラムにする必要がある。）
　コース一覧ページが見つかった場合に depth を浅くする設計ですが、dispatcher.py の build_site_states() は 同一ドメインの複数URLを束ねて max_depth を最大値で統一します（dispatcher.py:32）。
つまり同一ドメインに depth=1 と depth=3 の URL が混在すると、depth=3 が採用されます。浅い URL だけ depth を下げても効果が薄い点に注意が必要です。
  


５．seed_adder
（４で整形されたデータをDBへ投入する。　将来DBが変わったりした場合でも変更しやすいように４と分けとこうかと思っている。)
→init_seed_db.pyのupsert_targets()がそのまま使えるらしい。引数は変える必要がある。
init_seed_db.py:11 の infer_country() はTLDベースの簡易判定です。新しく発見した大学URLが .com や .org の場合 "unknown" になります。seed_transformer 側で検索API結果から国情報を付与しておき、seed_adder に渡す設計にしておく方が良いです。
　1.SEARCH_LOG_POSTGRES_DSN のテーブルに実行結果を保存。
　2.保存した実行結果をseed_urls(SEARCH_LOG_POSTGRES_DSN接続先)から　取り出し、ETLに投入可能なほどデータ品質が良いか判定
　３．取り出した実行結果を、QUEUE_LOG_POSTGRES_DSNの接続先DBへ投入


６．**seed_observe_logs**
（observer 実行結果を永続化するログ。デバッグ時に一時出力ではなく、あとから SQL で追えるようにする）

**run テーブル**
- `id`: 実行ID
- `external_run_id`: 呼び出し側から渡された observe run id
- `source_count`: 対象URL数
- `observed_count`: supervisor が処理した件数
- `queued_count`: queue に積まれた件数
- `dispatched_count`: searcher に渡した件数
- `transformed_count`: transformer 相当で結果化した件数
- `added_targets_count`: seed_urls へ投入した件数
- `error_count`: supervisor 側のエラー件数
- `status`: `running` / `completed` / `failed`
- `started_at`, `finished_at`: 実行時刻

**result テーブル**
- `run_id`: run テーブルへの外部キー
- `source_url`, `source_domain`: 元ページの識別子
- `source_page_type`, `page_flags`: observer 側のページ種別と判定結果
- `candidate_lines`, `request_log_count`: university 名抽出と監視ログの数
- `university_names`: observer が拾った大学名
- `hit_count`, `hits`: 検索APIの結果数と内容
- `root_seed_urls`, `detailed_seed_urls`: transformer に渡す前の seed 候補
- `course_list_found`, `recommended_depth`, `duplicate_root_urls`: 深さ判定に必要な情報
- `errors`, `error_count`: searcher のエラー内容
- `api_type`, `first_search_count`, `internal_link_extracted_count`, `fallback_executed`, `api_usage_count`: searcher の実行統計


１，２　同期
３　非同期
--------------
データクラス一つを、各ステップに応じて徐々に埋めていくという方針に

既存シードURL
　id
  country
  domain
  root_url
  depth(depth >= 0)
  enabled (0or1)
  created_at(datetime('now')),
  updated_at(datetime('now')),
  UNIQUE(domain, root_url)

「データクラス一つを各ステップで徐々に埋める」方針の場合、seed_observer → supervisor → searcher → transformer のパイプラインを通る間に埋まるフィールドが明確になっていないと、transformer で depth 決定に必要な情報（コース一覧URLが見つかったか否か）が欠落するリスクがあります。searcher の出力にそのフラグを含めることを明示的に設計しておく必要があります。

まとめ（優先度高い順）
#	問題	対応箇所
1	build_site_states が同ドメインの depth を max で統一する	seed_transformer の設計時に意識
2	depth 決定に必要な情報をデータクラスに含める	データクラス設計
3	seed_adder の入力型を upsert_targets に合わせる	seed_adder 実装
4	.com/.org ドメインの国情報を searcher 段階で補完	seed_transformer or searcher
5	schedular との実行タイミング調整	運用設計

データクラス案
　・URL（ドメイン）
　・シードURLリスト
　・depth
　・depth決定に必要な、ＵＲＬ意味推定orキーワードリストorＤＯＭから推定できるＵＲＬ一覧のコースっぽさ判定（中身まで見ると時間がかかるし役割がかぶるからＵＲＬからの推定にとどめる）
　　→キーワードリスト、ＤＯＭから推定されるそれっぽさスコア（ＤＯＭまで保存しなくてよい）

depth設定は、検索時のサイトマップか検索で有力なコース候補が見つかった場合は1~2,そうでない場合は３とかにしてシードＵＲＬに登録するのが良いのではないか

@dataclass
class SeedDiscovery:
    domain_url: str
    seed_urls: list[str] = field(default_factory=list)
    depth: int = 3

    seed_candidates: dict[URL:seedscore(それっぽさ)] = field(default_factory=list)　

    source_site: str | None = None
    university_name: str | None = None

course_keywords: list[str] = field(default_factory=list)はプログラム側で持つ
タイトル
URL
スニペット
から、シードＵＲＬっぽさをスコアリング

ここでいうスニペット（snippet）は、
検索結果に表示される説明文のことです。（検索APIが返したりするらしい）
最初に試してみたいのは、

検索APIが実際にどこまでタイトルやスニペットを返してくれるかですね。APIによってはURLしか返さないものもありますし、逆にかなり質の良いスニペットを返してくれるものもあります。そうすると設計がかなり変わります。


    #HTMLから取れる場合
        #例: TABLEタグが多い → TABLE_LIST 
            #tr>tbody>table といった数と思われる
            #table関連タグが多い場合、tableタグの中身を見て、～大学、~University、～College、～Institute などの文字列が含まれるかを確認する
            #aタグ、trタグ等、色々なタグの中にある場合があると思われるので、tableタグの中身を見て文字列判定の方が精度が高いと思われる
        #検索フォームと判定 → SEARCH_FORM
            #divタグに serarch という文字列が含まれたタグが多い　その数で判定して、何も選択せず検索ボタンをクリックさせ、大学一覧を出す
            #検索フォームから飛んだ大学詳細ページ → PROFILE
                #詳細ページもtablelistだったりする　その場合はtablelistとする
                #そうでない場合をsearch_formとして、あとから自分で調べてクローラー改善の足しにする
#PDFから取れる場合 →　PDF
PDFには3種類ある
1. テキスト埋め込みPDF → pdfplumberで取れる
2. 表PDF → pdfplumber / camelot / tabula が候補
3. 画像PDF → OCRが必要

今回は３は非対応とする

1. PageType判定
2. candidate_lines抽出
3. university_name正規化
4. 検索API接続

検索APIの利用回数に制限はあるので、1回リクエスト後にヒットしなかったら違う検索ワードで再検索　というフォールバック？が必要ですね。

１．公式ドメイン発見用検索→２．APIから得られたURLを探索、大学コース一覧サイトがあるか否か判定→3.なければ、depthを深め(3とか）に設定して渡す。コース一覧があれば、depthを浅く(1or2)にして渡す。

2.5として、もう一度検索APIから、公式サイト　コース一覧　のように検索してみてもよいかもしれないとは思わなくもありませんが。

クエリ生成は seed_searcher.py:278 で実施
検索API呼び出しは seed_searcher.py:350 で同期実行
URL探索は seed_searcher.py:203 で非同期実行
ただし探索は「取得したページ自身の判定」で、内部リンクを収集して次に辿る処理はまだない
判定結果の保持は dataclass.py:242
あなたの提案フローへの意見

公式ドメイン発見用検索
返却URL探索でコース一覧有無判定
なければ depth を深く、あれば浅くして渡す
これはとても良いです。実運用に強いです。
2.5 の再検索について
結論として「条件付きで有効」です。
API制限があるなら、無条件再検索は避けて、次の条件でだけ実行するのが最適です。

1回目で公式ドメインが確定しない
または公式ドメインは見えたが course_list_detected が立たない
かつ、URL探索でコース候補リンクが十分に得られない
このときだけ 2回目検索を実施すると、精度とコストのバランスが良いです。

推奨する実装方針

第1段階は大学名のみ中心で検索して公式ドメイン確定を優先
公式トップが見つかったら、そのページから内部リンク抽出して courses/programmes/undergraduate/postgraduate 系だけ非同期探索→これで見つかるのか？
それでも弱い場合のみ第2段階検索として site:公式ドメイン + courses/programmes を1回だけ実行
最終的に course 一覧が見つかれば depth 1-2、見つからなければ depth 3
この設計だと、あなたの責務定義にぴったり一致します。

-----
次やる事

１．引数や実行環境
URL未指定かつ supervisor 側の固定URLが空だと、件数 0 で終了（エラーではなく空実行）
２．検索API関連
APIキー未設定時は searcher 側で探索スキップ系のエラー情報が内部に積まれる
３．DB関連
DB接続情報不足、接続失敗、権限不足などで add 時に例外
４．通信関連
監視元URLや探索先へのタイムアウト・HTTPエラー
つまり、形式として何が返るかは確定済みで、実際のエラー内容は入力URL・環境変数・外部通信状態で決まる、という状態です。
５．git上の.envをいれてしまった部分の削除
削除方法を調べて、載ってない状態で公開する必要がある

-----
observerログ設計意図
1テーブルではなく、実行のまとまりを追う run テーブルと、各ソースURLの結果を残す item テーブルの2段にすると、あとで「どの入力で何が起きて 0 件になったか」を追えます。

DB接続フォールバック実装
変更の要点は2つです。ログDBの初期化・書き込み失敗を本処理から切り離し、かつ finally で必ず close() する形にしました。これで 404 など外部要因時にもハンドルが残って停止しにくくなります。次にテストで検証します。
　テストコードにRUN_DB_TARGET_CHECKを用いるものを追加（環境変数読み取り）
誤ってCIにそのまま情報が出るような形でpushしないよう注意すること

__________

分析用SQL使用想定
（SQL）
利用を通じて、SQLに直書きしているNGフィルタが増えていく想定
増え過ぎたらテーブルか、別ファイルにまとめる方向になるかもしれない

seed_noise_candidates.sql
seed_urls テーブルのノイズ候補を抽出できます（削除はしません）。
各レコードに noise_score と noise_reasons が付きます。
理由別サマリーも出せるので、どの種類のノイズが多いか把握できます。

seed_observe_noise_candidates.sql
seed_observe_results の JSONB（root_seed_urls, detailed_seed_urls）を展開して、候補URL単位でノイズ判定できます。
run_id 単位で「どの実行で何が混入したか」を追えます。
cross_domain など、観測段階特有の異常も見つけやすいです。
運用イメージ

1.まず抽出SQLで候補を見る
2.目視で方針を決める
3.ルールをアプリ側フィルタに反映
4.必要なら後で削除SQLを別途作る

抽出するロジック自体がtableタグのみになっているから、そこを変更する必要があるかもしれない。

抽出された内容が、対象サイトのどこにあるのかを調査する必要があるかもしれない
それ以前に、128件の大学名候補を検索APIに渡せていない（48件まで減ってる）し、その結果公式サイトにたどり着けていない　そこが多分次見るべき場所。

APIの利用数が多くなっているように思う。無料枠を使い切ったらplaywrightfallbackを実装する必要がある。
明日やる事
１．playwrightfallbackによって検索エンジン利用の検索、後段にURLを渡すこと。
２．なぜ大学公式サイトまでたどり着けないのか調査
　→まず404のフォールバックや、接続異常の原因を知る必要がある。
３．PDFでのデータ取得ではどうなるのかも見るべき（抽出ロジックの拡張）

→通った　PDFは後回しにする
　次は、他のURLを試すこと。同一ドメインの別の入り口のパターン
　と、完全別ドメイン　両方

先に、合格条件を記述し、それに合格したらdockerへ行くこととする

合格条件の例

追加サイトの 70%以上で root_seed_urls が 1件以上
errors に致命的接続エラーが連発しない
API使用量が想定内
後段の実行時の記録を取る事
____________
残りタスク
observer疎通完成
→ Docker Compose
→ Redis導入
→ scheduler / worker分離
→ Linux VPS稼働
→ 実行時リソース計測
→ README・構成図・資料整備
→ 応募開始

応募前に必須なのは、少なくとも次が説明できる状態です。

Docker Composeで起動できる
Redis経由で処理が流れる
VPS上で実データを取得できる
CPU・メモリ・処理件数を計測した
READMEで構成と設計理由を説明できる

分散そのものは応募後で問題ありません。
_____________________