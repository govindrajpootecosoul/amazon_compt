# Warmup: no Enter needed (auto-start)

## Default (current)

```python
MANUAL_WARMUP = True           # keep Chrome session flow (do not remove)
WARMUP_REQUIRE_ENTER = False   # auto-start — no terminal Enter
CAPTCHA_WAIT_SECONDS = 120     # if captcha shows, wait for you to solve in Chrome
```

## What happens

1. Script opens Amazon homepage in your Chrome
2. If **no captcha** → scrape starts immediately (no Enter)
3. If **captcha** → message: solve in Chrome; script waits up to 120s, then auto-continues
4. Mid-run captcha still uses the same wait (existing behavior)

## Old Enter flow (if you want it back)

```python
WARMUP_REQUIRE_ENTER = True
```

Then you press Enter after captcha, same as before.

## Do not turn off unless testing headless

`MANUAL_WARMUP = True` + real Chrome is the unpaid reliable path. Keep it.
