#!/usr/bin/env python3
"""Write metadata.json (and the config snapshot) for one benchmark run.

Called by scripts/run.sh while the stack is still up. Python standard library
and the docker CLI only. Records: git SHA, image tags and digests, the resolved
compose config, the gateway's config files, mock knobs, container limits, host
CPU and memory, Docker Desktop resources, k6 timing and the step plan.
"""

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(cmd, check=False):
    """Run a command in the repo root, return stdout ('' on failure)."""
    try:
        p = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if check and p.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed: {p.stderr.strip()}")
    return p.stdout.strip() if p.returncode == 0 else ""


def git_info():
    sha = run(["git", "rev-parse", "HEAD"])
    dirty = bool(run(["git", "status", "--porcelain", "--", ".", ":(exclude)results"]))
    return {"sha": sha, "branch": run(["git", "rev-parse", "--abbrev-ref", "HEAD"]), "dirty": dirty}


def host_info():
    info = {"os": platform.platform(), "machine": platform.machine()}
    if sys.platform == "darwin":
        info["cpu"] = run(["sysctl", "-n", "machdep.cpu.brand_string"])
        info["cpu_cores"] = int(run(["sysctl", "-n", "hw.ncpu"]) or 0)
        info["memory_bytes"] = int(run(["sysctl", "-n", "hw.memsize"]) or 0)
    else:
        info["cpu_cores"] = os.cpu_count()
        try:
            with open("/proc/meminfo") as f:
                info["memory_bytes"] = int(f.readline().split()[1]) * 1024
        except OSError:
            pass
    return info


def docker_info():
    raw = run(["docker", "info", "--format", "{{json .}}"])
    try:
        d = json.loads(raw)
    except ValueError:
        return {}
    return {
        "server_version": d.get("ServerVersion"),
        "operating_system": d.get("OperatingSystem"),
        "architecture": d.get("Architecture"),
        "ncpu": d.get("NCPU"),
        "mem_total_bytes": d.get("MemTotal"),
        "storage_driver": d.get("Driver"),
    }


def compose_images(compose_args):
    out = run(["docker", "compose", *compose_args, "--profile", "tools", "config", "--images"])
    return sorted(set(line for line in out.splitlines() if line))


def image_details(ref):
    raw = run(["docker", "image", "inspect", ref])
    try:
        d = json.loads(raw)[0]
    except (ValueError, IndexError):
        return {"ref": ref, "present": False}
    return {
        "ref": ref,
        "id": d.get("Id"),
        "repo_digests": d.get("RepoDigests") or [],
        "architecture": d.get("Architecture"),
        "created": d.get("Created"),
    }


def containers():
    names = run(["docker", "ps", "--filter", "name=gwbench-", "--format", "{{.Names}}"]).split()
    out = []
    for name in sorted(names):
        raw = run(["docker", "inspect", name])
        try:
            d = json.loads(raw)[0]
        except (ValueError, IndexError):
            continue
        hc = d.get("HostConfig", {})
        env = {}
        for item in d.get("Config", {}).get("Env") or []:
            k, _, v = item.partition("=")
            if k.startswith("MOCK_"):
                env[k] = v
        out.append(
            {
                "name": name,
                "image": d.get("Config", {}).get("Image"),
                "image_id": d.get("Image"),
                "cpus": (hc.get("NanoCpus") or 0) / 1e9 or None,
                "mem_limit_bytes": hc.get("Memory") or None,
                "mock_env": env,
            }
        )
    return out


def container_states():
    """State of every gwbench container, running or not: an OOM-killed or crashed container
    is not in `docker ps`, so look at `docker ps -a` (used by scripts/sustained.py)."""
    names = run(["docker", "ps", "-a", "--filter", "name=gwbench-", "--format", "{{.Names}}"]).split()
    out = []
    for name in sorted(names):
        try:
            st = json.loads(run(["docker", "inspect", name]))[0].get("State", {})
        except (ValueError, IndexError):
            continue
        out.append(
            {
                "name": name,
                "status": st.get("Status"),
                "oom_killed": bool(st.get("OOMKilled")),
                "exit_code": st.get("ExitCode"),
                "restart_count": (json.loads(run(["docker", "inspect", name]))[0].get("RestartCount")),
            }
        )
    return out


