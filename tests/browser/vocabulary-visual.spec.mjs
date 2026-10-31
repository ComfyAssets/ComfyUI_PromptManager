import {writeFile} from 'node:fs/promises';
import {test,expect} from './fixtures.mjs';
import {mapConceptLayout} from './concept-inventory.mjs';
import {captureCanonicalState} from './history-canonical-comparison.mjs';
import {pageInventoryOf,inventoryOf,diffInventory,mapVocabularyConcept,mapVocabularyDialog,compareVocabularyPage} from './vocabulary-canonical-comparison.mjs';

// Artifact-only captures of Tags & categories states that have no concept counterpart: the Manage categories
// preview, loading and 1,000-category cap notes, tag conflict and restore notes, the Delete tag confirmation (D14),
// empty lists (D12), a failed read with Retry, and Load more (D5). The page and the three concept dialogs are captured
// as concept/production pairs by workspace-vocabulary.spec.mjs. There are deliberately no pixel assertions or
// baselines: the maintainer accepts these captures visually before any baseline is added. The last test (#332 D143,
// D144) compares the category rows with image counts and the Manage category preview against the concept.
test.use({locale:'en-US',timezoneId:'UTC',reducedMotion:'reduce'});
const route='/prompt_manager/workspace#/tags';
const main=page=>page.locator('#workspace-main');
const rows=page=>main(page).locator('tbody tr');
const dialog=page=>page.locator('dialog.vocabulary-dialog');
const button=(scope,name)=>scope.getByRole('button',{name,exact:true});
const note=page=>dialog(page).locator('.warning-note');
const tagSurface='#workspace-main .two-col > section.surface:first-child';
const categoryOperation=(page,name)=>main(page).locator('.operation').filter({has:page.getByRole('heading',{level:3,name,exact:true})});

async function seed(request,data={}) {const response=await request.post('/__test__/vocabulary/reset',{data});await expect(response).toBeOK();return response.json();}
async function open(page,theme,count) {
    await page.goto('about:blank');await page.goto(route);
    await page.getByRole('combobox',{name:'Theme',exact:true}).selectOption(theme);
    if(count!==undefined)await expect(rows(page)).toHaveCount(count);
}
async function openDialog(page,trigger) {await trigger.click();await expect(dialog(page)).toBeVisible();return dialog(page);}
const empty=(manifest,kind)=>({success:true,library_epoch:manifest.epoch,total:0,has_more:false,next_cursor:null,[kind]:[]});

