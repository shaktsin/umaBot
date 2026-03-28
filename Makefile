.PHONY: help install dev upgrade init run start stop restart status reload logs ps \
        panel panel-build panel-dev \
        skills skill-add skill-rm \
        test lint format check shell gateway \
        config edit \
        db db-backup db-reset \
        reset clean \
        build publish-test publish \
        info doctor

# ---------------------------------------------------------------------------
# Variables
# ---------------------------------------------------------------------------

PYTHON    := python3
VENV      := .venv
BIN       := $(VENV)/bin
PIP       := $(BIN)/pip
UMABOT    := $(BIN)/umabot

CONFIG_DIR  := $(HOME)/.umabot
CONFIG_FILE := $(CONFIG_DIR)/config.yaml
LOG_LEVEL   := DEBUG

# Colors
BLUE   := \033[0;34m
GREEN  := \033[0;32m
YELLOW := \033[0;33m
RED    := \033[0;31m
NC     := \033[0m

# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------

help: ## Show this help
	@echo "$(BLUE)UmaBot — Self-hosted AI Assistant$(NC)"
	@echo ""
	@echo "$(GREEN)Config:$(NC)  $(CONFIG_FILE)"
	@echo "$(GREEN)Venv:$(NC)    $(VENV)"
	@echo ""
	@echo "$(GREEN)Setup$(NC)"
	@echo "  $(YELLOW)install$(NC)       Create venv and install dependencies"
	@echo "  $(YELLOW)dev$(NC)           Install with dev dependencies (pytest, black, mypy…)"
	@echo "  $(YELLOW)upgrade$(NC)       Upgrade all installed dependencies"
	@echo "  $(YELLOW)init$(NC)          Run interactive configuration wizard"
	@echo "  $(YELLOW)doctor$(NC)        Check config, connectors, and skill health"
	@echo ""
	@echo "$(GREEN)Run$(NC)"
	@echo "  $(YELLOW)run$(NC)           Start in foreground (Ctrl+C to stop); auto-starts web panel"
	@echo "  $(YELLOW)start$(NC)         Start as background daemon"
	@echo "  $(YELLOW)stop$(NC)          Stop daemon or foreground processes"
	@echo "  $(YELLOW)restart$(NC)       Stop then start daemon"
	@echo "  $(YELLOW)status$(NC)        Show whether daemon is running"
	@echo "  $(YELLOW)reload$(NC)        Hot-reload config without restart"
	@echo "  $(YELLOW)logs$(NC)          Tail the live log file"
	@echo "  $(YELLOW)ps$(NC)            List all UmaBot processes"
	@echo ""
	@echo "$(GREEN)Control Panel$(NC)"
	@echo "  $(YELLOW)panel$(NC)         Start web control panel (installs deps, opens browser)"
	@echo "  $(YELLOW)panel-build$(NC)   Build React frontend → umabot/controlpanel/static/"
	@echo "  $(YELLOW)panel-dev$(NC)     Start frontend HMR dev server (needs 'make run' on :8080)"
	@echo ""
	@echo "$(GREEN)Skills$(NC)"
	@echo "  $(YELLOW)skills$(NC)        List all loaded skills"
	@echo "  $(YELLOW)skill-add$(NC)     Install a skill:  make skill-add SKILL=<path|url|name>"
	@echo "  $(YELLOW)skill-rm$(NC)      Remove a skill:   make skill-rm  SKILL=<name>"
	@echo ""
	@echo "$(GREEN)Config & DB$(NC)"
	@echo "  $(YELLOW)config$(NC)        Print current config.yaml"
	@echo "  $(YELLOW)edit$(NC)          Open config.yaml in \$$EDITOR"
	@echo "  $(YELLOW)db$(NC)            Open SQLite shell"
	@echo "  $(YELLOW)db-backup$(NC)     Backup database to ~/.umabot/backups/"
	@echo "  $(YELLOW)db-reset$(NC)      Delete database (keep config)"
	@echo ""
	@echo "$(GREEN)Development$(NC)"
	@echo "  $(YELLOW)test$(NC)          Run pytest"
	@echo "  $(YELLOW)lint$(NC)          Run flake8 + mypy"
	@echo "  $(YELLOW)format$(NC)        Format code with black"
	@echo "  $(YELLOW)check$(NC)         lint + test"
	@echo "  $(YELLOW)shell$(NC)         Python REPL with umabot imported"
	@echo "  $(YELLOW)gateway$(NC)       Start gateway only (no connectors, for dev)"
	@echo ""
	@echo "$(GREEN)Cleanup$(NC)"
	@echo "  $(YELLOW)reset$(NC)         Wipe ~/.umabot/ + sessions, keep .venv  →  re-run 'make init'"
	@echo "  $(YELLOW)clean$(NC)         Nuke everything: .venv + ~/.umabot/ + sessions  →  clean slate"
	@echo ""
	@echo "$(GREEN)Release$(NC)"
	@echo "  $(YELLOW)build$(NC)         Build dist packages"
	@echo "  $(YELLOW)publish-test$(NC)  Upload to TestPyPI"
	@echo "  $(YELLOW)publish$(NC)       Upload to PyPI"
	@echo "  $(YELLOW)info$(NC)          Show system/path information"

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

install: ## Create venv and install dependencies
	@echo "$(BLUE)Installing UmaBot...$(NC)"
	@if [ ! -d "$(VENV)" ]; then \
		echo "$(YELLOW)Creating virtual environment...$(NC)"; \
		$(PYTHON) -m venv $(VENV); \
	fi
	@$(PIP) install --quiet --upgrade pip
	@$(PIP) install -e .
	@echo "$(GREEN)✓ Done. Run 'make init' to configure.$(NC)"

dev: ## Install with dev dependencies (pytest, black, mypy, flake8)
	@echo "$(BLUE)Installing dev dependencies...$(NC)"
	@if [ ! -d "$(VENV)" ]; then $(PYTHON) -m venv $(VENV); fi
	@$(PIP) install --quiet --upgrade pip
	@$(PIP) install -e ".[dev]"
	@echo "$(GREEN)✓ Dev install complete.$(NC)"

upgrade: ## Upgrade all installed dependencies
	@echo "$(YELLOW)Upgrading dependencies...$(NC)"
	@$(PIP) install --quiet --upgrade pip
	@$(PIP) install --upgrade -e .
	@echo "$(GREEN)✓ Upgraded.$(NC)"

init: install ## Run interactive configuration wizard
	@echo "$(BLUE)Running configuration wizard...$(NC)"
	@mkdir -p $(CONFIG_DIR)
	@$(UMABOT) onboard --config $(CONFIG_FILE) --log-level $(LOG_LEVEL)

doctor: install ## Check config, connectors, and skill health
	@echo "$(BLUE)Running diagnostics...$(NC)"
	@$(UMABOT) doctor --config $(CONFIG_FILE) --log-level $(LOG_LEVEL) || true

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

run: install ## Start in foreground; auto-starts web panel if configured (Ctrl+C to stop)
	@echo "$(BLUE)Starting UmaBot...$(NC)"
	@echo "$(YELLOW)Ctrl+C to stop$(NC)"
	@( \
		PANEL_PID=""; \
		if grep -q "ui_type: web" $(CONFIG_FILE) 2>/dev/null && grep -q "enabled: true" $(CONFIG_FILE) 2>/dev/null; then \
			PORT=$$(grep 'web_port' $(CONFIG_FILE) 2>/dev/null | awk '{print $$2}' | head -1); \
			PORT=$${PORT:-8080}; \
			$(BIN)/python -m umabot.controlpanel --config $(CONFIG_FILE) --no-open --log-level $(LOG_LEVEL) & \
			PANEL_PID=$$!; \
			echo "$(GREEN)✓ Panel → http://127.0.0.1:$$PORT$(NC)"; \
		fi; \
		cleanup() { [ -n "$$PANEL_PID" ] && kill "$$PANEL_PID" 2>/dev/null; }; \
		trap cleanup EXIT INT TERM; \
		$(UMABOT) orchestrate --config $(CONFIG_FILE) --log-level $(LOG_LEVEL); \
	)

start: install ## Start as background daemon
	@echo "$(BLUE)Starting daemon...$(NC)"
	@$(UMABOT) start --config $(CONFIG_FILE) --log-level $(LOG_LEVEL)
	@sleep 1
	@$(MAKE) status

stop: ## Stop daemon or any foreground UmaBot processes
	@echo "$(YELLOW)Stopping UmaBot...$(NC)"
	@PID_FILE="$(CONFIG_DIR)/umabot.pid"; \
	if [ -f "$$PID_FILE" ]; then \
		PID=$$(cat "$$PID_FILE" 2>/dev/null); \
		if [ -n "$$PID" ] && kill -0 "$$PID" 2>/dev/null; then \
			echo "  Stopping daemon PID=$$PID"; \
			kill -TERM "$$PID" 2>/dev/null || true; \
		fi; \
		rm -f "$$PID_FILE"; \
	fi
	@for pat in "umabot orchestrate" "umabot.controlpanel" "umabot.gateway"; do \
		pids=$$(pgrep -f "$$pat" 2>/dev/null); \
		if [ -n "$$pids" ]; then \
			echo "  Stopping $$pat (PID $$pids)"; \
			kill -TERM $$pids 2>/dev/null || true; \
		fi; \
	done
	@sleep 1
	@for pat in "umabot orchestrate" "umabot.controlpanel" "umabot.gateway"; do \
		pids=$$(pgrep -f "$$pat" 2>/dev/null); \
		if [ -n "$$pids" ]; then \
			echo "  $(RED)Force-killing $$pat (PID $$pids)$(NC)"; \
			kill -KILL $$pids 2>/dev/null || true; \
		fi; \
	done
	@echo "$(GREEN)✓ Stopped$(NC)"

restart: stop start ## Stop then start daemon

status: install ## Show whether daemon is running
	@$(UMABOT) status --config $(CONFIG_FILE) || echo "$(RED)UmaBot is not running$(NC)"

reload: install ## Hot-reload config without restart
	@echo "$(YELLOW)Reloading config...$(NC)"
	@$(UMABOT) reload --config $(CONFIG_FILE)

logs: ## Tail the live log file
	@tail -f $(CONFIG_DIR)/logs/umabot.log

ps: ## List all UmaBot processes
	@ps aux | grep -E "umabot|python.*gateway|python.*connector" | grep -v grep \
		|| echo "$(YELLOW)No UmaBot processes running$(NC)"

# ---------------------------------------------------------------------------
# Control Panel
# ---------------------------------------------------------------------------

panel: install ## Start web control panel (installs deps, opens browser)
	@echo "$(BLUE)Starting control panel...$(NC)"
	@$(PIP) install --quiet -e ".[panel]"
	@$(UMABOT) panel --config $(CONFIG_FILE) --log-level $(LOG_LEVEL)

panel-build: ## Build React frontend → umabot/controlpanel/static/
	@echo "$(BLUE)Building frontend...$(NC)"
	@cd umabot/controlpanel/frontend && npm install && npm run build
	@echo "$(GREEN)✓ Built → umabot/controlpanel/static/$(NC)"

panel-dev: ## Start frontend HMR dev server (requires 'make run' on :8080)
	@echo "$(BLUE)Starting HMR dev server → http://localhost:5173$(NC)"
	@echo "$(YELLOW)Requires 'make run' running on :8080$(NC)"
	@cd umabot/controlpanel/frontend && npm run dev

# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------

skills: install ## List all loaded skills
	@$(UMABOT) skills list --config $(CONFIG_FILE)

skill-add: install ## Install a skill  (make skill-add SKILL=<path|url|name>)
	@if [ -z "$(SKILL)" ]; then \
		echo "$(RED)Usage: make skill-add SKILL=<path|url|name>$(NC)"; exit 1; \
	fi
	@$(UMABOT) skills install $(SKILL) --config $(CONFIG_FILE)
	@echo "$(YELLOW)Run 'make reload' to activate$(NC)"

skill-rm: install ## Remove a skill  (make skill-rm SKILL=<name>)
	@if [ -z "$(SKILL)" ]; then \
		echo "$(RED)Usage: make skill-rm SKILL=<name>$(NC)"; exit 1; \
	fi
	@$(UMABOT) skills remove $(SKILL) --config $(CONFIG_FILE)
	@echo "$(YELLOW)Run 'make reload' to deactivate$(NC)"

# ---------------------------------------------------------------------------
# Config & DB
# ---------------------------------------------------------------------------

config: ## Print current config.yaml
	@cat $(CONFIG_FILE) 2>/dev/null || echo "$(YELLOW)No config found — run 'make init' first$(NC)"

edit: ## Open config.yaml in $$EDITOR (falls back to nano)
	@[ -f $(CONFIG_FILE) ] || { echo "$(YELLOW)No config — run 'make init' first$(NC)"; exit 1; }
	@$${EDITOR:-nano} $(CONFIG_FILE)

db: ## Open SQLite shell
	@sqlite3 $(CONFIG_DIR)/umabot.db

db-backup: ## Backup database to ~/.umabot/backups/
	@mkdir -p $(CONFIG_DIR)/backups
	@cp $(CONFIG_DIR)/umabot.db $(CONFIG_DIR)/backups/umabot-$(shell date +%Y%m%d-%H%M%S).db
	@echo "$(GREEN)✓ Backed up$(NC)"

db-reset: stop ## Delete database — keep config (WARNING: loses all history)
	@echo "$(RED)WARNING: Deletes all messages, tasks, and history$(NC)"
	@read -p "Are you sure? (yes/no): " c; [ "$$c" = "yes" ] || { echo "Cancelled"; exit 0; }
	@rm -f $(CONFIG_DIR)/umabot.db $(CONFIG_DIR)/umabot.db-shm $(CONFIG_DIR)/umabot.db-wal
	@echo "$(GREEN)✓ Database cleared — restart UmaBot to recreate$(NC)"

# ---------------------------------------------------------------------------
# Development
# ---------------------------------------------------------------------------

test: ## Run pytest
	@echo "$(YELLOW)Running tests...$(NC)"
	@$(BIN)/pytest tests/ -v 2>/dev/null || echo "$(YELLOW)No tests found$(NC)"

lint: ## Run flake8 + mypy
	@echo "$(YELLOW)Linting...$(NC)"
	@$(BIN)/flake8 umabot/ || true
	@$(BIN)/mypy umabot/ || true

format: ## Format code with black
	@$(BIN)/black umabot/
	@echo "$(GREEN)✓ Formatted$(NC)"

check: lint test ## Run lint + test

shell: install ## Open Python REPL with umabot imported
	@$(BIN)/python -i -c "from umabot import *; print('UmaBot loaded')"

gateway: install ## Start gateway only — no connectors (dev/debug)
	@echo "$(BLUE)Starting gateway only...$(NC)"
	@$(BIN)/python -m umabot.gateway --config $(CONFIG_FILE) --log-level $(LOG_LEVEL)

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

reset: stop ## Wipe ~/.umabot/ + sessions, keep .venv  →  re-run 'make init'
	@echo "$(RED)Deletes: ~/.umabot/ (config, db, vault, logs) and *.session files$(NC)"
	@read -p "Are you sure? (yes/no): " c; [ "$$c" = "yes" ] || { echo "Cancelled"; exit 0; }
	@rm -rf $(CONFIG_DIR)
	@find . -maxdepth 2 -name "*.session" -delete 2>/dev/null || true
	@echo "$(GREEN)✓ Reset. Run 'make init' to configure from scratch.$(NC)"

clean: stop ## Nuke everything: .venv + ~/.umabot/ + sessions + build artifacts
	@echo "$(RED)Deletes: .venv, ~/.umabot/, *.session, build artifacts$(NC)"
	@read -p "Are you sure? (yes/no): " c; [ "$$c" = "yes" ] || { echo "Cancelled"; exit 0; }
	@rm -rf build/ dist/ *.egg-info $(VENV) $(CONFIG_DIR)
	@find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@find . \( -name "*.pyc" -o -name "*.pyo" -o -name "*.session" \) -delete 2>/dev/null || true
	@echo "$(GREEN)✓ Clean slate. Run 'make install && make init' to start fresh.$(NC)"

# ---------------------------------------------------------------------------
# Release
# ---------------------------------------------------------------------------

build: ## Build distribution packages (wheel + sdist)
	@echo "$(BLUE)Building...$(NC)"
	@$(BIN)/python -m build
	@echo "$(GREEN)✓ Built → dist/$(NC)"

publish-test: build ## Upload to TestPyPI
	@$(BIN)/twine upload --repository testpypi dist/*

publish: build ## Upload to PyPI
	@echo "$(RED)Publishing to PyPI$(NC)"
	@read -p "Are you sure? (yes/no): " c; [ "$$c" = "yes" ] || { echo "Cancelled"; exit 0; }
	@$(BIN)/twine upload dist/*
	@echo "$(GREEN)✓ Published$(NC)"

# ---------------------------------------------------------------------------
# Info
# ---------------------------------------------------------------------------

info: ## Show system paths and state
	@echo "$(BLUE)UmaBot Info$(NC)"
	@echo "  Python    : $(shell $(PYTHON) --version)"
	@echo "  Venv      : $(VENV)"
	@echo "  Config    : $(CONFIG_FILE)"
	@echo "  Database  : $(CONFIG_DIR)/umabot.db"
	@echo "  Logs      : $(CONFIG_DIR)/logs/"
	@echo "  Log level : $(LOG_LEVEL)"
	@echo ""
	@[ -f $(CONFIG_FILE) ]         && echo "$(GREEN)  ✓ Config exists$(NC)"   || echo "$(YELLOW)  ! No config$(NC)"
	@[ -f $(CONFIG_DIR)/umabot.db ] && echo "$(GREEN)  ✓ Database exists$(NC)" || echo "$(YELLOW)  ! No database$(NC)"
	@[ -d $(VENV) ]                && echo "$(GREEN)  ✓ Venv exists$(NC)"     || echo "$(YELLOW)  ! No venv$(NC)"

.DEFAULT_GOAL := help
