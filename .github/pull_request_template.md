<!-- FSIP Pull Request Template (Phase 13 Governance) -->

## Summary of Changes
- **Phase / Component**: 
- **Summary**: 

## Verification & Testing
- [ ] Python unit & integration tests pass (`pytest`)
- [ ] Frontend unit tests pass (`npm test`)
- [ ] Playwright E2E tests pass (`npm run test:e2e`)
- [ ] Code formatting & linting clean (`ruff check libs services evaluation`)
- [ ] TypeScript type-check and Vite production build clean (`npm run build`)

## Simulator Contract & Topology
- [ ] Pinned external simulator image unchanged (`asifmahmoud414/bup-fuel-supply-simulator:1.0.0`)
- [ ] Simulator writes originate ONLY from authorized decision engine endpoints
- [ ] No regression against locked simulator contracts or schema

## Database & Migrations
- [ ] Database migrations backward-compatible
- [ ] No persistent volumes or data deleted (`docker compose down -v` forbidden)
- [ ] Transaction consistency preserved across buffered operations

## Security & Secrets
- [ ] No secrets or credentials committed in code, diffs, or container layers
- [ ] Security audits clean (`pip-audit`, `npm audit --audit-level=high`)
- [ ] Production compose configuration isolates internal database & cache ports

## Deployment & Rollback Impact
- [ ] Version and short commit SHA stamped in image build args and API status
- [ ] `deploy/versions.env` updated with explicit release tag
- [ ] Rollback strategy validated (`make rollback VERSION=...` or `scripts/deploy.sh`)

## Evidence & Documentation
- [ ] Relevant documentation and evidence reports updated in `docs/evidence/`
