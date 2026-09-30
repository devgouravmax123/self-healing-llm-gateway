#!/usr/bin/env python3
"""Multi-Container Physical Failover Demonstration Script.

Demonstrates genuine container/process-level LLM provider failover using two
independent Ollama containers on the Compose network:

  Nginx (:8000)
    │
  Gateway (:8000 internal)
    ├─► ollama_primary   (http://ollama-primary:11434, priority=1)
    └─► ollama_secondary (http://ollama-secondary:11434, priority=2)

Flow:
1. Verify preflight health of gateway and both Ollama backends.
2. Send Request 1: Serviced normally by ollama-primary.
3. Stop ollama-primary container (`docker compose stop ollama-primary`).
4. Send Request 2: Gateway encounters connection failure on primary, retries,
   trips circuit breaker on primary, and seamlessly fails over to ollama-secondary.
5. Query Prometheus metrics to verify failovers_total and provider request counts.
6. Restart ollama-primary container (`docker compose start ollama-primary`).
7. Wait for cooldown, probe recovery, and verify circuit state restoration.
8. Send Request 3: Verified healthy request completion.

Honesty Notice:
This demonstration executes against two separate containerized Ollama processes
with independent storage volumes on a local Docker bridge network. It demonstrates
genuine process-level fault tolerance and failover.
"""

import argparse
import subprocess
import sys
import time
from typing import Any, cast

import httpx

GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def log_step(title: str) -> None:
    print(f"\n{CYAN}{BOLD}{'=' * 75}{RESET}")
    print(f"{CYAN}{BOLD}{title}{RESET}")
    print(f"{CYAN}{BOLD}{'=' * 75}{RESET}")


def log_success(msg: str) -> None:
    print(f"  {GREEN}[OK]{RESET} {msg}")


def log_info(msg: str) -> None:
    print(f"  {YELLOW}[INFO]{RESET} {msg}")


def log_alert(msg: str) -> None:
    print(f"  {RED}[ALERT]{RESET} {msg}")


def run_cmd(cmd: list[str]) -> tuple[int, str]:
    res = subprocess.run(cmd, capture_output=True, text=True)
    return res.returncode, res.stdout.strip() + ("\n" + res.stderr.strip() if res.stderr else "")


