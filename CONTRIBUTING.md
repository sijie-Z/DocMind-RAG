# Contributing to DocMind

🎉 Thank you for considering contributing! We welcome all contributions — bug reports, feature requests, documentation, and code.

## Code of Conduct

This project adheres to the [Contributor Covenant](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code.

## How to Contribute

### 1. Reporting Issues

- Use the [GitHub Issues](https://github.com/sijie-Z/DocMind-RAG/issues) tracker
- Search existing issues before filing a new one
- Include: reproduction steps, expected vs actual behavior, environment details (OS, Python/Node version, Docker version)

### 2. Feature Requests

- Clearly describe the problem you're solving
- Explain why it belongs in DocMind vs. a custom integration
- If proposing a new Agent tool, include the tool's trigger conditions and expected I/O

### 3. Code Contributions

#### Getting Started

```bash
# Clone your fork
git clone https://github.com/YOUR_USERNAME/DocMind-RAG.git
cd DocMind-RAG

# Set up backend
cd backend
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install -r requirements.txt

# Set up frontend
cd ../frontend
npm install
```

#### Development Workflow

```bash
# Backend (terminal 1)
cd backend
source venv/bin/activate
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Frontend (terminal 2)
cd frontend
npm run dev
```

#### Branch Strategy

- **main** — production-ready, protected
- **develop** — integration branch
- **feat/** — feature branches (e.g. `feat/multi-modal-search`)
- **fix/** — bug fixes (e.g. `fix/sse-reconnect`)
- **docs/** — documentation only

```bash
git checkout -b feat/your-feature-name
```

#### Coding Standards

**Backend (Python)**
- Type hints on all function signatures (`def foo(bar: str) -> int:`)
- Async-first: use `async/await` for I/O; sync only for CPU-bound work
- Docstrings: Google style for public APIs, single-line OK for private
- Error handling: raise `AppError` subclasses, never raw `Exception`
- Tests required for new functionality (pytest + pytest-asyncio)

**Frontend (TypeScript/Vue)**
- `<script setup lang="ts">` for all Vue components
- Strict TypeScript: no `any`, no `@ts-ignore` (use `@ts-expect-error` with comment)
- Composables for reusable logic, don't bloat components
- Tests: Vitest for stores, composables, utils

#### Commit Messages

```
<type>(<scope>): <description>

[optional body]
[optional footer]
```

Types: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `chore`, `ci`

Examples:
```
feat(auth): add OAuth2 Google login
fix(rag): handle empty document chunk edge case
docs(readme): add architecture diagram
```

#### Before Submitting

```bash
# Run tests
cd backend && python -m pytest
cd frontend && npm test

# Lint
cd frontend && npm run lint
```

### 4. Pull Request Process

1. Ensure all CI checks pass (tests, lint, type check, build)
2. Update documentation (README, API docs) if changing public interfaces
3. Add or update tests — coverage should not decrease
4. Request review from maintainers
5. Squash commits if the history is noisy

### 5. Project Structure

```
backend/
├── app/
│   ├── api/v1/endpoints/    # API route handlers
│   ├── agent/               # ReAct Agent system
│   ├── core/                # Infrastructure (DB, ES, Redis, MinIO, Kafka)
│   ├── models/              # SQLAlchemy models
│   ├── rag/                 # RAG pipeline components
│   ├── schemas/             # Pydantic request/response schemas
│   ├── services/            # Business logic layer
│   └── worker/              # Kafka document processor
└── tests/                   # Unit + integration tests

frontend/
├── src/
│   ├── api/                 # API client modules
│   ├── components/          # Reusable Vue components
│   ├── composables/         # Vue composables
│   ├── stores/              # Pinia stores
│   ├── utils/               # Utility functions
│   └── views/               # Page-level components
└── ...
```

### Questions?

Open a [Discussion](https://github.com/sijie-Z/DocMind-RAG/discussions) or tag a maintainer in an issue.

Thank you for making DocMind better! 🚀
