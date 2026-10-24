# experiment-io

:gb: [English README](README.md)

experiment-io は、セッションと装置をつなぎます。オペランダム（レバー、キー、ノーズポークのセンサ）から反応を読み、強化子の装置（給餌器、ディッパー、ポンプ）を動かします。同じループが、模擬の時間で動く仮想被験体、シリアルポートにつないだマイコン、ハードウェア・イン・ザ・ループ（HIL）のプログラムのどれとも動きます。[OperantKit](https://github.com/OperantKit) の中では、[session-runner](https://github.com/OperantKit/session-runner) の下にあるハードウェア抽象化層（HAL）です。

- **ハードウェアなしでセッションを試せます。** 種を固定した仮想被験体と手動の時計で、セッション全体が数ミリ秒で終わり、毎回同じ結果になります。
- **どの装置でも同じループです。** `drive` がオペランダムを読み、反応をセッションに渡し、反応したオペランダムと対になった装置に強化子を提示させます。
- **時間に基づくスケジュールも進みます。** 反応が来ないあいだも FI・VI・FT・VT・DRO と制限時間を判定します。

AI のコーディングエージェント向けの案内は [AGENTS.md](AGENTS.md) にあります。

## インストール


Python 3.11 以上が必要です。experiment-io はまだ PyPI に登録されていません。依存するパッケージが無いので、GitHub からそのまま入ります。

```bash
pip install git+https://github.com/OperantKit/experiment-io
pip install pyserial     # 任意: 実際のシリアルポートを開くときだけ
```

下の例ではセッションも必要です。セッションは session-runner とその下のパッケージが提供します。これらも一緒に GitHub から入れます。

```bash
# PyPI に公開されるまでは、OperantKit の依存と一緒に GitHub から入れる。
pip install git+https://github.com/OperantKit/contingency-dsl-py \
            git+https://github.com/OperantKit/contingency-py \
            git+https://github.com/OperantKit/experiment-core \
            git+https://github.com/OperantKit/session-runner
```

## 例

並立 FR 5 FR 5 スケジュールを 2 つの仮想オペランダムで動かし、オペランダムごとに 1 台の給餌器を対にして、強化子を 10 回提示するまで続けます。

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
subject = MultiVirtualSubject(rates_hz=[2.0, 0.5], seeds=[1, 2])  # 種を固定したポアソン型の反応者
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

速いほうの反応者（毎秒 2 反応）が 10 回のうち 9 回の強化子を得ました。どの強化子も、比率を満たしたオペランダムと対になった給餌器が提示しています。時計には 1 回の提示ごとに 3 秒の強化子への接近時間が含まれます。`MultiVirtualOperandum` と `RecordingReinforcer` を `SerialOperandum` と `SerialReinforcer` に置き換えると、同じセッションがマイコンで動きます。

## 含まれるもの

| 部分 | 名前 | 役目 |
|---|---|---|
| ループ | `drive`、`SessionDriverLike` | `SessionDriverLike` のメソッドを持つセッション（ふつうは `session_runner.SessionRunner`）を、終わるまで装置から動かします。`max_duration` と `max_steps` でループの上限を決めます |
| インタフェース | `Clock`、`Operandum`、`Reinforcer`、`ResponseSample`（`experiment_io.hw`） | 装置の実装が備えるべきもの |
| 仮想の装置 | `ManualClock`、`VirtualOperandum`、`MultiVirtualOperandum`、`RecordingReinforcer` | 模擬の時間、模擬のオペランダム、提示を 1 回ずつ記録する強化子の装置 |
| 仮想被験体 | `VirtualSubject`、`MultiVirtualSubject` | ポアソン型の反応者（反応間時間が指数分布）。複数版はオペランダムごとに 1 つ |
| | `ScallopSubject` | 直前の強化子からの時間とともに反応率が上がります。FI では強化後休止（PRP）のあとに反応が加速します |
| | `SpacedRespondingSubject` | どの反応間時間（IRT）が強化されるかで IRT が形作られます |
| | `IrtCopyistSubject` | 強化された IRT を写します（Tanno & Silberberg, 2012） |
| | `MatchingSubject` | 局所的な強化率が高いオペランダムへ反応を移します（逐次改良; Herrnstein & Vaughan, 1980） |
| シリアル | `SerialOperandum`、`SerialReinforcer`、`open_serial`（`experiment_io.hw.backends.serial`） | `readline` / `write` / `flush` を持つ任意のストリームで、改行区切りの JSON を話す装置。`open_serial` は pyserial で実際のポートを開きます |
| HIL | `HilBridge`（`experiment_io.hw.backends.hil_bridge`） | HIL のプログラムを起動し、その標準入力と標準出力でやりとりします |

複数のオペランダムを受け持つ仮想被験体は、強化子への接近中は反応しません。

ヨークした VR と VI で、`IrtCopyistSubject` は Catania et al. (1977) の反応率の差（VR のほうが速い）を再現しますが、Baum (1993) が報告した中程度の強化率での 2 つの反応率の収束は再現しません。プールの大きさ 5〜200、IRT のばらつき（CV）0.1〜0.5 の範囲で、VR とヨークした VI の反応率の比が 1.1 まで下がる強化率の 10 シード中央値は、1 時間あたり 300〜600 回の範囲に一度も入りませんでした。ヨーク比較（プールの大きさ 70、CV 0.2。この範囲から選んだ値）は [operantkit-py](https://github.com/OperantKit/operantkit-py) の `tests/laws/test_ratio_vs_interval.py` です。

### やりとりの形式

シリアルの装置。1 行に JSON オブジェクト 1 つ:

```text
device -> host   {"op": 0, "ts": 1.25, "kind": "response"}
host -> device   {"cmd": "reinforcer", "op": 0, "duration": 3.0}
```

HIL のプログラム。標準入力と標準出力の 1 行に JSON オブジェクト 1 つ:

```text
host -> program  {"cmd": "start", "session_id": "..."}
                 {"cmd": "reinforcer", "op": 0, "duration": 3.0}
                 {"cmd": "stop"}
program -> host  {"event": "ready", "ts": 0.0}
                 {"event": "response", "op": 0, "ts": 1.25}
                 {"event": "reinforcer_done", "op": 0, "ts": 4.25}
                 {"event": "error", "msg": "..."}
```

時刻は装置自身の時計の秒です。`HilBridge` に起動するプログラムを渡すには、`binary=` か環境変数 `OPERANTKIT_HIL_BINARY` を使います。

```python
# requires a HIL program that speaks the protocol above
from experiment_io.hw.backends.hil_bridge import HilBridge

bridge = HilBridge(binary="/path/to/hil-program")
```

## 制限

- まだ PyPI に登録されておらず、API は変わることがあります。
- ファームウェアは配布していません。シリアルの装置は上の JSON の行を話す必要があり、そのプログラムは自分で書きます。
- [contingency-rs](https://github.com/OperantKit/contingency-rs) の `contingency-hil` は TCP のサーバで、メッセージの形式も違うため、`HilBridge` からはそのままでは動かせません。`binary=` も `OPERANTKIT_HIL_BINARY` も無いとき、`HilBridge` は組み込みの相対パスを使いますが、contingency-rs はそこにプログラムを作りません。
- 実時間の時計では、`drive` は `time.sleep` で待ち、既定で 1 ミリ秒ごとに読みます（`poll_interval`）。ハードウェアでの時間の精度は測っていません。
- 仮想被験体はモデルです。書いたスケジュールどおりにプログラムが強化子を提示することは示しますが、動物がどう振る舞うかは示しません。

## 開発

```bash
git clone https://github.com/OperantKit/experiment-io && cd experiment-io
python -m venv .venv
G=git+https://github.com/OperantKit
.venv/bin/python -m pip install --no-deps $G/contingency-dsl-py $G/contingency-py $G/experiment-core $G/session-runner -e .
.venv/bin/python -m pip install pytest pytest-cov hypothesis
.venv/bin/pytest
```

## 参考文献

- Baum, W. M. (1993). Performances on ratio and interval schedules of reinforcement: Data and theory. *Journal of the Experimental Analysis of Behavior*, 59(2), 245–264. https://doi.org/10.1901/jeab.1993.59-245
- Catania, A. C., Matthews, T. J., Silverman, P. J., & Yohalem, R. (1977). Yoked variable-ratio and variable-interval responding in pigeons. *Journal of the Experimental Analysis of Behavior*, 28(2), 155–161. https://doi.org/10.1901/jeab.1977.28-155
- Herrnstein, R. J., & Vaughan, W. (1980). Melioration and behavioral allocation. In J. E. R. Staddon (Ed.), *Limits to action: The allocation of individual behavior* (pp. 143-176). Academic Press.
- Tanno, T., & Silberberg, A. (2012). The copyist model of response emission. *Psychonomic Bulletin & Review*, 19(5), 759–778. https://doi.org/10.3758/s13423-012-0267-1

## ライセンス

MIT です。[LICENSE](LICENSE) を参照してください。
