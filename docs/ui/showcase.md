# Refreshing the public product screenshots

BTN-57's public images are captures of the production `BattalionWindow`, not
the HTML design mockup. The capture uses `battalion.desktop.demo` to construct a
fixed, fictional `ProjectInspection` and `IntelInspection` in memory. It does
not read a developer's `.battalion` directory, inspect Git state, load provider
configuration, or make network calls. The visible actor is always
`showcase-operator`.

From the repository root, install the desktop development dependencies and run:

```bash
python -m pip install -e ".[desktop,dev]"
python scripts/capture_pages_screenshots.py
python scripts/build_pages.py
```

The capture command rewrites these reviewed 1380 by 860 PNG files:

- `docs/assets/screenshots/battalion-work.png`
- `docs/assets/screenshots/battalion-history.png`
- `docs/assets/screenshots/battalion-intel.png`

The Work image selects an `awaiting-human` run and exposes the shipped interrupt
resolution and next-attempt controls. History selects a completed Reviewer
attempt and its evidence inspector. Intel selects a pending Recon candidate and
the canonical human review actions. Change the fixture only when the production
surface or shipped contract changes; never add mockup-only controls or real run
data merely to improve the composition.

Before committing a refresh:

1. Run `python -m pytest tests/test_desktop.py tests/test_pages.py -q`.
2. Confirm every image remains legible at its rendered size, contains no
   credentials, usernames, personal paths, or live-service claims, and stays
   below the size thresholds enforced by `tests/test_pages.py`.
3. Build the staged source and visually inspect the generated site at desktop
   and narrow viewport widths.
4. Check that the captions and alternative text still describe what the image
   actually shows and that the linked text operator documentation remains
   complete without the screenshots.
