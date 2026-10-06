"""Mock 用の証明書を EC2 に配布する（SSH / scp）。

証明書は手元で1回だけ作り（python -m benchmark.mock.gen_cert）、同じものを全インスタンスに配る。
インスタンスごとに作り直すと CA が食い違い、クローラーが Mock を信頼できなくなるため。

配布先（Name タグの Role で判別。起動中でパブリック IP があるものだけ）:
  Role=benchmark（Mock）                 server.crt / server.key -> /etc/benchmark/
  Role=controlplane / worker / legacy     ca-bundle.pem           -> /etc/benchmark/
コンテナへのマウントは docker-compose.bench.yml で行う。

Worker はパブリック IP が起動のたびに変わるため、実行時に AWS から取得する。停止中のインスタンスは飛ばす。

使い方（リポジトリのルートで実行）:
  python -m benchmark.distribute_certs --key ~/UniversityComparisonKey.pem --dry-run
  python -m benchmark.distribute_certs --key ~/UniversityComparisonKey.pem
  python -m benchmark.distribute_certs --key ~/UniversityComparisonKey.pem --check   # 配布後、Mock に HTTPS で接続できるか確認
"""
from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path

CERT_DIR = Path(__file__).parent / "mock" / "certs"
REMOTE_DIR = "/etc/benchmark"
SSH_USER = "ubuntu"
MOCK_ROLE = "benchmark"
CRAWLER_ROLES = ("controlplane", "worker", "legacy")
CHECK_URL = "https://domain-a.bench.internal/robots.txt"


def find_instances(region: str | None) -> list[dict]:
    import boto3

    ec2 = boto3.client("ec2", **({"region_name": region} if region else {}))
    pages = ec2.get_paginator("describe_instances").paginate(
        Filters=[{"Name": "tag:Role", "Values": [MOCK_ROLE, *CRAWLER_ROLES]}]
    )
    instances = []
    for page in pages:
        for reservation in page["Reservations"]:
            for i in reservation["Instances"]:
                tags = {t["Key"]: t["Value"] for t in i.get("Tags", [])}
                instances.append({
                    "name": tags.get("Name", i["InstanceId"]),
                    "role": tags.get("Role"),
                    "state": i["State"]["Name"],
                    "public_ip": i.get("PublicIpAddress"),
                })
    return sorted(instances, key=lambda x: (x["role"] != MOCK_ROLE, x["name"]))


def files_for(role: str) -> list[tuple[Path, str, str]]:
    """(手元のファイル, 配布先のファイル名, パーミッション)"""
    if role == MOCK_ROLE:
        return [(CERT_DIR / "server.crt", "server.crt", "644"), (CERT_DIR / "server.key", "server.key", "600")]
    return [(CERT_DIR / "ca-bundle.pem", "ca-bundle.pem", "644")]


def ssh_base(key: str) -> list[str]:
    return ["-i", key, "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=15"]


def run(cmd: list[str], dry_run: bool) -> subprocess.CompletedProcess | None:
    print("  $ " + " ".join(shlex.quote(c) for c in cmd))
    if dry_run:
        return None
    return subprocess.run(cmd, capture_output=True, text=True)


def distribute(instance: dict, key: str, dry_run: bool) -> bool:
    host = f"{SSH_USER}@{instance['public_ip']}"
    files = files_for(instance["role"])
    staged = [f"/tmp/{name}" for _, name, _ in files]
    # "C:\..." の "C:" をホスト名と誤認されないよう、手元のファイルは相対パスで渡す
    local_files = [os.path.relpath(src) for src, _, _ in files]
    result = run(["scp", *ssh_base(key), *local_files, f"{host}:/tmp/"], dry_run)
    if result is not None and result.returncode != 0:
        print(f"  scp failed: {result.stderr.strip()}")
        return False
    installs = " && ".join(
        f"sudo install -m {mode} -o root -g root {tmp} {REMOTE_DIR}/{name} && rm -f {tmp}"
        for (_, name, mode), tmp in zip(files, staged)
    )
    remote = f"sudo mkdir -p {REMOTE_DIR} && {installs} && ls -l {REMOTE_DIR}"
    result = run(["ssh", *ssh_base(key), host, remote], dry_run)
    if result is not None:
        if result.returncode != 0:
            print(f"  install failed: {result.stderr.strip()}")
            return False
        print("  " + result.stdout.strip().replace("\n", "\n  "))
    return True


def check(instance: dict, key: str, dry_run: bool) -> bool:
    """クローラー側のインスタンスから、配布した CA で Mock に HTTPS 接続できるか確認する。"""
    host = f"{SSH_USER}@{instance['public_ip']}"
    remote = (f"getent hosts domain-a.bench.internal; "
              f"curl -sS -o /dev/null -w 'HTTP %{{http_code}}\\n' --cacert {REMOTE_DIR}/ca-bundle.pem {CHECK_URL}")
    result = run(["ssh", *ssh_base(key), host, remote], dry_run)
    if result is None:
        return True
    print("  " + (result.stdout + result.stderr).strip().replace("\n", "\n  "))
    return result.returncode == 0 and "HTTP 200" in result.stdout


def main() -> None:
    parser = argparse.ArgumentParser(description="Distribute mock TLS certificates to EC2 instances")
    parser.add_argument("--key", required=True, help="SSH private key (UniversityComparisonKey.pem)")
    parser.add_argument("--region", default="ap-northeast-1")
    parser.add_argument("--dry-run", action="store_true", help="show the commands without running them")
    parser.add_argument("--check", action="store_true",
                        help="only check HTTPS access to the mock from crawler instances (the mock server must be running)")
    args = parser.parse_args()

    key = str(Path(args.key).expanduser())
    missing = [str(p) for p in (CERT_DIR / "server.crt", CERT_DIR / "server.key", CERT_DIR / "ca-bundle.pem") if not p.exists()]
    if missing and not args.check:
        sys.exit(f"missing certificates: {missing}; run python -m benchmark.mock.gen_cert first")

    ok = True
    for instance in find_instances(args.region):
        label = f"{instance['name']} (role={instance['role']}, {instance['state']})"
        if instance["state"] != "running" or not instance["public_ip"]:
            print(f"skip: {label} — not running; start it and run this again")
            continue
        if args.check and instance["role"] == MOCK_ROLE:
            continue
        print(f"{'check' if args.check else 'deploy'}: {label} {instance['public_ip']}")
        ok &= check(instance, key, args.dry_run) if args.check else distribute(instance, key, args.dry_run)
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
