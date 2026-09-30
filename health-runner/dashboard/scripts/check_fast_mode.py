#!/usr/bin/env python3
"""Focused mocked-browser check for optional Training Fast mode."""
import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / 'training_fast.js'
REVISION = 'a' * 40
DATE = '2026-08-20'
EXERCISES = [
    {'exercise': 'Press', 'equipment': 'dumbbells', 'work': '2 sets x 10', 'notes': 'keep shoulder relaxed'},
    {'exercise': 'Row', 'equipment': 'cable', 'work': '3 sets x 8', 'notes': 'pause at top'},
    {'exercise': 'Squat', 'equipment': 'barbell', 'work': '2 sets x 6', 'notes': 'comfortable depth'},
]


def page_html():
    rows = ''.join(
        '<li class="rx-row" data-exercise-index="{i}"><label><input type="checkbox"><span>{name}</span></label>'
        '<div class="target">{work}</div><details><summary>Notes</summary><div>{notes}</div></details></li>'.format(
            i=i, name=x['exercise'], work=x['work'], notes=x['notes'])
        for i, x in enumerate(EXERCISES)
    )
    snapshot = {'recommendation': {'date': DATE, 'template': {'exercises': EXERCISES}}}
    next_snapshot = {'recommendation': {'date': '2026-08-21', 'template': {'exercises': EXERCISES}}}
    html = """<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>
      body{font:16px sans-serif;margin:12px} .rx-list{list-style:none;padding:0} .rx-row{display:grid;gap:8px;border-bottom:1px solid #aaa;padding:12px 0}
      .fast-mode-head,.fast-mode-actions{display:flex;gap:8px;align-items:center;justify-content:space-between;flex-wrap:wrap}
      .fast-mode-panel{padding:8px 0} button{min-height:40px} .fast-priority{display:inline-block;margin-top:4px}
    </style></head><body><div id="training-fast"></div><section id="training-next"><ul class="rx-list">ROWS</ul></section>
      <script>SOURCE</script><script>
      const snapshot=SNAPSHOT;
      const host=document.getElementById('training-fast');
      const list=document.querySelector('.rx-list');
      TrainingFast.mount(host,{snapshot,revision:'REVISION',rows:list});
      window.remountPlan=revision=>{
        const next=NEXT_SNAPSHOT;
        TrainingFast.mount(host,{snapshot:next,revision,rows:list});
      };
      </script></body></html>"""
    return (html.replace('ROWS', rows).replace('SOURCE', MODULE.read_text())
            .replace('NEXT_SNAPSHOT', json.dumps(next_snapshot)).replace('SNAPSHOT', json.dumps(snapshot))
            .replace('REVISION', REVISION))


def open_page(page):
    page.route('**/*', lambda route: route.fulfill(status=200, content_type='text/html', body=page_html()))
    page.goto('http://fast-mode.test/')


def add_mock_fetch(page, delayed_step=None, fail_step_once=None, uncertain=False):
    page.add_init_script(f"""(() => {{
      window.__apiCalls=[]; window.__didFail=false;
      const revision='{REVISION}', date='{DATE}';
      const scores={{e0:{{score:3.8,probabilities:{{'0':0.05,'1':0.1,'2':0.25,'3':0.4,'4':0.2}}}},e1:{{score:1.5,probabilities:{{'0':0.3,'1':0.4,'2':0.2,'3':0.1,'4':0}}}},e2:{{score:3.7,probabilities:{{'0':0.01,'1':0.04,'2':0.1,'3':0.35,'4':0.5}}}}}};
      const base={{schema_version:1,plan_id:'mock-plan',date,source_revision:revision,phase:'not_started',step:0,ranked:[],remaining:['e0','e1','e2'],scores:{{}},must_count:null,threshold_probability:null,threshold_uncertain:false,model:null}};
      const response=(body,status=200)=>Promise.resolve(new Response(JSON.stringify(body),{{status,headers:{{'Content-Type':'application/json'}}}}));
      window.fetch=async (url,options={{}})=>{{
        const method=(options.method||'GET').toUpperCase();
        const call={{method,url:String(url),body:options.body?JSON.parse(options.body):null,headers:options.headers||{{}}}};
        window.__apiCalls.push(call);
        if(method==='GET') return response(base);
        const step=call.body.step;
        if({json.dumps(fail_step_once)}===step&&!window.__didFail){{window.__didFail=true;return response({{error:'temporary model outage'}},503);}}
        if({json.dumps(delayed_step)}===step) await new Promise(resolve=>setTimeout(resolve,350));
        const prefix=[{{id:'e0',index:0,score:3.8,probability:null,selection:'score'}}];
        if(step===0) return response({{...base,phase:'ranking',step:1,ranked:prefix,remaining:['e1','e2'],scores}});
        const two=[...prefix,{{id:'e2',index:2,score:3.7,probability:0.72,selection:'choice'}}];
        if(step===1) return response({{...base,phase:'ranking',step:2,ranked:two,remaining:['e1'],scores}});
        if(step===2) return response({{...base,phase:'threshold',step:3,ranked:[...two,{{id:'e1',index:1,score:1.5,probability:0.2,selection:'remaining'}}],remaining:[],scores}});
        return response({{...base,phase:'complete',step:4,ranked:[...two,{{id:'e1',index:1,score:1.5,probability:0.2,selection:'remaining'}}],remaining:[],scores,must_count:2,threshold_probability:0.84,threshold_uncertain:{json.dumps(uncertain)}}});
      }};
    }})();""")


