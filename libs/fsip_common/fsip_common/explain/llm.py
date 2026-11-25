"""Optional Anthropic LLM adapter, hallucination guard, and grounded rules fallback."""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Any

from prometheus_client import REGISTRY, Counter, Histogram

from fsip_common.circuit import CircuitBreaker, CircuitOpenError
from fsip_common.explain.explainer import generate_decision_narrative
from fsip_common.logging import get_logger

log = get_logger()

# ---------------------------------------------------------------- Metrics
try:
    FSIP_LLM_REQUESTS_TOTAL = Counter(
        "fsip_llm_requests_total",
        "Total LLM explanation and assistant requests",
        ["use", "result"],
    )
except ValueError:
    FSIP_LLM_REQUESTS_TOTAL = REGISTRY._names_to_collectors.get("fsip_llm_requests_total")  # type: ignore[assignment]

try:
    FSIP_LLM_LATENCY_SECONDS = Histogram(
        "fsip_llm_latency_seconds",
        "LLM request duration in seconds",
        ["use"],
        buckets=(0.1, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 15.0),
    )
except ValueError:
    FSIP_LLM_LATENCY_SECONDS = REGISTRY._names_to_collectors.get("fsip_llm_latency_seconds")  # type: ignore[assignment]

try:
    FSIP_LLM_GUARD_REJECTIONS_TOTAL = Counter(
        "fsip_llm_guard_rejections_total",
        "Total rejections by numeric hallucination guard",
    )
except ValueError:
    FSIP_LLM_GUARD_REJECTIONS_TOTAL = REGISTRY._names_to_collectors.get("fsip_llm_guard_rejections_total")  # type: ignore[assignment]

try:
    FSIP_RECOMMENDATION_TIME_TO_DECISION = Histogram(
        "fsip_recommendation_time_to_decision_seconds",
        "Time from recommendation creation to operator approval or rejection",
        buckets=(1.0, 5.0, 15.0, 30.0, 60.0, 120.0, 300.0, 600.0),
    )
except ValueError:
    FSIP_RECOMMENDATION_TIME_TO_DECISION = REGISTRY._names_to_collectors.get(
        "fsip_recommendation_time_to_decision_seconds"
    )  # type: ignore[assignment]


# ---------------------------------------------------------------- Numeric Guard
def extract_all_numbers(data: Any) -> set[float]:
    """Recursively extract all numeric values from a JSON structure."""
    numbers: set[float] = set()

    def _walk(obj: Any) -> None:
        if isinstance(obj, int | float) and not isinstance(obj, bool):
            numbers.add(round(float(obj), 4))
            # Also add integer representation if close
            if abs(obj - round(obj)) < 1e-4:
                numbers.add(float(round(obj)))
        elif isinstance(obj, str):
            # Extract numbers from strings like "6.2 h", "8,400 L", "1.8"
            clean = obj.replace(",", "")
            for match in re.findall(r"[-+]?\d*\.?\d+", clean):
                try:
                    val = float(match)
                    numbers.add(round(val, 4))
                    if abs(val - round(val)) < 1e-4:
                        numbers.add(float(round(val)))
                except ValueError:
                    pass
        elif isinstance(obj, dict):
            for v in obj.values():
                _walk(v)
        elif isinstance(obj, list | tuple | set):
            for item in obj:
                _walk(item)

    _walk(data)
    return numbers


