# Mercadona Shopping

Shop groceries on Mercadona (Spain). Search products, manage shopping lists, add/remove items.

## Quick Start

```bash
# 1. Setup (interactive — enter email, password, postal code, capsolver API key)
python3 scripts/mercadona.py setup

# 2. Login (authenticate — session lasts ~7 days)
python3 scripts/mercadona.py login

# 3. Shop!
python3 scripts/mercadona.py search "leche entera"
python3 scripts/mercadona.py add "cafe en grano"
python3 scripts/mercadona.py show
```

## Setup Options

### Interactive (recommended)

```bash
python3 scripts/mercadona.py setup
```

Collects: email, password, postal code, Capsolver API key. Warehouse code is resolved automatically from the postal code.

### Manual

Create `credentials.json` (see `credentials.example.json`):

```json
{
  "email": "your-email@example.com",
  "password": "your-password",
  "postal_code": "03015",
  "warehouse_code": "alc1",
  "capsolver_api_key": "CAP-YOUR-KEY-HERE"
}
```

> `warehouse_code` is resolved automatically from `postal_code` during setup. If setting up manually, use the `setup` command or find it via browser DevTools (look for `wh` parameter in network requests).

### Environment Variables

All credentials can be provided via environment variables (highest priority):

| Variable | Description |
|----------|-------------|
| `MERCADONA_EMAIL` | Mercadona account email |
| `MERCADONA_PASSWORD` | Mercadona account password |
| `MERCADONA_WAREHOUSE_CODE` | Warehouse code (e.g. `alc1`) |
| `MERCADONA_POSTAL_CODE` | Postal code (e.g. `03015`) |
| `MERCADONA_CAPSOLVER_API_KEY` | Capsolver API key |

Algolia and reCAPTCHA credentials use public defaults from Mercadona's frontend. Override if needed:

| Variable | Default |
|----------|---------|
| `MERCADONA_ALGOLIA_APP_ID` | `7UZJKL1DJ0` |
| `MERCADONA_ALGOLIA_API_KEY` | (from Mercadona frontend) |
| `MERCADONA_RECAPTCHA_SITEKEY` | (from Mercadona frontend) |

## Getting a Capsolver API Key

1. Sign up at [capsolver.com](https://capsolver.com)
2. Add funds (minimum ~$2)
3. Copy your API key from the dashboard

Each login session costs approximately $0.003 and typically lasts ~7 days.

## Usage

```bash
# Search products
python3 scripts/mercadona.py search "leche entera"

# Add to list (interactive)
python3 scripts/mercadona.py add "cafe en grano"

# Add by product ID
python3 scripts/mercadona.py add-id 13594
python3 scripts/mercadona.py add-id 13594 3   # 3 units

# Remove from list
python3 scripts/mercadona.py remove "pan"

# Show current list
python3 scripts/mercadona.py show

# List all shopping lists
python3 scripts/mercadona.py lists
```

## How It Works

1. **Warehouse resolution** — postal code → warehouse code via Mercadona API
2. **Product search** — Algolia public read-only API (index varies by warehouse)
3. **Login** — Capsolver solves reCAPTCHA → Mercadona auth API → JWT token
4. **Shopping list management** — Mercadona authenticated API with Bearer token
5. **Checkout** — always manual in the Mercadona app or website

## Requirements

- Python 3.9+
- Capsolver account (for reCAPTCHA solving)
- Mercadona account

## Security

- `credentials.json` is created with `chmod 600` during setup
- Auth tokens stored locally in `auth_token.json`
- Algolia API key is public and read-only
- No payment automation — you always confirm purchases manually

## License

MIT