def calls(page):
    return page.evaluate('window.__apiCalls')


def check_success(browser, width):
    context = browser.new_context(viewport={'width': width, 'height': 900})
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    add_mock_fetch(page)
    open_page(page)
    expect(page.locator('#training-fast button', has_text='Full workout')).to_have_attribute('aria-pressed', 'true')
    assert calls(page) == [], 'page load must not read or start a ranking'
    page.locator('#training-fast button', has_text='Fast mode').click()
    expect(page.locator('#training-fast')).to_contain_text('Build with Jev')
    expect(page.locator('#training-fast')).to_contain_text('No health readings or workout log evidence are sent')
    assert [c['method'] for c in calls(page)] == ['GET']
    page.locator('#training-fast button', has_text='Build with Jev').click()
    expect(page.locator('#training-next .fast-priority').nth(0)).to_contain_text('Absolute must', timeout=5000)
    assert page.locator('#training-next .rx-row').evaluate_all('rows=>rows.map(r=>r.dataset.exerciseIndex)') == ['0', '2', '1']
    labels = page.locator('#training-next .fast-priority').all_text_contents()
    assert labels[0].startswith('Absolute must') and labels[1].startswith('Absolute must') and labels[2].startswith('If time allows')
    first = page.locator('#training-next .rx-row').nth(0)
    first.locator('input').check()
    expect(first.locator('details div')).to_have_text('keep shoulder relaxed')
    posts = [c for c in calls(page) if c['method'] == 'POST']
    assert [c['body']['step'] for c in posts] == [0, 1, 2, 3]
    assert posts[0]['headers'].get('X-Health-Action') == 'rank-training'
    assert posts[0]['body'] == {'date': DATE, 'revision': REVISION, 'step': 0}
    page.locator('#training-fast button', has_text='Full workout').click()
    assert page.locator('#training-next .rx-row').evaluate_all('rows=>rows.map(r=>r.dataset.exerciseIndex)') == ['0', '1', '2']
    assert page.locator('#training-next .fast-priority').count() == 0
    assert page.locator('#training-next input').first.is_checked()
    details = page.locator('#training-next .rx-row').evaluate_all('rows=>rows.map(r=>r.querySelector("details div").textContent)')
    assert details == ['keep shoulder relaxed', 'pause at top', 'comfortable depth'], details
    assert not errors, errors
    context.close()


def check_pause_and_resume(browser):
    context = browser.new_context(viewport={'width': 1440, 'height': 900})
    page = context.new_page()
    add_mock_fetch(page, delayed_step=1)
    open_page(page)
    page.locator('#training-fast button', has_text='Fast mode').click()
    page.locator('#training-fast button', has_text='Build with Jev').click()
    expect(page.locator('#training-fast button', has_text='Pause after this choice')).to_be_visible()
    page.wait_for_function("window.__apiCalls.filter(c=>c.method==='POST').length===2", timeout=3000)
    page.locator('#training-fast button', has_text='Pause after this choice').click()
    expect(page.locator('#training-fast')).to_contain_text('priority order 2 of 3')
    assert [c['body']['step'] for c in calls(page) if c['method'] == 'POST'] == [0, 1]
    expect(page.locator('#training-next .fast-priority')).to_have_count(2)
    assert all('Absolute must' not in badge for badge in page.locator('#training-next .fast-priority').all_text_contents())
    page.locator('#training-fast button', has_text='Resume with Jev').click()
    expect(page.locator('#training-next .fast-priority').nth(0)).to_contain_text('Absolute must', timeout=5000)
    assert [c['body']['step'] for c in calls(page) if c['method'] == 'POST'] == [0, 1, 2, 3]
    context.close()