for(const [width,height] of [[1600,1100],[390,844]])for(const theme of ['dark','light'])test(`vocabulary state captures for maintainer acceptance ${width} ${theme}`,async({page,request},testInfo)=>{
    await page.setViewportSize({width,height});
    const shot=(name,selector='dialog.vocabulary-dialog')=>captureCanonicalState(page,testInfo,`production-${name}-${width}-${theme}`,selector);
    let manifest=await seed(request);await open(page,theme,manifest.tags.length);

    // Manage categories: preview shown (D9).
    let box=await openDialog(page,categoryOperation(page,'Manage category').getByRole('button',{name:'Review',exact:true}));
    await expect(button(box,'Preview')).toBeEnabled();
    await box.getByLabel('Proposed new category name',{exact:true}).fill('Scenery');await button(box,'Preview').click();
    await expect(box.locator('p.small')).toHaveText('“Landscapes” is used by 3 positive prompts. Apply renames it to “Scenery”.');
    await shot('manage-preview');
    await page.keyboard.press('Escape');await expect(box).toHaveCount(0);

    // Rename or merge: conflict note, then restore note (the server answers 409; nothing is written).
    for(const [name,code,text] of [['rename-conflict','vocabulary-conflict',/This tag changed since this dialog opened/],
        ['rename-restored','storage-changed',/Prompt storage was restored since this dialog opened/]]) {
        await page.route('**/prompt_manager/vocabulary/tags/*',item=>item.request().method()==='PATCH'
            ?item.fulfill({status:409,json:{success:false,code,error:'Changed.',library_epoch:manifest.epoch}}):item.continue(),{times:1});
        box=await openDialog(page,rows(page).first().getByRole('button',{name:'Rename / merge',exact:true}));
        await box.getByLabel('New name; existing names merge',{exact:true}).fill('cinematic_light');await button(box,'Apply').click();
        await expect(note(page)).toHaveText(text);await shot(name);
        await page.keyboard.press('Escape');await expect(box).toHaveCount(0);
        await expect(rows(page)).toHaveCount(manifest.tags.length);
    }

    // Delete tag confirmation (D14) with the real impact sentence.
    box=await openDialog(page,rows(page).first().getByRole('button',{name:'Rename / merge',exact:true}));
    await button(box,'Delete tag…').click();await expect(button(box,'Delete')).toBeEnabled();
    await expect(box.locator('p.small')).toBeVisible();await shot('delete-confirmation');
    await page.keyboard.press('Escape');await expect(box).toHaveCount(0);

    // Manage categories while further category pages load, and at the 1,000-category cap (ledger ruling).
    manifest=await seed(request,{extra_categories:150});
    let release;const held=new Promise(resolve=>{release=resolve;});
    await page.route('**/prompt_manager/vocabulary/categories?*',async item=>{if(new URL(item.request().url()).searchParams.has('cursor'))await held;await item.continue();});
    await open(page,theme,manifest.tags.length);
    box=await openDialog(page,categoryOperation(page,'Manage category').getByRole('button',{name:'Review',exact:true}));
    await expect(note(page)).toHaveText('Loading categories…');await shot('manage-loading');
    release();await expect(note(page)).toBeHidden();await page.keyboard.press('Escape');await expect(box).toHaveCount(0);
    await page.unroute('**/prompt_manager/vocabulary/categories?*');
    await page.route('**/prompt_manager/vocabulary/categories?*',item=>{
        const cursor=new URL(item.request().url()).searchParams.get('cursor');const index=cursor?Number(cursor.slice(1)):0;
        const categories=Array.from({length:100},(_,offset)=>({value:`many_${String(index*100+offset).padStart(5,'0')}`,prompt_count:1}));
        return item.fulfill({json:{success:true,library_epoch:manifest.epoch,total:5000,has_more:true,next_cursor:`c${index+1}`,categories}});
    });
    await open(page,theme);await expect(categoryOperation(page,'many_00000')).toHaveCount(1);
    const review=categoryOperation(page,'Manage category').getByRole('button',{name:'Review',exact:true});
    // Every mocked page has more, so scrolling to Review would keep appending; click it in place.
    await review.evaluate(node=>node.click());await expect(dialog(page)).toBeVisible();
    await expect(note(page)).toHaveText('Only the first 1,000 categories are listed.');await shot('manage-capped');
    await page.keyboard.press('Escape');await page.unroute('**/prompt_manager/vocabulary/categories?*');

    // Empty lists (D12).
    await page.route('**/prompt_manager/vocabulary/tags?*',item=>item.fulfill({json:empty(manifest,'tags')}));
    await page.route('**/prompt_manager/vocabulary/categories?*',item=>item.fulfill({json:empty(manifest,'categories')}));
    await open(page,theme);
    await expect(main(page).getByText('No tags yet.',{exact:true})).toBeVisible();await expect(main(page).getByText('No categories yet.',{exact:true})).toBeVisible();
    await shot('empty','#workspace-main');
    await page.unroute('**/prompt_manager/vocabulary/tags?*');await page.unroute('**/prompt_manager/vocabulary/categories?*');

    // A failed read shows the inline error with Retry and no zero counts (D12).
    await page.route('**/prompt_manager/vocabulary/tags?*',item=>item.fulfill({status:500,json:{success:false,error:'Database is unavailable'}}));
    await open(page,theme);await expect(main(page).getByRole('alert')).toHaveText('Tags could not be loaded. Nothing was changed.');
    await shot('read-error',tagSurface);
    await page.unroute('**/prompt_manager/vocabulary/tags?*');

    // Load more while more tags remain (D5): the end of the first page.
    manifest=await seed(request,{extra_tags:211});await open(page,theme,100);
    const more=button(main(page),'Load more tags');await expect(more).toBeVisible();
    // A full-page clip of the end of the first page: scrolling there would append the next page before the capture.
    const end=await more.evaluate(node=>{const box=node.closest('.surface').getBoundingClientRect();return box.bottom+scrollY;});
    const path=testInfo.outputPath(`production-load-more-${width}-${theme}.png`);
    await page.screenshot({path,fullPage:true,animations:'disabled',clip:{x:0,y:Math.max(0,end-420),width,height:440}});
    await testInfo.attach(`production-load-more-${width}-${theme}`,{path,contentType:'image/png'});
    await expect(rows(page)).toHaveCount(100);
});

// D143 and D144 (#332): with own image categories seeded (`image_categories`: "Landscapes" on two images, "Portraits"
// on one image only), the Categories panel and the Manage category preview against the concept (renderTags, app.js
// 719-729; the Manage dialog is its simpleInput, 1138-1143). The page is compared with D143 mapped, a model checked
// against the concept page with those rows added at runtime; the preview with D9's sentence and D144's images.
const concept='/__test__/canonical/workspace';
const IMAGE_CATEGORIES=Object.freeze({images:{Landscapes:2,Portraits:1},only:['Portraits']});
// D9's sentence; D144 adds the images holding the value ("and 2 images").
const PREVIEW='“Landscapes” is used by 3 positive prompts and 2 images. Apply renames it to “Scenery”.';
const pageOf=(page,production)=>page.locator(production?'#workspace-main':'#main').evaluate(pageInventoryOf);
async function openConceptRoute(page,route,theme) {
    await page.goto('about:blank');await page.goto(`${concept}#${route}`);
    await mapConceptLayout(page);
    await page.getByRole('combobox',{name:'Theme',exact:true}).selectOption(theme);
}
/** The concept page as D143 records it: image counts after the prompt counts, image-only rows before Manage category. */
function conceptWithImageCounts(page) {
    return page.evaluate(({images,only})=>{
        const surface=document.querySelectorAll('#main .two-col > section.surface')[1];
        const rows=[...surface.querySelectorAll('.operation')];
        const count=value=>`${images[value]} image${images[value]===1?'':'s'}`;
        for(const row of rows) {
            const value=row.querySelector('h3').textContent;
            if(images[value]&&!only.includes(value))row.querySelector('p').textContent+=` · ${count(value)}`;
        }
        const manage=rows.at(-1),last=rows.at(-2);
        for(const value of only) {
            const row=last.cloneNode(true);row.querySelector('h3').textContent=value;row.querySelector('p').textContent=count(value);manage.before(row);
        }
    },IMAGE_CATEGORIES);
}
/** Concept controls the Manage dialog adds (as in workspace-vocabulary.spec.mjs): the Filters field select and p.small. */
async function conceptDonors(page,theme,categories) {
    await openConceptRoute(page,'images',theme);
    await page.locator('#main').getByRole('button',{name:'Filters',exact:true}).click();await expect(page.locator('#modal')).toBeVisible();
    const filters=await page.locator('#modal').evaluate(inventoryOf);
    await openConceptRoute(page,'prompt/alpine',theme);
    await page.getByRole('button',{name:'Edit metadata',exact:true}).click();await expect(page.locator('#modal')).toBeVisible();
    const editor=await page.locator('#modal').evaluate(inventoryOf);
    return {filters,editor,categories};
}

