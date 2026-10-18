const test = require('node:test');
const assert = require('node:assert/strict');
const source = import('../../web/js/workspace/image-layout.mjs');

test('layouts preserve data order, remain within width and never overlap', async () => {
    const {layoutImages} = await source;
    const images=Array.from({length:200},(_,i)=>({width:i%2?1024:640,height:i%2?640:1024}));
    for(const width of [280,760,1400]) for(const mode of ['grid','list','masonry']) {
        const {positions,height}=layoutImages(images,width,mode);
        assert.equal(positions.length,200);
        for(const [i,a] of positions.entries()) {
            assert.ok(a.x>=0 && a.y>=0 && a.width>0 && a.height>0);
            assert.ok(a.x+a.width<=width+.01);
            assert.ok(a.y+a.height<=height+.01);
            for(const b of positions.slice(i+1)) assert.ok(a.x+a.width<=b.x+.01 || b.x+b.width<=a.x+.01 || a.y+a.height<=b.y+.01 || b.y+b.height<=a.y+.01);
        }
    }
});

test('empty, unknown dimensions and narrow containers keep valid geometry',async()=>{
    const {layoutImages}=await source;
    assert.deepEqual(layoutImages([],0,'grid'),{positions:[],height:0,maxHeight:0});
    for(const mode of ['grid','list','masonry']) {
        const result=layoutImages([{}, {width:0,height:null}],1,mode);
        assert.ok(Number.isFinite(result.height));
    }
});

test('virtual window retains visible rows and focused card while bounding rendered media',async()=>{
    const {layoutImages,visibleImages}=await source;
    const {positions}=layoutImages(Array(5000).fill({}),900,'masonry');
    const indexes=visibleImages(positions,10000,900,0);
    assert.ok(indexes.length<100);
    assert.ok(indexes.includes(0));
    assert.ok(indexes.some(i=>positions[i].y>=10000 && positions[i].y<10900));
    assert.deepEqual(indexes,[...indexes].sort((a,b)=>a-b));
    assert.equal(new Set(indexes).size,indexes.length);
    assert.ok(visibleImages(positions,0,800).includes(0));
});

test('large virtual collections inspect a bounded range rather than every loaded position', async () => {
    const {layoutImages,visibleImages}=await source;
    for(const mode of ['grid','list','masonry']) {
        const geometry=layoutImages(Array.from({length:50000},(_,index)=>({width:640,height:index%2?320:1024})),900,mode);
        for(let index=1;index<geometry.positions.length;index++) assert.ok(geometry.positions[index].y>=geometry.positions[index-1].y);
        let reads=0;
        const positions=new Proxy(geometry.positions,{get(target,key,receiver){
            if(typeof key==='string' && /^\d+$/.test(key)) reads++;
            return Reflect.get(target,key,receiver);
        }});
        const top=geometry.positions[25000].y;
        const indexes=visibleImages(positions,top,900,49999,geometry.maxHeight);
        assert.ok(reads<500,`${mode} read ${reads} positions`);
        const expected=geometry.positions.flatMap((position,index)=>(index===49999 ||
            (position.y+position.height>=top-900 && position.y<=top+1800))?[index]:[]);
        assert.deepEqual(indexes,expected);
    }
});

test('binary-searched windows handle boundaries, empty collections and focus without duplicates',async()=>{
    const {layoutImages,visibleImages}=await source;
    assert.deepEqual(visibleImages([],0,800,0),[]);
    const {positions}=layoutImages(Array(1000).fill({}),900,'grid');
    for(const focused of [-1,0,999,1000]) {
        for(const top of [0,10000,1000000]) {
            const actual=visibleImages(positions,top,600,focused);
            const expected=positions.flatMap((position,index)=>(index===focused ||
                (position.y+position.height>=top-600 && position.y<=top+1200))?[index]:[]);
            assert.deepEqual(actual,expected);
        }
    }
    assert.deepEqual(visibleImages(positions,1000000,0),[]);
});

test('reported maximum height keeps unusually tall custom layouts inside the virtual window',async()=>{
    const {layoutImages,visibleImages}=await source;
    const geometry=layoutImages([{}, {}, {}],10000,'grid',20000);
    assert.equal(geometry.maxHeight,7554.5);
    assert.deepEqual(visibleImages(geometry.positions,6000,200,-1,geometry.maxHeight),[0]);
});


test('approved workspace densities share viewport metrics and reject unknown values', async () => {
    const {workspaceImageMetrics, layoutImages} = await source;
    assert.equal(typeof workspaceImageMetrics, 'function', 'workspace density metrics must be exported');
    for (const [width, expected] of [[1600,[3,2,4]], [1250,[2,2,3]], [950,[2,2,3]], [700,[2,2,2]], [390,[2,2,2]], [1700,[4,2,5]]]) {
        assert.deepEqual(['comfortable','large','compact'].map(density => workspaceImageMetrics(width,density).columns), expected);
        for (const density of ['comfortable','large','compact']) {
            const metrics = workspaceImageMetrics(width,density);
            assert.deepEqual(metrics, {columns: expected[['comfortable','large','compact'].indexOf(density)], gap:13, footerHeight:54, listHeight:97});
            assert.equal(layoutImages([{},{}],990,'list',240,metrics).positions[1].x,0);
        }
    }
    assert.throws(() => workspaceImageMetrics(1600,'unknown'), TypeError);
    assert.deepEqual(workspaceImageMetrics(1600),workspaceImageMetrics(1600,'comfortable'));
});

test('approved grid uses inner media at four to three and explicit columns with thirteen pixel gaps', async () => {
    const {workspaceImageMetrics,layoutImages} = await source;
    assert.equal(typeof workspaceImageMetrics,'function');
    const metrics = workspaceImageMetrics(1600);
    const {positions} = layoutImages(Array(4).fill({}),990,'grid',240,metrics);
    assert.equal(positions[1].x-positions[0].width,13);
    assert.equal(positions[3].x,0);
    assert.equal(positions[3].y-positions[0].height,13);
    assert.equal(positions[0].height-54-2,(positions[0].width-2)*.75);
    assert.equal(layoutImages([{}],990,'list',240,metrics).height,97);
    const custom=layoutImages([{}],990,'grid',240,{columns:2,gap:20,footerHeight:60,listHeight:100});
    assert.equal(custom.positions[0].width,485);
    assert.equal(custom.height,(485-2)*.75+62);
    assert.equal(layoutImages([{}],990,'list',240,{listHeight:100}).height,100);
});

test('masonry uses real finite proportions and falls back for invalid dimensions', async () => {
    const {layoutImages}=await source;
    const images=[{width:100,height:1000},{width:1000,height:100},{width:Infinity,height:10},{width:10,height:NaN}];
    const {positions}=layoutImages(images,990,'masonry',240,{columns:3});
    assert.equal(positions[0].height,(positions[0].width-2)*10+56);
    assert.equal(positions[1].height,(positions[1].width-2)/10+56);
    for (const position of positions) assert.ok(Number.isFinite(position.height));
});
