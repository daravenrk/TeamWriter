# Development Next Steps & Process Guide

**Last Updated:** March 17, 2026  
**Project:** Dragonlair Agent Stack — Multi-agent orchestration for book writing and code generation  
**Current Focus:** Publisher output failure investigation in book-flow pipeline

---

## 1. Project Overview

Dragonlair is a distributed multi-agent system that orchestrates specialized Ollama LLM models to collaboratively write books and generate code. The system consists of:

- **Backend**: FastAPI REST orchestrator (`agent_stack/api_server.py`) managing task queue, resource tracking, and agent health
- **Frontend**: Vanilla JavaScript SPA (`agent_stack/static/index.html`) with real-time task monitoring and spawn queue controls
- **Agent Profiles**: Specialized roles defined in `agent_stack/agent_profiles/` (e.g., book-publisher, book-researcher, book-architect, book-chapter-planner, book-writer, etc.)
- **Orchestration**: Book-flow pipeline (`agent_stack/book_flow.py`) coordinating multi-stage handoffs between agents
- **Infrastructure**: Docker Compose deployment with Ollama containers for AMD and NVIDIA routes
- **Scheduling**: Cron-based reconciliation every 5 minutes (`agent_stack/trigger_next_job.sh`)

---

## 2. System Architecture

### Core Components

| Component | Purpose | Status |
|-----------|---------|--------|
| `api_server.py` | FastAPI REST API for task queuing, agent coordination, spawn controls, UI state | ✅ Fully functional with crontab timer |
| `book_flow.py` | Entry point for book pipeline orchestration | 🔴 Publisher stage returns empty outputs |
| `orchestrator.py` | Multi-agent task sequencing and handoff management | ✅ Working (diagnostics added) |
| `static/index.html` | WebUI with crontab schedule visibility, spawn queue controls | ✅ Fully implemented |
| `agent_profiles/*.agent.md` | 15+ specialized agent role definitions with prompts | ✅ Available |
| `docker-compose.agent.yml` | Ollama service orchestration (AMD + NVIDIA routes) | ✅ Running |

### Task Lifecycle

```
queued → (10s pre-spawn delay) → running → completed/failed/cancelled
```

- **Pre-spawn delay**: 10 seconds before task moves to running state (configurable via spawn control)
- **Spawn controls**: Real-time Go/Pause/Stop actions on queued tasks
- **Agent routes**: `ollama_amd` (1 concurrent, qwen3.5:27b) and `ollama_nvidia` (3 concurrent, qwen3.5:4b)

---

## 3. Current Status

### ✅ Completed Work (23 items)

- Book/code flow infrastructure, CLI setup, output saving
- API endpoints for publisher, researcher, architect, chapter-planner
- Web UI structure with task display, refresh logic, real-time status
- Crontab timer calculation and display (shows next scheduled run)
- Pre-spawn delay (10s pause before task execution)
- Spawn queue interdiction controls (Go/Pause/Stop buttons work correctly)
- Docker deployment and validation

### 🔴 Current Blocker: Empty Publisher Outputs

**Symptom**: Book-flow test initiates but publisher-brief stage returns empty/null outputs, preventing researcher handoff.

**Last Investigation**: 
- Publisher agent is being invoked successfully
- API responses indicate task completion but with empty content
- Likely causes: LLM non-response, orchestrator plan generation failure, or malformed handoff payload

**Files affected**: 
- `agent_stack/orchestrator.py` (task execution and output capture)
- `agent_stack/agent_profiles/book-publisher.agent.md` (agent prompt/role)
- `agent_stack/book_flow.py` (handoff logic)

### ⏳ Partially Complete (1 item)

- **Todo 25**: Investigate empty publisher outputs — Root cause analysis in progress

---

## 4. Critical Debugging Notes

### From User Memory (Patterns & Solutions)

1. **Docker Code Caching**: Code changes require `--build` flag on docker compose:
   ```bash
   docker compose -f agent_stack/docker-compose.agent.yml up -d --build
   ```

2. **Ollama CLI Access**: The host may not have `ollama` CLI installed; use:
   ```bash
   docker exec dragonlair_agent_stack ollama run [model-name]
   ```

3. **Lock Manager Pattern**: All agent changes are logged to shared changes log with producer lock:
   - Agents must acquire lock before editing
   - Publisher verifies lock status before marking tasks complete
   - Review `agent_stack/lock_manager.py` for implementation

