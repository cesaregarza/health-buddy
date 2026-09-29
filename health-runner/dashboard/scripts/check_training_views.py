#!/usr/bin/env python3
"""Offline browser regression for compact and detailed training views."""
import argparse
from datetime import datetime
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from dashboard_fixture import snapshot, AS_OF
from api_fixture import META,envelope
from preview import render
import training_fast as fast
from playwright.sync_api import sync_playwright, expect


def fixture():
    data = snapshot()
    data['meta'].update(META)
    data['config']={'integrations':{'jev':{'enabled':True}}}
    data['meta']['origin_full_sha'] = 'a' * 40
    # Add contrasting prescription detail and recorded evidence so both views
    # have meaningful content to reveal or keep available.
    recommendation = data['training_detail']['prescriptions'][0]['recommendation']
    template = recommendation['template']
    template['exercises'][0].update({
        'next_target': '20 lb per hand × 10; backoff 15 lb per hand × 12; stop at 2 RIR.',
        'rir': '2 RIR',
        'notes': 'Example setup detail',
    })
    session = data['training_detail']['days'][0]['sessions'][0]
    session['cardio'] = [{'duration_seconds': 600, 'speed_mph': 3.1, 'incline_percent': 2}]
    session['notes'] = 'Example recorded session note'
    data['progress']['strength']['exercises'] = [
        {**data['progress']['strength']['exercises'][0], 'name': name, 'unit': 'lb', 'points': [
            {'d': '2026-08-18', 'load': 20, 'reps': 10, 'work_sets': []},
            {'d': data['training_detail']['days'][0]['d'], 'load': load, 'reps': reps, 'work_sets': []},
        ]} for name, load, reps in [('More reps', 20, 11), ('More weight', 22.5, 8), ('Both', 22.5, 11)]
    ]
    return data



def expose_progression_test_hooks(html):
    """Expose closure-local renderers only in the fabricated preview fixture."""
    marker = "\n})();\n</script>"
    if html.count(marker) != 1:
        raise AssertionError('Expected one dashboard script closure for the test hook')
    hook = "\nwindow.__trainingRegression = {renderProgressionTarget, renderRecommendation, renderTraining};"
    return html.replace(marker, hook + marker, 1)


