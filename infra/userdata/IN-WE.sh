#!/bin/bash
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3-boto3
mkdir -p /opt/carbonshift
cat > /opt/carbonshift/worker.py <<'PYEOF'
"""CarbonShift worker — runs on every EC2 instance as a systemd service (installed by infra/create.sh).

Long-polls its group's SQS queue and runs one job at a time: a real CPU-bound task (Monte-Carlo
estimate of pi) for cpu_seconds / CS_SPEEDUP wall-clock seconds, i.e. the job's size in
compressed time. It then writes the timings and result to DynamoDB and deletes the message.
On start it writes a heartbeat item so the controller knows the VM is ready.

Standard library + boto3 only (Ubuntu package python3-boto3). Configured via environment:
CS_GROUP, CS_QUEUE_URL, CS_TABLE, CS_SPEEDUP, AWS_DEFAULT_REGION.
"""
import json
import os
import random
import socket
import time
import urllib.request

import boto3

IMDS = "http://169.254.169.254/latest"


def instance_id() -> str:
    """Instance id from the metadata service (IMDSv2)."""
    try:
        req = urllib.request.Request(f"{IMDS}/api/token", method="PUT",
                                     headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"})
        token = urllib.request.urlopen(req, timeout=2).read().decode()
        req = urllib.request.Request(f"{IMDS}/meta-data/instance-id", headers={"X-aws-ec2-metadata-token": token})
        return urllib.request.urlopen(req, timeout=2).read().decode()
    except Exception:
        return socket.gethostname()


def run_task(real_seconds: float, seed: str) -> tuple[float, int]:
    """Pure-CPU work for `real_seconds`: Monte-Carlo estimate of pi."""
    rng = random.Random(seed)
    inside = n = 0
    end = time.perf_counter() + real_seconds
    while time.perf_counter() < end:
        for _ in range(5000):
            x, y = rng.random(), rng.random()
            inside += x * x + y * y <= 1.0
        n += 5000
    return 4 * inside / max(n, 1), n


def to_item(d: dict) -> dict:
    """Plain dict -> DynamoDB attribute map (numbers as N, everything else as S)."""
    out = {}
    for k, v in d.items():
        if isinstance(v, bool) or v is None:
            out[k] = {"S": str(v)}
        elif isinstance(v, (int, float)):
            out[k] = {"N": repr(float(v))}
        else:
            out[k] = {"S": str(v)}
    return out


def main() -> None:
    group = os.environ["CS_GROUP"]
    queue_url = os.environ["CS_QUEUE_URL"]
    table = os.environ["CS_TABLE"]
    speedup = float(os.environ.get("CS_SPEEDUP", "60"))
    sqs, ddb = boto3.client("sqs"), boto3.client("dynamodb")
    iid = instance_id()
    ddb.put_item(TableName=table, Item=to_item(
        {"job_id": f"heartbeat#{iid}", "kind": "heartbeat", "group": group, "instance_id": iid,
         "worker_started_at": time.time()}))
    print(f"worker {iid} ({group}) polling {queue_url}", flush=True)
    while True:
        # AttributeNames (not MessageSystemAttributeNames): Ubuntu 24.04 ships an older boto3
        resp = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=1, WaitTimeSeconds=20,
                                   AttributeNames=["SentTimestamp"])
        for msg in resp.get("Messages", []):
            received = time.time()
            job = json.loads(msg["Body"])
            started = time.time()
            pi, n = run_task(float(job["cpu_seconds"]) / speedup, job["job_id"])
            finished = time.time()
            ddb.put_item(TableName=table, Item=to_item({
                **job, "kind": "result", "group": group, "instance_id": iid,
                "sqs_sent_at": int(msg["Attributes"]["SentTimestamp"]) / 1000,
                "received_at": received, "started_at": started, "finished_at": finished,
                "real_runtime_s": finished - started, "result_pi": pi, "iterations": n,
            }))
            sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=msg["ReceiptHandle"])
            print(f"done {job['job_id']} in {finished - started:.1f}s", flush=True)


if __name__ == "__main__":
    main()
PYEOF
cat > /etc/systemd/system/carbonshift-worker.service <<'UNIT'
[Unit]
Description=CarbonShift worker
After=network-online.target
Wants=network-online.target
[Service]
Environment=CS_GROUP=IN-WE CS_QUEUE_URL=https://sqs.ap-southeast-2.amazonaws.com/428521272060/carbonshift-jobs-IN-WE CS_TABLE=carbonshift-results CS_SPEEDUP=60.0 AWS_DEFAULT_REGION=ap-southeast-2
ExecStart=/usr/bin/python3 -u /opt/carbonshift/worker.py
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now carbonshift-worker
