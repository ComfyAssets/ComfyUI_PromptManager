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
    assert.equal(geometry.maxHeight,7572);
    assert.deepEqual(visibleImages(geometry.positions,6000,200,-1,geometry.maxHeight),[0]);
});