def check_numeric_highlights(page):
    """Check numeric-only output from the helper and rendered recommendation cards."""
    result = page.evaluate("""() => {
      const reps={status:'reps',load_basis:'per_hand',current_load:20,rep_targets:[11,10],evidence_sets:[
        {set_number:1,load:20,reps:10},{set_number:2,load:20,reps:9}]};
      const partial={status:'reps',load_basis:'per_hand',current_load:20,rep_targets:[11,11],evidence_sets:[
        {set_number:1,load:20,reps:10},{set_number:2,load:20,reps:11}]};
      const held={status:'hold_2_rir',load_basis:'per_hand',current_load:20,rep_targets:[11,10],evidence_sets:[
        {set_number:1,load:20,reps:10},{set_number:2,load:20,reps:10}]};
      const load={status:'coarse_increment_trial',load_basis:'per_hand',current_load:20,recommended_load:22.5,rep_targets:[8,8],evidence_sets:[
        {set_number:1,load:20,reps:12},{set_number:2,load:20,reps:12}]};
      const incomplete={status:'incomplete',load_basis:'per_hand',current_load:20,rep_targets:[11,11],review_required:true,evidence_sets:[
        {set_number:1,load:20,reps:10}]};
      const inputs=[
        ['all-rising-reps','20 lb per hand; 2 primary sets x 11/10 reps; add reps only while every set remains at least 2 RIR',reps,'target',['11','10']],
        ['partial-collapsed-reps','20 lb per hand; 2 primary sets x 11 reps; add reps only while every set remains at least 2 RIR',partial,'target',['11']],
        ['held-reps','20 lb per hand; 2 primary sets x 11/10 reps; add reps only while every set remains at least 2 RIR',held,'target',['11']],
        ['coarse-load-target','Trial one primary set at 22.5 lb per hand for 8 reps with at least 2 RIR; complete the remaining primary sets at 20 lb per hand.',load,'target',['22.5']],
        ['eligible-load','All 2 primary sets at 22.5 lb per hand, starting at 8 reps',{...load,status:'eligible_next_load',current_load:22.5},'target',['22.5']],
        ['missing-evidence','20 lb per hand; 2 primary sets x 11/10 reps',{...reps,evidence_sets:[]},'target',[]],
        ['load-detail','22.5 lb per hand',load,'load',['22.5']],
        ['work-detail','2 primary sets × 11/10 reps',reps,'work',['11','10']],
        ['review-required','20 lb per hand; 2 primary sets x 11 reps; evidence incomplete, preserve the reviewed baseline target',incomplete,'target',[]],
        ['unknown-backoff-prose','20 lb per hand; backoff 15 lb per hand × 12; stop at 2 RIR.',reps,'target',[]],
        ['unknown-summary','Top range at 2 RIR; verify the smallest available increment.',load,'target',[]],
      ];
      const host=document.createElement('div');host.style.cssText='position:fixed;left:-10000px;top:0';document.body.append(host);
      try {
        const direct=[],originalTheme=document.documentElement.getAttribute('data-theme');
        const blueProbe=document.createElement('span');blueProbe.style.color='var(--s1)';host.append(blueProbe);
        for(const theme of ['light','dark']) {
          document.documentElement.setAttribute('data-theme',theme);
          for(const [name,text,result,field,expected] of inputs) {
            const parent=document.createElement('div');window.__trainingRegression.renderProgressionTarget(parent,text,result,field);host.append(parent);
            direct.push({name,theme,text:parent.textContent,original:text,expected,
              spans:[...parent.querySelectorAll('.progression-increase')].map(el=>({text:el.textContent,field:el.dataset.increase,color:getComputedStyle(el).color})),
              parentColor:getComputedStyle(parent).color,blue:getComputedStyle(blueProbe).color});
          }
        }
        if(originalTheme===null)document.documentElement.removeAttribute('data-theme');else document.documentElement.setAttribute('data-theme',originalTheme);
        const root=document.createElement('div');host.append(root);
        const repText=inputs[0][1];
        window.__trainingRegression.renderRecommendation({recommendation:{date:'2026-08-20',template:{label:'Synthetic',exercises:[{exercise:'Regression fixture',next_target:repText,load:'20 lb per hand',work:'2 primary sets x 11/10 reps',progression_result:reps}]}}},root,true);
        const row=root.querySelector('.rx-row');
        const targets=['.target','.gym-target','.prescription-target'].map(sel=>{const el=row.querySelector(sel);return {text:el.textContent,spans:[...el.querySelectorAll('.progression-increase')].map(x=>({text:x.textContent,field:x.dataset.increase}))};});
        const work=[...row.querySelector('.rx-details dd:nth-of-type(4)').querySelectorAll('.progression-increase')].map(x=>({text:x.textContent,field:x.dataset.increase}));
        const loadRoot=document.createElement('div');host.append(loadRoot);const loadText=inputs[3][1];
        window.__trainingRegression.renderRecommendation({recommendation:{date:'2026-08-20',template:{label:'Synthetic load',exercises:[{exercise:'Load fixture',next_target:loadText,load:'22.5 lb per hand',work:'2 primary sets x 8 reps',progression_result:load}]}}},loadRoot,true);
        const loadRow=loadRoot.querySelector('.rx-row');
        const loadRendered=['.gym-target','.rx-details dd:nth-of-type(2)'].map(sel=>[...loadRow.querySelector(sel).querySelectorAll('.progression-increase')].map(x=>({text:x.textContent,field:x.dataset.increase})));
        return {direct,targets,work,loadRendered};
      } finally {host.remove();}
    }""")
    direct = result['direct']
    for case in direct:
        expected_text = case['original'].replace('x 11 reps', 'x 11/11 reps') if case['name'] == 'partial-collapsed-reps' else case['original']
        assert case['text'] == expected_text, f"{case['name']}: target text changed"
        assert [span['text'] for span in case['spans']] == case['expected'], f"{case['name']}: unexpected highlighted pieces"
        for span in case['spans']:
            assert span['field'] in ('reps', 'load')
            assert span['color'] == case['blue'], f"{case['name']} ({case['theme']}): increase is not blue"
            assert span['color'] != case['parentColor'], f"{case['name']} ({case['theme']}): container is also blue"
    partial = next(c for c in direct if c['name']=='partial-collapsed-reps' and c['theme']=='light')
    assert '11/11 reps' in partial['text']
    assert [s['text'] for s in result['targets'][0]['spans']] == ['11','10']
    assert [s['text'] for s in result['targets'][1]['spans']] == ['11','10']
    assert [s['text'] for s in result['targets'][2]['spans']] == ['11','10']
    assert result['work'] == [{'text':'11','field':'reps'},{'text':'10','field':'reps'}]
    assert result['loadRendered'] == [[{'text':'22.5','field':'load'}],[{'text':'22.5','field':'load'}]]
    return len(direct)


