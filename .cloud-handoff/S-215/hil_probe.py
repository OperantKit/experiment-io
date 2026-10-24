"""Flow real messages between HilBridge (experiment-io), contingency-hil (contingency-rs)
and HilBridgeApparatus (contingency-py) and print what each side does with them."""
import io, json, logging, os, socket, subprocess, sys, threading, time

BIN = sys.argv[1]
logging.basicConfig(level=logging.DEBUG, format="  [log %(name)s %(levelname)s] %(message)s", stream=sys.stdout)
from experiment_io.hw.backends.hil_bridge import HilBridge, DEFAULT_BINARY
from contingency.hw import HilBridgeApparatus
from contingency.entities import Reinforcer

def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

def spawn(port, schedule='{"type":"FR","params":{"n":3}}'):
    proc = subprocess.Popen([BIN, "--port", str(port), "--schedule", schedule],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    return proc

def connect(port, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            return socket.create_connection(("127.0.0.1", port), timeout=1.0)
        except OSError:
            time.sleep(0.02)
    raise RuntimeError("no connect")

def recv_lines(sock, wait=0.5):
    sock.settimeout(wait); buf = b""
    try:
        while True:
            c = sock.recv(4096)
            if not c: break
            buf += c
    except (socket.timeout, TimeoutError):
        pass
    return [l for l in buf.decode().split("\n") if l]

def section(t): print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)

# ---------------------------------------------------------------- E1
section("E1  HilBridge() with the default binary path")
print("DEFAULT_BINARY =", DEFAULT_BINARY)
print("exists? ", os.path.exists(DEFAULT_BINARY))
try:
    HilBridge()
except Exception as e:
    print("HilBridge() ->", type(e).__name__, e)
print("contingency-rs builds:", os.path.basename(BIN))

# ---------------------------------------------------------------- E2
section("E2  HilBridge(binary=contingency-hil) over stdio, as experiment-io intends")
port = free_port()
bridge = HilBridge(binary=BIN, args=["--port", str(port), "--schedule", '{"type":"FR","params":{"n":3}}'], session_id="s1")
time.sleep(0.3)
bridge.start()                       # {"cmd":"start","session_id":"s1"} -> stdin
bridge.deliver(0, 3.0)               # {"cmd":"reinforcer","op":0,"duration":3.0} -> stdin
print("sent to stdin: start + reinforcer; process alive:", bridge._proc.poll() is None)
got = []
t = threading.Thread(target=lambda: got.append(bridge.poll()), daemon=True)
t.start(); t.join(2.0)
print("poll() returned within 2 s?", not t.is_alive(), "->", got)
print("binary stderr (first lines):")
bridge._proc.stderr  # not read to avoid blocking; read after stop
bridge.stop()
try:
    _, err = bridge._proc.communicate(timeout=3)
except Exception as e:
    bridge._proc.kill(); _, err = bridge._proc.communicate()
for l in err.strip().splitlines()[:3]: print("  stderr:", l)
print("exit code after stop():", bridge._proc.returncode)

# ---------------------------------------------------------------- E3
section("E3  HilBridge's host->program lines delivered to contingency-hil over TCP")
port = free_port(); proc = spawn(port); sock = connect(port)
for msg in [{"cmd": "start", "session_id": "s1"}, {"cmd": "reinforcer", "op": 0, "duration": 3.0}, {"cmd": "stop"}]:
    sock.sendall((json.dumps(msg) + "\n").encode())
    print("sent", json.dumps(msg), "-> replies:", recv_lines(sock, 0.3), "| process alive:", proc.poll() is None)
print("-- now the binary's own protocol on the same socket (FR 3):")
for i in (1, 2, 3):
    m = {"type": "response", "time": float(i), "operandum": "main"}
    sock.sendall((json.dumps(m) + "\n").encode())
    print("sent", json.dumps(m), "-> replies:", recv_lines(sock, 0.3))
reinf_line = None
sock.sendall(b'{"type":"response","time":4.0,"operandum":"main"}\n{"type":"response","time":5.0,"operandum":"main"}\n{"type":"response","time":6.0,"operandum":"main"}\n')
lines = recv_lines(sock, 0.5); print("3 more responses -> replies:", lines); reinf_line = lines[-1] if lines else None
sock.sendall(b'{"type":"disconnect"}\n'); sock.close()
proc.wait(timeout=3); print("exit code after disconnect:", proc.returncode)

section("E3b  contingency-hil's reinforcer line fed to HilBridge.poll() (what the host would see)")
class Fake:
    def __init__(self, lines): self.stdin = io.StringIO(); self.stdout = io.StringIO("".join(l + "\n" for l in lines))
    def terminate(self): pass
print("line:", reinf_line)
print("HilBridge.poll() ->", HilBridge(process=Fake([reinf_line])).poll())

# ---------------------------------------------------------------- E4
section("E4  contingency-py HilBridgeApparatus (the documented client) <-> contingency-hil")
port = free_port(); proc = spawn(port, '{"type":"CRF"}'); time.sleep(0.3)
app = HilBridgeApparatus(host="127.0.0.1", port=port, operanda=("main",), reinforcers=("food",), stimuli=("house_light",))
app.connect(); print("connected:", app.status)
n = 0; t0 = time.time()
while time.time() - t0 < 1.0:
    n += len(app.poll_responses(now=time.time() - t0))
print("responses received from the binary in 1 s of polling:", n)
app.deliver_reinforcer(now=1.0, reinforcer=Reinforcer(time=1.0, magnitude=1.0, label="SR+"), channel="food")
app.set_stimulus(now=1.1, stimulus="house_light", on=True)
print("sent reinforcer + stimulus; replies in 0.5 s:", app.poll_responses(now=2.0), "(binary drops both)")
print("has HilBridgeApparatus any method that SENDS a 'response' message?",
      any(m for m in dir(app) if "response" in m and not m.startswith("poll")))
app.disconnect(); proc.wait(timeout=3); print("exit code after HilBridgeApparatus.disconnect():", proc.returncode)
_, err = proc.communicate()
for l in err.strip().splitlines(): print("  stderr:", l)
