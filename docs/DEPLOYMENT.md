# Deployment Guide

## Overview

Production deployment options for the Kalshi Trading Bot with emphasis on safety, observability, and operational simplicity.

---

## Pre-Deployment Checklist

- [ ] **Shadow mode validated** — 7+ days, zero kill switch triggers, < 1% API error rate
- [ ] **Secrets managed** — API key, private key in secret manager (not `.env`)
- [ ] **Private key permissions** — `chmod 600`, owned by bot user, in allowed directory (`~/.kalshi/` or `/etc/kalshi/keys/`)
- [ ] **Monitoring configured** — Alerts on kill switch, circuit breaker, API errors, WS disconnects
- [ ] **Runbook accessible** — Team knows `RUNBOOK.md` procedures
- [ ] **Rollback tested** — Can revert to shadow mode in < 5 minutes

---

## Option 1: Docker Compose (Single Host)

### Files

**Dockerfile** (place at repo root):
```dockerfile
FROM python:3.12-slim

WORKDIR /app

# System deps for cryptography
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libssl-dev libffi-dev && \
    rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application
COPY . .

# Non-root user
RUN useradd -m -u 1000 botuser && chown -R botuser:botuser /app
USER botuser

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import config; config.Config.validate()" || exit 1

ENTRYPOINT ["python", "main.py"]
```

**docker-compose.yml**:
```yaml
version: '3.8'

services:
  kalshi-bot:
    build: .
    container_name: kalshi-bot
    restart: unless-stopped
    env_file: .env
    volumes:
      - ./logs:/app/logs
      - ./data:/app/data
      - ${KALSHI_KEY_DIR:-/host/path/to/keys}:/home/botuser/.kalshi:ro
    environment:
      - PYTHONUNBUFFERED=1
      - LOG_LEVEL=INFO
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "5"
    healthcheck:
      test: ["CMD", "python", "-c", "import config; config.Config.validate()"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 15s

  # Optional: Log aggregation
  loki:
    image: grafana/loki:2.9
    ports: ["3100:3100"]
    volumes:
      - ./logs:/logs
    command: -config.file=/etc/loki/local-config.yaml

  # Optional: Metrics
  prometheus:
    image: prom/prometheus:v2.47
    ports: ["9090:9090"]
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml
```

**prometheus.yml** (for scraping `/metrics` if you add prometheus-client):
```yaml
global:
  scrape_interval: 15s
scrape_configs:
  - job_name: 'kalshi-bot'
    static_configs:
      - targets: ['host.docker.internal:8000']  # Add metrics endpoint
```

### Deploy
```bash
# On server
git clone https://github.com/DavidEscotoDev/kalshi_bot.git
cd kalshi_bot

# Configure secrets (use Docker secrets or mounted files)
cp .env.example .env
# Edit .env with production values

# Build & start
docker-compose up -d --build

# Verify
docker-compose logs -f kalshi-bot
docker-compose ps
```

### Update
```bash
git pull
docker-compose up -d --build  # Rebuilds image
docker-compose logs -f kalshi-bot
```

---

## Option 2: Systemd (Linux VM)

### 1. Create Service User
```bash
sudo useradd -r -s /bin/bash -m -d /opt/kalshi-bot kalshi-bot
sudo mkdir -p /opt/kalshi-bot/{logs,data}
sudo chown -R kalshi-bot:kalshi-bot /opt/kalshi-bot
```

### 2. Deploy Code
```bash
sudo -u kalshi-bot -i << 'EOF'
cd /opt/kalshi-bot
git clone https://github.com/DavidEscotoDev/kalshi_bot.git .
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with production values
EOF
```

### 3. Private Key Setup
```bash
sudo mkdir -p /etc/kalshi/keys
sudo cp /path/to/private_key.pem /etc/kalshi/keys/
sudo chown kalshi-bot:kalshi-bot /etc/kalshi/keys/private_key.pem
sudo chmod 600 /etc/kalshi/keys/private_key.pem
# Update .env: KALSHI_PRIVATE_KEY_PATH=/etc/kalshi/keys/private_key.pem
```

