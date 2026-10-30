import {startCoverage,saveCoverage} from '../../tools/vendor/browser-coverage.mjs';
import {test,expect} from './fixtures.mjs';
import {seedConcept,CONCEPT_URL} from './concept-inventory.mjs';

// #530: measured geometry for three deviations from the concept, found in the maintainer's test of the cutover branch.
// D182 two-column card tops line up, D183 Reusable tags scrolls in its own window (D185 on touch and at 390px),
// D184 System uses the full content width.
const workspace='/prompt_manager/workspace';
const TAGS=`${workspace}#/tags`;
const main=page=>page.locator('#workspace-main');
const cards=page=>main(page).locator('.two-col > section.surface');
const tagWindow=page=>main(page).locator('.two-col .table-wrap');
const rows=page=>tagWindow(page).locator('tbody tr');
const loadMore=page=>main(page).getByRole('button',{name:'Load more tags',exact:true});
const boxes=locator=>locator.evaluateAll(nodes=>nodes.map(node=>{const {top,bottom,left,right,width}=node.getBoundingClientRect();return {top,bottom,left,right,width};}));
const seedTags=async(request,data={})=>expect(await request.post('/__test__/vocabulary/reset',{data})).toBeOK();
const nextFrame=page=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));

test.beforeEach(async({page,browserName})=>{
    await startCoverage(page,browserName);
    page.layoutErrors=[];page.on('pageerror',error=>page.layoutErrors.push(error.message));
});
test.afterEach(async({page,browserName})=>{await saveCoverage(page,browserName);expect(page.layoutErrors).toEqual([]);});

test('two-column cards start on the same line; the concept still has the 18px offset (D182)',async({page,request})=>{
    await page.setViewportSize({width:1600,height:1100});
    await seedConcept(request);await expect(await request.post('/__test__/system/reset')).toBeOK();
    await page.goto(`${workspace}#/system/overview`);
    await expect(main(page).locator('.summary-metrics')).toBeVisible();
    const [health,preferences]=await boxes(cards(page));
    expect(preferences.top).toBe(health.top);
    await seedTags(request);
    await page.goto(TAGS);await expect(rows(page).first()).toBeVisible();
    const [tags,categories]=await boxes(cards(page));
    expect(categories.top).toBe(tags.top);
    // The deviation is real: the immutable concept keeps its offset.
    await page.goto('about:blank');await page.goto(`${CONCEPT_URL}#tags`);
    const concept=await boxes(page.locator('.two-col > section.surface'));
    expect(concept).toHaveLength(2);
    expect(Math.round(concept[1].top-concept[0].top)).toBe(18);
});

test('stacked cards keep one 18px gap at 390px (D182)',async({page,request})=>{
    await page.setViewportSize({width:390,height:844});
    await seedTags(request);
    await page.goto(TAGS);await expect(rows(page).first()).toBeVisible();
    const [tags,categories]=await boxes(cards(page));
    expect(Math.round(categories.top-tags.bottom)).toBe(18);
});

test('System sections use the full content width, as Inspect does (D184)',async({page,request})=>{
    await page.setViewportSize({width:1920,height:1100});
    await seedConcept(request);await expect(await request.post('/__test__/system/reset')).toBeOK();
    for(const section of ['overview','appearance','sources','maintenance']) {
        await page.goto(`${workspace}#/system/${section}`);
        const layout=main(page).locator('.settings-layout');
        await expect(layout).toBeVisible();
        const [[box],[nav]]=await Promise.all([boxes(layout),boxes(main(page).locator(':scope > .section-nav'))]);
        expect([section,box.left,box.width]).toEqual([section,nav.left,nav.width]);
    }
    await page.goto(`${workspace}#/system/overview`);
    await expect(main(page).locator('.summary-metrics')).toBeVisible();
    const [[metrics],[columns],[nav]]=await Promise.all([boxes(main(page).locator('.summary-metrics')),boxes(main(page).locator('.settings-layout > .two-col')),boxes(main(page).locator(':scope > .section-nav'))]);
    expect(metrics.right).toBeCloseTo(nav.right,0);
    expect(columns.right).toBeCloseTo(nav.right,0);
    await page.goto(`${workspace}#/inspect`);
    await expect(main(page).locator('section.surface').first()).toBeVisible();
    const [inspect]=await boxes(main(page).locator('section.surface').first());
    expect(inspect.width).toBeCloseTo(nav.width,0);
});

