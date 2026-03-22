.PHONY: help install dev-install panel-install clean test lint format run run-debug start stop status reload skills doctor setup panel panel-build panel-dev

# Default Python
PYTHON := python3
VENV := .venv
BIN := $(VENV)/bin
PIP := $(BIN)/pip
UMABOT := $(BIN)/umabot

# Configuration
CONFIG_DIR := $(HOME)/.umabot
CONFIG_FILE := $(CONFIG_DIR)/config.yaml
LOG_LEVEL := DEBUG

# Colors for output
BLUE := \033[0;34m
GREEN := \033[0;32m
YELLOW := \033[0;33m
RED := \033[0;31m
NC := \033[0m # No Color

help: ## Show this help message
	@echo "$(BLUE)UmaBot - Self-hosted AI Assistant$(NC)"
	@echo ""
	@echo "$(GREEN)Configuration:$(NC)"
	@echo "  Config:    $(CONFIG_FILE)"
	@echo "  Log Level: $(LOG_LEVEL) (change in Makefile)"
	@echo ""
	@echo "$(GREEN)Available targets:$(NC)"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  $(YELLOW)%-20s$(NC) %s\n", $$1, $$2}'
	@echo ""
	@echo "$(GREEN)Examples:$(NC)"
	@echo "  make install          # First-time setup"
	@echo "  make init             # Configure UmaBot (with control panel)"
	@echo "  make run              # Run in foreground"
	@echo "  make start            # Start as daemon"

##@ Installation

install: ## Install UmaBot (create venv, install deps)
	@echo "$(BLUE)Installing UmaBot...$(NC)"
	@if [ ! -d "$(VENV)" ]; then \
		echo "$(YELLOW)Creating virtual environment...$(NC)"; \
		$(PYTHON) -m venv $(VENV); \
	fi
	@echo "$(YELLOW)Installing dependencies...$(NC)"
	@$(PIP) install --upgrade pip
	@$(PIP) install -e .
	@echo "$(GREEN)✓ Installation complete!$(NC)"
	@echo ""
	@echo "$(BLUE)Next steps:$(NC)"
	@echo "  1. Configure UmaBot:          $(YELLOW)make init$(NC)"
	@echo "  2. Install panel deps:        $(YELLOW)make panel-install$(NC)"
	@echo "  3. Build frontend:            $(YELLOW)make panel-build$(NC)"
	@echo "  4. Run everything:            $(YELLOW)make run$(NC)"
	@echo ""
	@echo "$(GREEN)Config will be saved to: $(CONFIG_FILE)$(NC)"

dev-install: ## Install with development dependencies
	@echo "$(BLUE)Installing UmaBot (development mode)...$(NC)"
	@if [ ! -d "$(VENV)" ]; then \
		echo "$(YELLOW)Creating virtual environment...$(NC)"; \
		$(PYTHON) -m venv $(VENV); \
	fi
	@$(PIP) install --upgrade pip
	@$(PIP) install -e ".[dev]"
	@echo "$(GREEN)✓ Development installation complete!$(NC)"

upgrade: ## Upgrade dependencies
	@echo "$(YELLOW)Upgrading dependencies...$(NC)"
	@$(PIP) install --upgrade pip
	@$(PIP) install --upgrade -e .
	@echo "$(GREEN)✓ Dependencies upgraded!$(NC)"

##@ Configuration

init: install ## Run interactive configuration wizard (includes control panel setup)
	@echo "$(BLUE)Running configuration wizard...$(NC)"
	@mkdir -p $(CONFIG_DIR)
	@$(UMABOT) onboard --config $(CONFIG_FILE) --log-level $(LOG_LEVEL)

doctor: install ## Run system diagnostics
	@echo "$(BLUE)Running diagnostics...$(NC)"
	@$(UMABOT) doctor --config $(CONFIG_FILE) --log-level $(LOG_LEVEL) || true

##@ Running

run: install ## Run UmaBot in foreground (gateway + connectors + panel if configured)
	@echo "$(BLUE)Starting UmaBot in foreground...$(NC)"
	@echo "$(YELLOW)Press Ctrl+C to stop$(NC)"
	@( \
		PANEL_PID=""; \
		if grep -q "ui_type: web" $(CONFIG_FILE) 2>/dev/null && grep -q "enabled: true" $(CONFIG_FILE) 2>/dev/null; then \
			PORT=$$(grep 'web_port' $(CONFIG_FILE) 2>/dev/null | awk '{print $$2}' | head -1); \
			PORT=$${PORT:-8080}; \
			$(BIN)/python -m umabot.controlpanel --config $(CONFIG_FILE) --no-open --log-level $(LOG_LEVEL) & \
			PANEL_PID=$$!; \
			echo "$(GREEN)✓ Control panel starting → http://127.0.0.1:$$PORT$(NC)"; \
		fi; \
		cleanup() { [ -n "$$PANEL_PID" ] && kill "$$PANEL_PID" 2>/dev/null; }; \
		trap cleanup EXIT INT TERM; \
		$(UMABOT) orchestrate --config $(CONFIG_FILE) --log-level $(LOG_LEVEL); \
	)

run-debug: install ## Run in foreground with DEBUG logging
	@echo "$(BLUE)Starting UmaBot in DEBUG mode...$(NC)"
	@echo "$(YELLOW)Press Ctrl+C to stop$(NC)"
	@( \
		PANEL_PID=""; \
		if grep -q "ui_type: web" $(CONFIG_FILE) 2>/dev/null && grep -q "enabled: true" $(CONFIG_FILE) 2>/dev/null; then \
			PORT=$$(grep 'web_port' $(CONFIG_FILE) 2>/dev/null | awk '{print $$2}' | head -1); \
			PORT=$${PORT:-8080}; \
			$(BIN)/python -m umabot.controlpanel --config $(CONFIG_FILE) --no-open --log-level DEBUG & \
			PANEL_PID=$$!; \
			echo "$(GREEN)✓ Control panel starting → http://127.0.0.1:$$PORT$(NC)"; \
		fi; \
		cleanup() { [ -n "$$PANEL_PID" ] && kill "$$PANEL_PID" 2>/dev/null; }; \
		trap cleanup EXIT INT TERM; \
		$(UMABOT) orchestrate --config $(CONFIG_FILE) --log-level DEBUG; \
	)

run-gateway: install ## Run only gateway (no connectors)
	@echo "$(BLUE)Starting gateway only...$(NC)"
	@$(BIN)/python -m umabot.gateway --config $(CONFIG_FILE) --log-level DEBUG

##@ Control Panel

panel-install: ## Install control panel dependencies (fastapi + uvicorn)
	@echo "$(BLUE)Installing control panel dependencies...$(NC)"
	@$(PIP) install -e ".[panel]"
	@echo "$(GREEN)✓ Panel dependencies installed$(NC)"

panel: panel-install ## Start the web control panel (opens browser)
	@echo "$(BLUE)Starting control panel...$(NC)"
	@$(UMABOT) panel --config $(CONFIG_FILE) --log-level $(LOG_LEVEL)

panel-build: ## Build the frontend (outputs to umabot/controlpanel/static/)
	@echo "$(BLUE)Building control panel frontend...$(NC)"
	@cd umabot/controlpanel/frontend && npm install && npm run build
	@echo "$(GREEN)✓ Frontend built → umabot/controlpanel/static/$(NC)"

panel-dev: ## Start frontend dev server with HMR (requires gateway + panel running separately)
	@echo "$(BLUE)Starting frontend dev server on http://localhost:5173$(NC)"
	@echo "$(YELLOW)Make sure 'make run' or 'make panel' is running on port 8080$(NC)"
	@cd umabot/controlpanel/frontend && npm run dev

##@ Daemon Management

start: install ## Start UmaBot daemon
	@echo "$(BLUE)Starting UmaBot daemon...$(NC)"
	@$(UMABOT) start --config $(CONFIG_FILE) --log-level $(LOG_LEVEL)
	@sleep 1
	@$(MAKE) status

stop: ## Stop all UmaBot processes (daemon PID file + any foreground make run/start processes)
	@echo "$(YELLOW)Stopping UmaBot...$(NC)"
	@# 1. Daemon mode: kill via PID file written by `make start`
	@PID_FILE="$(CONFIG_DIR)/umabot.pid"; \
	if [ -f "$$PID_FILE" ]; then \
		PID=$$(cat "$$PID_FILE" 2>/dev/null); \
		if [ -n "$$PID" ] && kill -0 "$$PID" 2>/dev/null; then \
			echo "  $(YELLOW)Stopping daemon PID=$$PID$(NC)"; \
			kill -TERM "$$PID" 2>/dev/null || true; \
		fi; \
		rm -f "$$PID_FILE"; \
	fi
	@# 2. Foreground mode: kill processes started by `make run` / `make run-debug`
	@#    Patterns match: `umabot orchestrate`, `umabot.controlpanel`, `umabot.gateway`
	@for pat in "umabot orchestrate" "umabot.controlpanel" "umabot.gateway"; do \
		pids=$$(pgrep -f "$$pat" 2>/dev/null); \
		if [ -n "$$pids" ]; then \
			echo "  $(YELLOW)Stopping $$pat (PID $$pids)$(NC)"; \
			kill -TERM $$pids 2>/dev/null || true; \
		fi; \
	done
	@# 3. Give processes a moment to exit gracefully, then force-kill stragglers
	@sleep 1
	@for pat in "umabot orchestrate" "umabot.controlpanel" "umabot.gateway"; do \
		pids=$$(pgrep -f "$$pat" 2>/dev/null); \
		if [ -n "$$pids" ]; then \
			echo "  $(RED)Force-killing $$pat (PID $$pids)$(NC)"; \
			kill -KILL $$pids 2>/dev/null || true; \
		fi; \
	done
	@echo "$(GREEN)✓ All UmaBot processes stopped$(NC)"

restart: stop start ## Restart UmaBot daemon

status: ## Show daemon status
	@$(UMABOT) status --config $(CONFIG_FILE) || echo "$(RED)UmaBot is not running$(NC)"

reload: ## Reload configuration (hot reload)
	@echo "$(YELLOW)Reloading configuration...$(NC)"
	@$(UMABOT) reload --config $(CONFIG_FILE)

logs: ## Show daemon logs (tail -f)
	@tail -f $(CONFIG_DIR)/logs/umabot.log

##@ Skills Management

skills-list: install ## List installed skills
	@$(UMABOT) skills list --config $(CONFIG_FILE)

skills-install: install ## Install skill (usage: make skills-install SKILL=<source>)
	@if [ -z "$(SKILL)" ]; then \
		echo "$(RED)Error: SKILL not specified$(NC)"; \
		echo "Usage: make skills-install SKILL=<source>"; \
		echo "Examples:"; \
		echo "  make skills-install SKILL=umabot-skill-github"; \
		echo "  make skills-install SKILL=https://github.com/user/skill.git"; \
		echo "  make skills-install SKILL=./my-skill"; \
		exit 1; \
	fi
	@$(UMABOT) skills install $(SKILL) --config $(CONFIG_FILE)

skills-remove: install ## Remove skill (usage: make skills-remove SKILL=<name>)
	@if [ -z "$(SKILL)" ]; then \
		echo "$(RED)Error: SKILL not specified$(NC)"; \
		echo "Usage: make skills-remove SKILL=<name>"; \
		exit 1; \
	fi
	@$(UMABOT) skills remove $(SKILL) --config $(CONFIG_FILE)

skills-lint: install ## Validate all skills
	@$(UMABOT) skills lint --config $(CONFIG_FILE)

##@ Development

test: ## Run tests
	@echo "$(YELLOW)Running tests...$(NC)"
	@$(BIN)/pytest tests/ -v || echo "$(YELLOW)No tests found$(NC)"

lint: ## Run linters (flake8, mypy)
	@echo "$(YELLOW)Running linters...$(NC)"
	@$(BIN)/flake8 umabot/ || true
	@$(BIN)/mypy umabot/ || true

format: ## Format code with black
	@echo "$(YELLOW)Formatting code...$(NC)"
	@$(BIN)/black umabot/
	@echo "$(GREEN)✓ Code formatted!$(NC)"

check: lint test ## Run all checks (lint + test)

##@ Cleanup

clean: ## Clean build artifacts and cache
	@echo "$(YELLOW)Cleaning build artifacts...$(NC)"
	@rm -rf build/
	@rm -rf dist/
	@rm -rf *.egg-info
	@find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete
	@find . -type f -name "*.pyo" -delete
	@echo "$(GREEN)✓ Clean complete!$(NC)"

clean-all: clean stop ## Clean everything including venv and data
	@echo "$(RED)WARNING: This will delete the virtual environment and all data!$(NC)"
	@read -p "Are you sure? (yes/no): " confirm; \
	if [ "$$confirm" = "yes" ]; then \
		echo "$(YELLOW)Removing virtual environment...$(NC)"; \
		rm -rf $(VENV); \
		echo "$(YELLOW)Removing data directory...$(NC)"; \
		rm -rf $(CONFIG_DIR); \
		echo "$(GREEN)✓ Complete cleanup done!$(NC)"; \
	else \
		echo "$(BLUE)Cleanup cancelled$(NC)"; \
	fi

##@ Build & Release

build: clean ## Build distribution packages
	@echo "$(BLUE)Building distribution packages...$(NC)"
	@$(BIN)/python -m build
	@echo "$(GREEN)✓ Build complete! Check dist/$(NC)"

publish-test: build ## Publish to TestPyPI
	@echo "$(YELLOW)Publishing to TestPyPI...$(NC)"
	@$(BIN)/twine upload --repository testpypi dist/*

publish: build ## Publish to PyPI
	@echo "$(RED)Publishing to PyPI...$(NC)"
	@read -p "Are you sure? (yes/no): " confirm; \
	if [ "$$confirm" = "yes" ]; then \
		$(BIN)/twine upload dist/*; \
		echo "$(GREEN)✓ Published to PyPI!$(NC)"; \
	else \
		echo "$(BLUE)Publish cancelled$(NC)"; \
	fi

##@ Database

db-shell: ## Open SQLite shell for database
	@sqlite3 $(CONFIG_DIR)/umabot.db

db-backup: ## Backup database
	@mkdir -p $(CONFIG_DIR)/backups
	@cp $(CONFIG_DIR)/umabot.db $(CONFIG_DIR)/backups/umabot-$(shell date +%Y%m%d-%H%M%S).db
	@echo "$(GREEN)✓ Database backed up!$(NC)"

db-reset: stop ## Reset database (WARNING: deletes all data)
	@echo "$(RED)WARNING: This will delete all messages, tasks, and history!$(NC)"
	@read -p "Are you sure? (yes/no): " confirm; \
	if [ "$$confirm" = "yes" ]; then \
		rm -f $(CONFIG_DIR)/umabot.db; \
		echo "$(GREEN)✓ Database reset!$(NC)"; \
		echo "$(YELLOW)Restart UmaBot to create a new database$(NC)"; \
	else \
		echo "$(BLUE)Reset cancelled$(NC)"; \
	fi

##@ Utilities

shell: install ## Open Python shell with UmaBot imported
	@echo "$(BLUE)Opening Python shell...$(NC)"
	@$(BIN)/python -i -c "from umabot import *; print('UmaBot modules loaded')"

config-show: ## Show current configuration
	@cat $(CONFIG_FILE) || echo "$(YELLOW)No configuration found. Run 'make init' first.$(NC)"

config-edit: ## Edit configuration file
	@if [ ! -f $(CONFIG_FILE) ]; then \
		echo "$(YELLOW)No configuration found. Run 'make init' first.$(NC)"; \
		exit 1; \
	fi
	@$${EDITOR:-nano} $(CONFIG_FILE)

watch-logs: logs ## Watch logs in real-time (alias for logs)

ps: ## Show UmaBot processes
	@ps aux | grep -E "umabot|python.*gateway|python.*connector" | grep -v grep || echo "$(YELLOW)No UmaBot processes running$(NC)"

##@ Quick Start Workflows

quick-start: install init start ## Complete setup and start (interactive)
	@echo ""
	@echo "$(GREEN)✓ UmaBot is running!$(NC)"
	@echo ""
	@echo "Useful commands:"
	@echo "  make status   # Check if running"
	@echo "  make logs     # View logs"
	@echo "  make stop     # Stop daemon"

demo: install ## Quick demo setup (development)
	@echo "$(BLUE)Setting up demo environment...$(NC)"
	@cp config.example.yaml config.yaml || true
	@$(MAKE) run-debug

##@ Info

version: ## Show UmaBot version
	@$(UMABOT) --version || echo "UmaBot (development)"

info: ## Show system information
	@echo "$(BLUE)UmaBot System Information$(NC)"
	@echo ""
	@echo "Python:     $(shell $(PYTHON) --version)"
	@echo "Virtualenv: $(VENV)"
	@echo "Config:     $(CONFIG_FILE)"
	@echo "Database:   $(CONFIG_DIR)/umabot.db"
	@echo "Logs:       $(CONFIG_DIR)/logs/"
	@echo "Skills:     $(CONFIG_DIR)/skills/"
	@echo "Log Level:  $(LOG_LEVEL)"
	@echo ""
	@if [ -f $(CONFIG_DIR)/umabot.db ]; then \
		echo "$(GREEN)✓ Database exists$(NC)"; \
	else \
		echo "$(YELLOW)! Database not created yet$(NC)"; \
	fi
	@if [ -f $(CONFIG_FILE) ]; then \
		echo "$(GREEN)✓ Configuration exists$(NC)"; \
	else \
		echo "$(YELLOW)! No configuration found$(NC)"; \
	fi

.DEFAULT_GOAL := help