for(const [width,height] of [[1600,1100],[390,844]])for(const theme of ['dark','light'])test(`category rows and the Manage category preview match the concept with image categories ${width}x${height} ${theme} (D143, D144)`,async({page,request},testInfo)=>{
    await page.setViewportSize({width,height});
    const manifest=await seed(request,{image_categories:true});
    expect(manifest.image_categories).toEqual(IMAGE_CATEGORIES.images);
    await openConceptRoute(page,'tags',theme);await expect(page.locator('#main h1')).toHaveText('Tags & categories');
    await page.mouse.move(0,0);
    const reference=await pageOf(page,false);
    await conceptWithImageCounts(page);
    const oracle=await pageOf(page,false);
    await captureCanonicalState(page,testInfo,`concept-image-categories-${width}-${theme}`,'#main');
    const donors=await conceptDonors(page,theme,['Landscapes','Architecture','Environments','Illustration','Portraits']);
    await openConceptRoute(page,'tags',theme);
    await page.locator('#main').getByRole('button',{name:'Review',exact:true}).click();await expect(page.locator('#modal')).toBeVisible();
    const referenceDialog=await page.locator('#modal').evaluate(inventoryOf);

    await open(page,theme,manifest.tags.length);
    await expect(categoryOperation(page,'Portraits')).toHaveCount(1);
    // The pointer still rests where the concept's Review was pressed: park it so no row is hovered.
    await page.mouse.move(0,0);
    const actual=await pageOf(page,true);
    await captureCanonicalState(page,testInfo,`production-image-categories-${width}-${theme}`,'#workspace-main');
    const box=await openDialog(page,categoryOperation(page,'Manage category').getByRole('button',{name:'Review',exact:true}));
    await expect(box.getByLabel('Category',{exact:true})).toHaveValue('Landscapes');
    await box.getByLabel('Proposed new category name',{exact:true}).fill('Scenery');await button(box,'Preview').click();
    await expect(box.locator('p.small')).toHaveText(PREVIEW);
    // The pressed primary keeps the pointer and focus; the concept is measured as it opens (focus on ×, no hover),
    // which workspace-vocabulary.spec.mjs compares, so this state is restored before measuring the preview.
    await page.mouse.move(0,0);await box.locator('.dialog-header button').focus();
    const actualDialog=await box.evaluate(inventoryOf);
    await captureCanonicalState(page,testInfo,`production-image-categories-preview-${width}-${theme}`);
    const path=testInfo.outputPath('vocabulary-image-categories.json');
    await writeFile(path,JSON.stringify({reference,oracle,actual,referenceDialog,actualDialog},null,2));
    await testInfo.attach('vocabulary-image-categories',{path,contentType:'application/json'});

    // The D143 model reproduces the concept page with the rows added at runtime.
    expect.soft(diffInventory(mapVocabularyConcept(reference,IMAGE_CATEGORIES).operations,mapVocabularyConcept(oracle).operations),'D143 model').toEqual([]);
    const {diff,extra}=compareVocabularyPage(reference,actual,IMAGE_CATEGORIES);
    expect.soft(actual.operations.map(row=>[row.h3.text,row.p.text]).slice(0,-1),'category rows').toEqual([['Landscapes','3 positive prompts · 2 images'],
        ['Architecture','1 positive prompt'],['Environments','1 positive prompt'],['Illustration','1 positive prompt'],['Portraits','1 image']]);
    expect.soft(diff,'page differences after the recorded deviations and D143').toEqual([]);
    expect.soft(extra,'fixture-only rows').toEqual([manifest.pair_only_tag,manifest.archived_only_tag,manifest.negative_only_tag,manifest.unused_tag]);
    expect.soft(diffInventory(mapVocabularyDialog('manage',referenceDialog,{...donors,preview:PREVIEW}),actualDialog),'preview differences after D9 and D144').toEqual([]);
});
