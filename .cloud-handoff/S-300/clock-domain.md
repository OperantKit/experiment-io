# S-300 — `drive` の時計の領域（clock domain）: 機器の時刻とホストの tick

experiment-io `drive()` が、反応には機器の付けた時刻を、tick にはホストの時計を渡しているために contingency の schedule が `ScheduleStateError: Non-monotonic time` で止まる件の調査。再現手順、案 (a)(b)(c) の比較、推奨、S-301 の範囲を書く。コードは直していない（`tests/test_clock_domain.py` に strict xfail の再現テストを足しただけ）。

取り込み先: 手元の planning ディレクトリ（このファイルは `.cloud-handoff/S-300/` に置いた読み替え版）。

## 0. 要約

- **原因**は伝送の遅れだけではない。`drive` のループは「poll → `poll_interval` だけ進める（実時計なら sleep）→ `handle_tick(clock())`」の順なので、tick の時刻は待ち時間の**後**に押される。待ち時間の間に起きた反応は、伝送の遅れが 0 でも直前の tick より前になる。fake で伝送遅れ 0 にしても 1 回目の反応で止まる（§2）。伝送の遅れ（手元の実測: 受信 − 機器時刻 = 中央値 1.25 ms・最大 7 ms）はこれに上乗せされる。
- **推奨は案 (a)**: session に渡す反応の時刻は受信時にホストの `clock()` で付け、機器の時刻は `ResponseSample.metadata` に残す。tick・強化子の開始と終了・終了条件がすべてホストの時計なので、session の時刻が一つの時計にそろう。機器の時計のずれ（drift）やエポックの違いの影響を受けない（§3）。
- **代償**は反応時刻の精度: 受信時刻の誤差は伝送の遅れ + ホストの遅れ（fake で中央値 1.7〜1.8 ms、最大 4.4 ms。実機の最大 7 ms が上限の目安）。精密な IRT 解析のために**機器の時刻は OKL にも残す**（`timestamp` 列はセッション時刻、機器時刻は optional 列を追加。experiment-core / session-recorder 側の後続作業、§4.3）。
- **承認対象**: 案 (a) は「反応の時刻は機器の時計が正」という現在の取り決め（`ResponseSample.timestamp` の docstring、`hil_bridge.py` の「ホストは機器の時刻をそのまま session runner に渡す」）を「session の時刻はホストの時計が正、機器の時刻は記録用」に変える。正典の向きが変わるので **user 承認待ち: 案 (a)**（`.cloud-handoff/S-300.md` の引き継ぎに記載）。
- **案 (c)** `max(ts, 直前の tick)` は不採用: 二つの時計が混ざり、機器の時計がホストより速いと ts が「未来」になって次の tick で同じエラーになる（fake で +20 ppm なら 109 s、+50 ppm なら 47 s で停止、§3.3）。**案 (b)** 機器の時計を主にする案は、同期命令・watermark・周期的な再同期・強化中の drain が要り、線形式と firmware に手が入る（§3.2）。OKL に機器時刻の列を残しておけば、将来 (b) に移っても記録の形は変わらない。

## 1. 現象と経路

### 1.1 時刻が通る経路

```
SerialOperandum.poll()  -> ResponseSample(timestamp = 機器の "ts")
HilBridge.poll()        -> ResponseSample(timestamp = 機器の "ts")
VirtualOperandum.poll() -> clock.advance(irt) して ResponseSample(timestamp = clock())   ← 時計が 1 つ

drive:
  反応あり: driver.handle_response_on(idx, sample.timestamp) / handle_response(sample.timestamp)
  反応なし: _advance(clock, poll_interval); driver.handle_tick(clock())
  強化中:   reinforcers[i].deliver(d); _advance(clock, d); driver.end_reinforcement(clock())

session-runner SessionRunner.handle_response(ts) -> Session.update(ts, n) -> schedule.step(now=ts, ResponseEvent(time=ts))
                             handle_tick(t)      -> Session.update(t, n)  -> schedule.step(now=t, None)
```

contingency-py の全 schedule（ratio / interval / time_based / differential / concurrent ほか 21 ファイル）は `step()` の先頭で `now < last_now - 1e-9` なら `ScheduleStateError("Non-monotonic time ...")` を投げる（interval.py は小文字の "non-monotonic time"）。さらに `_check_event` が `event.time == now`（誤差 1e-9）を要求するので、「反応の時刻だけ機器のまま、now はホスト」という分け方もできない。