// Every System section and the Tags page: blocks stacked in one column are never closer than the concept's 18px surface
// gap. Between a `.two-col` and the surface after it the concept has no gap at all (D182).
const SECTIONS=['overview','appearance','sources','integrations','tagging','maintenance','backups','diagnostics'];
const stackGaps=host=>host.evaluate(node=>{
    const blocks=[...node.children].filter(child=>child.matches('.surface,.two-col')&&child.getClientRects().length);
    return blocks.slice(1).map((block,index)=>[blocks[index].className,block.className,Math.round(block.getBoundingClientRect().top-blocks[index].getBoundingClientRect().bottom)]);
});
test('every stacked card has one 18px gap, including after a two-column row (D182)',async({page,request})=>{
    for(const [width,height] of [[1600,1100],[390,844]]) {
        await page.setViewportSize({width,height});
        await seedConcept(request);await expect(await request.post('/__test__/system/reset')).toBeOK();
        for(const section of SECTIONS) {
            await page.goto('about:blank');await page.goto(`${workspace}#/system/${section}`);
            const layout=main(page).locator('.settings-layout');
            await expect(layout.locator(':scope > .surface, :scope > .two-col').first()).toBeVisible();
            for(const gap of await stackGaps(layout)) expect([width,section,...gap]).toEqual([width,section,gap[0],gap[1],18]);
        }
        await seedTags(request);
        await page.goto('about:blank');await page.goto(TAGS);await expect(rows(page).first()).toBeVisible();
        for(const gap of await stackGaps(main(page))) expect([width,'tags',...gap]).toEqual([width,'tags',gap[0],gap[1],18]);
    }
    // The deviation is real: the immutable concept has no gap after its Backup & export columns.
    await page.setViewportSize({width:1600,height:1100});
    await page.goto('about:blank');await page.goto(`${CONCEPT_URL}#system`);
    await page.locator('#main .section-nav').getByRole('button',{name:'Backup & export',exact:true}).click();
    const policy=page.locator('#main .settings-layout > section.surface');await expect(policy).toBeVisible();
    expect(await policy.evaluate(node=>Math.round(node.getBoundingClientRect().top-node.previousElementSibling.getBoundingClientRect().bottom))).toBe(0);
});

test('a long tag list scrolls in its own window and the page does not grow (D183)',async({page,request})=>{
    await page.setViewportSize({width:1600,height:1100});
    await seedTags(request,{extra_tags:211});
    await page.goto(TAGS);
    await expect(rows(page)).toHaveCount(100);
    const size=()=>tagWindow(page).evaluate(node=>({scrollHeight:node.scrollHeight,clientHeight:node.clientHeight,scrollTop:node.scrollTop}));
    const before=await size();
    expect(before.scrollHeight).toBeGreaterThan(before.clientHeight);
    expect(before.clientHeight).toBeGreaterThanOrEqual(280);
    const [tagCard]=await boxes(cards(page));
    expect(tagCard.bottom).toBeLessThanOrEqual(1100);
    await expect(loadMore(page)).toBeInViewport();

    // The header stays at the top of the window while the rows move under it.
    await tagWindow(page).evaluate(node=>{node.scrollTop=600;});await nextFrame(page);
    const [[head],[frame]]=await Promise.all([boxes(tagWindow(page).locator('thead th').first()),boxes(tagWindow(page))]);
    expect(Math.round(head.top)).toBe(Math.round(frame.top));

    // Reaching the end of the window appends one page; the page itself did not scroll.
    await tagWindow(page).evaluate(node=>{node.scrollTop=node.scrollHeight;});
    await expect(rows(page)).toHaveCount(200);
    expect(await page.evaluate(()=>window.scrollY)).toBe(0);

    // Load more works from below the window, with the window not at its end.
    await tagWindow(page).evaluate(node=>{node.scrollTop=0;});await nextFrame(page);
    await loadMore(page).click();
    await expect(rows(page)).toHaveCount(211+19);
    await expect(loadMore(page)).toHaveCount(0);

    // A new sort is a new list: the window returns to its top.
    await tagWindow(page).evaluate(node=>{node.scrollTop=800;});await nextFrame(page);
    expect((await size()).scrollTop).toBe(800);
    await main(page).getByRole('combobox',{name:'Sort tags',exact:true}).selectOption({index:1});
    await expect.poll(async()=>(await size()).scrollTop).toBe(0);
});

