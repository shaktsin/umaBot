# UMA BOT Architecture

## Overview

UMA BOT follows a **control plane + connectors** architecture with clear separation of concerns.

```
┌─────────────────────────────────────────────────────┐
│                   Control Panel                      │
│  (Your private interface - CLI/Web/Telegram/Discord) │
│            ↕ Confirmations & Management               │
└─────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────┐
│                      Gateway                         │
│  (Message routing, LLM runtime, Policy engine)       │
└─────────────────────────────────────────────────────┘
                          ↓
┌──────────────┬──────────────┬──────────────┬─────────┐
│  Telegram    │   Discord    │  Telegram    │  Future │
│  Bot         │   Bot        │  User        │  ...    │
│  Connector   │   Connector  │  Connector   │         │
└──────────────┴──────────────┴──────────────┴─────────┘
```

## Key Design: Control Panel ≠ Connectors

**Control Panel** = YOUR private interface
- Can be: Telegram bot, Discord, CLI chat, or Web UI
- Receives confirmations and management commands
- Completely isolated from external messages

**Connectors** = External message sources
- Handle messages from OTHER users/platforms
- Can have multiple connectors (even same type)
- Untrusted by default

This separation allows:
- Use CLI control panel + Telegram connectors
- Use Telegram control panel + Discord connectors
- Maximum flexibility and security