### 1.2 なぜ 1 回目から止まるか

実時計のループを 1 回転で見る:

1. `poll()` — 何も来ていない
2. `time.sleep(poll_interval)`（既定 1 ms）
3. `handle_tick(clock())` — tick の時刻 T は sleep の**後**
4. 次の `poll()` — sleep 中に機器が送った行を読む。その `ts` は反応が起きた時刻なので T より前（遅れ 0 でも）

反応は 1 ms の sleep の中で起きる確率が高い（ループの大半が sleep）ので、機器の時計をホストに合わせても 1 回目の反応でほぼ確実に止まる。手元の実機（CRF で 1 回目から再現、時計を合わせても再現）と一致する。伝送の遅れはこの差を 1.25〜7 ms 広げるだけで、原因そのものではない。

### 1.3 仮想で起きない理由

`VirtualOperandum.poll()` は `clock.advance(irt)` した値を `timestamp` にするので、tick と同じ時計の、必ず直前の tick より後の値になる。`MultiVirtualOperandum` も同じ。つまり virtual は「反応の時刻 = ホストの時計で付けた時刻」を既に実装している。案 (a) は serial / HIL を virtual と同じ取り決めにそろえることでもある。

### 1.4 関連する所見

- **エポック**: README「Timestamps are seconds from the device's own clock」のとおり機器の `ts` は機器起動からの秒で、ホストの `clock()`（`SessionRunner` の既定は `time.time`、README の例は 0 始まりの `ManualClock`）とは原点が違う。合わせなければ最初の反応か次の tick で止まる（fake: 機器が 30 s 先なら最初の反応の後の tick で停止）。現状、使う側が原点を合わせる手順が要るが、どこにも書かれていない。
- **強化子アクセス中の行**: 実時計では `_advance` が `time.sleep(reinforcer_duration)` で止まる間 poll しない。機器はその間の反応を送り続けるので、受信バッファに溜まった行が `end_reinforcement(clock())` の**後**に読まれる。その `ts` はアクセス終了より前なので、時計の問題を直してもここで止まる案（(b)）がある（§3.2）。
- **1 回の poll に複数行**: `drive` は 1 サンプルを渡して `is_reinforcing` になると `break` し、同じ poll で返った残りのサンプルを捨てる（driver.py の内側の `for sample in samples`）。溜まった行が一度に来る場面では反応が記録から消える（fake: 18 件送って 15 件記録、§2 表の 6）。この件は時計とは別の不具合だが、同じ場面で顕在化するので S-301 の範囲に含めるか別件にするか決める（§5）。
- **contingency-hil**（S-215 の所見）も `schedule.step` の時刻が逆行すると非 0 終了する。HIL の線形式を決めるときに同じ取り決め（§4.4）が要る。

## 2. 再現手順

### 2.1 実機

手元で済み（Teensy、CRF、1 回目の反応で `ScheduleStateError: Non-monotonic time`。受信 − 機器時刻 = 中央値 1.25 ms・最大 7.0 ms。機器の時刻をホストの時計に合わせても再現）。クラウドには実機がないので、以下は伝送の遅れを模した fake で行った。

### 2.2 テスト（tracked）

`tests/test_clock_domain.py` — `ManualClock` と、機器時刻 0.0721 s の反応 1 件を `delay` 秒後に返す fake operandum で、実際の `SessionRunner` + contingency `CRF()` を `drive` に通す。`delay` は 1.25 ms と 0 の 2 通り。`xfail(strict=True, raises=ScheduleStateError)` なので今は XFAIL、`drive` を直すと XPASS で落ちてマーカーを外す合図になる。

```
.venv/bin/pytest tests/test_clock_domain.py -v            -> 2 xfailed
.venv/bin/pytest tests/test_clock_domain.py --runxfail     -> ScheduleStateError: Non-monotonic time: new now=0.0721 precedes previous now=0.074
.venv/bin/pytest                                            -> 115 passed, 2 xfailed
```

### 2.3 probe（untracked 相当、`.cloud-handoff/S-300/`）

`clock_domain_probe.py` は Poisson 反応の fake 機器（自前の時計、伝送遅れの分布 = 0.8 ms + 指数分布(平均 0.65 ms) を 7 ms で切る → 中央値 1.25 ms・最大 7 ms、任意の drift ppm とエポック、強化中に反応するか、1 回の poll で渡す行数）を、**改変していない `drive`** と実際の `SessionRunner` に通す。案 (a)(b)(c) は operandum / driver のラッパで試作した。実行ログの全文は `clock_domain_probe.log`。