def check_published_views(html):
    """Inspect a saved served page offline; only change its view buttons."""
    results = []
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for width in (390, 1440):
            context = browser.new_context(viewport={'width': width, 'height': 1000})
            page = context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            def route(request):
                if request.request.resource_type == 'document':
                    request.fulfill(status=200, content_type='text/html', body=html)
                else:
                    request.abort()
            context.route('**/*', route)
            page.goto('http://localhost/saved-dashboard')
            page.locator('#tab-training').click()
            area = page.locator('#training-next')
            details = area.locator('[data-workout-detail]')
            assert details.count() > 0, 'The served plan has no view-controlled details'
            modes = {}
            for mode in ('compact', 'detailed', 'compact'):
                page.locator(f'#gym-view [data-view={mode}]').click()
                for detail in details.all():
                    assert detail.evaluate('(el) => el.open') == (mode == 'detailed')
                    body = detail.locator(':scope > :not(summary)').first
                    if mode == 'detailed':
                        expect(body).to_be_visible()
                    else:
                        expect(body).to_be_hidden()
                for row in area.locator('.rx-row').all():
                    target = row.locator('.gym-target' if mode == 'compact' else '.target')
                    expect(target).to_be_visible()
                    assert target.evaluate('(el)=>getComputedStyle(el).color') == row.evaluate('(el)=>getComputedStyle(el).color'), 'Target container must remain neutral'
                    if mode == 'detailed':
                        detail = row.locator('.prescription-target')
                        if detail.count():
                            assert detail.evaluate('(el)=>getComputedStyle(el).color') == row.evaluate('(el)=>getComputedStyle(el).color'), 'Detailed target container must remain neutral'
                modes[mode] = {'expanded': area.locator('[data-workout-detail][open]').count(),
                               'height': round(area.bounding_box()['height'])}
            assert modes['detailed']['height'] > modes['compact']['height']
            assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
            assert not errors, errors
            highlighted_count = page.locator('#training-next .progression-increase').count()
            assert highlighted_count > 0, 'Saved page contains no numeric progression highlights'
            assert page.locator('#training-next .progression-increase').evaluate_all('''els=>els.every(el=>{
                const probe=document.createElement('span');probe.style.color='var(--s1)';el.parentElement.append(probe);
                const valid=/^\d+(?:\.\d+)?$/.test(el.textContent) && ['load','reps'].includes(el.dataset.increase)
                    && getComputedStyle(el).color===getComputedStyle(probe).color
                    && getComputedStyle(el).color!==getComputedStyle(el.parentElement).color;
                probe.remove();return valid;
            })'''), 'Only numeric values should have the theme blue color'
            colors = page.locator('#training-next .rx-row').evaluate_all('''rows => rows.map(row => {
                const chip=row.querySelector('.chip'), target=row.querySelector('.gym-target');
                return {label:chip.textContent, badge:getComputedStyle(chip).color,
                        target:getComputedStyle(target).color};
            }).reduce((groups,row)=>{
                const key=JSON.stringify(row);groups[key]=(groups[key]||0)+1;return groups;
            }, {})''')
            results.append({'width': width, 'modes': modes, 'plan_colors': colors,
                            'highlighted_count': highlighted_count,
                            'highlighted_types': page.locator('#training-next .progression-increase').evaluate_all('els=>els.map(el=>el.dataset.increase).reduce((counts,type)=>{counts[type]=(counts[type]||0)+1;return counts},{})')})
            context.close()
        browser.close()
    print(json.dumps({'status': 'passed', 'saved_page': results, 'real_saves': 0,
                      'javascript_errors': len(errors)}))

