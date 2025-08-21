import time, requests

def get(url, params=None, headers=None, retries=3, timeout=30):
    """Eenvoudige GET helper met retries en JSON-detectie."""
    for i in range(retries):
        r = requests.get(url, params=params, headers=headers, timeout=timeout)
        if r.status_code == 200:
            ct = r.headers.get("Content-Type", "")
            return r.json() if "application/json" in ct else r.text
        time.sleep(2 * (i + 1))  # backoff: 2s, 4s, 6s...
    raise RuntimeError(f"GET failed {url} -> {r.status_code}: {r.text[:200]}")
