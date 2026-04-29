#!/usr/bin/env python3
"""Mercadona shopping CLI — unified, simple, fail-safe.

Commands:
  search <query>              Search products
  lists                       Show all shopping lists
  show [list_id]              Show list contents (auto-detect if one list)
  add <query>                 Search product and add to list (interactive)
  add-id <product_id> [qty]   Add product by ID directly
  remove <query>              Search and remove from list
  remove-id <product_id>      Remove product by ID
  login                       Authenticate (if session expired)
  setup                       Interactive setup (credentials + warehouse)

Credentials priority: env vars → credentials.json → hardcoded defaults
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# ---------------------------------------------------------------------------
# Defaults (public values from Mercadona's frontend JS bundle)
# ---------------------------------------------------------------------------

DEFAULT_ALGOLIA_APP_ID = "7UZJKL1DJ0"
DEFAULT_ALGOLIA_API_KEY = "9d8f2e39e90df472b4f2e559a116fe17"
DEFAULT_RECAPTCHA_SITEKEY = "6Lc-4Y0pAAAAABQGP1P6zM0Sh5SkdtVTfk8FHYOt"

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CRED_PATH = os.path.join(BASE_DIR, "credentials.json")
TOKEN_PATH = os.path.join(BASE_DIR, "auth_token.json")


# ---------------------------------------------------------------------------
# Credentials loading (env vars → file → defaults)
# ---------------------------------------------------------------------------

def _cred_env(name: str) -> str | None:
    return os.environ.get(name)


def load_credentials() -> dict:
    """Load credentials: env vars first, then credentials.json, then defaults."""
    cred: dict = {}

    # Hardcoded public defaults (lowest priority)
    cred["algolia_app_id"] = DEFAULT_ALGOLIA_APP_ID
    cred["algolia_api_key"] = DEFAULT_ALGOLIA_API_KEY
    cred["recaptcha_sitekey"] = DEFAULT_RECAPTCHA_SITEKEY

    # File override
    if os.path.exists(CRED_PATH):
        try:
            with open(CRED_PATH) as f:
                file_cred = json.load(f)
            cred.update({k: v for k, v in file_cred.items() if v})
        except (json.JSONDecodeError, IOError) as e:
            print(f"⚠️  Could not parse credentials.json: {e}")

    # Env var override (highest priority)
    env_map = {
        "email": "MERCADONA_EMAIL",
        "password": "MERCADONA_PASSWORD",
        "warehouse_code": "MERCADONA_WAREHOUSE_CODE",
        "postal_code": "MERCADONA_POSTAL_CODE",
        "capsolver_api_key": "MERCADONA_CAPSOLVER_API_KEY",
        "algolia_app_id": "MERCADONA_ALGOLIA_APP_ID",
        "algolia_api_key": "MERCADONA_ALGOLIA_API_KEY",
        "recaptcha_sitekey": "MERCADONA_RECAPTCHA_SITEKEY",
    }
    for key, env_name in env_map.items():
        val = _cred_env(env_name)
        if val:
            cred[key] = val

    return cred


def require_cred(cred: dict, key: str, label: str | None = None) -> str:
    """Get a required credential or exit with error."""
    val = cred.get(key)
    if not val:
        name = label or key
        print(f"❌ {name} not configured. Run: python3 mercadona.py setup")
        sys.exit(1)
    return val


# ---------------------------------------------------------------------------
# Warehouse resolution (postal code → warehouse code)
# ---------------------------------------------------------------------------

def resolve_warehouse(postal_code: str) -> str | None:
    """Resolve postal code to warehouse code via Mercadona API."""
    url = "https://tienda.mercadona.es/api/postal-codes/actions/change-pc/"
    payload = json.dumps({"new_postal_code": postal_code}).encode()
    req = urllib.request.Request(url, data=payload, method="PUT", headers={
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        "Origin": "https://tienda.mercadona.es",
        "Referer": "https://tienda.mercadona.es/",
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.headers.get("x-customer-wh")
    except Exception as e:
        print(f"❌ Could not resolve postal code: {e}")
        return None


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def load_auth() -> dict | None:
    """Load auth token if exists and not expired."""
    if not os.path.exists(TOKEN_PATH):
        return None
    try:
        with open(TOKEN_PATH) as f:
            data = json.load(f)
        if time.time() - data.get("timestamp", 0) > 7 * 86400:
            return None
        return data
    except (json.JSONDecodeError, IOError):
        return None


def save_auth(auth_result: dict, cookies: dict | None = None):
    """Save auth result to file."""
    data = {
        "timestamp": time.time(),
        "auth_result": auth_result,
        "cookies": cookies or {},
    }
    with open(TOKEN_PATH, "w") as f:
        json.dump(data, f, indent=2)


def get_auth_headers() -> tuple[dict | None, str | None]:
    """Get auth headers for API calls. Returns (headers, customer_id) or (None, None)."""
    auth = load_auth()
    if not auth or "auth_result" not in auth:
        return None, None
    token = auth["auth_result"]["access_token"]
    customer_id = auth["auth_result"]["customer_id"]
    cookies = auth.get("cookies", {})
    cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Cookie": cookie_str,
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        "Origin": "https://tienda.mercadona.es",
    }
    return headers, customer_id


def ensure_auth() -> tuple[dict, str]:
    """Ensure we have valid auth, exit if not."""
    headers, customer_id = get_auth_headers()
    if not headers:
        print("❌ No active session. Run: python3 mercadona.py login")
        sys.exit(1)
    return headers, customer_id


# ---------------------------------------------------------------------------
# Product Search (Algolia)
# ---------------------------------------------------------------------------

def search_products(query: str, limit: int = 5) -> list[dict]:
    """Search products via Algolia."""
    cred = load_credentials()
    algolia_key = cred.get("algolia_api_key", "")
    algolia_app_id = cred.get("algolia_app_id", "")
    warehouse = require_cred(cred, "warehouse_code", "Warehouse code")

    if not algolia_key or not algolia_app_id:
        print("❌ Algolia credentials not configured.")
        sys.exit(1)

    # Dynamic index based on warehouse code
    algolia_index = f"products_prod_{warehouse}_es"
    algolia_url = f"https://{algolia_app_id.lower()}-dsn.algolia.net/1/indexes/{algolia_index}/query"

    params = urllib.parse.urlencode({
        "query": query,
        "clickAnalytics": "true",
        "analyticsTags": '["web"]',
        "getRankingInfo": "true",
        "analytics": "true",
    })
    payload = json.dumps({"params": params}).encode()

    req = urllib.request.Request(
        algolia_url,
        data=payload,
        headers={
            "X-Algolia-API-KEY": algolia_key,
            "X-Algolia-Application-Id": algolia_app_id,
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://tienda.mercadona.es",
            "Referer": "https://tienda.mercadona.es/",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print(f"❌ Algolia index '{algolia_index}' not found. Check warehouse_code.")
        else:
            print(f"❌ Search failed: HTTP {e.code}")
        return []
    except Exception as e:
        print(f"❌ Search failed: {e}")
        return []

    results = []
    for hit in data.get("hits", [])[:limit]:
        price_info = hit.get("price_instructions", {})
        results.append({
            "id": hit["id"],
            "name": hit.get("display_name", hit.get("slug", "")),
            "price": price_info.get("bulk_price", price_info.get("unit_price", "N/A")),
            "unit_size": price_info.get("unit_size", ""),
            "size_format": price_info.get("size_format", ""),
        })
    return results


def format_product(p: dict) -> str:
    """Format product for display."""
    size = f" ({p['unit_size']} {p['size_format']})" if p["unit_size"] else ""
    price = p["price"]
    if isinstance(price, (int, float)):
        price = f"{price:.2f}"
    return f"ID: {p['id']} | {p['name']}{size} | {price}€"


# ---------------------------------------------------------------------------
# API helper with retry
# ---------------------------------------------------------------------------

def api_call(method: str, path: str, body: dict | None = None, retries: int = 1):
    """Make API call to Mercadona with retry on 5xx/429."""
    headers, customer_id = ensure_auth()
    base = f"https://tienda.mercadona.es/api/customers/{customer_id}/shopping-lists/"
    url = base + path
    data = json.dumps(body).encode() if body else None

    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                try:
                    return resp.status, json.loads(resp.read())
                except json.JSONDecodeError:
                    return resp.status, None
        except urllib.error.HTTPError as e:
            if e.code == 401:
                print("❌ Session expired. Run: python3 mercadona.py login")
                return e.code, None
            if e.code in (429, 502, 503, 504) and attempt < retries:
                wait = 2 ** attempt
                print(f"⚠️  HTTP {e.code}, retrying in {wait}s...")
                time.sleep(wait)
                continue
            err = e.read().decode()[:300] if e.fp else str(e)
            return e.code, err
        except Exception as e:
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
            return 0, str(e)
    return 0, "Max retries exceeded"


# ---------------------------------------------------------------------------
# Shopping Lists API
# ---------------------------------------------------------------------------

def get_lists() -> list[dict]:
    """Get all shopping lists."""
    status, data = api_call("GET", "")
    if status != 200:
        if status == 401:
            print("❌ Session expired. Run: python3 mercadona.py login")
        else:
            print(f"❌ Error {status}: {data}")
        return []
    return data.get("shopping_lists", []) if isinstance(data, dict) else []


def get_list(list_id: str) -> dict:
    """Get single list details."""
    status, data = api_call("GET", f"{list_id}/")
    if status != 200:
        print(f"❌ Error {status}: {data}")
        return {}
    return data if isinstance(data, dict) else {}


def get_default_list_id() -> str | None:
    """Get list ID if there's only one list."""
    lists = get_lists()
    if len(lists) == 0:
        print("❌ No shopping lists found. Create one in the Mercadona app.")
        return None
    if len(lists) == 1:
        return lists[0]["id"]
    print("❌ Multiple lists found. Specify one:")
    for lst in lists:
        print(f"   {lst['id']} | {lst['name']}")
    return None


