---
name: mercadona-shopping
description: Shop groceries on Mercadona (Spain) — search products, manage shopping lists, add/remove items
homepage: https://github.com/luxeon/mercadona-shopping
metadata: {"openclaw":{"emoji":"🛒","requires":{"bins":["python3"]},"files":["scripts/mercadona.py"]}}
---

# Mercadona Shopping Skill

Manage a shopping list on Mercadona (Spain) — search products, add/remove items.

## Architecture

- **Setup** → Interactive `setup` command collects email, password, postal code. Warehouse code resolved automatically via Mercadona API.
- **Product search** → Algolia API (public read-only key, hardcoded with override)
- **Auth** → capsolver.com solves reCAPTCHA v2 invisible (~$0.003/session)
- **Shopping lists** → Server-side API with Bearer token (authenticated)
- **Review/checkout** → User does manually in app/web

## Credentials

Priority: **env vars** → `credentials.json` → **hardcoded defaults** (Algolia/reCAPTCHA).

### Setup (recommended)

```bash
python3 scripts/mercadona.py setup
```

Interactive prompt collects email, password, postal code, capsolver API key. Warehouse code resolved automatically.

### Manual setup

Create `credentials.json` (see `credentials.example.json`).

### Environment variables

| Variable | Description |
|----------|-------------|
| `MERCADONA_EMAIL` | Mercadona account email |
| `MERCADONA_PASSWORD` | Mercadona account password |
| `MERCADONA_WAREHOUSE_CODE` | Warehouse code (e.g. `alc1`) |
| `MERCADONA_POSTAL_CODE` | Postal code (e.g. `03015`) |
| `MERCADONA_CAPSOLVER_API_KEY` | Capsolver API key |
| `MERCADONA_ALGOLIA_APP_ID` | Override default Algolia App ID |
| `MERCADONA_ALGOLIA_API_KEY` | Override default Algolia API key |
| `MERCADONA_RECAPTCHA_SITEKEY` | Override default reCAPTCHA sitekey |

## Quick Reference

```bash
# First-time setup
python3 scripts/mercadona.py setup

# Authenticate (run once, session persists ~7 days)
python3 scripts/mercadona.py login

# Search products
python3 scripts/mercadona.py search "leche entera" --limit 3

# Add product (interactive: search → select → add)
python3 scripts/mercadona.py add "cafe en grano"

# Add by product ID directly
python3 scripts/mercadona.py add-id 13594
python3 scripts/mercadona.py add-id 13594 3        # add 3 units

# Remove product
python3 scripts/mercadona.py remove "pan"
python3 scripts/mercadona.py remove-id 22388

# Show list contents
python3 scripts/mercadona.py show

# List all shopping lists
python3 scripts/mercadona.py lists
```

## Non-interactive mode

When stdin is not a TTY (e.g. agent invocation), `add` and `remove` auto-select the first match. Use `add-id` / `remove-id` for precise control.

## Security & Privacy

- **Passwords:** Stored only in local `credentials.json` (user-created, never uploaded). File permissions set to 600.
- **API Keys:** Algolia App ID and API key are public read-only values from Mercadona's frontend. Capsolver API key is user-provided.
- **Session:** Auth tokens saved locally in `auth_token.json`, valid ~7 days.
- **Data sent externally:** Search queries → Algolia; reCAPTCHA challenges → Capsolver; login/shopping list data → Mercadona servers.
- **No payment automation:** User always reviews and pays manually in the Mercadona app.

## Trust Statement

By using this skill, your search queries are sent to Algolia, reCAPTCHA challenges are sent to Capsolver, and your Mercadona credentials are sent to Mercadona's servers. Only install if you trust these services.

## API Reference

### Warehouse Resolution

- `PUT /api/postal-codes/actions/change-pc/` → `x-customer-wh` header contains warehouse code
- Input: `{"new_postal_code": "03015"}`

### Product Search

- Index: `products_prod_{warehouse_code}_es` (dynamic, based on warehouse)
- Algolia credentials: hardcoded defaults with override via env/config

### Auth

- reCAPTCHA v2 invisible → solved via Capsolver
- `POST /api/auth/tokens/?lang=es&wh={warehouse_code}`

### Shopping Lists (Bearer auth)

Base: `/api/customers/{customer_id}/shopping-lists/`

| Action | Method | Endpoint | Body |
|--------|--------|----------|------|
| List all | GET | `/` | — |
| Get detail | GET | `/{list_id}/` | — |
| Add product | POST | `/{list_id}/products/` | `{"merca_code":"123"}` |
| Remove product | DELETE | `/{list_id}/products/{product_id}/` | — |

## Notes

- **reCAPTCHA cost:** ~$0.003 per session via capsolver. Session lasts ~7 days.
- **Language:** Product names are in Spanish. Translate user requests to Spanish for search.
