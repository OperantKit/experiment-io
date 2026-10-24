# HIL プロトコル調査: HilBridge（experiment-io）と contingency-hil（contingency-rs）

S-215 の成果物。両側のコードを読み、実際にメッセージを流して食い違いを確かめ、合わせ方を案として書く。正典の向きと責務境界に触れる案なので、決定は user の承認待ち（末尾）。

## 1. 結論（先に）

- 2 つの実装は **語彙（キー名・値の型）だけでなく、役割が逆** である。experiment-io の `HilBridge` は「HIL プロセス = 装置（反応を報告し、強化子を提示する）、スケジュールはホスト側（Python セッション）が回す」。contingency-rs の `contingency-hil` は「バイナリ自身がスケジュールを回し、クライアントから反応を受け取って強化子の判定を返す」。つまり contingency-hil は装置ではなく **エンジンのサービス化** であり、`HilBridge` が期待する相手ではない。
- 実際に流すと、`HilBridge` の 3 種のコマンドは contingency-hil にすべて黙って捨てられ、contingency-hil の唯一の出力（`reinforcer`）は `HilBridge` に「unknown event」として捨てられる。接続形態も違う（標準入出力 vs TCP 7788）ので、そもそも同じ経路に乗らない。既定パス `apps/contingency-rs/target/debug/operantkit-hil` は存在せず、contingency-rs が作るのは `contingency-hil`（llm-4e の点検どおり）。
- 副産物として、contingency-hil の docstring が「クライアント」と名指しする contingency-py の `HilBridgeApparatus` とも **両者ともホスト役** で噛み合っておらず、実際につないでも反応は 1 件も来ず、送った強化子は捨てられる。contingency-rs の `docs/en/correspondence.md` §6 の「相補的に HIL ループを完成させる」は実機では成り立っていない。
- 合わせ方の案（§6）: **役割は experiment-io 側（HIL プロセス = 装置）を正**、**語彙は contingency 側（`type` キー、operandum 名、`time`）を正** にし、装置に要る lifecycle（`start`/`stop`/`ready`/`reinforcer_done`/`error`）は experiment-io 側から持ち込む。接続形態は語彙から切り離す。これは contingency-hil の責務（スケジュールを回す → 装置として振る舞う）と、HIL 仕様の正典の置き場所を変えるので承認対象。

## 2. 登場する実装と役割

| 実装 | repo / ファイル | 接続 | 誰がスケジュールを回すか | この実装は何の役か |
|---|---|---|---|---|
| `HilBridge` | experiment-io `src/experiment_io/hw/backends/hil_bridge.py` | 子プロセスの標準入出力（JSON Lines） | ホスト（session-runner + contingency-py） | ホスト側。相手の HIL プロセスを **装置** として扱う |
| `contingency-hil` | contingency-rs `src/bin/hil.rs` | TCP サーバ（既定 127.0.0.1:7788、1 クライアントで終了） | バイナリ自身（`--schedule` の JSON で構築） | スケジュールエンジン。相手を反応の供給元として扱う |
| `HilBridgeApparatus` | contingency-py `src/contingency/hw/hil_bridge.py` | TCP クライアント（既定 127.0.0.1:7788） | ホスト（contingency-py） | ホスト側。相手を **装置** として扱う |

`HilBridge` と `HilBridgeApparatus` は同じ役（ホスト）で、`contingency-hil` だけが逆向き。3 者のうち互いに動く組み合わせはない。

## 3. 両側のメッセージ形式（コードから）

### 3.1 experiment-io `HilBridge`（README「Wire formats」と docstring が一致）

ホスト → HIL プロセス（stdin、1 行 1 JSON）:

```text
{"cmd": "start", "session_id": "<str>"}
{"cmd": "reinforcer", "op": <int>, "duration": <float 秒>}
{"cmd": "stop"}
```

HIL プロセス → ホスト（stdout）:

```text
{"event": "ready", "ts": <float>}
{"event": "response", "op": <int>, "ts": <float>}
{"event": "reinforcer_done", "op": <int>, "ts": <float>}
{"event": "error", "msg": "<str>"}
```

