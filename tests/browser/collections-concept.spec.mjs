import {test,expect} from './fixtures.mjs';
import {SIZES,THEMES,seedConceptAt,openConcept,openProduction,diffInventory,mutate} from './concept-inventory.mjs';

// #331 D138: the Images selection bar's "Add to collection" dialog against the concept's own (`bulk-collection` →
// simpleInput, app.js a1392–1398 and a1138), on the concept-shaped fixture, at 1600x1100 and 390x844 in both themes.
// Paired screenshots are artifacts only; there are no PNG baselines.
test.use({locale:'en-US',timezoneId:'UTC'});

const CARDS='.gallery-body > .image-grid > .media-card';

/**
 * Runs in the page: the open "Add selected images to collection" dialog: its title, the body's tree, the field's
 * label, input and suggestions, and the actions, with the concept's geometry and tokens.
 */
function measureAddDialog() {
    const dialog=document.querySelector('dialog#modal[open]');
    const css=(node,...keys)=>node?Object.fromEntries(keys.map(key=>[key,getComputedStyle(node)[key]])):null;
    const name=node=>[node.localName,...[...node.classList].sort()].join('.');
    const text=node=>node?.textContent.trim()??null;
    const body=dialog.querySelector('#modal-body');
    const field=body.querySelector(':scope > .field');
    const label=field?.querySelector(':scope > label');
    const input=field?.querySelector(':scope > input');
    const list=input?.list??null;
    const actions=body.querySelector(':scope > .dialog-actions');
    const rect=dialog.getBoundingClientRect();
    return {
        title:text(dialog.querySelector('#modal-title')),
        tree:[...body.children].map(name),
        field:field&&{tree:[...field.children].map(name),height:Math.round(field.getBoundingClientRect().height),
            ...css(field,'display','flexDirection','rowGap','marginBottom')},
        label:label&&{text:text(label),for:label.htmlFor,...css(label,'fontSize','fontWeight','color')},
        input:input&&{id:input.id,type:input.type,value:input.value,maxLength:input.maxLength,list:input.getAttribute('list'),
            ...css(input,'height','paddingLeft','fontSize','color','backgroundColor','borderTopColor','borderTopLeftRadius')},
        suggestions:list&&{id:list.id,display:getComputedStyle(list).display,options:[...list.options].map(option=>option.value)},
        actions:{tree:[...actions.children].map(name),...css(actions,'display','justifyContent','columnGap','marginTop'),
            buttons:[...actions.children].map(button=>({text:text(button),className:button.className,
                ...css(button,'backgroundColor','color','fontSize','paddingTop','paddingLeft','borderTopColor')}))},
        width:Math.round(rect.width),height:Math.round(rect.height),
    };
}

/**
 * D138: the action is "Add" (the concept's "Apply in prototype"), the field starts empty (no sample "Experiments") and
 * holds at most 100 characters, and a hidden `datalist` of the existing names (the fixture's two) follows the input.
 */
function mapAddDialogConcept(reference) {
    const [cancel,apply]=reference.actions.buttons;
    return {...reference,field:{...reference.field,tree:[...reference.field.tree,'datalist']},
        input:{...reference.input,value:'',maxLength:100,list:'action-input-suggestions'},
        suggestions:{id:'action-input-suggestions',display:'none',options:['Experiments','Landscapes']},
        actions:{...reference.actions,buttons:[cancel,{...apply,text:'Add'}]}};
}

async function openAddDialog(page,main) {
    await page.locator(CARDS).first().locator('.image-overlays > label > input[type="checkbox"]').check();
    await page.locator(`${main} #bulkbar`).getByRole('button',{name:'Add to collection',exact:true}).click();
    await expect(page.locator('dialog#modal[open] #modal-title')).toHaveText('Add selected images to collection');
    await page.mouse.move(0,0);
    return page.evaluate(measureAddDialog);
}

async function conceptAddDialog(page,theme) {
    await openConcept(page,'images',theme);
    return openAddDialog(page,'#main');
}

async function productionAddDialog(page,theme) {
    await openProduction(page,'images',theme);
    await expect(page.locator(CARDS).first()).toBeVisible();
    return openAddDialog(page,'#workspace-main');
}

test.beforeEach(async({page,request},testInfo)=>{testInfo.manifest=await seedConceptAt(page,request);});

for(const theme of THEMES)for(const [width,height] of SIZES) {
    test(`add to collection dialog matches the concept ${theme} ${width}x${height}`,async({page},testInfo)=>{
        await page.setViewportSize({width,height});
        const concept=await conceptAddDialog(page,theme);
        await page.screenshot({path:testInfo.outputPath(`concept-add-to-collection-${theme}-${width}.png`)});
        const production=await productionAddDialog(page,theme);
        await page.screenshot({path:testInfo.outputPath(`production-add-to-collection-${theme}-${width}.png`)});
        expect(concept.input.value,'the concept pre-fills its sample').toBe('Experiments');
        expect(diffInventory(mapAddDialogConcept(concept),production)).toEqual([]);
    });
}

test('add to collection comparison detects mutations',async({page})=>{
    await page.setViewportSize({width:1600,height:1100});
    const concept=await conceptAddDialog(page,'dark');
    const expected=mapAddDialogConcept(concept);
    const production=await productionAddDialog(page,'dark');
    expect(diffInventory(expected,production)).toEqual([]);
    // Unmapped, the concept differs only where D138 is recorded.
    expect(diffInventory(concept,production).map(item=>item.path).sort()).toEqual(expect.arrayContaining(
        ['actions.buttons.1.text','field.tree.2','input.list','input.maxLength','input.value']));
    const paths=async()=>diffInventory(expected,await page.evaluate(measureAddDialog)).map(item=>item.path);
    await page.evaluate(()=>{document.querySelector('dialog#modal[open] label[for="action-input"]').textContent='Name';});
    expect(await paths()).toEqual(['label.text']);
    await page.evaluate(()=>{document.querySelector('dialog#modal[open] label[for="action-input"]').textContent='Collection name';});
    const undoStyle=await mutate(page,{css:'dialog#modal #action-input { border-color: rgb(255, 0, 0); } dialog#modal .dialog-actions { gap: 31px; }'});
    expect(await paths()).toEqual(expect.arrayContaining(['input.borderTopColor','actions.columnGap']));
    await undoStyle();
    await page.evaluate(()=>document.querySelector('#action-input-suggestions').remove());
    expect(await paths()).toEqual(expect.arrayContaining(['suggestions']));
});
