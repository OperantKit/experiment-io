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
mise exec -- python -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
```

実ハードウェア上の optional シリアル・バックエンド用:

```bash
.venv/bin/python -m pip install -e ".[hw-serial]"
```

## テスト

```bash
.venv/bin/python -m pytest -q
```

## ローカル依存関係に関する注記

`experiment-io` は論理的には `contingency-py` の葉コンシューマであり、
`experiment-core` によって消費される。両パッケージは本 monorepo に存在
するがまだ PyPI に公開されていないため、ここでは意図的に
`[project.dependencies]` に **列挙していない**。本パッケージをより大きな
アプリケーションに組み込む際は、姉妹パッケージをローカル・パスから
インストールする:

```bash
.venv/bin/python -m pip install -e ../../core/contingency-py
```

## HIL ブリッジ・バイナリ

`HilBridge` はデフォルトで組み込みパスの Rust バイナリを起動する。
バイナリが別の場所にあるデプロイでは、パスを明示的に渡す:

```python
from experiment_io.hw.backends.hil_bridge import HilBridge

bridge = HilBridge(binary="/path/to/operantkit-hil")
```

デフォルトは `OPERANTKIT_HIL_BINARY` 環境変数でも上書き可能。

## ワイヤフォーマット

シリアルと HIL ブリッジの両バックエンドは改行区切り JSON を話す。
正確なフレーミングは `experiment_io.hw.backends.serial` と
`experiment_io.hw.backends.hil_bridge` の docstring を参照。
