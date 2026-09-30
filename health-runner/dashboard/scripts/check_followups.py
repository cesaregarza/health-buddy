#!/usr/bin/env python3
"""Offline browser regressions for drafts, central-save UI and changed records."""
from datetime import datetime
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from dashboard_fixture import snapshot,AS_OF
from preview import render
from check_browser import layout,contrast
from api_fixture import META,envelope
from playwright.sync_api import sync_playwright,expect


def main():
    errors=[]
    with sync_playwright() as p:
        browser=p.chromium.launch()
        for width in (320,390,768,1440):
            for theme in ('light','dark'):
                context=browser.new_context(viewport={'width':width,'height':900},color_scheme=theme,reduced_motion='reduce')
                data=snapshot()
                data['meta'].update(META)
                # Stable bounds let the BP test detect mean movement independent of auto-scaling.
                data['bp']=[{'d':'2026-08-18','t':'2026-08-18T08:00','sys':120,'dia':80,'session':'morning','status':'valid'},
                            {'d':'2026-08-19','t':'2026-08-19T08:00','sys':122,'dia':82,'session':'morning','status':'valid'},
                            {'d':'2026-08-19','t':'2026-08-19T08:01','sys':128,'dia':86,'session':'morning','status':'unknown'},
                            {'d':'2026-08-19','t':'2026-08-19T08:02','sys':150,'dia':100,'session':'morning','status':'invalid'}]
                page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
                page.clock.set_fixed_time(datetime.fromisoformat('2026-08-20T15:00:00+00:00'))
                calls=[]
                def route(req):
                    if req.request.resource_type=='document':req.fulfill(status=200,content_type='text/html',body=render(data,AS_OF))
                    elif req.request.url.endswith('/v1/capabilities'):
                        req.fulfill(status=200,json=envelope({'writable':True}))
                    elif req.request.url.endswith('/v1/workouts'):
                        value=req.request.post_data_json;calls.append(value)
                        if len(calls)==1:req.fulfill(status=503,json={'error':{'code':'source_unavailable'},'meta':{}})
                        else:req.fulfill(status=200,headers={'ETag':'"rev-1"'},json=envelope({'saved':True,'sessionId':value['session_id'],'duplicate':False,'projection':{'state':'pending'}},1))
                    else:raise AssertionError('Unexpected request '+req.request.url)
                context.route('**/*',route)
                page.goto('http://localhost/dashboard')
                expect(page.locator('#changes-since-open')).to_contain_text('baseline is set')
                page.locator('a[href="#section-body"]').click()
                mean=page.locator('#bp [data-series=valid-morning-mean]').first
                expect(mean).to_be_attached();before=mean.get_attribute('d')
                page.evaluate("DATA.bp.find(p=>p.status==='unknown').sys=140")
                page.locator('#range [data-days="30"]').click()
                assert page.locator('#bp [data-series=valid-morning-mean]').first.get_attribute('d')==before
                page.locator('#tab-training').click()
                page.locator('#training-next input[type=checkbox]').first.check()
                page.locator('#workout-editor > summary').click()
                page.locator('[data-field=exercise-choice]').select_option('custom')
                page.locator('#add-workout-set').click()
                row=page.locator('.entered-set').first
                row.locator('[data-field=exercise]').fill('example_press')
                row.locator('[data-field=equipment]').fill('example_machine')
                row.locator('[data-field=load_basis]').select_option('per_hand')
                row.locator('[data-field=load_lb]').fill('0')
                row.locator('[data-field=reps]').fill('12')
                row.locator('[data-field=notes]').fill('Example user-entered actual results')
                layout(page);contrast(page)
                page.reload()
                expect(page.locator('#training-next input[type=checkbox]').first).to_be_checked()
                page.locator('#workout-editor > summary').click()
                expect(page.locator('.entered-set [data-field=reps]')).to_have_value('12')
                expect(page.locator('.entered-set [data-field=rir]')).to_have_value('')
                page.locator('#review-workout').click()
                expect(page.locator('#workout-review')).to_contain_text('0 lb')
                expect(page.locator('#workout-review')).to_contain_text('RIR unknown')
                page.locator('#save-workout').click()
                expect(page.locator('#draft-status')).to_contain_text('source_unavailable')
                page.locator('#save-workout').click()
                expect(page.locator('#draft-status')).to_contain_text('Saved to the central health log')
                assert calls[0]==calls[1] and calls[0]['sets'][0]['load_lb']==0 and calls[0]['sets'][0]['rir'] is None
                page.reload();page.locator('#workout-editor > summary').click()
                expect(page.locator('#draft-status')).to_contain_text('Saved to the central log')
                expect(page.locator('#review-workout')).to_be_hidden()
                layout(page)
                # New and corrected content are distinguished across reloads of the same device.
                data['weight'].append({'d':'2026-08-21','lb':210,'src':'log'})
                data['visit']['prepared_questions'][0]['question']='Corrected example question?'
                data['labs']['analytes'][0]['points'].insert(0,{'d':'2025-01-01','v':100})
                page.reload();page.locator('#tab-overview').click()
                expect(page.locator('#changes-since-open')).to_contain_text('1 new')
                expect(page.locator('#changes-since-open')).to_contain_text('1 updated')
                lab_row=page.locator('.change-row').filter(has_text='Lab results')
                expect(lab_row).to_contain_text('1 new')
                expect(lab_row).not_to_contain_text('updated')
                page.reload()
                expect(page.locator('#changes-since-open')).to_contain_text('No new or changed records')
                # A malformed nested draft must not break the page.
                page.evaluate("localStorage.setItem('health-workout-draft-v1',JSON.stringify({version:1,revision:1,session_id:'bad',date:'2026-08-20',sets:[null]}))")
                page.reload();page.locator('#tab-training').click()
                page.locator('#workout-editor > summary').click()
                expect(page.locator('#draft-status')).to_contain_text('could not be restored')
                expect(page.locator('#review-workout')).to_be_disabled()
                assert page.evaluate("JSON.parse(localStorage.getItem('health-workout-draft-v1')).session_id")=='bad'
                page.once('dialog',lambda dialog:dialog.accept())
                page.get_by_role('button',name='Resolve saved retry request',exact=True).click()
                page.locator('[data-field=notes]').first.fill('New draft after malformed storage')
                expect(page.locator('#review-workout')).to_be_enabled()
                assert page.evaluate("JSON.parse(localStorage.getItem('health-workout-draft-v1')).notes")=='New draft after malformed storage'
                assert not errors,errors
                context.close()
        browser.close()
    print(json.dumps({'status':'passed','widths':[320,390,768,1440],'themes':['light','dark'],'save_requests':'mocked only','javascript_errors':len(errors)}))


if __name__=='__main__':main()
