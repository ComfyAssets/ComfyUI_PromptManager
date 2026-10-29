import {test,expect} from './fixtures.mjs';
import {SIZES,THEMES,seedConceptAt,openConcept,openProduction,diffInventory,mutate} from './concept-inventory.mjs';

// #480 (D159): the Images selection bar's Delete… against the concept's "Remove links…" (app.js a432, `danger`) and
// its review against the concept's `maintenancePlan` (a953–957): the bar button's place and style, the review's
// `dl.meta-grid` rows and its actions, on the concept-shaped fixture, at 1600x1100 and 390x844 in both themes.
// Paired screenshots are artifacts only; there are no PNG baselines.
test.use({locale:'en-US',timezoneId:'UTC'});

const CARDS='.gallery-body > .image-grid > .media-card';

/** Runs in the page: the selection bar's danger button and where it sits among the bar's buttons. */
function measureBar() {
    const bar=document.querySelector('#bulkbar > .bulkbar');
    const buttons=[...bar.querySelectorAll(':scope > button')];
    const danger=bar.querySelector(':scope > button.danger');
    const css=(node,...keys)=>Object.fromEntries(keys.map(key=>[key,getComputedStyle(node)[key]]));
    return {
        // The danger action sits right before Clear, the last button.
        dangerBeforeClear:buttons.indexOf(danger)===buttons.length-2,
        lastText:buttons.at(-1).textContent.trim(),
        danger:css(danger,'color','backgroundColor','borderTopColor','fontSize','fontWeight','paddingTop','paddingLeft',
            'borderTopLeftRadius','height'),
    };
}

/** Runs in the page: the open review's grid and actions (the parts production shares with the concept). */
function measureReview() {
    const dialog=document.querySelector('dialog#modal[open]');
    const css=(node,...keys)=>node?Object.fromEntries(keys.map(key=>[key,getComputedStyle(node)[key]])):null;
    const grid=dialog.querySelector('#modal-body dl.meta-grid');
    const entry=grid.querySelector(':scope > div');
    const actions=dialog.querySelector('#modal-body > .dialog-actions');
    const [cancel]=actions.children;
    return {
        grid:css(grid,'display','gridTemplateColumns','columnGap','rowGap'),
        dt:css(entry.querySelector('dt'),'fontSize','color','textTransform','letterSpacing'),
        dd:css(entry.querySelector('dd'),'fontSize','color','marginLeft'),
        terms:[...grid.querySelectorAll('dt')].map(dt=>dt.textContent.trim()),
        actions:css(actions,'display','justifyContent','columnGap','marginTop'),
        cancel:{text:cancel.textContent.trim(),...css(cancel,'backgroundColor','color','fontSize','paddingTop','paddingLeft')},
    };
}

async function checkFirst(page) {
    await page.locator(CARDS).first().locator('.image-overlays > label > input[type="checkbox"]').check();
}

async function concept(page,theme) {
    await openConcept(page,'images',theme);
    await checkFirst(page);
    const bar=await page.evaluate(measureBar);
    await page.locator('#main #bulkbar').getByRole('button',{name:'Remove links…',exact:true}).click();
    await expect(page.locator('dialog#modal[open] #modal-title')).toHaveText('Review maintenance plan');
    await page.mouse.move(0,0);
    return {bar,review:await page.evaluate(measureReview)};
}

async function production(page,theme) {
    await openProduction(page,'images',theme);
    await expect(page.locator(CARDS).first()).toBeVisible();
    await checkFirst(page);
    const bar=await page.evaluate(measureBar);
    await page.locator('#workspace-main #bulkbar').getByRole('button',{name:'Delete…',exact:true}).click();
    const dialog=page.locator('dialog#modal[open]');
    await expect(dialog.locator('#modal-title')).toHaveText('Delete 1 image');
    await dialog.getByRole('radio',{name:/^Remove from the library only/}).check();
    await page.mouse.move(0,0);
    return {bar,review:await page.evaluate(measureReview)};
}

/**
 * D159: production's rows follow the chosen mode (Scope, then what the files lose, Backup and Media on disk; the
 * concept's sample has Scope, Protected, Backup and Media on disk). The geometry, tokens and the Cancel button match.
 */
function mapConcept(reference,actual) {
    return {...reference,review:{...reference.review,terms:actual.review.terms}};
}

test.beforeEach(async({page,request},testInfo)=>{testInfo.manifest=await seedConceptAt(page,request);});

for(const theme of THEMES)for(const [width,height] of SIZES) {
    test(`delete matches the concept's remove links ${theme} ${width}x${height}`,async({page},testInfo)=>{
        await page.setViewportSize({width,height});
        const reference=await concept(page,theme);
        await page.screenshot({path:testInfo.outputPath(`concept-remove-links-${theme}-${width}.png`)});
        const actual=await production(page,theme);
        await page.screenshot({path:testInfo.outputPath(`production-delete-${theme}-${width}.png`)});
        expect(reference.review.terms).toEqual(['Scope','Protected','Backup','Media on disk']);
        expect(actual.review.terms[0]).toBe('Scope');
        expect(actual.review.terms.slice(-2)).toEqual(['Backup','Media on disk']);
        expect(diffInventory(mapConcept(reference,actual),actual)).toEqual([]);
    });
}

test('delete comparison detects mutations',async({page})=>{
    await page.setViewportSize({width:1600,height:1100});
    const reference=await concept(page,'dark');
    const actual=await production(page,'dark');
    const expected=mapConcept(reference,actual);
    expect(diffInventory(expected,actual)).toEqual([]);
    const undo=await mutate(page,{css:'dialog#modal dl.meta-grid { column-gap: 31px; } dialog#modal .dialog-actions { gap: 29px; }'});
    const paths=diffInventory(expected,{...actual,review:await page.evaluate(measureReview)}).map(item=>item.path);
    expect(paths).toEqual(expect.arrayContaining(['review.grid.columnGap','review.actions.columnGap']));
    await undo();
});