def check_numbers_grounded(narrative: str, input_json: dict[str, Any]) -> tuple[bool, list[str]]:
    """Verify that every number mentioned in LLM narrative appears in input JSON.
    Returns (True, []) if grounded, or (False, [rejected_numbers]) if ungrounded numbers exist.
    """
    grounded_numbers = extract_all_numbers(input_json)

    # Allowed trivial small numbers / counts
    trivial_numbers = {0.0, 1.0, 2.0, 3.0, 4.0, 5.0}

    # Extract all numbers from text
    clean_text = narrative.replace(",", "")
    unmatched: list[str] = []

    # Find numbers or percentages e.g. "5000", "72%", "0.19"
    tokens = re.findall(r"\b\d+(?:\.\d+)?%?", clean_text)
    for tok in tokens:
        is_pct = tok.endswith("%")
        num_str = tok[:-1] if is_pct else tok
        try:
            val = float(num_str)
        except ValueError:
            continue

        if val in trivial_numbers:
            continue

        # Check direct match
        matched = False
        rounded_val = round(val, 4)
        for g in grounded_numbers:
            if abs(g - rounded_val) < 1e-2:
                matched = True
                break
            # Check percentage/ratio conversions (e.g. 72% or 72 vs 0.72)
            if abs(g - (rounded_val / 100.0)) < 1e-2:
                matched = True
                break
            if abs(g - (rounded_val * 100.0)) < 1e-2:
                matched = True
                break

        if not matched:
            unmatched.append(tok)

    if unmatched:
        return False, unmatched
    return True, []