def main():
    errors = []
    data = fixture()
    html = expose_progression_test_hooks(render(data, AS_OF))
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for width in (390, 1440):
            context = browser.new_context(
                viewport={'width': width, 'height': 1000},
                reduced_motion='reduce',
            )
            page = context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))

            def offline_route(route):
                if route.request.resource_type != 'document':
                    raise AssertionError(f'Unexpected external request: {route.request.url}')
                route.fulfill(status=200, content_type='text/html', body=html)

            context.route('**/*', offline_route)
            plan = fast.select_plan(data, '2026-08-20', 'a' * 40, 'fixture')
            fast_fixture = {'state': fast.initial(plan), 'posts': 0}
            def fast_route(route):
                if route.request.method == 'POST':
                    body = route.request.post_data_json
                    assert body == {'date': '2026-08-20', 'revision': 'a' * 40, 'step': fast_fixture['state']['step']}
                    assert route.request.headers['x-health-action'] == 'rank-training'
                    request = fast.question(plan, fast_fixture['state'])
                    if fast_fixture['state']['phase'] == 'not_started':
                        answers = {key: {'type':'score','score':4 if key=='score_e2' else 2,
                            'probabilities':{str(i):float(i==(4 if key=='score_e2' else 2)) for i in range(5)}}
                            for key in request['questions']}
                    else:
                        key, spec = next(iter(request['questions'].items()))
                        winner = 'keep_2' if key == 'must' else max(spec['criteria'])
                        answers = {key:{'type':'choice','choice':winner,
                            'probabilities':{option:float(option==winner) for option in spec['criteria']}}}
                    fast_fixture['state'] = fast.advance(plan, fast_fixture['state'], {'model':'fixture','answers':answers})
                    fast_fixture['posts'] += 1
                route.fulfill(status=200, content_type='application/json', body=json.dumps(envelope(fast_fixture['state'])))
            page.route('**/v1/training/fast**', fast_route)
            page.clock.set_fixed_time(datetime.fromisoformat('2026-08-20T15:00:00+00:00'))
            page.goto('http://localhost/training-views')
            page.locator('#tab-training').click()
            check_numeric_highlights(page)
            comparisons = page.locator('#last-session > .training-list > li')
            assert comparisons.count() == 3
            for row, expected in zip(comparisons.all(), [[['reps','11']], [['load','22.5']], [['load','22.5'],['reps','11']]]):
                assert row.locator('.progression-increase').evaluate_all('els=>els.map(el=>[el.dataset.increase,el.textContent])') == expected, row.inner_text()

            page.evaluate("DATA.progress.strength.exercises.forEach(x => x.comparison_unverified = true); window.__trainingRegression.renderTraining()")
            expect(page.locator('#last-session')).not_to_contain_text('Changed comparable working sets')
            expect(page.locator('#last-session')).to_contain_text('No comparable working-set changes available')
            assert page.locator('#last-session > .training-list > li').count() == 0
            page.evaluate("DATA.progress.strength.exercises.forEach(x => x.comparison_unverified = false); window.__trainingRegression.renderTraining()")
            assert page.locator('#last-session > .training-list > li').count() == 3

            compact = page.locator('#training-next')
            expect(page.locator('#gym-view [data-view=compact]')).to_have_attribute('aria-pressed', 'true')
            expect(compact.locator('.gym-target').first).to_be_visible()
            expect(compact.locator('.gym-target').first).to_contain_text('backoff')
            expect(compact.locator('.gym-target').first).to_contain_text('per hand')
            expect(page.locator('#gym-view-description')).to_be_visible()
            assert compact.locator('[data-workout-detail]').count() >= 3
            for detail in compact.locator('[data-workout-detail]').all():
                assert detail.evaluate('(el) => !el.open')
                assert not detail.locator(':scope > :not(summary)').first.is_visible()

            # A real draft and checklist state must survive view changes intact.
            page.locator('#training-next input[type=checkbox]').first.check()
            page.locator('#workout-editor > summary').click()
            page.locator('[data-field=exercise-choice]').select_option('custom')
            page.locator('#add-workout-set').click()
            draft = page.locator('.entered-set').first
            draft.locator('[data-field=exercise]').fill('draft press')
            draft.locator('[data-field=load_lb]').fill('42')
            draft.locator('[data-field=reps]').fill('9')

            page.locator('#gym-view [data-view=detailed]').click()
            expect(page.locator('#gym-view [data-view=detailed]')).to_have_attribute('aria-pressed', 'true')
            expect(compact.locator('.target').first).to_be_visible()
            expect(compact.locator('.gym-target').first).to_be_hidden()
            expect(compact.locator('.target').first).to_contain_text('backoff')
            for detail in compact.locator('[data-workout-detail]').all():
                assert detail.evaluate('(el) => el.open')
                body = detail.locator(':scope > :not(summary)').first
                expect(body).to_be_visible()
                assert body.inner_text().strip()

            session_detail = page.locator('#last-session .session-details')
            expect(session_detail).to_have_count(1)
            assert session_detail.evaluate('(el) => el.open')
            expect(session_detail).to_contain_text('20 lb')
            expect(session_detail).to_contain_text('10.0 min')
            notes_detail = page.locator('#last-session details:has(> summary:text-is("Session notes"))')
            expect(notes_detail).to_have_count(1)
            assert notes_detail.evaluate('(el) => el.open')
            expect(notes_detail.locator(':scope > :not(summary)').first).to_contain_text('Example recorded session note')
            expect(page.locator('#workout-editor')).to_have_attribute('open', '')
            expect(draft.locator('[data-field=exercise]')).to_have_value('draft press')
            expect(draft.locator('[data-field=load_lb]')).to_have_value('42')
            expect(draft.locator('[data-field=reps]')).to_have_value('9')
            expect(page.locator('#training-next input[type=checkbox]').first).to_be_checked()

            # Date navigation rerenders training content but keeps the chosen mode.
            page.locator('#training-date').fill('2026-08-19')
            page.locator('#training-date').dispatch_event('change')
            expect(page.locator('#gym-view [data-view=detailed]')).to_have_attribute('aria-pressed', 'true')
            page.locator('#training-date').fill('2026-08-20')
            page.locator('#training-date').dispatch_event('change')
            expect(page.locator('#gym-view [data-view=detailed]')).to_have_attribute('aria-pressed', 'true')

            page.locator('#gym-view [data-view=compact]').click()
            for detail in page.locator('#training-next [data-workout-detail]').all():
                assert detail.evaluate('(el) => !el.open')
            expect(page.locator('#workout-editor')).to_have_attribute('open', '')
            expect(draft.locator('[data-field=exercise]')).to_have_value('draft press')
            expect(page.locator('#training-next input[type=checkbox]').first).to_be_checked()

            page.reload()
            page.locator('#tab-training').click()
            expect(page.locator('#gym-view [data-view=compact]')).to_have_attribute('aria-pressed', 'true')
            expect(page.locator('#training-next .gym-target').first).to_be_visible()
            page.locator('#gym-view [data-view=detailed]').click()
            page.reload()
            page.locator('#tab-training').click()
            expect(page.locator('#gym-view [data-view=detailed]')).to_have_attribute('aria-pressed', 'true')
            for detail in page.locator('#training-next [data-workout-detail]').all():
                assert detail.evaluate('(el) => el.open')
                expect(detail.locator(':scope > :not(summary)').first).to_be_visible()
            # Run the actual integrated panel with the real ranking engine and mocked Jev.
            assert fast_fixture['posts'] == 0
            page.locator('#training-fast').get_by_role('button', name='Fast mode', exact=True).click()
            page.locator('#training-fast').get_by_role('button', name='Build with Jev', exact=True).click()
            expect(page.locator('#training-next .fast-priority').filter(has_text='Absolute must')).to_have_count(2)
            ranked_order = page.locator('#training-next .rx-row').evaluate_all('els=>els.map(el=>el.dataset.exerciseIndex)')
            assert ranked_order == ['2','5','4','3','1','0']
            posts = fast_fixture['posts']
            assert posts == 6
            expect(page.locator('#training-next .rx-row[data-exercise-index="0"] input')).to_be_checked()
            expect(page.locator('.entered-set [data-field=exercise]').first).to_have_value('draft press')
            expect(page.locator('.entered-set [data-field=load_lb]').first).to_have_value('42')
            expect(page.locator('#gym-view [data-view=detailed]')).to_have_attribute('aria-pressed', 'true')
            page.locator('#training-date').fill('2026-08-19')
            page.locator('#training-date').dispatch_event('change')
            assert page.locator('#training-next .rx-row').evaluate_all('els=>els.map(el=>el.dataset.exerciseIndex)') == ranked_order
            assert fast_fixture['posts'] == posts, 'Rerender must not start new Jev requests'
            page.locator('#training-fast').get_by_role('button', name='Full workout', exact=True).click()
            assert page.locator('#training-next .rx-row').evaluate_all('els=>els.map(el=>el.dataset.exerciseIndex)') == ['0','1','2','3','4','5']
            expect(page.locator('#training-next input[type=checkbox]').first).to_be_checked()
            assert not errors, errors
            context.close()
        browser.close()
    print(json.dumps({'status': 'passed', 'viewports': [390, 1440], 'external_requests': 0,
                      'real_saves': 0, 'javascript_errors': len(errors)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--html', type=Path, help='Read-only mode check of a saved served page; no draft or save actions')
    args = parser.parse_args()
    if args.html:
        check_published_views(args.html.read_text())
    else:
        main()
