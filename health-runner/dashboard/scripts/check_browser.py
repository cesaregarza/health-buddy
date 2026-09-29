#!/usr/bin/env python3
"""Exercise the real dashboard offline in Chromium using fabricated data by default."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from dashboard_fixture import AS_OF, snapshot
from preview import render

TABS = ("overview", "progress", "training", "labs", "notes")


def layout(page):
    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth"), "Page overflow"
    small = page.evaluate("""() => [...document.querySelectorAll('button,summary,select,input:not([type=checkbox]),.check-label,.jump-links a')]
      .filter(e => {const r=e.getBoundingClientRect();return r.width && r.height && getComputedStyle(e).pointerEvents !== 'none' && r.height < 43.5;})
      .map(e=>e.id || e.tagName)""")
    assert not small, f"Targets below 44 px: {small}"
    assert not page.evaluate("""() => [...document.querySelectorAll('svg path')].some(p=>/NaN|Infinity/.test(p.getAttribute('d') || ''))"""), "Invalid chart geometry"


def contrast(page):
    ratios=page.evaluate("""() => {
      const el=document.createElement('span');document.body.append(el);
      const rgb=name=>{el.style.color=`var(--${name})`;return getComputedStyle(el).color.match(/[\d.]+/g).slice(0,3).map(Number);};
      const lum=c=>{const v=c.map(x=>{x/=255;return x<=.04045?x/12.92:((x+.055)/1.055)**2.4;});return .2126*v[0]+.7152*v[1]+.0722*v[2];};
      const bg=lum(rgb('surface-1'));
      const result=Object.fromEntries(['text-muted','text-secondary','warning','good','critical','delta-good','s1'].map(name=>{const fg=lum(rgb(name));return [name,(Math.max(bg,fg)+.05)/(Math.min(bg,fg)+.05)];}));
      el.remove();return result;
    }""")
    assert all(r >= 4.5 for r in ratios.values()), f'Text contrast below 4.5: {ratios}'


def usability(page):
    from playwright.sync_api import expect
    assert page.locator('#tiles').bounding_box()['y'] < 340, "Daily summary starts too low"
    expect(page.locator('#tiles .tile')).to_have_count(3)
    expect(page.locator('#metric-details')).not_to_have_attribute('open', '')
    expect(page.locator('#tiles')).to_contain_text('Last recorded')
    page.locator('#data-status > summary').click()
    expect(page.locator('#data-status-body')).to_contain_text('Blood pressure')
    expect(page.locator('#data-status-body')).to_contain_text('Food')
    page.locator('#data-status > summary').click()
    page.locator('a[href="#section-fuel"]').click()
    expect(page.locator('#intake svg')).to_be_visible()
    for state in ('unknown', 'partial', 'complete'):
        assert page.locator(f'#intake svg [data-state={state}]').count() > 0
    page.locator('#intake .hit').nth(1).hover()
    expect(page.locator('#intake .tip')).to_contain_text('known subtotal')
    expect(page.locator('#intake .tip')).to_contain_text('1/2 entries missing')
    expect(page.locator('#intake .tip')).to_contain_text('0 kcal')
    page.locator('#intake .fig-tools button', has_text='Table').click()
    table=page.locator('#intake table')
    expect(table).to_contain_text('Unknown')
    expect(table).to_contain_text('0 · partial')
    assert table.locator('tbody tr').filter(has_text='2026-08-18').locator('td').nth(1).inner_text() == '0'
    expect(table).to_contain_text('1/2 entries missing')
    page.locator('#intake .fig-tools button', has_text='Table').click()
    page.locator('#tab-progress').click()
    expect(page.locator('#waist-history')).to_contain_text('Baseline only')
    page.locator('#waist-site').select_option('navel')
    expect(page.locator('#waist-history')).to_contain_text('39.0 in')
    expect(page.locator('#waist-history')).to_contain_text('−1.0 in since')
    expect(page.locator('#body-composition-baseline')).to_contain_text('One recorded scan')
    expect(page.locator('#body-composition-baseline')).to_contain_text('60.0 lb')
    page.locator('#tab-training').click()
    expect(page.locator('#gym-view [data-view=compact]')).to_have_attribute('aria-pressed', 'true')
    expect(page.locator('#training-next .gym-target').first).to_be_visible()
    expect(page.locator('#training-next .more summary').filter(has_text='Last Aug 18').first).to_contain_text('20 lb / hand × 10 @ 2 RIR')
    page.locator('#gym-view [data-view=detailed]').click()
    expect(page.locator('#training-next .target').first).to_be_visible()
    expect(page.locator('#training-next .gym-target').first).to_be_hidden()
    page.reload()
    expect(page.locator('#gym-view [data-view=detailed]')).to_have_attribute('aria-pressed','true')
    page.locator('#gym-view [data-view=compact]').click()
    page.locator('#tab-notes').click()
    expect(page.locator('#visit-header')).to_contain_text('Questions in prepared note')
    assert page.locator('#visit-header dt').filter(has_text='Questions in prepared note').evaluate('el => el.nextElementSibling.textContent') == '2'
    expect(page.locator('#visit-questions input[type=checkbox]')).to_have_count(2)
    suggested=page.locator('#visit-questions li').filter(has_text='Example generated question?')
    expect(suggested).to_contain_text('Suggested discussion')
    expect(suggested).not_to_contain_text('not yet in the log')
    expect(page.locator('#visit-note')).to_be_hidden()
    page.evaluate("Object.defineProperty(navigator, 'clipboard', {configurable:true,value:{writeText:async text=>{window.copiedNote=text;}}})")
    page.locator('#visit-header button', has_text='Copy as text').click()
    expect(page.locator('#visit-header button', has_text='Copied')).to_be_visible()
    assert page.evaluate('window.copiedNote') == snapshot()['visit']['note_markdown']
    page.evaluate("window.dispatchEvent(new Event('beforeprint'))")
    expect(page.locator('#visit-note')).to_be_visible()
    expect(page.locator('#visit-questions')).to_contain_text('Example context')
    page.evaluate("window.dispatchEvent(new Event('afterprint'))")
    expect(page.locator('#visit-note')).to_be_hidden()
    page.locator('#tab-overview').click()


def exercise(page, synthetic):
    from playwright.sync_api import expect
    expect(page.locator('#settings')).to_be_hidden()
    page.locator('#settings-toggle').click()
    page.locator('#theme [data-theme=light]').click()
    expect(page.locator('html')).to_have_attribute('data-theme','light')
    page.keyboard.press('Escape')
    expect(page.locator('#settings')).to_be_hidden()
    page.locator('#tab-overview').focus()
    page.keyboard.press('ArrowRight')
    expect(page.locator('#pane-progress')).to_be_visible()
    page.keyboard.press('End')
    expect(page.locator('#pane-notes')).to_be_visible()
    page.keyboard.press('Home')
    expect(page.locator('#pane-overview')).to_be_visible()
    page.locator('a[href="#section-fuel"]').click()
    expect(page.locator('#intake')).to_be_visible()
    page.locator('#intake .fig-tools button', has_text='Method').click()
    expect(page.locator('#intake-method')).to_be_visible()
    page.locator('#intake .fig-tools button', has_text='Table').click()
    expect(page.locator('#intake table')).to_be_visible()
    page.locator('#range [data-days="90"]').click()
    expect(page.locator('#intake table')).to_be_visible()
    expect(page.locator('#intake-method')).to_be_visible()
    page.locator('#range [data-days="0"]').click()
    for name in ('activity','recovery'):
        page.locator(f'a[href="#section-{name}"]').click()
    expect(page.locator('#figs figure')).to_have_count(11)
    chart = page.locator('#weight .plot svg').first
    chart.scroll_into_view_if_needed()
    touch_visible = chart.evaluate("""el => {
      const r=el.getBoundingClientRect();
      el.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,pointerType:'touch',clientX:r.left+r.width/2,clientY:r.top+r.height/2}));
      const tip=el.closest('figure').querySelector('.tip');
      const tapped=getComputedStyle(tip).display !== 'none';
      el.dispatchEvent(new PointerEvent('pointerleave',{pointerType:'touch'}));
      return [tapped,getComputedStyle(tip).display !== 'none'];
    }""")
    assert touch_visible == [True, True], touch_visible
    layout(page)
    page.locator('#section-body > summary').click()
    row = page.locator('#today .today-row').first
    if synthetic:
        expect(page.locator('#today .today-row')).to_have_count(3)
        expect(page.locator('#today')).not_to_contain_text('next weekly dose')
        expect(page.locator('#today .tracking-row')).to_have_count(2)
        expect(page.locator('[data-tracking=waist]')).to_contain_text('Due today')
        expect(page.locator('[data-tracking=photos]')).to_contain_text('Upcoming')
        page.evaluate("""() => {
          window.savedTracking = DATA.tracking_schedule;
          DATA.tracking_schedule = DATA.tracking_schedule.map(x=>({...x, due_date:'2026-08-19'}));
        }""")
        page.locator('#range [data-days="0"]').click()
        expect(page.locator('[data-tracking=waist]')).to_contain_text('Overdue · 1 day')
        page.evaluate("""() => {
          DATA.tracking_schedule = DATA.tracking_schedule.map(x=>({...x,last_recorded:null,due_date:null}));
        }""")
        page.locator('#range [data-days="0"]').click()
        expect(page.locator('[data-tracking=photos]')).to_contain_text('Not yet recorded')
        page.evaluate('DATA.tracking_schedule = window.savedTracking; delete window.savedTracking')
        page.locator('#range [data-days="0"]').click()
        row.click()
        expect(page.locator('#bp')).to_be_visible()
    page.locator('#tab-progress').click()
    if synthetic:
        expect(page.locator('#progress-tiles meter')).to_have_count(2)
        assert 'Projected' in page.locator('#progress-tiles').inner_text()
        assert 'meets this goal' not in page.locator('#progress-tiles').inner_text()
    assert page.evaluate("document.querySelector('#progress-weight').compareDocumentPosition(document.querySelector('.progress-controls')) & Node.DOCUMENT_POSITION_FOLLOWING")
    for selector in ('#forecast-model [data-model=ordinary]', '#forecast-model [data-model=robust]', '#forecast-model [data-model=both]', '#forecast-goal [data-goal="200"]'):
        page.locator(selector).click()
        expect(page.locator(selector)).to_have_attribute('aria-pressed','true')
    expect(page.locator('#strength-metric [data-metric=volume]')).to_have_attribute('aria-pressed','true')
    if synthetic:
        expect(page.locator('#progress-strength-chart')).to_contain_text('+156%')
        page.evaluate("DATA.progress.strength.exercises[0].comparison_unverified = true")
        page.locator('#strength-metric [data-metric=load]').click()
        expect(page.locator('#progress-strength-chart')).to_contain_text('Machine identity or load scale is unverified')
        page.evaluate("DATA.progress.strength.exercises[0].comparison_unverified = false")
        page.locator('#strength-metric [data-metric=volume]').click()
        expect(page.locator('#progress-strength-chart')).to_contain_text('+156%')
        expect(page.locator('#strength-session-detail')).to_contain_text('1,280')
        expect(page.locator('#strength-session-detail')).to_contain_text('16 reps')
        page.locator('#strength-session').select_option('example-first')
        expect(page.locator('#strength-session-detail')).to_contain_text('500')
        page.evaluate("""() => {
          const original = DATA.progress.strength.exercises[0];
          DATA.progress.strength.exercises.push({...original, key:'chest_press:other', name:'Chest press · other machine'});
        }""")
        page.locator('#forecast-model [data-model=ordinary]').click()
        expect(page.locator('#strength-exercises option')).to_have_count(1)
        expect(page.locator('#strength-variants option')).to_have_count(2)
        page.locator('#strength-variants').select_option('chest_press:other')
        expect(page.locator('#progress-strength-chart')).to_contain_text('other machine')
        page.evaluate("""() => {
          const goal = DATA.progress.weight.goals.find(g => g.target_value === 200);
          window.saved200 = {...goal, projections: {...goal.projections}};
          const other = DATA.progress.weight.goals.find(g => g.target_value === 170);
          window.saved170 = {...other, projections: {...other.projections}};
          window.savedAverage = DATA.progress.weight.weight.current_7_day.average_weight_lb;
          goal.first_7_day_average_crossing_below = '2026-08-18';
          goal.milestone = {elapsed_days:61, average_loss_lb_per_week:2.3, start_date:'2026-06-18'};
          goal.projections = {};
          goal.projection_range = null;
          DATA.progress.weight.weight.current_7_day.average_weight_lb = 198;
        }""")
        page.locator('#forecast-goal [data-goal="200"]').click()
        expect(page.locator('.goal-row.milestone strong')).to_contain_text('Aug 18, 2026')
        expect(page.locator('.goal-row.milestone')).to_contain_text('61 days to milestone')
        expect(page.locator('.goal-row.milestone')).to_contain_text('~2.3 lb/week average pace')
        expect(page.locator('#forecast-goal [data-goal="200"]')).to_contain_text('200 milestone')
        expect(page.locator('#forecast-model-control')).to_be_hidden()
        expect(page.locator('#progress-journey')).to_contain_text('First 7-day average crossing below 200 lb')
        expect(page.locator('#progress-journey')).not_to_contain_text('30-day OLS scenario')
        layout(page)
        page.locator('#forecast-goal [data-goal="170"]').click()
        expect(page.locator('#forecast-model-control')).to_be_visible()
        page.evaluate("""() => {
          const goal = DATA.progress.weight.goals.find(g => g.target_value === 170);
          goal.first_7_day_average_crossing_below = '2026-08-19';
          goal.milestone = {elapsed_days:62, average_loss_lb_per_week:6.4, start_date:'2026-06-18'};
          goal.projections = {};
          goal.projection_range = null;
          DATA.progress.weight.weight.current_7_day.average_weight_lb = 168;
        }""")
        page.locator('#forecast-goal [data-goal="170"]').click()
        row170 = page.locator('.goal-row.milestone').filter(has_text='170 lb milestone')
        expect(row170).to_contain_text('62 days to milestone')
        expect(row170).to_contain_text('~6.4 lb/week average pace')
        expect(page.locator('#forecast-goal [data-goal="170"]')).to_contain_text('170 milestone')
        expect(page.locator('#forecast-model-control')).to_be_hidden()
        expect(page.locator('#progress-journey')).to_contain_text('First 7-day average crossing below 170 lb')
        layout(page)
        page.evaluate("""() => {
          for (const [target, saved] of [[200, window.saved200], [170, window.saved170]]) {
            const goal = DATA.progress.weight.goals.find(g => g.target_value === target);
            delete goal.first_7_day_average_crossing_below;
            delete goal.milestone;
            Object.assign(goal, saved);
          }
          DATA.progress.weight.weight.current_7_day.average_weight_lb = window.savedAverage;
          delete window.saved200; delete window.saved170; delete window.savedAverage;
        }""")
        page.locator('#forecast-goal [data-goal="170"]').click()
    page.locator('#strength-metric [data-metric=load]').click()
    expect(page.locator('#progress-strength-chart')).to_contain_text('Heaviest logged working set')
    page.locator('#strength-metric [data-metric=volume]').click()
    layout(page)
    page.locator('#tab-training').click()
    if synthetic:
        expect(page.locator('#training-next .rx-row')).to_have_count(6)
        for text in ('progression pending', 'add reps', 'increment earned', 'verify smallest step'):
            assert text in page.locator('#training-next').inner_text()
        expect(page.locator('#training-day .rx-row')).to_have_count(0)
    checks=page.locator('#training-next input[type=checkbox]')
    if checks.count():
        checks.first.check()
        page.locator('#training-prev').click()
        expect(page.locator('#training-next input[type=checkbox]').first).to_be_checked()
    page.locator('#training-today').click()
    layout(page)
    page.locator('#tab-labs').click()
    if synthetic:
        assert 'All 10 headline analytes' in page.locator('#lab-headlines').inner_text()
    search=page.get_by_role('searchbox')
    search.fill('no-such-example-analyte')
    expect(page.locator('#other-labs')).to_contain_text('No matching analytes')
    search.fill('CBC')
    if synthetic:
        expect(page.locator('#other-labs .lab-row')).to_have_count(1)
    search.fill('')
    page.locator('#lab-chart-history > summary').click()
    expect(page.locator('#labs-timeline')).to_be_visible()
    first=page.locator('#lab-chart-history .fig-tools button').filter(has_text='Table').first
    first.click()
    expect(page.locator('#lab-chart-history table').first).to_be_visible()
    layout(page)
    page.locator('#tab-notes').click()
    if synthetic:
        assert 'needs outcome' in page.locator('#visit-header').inner_text().lower()
    check=page.locator('#visit-questions input[type=checkbox]').first
    if check.count():
        check.check()
        page.locator('#tab-overview').click()
        page.locator('#tab-notes').click()
        expect(check).to_be_checked()
    page.emulate_media(media='print')
    expect(page.locator('#tabbar')).to_be_hidden()
    expect(page.locator('#visit-questions')).to_be_visible()
    page.emulate_media(media='screen')
    page.locator('#tab-overview').click()
    page.evaluate('window.scrollTo(0,document.body.scrollHeight)')
    expect(page.locator('#to-top')).to_have_class('to-top icon-button show')
    page.locator('#to-top').click()
    expect(page.locator('#tab-overview')).to_be_focused()
    page.reload()
    expect(page.locator('#section-fuel')).to_have_attribute('open','')
    expect(page.locator('html')).to_have_attribute('data-theme','light')
    page.locator('#section-fuel > summary').click()
    page.evaluate("window.dispatchEvent(new Event('beforeprint'))")
    expect(page.locator('.chart-section[open]')).to_have_count(4)
    expect(page.locator('#figs figure')).to_have_count(11)
    page.evaluate("window.dispatchEvent(new Event('afterprint'))")
    expect(page.locator('#section-fuel')).not_to_have_attribute('open','')


def capture_page_error(errors, error, *, phase, width, theme):
    """Keep bounded diagnostics tied to the page that emitted the error."""
    if len(errors) < 10:
        errors.append({"phase": phase, "width": width, "theme": theme,
                       "message": str(error)[:1000],
                       "stack": str(getattr(error, "stack", ""))[:2000]})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--html',type=Path,help='Existing local private preview instead of synthetic data')
    parser.add_argument('--screenshots',action='store_true',help='Save beside --html (requires that file to be gitignored)')
    parser.add_argument('--date',default='2026-08-20T15:00:00+00:00',help='Fixed browser time, ISO 8601 with offset')
    parser.add_argument('--width',type=int,choices=(320,390,768,1440),help='Run one viewport instead of the full matrix')
    parser.add_argument('--theme',choices=('dark','light'),help='Run one theme instead of both')
    args=parser.parse_args()
    if args.html and args.html.resolve().is_relative_to('/mnt'):
        parser.error('Use a native Linux preview path')
    if args.screenshots:
        import subprocess
        if not args.html or subprocess.run(['git','-C',str(ROOT),'check-ignore','--quiet',str(args.html.resolve())],capture_output=True).returncode:
            parser.error('Screenshots require a gitignored --html preview')
    html=args.html.read_text() if args.html else render(snapshot(),AS_OF)
    from playwright.sync_api import sync_playwright, expect
    errors=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(timeout=15000)
        for width in (args.width,) if args.width else (320,390,768,1440):
            for theme in (args.theme,) if args.theme else ('dark','light'):
                context=browser.new_context(viewport={'width':width,'height':900},color_scheme=theme,reduced_motion='reduce',timezone_id='Asia/Tokyo')
                requests=[]
                def route(req):
                    if req.request.url.split('?',1)[0] == 'http://localhost/dashboard' and req.request.resource_type == 'document':
                        req.fulfill(status=200,content_type='text/html',body=html)
                    else:
                        requests.append(req.request.resource_type)
                        req.abort()
                context.route('**/*',route)
                page=context.new_page()
                page.on('pageerror',lambda e,w=width,t=theme:capture_page_error(errors,e,phase='regular',width=w,theme=t))
                page.clock.set_fixed_time(datetime.fromisoformat(args.date).astimezone(timezone.utc))
                page.goto('http://localhost/dashboard')
                assert not errors, f'JavaScript errors: {errors}'
                expect(page.locator('#tiles .tile')).not_to_have_count(0)
                maintenance = page.locator('#supporting-tiles .tile').filter(has_text='Maintenance estimate')
                if not args.html:
                    expect(maintenance).to_contain_text('2,350')
                if 'unavailable' in (maintenance.get_attribute('class') or ''):
                    expect(maintenance).to_contain_text('Not enough recent energy data')
                else:
                    expect(maintenance).to_contain_text('not a food target')
                contrast(page)
                for tab in TABS:
                    page.locator(f'#tab-{tab}').click()
                    layout(page)
                    if args.screenshots:
                        page.screenshot(path=str(args.html.parent/f'{tab}-{width}-{theme}.png'),full_page=True)
                page.locator('#tab-overview').click()
                if width == 390 and theme == 'dark':
                    if not args.html:
                        usability(page)
                    exercise(page,not args.html)
                    page.set_viewport_size({'width':1440,'height':900})
                    page.wait_for_timeout(250)
                    layout(page)
                    page.set_viewport_size({'width':390,'height':844})
                    page.wait_for_timeout(250)
                    layout(page)
                assert not requests, f'Unexpected external requests: {requests}'
                context.close()
        if not args.html:
            # Missing sources must render explicit empty states, never stale claims.
            data=snapshot()
            for key in ('weight','weight7','bp','injections','intake','training','sleep','rhr','hrv','steps','energy','workouts'):
                data[key]=[]
            data['tape']={'waist':[],'circumferences':[]}
            data['body_composition']={'scans':[]}
            data['tracking_schedule']=[]
            data['labs']={'analytes':[],'dates':[],'events':[]}
            data['visit']={'questions':[],'note_markdown':None}
            data['training_detail']={'days':[],'prescriptions':[],'prescription_errors':[]}
            data['progress']={'weight':None,'strength':{'exercises':[]}}
            empty_html=render(data,AS_OF)
            context=browser.new_context(viewport={'width':390,'height':844})
            context.route('**/*',lambda route:route.fulfill(status=200,content_type='text/html',body=empty_html))
            page=context.new_page()
            page.on('pageerror',lambda e:capture_page_error(errors,e,phase='empty-snapshot',width=390,theme='default'))
            page.clock.set_fixed_time(datetime.fromisoformat(args.date).astimezone(timezone.utc))
            page.goto('http://localhost/dashboard')
            expect(page.locator('#today')).to_contain_text('No highlighted items')
            expect(page.locator('#supporting-tiles .energy-tile')).to_contain_text('need recent HealthKit energy data')
            for tab in TABS:
                page.locator(f'#tab-{tab}').click()
                layout(page)
            context.close()
        browser.close()
    assert not errors, f'JavaScript errors: {errors}'
    print(json.dumps({'status':'passed','widths':[args.width] if args.width else [320,390,768,1440],
                      'themes':[args.theme] if args.theme else ['dark','light'],
                      'tabs':list(TABS),'javascript_errors':0,'external_requests':0}))


if __name__=='__main__':
    main()
