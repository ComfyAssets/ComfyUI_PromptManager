import {defineConfig} from './node_modules/@playwright/test/index.mjs';
import {fileURLToPath} from 'node:url';
import {compatibilityPorts,validateKikoCheckout} from './pack-host.mjs';

if(!process.env.PM_COMFY_ROOT)throw new Error('Set PM_COMFY_ROOT to a pinned disposable ComfyUI checkout');
await validateKikoCheckout(process.env.PM_KIKO_ROOT);
const ports=compatibilityPorts(process.env.PM_COMPAT_PORT||8193);
const modes=Object.keys(ports);
const servers=modes.map(mode=>({
    command:'node tools/vendor/pack-host.mjs',cwd:fileURLToPath(new URL('../../',import.meta.url)),
    url:`http://127.0.0.1:${ports[mode]}/system_stats`,timeout:120000,reuseExistingServer:false,
    env:{PM_COMPAT_MODE:mode},gracefulShutdown:{signal:'SIGTERM',timeout:10000},
}));

export default defineConfig({
    testDir:'../../tests/compatibility',outputDir:'./test-results/compatibility',
    timeout:90000,workers:1,forbidOnly:Boolean(process.env.CI),retries:0,
    use:{browserName:'chromium',viewport:{width:1600,height:1100},trace:'retain-on-failure',screenshot:'only-on-failure'},
    webServer:servers,
    projects:modes.flatMap(mode=>[false,true].map(initialRenderer=>({
        name:`${mode}-${initialRenderer?'nodes2':'classic'}`,
        metadata:{mode,initialRenderer},use:{baseURL:`http://127.0.0.1:${ports[mode]}/`},
    }))),
});