def check_retry(browser):
    context = browser.new_context(viewport={'width': 390, 'height': 844})
    page = context.new_page()
    add_mock_fetch(page, fail_step_once=1)
    open_page(page)
    page.locator('#training-fast button', has_text='Fast mode').click()
    page.locator('#training-fast button', has_text='Build with Jev').click()
    expect(page.locator('#training-fast')).to_contain_text('temporary model outage')
    expect(page.locator('#training-fast button', has_text='Retry / resume')).to_be_visible()
    assert page.locator('#training-next .fast-priority').count() == 1
    assert 'Absolute must' not in page.locator('#training-next').inner_text()
    page.locator('#training-fast button', has_text='Retry / resume').click()
    expect(page.locator('#training-next .fast-priority').nth(0)).to_contain_text('Absolute must', timeout=5000)
    assert [c['body']['step'] for c in calls(page) if c['method'] == 'POST'] == [0, 1, 1, 2, 3]
    context.close()


def check_stale_plan(browser):
    context = browser.new_context(viewport={'width': 390, 'height': 844})
    page = context.new_page()
    add_mock_fetch(page, delayed_step=1)
    open_page(page)
    page.locator('#training-fast button', has_text='Fast mode').click()
    page.locator('#training-fast button', has_text='Build with Jev').click()
    page.wait_for_function("window.__apiCalls.filter(c=>c.method==='POST').length===2", timeout=3000)
    page.evaluate("window.remountPlan('b'.repeat(40))")
    expect(page.locator('#training-fast button', has_text='Full workout')).to_have_attribute('aria-pressed', 'true')
    page.wait_for_timeout(450)
    assert len([c for c in calls(page) if c['method'] == 'POST']) == 2
    assert page.locator('#training-next .fast-priority').count() == 0
    context.close()


def check_uncertain_cutoff(browser):
    context = browser.new_context(viewport={'width': 390, 'height': 844})
    page = context.new_page()
    add_mock_fetch(page, uncertain=True)
    open_page(page)
    page.locator('#training-fast button', has_text='Fast mode').click()
    page.locator('#training-fast button', has_text='Build with Jev').click()
    expect(page.locator('#training-fast')).to_contain_text('could not set a clear minimum-session cutoff', timeout=5000)
    assert 'Absolute must' not in page.locator('#training-next').inner_text()
    assert all(label.startswith('Priority') for label in page.locator('#training-next .fast-priority').all_text_contents())
    context.close()


def check_invalid_response(browser):
    context = browser.new_context(viewport={'width': 390, 'height': 844})
    page = context.new_page()
    add_mock_fetch(page)
    open_page(page)
    page.locator('#training-fast').get_by_role('button', name='Fast mode', exact=True).click()
    expect(page.locator('#training-fast').get_by_role('button', name='Build with Jev', exact=True)).to_be_enabled()
    page.evaluate("""() => {const original=window.fetch;window.fetch=async(url,options={})=>{
      if(options.method==='POST'){window.__invalidPosts=(window.__invalidPosts||0)+1;return new Response('{}',{status:200,headers:{'Content-Type':'application/json'}});}
      return original(url,options);
    }}""")
    page.locator('#training-fast').get_by_role('button', name='Build with Jev', exact=True).click()
    expect(page.locator('#training-fast')).to_contain_text('invalid or unchanged ranking step')
    assert page.evaluate('window.__invalidPosts') == 1
    assert page.locator('.fast-priority').count() == 0
    context.close()


def check_missing_plan(browser):
    context = browser.new_context(viewport={'width': 390, 'height': 844})
    page = context.new_page()
    add_mock_fetch(page)
    open_page(page)
    page.evaluate("TrainingFast.mount(document.getElementById('training-fast'), {})")
    expect(page.locator('#training-fast')).to_be_hidden()
    assert calls(page) == [], 'a missing plan must not request optional ranking'
    context.close()


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        check_success(browser, 390)
        check_success(browser, 1440)
        check_pause_and_resume(browser)
        check_retry(browser)
        check_stale_plan(browser)
        check_uncertain_cutoff(browser)
        check_invalid_response(browser)
        check_missing_plan(browser)
        browser.close()
    print(json.dumps({'status': 'passed', 'viewports': [390, 1440], 'mocked_api_calls_only': True,
                      'model_calls': 0, 'real_saves': 0}))


if __name__ == '__main__':
    argparse.ArgumentParser(description=__doc__).parse_args()
    main()