def add_product(list_id: str, product_id: str) -> bool:
    """Add product to list."""
    status, data = api_call("POST", f"{list_id}/products/", {"merca_code": str(product_id)})
    if status in (200, 201):
        return True
    if status == 409:
        print(f"⚠️  Product {product_id} already in list")
        return True
    print(f"❌ Failed to add {product_id}: {status} {data}")
    return False


def remove_product(list_id: str, product_id: str) -> bool:
    """Remove product from list."""
    status, data = api_call("DELETE", f"{list_id}/products/{product_id}/")
    if status in (200, 204):
        return True
    print(f"❌ Failed to remove {product_id}: {status} {data}")
    return False


# ---------------------------------------------------------------------------
# Authentication (capsolver)
# ---------------------------------------------------------------------------

def login_with_capsolver() -> bool:
    """Authenticate using capsolver for reCAPTCHA."""
    cred = load_credentials()
    capsolver_key = require_cred(cred, "capsolver_api_key", "Capsolver API key")
    sitekey = cred.get("recaptcha_sitekey", DEFAULT_RECAPTCHA_SITEKEY)

    print("[1/3] Solving reCAPTCHA via capsolver...")
    create_payload = {
        "clientKey": capsolver_key,
        "task": {
            "type": "ReCaptchaV2TaskProxyless",
            "websiteURL": "https://tienda.mercadona.es/",
            "websiteKey": sitekey,
        },
    }

    req = urllib.request.Request(
        "https://api.capsolver.com/createTask",
        data=json.dumps(create_payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
    except Exception as e:
        print(f"❌ Capsolver createTask failed: {e}")
        return False

    task_id = result.get("taskId")
    if not task_id:
        print(f"❌ No taskId in response: {result}")
        return False

    print(f"[2/3] Waiting for reCAPTCHA solution (task: {task_id[:16]}...)")
    recaptcha_token = None
    for _ in range(60):
        time.sleep(3)
        get_payload = {"clientKey": capsolver_key, "taskId": task_id}
        req = urllib.request.Request(
            "https://api.capsolver.com/getTaskResult",
            data=json.dumps(get_payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read())
        except Exception as e:
            print(f"❌ Capsolver getTaskResult failed: {e}")
            return False

        if result.get("status") == "ready":
            recaptcha_token = result["solution"]["gRecaptchaResponse"]
            print("[2/3] reCAPTCHA solved!")
            break

    if not recaptcha_token:
        print("❌ Capsolver timeout")
        return False

    warehouse = require_cred(cred, "warehouse_code", "Warehouse code")

    print(f"[3/3] Logging in to Mercadona (warehouse: {warehouse})...")
    login_payload = json.dumps({
        "username": cred["email"],
        "password": cred["password"],
        "recaptcha_token": recaptcha_token,
    }).encode()

    req = urllib.request.Request(
        f"https://tienda.mercadona.es/api/auth/tokens/?lang=es&wh={warehouse}",
        data=login_payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
            "Origin": "https://tienda.mercadona.es",
            "Referer": "https://tienda.mercadona.es/",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            auth_result = json.loads(resp.read())

        # Extract cookies from response headers
        set_cookie = resp.headers.get("Set-Cookie", "")
        cookies = {}
        if set_cookie:
            for part in set_cookie.split(","):
                part = part.strip()
                if "=" in part:
                    name_val = part.split(";")[0].strip()
                    if "=" in name_val:
                        k, v = name_val.split("=", 1)
                        cookies[k.strip()] = v.strip()

        save_auth(auth_result, cookies)
        return True

    except urllib.error.HTTPError as e:
        body = e.read().decode()[:300] if e.fp else str(e)
        print(f"❌ Login failed ({e.code}): {body}")
        return False
    except Exception as e:
        print(f"❌ Login failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Interactive helpers
# ---------------------------------------------------------------------------

def is_interactive() -> bool:
    """Check if stdin is a TTY (interactive mode)."""
    return sys.stdin.isatty()


def interactive_select(items: list, prompt: str = "Select") -> int | None:
    """Show numbered list and get selection. Returns 0-based index or None."""
    if not is_interactive():
        # Non-interactive: auto-select first item
        print(f"Auto-selecting: {items[0] if isinstance(items[0], str) else items[0].get('display_name', items[0].get('name', str(items[0])))}")
        return 0

    for i, item in enumerate(items, 1):
        name = item if isinstance(item, str) else item.get("display_name", item.get("name", str(item)))
        print(f"  {i}. {name}")
    print("  0. Cancel")

    try:
        choice = int(input(f"{prompt}: "))
        if choice == 0:
            return None
        if 1 <= choice <= len(items):
            return choice - 1
        print("❌ Invalid choice")
        return None
    except (ValueError, EOFError):
        print("❌ Invalid input")
        return None


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_search(query: str, limit: int = 5):
    """Search products and display results."""
    results = search_products(query, limit)
    if not results:
        print(f"No results for '{query}'")
        return
    for p in results:
        print(format_product(p))


def cmd_lists():
    """Show all shopping lists."""
    lists = get_lists()
    if not lists:
        print("No shopping lists found.")
        return
    for lst in lists:
        print(f"{lst['id']} | {lst['name']}")


def cmd_show(list_id: str | None = None):
    """Show list contents."""
    if not list_id:
        list_id = get_default_list_id()
    if not list_id:
        return

    data = get_list(list_id)
    if not data:
        return

    name = data.get("name", "Shopping list")
    products = data.get("products", [])
    print(f"📋 {name} ({len(products)} items)")
    print("-" * 50)
    total = 0.0
    for p in products:
        price_info = p.get("price_instructions", {})
        price = price_info.get("bulk_price", price_info.get("unit_price", 0))
        try:
            price_f = float(price)
        except (ValueError, TypeError):
            price_f = 0.0
        total += price_f
        size = f" ({price_info.get('unit_size', '')} {price_info.get('size_format', '')})" if price_info.get("unit_size") else ""
        print(f"  {p.get('display_name', '?')}{size} — {price_f:.2f}€")
    print("-" * 50)
    print(f"  Total: {total:.2f}€")


def cmd_add(query: str, auto: bool = False):
    """Search and add product to list."""
    results = search_products(query, limit=5)
    if not results:
        print(f"❌ No products found for '{query}'")
        return

    if len(results) == 1 or auto or not is_interactive():
        p = results[0]
        print(f"Adding: {format_product(p)}")
    else:
        print(f"Found {len(results)} matches:")
        idx = interactive_select(results, "Select product")
        if idx is None:
            return
        p = results[idx]

    list_id = get_default_list_id()
    if not list_id:
        return

    if add_product(list_id, str(p["id"])):
        print(f"✅ Added {p['name']}")


def cmd_add_id(product_id: str, qty: int = 1):
    """Add product by ID directly."""
    list_id = get_default_list_id()
    if not list_id:
        return

    for _ in range(qty):
        if add_product(list_id, str(product_id)):
            print(f"✅ Added {product_id}")
        else:
            return


def cmd_remove(query: str):
    """Search and remove product from list."""
    list_id = get_default_list_id()
    if not list_id:
        return

    data = get_list(list_id)
    if not data:
        return

    products = data.get("products", [])
    query_lower = query.lower()
    matches = [p for p in products if query_lower in p.get("display_name", "").lower()]

    if not matches:
        print(f"❌ No products matching '{query}' in list")
        return

    if len(matches) == 1 or not is_interactive():
        p = matches[0]
        print(f"Removing: {p['display_name']}")
        if remove_product(list_id, p["id"]):
            print("✅ Removed")
        return

    print(f"Found {len(matches)} matches:")
    idx = interactive_select(matches, "Select")
    if idx is None:
        return
    p = matches[idx]
    if remove_product(list_id, p["id"]):
        print(f"✅ Removed {p['display_name']}")


def cmd_remove_id(product_id: str):
    """Remove product by ID directly."""
    list_id = get_default_list_id()
    if not list_id:
        return

    if remove_product(list_id, product_id):
        print(f"✅ Removed {product_id}")


def cmd_login():
    """Authenticate."""
    if login_with_capsolver():
        print("✅ Session saved. You can now use other commands.")
    else:
        print("❌ Login failed")
        sys.exit(1)


def cmd_setup():
    """Interactive setup: collect credentials and resolve warehouse."""
    print("🛒 Mercadona Shopping Setup")
    print("=" * 40)

    # Check if credentials.json already exists
    existing = {}
    if os.path.exists(CRED_PATH):
        try:
            with open(CRED_PATH) as f:
                existing = json.load(f)
            print(f"Found existing credentials ({CRED_PATH})")
        except Exception:
            pass

    def ask(field: str, label: str, hidden: bool = False) -> str:
        default = existing.get(field, "")
        default_hint = f" [{default}]" if default else ""
        if hidden:
            import getpass
            val = getpass.getpass(f"{label}{default_hint}: ")
        else:
            val = input(f"{label}{default_hint}: ").strip()
        return val or default

    email = ask("email", "Email")
    password = ask("password", "Password", hidden=True)
    postal_code = ask("postal_code", "Postal code (e.g. 03015)")
    capsolver_key = ask("capsolver_api_key", "Capsolver API key")

    if not all([email, password, postal_code, capsolver_key]):
        print("❌ All fields are required.")
        sys.exit(1)

    # Resolve warehouse code from postal code
    print(f"Resolving warehouse for postal code {postal_code}...")
    warehouse = resolve_warehouse(postal_code)
    if not warehouse:
        print("❌ Could not resolve warehouse. Check your postal code.")
        sys.exit(1)

    print(f"✅ Warehouse: {warehouse}")

    # Save credentials
    cred = {
        "email": email,
        "password": password,
        "postal_code": postal_code,
        "warehouse_code": warehouse,
        "capsolver_api_key": capsolver_key,
    }

    with open(CRED_PATH, "w") as f:
        json.dump(cred, f, indent=2)
    os.chmod(CRED_PATH, 0o600)

    print(f"\n✅ Credentials saved to {CRED_PATH}")
    print(f"   Warehouse: {warehouse} (from postal code {postal_code})")
    print(f"\nNext step: python3 mercadona.py login")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Mercadona shopping CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 mercadona.py setup              First-time setup
  python3 mercadona.py login              Authenticate
  python3 mercadona.py search "leche"     Search products
  python3 mercadona.py add "cafe"         Add to list
  python3 mercadona.py add-id 13594       Add by ID
  python3 mercadona.py remove "pan"       Remove from list
  python3 mercadona.py show               Show list
        """,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # setup
    sub.add_parser("setup", help="Interactive setup (credentials + warehouse)")

    # login
    sub.add_parser("login", help="Authenticate")

    # search
    search_p = sub.add_parser("search", help="Search products")
    search_p.add_argument("query", help="Search query")
    search_p.add_argument("--limit", type=int, default=5, help="Max results")

    # lists
    sub.add_parser("lists", help="Show all shopping lists")

    # show
    show_p = sub.add_parser("show", help="Show list contents")
    show_p.add_argument("list_id", nargs="?", help="List ID (auto-detect if omitted)")

    # add
    add_p = sub.add_parser("add", help="Search and add product")
    add_p.add_argument("query", help="Product name to search")
    add_p.add_argument("--yes", "-y", action="store_true", help="Auto-select first result")

    # add-id
    addid_p = sub.add_parser("add-id", help="Add product by ID")
    addid_p.add_argument("product_id", help="Product ID")
    addid_p.add_argument("qty", type=int, nargs="?", default=1, help="Quantity (default: 1)")

    # remove
    rm_p = sub.add_parser("remove", help="Search and remove product")
    rm_p.add_argument("query", help="Product name to search in list")

    # remove-id
    rmid_p = sub.add_parser("remove-id", help="Remove product by ID")
    rmid_p.add_argument("product_id", help="Product ID")

    args = parser.parse_args()

    if args.command == "setup":
        cmd_setup()
    elif args.command == "login":
        cmd_login()
    elif args.command == "search":
        cmd_search(args.query, args.limit)
    elif args.command == "lists":
        cmd_lists()
    elif args.command == "show":
        cmd_show(args.list_id)
    elif args.command == "add":
        cmd_add(args.query, args.yes)
    elif args.command == "add-id":
        cmd_add_id(args.product_id, args.qty)
    elif args.command == "remove":
        cmd_remove(args.query)
    elif args.command == "remove-id":
        cmd_remove_id(args.product_id)


if __name__ == "__main__":
    main()