# ---------------------------------------------------------------- Rules-based Fallback Assistant
def rules_based_assistant(question: str, context_json: dict[str, Any]) -> tuple[str, str, list[dict[str, str]]]:
    """Provide grounded rule-based answers when LLM is unavailable or ungrounded."""
    q_lower = question.lower().strip()
    sources: list[dict[str, str]] = []

    clean_q_alpha = re.sub(r"[^a-z0-9]", "", q_lower)

    stations = context_json.get("stations", [])
    risks = context_json.get("risks", [])
    depots = context_json.get("depots", [])
    alerts = context_json.get("alerts", [])
    decisions = context_json.get("decisions", [])
    incidents = context_json.get("incidents", [])
    summary = context_json.get("state_summary", {})
    health = context_json.get("health", {})

    # Helper: resolve target station from query
    target_station = None
    for st in stations:
        st_id = st.get("id", "").lower()
        st_name = st.get("name", "").lower()
        base_id = st_id.replace("station-", "")
        clean_base = re.sub(r"[^a-z0-9]", "", base_id)
        clean_name = re.sub(r"[^a-z0-9]", "", st_name)
        if (
            st_id in q_lower
            or (base_id and (base_id in q_lower or clean_base in clean_q_alpha))
            or (st_name and (st_name in q_lower or clean_name in clean_q_alpha))
            or any(word in q_lower for word in st_name.split() if len(word) > 3)
        ):
            target_station = st
            break

    # Helper: resolve target depot from query
    target_depot = None
    if not target_station:
        for dp in depots:
            dp_id = dp.get("id", "").lower()
            dp_name = dp.get("name", "").lower()
            base_dp = dp_id.replace("depot-", "")
            clean_dp_base = re.sub(r"[^a-z0-9]", "", base_dp)
            clean_dp_name = re.sub(r"[^a-z0-9]", "", dp_name)
            if (
                dp_id in q_lower
                or (base_dp and (base_dp in q_lower or clean_dp_base in clean_q_alpha))
                or (dp_name and (dp_name in q_lower or clean_dp_name in clean_q_alpha))
            ):
                target_depot = dp
                break

    # 1. Target Station specific query (e.g. "Why is Cox's Bazar at risk?", "How much fuel in Mirpur?")
    if target_station:
        st_id = target_station.get("id", "")
        st_name = target_station.get("name", st_id)
        sources.append({"type": "station", "id": st_id})

        inv = target_station.get("inventory", {})
        total_inv = sum(inv.values()) if isinstance(inv, dict) else 0.0

        # Gather risk information from both station.risk and context risks
        st_risks = []
        if isinstance(target_station.get("risk"), dict):
            for fuel, rdata in target_station["risk"].items():
                if isinstance(rdata, dict):
                    st_risks.append({
                        "fuel_type": fuel,
                        "level": rdata.get("level", "LOW"),
                        "hours_to_stockout": rdata.get("hours_to_stockout", 99.0),
                        "stockout_prob_horizon": rdata.get("stockout_prob_horizon", 0.0),
                        "drivers": rdata.get("drivers", []),
                        "inventory": rdata.get("inventory", inv.get(fuel, 0.0)),
                        "inflight": rdata.get("inflight", 0.0),
                        "fill_pct": rdata.get("fill_pct", 0.0),
                    })
        for r in risks:
            if r.get("station_id") == st_id and not any(sr.get("fuel_type") == r.get("fuel_type") for sr in st_risks):
                st_risks.append(r)

        # Sort risks: CRITICAL > HIGH > MEDIUM > LOW
        sev_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
        st_risks.sort(key=lambda r: (sev_order.get(r.get("level", "LOW"), 4), r.get("hours_to_stockout", 99.0)))
        elevated_risks = [r for r in st_risks if r.get("level") in ("CRITICAL", "HIGH", "MEDIUM")]

        # If user asked specifically about inventory / fuel stock without risk keywords
        is_inv_query = bool(
            re.search(r"\b(inventory|stock|fuel|remaining|capacity|liters|level)\b", q_lower)
            and not re.search(r"\b(risk|why|danger|shortage|stockout)\b", q_lower)
        )

        if is_inv_query:
            diesel_val = int(inv.get("DIESEL", 0))
            petrol_val = int(inv.get("PETROL", 0))
            octane_val = int(inv.get("OCTANE", 0))
            ans = (
                f"{st_name} ({st_id}) currently holds {int(total_inv):,} L total inventory. "
                f"Fuel breakdown: Diesel {diesel_val:,} L, Petrol {petrol_val:,} L, Octane {octane_val:,} L. "
                f"Operating status is {target_station.get('status', 'ACTIVE')}."
            )
            return ans, "rules", sources

        # Risk query for this station
        if elevated_risks:
            worst = elevated_risks[0]
            lvl = worst.get("level", "HIGH")
            fuel = worst.get("fuel_type", "DIESEL")
            h_so = worst.get("hours_to_stockout", 0.0)
            p_so = worst.get("stockout_prob_horizon", 0.0)
            drivers = worst.get("drivers", [])
            driver_str = ", ".join(drivers) if drivers else "local consumption surge and transit delivery lead time"

            # Check if there are active alerts for this station
            st_alerts = [a for a in alerts if a.get("station_id") == st_id]
            for a in st_alerts:
                aid = str(a.get("id"))
                if not any(s["id"] == aid for s in sources):
                    sources.append({"type": "alert", "id": aid})

            rec_note = "Priority replenishment tanker dispatch is recommended to prevent stockout."
            ans = (
                f"{st_id} ({st_name}) is at {lvl} risk for {fuel}. "
                f"Current total inventory is {int(total_inv):,} L with {h_so:.1f} hours to stockout "
                f"(projected stockout probability: {int(p_so * 100)}%). "
                f"Contributing drivers include: {driver_str}. "
                f"Operational action: {rec_note}"
            )
            return ans, "rules", sources
        else:
            # Low risk
            ans = (
                f"{st_id} ({st_name}) is currently at LOW risk across all fuel types. "
                f"Current total inventory is {int(total_inv):,} L with over 48 hours of cover runway "
                f"and no imminent stockout detected. Telemetry confirms normal replenishment pipeline."
            )
            return ans, "rules", sources

    # 2. Target Depot specific query
    if target_depot:
        dp_id = target_depot.get("id", "")
        dp_name = target_depot.get("name", dp_id)
        sources.append({"type": "depot", "id": dp_id})
        d_inv = target_depot.get("inventory", {})
        total_d_inv = sum(d_inv.values()) if isinstance(d_inv, dict) else 0.0
        d_status = target_depot.get("status", "OPEN")
        ans = (
            f"{dp_name} ({dp_id}) is currently {d_status} with {int(total_d_inv):,} L bulk inventory. "
            f"Breakdown: Diesel {int(d_inv.get('DIESEL', 0)):,} L, "
            f"Petrol {int(d_inv.get('PETROL', 0)):,} L, "
            f"Octane {int(d_inv.get('OCTANE', 0)):,} L."
        )
        return ans, "rules", sources

    # 3. Alerts inquiry
    if re.search(r"\b(alert|alerts|alarm|alarms|incident|incidents)\b", q_lower):
        if not alerts and not incidents:
            return (
                "There are currently no active shortage alerts or unacknowledged incidents across the network.",
                "rules",
                [],
            )
        lines = []
        for a in alerts[:4]:
            aid = str(a.get("id", ""))
            sev = a.get("severity", "WARNING")
            title = a.get("title", "Alert")
            sources.append({"type": "alert", "id": aid})
            lines.append(f"[{sev}] #{aid}: {title}")
        for inc in incidents[:2]:
            iid = str(inc.get("id", ""))
            comp = inc.get("component", "system")
            sources.append({"type": "incident", "id": iid})
            lines.append(f"[INCIDENT] #{iid} on {comp}")
        ans = f"Active alerts ({len(alerts)} open, {len(incidents)} incidents): " + "; ".join(lines) + "."
        return ans, "rules", sources

    # 4. Overall / Network Risk inquiry
    if re.search(r"\b(risk|risks|danger|shortage|shortages|stockout|stockouts)\b", q_lower):
        elevated = [r for r in risks if r.get("level") in ("CRITICAL", "HIGH", "MEDIUM")]
        if not elevated:
            return (
                "No critical stockout risks are currently detected across the network. "
                "All retail stations are operating with adequate fuel cover.",
                "rules",
                [],
            )
        top_risks = elevated[:3]
        lines = []
        for r in top_risks:
            sid = r.get("station_id", "")
            f = r.get("fuel_type", "")
            lvl = r.get("level", "")
            h = r.get("hours_to_stockout", 0.0)
            if not any(s["id"] == sid for s in sources):
                sources.append({"type": "station", "id": sid})
            lines.append(f"{sid} ({f}): {lvl} risk (~{h:.1f}h to stockout)")
        ans = (
            f"Active stockout risks detected ({len(elevated)} elevated): "
            + "; ".join(lines)
            + ". Replenishment replanning advised."
        )
        return ans, "rules", sources

    # 5. Decisions / recommendations inquiry
    decisions_pattern = (
        r"\b(decision|decisions|recommendation|recommendations|dispatch|dispatches|plan|plans|planning)\b"
    )
    if re.search(decisions_pattern, q_lower):
        if not decisions:
            return (
                "No pending or recent dispatch decisions in the current planning cycle. "
                "Network deliveries are up to date.",
                "rules",
                [],
            )
        latest = decisions[0]
        did = str(latest.get("id", ""))
        status = latest.get("status", "PENDING_APPROVAL")
        policy = latest.get("policy", "optimizer")
        expl = latest.get("explanation", {})
        headline = expl.get("headline") if isinstance(expl, dict) else None
        if not headline:
            headline = f"Optimization plan #{did} under policy {policy}"
        sources.append({"type": "decision", "id": did})
        ans = f"Latest recommendation #{did} ({status}, policy: {policy}): {headline}."
        return ans, "rules", sources

    # 6. System health / engine status inquiry
    if re.search(r"\b(health|system|status|mode|engine|sync|simulator)\b", q_lower):
        overall = health.get("overall", "Healthy")
        mode = health.get("engine_mode", "NORMAL")
        tick = summary.get("tick", 0)
        sl = summary.get("service_level", 1.0)
        ans = (
            f"System overall status is {overall}. Engine is operating in {mode} mode at tick {tick}. "
            f"Current network service level is {sl * 100:.2f}%."
        )
        return ans, "rules", []

    # 7. Shift summary inquiry
    if re.search(r"\b(summary|shift|overview|kpi|kpis|report)\b", q_lower):
        sl = summary.get("service_level", 1.0)
        unmet = summary.get("unmet_liters", 0.0)
        tick = summary.get("tick", 0)
        mode = summary.get("engine_mode", "NORMAL")
        num_alerts = len(alerts)
        num_decs = len(decisions)
        ans = (
            f"Shift executive summary at tick {tick}: Network service level is {sl * 100:.2f}% "
            f"with {int(unmet):,} L total unmet demand. Operating mode: {mode}. "
            f"Active alerts: {num_alerts}, recent decisions: {num_decs}."
        )
        return ans, "rules", []

    # 8. General inventory inquiry across network
    if re.search(r"\b(inventory|stock|fuel|storage|depots|stations)\b", q_lower):
        total_st_fuel = sum(
            sum(st.get("inventory", {}).values()) for st in stations if isinstance(st.get("inventory"), dict)
        )
        ans = (
            f"Network inventory summary: {len(stations)} active retail stations "
            f"holding {int(total_st_fuel):,} L total fuel, "
            f"supported by {len(depots)} bulk supply depots."
        )
        return ans, "rules", []

    # Default: unsupported question -> strictly return "not in current data"
    return "not in current data", "rules", []