test('a short tag list has no inner scrolling (D183)',async({page,request})=>{
    await page.setViewportSize({width:1600,height:1100});
    await seedTags(request);
    await page.goto(TAGS);await expect(rows(page).first()).toBeVisible();
    await page.setViewportSize({width:1600,height:2000});await nextFrame(page);
    const {scrollHeight,clientHeight}=await tagWindow(page).evaluate(node=>({scrollHeight:node.scrollHeight,clientHeight:node.clientHeight}));
    expect(scrollHeight).toBe(clientHeight);
});

test('at 390px the window fits the screen, actions stay reachable and the page still scrolls (D185)',async({page,request})=>{
    await page.setViewportSize({width:390,height:844});
    await seedTags(request,{extra_tags:211});
    await page.goto(TAGS);
    await expect(rows(page)).toHaveCount(100);
    const [frame]=await boxes(tagWindow(page));
    expect(frame.bottom-frame.top).toBeLessThan(844);
    expect(frame.bottom-frame.top).toBeGreaterThanOrEqual(280);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    // The last row's actions can be scrolled to inside the window and fit its width.
    const last=rows(page).last().getByRole('button',{name:'Rename / merge',exact:true});
    await last.scrollIntoViewIfNeeded();await expect(last).toBeInViewport();
    const [button]=await boxes(last);const [after]=await boxes(tagWindow(page));
    expect(button.right).toBeLessThanOrEqual(after.right);
    await tagWindow(page).evaluate(node=>{node.scrollTop=0;});await nextFrame(page);
    await expect(loadMore(page)).toBeAttached();
    await loadMore(page).scrollIntoViewIfNeeded();await expect(loadMore(page)).toBeInViewport();
});

test('a swipe moves the window inside it and the page outside it (D185)',async({page,request,browserName},testInfo)=>{
    test.skip(browserName!=='chromium'||testInfo.project.name!=='touch','needs a touch-emulating Chromium');
    await page.setViewportSize({width:390,height:844});
    await seedTags(request,{extra_tags:211});
    await page.goto(TAGS);
    await expect(rows(page)).toHaveCount(100);
    await tagWindow(page).evaluate(node=>node.scrollIntoView({block:'start'}));await nextFrame(page);
    // A real touch sequence: the browser decides what scrolls.
    const swipe=async(x,y,distance)=>{
        const client=await page.context().newCDPSession(page);
        const touch=(type,at)=>client.send('Input.dispatchTouchEvent',{type,touchPoints:type==='touchEnd'?[]:[{x,y:at}]});
        await touch('touchStart',y);
        for(let step=1;step<=10;step++) {await touch('touchMove',y+distance*step/10);await nextFrame(page);}
        await touch('touchEnd');
        await client.detach();await nextFrame(page);
    };
    const [frame]=await boxes(tagWindow(page));
    const y=Math.round(frame.top+(frame.bottom-frame.top)/2);
    const pageTop=await page.evaluate(()=>window.scrollY);
    await swipe(195,y,-150);
    await expect.poll(()=>tagWindow(page).evaluate(node=>node.scrollTop)).toBeGreaterThan(50);
    expect(await page.evaluate(()=>window.scrollY)).toBe(pageTop);
    // From the top of the window a downward swipe chains to the page, so a phone is never trapped in the list.
    await tagWindow(page).evaluate(node=>{node.scrollTop=0;});await nextFrame(page);
    await swipe(195,y,200);
    await expect.poll(()=>page.evaluate(()=>window.scrollY)).toBeLessThan(pageTop);
});
