# Fuel Supply Intelligence & Resilience Platform (FSIP)

Real-time, resilient, closed-loop fuel supply intelligence and dispatch optimization platform for the BUP CSE FEST 2026 Final Hackathon (organized with Poridhi.io).

## Services
- **engine**: Core event processing, state machine, and closed-loop control
- **intelligence**: Multi-step demand forecasting, risk projection, and LP dispatch optimization
- **api**: FastAPI gateway serving REST endpoints and SSE streams to the operator UI
- **frontend**: React 18 + TypeScript + Vite + Tailwind CSS operator dashboard
- **timescaledb**: TimescaleDB for time-series metrics, demand history, decisions, and audit events
- **redis**: Redis 7.4 for Pub/Sub and event streaming

## Getting Started

### Prerequisites
- Python 3.12+
- Node.js 20+ & npm 10+
- Docker & Docker Compose (optional for local component testing)

### Quick Start
```bash
cp .env.example .env
make venv
make test
```