4. **Mode-based Workflow**: Web UI uses mode selection (Book/Code) instead of profile dropdown
   - User enters prompt and presses Go
   - Backend routes to appropriate agent stack

---

## 5. Next Development Steps (Prioritized)

### 🔴 **URGENT: Publisher Failure Investigation (Blocker)**

**Todo 25**: Investigate empty publisher outputs

**Steps**:
1. **Run diagnostic test**:
   ```bash
   cd /home/daravenrk/dragonlair
   python3 -m agent_stack.book_flow \
     --title "Test Book" \
     --premise "A test of the pipeline" \
     --chapter_number 1 \
     --chapter_count 3 \
     --output_dir ./book_project \
     --api_base http://127.0.0.1:11888
   ```

2. **Capture failure point**:
   - Check `book_project/changes.log` for stage_failure event in publisher-brief
   - Look for empty output or error messages

3. **Debug handoff directory**:
   ```bash
   ls -la book_project/04_drafts/brief_* 2>/dev/null || echo "No brief outputs"
   cat book_project/changes.log | grep -A 5 "stage_failure"
   ```

4. **Verify publisher model availability**:
   ```bash
   docker exec dragonlair_agent_stack ollama list | grep qwen
   curl http://127.0.0.1:11888/api/health
   ```

5. **Check orchestrator output capture** (in `orchestrator.py`):
   - Verify `_capture_output()` is properly extracting LLM response
   - Add debug logging: `logger.debug(f"Publisher output: {output_data}")`

---

### 📋 **HIGH PRIORITY: Publisher-Specific Fixes (Post-investigation)**

After identifying root cause, apply one of:

- **Todo 30**: Verify publisher model availability
  - Ensure `ollama_amd` route has qwen3.5:27b pulled and ready
  - Check resource constraints (memory, VRAM)

- **Todo 31**: Add upstream payload diagnostics
  - Inject debug output at orchestrator invocation point
  - Log input payload passed to publisher agent

- **Todo 32**: Retry empty-response handling
  - Implement retry logic with exponential backoff
  - Add fallback template for empty responses

---

### 🟡 **MEDIUM PRIORITY: Infrastructure Validation (Post-publisher fix)**

These should be completed once publisher outputs successfully:

- **Todo 26**: Clean duplicate orchestrator initialization
  - Review `orchestrator.py` for redundant init patterns
  - Consolidate into single initialization path

- **Todo 27**: Add checkpoint save agent
  - Implement agent for persisting book state between stages
  - Enable pause/resume on long-running pipelines

- **Todo 28**: Add failure-path integration tests
  - Test retry logic, fallback handling, graceful degradation
  - Validate task cancellation flows

- **Todo 29**: Expose logging metrics in UI
  - Add metrics card showing agent health, queue depth, error rates
  - Integrate prometheus or similar metrics export

- **Todo 33**: Validate FastAPI runtime environment
  - Test under load (multiple concurrent tasks)
  - Verify resource limits and error handling

---

## 6. How to Test & Validate

### Quick Health Check

```bash
# 1. Verify API is running
curl http://127.0.0.1:11888/api/status | python3 -m json.tool | head -20

# 2. Check agent health
curl http://127.0.0.1:11888/api/status | grep -A 2 "health"

# 3. Verify crontab schedule
curl http://127.0.0.1:11888/api/ui-state | grep "cron"

# 4. Check task queue
curl http://127.0.0.1:11888/api/status | grep -E "queued|running|completed|failed"
```

### Running Book-Flow Test

```bash
# Minimal test arguments (required)
python3 -m agent_stack.book_flow \
  --title "Test Flow" \
  --premise "Testing the pipeline" \
  --chapter-title "Chapter One" \
  --section-title "Opening" \
  --section-goal "Introduce setting"

# Full test with output directory
python3 -m agent_stack.book_flow \
  --title "Test Book" \
  --premise "A comprehensive test" \
  --chapter_number 1 \
  --chapter_count 3 \
  --output_dir ./book_project \
  --api_base http://127.0.0.1:11888
```

### Monitoring Task Execution

```bash
# Watch changes log in real-time
tail -f book_project/changes.log

# Check specific stage outputs
cat book_project/04_drafts/brief_*.md 2>/dev/null

# View task queue state
curl http://127.0.0.1:11888/api/ui-state | python3 -m json.tool
```

---

## 7. Key Files Reference

