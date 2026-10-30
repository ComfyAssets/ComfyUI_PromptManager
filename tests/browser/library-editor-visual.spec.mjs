import {test,expect} from './fixtures.mjs';
import {captureCanonicalState} from './history-canonical-comparison.mjs';

// Artifact-only captures of the editor states that have no concept counterpart (archive confirmation, Show archived,
// busy and error). The four concept-referenced modes are captured as concept/production pairs by
// workspace-library-editor.spec.mjs. There are deliberately no pixel assertions or baselines: the maintainer accepts
// these captures visually before any baseline is added.
test.use({locale:'en-US',timezoneId:'UTC',reducedMotion:'reduce'});
const workspace='/prompt_manager/workspace';
const editor=page=>page.locator('dialog.library-editor');
const variants=page=>page.getByRole('region',{name:'Saved negative variants',exact:true});
const note=page=>editor(page).locator('.warning-note');

test.beforeEach(async({page,request})=>{
    const reset=await request.post('/__test__/library-editor/reset',{data:{}});await expect(reset).toBeOK();page.fixture=await reset.json();
});

for(const [width,height,theme] of [[1600,1100,'dark'],[390,844,'light']])test(`editor state captures for maintainer acceptance ${width} ${theme}`,async({page},testInfo)=>{
    await page.setViewportSize({width,height});const f=page.fixture;
    await page.goto(`${workspace}#/library/${f.positive_id}`);await expect(variants(page)).toBeVisible();
    await page.getByRole('combobox',{name:'Theme',exact:true}).selectOption(theme);
    const shot=name=>captureCanonicalState(page,testInfo,`production-${name}-${width}-${theme}`,name.startsWith('editor-archived')?'.library-history .timeline-panel':'dialog.library-editor');

    await variants(page).getByRole('button',{name:'Select negative variant 2',exact:true}).click();
    await page.getByRole('button',{name:'Edit metadata',exact:true}).click();await expect(editor(page)).toBeVisible();
    await expect(editor(page).getByRole('button',{name:'Save',exact:true})).toBeEnabled();
    await editor(page).getByRole('button',{name:'Archive variant…',exact:true}).click();
    await expect(editor(page).getByRole('button',{name:'Archive',exact:true})).toBeEnabled();await shot('editor-archive');

    let release;const held=new Promise(resolve=>{release=resolve;});
    await page.route(`**/prompt_manager/pairs/${f.pair_id}`,async route=>{if(route.request().method()==='PATCH')await held;await route.continue();});
    await editor(page).getByRole('button',{name:'Archive',exact:true}).click();
    await expect(note(page)).toBeVisible();await shot('editor-busy');
    release();await expect(editor(page)).toHaveCount(0);await page.unroute(`**/prompt_manager/pairs/${f.pair_id}`);

    await variants(page).getByRole('button',{name:/^Show archived/}).click();
    await expect(variants(page).locator('.library-variant-archived').first()).toBeVisible();await shot('editor-archived-library');

    await page.goto(`${workspace}#/library`);await page.getByRole('button',{name:'+ New prompt',exact:true}).click();
    await expect(editor(page).getByRole('button',{name:'Save',exact:true})).toBeEnabled();
    await page.route('**/prompt_manager/library',route=>route.request().method()==='POST'
        ?route.fulfill({status:400,contentType:'application/json',body:'{"success":false,"error":"Title is too long.","code":null}'}):route.continue(),{times:1});
    await editor(page).getByLabel('Title',{exact:true}).fill(`Long title ${'W'.repeat(160)} with a very long tail`);
    await editor(page).getByLabel('Positive prompt',{exact:true}).fill('A quiet harbor at dawn');
    await editor(page).getByLabel('Notes',{exact:true}).fill(Array.from({length:40},(_,index)=>`Long note line ${index}`).join('\n'));
    await editor(page).getByRole('button',{name:'Save',exact:true}).click();
    await expect(note(page)).toHaveText('Not saved: Title is too long. Your entries are kept.');
    await shot('editor-long-error');
});
