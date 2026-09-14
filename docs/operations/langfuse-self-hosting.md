# Self-hosting Langfuse v4

This runbook describes the supported low-scale deployment of Langfuse v4 for
Check-Manage AI Agent observability. It is intended for an internal network,
a staging environment, or a small production installation. Docker Compose is
officially supported, but it is not a high-availability design.

## Architecture

Run the two Langfuse application containers and the required storage services
on one private Docker network:

```text
HTTPS reverse proxy
        |
  Langfuse Web  ---- Postgres (OLTP)
        |  \------ Redis or Valkey (cache and queue)
        |  \------ ClickHouse (OLAP trace data)
        \--------- S3-compatible blob storage
                         ^
                  Langfuse Worker
```

- **Web** serves the console and API. It accepts trace batches and writes the
  raw events to blob storage before queueing work.
- **Worker** consumes the queue and ingests events into ClickHouse. Run it as a
  separate container; do not rely on the web process to perform background
  work.
- **PostgreSQL** stores transactional state, projects, users, and API keys.
- **Redis/Valkey** provides cache and queue services.
- **ClickHouse** stores traces, observations, and scores. Langfuse v4 requires
  ClickHouse 25.12 or newer; 26.4 is a suitable current target.
- **S3 or S3-compatible storage** stores incoming events, large payloads,
  multimodal attachments, and exports. MinIO is suitable for an isolated
  installation; a managed S3-compatible service is preferable for production.

Use the official Langfuse Compose file as the starting point and pin every
image to the exact Langfuse v4 release tested by the deployment. Do not use
the floating `latest` tag. The application images are normally
`langfuse/langfuse:<exact-version>` and
`langfuse/langfuse-worker:<exact-version>` (the `docker.langfuse.com/` registry
prefix is also supported). Record the chosen image digests in the deployment
change log.

PostgreSQL and ClickHouse must use UTC. A non-UTC timezone can produce
incorrect or empty time-range queries. Keep all services on the same Compose
network and expose only the reverse proxy to users. Do not publish Postgres,
Redis/Valkey, ClickHouse, or the S3 service to the public internet.

## Compose layout and persistent data

The Compose project should define persistent volumes or managed equivalents
for at least:

| Service | Persistent data |
|---|---|
| PostgreSQL | Langfuse metadata and configuration |
| ClickHouse | Trace, observation, and score tables plus metadata |
| Redis/Valkey | Queue and cache data; persistence is recommended for recovery |
| S3/MinIO | Raw event objects, attachments, and exports |

ClickHouse may use object storage for its data, but its local metadata still
needs a persistent volume. A container restart must not remove any of these
volumes. Keep Compose files, environment files, reverse-proxy configuration,
and backup scripts outside disposable container layers.

A minimal service relationship is:

```yaml
services:
  langfuse-web:
    image: langfuse/langfuse:<pinned-v4-version>
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
      clickhouse:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "wget", "--spider", "-q", "http://localhost:3000/api/public/health"]

  langfuse-worker:
    image: langfuse/langfuse-worker:<pinned-v4-version>
    ports:
      - "3030:3030"
    depends_on:
      langfuse-web:
        condition: service_started
    healthcheck:
      test: ["CMD", "wget", "--spider", "-q", "http://localhost:3030/api/health"]

  postgres:
    image: postgres:16
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $${POSTGRES_USER} -d $${POSTGRES_DB}"]

  redis:
    image: valkey/valkey:<pinned-version>

  clickhouse:
    image: clickhouse/clickhouse-server:<pinned-v4-compatible-version>

  object-store:
    image: minio/minio:<pinned-version>
```

Treat this as a relationship sketch, not a replacement for the release's
official Compose file. Copy the exact health checks, migrations, permissions,
S3 settings, and volume mounts from the pinned Langfuse release. Compose
`depends_on` controls startup ordering only; it is not a readiness guarantee.

## Docker Compose setup checklist

Use this checklist for a new low-scale installation. Keep the official Compose file
for the pinned release as the source of truth; the commands below are verification
steps, not a substitute for that file.

1. Create a private deployment directory, copy the pinned release's Compose file and
   `.env.example`, and record the exact image tags or digests.
2. Set the Langfuse stack variables in that environment file: application secrets,
   Postgres, Redis/Valkey, ClickHouse, and the S3-compatible event/media storage
   settings required by that release. Do not copy placeholder values into production.
