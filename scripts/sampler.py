#!/usr/bin/env python3
"""Per-container CPU and memory sampler for Docker Desktop. Writes a CSV.

Talks to the Docker Engine API over the unix socket (python stdlib only, no
docker CLI, no pip). For every running container whose name matches a glob it
reads the daemon's stats stream, which delivers one sample per second, and
writes one CSV row per sample. (On Docker Desktop a one-shot stats call blocks
for about a second, so polling would drift; the stream does not.)

CSV columns (long format, one row per container per tick):
  ts                unix time of the daemon's reading, seconds, 3 decimals.
                    Containers are read independently, so their timestamps are
                    close to, not exactly, equal within a second
  container         container name
  cpu_percent       CPU use since the previous reading of that container.
                    100 means one full core (like `docker stats`, not
                    normalised by the number of CPUs)
  mem_bytes         working set: usage minus inactive file cache
                    (the figure `docker stats` shows as MEM USAGE)
  mem_limit_bytes   the container memory limit (0 if none is reported)
  pids              number of processes
  net_rx_bytes      cumulative bytes received, all interfaces
  net_tx_bytes      cumulative bytes sent, all interfaces

The first reading of a container has no previous reading, so no row is written
for it. Containers are looked up again every few seconds, so containers that
start or restart during the run are picked up. Stop with SIGTERM or SIGINT; the
file is flushed about once a second and once more on exit.

Example:
  sampler.py --out /results/run1/resources.csv 'gwbench-mock-llm' 'gwbench-litellm*'
"""

import argparse
import csv
import fnmatch
import http.client
import json
import os
import queue
import signal
import socket
import sys
import threading
import time
from datetime import datetime, timezone

SOCKET_PATH = "/var/run/docker.sock"
COLUMNS = [
    "ts",
    "container",
    "cpu_percent",
    "mem_bytes",
    "mem_limit_bytes",
    "pids",
    "net_rx_bytes",
    "net_tx_bytes",
]


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path, timeout=5):
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self._path)


def api_get(path, sock_path):
    conn = UnixHTTPConnection(sock_path)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        body = resp.read()
        if resp.status != 200:
            raise RuntimeError(f"GET {path}: HTTP {resp.status}")
        return json.loads(body)
    finally:
        conn.close()


def parse_time(value):
    """RFC 3339 with nanoseconds, as Docker prints it, to unix seconds."""
    # 2026-11-10T19:40:46.123456789Z, nanoseconds trimmed to microseconds
    head, _, frac = value.rstrip("Z").partition(".")
    frac = (frac + "000000")[:6]
    dt = datetime.strptime(head, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    return dt.timestamp() + int(frac) / 1e6


def mem_working_set(mem):
    usage = mem.get("usage", 0)
    stats = mem.get("stats", {})
    # cgroup v2 reports inactive_file, cgroup v1 total_inactive_file
    cache = stats.get("inactive_file", stats.get("total_inactive_file", 0))
    return max(usage - cache, 0)


def parse_sample(s):
    """One stats object from the daemon as a dict of raw cumulative counters."""
    if not s.get("read") or s["read"].startswith("0001"):
        return None
    nets = s.get("networks") or {}
    mem = s.get("memory_stats", {})
    return {
        "t": parse_time(s["read"]),
        "cpu_ns": s["cpu_stats"]["cpu_usage"]["total_usage"],
        "mem": mem_working_set(mem),
        "mem_limit": mem.get("limit", 0),
        "pids": s.get("pids_stats", {}).get("current", 0),
        "rx": sum(n.get("rx_bytes", 0) for n in nets.values()),
        "tx": sum(n.get("tx_bytes", 0) for n in nets.values()),
    }


def stream_container(name, sock_path, out, stop):
    """Read the stats stream of one container until it ends; put rows on `out`."""
    conn = UnixHTTPConnection(sock_path, timeout=5)
    prev = None
    try:
        conn.request("GET", f"/containers/{name}/stats?stream=true")
        resp = conn.getresponse()
        if resp.status != 200:
            return
        while not stop.is_set():
            line = resp.readline()
            if not line:
                return  # container stopped
            snap = parse_sample(json.loads(line))
            if snap is None:
                prev = None
                continue
            last, prev = prev, snap
            if last is None or snap["t"] <= last["t"] or snap["cpu_ns"] < last["cpu_ns"]:
                continue  # first reading, repeated reading, or counter reset
            cpu = (snap["cpu_ns"] - last["cpu_ns"]) / ((snap["t"] - last["t"]) * 1e9) * 100
            out.put(
                [
                    f"{snap['t']:.3f}",
                    name,
                    f"{cpu:.2f}",
                    snap["mem"],
                    snap["mem_limit"],
                    snap["pids"],
                    snap["rx"],
                    snap["tx"],
                ]
            )
    except (OSError, ValueError, KeyError):
        return  # socket timeout, container gone, odd payload: the next scan retries
    finally:
        conn.close()


def matching_containers(patterns, excludes, sock_path):
    try:
        listing = api_get("/containers/json", sock_path)
    except Exception:
        return None
    names = []
    for c in listing:
        for raw in c.get("Names", []):
            name = raw.lstrip("/")
            if any(fnmatch.fnmatchcase(name, p) for p in patterns) and not any(
                fnmatch.fnmatchcase(name, x) for x in excludes
            ):
                names.append(name)
    return sorted(set(names))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("patterns", nargs="+", metavar="NAME", help="container name or glob, e.g. 'gwbench-*'")
    ap.add_argument("--out", required=True, help="CSV file to write (parent directories are created)")
    ap.add_argument("--duration", type=float, default=0, help="stop after this many seconds (default: run until signalled)")
    ap.add_argument("--exclude", action="append", default=[], metavar="GLOB", help="skip matching containers (repeatable)")
    ap.add_argument("--socket", default=SOCKET_PATH, help=f"Docker socket (default {SOCKET_PATH})")
    ap.add_argument("--rescan", type=float, default=3.0, help="seconds between container lookups (default 3)")
    args = ap.parse_args(argv)

    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    rows = queue.Queue()
    readers = {}  # container name -> thread
    rows_written = 0
    started = time.monotonic()
    last_scan = -1e9

    with open(args.out, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(COLUMNS)
        fh.flush()
        print(f"sampler: writing {args.out}", flush=True)
        while not stop.is_set():
            now = time.monotonic()
            if args.duration and now - started >= args.duration:
                break
            if now - last_scan >= args.rescan:
                last_scan = now
                found = matching_containers(args.patterns, args.exclude, args.socket)
                for name in found or []:
                    t = readers.get(name)
                    if t is None or not t.is_alive():
                        t = threading.Thread(
                            target=stream_container,
                            args=(name, args.socket, rows, stop),
                            daemon=True,
                        )
                        t.start()
                        readers[name] = t
            try:
                writer.writerow(rows.get(timeout=0.5))
                rows_written += 1
                while True:  # drain what is queued
                    writer.writerow(rows.get_nowait())
                    rows_written += 1
            except queue.Empty:
                pass
            fh.flush()
        stop.set()
        try:  # rows that arrived while stopping
            while True:
                writer.writerow(rows.get_nowait())
                rows_written += 1
        except queue.Empty:
            pass
        fh.flush()
    print(f"sampler: stopped, {rows_written} rows", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
