"""計測用の Neon ブランチを作成・リセットする（Neon API v2）。

ブランチ構成:
  <親（本番）ブランチ>
   └─ bench-base   seed テーブルを置く。計測では書き込まない
       └─ bench-run 計測で書き込む。計測ごとに bench-base の状態へリセットする

子ブランチを持つブランチと、ルート（本番）ブランチはリセットできないため、2段にしている。
ブランチはロールとパスワードを親から引き継ぐため、接続先はホスト名だけが変わる。

環境変数:
  NEON_API_KEY     Neon の API キー（必須）
  NEON_PROJECT_ID  省略時は DB_HOST のエンドポイントから自動で探す
  DB_HOST          本番の接続先。親ブランチの特定と、誤って本番を操作しないための確認に使う

使い方（リポジトリのルートで実行）:
  python -m benchmark.neon_branch setup     # bench-base と bench-run を作成（作成済みなら何もしない）
  python -m benchmark.neon_branch reset     # bench-run を bench-base の状態に戻す（計測ごとに実行）
  python -m benchmark.neon_branch status    # ブランチと接続先ホストを表示
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Optional

API_BASE = "https://console.neon.tech/api/v2"
BASE_BRANCH = "bench-base"
RUN_BRANCH = "bench-run"
OPERATION_TIMEOUT_SEC = 300


class NeonApi:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def request(self, method: str, path: str, body: Optional[dict] = None) -> dict[str, Any]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            API_BASE + path,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as res:
                payload = res.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise RuntimeError(f"Neon API {method} {path} failed: {exc.code} {detail}") from exc
        return json.loads(payload) if payload else {}

    def wait_operations(self, project_id: str, operations: list[dict]) -> None:
        deadline = time.monotonic() + OPERATION_TIMEOUT_SEC
        for op in operations:
            while True:
                status = self.request("GET", f"/projects/{project_id}/operations/{op['id']}")["operation"]["status"]
                if status == "finished":
                    break
                if status in ("failed", "error", "cancelled", "skipped"):
                    raise RuntimeError(f"Neon operation {op['id']} ({op.get('action')}) ended with status={status}")
                if time.monotonic() > deadline:
                    raise TimeoutError(f"Neon operation {op['id']} did not finish in {OPERATION_TIMEOUT_SEC}s")
                time.sleep(2)


def endpoint_id_from_host(host: str) -> str:
    """ep-xxx-123456-pooler.region.aws.neon.tech -> ep-xxx-123456"""
    label = host.split(".", 1)[0]
    return label[: -len("-pooler")] if label.endswith("-pooler") else label


def list_project_ids(api: NeonApi) -> list[str]:
    """組織（organization）配下のプロジェクトは org_id を付けないと一覧に出ないため、所属組織ごとに探す。"""
    org_ids = [o["id"] for o in api.request("GET", "/users/me/organizations").get("organizations", [])]
    project_ids = []
    for org_id in org_ids or [None]:
        path = f"/projects?org_id={org_id}" if org_id else "/projects"
        project_ids += [p["id"] for p in api.request("GET", path)["projects"]]
    return project_ids


def find_production(api: NeonApi, db_host: str, project_id: Optional[str]) -> tuple[str, str]:
    """(project_id, 本番ブランチの id) を DB_HOST のエンドポイントから求める。"""
    endpoint_id = endpoint_id_from_host(db_host)
    project_ids = [project_id] if project_id else list_project_ids(api)
    for pid in project_ids:
        for endpoint in api.request("GET", f"/projects/{pid}/endpoints")["endpoints"]:
            if endpoint["id"] == endpoint_id:
                return pid, endpoint["branch_id"]
    sys.exit(f"could not find the Neon endpoint {endpoint_id} (from DB_HOST); set NEON_PROJECT_ID")


def branches_by_name(api: NeonApi, project_id: str) -> dict[str, dict]:
    return {b["name"]: b for b in api.request("GET", f"/projects/{project_id}/branches")["branches"]}


def endpoint_host(api: NeonApi, project_id: str, branch_id: str) -> Optional[str]:
    endpoints = api.request("GET", f"/projects/{project_id}/branches/{branch_id}/endpoints")["endpoints"]
    rw = [e for e in endpoints if e["type"] == "read_write"]
    return rw[0]["host"] if rw else None


def create_branch(api: NeonApi, project_id: str, name: str, parent_id: str, cu: Optional[float]) -> dict:
    endpoint: dict[str, Any] = {"type": "read_write"}
    if cu is not None:
        # 計測中に自動スケールで性能が変わらないよう、最小と最大を同じにする
        endpoint.update(autoscaling_limit_min_cu=cu, autoscaling_limit_max_cu=cu)
    created = api.request("POST", f"/projects/{project_id}/branches", {
        "branch": {"name": name, "parent_id": parent_id},
        "endpoints": [endpoint],
    })
    api.wait_operations(project_id, created.get("operations", []))
    print(f"created branch {name} ({created['branch']['id']})")
    return created["branch"]


def cmd_setup(api: NeonApi, project_id: str, production_id: str, args) -> None:
    branches = branches_by_name(api, project_id)
    base = branches.get(BASE_BRANCH)
    if base is None:
        base = create_branch(api, project_id, BASE_BRANCH, production_id, None)
    elif base.get("parent_id") != production_id:
        print(f"note: {BASE_BRANCH} already exists and its parent is not the production branch")
    run = branches.get(RUN_BRANCH) or create_branch(api, project_id, RUN_BRANCH, base["id"], args.cu)
    if run.get("parent_id") != base["id"]:
        sys.exit(f"{RUN_BRANCH} exists but its parent is not {BASE_BRANCH}; fix it in the Neon console")
    cmd_status(api, project_id, production_id, args)


def cmd_reset(api: NeonApi, project_id: str, production_id: str, args) -> None:
    branches = branches_by_name(api, project_id)
    base, run = branches.get(BASE_BRANCH), branches.get(RUN_BRANCH)
    if not base or not run:
        sys.exit(f"{BASE_BRANCH} / {RUN_BRANCH} not found; run setup first")
    if run["id"] == production_id or run.get("parent_id") != base["id"]:
        sys.exit(f"refusing to reset: {RUN_BRANCH} is not a child of {BASE_BRANCH}")
    started = time.monotonic()
    result = api.request("POST", f"/projects/{project_id}/branches/{run['id']}/restore",
                         {"source_branch_id": base["id"]})
    api.wait_operations(project_id, result.get("operations", []))
    print(f"reset {RUN_BRANCH} to the current state of {BASE_BRANCH} ({time.monotonic() - started:.1f}s)")


def cmd_status(api: NeonApi, project_id: str, production_id: str, args) -> None:
    branches = branches_by_name(api, project_id)
    print(f"project: {project_id}")
    for name in (BASE_BRANCH, RUN_BRANCH):
        b = branches.get(name)
        if not b:
            print(f"  {name}: (not created)")
            continue
        parent = next((n for n, x in branches.items() if x["id"] == b.get("parent_id")), b.get("parent_id"))
        print(f"  {name}: id={b['id']} parent={parent} host={endpoint_host(api, project_id, b['id'])}")
    print("connect with the same DB_USER / DB_PASSWORD / DB_NAME as production; only DB_HOST changes")


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage Neon branches for the benchmark")
    sub = parser.add_subparsers(dest="command", required=True)
    p_setup = sub.add_parser("setup", help=f"create {BASE_BRANCH} and {RUN_BRANCH}")
    p_setup.add_argument("--cu", type=float, help=f"fix the compute size of {RUN_BRANCH} (e.g. 1)")
    sub.add_parser("reset", help=f"reset {RUN_BRANCH} to {BASE_BRANCH}")
    sub.add_parser("status", help="show branches and hosts")
    args = parser.parse_args()

    try:
        from dotenv import load_dotenv

        load_dotenv(encoding="utf-8-sig")
    except ImportError:
        pass
    api_key = os.getenv("NEON_API_KEY", "").strip()
    db_host = os.getenv("DB_HOST", "").strip()
    if not api_key:
        sys.exit("NEON_API_KEY is not set")
    if not db_host:
        sys.exit("DB_HOST (production) is not set")

    api = NeonApi(api_key)
    project_id, production_id = find_production(api, db_host, os.getenv("NEON_PROJECT_ID", "").strip() or None)
    {"setup": cmd_setup, "reset": cmd_reset, "status": cmd_status}[args.command](api, project_id, production_id, args)


if __name__ == "__main__":
    main()
