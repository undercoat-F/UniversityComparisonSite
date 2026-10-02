"""ベンチマーク用Mock Web Server

Host ヘッダーで仮想ドメインを判別し、シナリオに従って応答する。
受信した全リクエストを JSON Lines のアクセスログに記録する（R2.3）。

使い方（リポジトリのルートで実行）:
  python -m benchmark.mock.server --scenario benchmark/mock/scenarios/A_scaling.yaml \
      --run-label A-distributed-x2-run1 \
      --certfile benchmark/mock/certs/server.crt --keyfile benchmark/mock/certs/server.key

出力（--results-dir/<run-label>/）:
  access.jsonl                 アクセスログ
  domains.csv, pages.csv など  起動時のシナリオ展開結果（inspect_scenario.py --out と同じ）
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

import uvicorn

from benchmark.mock.inspect_scenario import write_outputs
from benchmark.mock.scenario import Scenario, ScenarioError, load_scenario

REPO_ROOT = Path(__file__).resolve().parents[2]
FLUSH_INTERVAL_SEC = 1.0


class AccessLog:
    def __init__(self, path: Path, run_label: str) -> None:
        self.path = path
        self.run_label = run_label
        self._buffer: list[str] = []
        self._file = path.open("a", encoding="utf-8")

    def write(self, record: dict[str, Any]) -> None:
        record["run_label"] = self.run_label
        self._buffer.append(json.dumps(record, ensure_ascii=False))

    def flush(self) -> None:
        if self._buffer:
            self._file.write("\n".join(self._buffer) + "\n")
            self._file.flush()
            self._buffer.clear()

    def close(self) -> None:
        self.flush()
        self._file.close()


class MockApp:
    """フレームワークに依存しない ASGI アプリ。"""

    def __init__(self, scenario: Scenario, log: AccessLog) -> None:
        self.scenario = scenario
        self.log = log
        self._flusher: asyncio.Task | None = None

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "lifespan":
            await self._lifespan(receive, send)
        elif scope["type"] == "http":
            await self._http(scope, receive, send)

    async def _lifespan(self, receive, send) -> None:
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                self._flusher = asyncio.create_task(self._flush_loop())
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                if self._flusher:
                    self._flusher.cancel()
                self.log.close()
                await send({"type": "lifespan.shutdown.complete"})
                return

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(FLUSH_INTERVAL_SEC)
            self.log.flush()

    async def _http(self, scope, receive, send) -> None:
        recv_ts = time.time()
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        host = headers.get("host", "").split(":")[0].lower()
        path = scope["path"]
        record: dict[str, Any] = {
            "recv_ts": recv_ts,
            "send_ts": None,
            "host": host,
            "path": path,
            "method": scope["method"],
            "client_ip": (scope.get("client") or ("", 0))[0],
            "user_agent": headers.get("user-agent", ""),
            "response_type": None,
            "status": None,
            "injected_delay_ms": 0,
            "response_bytes": 0,
            "outcome": "sent",
        }
        try:
            resolved = self.scenario.resolve(host, path)
            if resolved is None:
                record.update(response_type="undefined", status=404)
                await _respond(send, 404, "text/plain; charset=utf-8", b"not defined in scenario\n", record)
                return

            rt, delay_ms, content_type, body = resolved
            record.update(response_type=rt.name, status=rt.status, injected_delay_ms=delay_ms)

            if rt.status is None:
                # 応答を返さず、クライアントが切断するか delay_ms が経過するまで保持する
                record["outcome"] = await _hold_until_disconnect(receive, delay_ms / 1000)
                if record["outcome"] == "held_until_limit":
                    record["status"] = 504
                    await _respond(send, 504, "text/plain; charset=utf-8", b"held\n", record)
                return

            if delay_ms:
                await asyncio.sleep(delay_ms / 1000)
            await _respond(send, rt.status, content_type, body.encode("utf-8"), record)
        except OSError:
            record["outcome"] = "client_disconnected"
        finally:
            record["send_ts"] = time.time()
            self.log.write(record)


async def _respond(send, status: int, content_type: str, body: bytes, record: dict[str, Any]) -> None:
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [(b"content-type", content_type.encode()), (b"content-length", str(len(body)).encode())],
    })
    await send({"type": "http.response.body", "body": body})
    record["response_bytes"] = len(body)


async def _hold_until_disconnect(receive, limit_sec: float) -> str:
    async def wait_disconnect() -> None:
        while (await receive())["type"] != "http.disconnect":
            pass

    try:
        await asyncio.wait_for(wait_disconnect(), timeout=limit_sec)
        return "client_disconnected"
    except asyncio.TimeoutError:
        return "held_until_limit"


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark mock web server")
    parser.add_argument("--scenario", required=True, help="scenario YAML path")
    parser.add_argument("--run-label", required=True, help="e.g. A-distributed-x2-run1")
    parser.add_argument("--results-dir", default=str(REPO_ROOT / "benchmark" / "results"))
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=None, help="default: 443 with TLS, 80 without")
    parser.add_argument("--certfile", help="TLS certificate (see gen_cert.py)")
    parser.add_argument("--keyfile", help="TLS private key")
    args = parser.parse_args()

    try:
        scenario = load_scenario(args.scenario)
    except ScenarioError as exc:
        sys.exit(f"scenario error: {exc}")
    if scenario.scheme == "https" and not (args.certfile and args.keyfile):
        sys.exit("scenario scheme is https: --certfile and --keyfile are required")

    out_dir = Path(args.results_dir) / args.run_label
    log_path = out_dir / "access.jsonl"
    if log_path.exists() and log_path.stat().st_size > 0:
        sys.exit(f"{log_path} already exists; use a new --run-label")
    write_outputs(scenario, out_dir)
    log = AccessLog(log_path, args.run_label)

    port = args.port or (443 if args.certfile else 80)
    print(f"[MOCK] scenario={scenario.name} domains={len(scenario.domains)} "
          f"listen={args.host}:{port} log={log_path}", flush=True)
    uvicorn.run(
        MockApp(scenario, log),
        host=args.host,
        port=port,
        ssl_certfile=args.certfile,
        ssl_keyfile=args.keyfile,
        log_level="warning",
        access_log=False,
        lifespan="on",
        timeout_keep_alive=30,
    )


if __name__ == "__main__":
    main()