### 4. Systemd Unit (`/etc/systemd/system/kalshi-bot.service`)
```ini
[Unit]
Description=Kalshi Trading Bot
Documentation=https://github.com/DavidEscotoDev/kalshi_bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=kalshi-bot
Group=kalshi-bot
WorkingDirectory=/opt/kalshi-bot
Environment=PATH=/opt/kalshi-bot/venv/bin:/usr/local/bin:/usr/bin:/bin
EnvironmentFile=/opt/kalshi-bot/.env
ExecStart=/opt/kalshi-bot/venv/bin/python main.py
Restart=on-failure
RestartSec=10
StartLimitIntervalSec=60
StartLimitBurst=3

# Resource limits
MemoryLimit=512M
CPUQuota=50%

# Security hardening
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/opt/kalshi-bot/logs /opt/kalshi-bot/data
ReadOnlyPaths=/etc/kalshi/keys

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=kalshi-bot

[Install]
WantedBy=multi-user.target
```

### 5. Enable & Start
```bash
sudo systemctl daemon-reload
sudo systemctl enable kalshi-bot
sudo systemctl start kalshi-bot
sudo systemctl status kalshi-bot
journalctl -u kalshi-bot -f
```

### 6. Log Rotation (`/etc/logrotate.d/kalshi-bot`)
```
/opt/kalshi-bot/logs/*.log {
    daily
    missingok
    rotate 14
    compress
    delaycompress
    notifempty
    create 640 kalshi-bot kalshi-bot
    sharedscripts
    postrotate
        systemctl reload kalshi-bot > /dev/null 2>&1 || true
    endscript
}
```

---

## Option 3: AWS ECS Fargate

### Task Definition (JSON)
```json
{
  "family": "kalshi-bot",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "512",
  "memory": "1024",
  "executionRoleArn": "arn:aws:iam::123456789:role/ecsTaskExecutionRole",
  "taskRoleArn": "arn:aws:iam::123456789:role/kalshi-bot-task-role",
  "containerDefinitions": [{
    "name": "kalshi-bot",
    "image": "123456789.dkr.ecr.us-east-1.amazonaws.com/kalshi-bot:latest",
    "essential": true,
    "environment": [
      {"name": "KALSHI_ENV", "value": "prod"},
      {"name": "SHADOW_MODE", "value": "false"},
      {"name": "LIVE_TRADE_CONFIRMED", "value": "1"},
      {"name": "LOG_LEVEL", "value": "INFO"}
    ],
    "secrets": [
      {"name": "KALSHI_API_KEY_ID", "valueFrom": "arn:aws:secretsmanager:us-east-1:123456789:secret:kalshi/api-key"},
      {"name": "KALSHI_PRIVATE_KEY_PATH", "valueFrom": "arn:aws:secretsmanager:us-east-1:123456789:secret:kalshi/private-key-path"}
    ],
    "logConfiguration": {
      "logDriver": "awslogs",
      "options": {
        "awslogs-group": "/ecs/kalshi-bot",
        "awslogs-region": "us-east-1",
        "awslogs-stream-prefix": "ecs"
      }
    },
    "healthCheck": {
      "command": ["CMD-SHELL", "python -c \"import config; config.Config.validate()\""],
      "interval": 30,
      "timeout": 10,
      "retries": 3,
      "startPeriod": 15
    },
    "mountPoints": [{
      "sourceVolume": "bot-data",
      "containerPath": "/app/data",
      "readOnly": false
    }],
    "volumesFrom": []
  }],
  "volumes": [{
    "name": "bot-data",
    "efsVolumeConfiguration": {
      "fileSystemId": "fs-12345678",
      "rootDirectory": "/kalshi-bot"
    }
  }]
}
```

