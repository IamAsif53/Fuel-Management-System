"""Optional Anthropic LLM adapter, hallucination guard, and grounded rules fallback."""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Any

from fsip_common.circuit import CircuitBreaker, CircuitOpenError
from fsip_common.explain.explainer import generate_decision_narrative
from fsip_common.logging import get_logger
from prometheus_client import REGISTRY, Counter, Histogram

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
    q_lower = question.lower()
    sources: list[dict[str, str]] = []

    # 1. Check for specific station queries e.g. "why is coxsbazar at risk"
    stations = context_json.get("stations", [])
    risks = context_json.get("risks", [])

    target_station = None
    for st in stations:
        st_id = st.get("id", "").lower()
        st_name = st.get("name", "").lower()
        base_id = st_id.replace("station-", "")
        if st_id in q_lower or (base_id and base_id in q_lower) or (st_name and st_name in q_lower):
            target_station = st
            break

    if target_station:
        st_id = target_station.get("id", "")
        sources.append({"type": "station", "id": st_id})

        # Match risk row
        matched_risk = None
        for r in risks:
            if r.get("station_id") == st_id:
                matched_risk = r
                break

        inv = target_station.get("inventory", {})
        total_inv = sum(inv.values()) if isinstance(inv, dict) else 0.0

        if matched_risk:
            lvl = matched_risk.get("level", "MEDIUM")
            h_so = matched_risk.get("hours_to_stockout", 0.0)
            p_so = matched_risk.get("stockout_prob_horizon", 0.0)
            fuel = matched_risk.get("fuel_type", "DIESEL")
            ans = (
                f"{st_id} is at {lvl} risk for {fuel}. Current total inventory is {int(total_inv):,} L with "
                f"{h_so:.1f} hours to stockout (projected stockout probability: {int(p_so * 100)}%). "
                f"Demand drivers include local consumption surge and transit lead times."
            )
            return ans, "rules", sources
        else:
            ans = f"{st_id} currently holds {int(total_inv):,} L inventory and is operating within normal parameters."
            return ans, "rules", sources

    # 2. Top risks inquiry
    if re.search(r"\b(risk|risks|danger|shortage|shortages|stockout|stockouts)\b", q_lower):
        if not risks:
            return "No critical stockout risks are currently detected across the network.", "rules", []
        top_risks = risks[:3]
        lines = []
        for r in top_risks:
            sid = r.get("station_id", "")
            f = r.get("fuel_type", "")
            lvl = r.get("level", "")
            h = r.get("hours_to_stockout", 0.0)
            sources.append({"type": "station", "id": sid})
            lines.append(f"{sid} ({f}): {lvl} risk, ~{h:.1f} h to stockout")
        ans = "Top at-risk stations in the network: " + "; ".join(lines) + "."
        return ans, "rules", sources

    # 3. Alerts inquiry
    if re.search(r"\b(alert|alerts|alarm|alarms)\b", q_lower):
        alerts = context_json.get("alerts", [])
        if not alerts:
            return "There are currently no open alerts.", "rules", []
        lines = []
        for a in alerts[:4]:
            aid = str(a.get("id", ""))
            sev = a.get("severity", "WARNING")
            title = a.get("title", "Alert")
            sources.append({"type": "alert", "id": aid})
            lines.append(f"[{sev}] #{aid}: {title}")
        ans = f"Active alerts ({len(alerts)} open): " + "; ".join(lines) + "."
        return ans, "rules", sources

    # 4. Decisions / recommendations inquiry
    decisions_pattern = (
        r"\b(decision|decisions|recommendation|recommendations|dispatch|dispatches|plan|plans|planning)\b"
    )
    if re.search(decisions_pattern, q_lower):
        decisions = context_json.get("decisions", [])
        if not decisions:
            return "No pending or recent dispatch decisions in the current planning cycle.", "rules", []
        latest = decisions[0]
        did = str(latest.get("id", ""))
        status = latest.get("status", "PENDING_APPROVAL")
        expl = latest.get("explanation", {})
        headline = expl.get("headline") if isinstance(expl, dict) else None
        if not headline:
            headline = f"Decision #{did} ({status})"
        sources.append({"type": "decision", "id": did})
        ans = f"Latest recommendation #{did} ({status}): {headline}."
        return ans, "rules", sources

    # 5. System status / health inquiry
    if re.search(r"\b(health|system|status|mode|engine)\b", q_lower):
        health = context_json.get("health", {})
        overall = health.get("overall", "Healthy")
        mode = health.get("engine_mode", "NORMAL")
        ans = f"System overall status is {overall}. Engine is operating in {mode} mode."
        return ans, "rules", []

    # 6. Shift summary inquiry
    if re.search(r"\b(summary|shift|overview|kpi|kpis)\b", q_lower):
        summary = context_json.get("state_summary", {})
        sl = summary.get("service_level", 1.0)
        unmet = summary.get("unmet_liters", 0.0)
        tick = summary.get("tick", 0)
        ans = (
            f"Shift snapshot at tick {tick}: Network service level is {sl * 100:.2f}% "
            f"with {int(unmet):,} L total unmet demand."
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
