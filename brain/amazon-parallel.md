# Auto browsers: 10 ASINs each

## Config

```python
ASINs_PER_WORKER = 10   # each Chrome gets ~10 ASINs
MAX_WORKERS = 15        # never open more than 15 browsers
WORKERS = None          # None = auto; set 3 to force exactly 3
```

## Example

149 ASINs → `ceil(149/10) = 15` browsers (hits MAX_WORKERS).  
80 ASINs → 8 browsers.

## Note

Without proxies, many browsers on one IP increase captcha risk. Lower `MAX_WORKERS` (e.g. 5) if needed.