### Required IAM Permissions (Task Role)
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["secretsmanager:GetSecretValue"],
      "Resource": [
        "arn:aws:secretsmanager:us-east-1:123456789:secret:kalshi/api-key*",
        "arn:aws:secretsmanager:us-east-1:123456789:secret:kalshi/private-key*"
      ]
    },
    {
      "Effect": "Allow",
      "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
      "Resource": "arn:aws:logs:us-east-1:123456789:log-group:/ecs/kalshi-bot:*"
    }
  ]
}
```

### Deploy via CLI
```bash
aws ecs register-task-definition --cli-input-json file://task-def.json
aws ecs update-service --cluster production --service kalshi-bot --task-definition kalshi-bot:2
```

---

## Secrets Management

| Method | Use Case | Example |
|--------|----------|---------|
| **Docker Secrets** | Swarm mode | `echo "key" | docker secret create kalshi_key -` |
| **AWS Secrets Manager** | ECS/EKS/Lambda | Store API key + key path |
| **HashiCorp Vault** | Self-hosted | `vault kv put secret/kalshi api_key=...` |
| **SOPS + Git** | GitOps | Encrypt `.env` in repo |
| **Mounted Files** | Simple VM | `/etc/kalshi/keys/private_key.pem` (chmod 600) |

**Never** commit `.env` or private keys to git.

---

## Monitoring & Alerting

### Key Metrics to Alert On

| Metric | Warning | Critical | Source |
|--------|---------|----------|--------|
| `bot_up` | — | 0 for 2m | Health check / process |
| `ws_connected` | 0 for 30s | 0 for 2m | WebSocket client |
| `kill_switch_triggered` | — | 1 | Audit log / counter |
| `circuit_breaker_open` | > 5/min | > 20/min | Circuit breaker state |
| `api_error_rate` | > 1% | > 5% | HTTP response codes |
| `shadow_trades_per_hour` | < 1 (expected > 5) | 0 for 1h | Shadow log / DB |
| `balance_usd` | < $500 | < $150 | Kill switch check |
| `db_size_mb` | > 500 | > 1000 | SQLite file size |

### Prometheus Rules (if using Prometheus)
```yaml
groups:
- name: kalshi-bot
  rules:
  - alert: BotDown
    expr: up{job="kalshi-bot"} == 0
    for: 2m
    labels: {severity: "critical"}
    annotations:
      summary: "Kalshi bot is down"
  - alert: KillSwitchTriggered
    expr: increase(kill_switch_triggered_total[5m]) > 0
    labels: {severity: "critical"}
    annotations:
      summary: "KILL SWITCH TRIGGERED - Manual intervention required"
  - alert: HighAPIErrorRate
    expr: rate(api_errors_total[5m]) / rate(api_requests_total[5m]) > 0.05
    for: 2m
    labels: {severity: "warning"}
    annotations:
      summary: "API error rate > 5%"
```

### Grafana Dashboard Panels
- Uptime (last 24h)
- WS reconnect count / hour
- API latency (p50, p95, p99)
- Orders placed (shadow vs live)
- Kill switch status (green/red)
- Balance over time
- Circuit breaker state timeline
- Fee accumulator value

---

## Health Checks

### Liveness (Process Alive)
```bash
# Docker healthcheck
python -c "import config; config.Config.validate()"

# Systemd
systemctl is-active kalshi-bot
```

### Readiness (Ready to Trade)
```python
# Add to main.py or separate endpoint
def readiness_check():
    checks = {
        "config": Config.validate(),
        "ws_connected": ws_client.connected_event.is_set(),
        "db_accessible": test_db_connection(),
        "balance_ok": kill_switch.get_cached_balance() > Config.KILL_SWITCH_MIN_BALANCE * 2,
    }
    return all(checks.values()), checks
```

---

## Rollback Procedure

### Shadow Mode → Live → Shadow Mode (Emergency)
```bash
# 1. Stop live bot
docker-compose stop kalshi-bot  # or systemctl stop kalshi-bot

