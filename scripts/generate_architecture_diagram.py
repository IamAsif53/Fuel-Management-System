"""Generate high-resolution architecture diagram for FSIP platform."""
import os
import matplotlib.pyplot as plt
import matplotlib.patches as patches

def generate_diagram(output_path: str):
    fig, ax = plt.subplots(figsize=(16, 10), dpi=300)
    ax.set_xlim(0, 16)
    ax.set_ylim(0, 10)
    ax.axis("off")

    # Background color
    fig.patch.set_facecolor("#0f172a") # Slate-900
    ax.set_facecolor("#0f172a")

    # Title
    ax.text(8, 9.6, "FSIP: Fuel Supply Intelligence & Resilience Platform", 
            fontsize=20, fontweight="bold", color="#f8fafc", ha="center")
    ax.text(8, 9.2, "Complete System Topology & Service Interconnects (v1.0.0)", 
            fontsize=12, color="#94a3b8", ha="center")

    def draw_box(x, y, w, h, title, subtitle, color, text_color="#ffffff", badge=""):
        rect = patches.FancyBboxPatch(
            (x, y), w, h,
            boxstyle="round,pad=0.1,rounding_size=0.15",
            facecolor=color,
            edgecolor="#334155",
            linewidth=1.5,
            alpha=0.95
        )
        ax.add_patch(rect)
        if badge:
            ax.text(x + w - 0.1, y + h - 0.15, badge, fontsize=8, color="#38bdf8",
                    fontweight="bold", ha="right", va="top")
        ax.text(x + w/2, y + h*0.62, title, fontsize=11, fontweight="bold",
                color=text_color, ha="center", va="center")
        ax.text(x + w/2, y + h*0.35, subtitle, fontsize=8, color="#cbd5e1",
                ha="center", va="center")

    # Top: Physical Layer / Simulator
    draw_box(5.5, 7.8, 5, 1.0, "Physical Simulator (BUP)", 
             "External IoT Telemetry | 10s Ticks | 10 Stations / 2 Depots", 
             "#1e293b", badge="PORT: 8000")

    # Middle Left: Intelligence & Analytics
    draw_box(0.8, 5.0, 3.8, 1.4, "Intelligence Service", 
             "Multi-Horizon Forecasts (LightGBM/ARIMA)\nConformal Intervals | Page-Hinkley Drift", 
             "#1e3a8a", badge="PORT: 8200")

    # Middle Center: Core Engine
    draw_box(5.3, 4.7, 5.4, 2.0, "Core Optimization Engine", 
             "Tick Ingestion | F-RECON Transit Reconciliation\nFSM (NORMAL, DEGRADED, SAFE_HOLD)\nMILP Solver (PuLP/CBC) | Scarcity Rationing", 
             "#047857", badge="PORT: 8100")

    # Middle Right: Gateway API
    draw_box(11.4, 5.0, 3.8, 1.4, "Gateway API (FastAPI)", 
             "REST APIs | SSE Stream (/api/stream)\nRate Limiting | Cognitive Forcing", 
             "#0369a1", badge="PORT: 18080")

    # Data Store Tier (Below Middle)
    draw_box(2.0, 2.8, 5.5, 1.2, "TimescaleDB (Hypertable Ledger)", 
             "world_snapshots | sim_runs | decisions | allocations\nAudit Trail & Incident Records", 
             "#312e81", badge="PORT: 15432")

    draw_box(8.5, 2.8, 5.5, 1.2, "Redis 7 (Message Bus & Mutex)", 
             "fsip:domain-events Stream | fsip:state Cache\nfsip:allocations:lock Distributed Mutex", 
             "#831843", badge="PORT: 16379")

    # Presentation & Supervision Tier (Bottom)
    draw_box(0.8, 0.6, 4.4, 1.4, "Operations Console (UI)", 
             "React 18 + Vite + Tailwind\nNetwork Map | Risk Heatmap | Decision Ledger\nTime-Travel Historical Replay", 
             "#4c1d95", badge="PORT: 3000")

    draw_box(5.8, 0.6, 4.4, 1.4, "Observability & Telemetry", 
             "Grafana Dashboards (13001)\nPrometheus SLO Engine (9090)\nAlertmanager (9093) | Loki (3100)", 
             "#701a75", badge="STACK")

    draw_box(10.8, 0.6, 4.4, 1.4, "Resilience & Break-Glass", 
             "Standalone Break-Glass Console\nZero-Dependency Direct Simulator Dispatch\nF1-F18 Chaos Defense", 
             "#854d0e", badge="FALLBACK")

    # Connectors & Arrows
    def draw_arrow(x1, y1, x2, y2, label="", color="#38bdf8", rad=0.0):
        ax.annotate(
            "", xy=(x2, y2), xytext=(x1, y1),
            arrowprops=dict(
                arrowstyle="->", color=color, lw=2,
                connectionstyle=f"arc3,rad={rad}"
            )
        )
        if label:
            mx, my = (x1 + x2)/2, (y1 + y2)/2
            ax.text(mx, my + 0.1, label, fontsize=8, color="#94a3b8",
                    fontweight="bold", ha="center", va="center",
                    bbox=dict(boxstyle="round,pad=0.2", facecolor="#0f172a", edgecolor="#334155"))

    # Sim -> Engine
    draw_arrow(8.0, 7.8, 8.0, 6.7, "GET /v1/state & POST /v1/allocations", "#38bdf8")

    # Engine <-> Intelligence
    draw_arrow(5.3, 5.7, 4.6, 5.7, "Batch Forecast", "#a855f7")

    # Engine <-> TimescaleDB
    draw_arrow(6.5, 4.7, 5.0, 4.0, "Persist Snapshots", "#6366f1")

    # Engine <-> Redis
    draw_arrow(9.5, 4.7, 10.5, 4.0, "Publish Events", "#ec4899")

    # Redis -> API
    draw_arrow(12.0, 4.0, 13.0, 5.0, "Event Stream", "#ec4899")

    # API -> Frontend
    draw_arrow(13.0, 5.0, 3.5, 2.0, "SSE / State Polling", "#38bdf8", rad=-0.2)

    # API -> Simulator (Emergency / Approval Dispatch)
    draw_arrow(13.3, 6.4, 10.5, 8.2, "Proxy Allocations", "#f59e0b", rad=0.15)

    # Observability Scrapes
    draw_arrow(8.0, 2.0, 8.0, 4.7, "Prometheus Metrics", "#10b981")

    plt.tight_layout()
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    plt.savefig(output_path, dpi=300, facecolor=fig.get_facecolor(), edgecolor="none")
    plt.close()
    print(f"Diagram successfully saved to {output_path}")

if __name__ == "__main__":
    generate_diagram("f:/NewTask/fuel-platform/docs/architecture.png")
