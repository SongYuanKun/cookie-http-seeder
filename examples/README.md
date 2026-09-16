# Example configs and snippets

| File | Purpose |
|------|---------|
| `sources.json` | Default domain allow-list (kept in sync with packaged defaults) |
| `sources.minimal.json` | Tiny template for adding your own sites |
| `consume_cookies.py` | Read `{source}-cookies.json` in a crawler |

Edit `sources.json`, then regenerate the extension allow-list:

```bash
python scripts/sync_extension_sources.py
```

Reload the unpacked extension in Chrome after syncing.