- `response` だけが `ResponseSample(operandum_index=op, timestamp=ts)` になる。`ready`/`reinforcer_done` は debug ログ、`error` は error ログ、それ以外は warning で捨てる。JSON でない行は warning で捨てる。
- 起動: `binary=` → 環境変数 `OPERANTKIT_HIL_BINARY` → 既定 `apps/contingency-rs/target/debug/operantkit-hil`。`stop()` は `{"cmd":"stop"}` を送ってから `terminate()`。
- operandum は **整数の index**。強化子のパラメータは **提示時間（秒）** のみ。弁別刺激のメッセージはない。
- 備考（調査中に判明、範囲外なので直していない）: `HilBridge` は `Operandum`/`Reinforcer` の Protocol を満たさない（`operandum_index` 属性がなく、`deliver(operandum, duration)` と引数が違う。`isinstance` で確認、§5 参照）。また `poll()` は blocking なパイプの `readline()` を回すので、何も出力しないプロセス相手では返ってこない（§5 の E2）。

### 3.2 contingency-rs `contingency-hil`（`src/bin/hil.rs` の docstring と実装が一致）

クライアント → バイナリ（TCP、1 行 1 JSON、`type` で判別）:

```text
{"type": "response", "time": <float>, "operandum": "<str>"}      ← schedule.step に渡す
{"type": "reinforcer", "time", "channel", "magnitude", "label"}  ← 無視（バイナリが強化の権限を持つ）
{"type": "stimulus", "time", "stimulus", "on"}                    ← 無視（HAL 配線なし）
{"type": "disconnect"}                                            ← 終了
```

バイナリ → クライアント:

```text
{"type": "reinforcer", "time": <float>, "channel": "<str>", "magnitude": <float>, "label": "<str>"}
```

- `response` 以外に出力はない。`response` は時刻と operandum 名を `ResponseEvent` にしてスケジュールを 1 ステップ進め、強化されたときだけ `reinforcer` を 1 行返す。`channel` は `--channel`（既定 `food`）、`magnitude`/`label` はスケジュールの `Reinforcer`（既定 1.0 / `SR+`）。
- `operandum` 欠落時は `"main"`、`time` 欠落時は 0.0。未知の `type`・JSON でない行は黙って捨てる。時刻が単調でないと `schedule.step` がエラーになり、バイナリは非 0 終了する（接続ごとの保護はない）。
- スケジュールは CLI の `--schedule '{"type":"FR","params":{"n":3}}'` か `--schedule-file`。対応は CRF/EXT/FR/FI/FT/DRO/DRL/DRH の 8 種で、params の名前は手書き（`n`, `interval`, `mode`, `response_count`, `time_window`）。
- 標準入力は読まない。標準出力には何も書かず、ログは標準エラーのみ。

### 3.3 参考: contingency-py `HilBridgeApparatus`（contingency-hil が「クライアント」と呼ぶ実装）

- 装置 → ホスト: `{"type":"response","time","operandum"}` を **受信** して `ResponseEvent` にする（宣言した operanda 名以外は捨てる）。
- ホスト → 装置: `{"type":"reinforcer",…}`、`{"type":"stimulus",…}`、切断前に `{"type":"disconnect"}` を **送信**。
- `response` を送る手段はない。つまり語彙は contingency-hil と同じだが、矢印の向きが contingency-hil と完全に逆。

## 4. 食い違いの一覧

