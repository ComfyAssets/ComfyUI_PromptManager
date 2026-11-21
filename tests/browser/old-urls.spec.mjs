// #668 (D207): the old admin, gallery and metadata pages are gone. Their URLs, and the old tag bookmarks, lead into
// the workspace, against the real server.
import {startCoverage,saveCoverage} from '../../tools/vendor/browser-coverage.mjs';
import {test,expect} from './fixtures.mjs';

const cards=page=>page.locator('article[data-prompt-id]');
const card=(page,text)=>cards(page).filter({has:page.getByText(text,{exact:true})});
const filtersButton=page=>page.locator('#workspace-main search.toolbar').getByRole('button',{name:/^Filters(?: · \d)?$/});
const caption=page=>page.locator('#workspace-main .section-caption > span').first();
const heading=page=>page.locator('#workspace-main h1').first();
const at=hash=>new RegExp(`/prompt_manager/workspace${hash.replace(/[/#]/g,'\\$&')}$`);

test.beforeEach(async({page,request,browserName})=>{
    await startCoverage(page,browserName);await expect(await request.post('/__test__/reset',{data:{native_library:true}})).toBeOK();
    page.oldUrlErrors=[];page.on('pageerror',error=>page.oldUrlErrors.push(error.message));
    // A deleted page asset that is still requested would answer 404.
    page.missing=[];page.on('response',response=>{if(response.status()===404)page.missing.push(new URL(response.url()).pathname);});
});
test.afterEach(async({page,browserName})=>{
    await saveCoverage(page,browserName);expect(page.oldUrlErrors).toEqual([]);expect(page.missing).toEqual([]);
});

for(const [url,hash,title] of [
    ['/prompt_manager/admin','#/library','Prompt library'],
    ['/prompt_manager/admin#/tags','#/tags','Tags & categories'],
    ['/prompt_manager/admin#/tags/','#/tags','Tags & categories'],
    ['/prompt_manager/admin#/something-else','#/library','Prompt library'],
    ['/prompt_manager/gallery','#/images','Images'],
    ['/prompt_manager/gallery.html','#/inspect','Inspect a file'],
    ['/prompt_manager/web','#/library','Prompt library'],
]) {
    test(`${url} opens the workspace's ${title}`,async({page})=>{
        await page.goto(url);
        await expect(page).toHaveURL(at(hash));
        await expect(heading(page)).toHaveText(title);
    });
}

test('an old one-tag bookmark opens the Library filtered by that tag',async({page})=>{
    await page.goto('/prompt_manager/admin#/tags/cats');
    await expect(page).toHaveURL(at('#/library'));
    await expect(caption(page)).toHaveText(/^1 positive prompt · /);
    await expect(card(page,'Cat portrait')).toHaveCount(1);await expect(cards(page)).toHaveCount(1);
    await expect(filtersButton(page)).toHaveText('Filters · 1');
});

test('an old two-tag bookmark matches all of the tags, and any of them with ?mode=or',async({page})=>{
    await page.goto('/prompt_manager/admin#/tags/cats,dogs');
    await expect(caption(page)).toHaveText(/^0 positive prompts · /);await expect(filtersButton(page)).toHaveText('Filters · 1');
    await page.goto('about:blank');
    await page.goto('/prompt_manager/admin#/tags/cats,dogs?mode=or');
    await expect(page).toHaveURL(at('#/library'));
    await expect(caption(page)).toHaveText(/^2 positive prompts · /);
    await expect(card(page,'Cat portrait')).toHaveCount(1);await expect(card(page,'Dog landscape')).toHaveCount(1);
    await expect(filtersButton(page)).toHaveText('Filters · 2');
});

test('an old untagged bookmark asks the Library for the prompts without a tag',async({page})=>{
    const listed=page.waitForResponse(item=>{const url=new URL(item.url());return url.pathname==='/prompt_manager/library'&&url.searchParams.get('tag_mode')==='untagged';});
    await page.goto('/prompt_manager/admin#/tags/__untagged__');
    expect((await (await listed).json()).tag_mode).toBe('untagged');
    await expect(page).toHaveURL(at('#/library'));await expect(filtersButton(page)).toHaveText('Filters · 1');
});

test('Back from the workspace skips the old URL instead of bouncing through it',async({page})=>{
    await page.goto('/prompt_manager/workspace#/overview');
    await expect(heading(page)).toHaveText('Overview');
    for(const [url,title] of [['/prompt_manager/admin#/tags/cats','Prompt library'],['/prompt_manager/gallery','Images']]) {
        await page.goto(url);
        await expect(heading(page)).toHaveText(title);
        await page.goBack();
        await expect(page).toHaveURL(at('#/overview'));
        await expect(heading(page)).toHaveText('Overview');
    }
});

test('the server itself redirects the two old pages that had no hash routes',async({request})=>{
    for(const [url,target] of [['/prompt_manager/gallery','workspace#/images'],['/prompt_manager/gallery.html','workspace#/inspect']]) {
        const response=await request.get(url,{maxRedirects:0});
        expect([response.status(),response.headers().location]).toEqual([302,target]);
    }
});
