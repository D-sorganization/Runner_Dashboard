#!/usr/bin/env python3
"""
Benchmark for /api/health endpoint.
Uses locust to send 200 RPS and verifies that the p95 response time is < 200ms.
"""

import logging
import sys

try:
    import gevent
    from locust import HttpUser, between, task
    from locust.env import Environment
    from locust.log import setup_logging
    from locust.stats import stats_history, stats_printer
except ImportError:
    logging.error("locust is not installed. Please pip install locust to run this benchmark.")
    sys.exit(1)

setup_logging("INFO", None)


class HealthUser(HttpUser):
    # Short wait time to generate high load
    wait_time = between(0.01, 0.05)

    @task
    def check_health(self):
        self.client.get("/api/health")


def main():
    target_url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"

    env = Environment(user_classes=[HealthUser])
    env.host = target_url
    env.create_local_runner()

    # 200 users, spawn rate of 50 users per second
    env.runner.start(200, spawn_rate=50)

    # Run for 15 seconds to gather stable metrics
    gevent.spawn(stats_printer(env.stats))
    gevent.spawn(stats_history, env.runner)

    gevent.sleep(15)
    env.runner.quit()
    env.runner.greenlet.join()

    p95 = env.stats.total.get_response_time_percentile(0.95)
    total_reqs = env.stats.total.num_requests
    logging.info(f"Total requests: {total_reqs}")
    logging.info(f"P95 Response Time: {p95} ms")

    if total_reqs < 100:
        logging.error("FAIL: Not enough requests made.")
        sys.exit(1)

    if p95 is None or p95 > 200:
        logging.error(f"FAIL: p95 response time {p95}ms > 200ms")
        sys.exit(1)

    logging.info("PASS: p95 response time <= 200ms")
    sys.exit(0)


if __name__ == "__main__":
    main()