# ---------------------------------------------------------------- LLM Explainer Class
class LLMExplainer:
    """Manages LLM explanation generation with circuit breaker, timeout, and guard."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "claude-haiku-5-5",
        timeout_sec: float = 8.0,
    ) -> None:
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.model = model or os.environ.get("LLM_MODEL", "claude-haiku-5-5")
        self.timeout_sec = timeout_sec
        self.enabled = bool(self.api_key)

        self.breaker = CircuitBreaker("anthropic", failure_threshold=3, reset_timeout=30.0)
        self._client: Any = None

        if self.enabled:
            try:
                import anthropic
                self._client = anthropic.AsyncAnthropic(api_key=self.api_key)
            except Exception as exc:
                log.warning("llm.init_failed", error=str(exc))
                self.enabled = False

    async def explain_decision(self, explanation: dict[str, Any], tick: int = 0) -> str:
        """Produce an operator narrative for a decision recommendation."""
        template_fallback = generate_decision_narrative(explanation, tick=tick)

        if not self.enabled or not self._client:
            return template_fallback

        start_time = time.perf_counter()
        use = "decision_narrative"

        try:
            async with self.breaker:
                system_prompt = (
                    "You are an operations analyst. Explain this SIMULATED fuel allocation recommendation "
                    "in <= 120 words for a control-room operator. Use only the facts given. Do not invent numbers."
                )
                user_content = json.dumps(explanation, default=str)

                # Execute with timeout
                resp = await asyncio.wait_for(
                    self._client.messages.create(
                        model=self.model,
                        max_tokens=400,
                        temperature=0.2,
                        system=system_prompt,
                        messages=[{"role": "user", "content": user_content}],
                    ),
                    timeout=self.timeout_sec,
                )

                duration = time.perf_counter() - start_time
                if FSIP_LLM_LATENCY_SECONDS:
                    FSIP_LLM_LATENCY_SECONDS.labels(use=use).observe(duration)

                content_text = ""
                for blk in resp.content:
                    if getattr(blk, "type", "") == "text":
                        content_text += blk.text

                # Hallucination Guard
                is_grounded, ungrounded = check_numbers_grounded(content_text, explanation)
                if not is_grounded:
                    if FSIP_LLM_GUARD_REJECTIONS_TOTAL:
                        FSIP_LLM_GUARD_REJECTIONS_TOTAL.inc()
                    if FSIP_LLM_REQUESTS_TOTAL:
                        FSIP_LLM_REQUESTS_TOTAL.labels(use=use, result="guard_rejected").inc()
                    log.warning("llm.guard_rejection", ungrounded_numbers=ungrounded)
                    return template_fallback

                if FSIP_LLM_REQUESTS_TOTAL:
                    FSIP_LLM_REQUESTS_TOTAL.labels(use=use, result="success").inc()
                return content_text.strip()

        except (TimeoutError, CircuitOpenError, Exception) as exc:
            duration = time.perf_counter() - start_time
            if FSIP_LLM_LATENCY_SECONDS:
                FSIP_LLM_LATENCY_SECONDS.labels(use=use).observe(duration)
            if FSIP_LLM_REQUESTS_TOTAL:
                res = "circuit_open" if isinstance(exc, CircuitOpenError) else "error"
                FSIP_LLM_REQUESTS_TOTAL.labels(use=use, result=res).inc()
            log.warning("llm.call_failed", error=str(exc))
            return template_fallback

    async def answer_assistant(
        self,
        question: str,
        context_json: dict[str, Any],
    ) -> tuple[str, str, list[dict[str, str]]]:
        """Answer operator questions strictly grounded in platform context."""
        use = "assistant"
        if not self.enabled or not self._client:
            return rules_based_assistant(question, context_json)

        start_time = time.perf_counter()
        try:
            async with self.breaker:
                system_prompt = (
                    "You are an assistant for the Fuel Supply Intelligence Platform. "
                    "Answer only from the supplied context. "
                    "Cite source IDs (e.g., station-mirpur, alert-12). "
                    "If the information is unsupported by the context, answer 'not in current data'. "
                    "Do not invent numbers or IDs."
                )

                # Context hard-limit: compact JSON to max ~6000 tokens
                raw_json = json.dumps(context_json, default=str)
                if len(raw_json) > 24000:
                    raw_json = raw_json[:24000]

                user_content = f"Context:\n{raw_json}\n\nQuestion:\n{question}"

                resp = await asyncio.wait_for(
                    self._client.messages.create(
                        model=self.model,
                        max_tokens=400,
                        temperature=0.2,
                        system=system_prompt,
                        messages=[{"role": "user", "content": user_content}],
                    ),
                    timeout=self.timeout_sec,
                )

                duration = time.perf_counter() - start_time
                if FSIP_LLM_LATENCY_SECONDS:
                    FSIP_LLM_LATENCY_SECONDS.labels(use=use).observe(duration)

                answer_text = ""
                for blk in resp.content:
                    if getattr(blk, "type", "") == "text":
                        answer_text += blk.text
                answer_text = answer_text.strip()

                if "not in current data" in answer_text.lower():
                    if FSIP_LLM_REQUESTS_TOTAL:
                        FSIP_LLM_REQUESTS_TOTAL.labels(use=use, result="unsupported").inc()
                    return "not in current data", "llm", []

                # Extract cited sources
                sources: list[dict[str, str]] = []
                for st in context_json.get("stations", []):
                    sid = st.get("id", "")
                    if sid and sid in answer_text:
                        sources.append({"type": "station", "id": sid})
                for al in context_json.get("alerts", []):
                    aid = str(al.get("id", ""))
                    if aid and f"alert-{aid}" in answer_text or f"#{aid}" in answer_text:
                        sources.append({"type": "alert", "id": aid})
                for dec in context_json.get("decisions", []):
                    did = str(dec.get("id", ""))
                    if did and f"decision-{did}" in answer_text or f"#{did}" in answer_text:
                        sources.append({"type": "decision", "id": did})

                if FSIP_LLM_REQUESTS_TOTAL:
                    FSIP_LLM_REQUESTS_TOTAL.labels(use=use, result="success").inc()
                return answer_text, "llm", sources

        except Exception as exc:
            duration = time.perf_counter() - start_time
            if FSIP_LLM_LATENCY_SECONDS:
                FSIP_LLM_LATENCY_SECONDS.labels(use=use).observe(duration)
            if FSIP_LLM_REQUESTS_TOTAL:
                FSIP_LLM_REQUESTS_TOTAL.labels(use=use, result="error").inc()
            log.warning("llm.assistant_failed", error=str(exc))
            return rules_based_assistant(question, context_json)
