# Makefile Quick Reference

## 📍 Configuration

The Makefile uses these defaults (configurable at top of Makefile):
- **Config File**: `~/.umabot/config.yaml`
- **Log Level**: `DEBUG` (change `LOG_LEVEL` variable in Makefile)
- **Virtual Env**: `.venv` in project directory

## 🚀 Quick Start Commands

```bash
make install          # First-time installation (creates .venv)
make init             # Configure UmaBot with control panel (saves to ~/.umabot/config.yaml)
make run              # Run in foreground with DEBUG logging
```

**Or all-in-one:**
```bash
make quick-start      # Does install + init + start
```

**Note:** `make init` includes automatic Telegram control panel setup with chat ID discovery!

## 📦 Installation

| Command | Description |
|---------|-------------|
| `make install` | Create venv and install UmaBot |
| `make dev-install` | Install with dev dependencies |
| `make upgrade` | Upgrade all dependencies |

## ⚙️ Configuration

| Command | Description |
|---------|-------------|
| `make init` | Run configuration wizard (includes control panel setup with auto chat ID discovery) |
| `make doctor` | Run system diagnostics |
| `make config-show` | Display current config |
| `make config-edit` | Edit config in editor |

## 🎮 Running UmaBot

| Command | Description |
|---------|-------------|
| `make run` | **Run in foreground** (uses LOG_LEVEL from Makefile, default: DEBUG) |
| `make run-debug` | **Run in foreground** (DEBUG logging) |
| `make start` | Start as daemon |
| `make stop` | Stop daemon |
| `make restart` | Restart daemon |
| `make status` | Check daemon status |
| `make reload` | Hot reload configuration |

### Foreground vs Daemon

**Foreground mode** (`make run`):
- Runs in terminal
- See logs immediately
- Press Ctrl+C to stop
- **Perfect for development**

**Daemon mode** (`make start`):
- Runs in background
- Survives terminal close
- Use `make logs` to view output
- **Perfect for production**

## 📊 Monitoring

| Command | Description |
|---------|-------------|
| `make logs` | Watch logs in real-time (`tail -f`) |
| `make status` | Show if daemon is running |
| `make ps` | Show all UmaBot processes |
| `make info` | Show system information |

## 🛠️ Skills Management

| Command | Example | Description |
|---------|---------|-------------|
| `make skills-list` | | List installed skills |
| `make skills-install SKILL=X` | `make skills-install SKILL=umabot-skill-github` | Install from PyPI |
| | `make skills-install SKILL=https://github.com/user/skill.git` | Install from GitHub |
| | `make skills-install SKILL=./my-skill` | Install from local path |
| `make skills-remove SKILL=X` | `make skills-remove SKILL=github` | Remove skill |
| `make skills-lint` | | Validate all skills |

## 🗄️ Database

| Command | Description |
|---------|-------------|
| `make db-shell` | Open SQLite shell |
| `make db-backup` | Backup database |
| `make db-reset` | Reset database (deletes all data!) |

## 🧹 Cleanup

| Command | Description |
|---------|-------------|
| `make clean` | Clean build artifacts and cache |
| `make clean-all` | **Delete venv and all data** (confirmation required) |

## 🔧 Development

| Command | Description |
|---------|-------------|
| `make test` | Run tests |
| `make lint` | Run linters (flake8, mypy) |
| `make format` | Format code with black |
| `make check` | Run lint + test |
| `make shell` | Open Python shell with UmaBot loaded |

## 📦 Build & Publish

| Command | Description |
|---------|-------------|
| `make build` | Build distribution packages |
| `make publish-test` | Publish to TestPyPI |
| `make publish` | Publish to PyPI (confirmation required) |

## 🎯 Common Workflows

### First Time Setup
```bash
make install          # Install
make init             # Configure (includes control panel with auto chat ID)
make run              # Test in foreground
```

### Development Workflow
```bash
make run-debug        # Run with debug logging
# Make code changes
make reload           # Hot reload (if daemon)
# Or just Ctrl+C and re-run
```

### Production Deployment
```bash
make install
make init             # Configure (includes control panel setup)
make start            # Start as daemon
make status           # Verify running
```

### Installing Skills
```bash
make skills-install SKILL=umabot-skill-github
make reload           # Reload to activate
make skills-list      # Verify installed
```

### Debugging Issues
```bash
make doctor           # Run diagnostics
make logs             # Watch logs
make ps               # Check processes
make info             # System info
```

### Daily Operations
```bash
make status           # Check if running
make logs             # Monitor activity
make skills-list      # See installed skills
make db-backup        # Periodic backup
```

## 💡 Tips

1. **Always use `make run` for development** - See logs immediately
2. **Use `make start` for production** - Runs in background
3. **Before reporting issues** - Run `make doctor` first
4. **Before major changes** - Run `make db-backup`
5. **See all commands** - Just type `make help`

## 🎨 Color Coding

The Makefile uses colors in output:
- 🔵 **Blue**: Section headers
- 🟢 **Green**: Success messages
- 🟡 **Yellow**: Warnings/in-progress
- 🔴 **Red**: Errors/destructive actions

## ⌨️ Shortcuts

```bash
make               # Shows help (same as `make help`)
make run           # Most common: run in foreground
make start         # Second most common: daemon mode
```

## 🔍 Getting Help

```bash
make help          # Show all commands
make info          # Show system info
make doctor        # Run diagnostics
```

## 📝 Examples

### Complete Fresh Install
```bash
git clone https://github.com/yourusername/umabot.git
cd umabot
make quick-start   # Does everything!
```

### Update After Git Pull
```bash
git pull
make upgrade       # Upgrade dependencies
make restart       # Restart daemon
```

### Clean Reinstall
```bash
make clean-all     # WARNING: Deletes everything!
make install       # Fresh install
make init          # Reconfigure
```

### Run Different Modes
```bash
# Development (see logs)
make run

# Production (background)
make start

# Debug specific issue
make run-debug

# Just gateway (no connectors)
make run-gateway
```

## 🚨 Safety Features

Commands that are **destructive** require confirmation:
- `make clean-all` - Deletes venv and data
- `make db-reset` - Deletes database
- `make publish` - Publishes to PyPI

Just type `yes` to confirm or `no` to cancel.

## 🔗 Related Documentation

- [README.md](README.md) - Main documentation
- [docs/CONTROL_PANEL_SETUP.md](docs/CONTROL_PANEL_SETUP.md) - Control panel setup
- [plans/SKILL_SYSTEM_COMPLETE.md](plans/SKILL_SYSTEM_COMPLETE.md) - Skills guide

## 📋 Cheat Sheet

**Most Used:**
```bash
make install       # Once
make run           # Development
make start         # Production
make stop          # Stop daemon
make logs          # Watch logs
make status        # Check status
```

**Skills:**
```bash
make skills-list
make skills-install SKILL=package-name
make skills-remove SKILL=name
```

**Maintenance:**
```bash
make doctor        # Diagnostics
make db-backup     # Backup
make upgrade       # Update deps
```