| # | 項目 | `HilBridge`（experiment-io） | `contingency-hil`（contingency-rs） | 実際に起きること |
|---|---|---|---|---|
| 1 | 役割（誰がスケジュールを回すか） | ホスト。HIL プロセスは装置 | バイナリ自身。相手は反応の供給元 | 両方がスケジュールを回し、どちらも装置ではない。反応がどこからも生まれない |
| 2 | 接続形態 | 子プロセスの stdin/stdout | TCP サーバ（127.0.0.1:7788、1 クライアント） | `HilBridge` が stdin に書いた行は読まれず、stdout は空のまま `poll()` が返ってこない（E2） |
| 3 | 判別キー | 方向別: `cmd`（ホスト→）/ `event`（→ホスト） | 双方向とも `type` | `cmd` 行は `type` なしとして捨てられる（E3）。`type` 行は `event` なしとして warning（E3b） |
| 4 | operandum の表し方 | 整数 index `op` | 文字列名 `operandum`（既定 `"main"`） | 変換表がどこにもない。experiment-io の `drive` と session-runner は index、contingency の並立スケジュールは名前で振り分ける |
| 5 | 時刻のキー | `ts` | `time` | 名前違いのみ。どちらも float、単位は秒（contingency 側は「単位非依存」と明記） |
| 6 | 強化子の内容 | `duration`（提示時間、秒） | `channel` / `magnitude` / `label` | 装置が要る情報（何秒・どの装置）と、スケジュールが持つ情報（大きさ・ラベル）が別々で、どちらも相手の欄を持たない |
| 7 | lifecycle | `start(session_id)` / `stop`、装置から `ready` | `disconnect` のみ（接続＝開始） | `start`/`stop` は捨てられ、`stop` では終了しない（E3）。`ready` に相当する出力はない |
| 8 | 強化子提示の完了 | `reinforcer_done` | なし | ホストは装置側の提示完了を知る手段を失う |
| 9 | エラー報告 | `event: error` | なし（標準エラーにログ、致命的なら終了） | — |
| 10 | 弁別刺激 | なし | `stimulus`（受けるが捨てる） | experiment-io 側には弁別刺激の語彙自体がない |
| 11 | スケジュールの指定 | 経路に乗らない（ホストが持つ） | CLI の `--schedule` JSON（8 種、params 名は手書き） | S-239（schedule spec）と重なる。§7 |
| 12 | 既定の実行ファイル | `apps/contingency-rs/target/debug/operantkit-hil` | 生成物は `target/debug/contingency-hil` | 名前もディレクトリも一致せず `FileNotFoundError`（E1） |
| 13 | 不正な行の扱い | warning を出して続行 | 黙って続行（時刻の逆行だけ致命的） | 設計思想は近い（行単位で捨てて続ける）。ログの有無が違う |
| 14 | 仕様の置き場所 | experiment-io README「Wire formats」+ docstring | `src/bin/hil.rs` docstring + README 1 行 | どちらも自分の形式を仕様として書いており、相手を参照しない。正典が未定 |

## 5. 実際に流した結果（証跡）

contingency-rs を `cargo build --bin contingency-hil` し、experiment-io の venv（experiment-io + contingency-py + session-runner を `--no-deps` で導入）から検証スクリプトを実行した。スクリプトは `.cloud-handoff/S-215/hil_probe.py`、全文ログは `.cloud-handoff/S-215/hil_probe.log`。要点:

- **E1 既定パス**: `DEFAULT_BINARY` は存在せず、`HilBridge()` は `FileNotFoundError`。contingency-rs の生成物名は `contingency-hil`。
- **E2 `HilBridge(binary=contingency-hil)`（experiment-io の想定経路）**: `start()`・`deliver()` は stdin に書けるがプロセスは読まない（標準エラーに `listening on 127.0.0.1:<port>` と出て待ち受けに入る）。`poll()` は 2 秒待っても返らない（空のパイプで `readline()` が block）。`stop()` は `{"cmd":"stop"}` を無視され、`terminate()` の SIGTERM で終了（exit −15）。
- **E3 `HilBridge` の 3 コマンドを TCP で contingency-hil に送る**: `start` / `reinforcer` / `stop` のいずれも返答なし・状態変化なし・プロセスは生存。同じソケットで `{"type":"response",…}` を 3 回送ると 3 回目に `{"channel":"food","label":"SR+","magnitude":1.0,"time":3.0,"type":"reinforcer"}` が返る（FR 3 として正しく動く）。`{"type":"disconnect"}` で exit 0。
- **E3b contingency-hil の `reinforcer` 行を `HilBridge.poll()` に食わせる**: `HilBridge: unknown event: {...}` の warning を出して捨て、`[]` を返す。
- **E4 contingency-py `HilBridgeApparatus` ↔ contingency-hil（CRF）**: 接続は成功。1 秒 poll し続けて反応 0 件（バイナリは `response` を送らない）。`deliver_reinforcer` と `set_stimulus` は送れるがバイナリが捨てる。`HilBridgeApparatus` に `response` を送るメソッドはない。`disconnect()` でバイナリは exit 0。
- **Protocol 適合**: `isinstance(HilBridge(...), Operandum)` は `False`、`Reinforcer` も `False`。`deliver` の引数は `(operandum, duration)` で、Protocol の `deliver(duration)` と違う。
- **ベースライン**: experiment-io `pytest` 115 passed。contingency-rs `cargo test --test hil_integration` 2 passed（どちらも自分の形式だけを fake 相手に検証しており、相互接続は試験されていない）。

