# experiment-io

:gb: [English README](README.md)

OperantKit 実験ランタイムのためのハードウェア抽象化層 (HAL)。

`experiment-io` はプロトコル契約と具象バックエンドを定義する。これに
より、同じ Session/Runner コードで以下を駆動できる:

- **virtual** — 隔離されたユニットテスト用のインメモリ Poisson 応答器
- **serial** — 双方向バイトストリーム越しの Arduino スタイル JSONL
  (pyserial は optional)
- **hil_bridge** — `contingency-rs` hardware-in-the-loop サブプロセス
  への stdio ブリッジ

インポートは意図的に軽量: `experiment_io.hw.backends.serial` の取り込み
に `pyserial` は不要、`experiment_io.hw.backends.hil_bridge` の取り込み
に Rust バイナリの存在も不要。

## 公開 API

```python
from experiment_io import drive, SessionDriverLike
from experiment_io.hw import Clock, Operandum, Reinforcer, ResponseSample
from experiment_io.hw.backends.virtual import (
    ManualClock,
    VirtualSubject,
    ScallopSubject,
    SpacedRespondingSubject,
    IrtCopyistSubject,
    MatchingSubject,
    MultiVirtualSubject,
    VirtualOperandum,
    MultiVirtualOperandum,
    RecordingReinforcer,
)
from experiment_io.hw.backends.serial import (
    SerialOperandum,
    SerialReinforcer,
    open_serial,
)
from experiment_io.hw.backends.hil_bridge import HilBridge
```

### ハードウェアからセッションを駆動する

`experiment_io.drive(driver, operanda, reinforcers, clock, ...)` は
HAL デバイスをループポーリングして、`SessionDriverLike` Protocol を
満たす任意のオブジェクト（典型的には `session_runner.SessionRunner`）に
反応を流し込む。driver 型は構造的に型付けされるため、本パッケージは
`session-runner` を import しない。

```python
from contingency import ScheduleBuilder
from experiment_core import ReinforcementCountExit
from experiment_io import drive
from experiment_io.hw.backends.virtual import (
    ManualClock, RecordingReinforcer, VirtualOperandum, VirtualSubject,
)
from session_runner import SessionConfig, SessionRunner

clock = ManualClock()
op = VirtualOperandum(0, VirtualSubject(rate_hz=5.0, seed=0), clock)
rein = RecordingReinforcer(operandum_index=0)

runner = SessionRunner(
    SessionConfig(
        name="demo",
        schedule=ScheduleBuilder.fr(1),
        exit_condition=ReinforcementCountExit(count=3),
    ),
    clock=clock,
)
runner.start(start_time=clock())
drive(runner, [op], [rein], clock, reinforcer_duration=0.2)
```

## インストール

```bash
pip install experiment-io
pip install 'experiment-io[hw-serial]'   # optional シリアル・バックエンド
```

開発用:

```bash
mise exec -- python -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
```

実ハードウェア上の optional シリアル・バックエンド用:

```bash
.venv/bin/python -m pip install -e ".[hw-serial]"
```

## 30 秒で動く例

2 つの仮想オペランダムと、オペランダムごとの給餌器で並立スケジュールを駆動し、強化子が 10 回提示されるまで動かす:

```python
from contingency_dsl import parse
from contingency import from_dsl
from experiment_core import ReinforcementCountExit
from experiment_io import drive
from experiment_io.hw.backends.virtual import (
    ManualClock, MultiVirtualOperandum, MultiVirtualSubject, RecordingReinforcer,
)
from session_runner import SessionConfig, SessionRunner

clock = ManualClock()
subject = MultiVirtualSubject(rates_hz=[2.0, 0.5], seeds=[1, 2])  # seed 固定のポアソン反応体
operanda = MultiVirtualOperandum(subject, clock).split()
feeders = [RecordingReinforcer(operandum_index=i) for i in range(2)]

runner = SessionRunner(
    SessionConfig(
        name="conc-fr",
        schedule=from_dsl(parse("Conc(FR 5, FR 5)")),
        exit_condition=ReinforcementCountExit(count=10),
    ),
    clock=clock,
)
runner.start(start_time=clock())
drive(runner, operanda, feeders, clock, reinforcer_duration=3.0)

print(runner.state_name, runner.component_responses, [len(f.calls) for f in feeders], round(clock(), 1))
# FINISHED [45, 6] [9, 1] 49.8
```

速く反応する側（毎秒 2 反応）が 10 回のうち 9 回の強化子を生み、各強化子は比率を満たしたオペランダムと対になる給餌器から提示された。時計には 1 回あたり 3 秒の強化子提示が含まれる。`MultiVirtualOperandum` と `RecordingReinforcer` を `SerialOperandum` と `SerialReinforcer` に置き換えると、同じセッションをマイコンで動かせる。この例は [session-runner](https://github.com/OperantKit/session-runner)（`pip install session-runner`。experiment-core・contingency-py・contingency-dsl-py も入る）を使う。

## 自作の 50 行のハードウェアループでは足りない理由

1 つの装置向けに書いたポーリングループは、時間・振り分け・装置のコードが混ざる。本パッケージはそれらを分ける:

- **どのバックエンドにも同じループ.** `drive` は `Operandum` / `Reinforcer` プロトコルを満たす任意のオブジェクト（仮想被験体・シリアル装置・contingency-rs の HIL ブリッジ）で動く。
- **強化子を正しいオペランダムへ.** 並立スケジュールでは各反応を `handle_response_on(index, ...)` に渡し、強化子はそれを生んだオペランダムと対の装置から提示する。
- **決定的なシミュレーション.** `ManualClock` を使うとループは待たずに仮想時間を進め、仮想被験体は seed で固定されるので、セッション全体が数ミリ秒で走り、毎回同じ結果になる。複数オペランダムの仮想被験体は強化子提示中に反応しない。
- **時間に基づくスケジュールも進む.** 反応が来ないときは時計を進めて `handle_tick` を呼ぶので、FI・VI・FT・VT・DRO と時間制限が評価される。`max_duration` と `max_steps` でループの上限を決められる。
- **文書化されたワイヤ形式.** シリアル装置と HIL ブリッジは改行区切り JSON を話す（入力 `{"op": 0, "ts": 1.25, "kind": "response"}`、出力 `{"cmd": "reinforcer", "op": 0, "duration": 3.0}`）。`pyserial` は任意で、必要になった時点で import する。

## テスト

```bash
.venv/bin/python -m pytest -q
```

## 依存関係

`experiment-io` は他の OperantKit パッケージに実行時依存を持たない。
`drive` は `SessionDriverLike` の形を持つ任意のオブジェクトを受け取るので、
セッション自体は `session-runner`（`pip install session-runner`）から
用意する。上の例もそうしている。

## HIL ブリッジ・バイナリ

`HilBridge` はデフォルトで組み込みパスの Rust バイナリを起動する。
バイナリが別の場所にあるデプロイでは、パスを明示的に渡す:

```python
# requires the operantkit-hil binary
from experiment_io.hw.backends.hil_bridge import HilBridge

bridge = HilBridge(binary="/path/to/operantkit-hil")
```

デフォルトは `OPERANTKIT_HIL_BINARY` 環境変数でも上書き可能。

## ワイヤフォーマット

シリアルと HIL ブリッジの両バックエンドは改行区切り JSON を話す。
正確なフレーミングは `experiment_io.hw.backends.serial` と
`experiment_io.hw.backends.hil_bridge` の docstring を参照。