3. Confirm Compose declares persistent volumes or managed storage for Postgres,
   Redis/Valkey, ClickHouse, and S3/MinIO. Run `docker compose config` and inspect the
   rendered mounts before starting anything.
4. Confirm all database and ClickHouse containers use UTC, and that only the HTTPS
   reverse proxy publishes a host port.
5. Start dependencies and applications with `docker compose up -d`, then inspect
   `docker compose ps` until Web and Worker report healthy/running.
6. Check the Web container's documented health and readiness endpoints. For the
   standard Web port, both checks should return a successful HTTP status:

   ```bash
    curl --fail --silent --show-error http://localhost:3000/api/public/health
    curl --fail --silent --show-error http://localhost:3000/api/public/ready
    curl --fail --silent --show-error http://localhost:3030/api/health
   ```

    If the reverse proxy is the only published Web endpoint, run the first two checks
    against the configured HTTPS Langfuse URL instead of publishing port 3000. Keep
    the Worker probe on the Worker container or its port 3030; do not send it to Web.
7. Check dependency and Worker health:

   ```bash
   docker compose ps
   docker compose logs --tail=100 langfuse-web langfuse-worker postgres redis clickhouse
   docker compose exec langfuse-worker sh -c 'ps -ef || true'
   ```

   Confirm the Worker is consuming jobs in its logs; a running container alone is not
   proof that ingestion is progressing.
8. Create a test project/key, send one non-sensitive test trace, and confirm it is
   visible in the console before connecting Check-Manage.