## 6. 合わせ方の案

決めるべきことは 2 つに分かれる。(a) 役割: HIL の境界をどこに置くか（装置の境界か、エンジンの境界か）。(b) 語彙: キー名・識別子・強化子の表現。

### 案 1（推奨）: 境界は「装置」、語彙は contingency 側、lifecycle は experiment-io 側から

- **役割**: experiment-io `HilBridge` の分担を正とする。ホスト = Python セッション（session-runner + contingency。将来は手元の user 決定どおり内部で Rust エンジンを使う包み）、HIL プロセス = 装置。スケジュールは経路に乗らない。
  - 理由: OperantKit の層構造（session-runner → experiment-core / contingency、その下の HAL が experiment-io）と整合する。Rust エンジンを正本にする方針は「Python セッションの中で Rust を呼ぶ」ことで満たされ、ソケット越しにエンジンを呼ぶ必要はない。contingency-hil の「エンジンのサービス化」は PyO3 束縛（`contingency_core`）と役割が重なり、しかも接続形態・1 クライアント終了・時刻逆行で落ちる、と実機向けには弱い。
- **語彙**: contingency 側に寄せる。双方向とも `type` で判別、operandum は **名前**、時刻は `time`。理由: contingency-py と contingency-rs が同じ語彙を既に共有し、Rust の `ResponseEvent` / `Reinforcer` が serde でそのまま出せる型である（エンジン正本の方針と同じ向き）。experiment-io の index は `drive` に渡す operanda の並び順から名前へ写せる（`MultiVirtualOperandum.split()` のように operandum ごとの view を作る設計と相性がよい）。
- **装置に要るものを足す**: experiment-io 側にしかない `start` / `stop` / `ready` / `reinforcer_done` / `error` を `type` 語彙に移して採用。強化子は装置の欄（`channel`、`duration`）とスケジュールの欄（`magnitude`、`label`）を両方持てるようにする。`stimulus` は contingency 側のものをそのまま採用（弁別刺激の提示に必要）。
- **接続形態**: 語彙から切り離す。`HilBridge` は serial backend と同じく「`readline`/`write`/`flush` を持つ 2 本の行ストリーム」を受け取る形にし、子プロセスの stdio でも TCP ソケットでも同じ形式を流す。`poll()` は非 blocking を前提にする（serial backend と同じ）。
- **contingency-hil の扱い**: 装置役のバイナリに作り直す（contingency-rs の `hw::VirtualApparatus` を上の語彙で stdio/TCP に出す、または実機 HAL を載せる）か、名前を「スケジュールサーバ」に改めて HIL 文脈から外す。いずれも責務境界の変更なので承認対象。
- **正典の置き場所**: HIL 線形式の仕様文書は 1 か所に置き、他は参照する。候補は contingency-rs（型の定義元。エンジン正本の方針と同じ側）か experiment-io（HAL の持ち主）。推奨は **contingency-rs の docs に「装置線形式」として置き、experiment-io README はそれを参照**。ただし「仕様が Rust 側、実装の主な利用者が Python 側」になるので、ミラー関係の決め方として承認が要る。

