#!/usr/bin/env python3
"""Screenshot every beat of the deck (mid and last frame) into shots/, then print take-a-number.pdf.

Run with a Python that has Playwright:  <pw>/bin/python deck/shoot.py [--pdf-only]
Headless only. Exits 1 if the page logged an error, so a broken beat is never quiet.
"""
import asyncio, os, sys
from playwright.async_api import async_playwright

here = os.path.dirname(os.path.abspath(__file__))
url = 'file://' + os.path.join(here, 'index.html')


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(args=['--allow-file-access-from-files'])
        pg = await b.new_page(viewport={'width': 1920, 'height': 1080}, device_scale_factor=1)
        errs = []
        pg.on('console', lambda m: errs.append(m.text) if m.type == 'error' else None)
        pg.on('pageerror', lambda e: errs.append(str(e)))
        if '--pdf-only' not in sys.argv:
            await pg.goto(url + '?t=0')
            await pg.wait_for_function('window.__ready===true', timeout=15000)
            starts = await pg.evaluate('window.BEAT_STARTS')
            total = await pg.evaluate('window.DURATION_MS')
            print('DURATION_MS', total, 'beats', len(starts))
            os.makedirs(os.path.join(here, 'shots'), exist_ok=True)
            for i, s in enumerate(starts):
                end = (starts[i + 1] if i + 1 < len(starts) else total) - 1
                for tag, t in (('mid', s + (end - s) * 0.45), ('end', end)):
                    await pg.evaluate(f'window.seek({t})')
                    await pg.wait_for_timeout(80)
                    await pg.screenshot(path=os.path.join(here, 'shots', f'{i + 1:02d}_{tag}.png'))
            missing = await pg.evaluate('document.documentElement.dataset.fontsMissing||""')
            if missing:
                errs.append('fonts missing: ' + missing)
        await pg.goto(url + '?print=1')
        await pg.wait_for_function('window.__ready===true', timeout=15000)
        await pg.wait_for_timeout(800)
        out = os.path.join(here, 'take-a-number.pdf')
        await pg.pdf(path=out, width='1920px', height='1080px', print_background=True)
        print('pdf', out, os.path.getsize(out), 'bytes')
        await b.close()
        if errs:
            sys.exit('FAIL: page errors: ' + '; '.join(errs)[:800])
        print('OK, no page errors')

asyncio.run(main())