9. Configure Check-Manage separately, as described in [Check-Manage application
   variables](#check-manage-application-variables), then restart its backend and send
   one AI Chat turn. This verifies the full Web -> Worker -> ClickHouse path.

## Health and readiness

Use health checks for every dependency and probe readiness after startup:

1. Verify PostgreSQL accepts connections and ClickHouse accepts both HTTP and
   native protocol connections.
2. Verify Redis/Valkey accepts authenticated commands and the object store can
   create and read a test object in the Langfuse bucket.
3. Verify the Langfuse Web health endpoint and inspect migration logs.
4. Verify the Worker is running and consuming jobs; a healthy Web container
   alone does not prove that traces are being processed.
5. Send one non-sensitive test trace and confirm it appears in the console.

Keep a separate readiness check for the reverse proxy. Return an unhealthy
status until Web is ready, but do not expose dependency credentials or health
details in the public response.

## Network and HTTPS

Terminate TLS at an existing reverse proxy or load balancer. Set the public
Langfuse URL to the HTTPS URL users and the Check-Manage server will use, and
forward `Host`, `X-Forwarded-Proto`, and the usual client IP headers. Redirect
HTTP to HTTPS. Apply authentication, firewall rules, and rate limits at the
proxy; keep the Langfuse console and API private unless an explicit external
access policy exists.

The Check-Manage backend needs network access to the Langfuse URL. It sends
server-side SDK requests using the Langfuse public and secret keys; the browser
does not receive the secret key. If the service is internal, use an internal
DNS name and a certificate trusted by the backend host rather than disabling
TLS verification.

## Langfuse stack variables

The exact names and defaults can change between Langfuse releases. Start with
the pinned release's `.env.example` and keep this list as a review checklist.
The Web and Worker containers must receive the common database, queue,
ClickHouse, and S3 settings required by that release.

Typical v4 settings include:

```dotenv
# Application identity and authentication
DATABASE_URL=postgresql://langfuse:<password>@postgres:5432/langfuse
NEXTAUTH_URL=https://langfuse.example.internal
NEXTAUTH_SECRET=<random-secret>
SALT=<random-secret>
ENCRYPTION_KEY=<64-hex-character-key>

# Redis or Valkey
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_AUTH=<redis-password>

# ClickHouse (UTC)
CLICKHOUSE_URL=http://clickhouse:8123
CLICKHOUSE_MIGRATION_URL=clickhouse://clickhouse:9000
CLICKHOUSE_USER=langfuse
CLICKHOUSE_PASSWORD=<clickhouse-password>
CLICKHOUSE_DB=default
CLICKHOUSE_CLUSTER_ENABLED=false

# S3-compatible event and blob storage
LANGFUSE_S3_EVENT_UPLOAD_BUCKET=langfuse-events
LANGFUSE_S3_EVENT_UPLOAD_REGION=us-east-1
LANGFUSE_S3_EVENT_UPLOAD_ACCESS_KEY_ID=<access-key>
LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY=<secret-key>
```

Use the release documentation for optional endpoint, TLS, path-style, and
additional S3 bucket variables. Media storage variables use the release's
`LANGFUSE_S3_MEDIA_UPLOAD_*` family in addition to the event-upload family when that
release enables media uploads. Copy the exact variable names supported by the pinned
release; do not invent extra secret names or put secret values in this runbook. Do not
copy example credentials into a real environment. Generate `NEXTAUTH_SECRET`, `SALT`, and `ENCRYPTION_KEY` with a
cryptographically secure tool, store them in a secret manager, and back them
up separately from the Compose file. Changing them can invalidate sessions or
make encrypted data inaccessible.

## Check-Manage application variables

These are the `LANGFUSE_*` variables read by Check-Manage. They belong in
`server/.env`, the container environment, or the deployment secret manager.
The example file contains names and safe placeholders only.

These application variables are distinct from the Langfuse stack variables above:
the stack variables configure Langfuse Web/Worker and its databases/storage, while
these variables configure the Check-Manage exporter. Check-Manage needs only the
Langfuse HTTPS URL and project key pair; it does not need the stack's Postgres,
Redis/Valkey, ClickHouse, or S3 credentials.

| Variable | Default | Meaning |
|---|---:|---|
| `LANGFUSE_ENABLED` | `false` | Enables export only when both keys are also present. |
| `LANGFUSE_HOST` | `https://cloud.langfuse.com` | Base URL of self-hosted Langfuse, including scheme and no trailing slash. |
| `LANGFUSE_PROJECT_ID` | empty | Langfuse project ID used to build project-scoped trace links. |
| `LANGFUSE_PUBLIC_KEY` | empty | Langfuse project public key. |
| `LANGFUSE_SECRET_KEY` | empty | Langfuse project secret key; never expose it to the browser or commit it. |
| `LANGFUSE_ENVIRONMENT` | `development` | Environment label attached to exported observations, such as `staging` or `production`. |
| `LANGFUSE_CAPTURE_CONTENT` | `false` | When `false`, no raw prompt, response, or tool content is sent. When `true`, content is exported after sensitive-value redaction. |
| `LANGFUSE_SAMPLE_RATE` | `1.0` | Stable sampling ratio for complete Traces from `0.0` to `1.0`; sampling is decided once per Trace. |
| `LANGFUSE_QUEUE_SIZE` | `100` | In-memory exporter buffer capacity before new observations are dropped. |
| `LANGFUSE_FLUSH_INTERVAL_SECONDS` | `5.0` | Maximum exporter flush interval; `0` flushes without a timer. |

Set `LANGFUSE_ENABLED=true` only after the self-hosted URL and project keys
work. Restart the Check-Manage backend after changing these variables. Export
failure must not block an AI conversation; the exporter uses a bounded buffer
and logs failures for operators to investigate.

## Create and rotate keys

1. Open the Langfuse console through its private HTTPS URL.
2. Create or select a project dedicated to this Check-Manage environment.
3. Create a project API key pair and copy the public and secret values once.
4. Store the secret in the deployment secret manager and set
    `LANGFUSE_PROJECT_ID`, `LANGFUSE_PUBLIC_KEY`, and `LANGFUSE_SECRET_KEY` on the backend only.
5. Set `LANGFUSE_HOST` to the exact HTTPS base URL and enable export.
6. Send a test AI Chat turn and confirm a trace is visible.

Use separate projects and key pairs for development, staging, and production.
For rotation, create the replacement key first, deploy it, verify ingestion,
then revoke the old key. Never put either key in Markdown, screenshots, issue
comments, browser storage, or client-side JavaScript.

## Privacy, content capture, and sampling

By default Check-Manage exports metadata and timing information without prompt
or response content. `LANGFUSE_CAPTURE_CONTENT=false` is the recommended
production setting and sends no raw content fields to Langfuse. When content capture is enabled, the exporter recursively
redacts values under keys resembling passwords, tokens, secrets, API keys,
authorization headers, and cookies, and masks Bearer values. Redaction is a
last safety net, not a substitute for a data classification policy.

Before enabling content capture, document the lawful basis, access roles,
retention period, and deletion process for prompts, responses, tool arguments,
file names, and generated output. Do not send regulated or customer content to
Langfuse unless the deployment has been approved for that data class. Sampling
is stable at Trace level: `0` disables export, `1` exports all eligible traces,
and values in between keep or drop complete traces rather than individual
observations. Trace IDs are deterministic 32-character lowercase hexadecimal values.
Keep
sampling high enough to diagnose failures but low enough to satisfy the data
minimization policy.

## Retention, backup, and restore

Langfuse retention is an operational policy, not a replacement for backup.
Define retention separately for traces, raw event objects, attachments, and
application audit data. Configure the supported Langfuse retention mechanism
for the pinned release, and apply object-store lifecycle rules that match it.

Back up all of the following together:

- PostgreSQL logical or physical backup;
- ClickHouse data and metadata, using a tested snapshot or the supported
  ClickHouse backup procedure;
- S3/MinIO event and attachment objects;
- Redis/Valkey only when queue recovery is required (it is cache data, but an
  in-flight queue may affect recovery time);
- the pinned Compose files, image digests, environment variable names, and
  secret-manager references.

For restore, stop Web and Worker, restore Postgres, ClickHouse, and object
storage from a consistent point, restore the Compose configuration, then start
dependencies before Web and Worker. Run migrations only with the release's
documented command, verify health and object access, and send a test trace.
Do not restore a database without its corresponding raw event objects.
Perform a restore drill at least once per release cycle and record the recovery
time and any missing data.

Example backup checks (replace the output paths and ClickHouse/object-store backup
commands with the pinned release's supported tools):

```bash
mkdir -p backups/$(date +%Y%m%d-%H%M%S)
docker compose exec -T postgres pg_dump --format=custom --file=- "$POSTGRES_DB" > backups/postgres.dump
docker compose exec clickhouse clickhouse-client --query 'SELECT count() FROM system.tables'
docker compose ps postgres clickhouse object-store
```

Keep the PostgreSQL dump, tested ClickHouse backup/snapshot, and S3/MinIO object
backup from the same recovery point. Verify each artifact is readable before
declaring the backup usable; a container status check alone is not a backup test.

Example verification sequence after a restore (adapt service names and backup tools
to the pinned release):

```bash
docker compose up -d postgres redis clickhouse object-store
docker compose exec postgres pg_isready
docker compose exec clickhouse clickhouse-client --query 'SELECT 1'
docker compose up -d langfuse-web langfuse-worker
curl --fail --silent --show-error https://langfuse.example.internal/api/public/health
curl --fail --silent --show-error https://langfuse.example.internal/api/public/ready
curl --fail --silent --show-error http://localhost:3030/api/health
docker compose logs --tail=100 langfuse-web langfuse-worker
```

Then create or query a known test trace and verify that an object can be read from
the restored event/media bucket. Do not treat successful container startup as proof
that Postgres, ClickHouse, Redis/Valkey, and S3 data were restored consistently.

## Upgrades

1. Read the Langfuse v4 release notes and the v3-to-v4 or point-release upgrade
   notes applicable to the current version.
2. Check required PostgreSQL, ClickHouse, Redis/Valkey, and object-store
   versions; confirm all are UTC.
3. Take and verify backups of Postgres, ClickHouse, and S3/blob storage.
4. Test the exact new image tags and migrations in a disposable clone.
5. Stop or drain Worker, then stop Web if the release requires downtime.
6. Upgrade storage components in the release-documented order: preserve/upgrade
   Postgres, Redis/Valkey, ClickHouse, and S3 compatibility before application
   migrations; do not guess an order when the release notes specify one.
7. Run migrations once, then start the new Web and Worker images at the same exact
   version.
8. Verify readiness, login, a new trace, historical trace queries, and worker
   processing before reopening traffic.

Never run Web and Worker from different Langfuse release versions against the
same databases. Keep the previous images available for rollback, but do not
blindly roll back after a destructive migration; restore the tested backup or
follow the release-specific rollback guidance.

## Troubleshooting checklist

- Web is healthy but no traces appear: check project keys, `LANGFUSE_HOST`,
  S3 writes, Redis queue health, and Worker logs.
- Traces stop at ingestion: check ClickHouse credentials, UTC timezone, and
  Worker connectivity.
- Console redirects to HTTP: check `NEXTAUTH_URL` and forwarded HTTPS headers.
- Old data disappears: check object-store lifecycle rules and trace retention.
- Content appears unexpectedly: disable `LANGFUSE_CAPTURE_CONTENT`, rotate any
  exposed key, and investigate access logs and Langfuse project permissions.
