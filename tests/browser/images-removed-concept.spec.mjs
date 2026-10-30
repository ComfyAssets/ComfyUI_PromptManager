import {test,expect} from './fixtures.mjs';
import {seedConceptAt,openConcept,openProduction} from './concept-inventory.mjs';

// #498 (D162): the concept has no Show removed images screen, so production builds it from the concept's own parts:
// the heading action button (as the concept's Images heading button), the card's `.chip` (as a concept card chip) and
// the selection bar's buttons (as the concept bar's plain buttons). Each is compared with its concept counterpart.
test.use({locale:'en-US',timezoneId:'UTC'});

const CARDS='.gallery-body > .image-grid > .media-card';
const KEYS=['color','backgroundColor','borderTopColor','borderTopWidth','fontSize','fontWeight','paddingTop','paddingLeft',
    'borderTopLeftRadius','height'];
const CHIP_KEYS=['color','backgroundColor','borderTopColor','fontSize','fontWeight','paddingTop','paddingLeft',
    'borderTopLeftRadius'];

/** Runs in the page: the computed style of the first node `selector` matches. */
function styleOf([selector,keys]) {
    const node=document.querySelector(selector);
    return node?Object.fromEntries(keys.map(key=>[key,getComputedStyle(node)[key]])):null;
}

async function measure(page,selectors) {
    const found={};
    for (const [name,selector,keys] of selectors) found[name]=await page.evaluate(styleOf,[selector,keys]);
    return found;
}

test.beforeEach(async({page,request})=>{await seedConceptAt(page,request);});

for (const theme of ['dark','light']) {
    test(`Show removed images uses the concept's heading button, chip and bar buttons (${theme})`,async({page})=>{
        await page.setViewportSize({width:1600,height:1100});
        await openConcept(page,'images',theme);
        await page.locator(CARDS).first().locator('.image-overlays > label > input[type="checkbox"]').check();
        await page.mouse.move(0,0);
        const reference=await measure(page,[
            ['heading','#main .heading-actions > button:not(.ca-primary)',KEYS],
            ['chip','#main .chip',CHIP_KEYS],
            ['bar','#main #bulkbar > .bulkbar > button:not(.danger)',KEYS],
        ]);

        await openProduction(page,'images',theme);
        await expect(page.locator(CARDS).first()).toBeVisible();
        await page.locator(CARDS).first().locator('.image-overlays > label > input[type="checkbox"]').check();
        await page.locator('#workspace-main #bulkbar').getByRole('button',{name:'Delete…',exact:true}).click();
        const dialog=page.locator('dialog#modal[open]');
        await dialog.getByRole('radio',{name:/^Remove from the library only/}).check();
        await dialog.locator('.dialog-actions button.danger').click();
        const toggle=page.locator('.heading-actions').getByRole('button',{name:/^Show removed images \(1\)$/});
        await expect(toggle).toBeVisible();
        await page.mouse.move(0,0);
        const heading=await measure(page,[['heading','#workspace-main .heading-actions > button[aria-pressed]',KEYS]]);
        await toggle.click();
        await expect(page.locator(`${CARDS} .chip`,{hasText:'Removed'})).toBeVisible();
        await page.locator(CARDS).first().locator('.image-overlays > label > input[type="checkbox"]').check();
        await expect(page.locator('#workspace-main #bulkbar').getByRole('button',{name:'Return to library'})).toBeVisible();
        await page.mouse.move(0,0);
        const actual={...heading,...await measure(page,[
            ['chip',`${CARDS} .media-info .chip`,CHIP_KEYS],
            ['bar','#workspace-main #bulkbar > .bulkbar > button',KEYS],
        ])};
        // Every part was found on both sides: two missing nodes must never compare equal.
        expect(Object.values(reference).every(Boolean)).toBe(true);
        expect(actual).toEqual(reference);
    });
}