# 2. Flip config
sed -i 's/SHADOW_MODE=False/SHADOW_MODE=True/' .env
sed -i 's/LIVE_TRADE_CONFIRMED=1/LIVE_TRADE_CONFIRMED=0/' .env

# 3. Restart in shadow
docker-compose up -d kalshi-bot

# 4. Verify shadow trades logging
tail -f logs/shadow_trades.log
```

### Code Rollback
```bash
git log --oneline -10
git checkout <previous-tag>
docker-compose up -d --build
# Or: systemctl restart kalshi-bot (after git pull in /opt/kalshi-bot)
```

### Database Rollback
```bash
# SQLite — copy backup
cp data/kalshi_shadow.db data/kalshi_shadow.db.rollback
# Or restore from backup
```

---

## Disaster Recovery

| Scenario | RTO | RPO | Procedure |
|----------|-----|-----|-----------|
| Bot process crash | < 1 min | 0 | systemd/docker restart |
| Host failure | < 5 min | 0 | Launch on standby host (same EFS/volume) |
| API key compromised | < 10 min | 0 | Rotate key in Kalshi dashboard → update secret → restart |
| DB corruption | < 30 min | 24h | Restore from daily backup |
| Kill switch triggered | Immediate | 0 | Investigate → fix root cause → manual restart |

**Backup Strategy**:
```bash
# Daily cron (systemd timer or cron)
#!/bin/bash
DATE=$(date +%F)
cp /opt/kalshi-bot/data/kalshi_shadow.db /backups/kalshi_shadow_${DATE}.db
cp /opt/kalshi-bot/logs/audit/audit-$(date +%F).log /backups/
# Upload to S3/GCS
aws s3 cp /backups/ s3://my-backups/kalshi-bot/ --recursive
```

---

## Security Hardening

### Container
- Non-root user (UID 1000)
- Read-only root filesystem (except logs/data)
- Drop all capabilities
- No new privileges

### VM
- Firewall: only outbound HTTPS (443) to Kalshi + monitoring
- SSH: key-only, non-standard port, fail2ban
- Automatic security updates
- Auditd for file access on keys

### Network
- Private subnet (no public IP)
- NAT Gateway for outbound
- VPC Flow Logs enabled
- Security group: egress 443 only

---

## Cost Optimization

| Component | Monthly Estimate (USD) | Notes |
|-----------|----------------------|-------|
| t3.micro (Linux) | $7-10 | Sufficient for single bot |
| ECS Fargate (0.5 vCPU, 1GB) | $15-20 | Per-task, pay per second |
| EFS (10GB) | $3 | For SQLite persistence |
| CloudWatch Logs | $0.50/GB | Log retention 30 days |
| Secrets Manager | $0.40/secret | 2 secrets = $0.80 |
| **Total (VM)** | **~$10-15** | |
| **Total (Fargate)** | **~$20-25** | |

---

## Troubleshooting Deployment

| Symptom | Likely Cause | Fix |
|---------|--------------|-----|
| Health check fails | Config validation error | Check logs: `Config.validate()` output |
| WS won't connect | Wrong env (demo/prod) or key perms | Verify `KALSHI_ENV`, `chmod 600` key |
| Orders not placing | `SHADOW_MODE=True` or `LIVE_TRADE_CONFIRMED=0` | Check `.env` |
| Kill switch triggers immediately | `KILL_SWITCH_MIN_BALANCE` > actual balance | Lower threshold or fund account |
| Permission denied on key | Wrong path or perms | `ls -la /path/to/key` → must be 600, owned by bot user |
| DB locked | Multiple processes | Ensure single instance; check `lsof data/*.db` |

---

## Related Documents

- [ARCHITECTURE.md](ARCHITECTURE.md) — System design
- [RUNBOOK.md](../RUNBOOK.md) — Operational procedures
- [CONTRIBUTING.md](CONTRIBUTING.md) — Development workflow