# Matrix E2EE Self-Hosting Guide (RPi 5)

## Architektur

```
┌─────────────────────────────────────────────────────┐
│ RPi 5 (ARM64, 8GB RAM)                               │
├─────────────────────────────────────────────────────┤
│  ┌──────────┐   ┌──────────┐   ┌────────────────┐  │
│  │ Postgres │   │ Synapse  │   │ Element Web    │  │
│  │ (matrix) │◄──│ (:8008)  │   │ PWA (:8080)    │  │
│  └──────────┘   └──────────┘   └────────────────┘  │
│         │              │                            │
│         └── volumes ───┴── (persistent, reboot-safe)│
├─────────────────────────────────────────────────────┤
│  Caddy (Reverse Proxy, Auto-HTTPS via Let's Encrypt)│
└─────────────────────────────────────────────────────┘
```

## Komponenten-Entscheidungen

| Komponente | Wahl | Begründung |
|---|---|---|
| Server | **Synapse** | Reference-Implementation, 4.6k★, Python+Rust, gut gepflegt |
| DB | **PostgreSQL** | Persistenz, ACID, vertraut (bestehende Cactus-DB) |
| Client | **Element Web** | PWA-fähig, E2EE-Support, auf allen Geräten |
| Proxy | **Caddy** | Auto-HTTPS, einfach zu konfigurieren |

## Installation

### 1. Docker Compose Setup

```yaml
# docker-compose.matrix.yml
version: "3.8"

services:
  postgres:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_DB: matrix
      POSTGRES_USER: matrix
      POSTGRES_PASSWORD: ${MATRIX_DB_PASSWORD}
      POSTGRES_INITDB_ARGS: "--encoding=UTF8 --locale=C"
    volumes:
      - matrix_postgres:/var/lib/postgresql/data
    networks:
      - matrix

  synapse:
    image: matrixdotorg/synapse:latest
    restart: unless-stopped
    depends_on:
      - postgres
    environment:
      SYNAPSE_SERVER_NAME: ${MATRIX_DOMAIN}
      SYNAPSE_REPORT_STATS: "no"
      POSTGRES_HOST: postgres
      POSTGRES_PORT: 5432
      POSTGRES_DB: matrix
      POSTGRES_USER: matrix
      POSTGRES_PASSWORD: ${MATRIX_DB_PASSWORD}
    volumes:
      - matrix_synapse:/data
    ports:
      - "8008:8008"
    networks:
      - matrix

  element:
    image: vectorim/element-web:latest
    restart: unless-stopped
    ports:
      - "8080:80"
    volumes:
      - ./element-config.json:/app/config.json:ro
    networks:
      - matrix

volumes:
  matrix_postgres:
  matrix_synapse:

networks:
  matrix:
    driver: bridge
```

### 2. Element Web Config

```json
{
  "default_server_config": {
    "m.homeserver": {
      "base_url": "https://matrix.example.com",
      "server_name": "example.com"
    },
    "m.identity_server": {
      "base_url": "https://vector.im"
    }
  },
  "disable_custom_urls": false,
  "disable_guests": false,
  "disable_login_language_selector": false,
  "default_theme": "dark",
  "brand": "Cactus Matrix",
  "integrations_ui_url": "https://scalar.vector.im/",
  "integrations_rest_url": "https://scalar.vector.im/api",
  "show_labs_settings": true
}
```

### 3. Caddy Reverse Proxy (Auto-HTTPS)

```bash
# Caddyfile
matrix.example.com {
    reverse_proxy localhost:8008
}

element.example.com {
    reverse_proxy localhost:8080
}
```

### 4. Erste Schritte

```bash
# .env erstellen
echo "MATRIX_DB_PASSWORD=$(openssl rand -hex 32)" >> .env
echo "MATRIX_DOMAIN=matrix.example.com" >> .env

# Server starten
docker compose -f docker-compose.matrix.yml up -d

# Admin-User registrieren
docker exec -it matrix-synapse register_new_matrix_user \
  -u admin -p <password> -a \
  -c /data/homeserver.yaml http://localhost:8008

# Element Web öffnen
# → https://element.example.com
# → Login mit erstelltem User
# → E2EE wird automatisch aktiviert (Olm/Megolm)
```

## E2EE: Was ist wichtig?

1. **HTTPS ist Pflicht**: E2EE funktioniert nur über HTTPS
2. **Olm/Megolm**: Cryptographische Protokolle für E2EE
3. **Cross-Signing**: Geräte-Verifikation für neue Devices
4. **Key Backup**: Verschlüsselte Sicherung der Session-Keys

## PWA auf diversen Endgeräten

| Gerät | Methode |
|---|---|
| **Desktop (Chrome/Edge)** | URL öffnen → Install-Button in der Adressleiste |
| **iOS Safari** | Teilen → "Zum Home-Bildschirm" |
| **Android Chrome** | Menü → "App installieren" |
| **Native Apps** | Element X (iOS/Android) mit selbst-gehostetem Server |

## Persistenz sicherstellen

```bash
# Docker volumes überleben Reboots automatisch:
# - matrix_postgres (Datenbank)
# - matrix_synapse (Media, Config, Keys)

# Backup erstellen (empfohlen: täglich via cron)
docker exec matrix-postgres pg_dump -U matrix matrix | \
  gzip > backup/matrix_$(date +%Y%m%d).sql.gz

# Restore
gunzip -c backup/matrix_20260906.sql.gz | \
  docker exec -i matrix-postgres psql -U matrix matrix
```

## Telegram-Bot zu Matrix-Bot migrieren

1. Matrix-User für Bot erstellen (z.B. `@needle:matrix.example.com`)
2. Access-Token generieren
3. Bot-API nutzen (matrix-nio oder simple matrix SDK)
4. Gleiche 7 CRUD-Tools, gleicher Semantic Router

## Vergleich: Telegram vs. Matrix

| Feature | Telegram | Matrix (self-hosted) |
|---|---|---|
| E2EE | Nur Secret Chats | Standard (Olm/Megolm) |
| Self-hosted | ❌ | ✅ |
| Datenschutz | Metadaten bei Telegram | Alles auf eigenem Server |
| Bot-API | BotFather + Token | Access-Token + SDK |
| Multi-Device | Cloud-Chats | E2EE über alle Devices |
| Kosten | Kostenlos | Strom (~3-5W RPi 5) |

## Nächste Schritte

1. **Domain + DNS**: A-Record auf RPi-IP, ggf. DDNS
2. **Ports öffnen**: 80/443 für Caddy (HTTPS)
3. **Synapse deployen**: docker-compose.matrix.yml
4. **Element Web deployen**: PWA-Config
5. **User registrieren**: Admin + normale User
6. **Backup-Cron einrichten**: Tägliche pg_dumps
7. **Bot migrieren**: Telegram → Matrix (matrix-nio)

## Sicherheitshinweise

- **Registrierung deaktivieren** nach Setup (`enable_registration: false`)
- **Federation** nur aktivieren wenn gewünscht (`enable_federation: false` default)
- **Rate-Limiting** konfigurieren
- **Regelmäßige Updates**: `docker compose pull && docker compose up -d`
- **Secrets**: `registration_shared_secret` sicher aufbewahren