| # | 場面 | 現状 (raw) | 案 (a) 受信時刻 | 案 (b) 機器時計 + watermark | 案 (c) max(ts, 直前 tick) |
|---|---|---|---|---|---|
| 1 | CRF、実測の遅れ、時計そろえ済 | **1 件目で停止**（now=0.0721 < 0.075） | 完走。誤差 中央値 1.76 ms / 最大 2.85 ms | 完走（offset 誤差 +0.6 ms、guard 8 ms）。誤差 0 | 完走。誤差 (a) と同じ |
| 2 | CRF、伝送遅れ 0 | **1 件目で停止**（now=0.0721 < 0.073） | — | — | — |
| 3 | CRF、機器の原点が 30 s 先 | **停止**（tick 0.575 < 30.07） | 完走（原点は無関係） | 同期で吸収 | **停止**（同じ） |
| 4 | FI 5 s × 5、実測の遅れ | **1 件目で停止** | 完走。42 反応、誤差 中央値 1.74 / 最大 4.18 ms、IRT 誤差 中央値 0.77 / 最大 2.74 ms | 完走。誤差 0 | 完走。誤差 (a) と同じ |
| 5 | FI 5 s × 20、機器が **速い** +50 ppm | — | 完走。誤差 中央値 1.82 / 最大 4.44 ms（drift の影響なし） | 完走（tick は最後の反応より戻さない） | **91 件目・47 s で停止**（+20 ppm なら 192 件目・109 s） |
| 6 | FI 5 s × 20、機器が **遅い** −50 ppm | — | 完走（同上） | **134 件目・76 s で停止**（再同期なし: drift が guard − 遅れ − 同期誤差 を超える） | 完走 |
| 7 | 案 (b) の同期誤差 +10 ms（guard 8 ms 超）/ guard 0 | — | — | **1 件目で停止** | — |
| 8 | FR 3、アクセス 0.5 s 中も反応（行が溜まる） | **停止**（§1.2 と同じ） | 完走。溜まった行は全部アクセス終了時刻 → 誤差 最大 440 ms、IRT 誤差 中央値 16 ms | **4 件目で停止**（溜まった行の ts < end_reinforcement） | (a) と同じ |
| 9 | 同上、1 回の poll で全行を渡す（案 (a)） | — | 送信 18 件・記録 15 件、**3 件が `drive` の `break` で消える** | — | — |

数値は seed 1、`poll_interval` 1 ms、`ManualClock`（poll の間隔がちょうど 1 ms なので実時計より誤差は小さめに出る。実機の受信遅れの最大 7 ms を上限の目安とする）。

## 3. 案の比較

評価軸: 計時の正確さ（session が schedule を評価する時刻、記録に残る時刻）、既存 API への影響、線形式（serial / HIL）への影響、HilBridge・virtual との統一、時計のずれ（drift・エポック）への強さ。

### 3.1 案 (a) 受信時にホストの `clock()` で付け、機器の時刻は `ResponseSample.metadata` に残す

実装: `drive` が sample を渡す直前に `t = clock()` を取り、`handle_response(_on)` には `t` を渡す。機器の時刻は `sample.timestamp` のまま（名前を変えるなら `device_time`）か `metadata["device_time"]` に置き、OKL に書く経路ができるまでは記録されない。

- 利点
  - session の時刻が一つの時計になる。tick・`end_reinforcement`・終了条件・`ReinforcerStartEvent` と同じ時計なので、`Non-monotonic` は構造的に起きない（表 1, 4, 5, 6, 8）。
  - 機器の drift・エポックに依存しない（表 3, 5, 6）。機器の原点合わせの手順が不要になる。
  - virtual は既にこの意味（§1.3）。serial / HIL / virtual の 3 バックエンドが同じ取り決めになり、`drive` の中の 1 か所で済む。HilBridge の poll が blocking（S-215）でも、readline が返った時刻が受信時刻になるので同じ。
  - 既存 API への影響が最小: `drive` の内部、`ResponseSample` の docstring と metadata のキー、serial / hil_bridge の docstring、README の「Timestamps are seconds from the device's own clock」への注記。`SessionDriverLike` と session-runner の呼び出しは変わらない。README の例（virtual）の出力は変わらない。
  - 物理的にも正しい側面がある: 強化子を出すのはホストなので、「ホストが反応を知った時刻」が強化までの遅れの起点になる。