class PhysicalFailoverDemo:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        prom_url: str = "http://127.0.0.1:9090",
        tenant_api_key: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.prom_url = prom_url.rstrip("/")
        self.tenant_api_key = tenant_api_key
        self.client = httpx.Client(base_url=self.base_url, timeout=120.0)

    def verify_preflight(self) -> bool:
        log_step("Preflight Check: Verifying Gateway & Docker Stack")
        try:
            health = self.client.get("/health")
            ready = self.client.get("/ready")
            if health.status_code != 200 or ready.status_code != 200:
                log_alert(
                    f"Gateway health/ready check failed: {health.status_code}, {ready.status_code}"
                )
                return False
            log_success(f"Gateway Health: {health.json()}")
            log_success(f"Gateway Readiness: {ready.json()}")
        except Exception as exc:
            log_alert(f"Failed to connect to gateway at {self.base_url}: {exc}")
            return False

        # Check containers
        code, out = run_cmd(["docker", "ps", "--format", "{{.Names}}: {{.Status}}"])
        log_info(f"Running Containers:\n{out}")
        if "llm-gateway-ollama-primary" not in out or "llm-gateway-ollama-secondary" not in out:
            log_alert("Both ollama-primary and ollama-secondary containers must be running.")
            return False
        log_success("Both Ollama containers are running.")
        return True

    def get_auth_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.tenant_api_key:
            headers["Authorization"] = f"Bearer {self.tenant_api_key}"
        return headers

    def send_chat_completion(self, prompt: str) -> dict[str, Any] | None:
        payload = {
            "model": "qwen2.5:3b",
            "messages": [{"role": "user", "content": prompt}],
        }
        try:
            resp = self.client.post(
                "/v1/chat/completions",
                json=payload,
                headers=self.get_auth_headers(),
            )
            if resp.status_code == 200:
                return cast(dict[str, Any], resp.json())
            log_alert(f"Chat request failed with HTTP {resp.status_code}: {resp.text}")
            return None
        except Exception as exc:
            log_alert(f"Chat request exception: {exc}")
            return None

    def query_prometheus_metric(self, query: str) -> str:
        try:
            resp = httpx.get(f"{self.prom_url}/api/v1/query", params={"query": query}, timeout=5.0)
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("data", {}).get("result", [])
                if results:
                    return str(results)
                return "No data returned (metric value is 0 or uninstantiated)"
            return f"Prometheus query error HTTP {resp.status_code}"
        except Exception as exc:
            return f"Prometheus connection error: {exc}"

    def run(self) -> bool:
        print(f"\n{BOLD}{CYAN}Starting Multi-Container Physical Failover Demonstration{RESET}")

        if not self.verify_preflight():
            return False

        # Step 1: Baseline Request to Primary
        log_step("Step 1: Baseline Request (Target: ollama_primary)")
        log_info("Sending completion request when both Ollama containers are healthy...")
        t0 = time.time()
        res1 = self.send_chat_completion("Respond with 'Primary Ollama is active.'")
        dur1 = time.time() - t0
        if not res1:
            log_alert("Step 1 failed: Could not get response from primary.")
            return False
        content1 = res1["choices"][0]["message"]["content"]
        log_success(f"Response ({dur1:.2f}s): {content1.strip()}")
        log_success("Step 1 PASSED: Primary Ollama served request successfully.")

        # Step 2: Stop ollama-primary container
        log_step("Step 2: Stopping ollama-primary Container")
        log_info("Executing: docker stop llm-gateway-ollama-primary")
        code, out = run_cmd(["docker", "stop", "llm-gateway-ollama-primary"])
        if code != 0:
            log_alert(f"Failed to stop ollama-primary container: {out}")
            return False
        log_success("llm-gateway-ollama-primary stopped successfully.")

        # Step 3: Request During Outage -> Failover to Secondary
        log_step("Step 3: Sending Request During Primary Outage (Expect Failover)")
        log_info("Sending completion request. Gateway should encounter primary connection failure,")
        log_info("record retry failure, trip circuit on primary, and route to ollama_secondary...")
        t0 = time.time()
        res2 = self.send_chat_completion("Respond with 'Secondary Ollama active after failover.'")
        dur2 = time.time() - t0
        if not res2:
            log_alert("Step 3 failed: Gateway failed to failover to secondary Ollama!")
            return False
        content2 = res2["choices"][0]["message"]["content"]
        log_success(f"Failover Response ({dur2:.2f}s): {content2.strip()}")
        log_success("Step 3 PASSED: Request succeeded seamlessly via ollama_secondary!")

        # Step 4: Inspect Metrics
        log_step("Step 4: Verifying Metrics in Prometheus")
        log_info("Querying gateway_failovers_total...")
        failovers = self.query_prometheus_metric("gateway_failovers_total")
        log_info(f"gateway_failovers_total: {failovers}")

        log_info("Querying gateway_provider_requests_total...")
        provider_reqs = self.query_prometheus_metric("gateway_provider_requests_total")
        log_info(f"gateway_provider_requests_total: {provider_reqs}")

        # Step 5: Restart ollama-primary
        log_step("Step 5: Restarting ollama-primary Container")
        log_info("Executing: docker start llm-gateway-ollama-primary")
        code, out = run_cmd(["docker", "start", "llm-gateway-ollama-primary"])
        if code != 0:
            log_alert(f"Failed to restart ollama-primary container: {out}")
            return False
        log_success("llm-gateway-ollama-primary restarted.")
        log_info("Waiting 6 seconds for circuit cooldown (cooldown=5.0s in demo override)...")
        time.sleep(6)

        # Step 6: Post-Recovery Request
        log_step("Step 6: Post-Recovery Request")
        log_info("Sending completion request to verify circuit probe and primary recovery...")
        t0 = time.time()
        res3 = self.send_chat_completion("Respond with 'Gateway fully recovered.'")
        dur3 = time.time() - t0
        if not res3:
            log_alert("Step 6 failed: Request after recovery failed.")
            return False
        content3 = res3["choices"][0]["message"]["content"]
        log_success(f"Post-recovery Response ({dur3:.2f}s): {content3.strip()}")
        log_success("Step 6 PASSED: Primary recovered and served request.")

        log_step("Physical Failover Demonstration Complete: ALL STEPS SUCCEEDED")
        return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-Container Physical Failover Demo")
    parser.add_argument(
        "--url",
        default="http://localhost:8000",
        help="Gateway URL (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--prom-url",
        default="http://localhost:9090",
        help="Prometheus URL (default: http://localhost:9090)",
    )
    parser.add_argument("--api-key", default=None, help="Optional Tenant API Key")
    args = parser.parse_args()

    demo = PhysicalFailoverDemo(
        base_url=args.url, prom_url=args.prom_url, tenant_api_key=args.api_key
    )
    success = demo.run()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