案 1 で揃えた形の例（提案。確定ではない）:

```text
host -> apparatus  {"type":"start","session_id":"s1"}
                   {"type":"reinforcer","time":4.0,"operandum":"left","channel":"food","duration":3.0,"magnitude":1.0,"label":"SR+"}
                   {"type":"stimulus","time":0.0,"stimulus":"house_light","on":true}
                   {"type":"stop"}
apparatus -> host  {"type":"ready","time":0.0}
                   {"type":"response","time":1.25,"operandum":"left"}
                   {"type":"reinforcer_done","time":7.0,"channel":"food"}
                   {"type":"error","message":"..."}
```

### 案 2: 境界は「エンジン」、contingency-hil の形式をそのまま正

- contingency-hil を正とし、Python 側は装置役になる（実機を serial で読み、`response` を送り、`reinforcer` を受けて提示する）。experiment-io の `HilBridge` は「スケジュールクライアント」に作り直す。
- 向く場面: スケジュール判定を別プロセス・別機（たとえば rig 上の Rust）で時間厳密に回し、Python は記録と設定だけにしたいとき。
- 難点: session-runner が持つセッションの状態（終了条件、強化中の扱い、`handle_tick`）とスケジュールが別プロセスに割れ、OKL への記録や experiment-core との整合が難しくなる。PyO3 束縛と重複する。schedule spec（S-239）が線形式に必須になる。

### 案 3: 両方残して相互変換のアダプタを書く

- 語彙変換だけなら小さいが、§4 の #1（役割の逆転）は変換では埋まらない。反応の供給元がいない問題は残るので、単独では解にならない。

### 推奨

案 1。承認が要る点は次の 3 つで、それぞれ yes/no で答えられる形にしてある。

1. HIL の境界は「装置」とし、スケジュールはホスト側（Python セッション、内部で Rust エンジン）が持つ。contingency-hil の責務を「スケジュールを回す」から外す。
2. 線形式の語彙は contingency 側（`type` / operandum 名 / `time` / `channel`・`magnitude`・`label`）を正とし、experiment-io の `cmd`/`event` と整数 `op` は廃止する。lifecycle と `duration` は experiment-io から持ち込む。
3. 線形式の仕様文書は contingency-rs に置き、experiment-io はそれを参照する（ミラーの向き）。

## 7. 手元の他の決定との重なり

- **Rust エンジン正本・Python 包み（user 決定）**: 案 1 はこの方針と矛盾しない。HIL の境界を装置に置けば、エンジンをどちらの言語で回すかは HIL 線形式に影響しない。案 2 は方針を「ソケット越し」に読み替えたもので、PyO3 束縛と二重になる。
- **S-239（schedule spec）**: contingency-hil の `--schedule '{"type":"FR","params":{"n":5}}'` と contingency-py の conformance fixture の `schedule` 欄は、すでに事実上の schedule spec になっている（8 種のみ、params 名は手書き）。S-239 で中間の形を決めるときは、この CLI 形式を引き継ぐか捨てるかを明示したほうがよい。案 1 なら HIL 線形式は schedule spec を運ばないので、S-239 と S-215 は独立に進められる。案 2 なら S-239 の結果が HIL 線形式の一部になる。

## 8. 調査中に見つけた、今回は直していないこと

- experiment-io: `DEFAULT_BINARY` が存在しない名前・場所を指す（README の Limitations には書いてある）。`HilBridge` が `Operandum`/`Reinforcer` Protocol を満たさず、`drive` にそのまま渡せない（README と AGENTS.md は渡せると読める）。`poll()` が blocking。
- contingency-rs: `docs/en/correspondence.md` §6 と `src/hw/mod.rs` の「Python の `HilBridgeApparatus` がクライアント」という記述は、矢印が逆で成り立たない。contingency-hil は時刻が逆行する 1 行で終了する。
- contingency-py: `HilBridgeApparatus` は装置役の相手が OperantKit 内に存在しない（contingency-hil は相手にならない）。