- 欠点
  - 反応の時刻の精度が伝送の遅れ + ホストの遅れになる（fake: 中央値 1.7〜1.8 ms、最大 4.4 ms。実機: 最大 7 ms）。IRT の誤差は fake で中央値 0.6〜0.8 ms、最大 3.2 ms。DRL / IRT 解析の分解能としては 10 ms 単位の伝統的な記録と同等だが、機器側の割り込みで押した時刻より粗い。
  - ホストが poll していない時間は全部誤差になる。現状の実時計では強化子アクセス中（`time.sleep(reinforcer_duration)`）に溜まった行がアクセス終了時刻になる（表 8: 最大 440 ms）。→ S-301 で「アクセス中も poll する」が必須（§5）。
  - 機器の時刻を記録に残す経路が今はない（`ResponseEvent` は id / timestamp / operandum のみ、OKL の response 行は `id`, `operandum`）。残さないと「本当の IRT」が失われる。→ experiment-core / session-recorder の optional 列（§4.3）。
  - 正典の向きが変わる（機器 → ホスト）。承認対象。

### 3.2 案 (b) 機器の時計を主にし、tick も機器の時刻に換算する

実装に要るもの（fake のラッパで確かめた最小構成）:

1. 同期命令: 起動時に往復で offset（機器時刻 − ホスト時刻）を推定する線形式のメッセージ（例: `{"cmd":"time"}` → `{"kind":"time","ts":...}`）。serial firmware と HIL プログラムの両方に追加。
2. watermark: tick と `end_reinforcement` の時刻を `ホスト時刻 + offset − guard` にする。`guard` は「これより古い行はもう届かない」保証値で、最大伝送遅れ + 同期誤差 + その時点の drift 分 以上。不足すると 1 件目で止まる（表 7: 同期誤差 +10 ms、guard 0）。
3. 下限: tick は最後の反応の時刻より戻さない（`max(watermark, 直前の反応の ts)`）。これがないと反応の直後の tick が必ず逆行する。
4. 周期的な再同期: drift を追わないと guard を食いつぶす（表 6: −50 ppm で 76 s）。水晶の典型 ±20〜50 ppm なら数十秒〜数分。
5. 強化中の drain: アクセス中に溜まった行は `end_reinforcement` より前なので、`drive` がアクセス中も poll して**先に** session に渡さないと止まる（表 8）。これは (a) でも精度のために要るが、(b) では正しさのために必須。

- 利点
  - 反応の時刻が機器の精度そのまま（表の誤差 0）。IRT 解析に最適。
  - 正典の向き（機器が正）は現在の docstring のまま。
- 欠点
  - 時間ベースの schedule（FI/VI/FT/VT/DRO、時間制限）の評価が常に `guard`（≥ 7〜10 ms）+ drift 分 だけ遅れる。反応の時刻の精度を上げる代わりに、強化子の設定・提示の時刻が遅れる。
  - 線形式の変更（同期命令）が serial と HIL の両方に入る。S-215 の線形式の決定と firmware の改修が前提になる。
  - virtual には時計が一つしかないので同期は空実装になり、virtual で試せない分岐が増える。
  - `drive` の責務が増える（offset 推定、guard、再同期、drain）。試作のラッパでも tick・end_reinforcement・start の 3 か所を換算し、session の `start_time` も機器時刻にする必要があった。
  - 複数の機器（operandum ごとに別のマイコン）では時計が複数になり、「機器の時計」が一つに決まらない。ホストの時計に寄せる (a) ならこの問題はない。
- 判断: 今は採らない。OKL に機器時刻の列を残しておけば（§4.3）、(a) の記録から事後に機器時刻ベースの解析ができ、将来 (b) に移っても記録の形は変わらない。(b) に進むなら S-215 の線形式に同期命令を足すところから。

### 3.3 案 (c) `max(ts, 直前の tick)` に丸める

実装: `drive` が最後に session に渡した時刻を覚え、反応の `ts` がそれより前なら差し替える。

- 利点
  - 変更が最小（`drive` の数行）。
  - 遅れが小さいうちは、機器の `ts` が直前の tick 以降なら機器時刻をそのまま使えるので、(a) より誤差が小さいか同じ（表 1, 4 では (a) と同値。`ManualClock` の 1 ms 刻みでは tick が受信時刻と一致するため）。
