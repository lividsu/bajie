# Bajie

FastAPI version of the Feishu bot service.

## Run

```powershell
uv sync
uv run uvicorn main:app --host 0.0.0.0 --port 3002
```

Runtime configuration lives under `data/`. The service does not read `.env`.

```text
data/
  config.yaml
  config.example.yaml
  tenants/
    cpnj/
      config.yaml
      skills/
      cache/
```

By default the service starts Feishu WebSocket long connections for configured
tenants:

```yaml
service:
  run_mode: websocket
```

The existing HTTP callback endpoints remain available:

```text
/
/tenant/{tenant_id}/callback
```

Use `run_mode: webhook` if you only want HTTP callbacks. The root callback `/`
uses `service.default_tenant_id` from `data/config.yaml`.

## Service Config

Copy `data/config.example.yaml` to `data/config.yaml` and edit it:

```yaml
service:
  run_mode: websocket
  host: 0.0.0.0
  port: 3002
  debug: true
  tenants_dir: data/tenants
  default_tenant_id: cpnj_test
```

## Tenant Config

Each tenant has its own YAML file:

```text
data/tenants/{tenant_folder}/config.yaml
```

Use `data/tenants/example/config.example.yaml` as the detailed template. Tenant
callbacks can be configured in Feishu as:

```text
https://your-host/tenant/{tenant_id}/callback
```

The tenant `id` inside `config.yaml` is the id used in URLs, not necessarily the
folder name.

Tenant configs contain Feishu app credentials and model keys directly:

```yaml
id: acme
name: Acme

feishu:
  app_id: cli_xxx
  app_secret: replace-with-this-tenant-feishu-app-secret
  verification_token: replace-with-this-tenant-verification-token
  encrypt_key: replace-with-this-tenant-encrypt-key
  lark_host: https://open.feishu.cn
  bot_name: 八戒-Acme
  processing_emoji: OnIt
  done_emoji: DONE

llm:
  provider: gemini
  gemini_api_key: replace-with-this-tenant-gemini-api-key
  fast_model: gemini-3-flash-preview
```

When a task starts, Bajie adds `processing_emoji`. After the first response is
sent it removes that reaction and adds `done_emoji`.

## Tenant Skills

Each tenant can add or override skills under:

```text
data/tenants/{tenant_id}/skills/
```

Skills are resolved in this order:

```text
tenant skills > common skills in ./skills
```

That means tenant-only skills are added to the common set, and a tenant skill
with the same name can intentionally override a common skill. See:

```text
data/tenants/example/skills/brand_voice/
```

## Feishu Docs And Sheets

Bajie can read and write Feishu Docs and Sheets through the `feishu_docs` tool:

- `read_doc`
- `append_doc_text`
- `read_sheet`
- `write_sheet`
- `append_sheet`

The Feishu app must have the corresponding Docs/Sheets API permissions and
access to the target document or spreadsheet.

## Runtime Files

Tenant runtime files are isolated under:

```text
data/tenants/{tenant_id}/cache/images
data/tenants/{tenant_id}/cache/files
data/tenants/{tenant_id}/cache/generated_images
```

Real tenant data under `data/tenants/*` is ignored by Git. Only templates under
`data/tenants/example/` are intended to be committed.
