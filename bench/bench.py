"""stdlib-only TCP load probe for brat-lb.

Spawns --conns threads x --reqs sequential TCP round-trips against --target.
Each round-trip: connect, send payload, shutdown(WR), read until EOF.
Measures wall clock; reports rps, p50/p99 connect ms + roundtrip ms, errors.
"""
import argparse
import socket
import threading
import time


def parse_target(s):
    h, p = s.rsplit(":", 1)
    return h, int(p)


def percentile(sorted_vals, q):
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = k - lo
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac


def worker(host, port, reqs, payload, timeout, conn_ms, rt_ms, err_count, lock):
    for _ in range(reqs):
        s = socket.socket()
        s.settimeout(timeout)
        try:
            t0 = time.perf_counter()
            s.connect((host, port))
            t1 = time.perf_counter()
            s.sendall(payload)
            try:
                s.shutdown(socket.SHUT_WR)
            except OSError:
                pass
            out = b""
            while True:
                try:
                    b = s.recv(4096)
                except socket.timeout:
                    raise OSError("recv timeout")
                if not b:
                    break
                out += b
            t2 = time.perf_counter()
            if not out:
                raise OSError("empty reply")
            c_ms = (t1 - t0) * 1000.0
            r_ms = (t2 - t1) * 1000.0
            with lock:
                conn_ms.append(c_ms)
                rt_ms.append(r_ms)
        except OSError:
            with lock:
                err_count[0] += 1
        finally:
            try:
                s.close()
            except OSError:
                pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True, help="host:port, e.g. 127.0.0.1:8080")
    ap.add_argument("--conns", type=int, default=4)
    ap.add_argument("--reqs", type=int, default=25)
    ap.add_argument("--payload", default="ping-bench")
    ap.add_argument("--timeout", type=float, default=5.0)
    a = ap.parse_args()
    host, port = parse_target(a.target)
    payload = a.payload.encode()
    conn_ms = []
    rt_ms = []
    err_count = [0]
    lock = threading.Lock()

    total = a.conns * a.reqs
    t_start = time.perf_counter()
    threads = []
    for _ in range(a.conns):
        t = threading.Thread(
            target=worker,
            args=(host, port, a.reqs, payload, a.timeout, conn_ms, rt_ms, err_count, lock),
        )
        t.start()
        threads.append(t)
    for t in threads:
        t.join()
    elapsed = time.perf_counter() - t_start

    ok = len(rt_ms)
    errors = err_count[0]
    rps = ok / elapsed if elapsed > 0 else 0.0
    sc = sorted(conn_ms)
    sr = sorted(rt_ms)
    print("target=%s conns=%d reqs_per_conn=%d total=%d" % (a.target, a.conns, a.reqs, total))
    print("elapsed_s=%.3f ok=%d errors=%d rps=%.1f" % (elapsed, ok, errors, rps))
    print("connect_ms p50=%.2f p99=%.2f n=%d" % (
        percentile(sc, 0.50), percentile(sc, 0.99), len(sc)))
    print("roundtrip_ms p50=%.2f p99=%.2f n=%d" % (
        percentile(sr, 0.50), percentile(sr, 0.99), len(sr)))


if __name__ == "__main__":
    main()