| File | Purpose | Priority |
|------|---------|----------|
| `agent_stack/api_server.py` | FastAPI REST orchestrator, task queue, UI state | Core |
| `agent_stack/book_flow.py` | Book pipeline entry point, multi-stage orchestration | Core |
| `agent_stack/orchestrator.py` | Agent task execution, output capture, handoff | Core |
| `agent_stack/static/index.html` | WebUI, crontab timer, spawn controls | UI |
| `agent_stack/agent_profiles/book-publisher.agent.md` | Publisher role definition (CURRENT BLOCKER) | 🔴 |
| `agent_stack/lock_manager.py` | Resource serialization, producer locks | Infrastructure |
| `agent_stack/docker-compose.agent.yml` | Ollama service definition | Deployment |
| `agent_stack/trigger_next_job.sh` | Cron-triggered reconciliation | Automation |
| `book_project/changes.log` | Event log for all task stages | Debugging |

---

## 8. Common Debugging Patterns

### Issue: Code Changes Not Reflected

**Symptom**: Modified `api_server.py` but API still returns old responses

**Solution**:
```bash
docker compose -f agent_stack/docker-compose.agent.yml down
docker compose -f agent_stack/docker-compose.agent.yml up -d --build
```

### Issue: Publisher Returns Empty Output

**Symptom**: Book-flow test fails at publisher-brief stage with empty output

**Debug Steps**:
1. Check if `ollama_amd` route is healthy: `curl http://127.0.0.1:11888/api/status | grep -A 3 "ollama_amd"`
2. Verify model availability: `docker exec dragonlair_agent_stack ollama list | grep qwen3.5:27`
3. Inspect changes.log: `cat book_project/changes.log | grep -E "publisher|stage_failure"`
4. Add debug logging in orchestrator output capture (see Todo 31)

### Issue: Spawn Control Buttons Not Working

**Symptom**: Clicking Go/Pause/Stop buttons in WebUI has no effect

**Debug Steps**:
1. Check browser console for fetch errors (F12 → Console)
2. Verify API endpoint: `curl -X POST http://127.0.0.1:11888/api/tasks/[task-id]/spawn-control`
3. Confirm task ID exists: `curl http://127.0.0.1:11888/api/status | grep task_id`
4. Check API logs: `docker logs dragonlair_agent_stack | tail -50`

---

## 9. Development Workflow

### Standard Development Loop

1. **Identify issue** from todo list or bug report
2. **Read/understand** relevant code files
3. **Make changes** to source files (not in Docker container)
4. **Test locally** if possible (Python syntax check, curl API calls)
5. **Rebuild Docker**: `docker compose up -d --build`
6. **Validate** with end-to-end test (book-flow, code-flow, or manual API test)
7. **Mark todo complete** when validated

### Code Change Patterns

- **API endpoints**: Edit `agent_stack/api_server.py`, rebuild, test with curl
- **WebUI/UI**: Edit `agent_stack/static/index.html`, rebuild, refresh browser
- **Agent roles**: Edit `agent_stack/agent_profiles/*.agent.md`, rebuilding not needed (live-reload via volume mount)
- **Orchestration logic**: Edit `agent_stack/orchestrator.py` or `agent_stack/book_flow.py`, rebuild, run integration test

---

## 10. Quick Reference: Current System State

- **API Running**: http://127.0.0.1:11888 ✅
- **Frontend**: http://127.0.0.1:11888/static/ ✅
- **Agent Routes**: ollama_amd (1 slot), ollama_nvidia (3 slots) ✅
- **Task Queue**: Empty (0 queued, 0 running) ✅
- **Crontab Timer**: Working, next run at 19:35:00 UTC ✅
- **Last Test**: Book-flow initiated, awaiting output analysis 🔄

---

## 11. Resources & Documentation

- Full project plan: `AGENT_PLAN.md`
- System architecture: `SYSTEM_NOTES_AND_AUTONOMY.md`
- Usage guide for end-users: `USER_GUIDE.md`
- Bare-metal recovery: `BARE_METAL_RECOVERY.md`
- Agent profiles: `agent_stack/agent_profiles/` (15+ specialized roles)

---

## Next Action

**Immediate**: Complete publisher output investigation (Todo 25)
1. Run book-flow diagnostic test
2. Capture failure point from changes.log
3. Apply appropriate fix (Todo 30/31/32)
4. Re-validate with successful end-to-end test

**Then**: Complete remaining infrastructure validation (Todos 26-29, 33)

---

*This guide should be your companion while developing. Update it as patterns emerge or new issues are discovered.*