def other_containers():
    """Running containers that are not part of this benchmark (name does not start with gwbench-).

    They share the Docker VM and can add noise (D20), so the run records them.
    """
    out = []
    for line in run(["docker", "ps", "--format", "{{.Names}}\t{{.Image}}\t{{.Status}}"]).splitlines():
        name, _, rest = line.partition("\t")
        if name and not name.startswith("gwbench-"):
            image, _, status = rest.partition("\t")
            out.append({"name": name, "image": image, "status": status})
    return sorted(out, key=lambda c: c["name"])


def parse_plan(log_path):
    """The k6 script prints `GWBENCH_PLAN {json}` in setup() (see k6/lib/arrival.js)."""
    try:
        with open(log_path) as f:
            text = f.read()
    except OSError:
        return None
    m = re.search(r"GWBENCH_PLAN (\{.*\})", text)
    if not m:
        return None
    blob = m.group(1)
    for cand in (blob, blob.replace('\\"', '"')):
        # the log line quotes the message, so cut at the closing quote if needed
        for end in range(len(cand), 0, -1):
            if cand[end - 1] != "}":
                continue
            try:
                return json.loads(cand[:end])
            except ValueError:
                continue
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--target", required=True)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--run", type=int, required=True)
    ap.add_argument("--started", required=True)
    ap.add_argument("--finished", required=True)
    ap.add_argument("--k6-exit", type=int, default=0)
    ap.add_argument("--k6-script", required=True)
    ap.add_argument("--k6-log", required=True)
    ap.add_argument("--compose-files", default="")
    ap.add_argument("--config-files", default="")
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--flags", default="")
    a = ap.parse_args()

    run_dir = os.path.join(REPO, a.run_dir)
    compose_args = []
    for f in a.compose_files.split():
        compose_args += ["-f", f]

    # resolved compose file, without absolute host paths
    resolved = run(["docker", "compose", *compose_args, "--profile", "tools", "config", "--no-path-resolution"])
    with open(os.path.join(run_dir, "compose.resolved.yml"), "w") as f:
        f.write(resolved + "\n")

    # config snapshot: copy the gateway's config files under config/, same relative paths
    snapshot = []
    for rel in a.config_files.split():
        src = os.path.join(REPO, rel)
        if not os.path.isfile(src):
            print(f"metadata: config file {rel} not found, skipped", file=sys.stderr)
            continue
        dst = os.path.join(run_dir, "config", rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
        snapshot.append(rel)

    conts = containers()
    mock_env = {}
    for c in conts:
        if c["name"] == "gwbench-mock-llm":
            mock_env = c["mock_env"]

    meta = {
        "target": a.target,
        "profile": a.profile,
        "scenario": a.scenario,
        "run": a.run,
        "started": a.started,
        "finished": a.finished,
        "k6": {
            "script": a.k6_script,
            "exit_code": a.k6_exit,
            "target_url": a.base_url,
            "model": a.model,
            "runner_flags": a.flags,
            "plan": parse_plan(os.path.join(REPO, a.k6_log)),
        },
        "git": git_info(),
        "mock_knobs": mock_env,
        "images": [image_details(r) for r in compose_images(compose_args)],
        "containers": conts,
        "container_states": container_states(),
        "other_running_containers": other_containers(),
        "compose_files": a.compose_files.split(),
        "config_snapshot": snapshot,
        "host": host_info(),
        "docker": docker_info(),
        "notes": [
            "Read results from the per-step sub-metrics in summary.json (e.g. http_req_duration{step:r500});"
            " whole-run totals include warm-up and ramp.",
            "resources.csv covers the whole run including warm-up; timestamps are unix seconds."
            " k6.plan.t0_ms is when the load started.",
        ],
    }
    with open(os.path.join(run_dir, "metadata.json"), "w") as f:
        json.dump(meta, f, indent=2)
        f.write("\n")
    print(f"metadata: wrote {a.run_dir}/metadata.json")


if __name__ == "__main__":
    main()