- 欠点
  - 二つの時計が混ざる。機器の時計がホストより**速い**と `ts` がホストの「未来」になり、その次の tick がそれより前になって同じエラーで止まる（表 5: +20 ppm で 109 s、+50 ppm で 47 s。drift が「伝送遅れ + poll_interval」を超えた時点）。tick 側も `max` で守れば止まらないが、session の時刻が「二つの時計のうち進んでいる方」になり、どちらの時計の値かが行ごとに変わる。
  - エポック合わせが引き続き要る（表 3）。
  - 記録に残る時刻の意味が定義できない（ある反応は機器時刻、ある反応は tick 時刻）。IRT 解析で tick 境界に量子化した値が混ざる。
  - 正典の向きは「機器が正」のままに見えて、実際はホストが上書きしている。
- 判断: 不採用。

### 3.4 比較表

| 観点 | (a) 受信時刻 | (b) 機器時計 + 同期 | (c) max 丸め |
|---|---|---|---|
| session の時刻の時計 | ホスト 1 つ | 機器 1 つ（換算） | 2 つが混在 |
| 反応時刻の誤差 | 遅れ分（中央値 ≈ 2 ms、最大 ≈ 7 ms） | 0（機器精度） | 0〜遅れ分、量子化あり |
| 時間ベース schedule の評価の遅れ | poll_interval | guard（≥ 7〜10 ms）+ drift | poll_interval |
| drift への強さ | 無関係 | 再同期が要る | 速い機器で停止 |
| エポック | 無関係 | 同期で吸収 | 合わせが要る |
| 線形式の変更 | なし（docstring の注記） | 同期命令の追加（serial・HIL・firmware） | なし |
| 既存 API | `drive` 内部 + metadata | `drive` の責務増、start_time の換算 | `drive` 内部 |
| virtual との統一 | 既に同じ意味 | 空の同期 | virtual は該当なし |
| 複数機器 | 問題なし | 時計が複数 | 問題が増える |
| 正典の向き | 変わる（ホストが正） | 変わらない | 名目上変わらないが上書き |
| 強化中の drain | 精度のために要る | 正しさのために必須 | 精度のために要る |

## 4. 推奨

### 4.1 取り決め

1. **session（schedule・runner・sink に渡す時刻）はホストの `clock()` で統一する。** 反応は受信時に `drive` が `clock()` で付ける。tick・強化子・終了条件は今のまま。
2. **機器の時刻は記録用として残す。** `ResponseSample` では機器の値を保持し（`timestamp` の意味を「機器の時刻」と明記するか、`device_time` に改名して `timestamp` を廃止するかは S-301 で決める。互換性を取るなら `timestamp` を機器時刻のまま残し、`metadata["device_time"]` に同じ値を複製する案もある）、OKL には optional 列として書く（§4.3）。
3. **virtual はそのまま。** `VirtualOperandum` の `timestamp` は `clock()` の値なので、(a) の受信時刻と一致し挙動は変わらない。
4. **HilBridge も `drive` の同じ経路を通る**ので取り決めは自動的に同じ。S-215 で線形式を決めるときに「`time` は機器の時刻で、session の時刻ではない」と仕様に書く（§4.4）。

### 4.2 理由

- 問題の本体はループ構造（tick が待ちの後に押される）であり、どの時計を正にしても「二つの時計を一つの単調列にする」処理が要る。ホストに寄せる (a) は、その処理が `clock()` を読むだけで済み、drift・エポック・複数機器の問題が消える。
- 強化子を出す・時間を測るのはホスト。schedule が動くべき時刻は「ホストが反応を知った時刻」であり、反応の本当の時刻は記録に残せば解析で使える。
- 誤差 1〜7 ms は行動データの記録としては許容範囲（機器の割り込み時刻より粗いが、10 ms 分解能の伝統的な装置と同等）。許容できない研究用途（IRT の精密分布）には OKL の機器時刻列を使う。
- (b) は線形式・firmware・`drive` の責務に広く手が入り、しかも時間ベース schedule の評価が遅れる。OKL に機器時刻を残しておけば後から移れる。

### 4.3 OKL に何の時刻を書くか

