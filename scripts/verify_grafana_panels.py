import json
import urllib.parse
import urllib.request

PROMETHEUS_URL = "http://localhost:9090"
LOKI_URL = "http://localhost:3100"
GRAFANA_URL = "http://localhost:13001"

dashboards = [
    "fsip-operations-overview",
    "fsip-service-health",
    "fsip-intelligence",
    "fsip-logs-incidents",
]


def query_prometheus(expr):
    try:
        url = f"{PROMETHEUS_URL}/api/v1/query?query=" + urllib.parse.quote(expr)
        req = urllib.request.urlopen(url, timeout=5)
        res = json.loads(req.read().decode())
        result = res.get("data", {}).get("result", [])
        return len(result) > 0, len(result), result[:1]
    except Exception as exc:
        return False, 0, str(exc)


def query_loki(expr):
    try:
        import time

        now_ns = int(time.time() * 1e9)
        start_ns = now_ns - int(3600 * 1e9)
        encoded_expr = urllib.parse.quote(expr)
        url = f"{LOKI_URL}/loki/api/v1/query_range?query={encoded_expr}&start={start_ns}&end={now_ns}&limit=5"
        req = urllib.request.urlopen(url, timeout=5)
        res = json.loads(req.read().decode())
        result = res.get("data", {}).get("result", [])
        return len(result) > 0, len(result), result[:1]
    except Exception as exc:
        return False, 0, str(exc)


print("=" * 80)
print("FSIP GRAFANA DASHBOARDS & PANEL DATA VERIFICATION")
print("=" * 80)

total_panels = 0
active_panels = 0

for uid in dashboards:
    url = f"{GRAFANA_URL}/api/dashboards/uid/{uid}"
    try:
        req = urllib.request.urlopen(url, timeout=5)
        dash = json.loads(req.read().decode())["dashboard"]
        title = dash.get("title")
        print(f"\n[Dashboard] {title} (UID: {uid})")
        panels = dash.get("panels", [])
        for p in panels:
            p_title = p.get("title", "Row / Container")
            targets = p.get("targets", [])
            if not targets:
                continue
            total_panels += 1
            has_data = False
            details = []
            for t in targets:
                expr = t.get("expr")
                if not expr:
                    continue
                # Determine datasource: Prometheus or Loki
                is_loki = (
                    "{" in expr
                    and ("service=" in expr or "job=" in expr or "filename=" in expr)
                    and not ("http_" in expr or "fsip_" in expr or "up" in expr)
                )
                if is_loki:
                    ok, count, sample = query_loki(expr)
                    ds = "Loki"
                else:
                    ok, count, sample = query_prometheus(expr)
                    ds = "Prometheus"
                if ok:
                    has_data = True
                details.append(f"{ds}: count={count}, expr='{expr[:45]}...'")
            if has_data:
                active_panels += 1
                status_str = "DATA ACTIVE"
            else:
                status_str = "EMPTY / PENDING"
            print(f"  - [{status_str}] {p_title} -> {'; '.join(details)}")
    except Exception as exc:
        print(f"Failed to fetch {uid}: {exc}")

print("\n" + "=" * 80)
print(f"SUMMARY: {active_panels}/{total_panels} dashboard panels actively receiving telemetry data.")
print("=" * 80)
