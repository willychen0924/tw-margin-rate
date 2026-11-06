"""Deterministic browser acceptance; no model, user profile or external assets."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright, expect


def verify(url, history, output):
    output.mkdir(parents=True, exist_ok=True)
    latest={m:rows[-1] for m,rows in history['markets'].items()}
    errors=[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch()
        context=browser.new_context(viewport={'width':1280,'height':900})
        page=context.new_page()
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto(url, wait_until='load', timeout=60000)
        expect(page).to_have_title('台股上市櫃融資維持率')
        frame=page.frame_locator('iframe')
        expect(frame.locator('[data-range="1y"]')).to_have_attribute('aria-pressed','true')
        expect(frame.locator('.mmc-updated')).to_contain_text(latest['twse']['date'].replace('-','/'))
        for market,row in latest.items():
            chart=frame.locator(f'[data-chart="{market}"]')
            # JS formatting, not Python bankers rounding, matches the actual UI.
            values=page.evaluate('(r)=>[r.maintenance.toFixed(1)+"%",r.index.toLocaleString("en-US",{minimumFractionDigits:r.market==="tpex"?1:0,maximumFractionDigits:r.market==="tpex"?1:0}),r.financed_amount.toFixed(1),r.margin_market_cap_ratio.toFixed(2)+"%"]',dict(row,market=market))
            for role,value in zip(('maint-value','index-value','balance-value','ratio-value'),values):
                expect(chart.locator(f'[data-role="{role}"]')).to_have_text(value)
            for series in ('maint','index','balance','ratio'):
                path=chart.locator(f'[data-role="{series}-path"]')
                assert len(path.get_attribute('d') or '')>10, f'{market} {series} empty path'
            assert chart.locator('[data-role="alert-line"]').get_attribute('y1')
            expect(chart.locator('[data-role="alert-label"]')).to_have_text('')
        assert frame.locator('[data-scale]:visible').count()==0
        for width in (1280,768,390,360,320):
            page.set_viewport_size({'width':width,'height':900})
            page.wait_for_timeout(120)
            assert frame.locator('body').evaluate('(e)=>e.scrollWidth<=e.clientWidth'),f'overflow {width}'
            for market in latest:
                frame.locator(f'[data-chart="{market}"]').scroll_into_view_if_needed()
                page.screenshot(path=str(output/f'{width}-{market}.png'))
                if width<=390:
                    for button in frame.locator(f'[data-chart="{market}"] [data-series]').all():
                        assert button.bounding_box()['height']>=31.5,'legend touch height'
        page.set_viewport_size({'width':1280,'height':900})
        buttons=frame.locator('[data-series]')
        assert buttons.count()==8
        for button in buttons.all(): button.click()
        page.wait_for_function("()=>{const s=JSON.parse(localStorage.getItem('tw-margin-rate:series-visibility:v1'));return s&&Object.values(s).flatMap(Object.values).every(v=>v===false)}")
        page.reload()
        for button in buttons.all(): expect(button).to_have_attribute('aria-pressed','false')
        for button in buttons.all(): button.click()
        page.wait_for_function("()=>{const s=JSON.parse(localStorage.getItem('tw-margin-rate:series-visibility:v1'));return s&&Object.values(s).flatMap(Object.values).every(v=>v===true)}")
        page.reload()
        for button in buttons.all(): expect(button).to_have_attribute('aria-pressed','true')
        for market in latest:
            hit=frame.locator(f'[data-chart="{market}"] [data-role="hit"]')
            hit.hover()
            expect(frame.locator('[data-role="tooltip"][aria-hidden="false"]')).to_be_visible()
            dates=frame.locator('[data-role="date"]').all_text_contents()
            assert len(dates)==2 and dates[0]==dates[1], 'cross-chart date mismatch'
            page.screenshot(path=str(output/f'tooltip-{market}.png'))
            page.mouse.move(0,0)
        for period in ('all','3m','6m','ytd','1y','2y','3y','5y'):
            frame.locator(f'[data-range="{period}"]').click()
            expect(frame.locator(f'[data-range="{period}"]')).to_have_attribute('aria-pressed','true')
            for market in latest:
                assert len(frame.locator(f'[data-chart="{market}"] [data-role="maint-path"]').get_attribute('d') or '')>10
        page.set_viewport_size({'width':390,'height':900})
        frame.locator('[data-range="1y"]').click()
        hit=frame.locator('[data-chart="tpex"] [data-role="hit"]')
        hit.scroll_into_view_if_needed();box=hit.bounding_box()
        point={'pointerId':41,'pointerType':'touch','isPrimary':True,'clientX':box['x']+box['width']*.45,'clientY':box['y']+box['height']*.5,'bubbles':True}
        hit.dispatch_event('pointerdown',point);page.wait_for_timeout(360)
        hit.dispatch_event('pointermove',dict(point,clientX=point['clientX']+20))
        expect(frame.locator('[data-role="tooltip"][aria-hidden="false"]')).to_be_visible()
        dates=frame.locator('[data-role="date"]').all_text_contents()
        assert dates[0]==dates[1], 'touch date mismatch'
        hit.dispatch_event('pointerup',point)
        expect(frame.locator('[data-role="date"]').first).to_have_text(latest['twse']['date'].replace('-','/'))
        page.set_viewport_size({'width':1280,'height':900})
        page.reload()
        expect(frame.locator('[data-role="date"]').first).to_have_text(latest['twse']['date'].replace('-','/'))
        page.screenshot(path=str(output/'latest.png'),full_page=True)
        assert not errors, f'JavaScript errors: {errors}'
        browser.close()
    result={'url':url,'date':latest['twse']['date'],'widths':[1280,768,390,360,320],
            'eight_toggle_reload_memory':True,'all_period_buttons':True,'synchronized_hover':True,'synthetic_touch_longpress_drag':True,'errors':errors,
            'scope':'DOM/geometry/interaction checks and screenshots; not human visual judgment or physical touch testing'}
    (output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    return result