- `timestamp` 列（全イベント共通の 1 列目）は**セッション時刻 = ホストの時計**。`reinforcer_start` / `reinforcer_end` / `state_change` と同じ時計で、ファイル内で単調。既存の reader と解析はそのまま。
- response 行に optional 列 **`device_time?`（float）** を追加し、機器の時刻をそのまま書く。無ければ `-`（virtual、旧ログ）。これで「受信 − 機器時刻」が行ごとに残り、遅れの分布と drift の監視にもなる。
- 必要な変更（experiment-io の外、S-301 の後続）: experiment-core `ResponseEvent` に `device_time: float | None = None`、session-runner `handle_response(_on)` に optional 引数、session-recorder `CANONICAL_CODEBOOK` の response に `Field("device_time", "float", True)` と encode / decode。`SessionDriverLike` にも optional 引数を足すか、`drive` が `metadata` を渡す別メソッドを足すかは、session-runner 側の API と合わせて決める。
- OKL header の `meta` に時計の種別（`clock_type`）が既にあるので、「session の時計 = host」「device_time の時計 = device」を notes に書く運用にできる。

### 4.4 S-215（HIL 線形式）との整合

- S-215 の推奨は語彙を contingency 側（`type` / operandum 名 / `time`）に寄せ、HIL プロセスを「装置」にする。その `time` は機器の時刻として送られる。本件の取り決めでは **`time` は記録用で、session の時刻はホストが受信時に付ける**。仕様文書にその一文を入れる。
- 同期命令（`type: "time"` の往復）は (b) に進む場合だけ追加する。今は不要。
- contingency-hil が時刻の逆行で落ちる件（S-215 所見）も、ホストが受信時刻を付けて渡すなら起きない。

### 4.5 承認が要る点

- 正典の向き: 「反応の時刻は機器が正」（現 docstring）→「session の時刻はホストが正、機器の時刻は記録」。
- `ResponseSample.timestamp` の意味の変更（または改名）。
- OKL response 行への optional 列の追加（session-recorder / experiment-core の変更を伴う）。

## 5. S-301 の範囲（案）

experiment-io 内（承認後）:

1. `drive`: sample を渡す直前に `t = clock()` を取り、`handle_response(_on)` には `t` を渡す。機器の時刻は `ResponseSample` に保持。`tests/test_clock_domain.py` の `xfail` を外し、受信時刻が渡ること・機器時刻が保持されることのテストを足す。
2. `drive`: 強化子アクセス中の poll。実時計では `time.sleep(reinforcer_duration)` を `poll_interval` 刻みにして各刻みで poll し、受信した反応は受信時刻で session に渡す（session は REINFORCING 中の反応を記録だけして schedule は進めないので安全）。`end_reinforcement` の前に一度 drain する。virtual は `_freeze_virtual_subjects` の現状どおり（アクセス中は反応しない）。
3. `drive`: 1 回の poll で複数サンプルが返り、途中で `is_reinforcing` になったとき残りを捨てない（次の反復に持ち越す）。時計とは別件なので S-301 から外して別 ID にしてもよいが、2 の drain と同じ場面で顕在化する。
4. docstring と README: `ResponseSample.timestamp`、`hil_bridge.py` 冒頭の「ホストは機器の時刻をそのまま渡す」、serial / HIL の線形式の「Timestamps are seconds from the device's own clock」に「session の時刻はホストが受信時に付ける。機器の時刻は記録用」を追記。README.ja.md を同じ構成で。AGENTS.md の「Do not use a wall-clock time source in tests」はそのまま。
5. `MultiVirtualOperandum` / `VirtualOperandum`: 変更なし（確認のテストのみ）。

experiment-io の外（S-301 の後続または並行、別 ID）:

6. experiment-core `ResponseEvent.device_time`、session-runner `handle_response(_on)` の optional 引数、session-recorder codebook `device_time?`（§4.3）。
7. S-215 の線形式仕様に §4.4 の一文。

S-301 に含めないもの: 案 (b) の同期命令、contingency-py の `_check_time` の緩和（schedule の単調性は守るべき不変条件で、緩めると FI/DRL の計算が壊れる）。

## 6. 直していない所見

- `drive` が同じ poll の残りサンプルを捨てる（§1.4、表 9）。
- 実時計では強化子アクセス中に poll しない（§1.4）。
- 機器の原点合わせの手順が README にない（(a) なら不要になる）。
- `HilBridge.poll()` が blocking、`Operandum` Protocol を満たさない（S-215 所見、本件では扱わない）。
