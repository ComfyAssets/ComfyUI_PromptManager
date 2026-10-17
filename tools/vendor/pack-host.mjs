import {execFileSync} from 'node:child_process';
import {access,realpath} from 'node:fs/promises';
import {join,resolve} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {launchComfyHost} from '@comfyassets/toolkit/testing/host';
import {preparePromptManagerPlugin} from './comfyui-host.mjs';
import runtime from './comfyui-runtime.json' with {type:'json'};
import crossPack from './cross-pack-runtime.json' with {type:'json'};

const repository=fileURLToPath(new URL('../../',import.meta.url));
const MODES=['promptmanager','kiko','both'];
export function compatibilityPorts(value=8193) {
    const first=Number(value);
    if(!Number.isInteger(first)||first<1024||first>65533)throw new Error('PM_COMPAT_PORT must reserve three unprivileged ports');
    return Object.fromEntries(MODES.map((mode,index)=>[mode,first+index]));
}

export async function validateKikoCheckout(checkout) {
    if(!checkout)throw new Error('Set PM_KIKO_ROOT to the pinned disposable KikoTools checkout');
    const repository=await realpath(checkout);
    await access(join(repository,'__init__.py'));
    const git=args=>execFileSync('git',['-C',repository,...args],{encoding:'utf8',stdio:['ignore','pipe','pipe']}).trim();
    if(await realpath(git(['rev-parse','--show-toplevel']))!==repository)throw new Error('PM_KIKO_ROOT must name the repository root');
    if(git(['rev-parse','HEAD'])!==crossPack.revision)throw new Error(`KikoTools revision must be ${crossPack.revision}`);
    try {git(['diff','--quiet','HEAD','--']);}
    catch {throw new Error('KikoTools disposable checkout has modified tracked files');}
    return repository;
}

export async function launchPackHost({mode=process.env.PM_COMPAT_MODE,kikoRoot=process.env.PM_KIKO_ROOT,
    checkout=process.env.PM_COMFY_ROOT,python=process.env.PM_TEST_PYTHON||'python',
    firstPort=process.env.PM_COMPAT_PORT||8193}={}) {
    if(!MODES.includes(mode))throw new Error('Select promptmanager, kiko or both for PM_COMPAT_MODE');
    const ports=compatibilityPorts(firstPort);const kikoRepository=await validateKikoCheckout(kikoRoot);
    const pm={pluginName:'promptmanager',repository,preparePlugin:preparePromptManagerPlugin};
    const kiko={pluginName:'kikotools',repository:kikoRepository};
    const primary=mode==='kiko'?kiko:pm;
    return launchComfyHost({...primary,additionalPlugins:mode==='both'?[kiko]:[],checkout,python,
        port:ports[mode],pins:runtime,logPath:join(repository,`tools/vendor/test-results/compatibility-${mode}-host.log`)});
}

if(process.argv[1]&&import.meta.url===pathToFileURL(resolve(process.argv[1])).href) {
    try {
        const host=await launchPackHost();
        const stop=()=>{host.stop().catch(error=>{console.error(error.message);process.exitCode=1;});};
        process.once('SIGINT',stop);process.once('SIGTERM',stop);
        const result=await host.finished;
        process.removeListener('SIGINT',stop);process.removeListener('SIGTERM',stop);
        process.exitCode=result.code;
    } catch(error) {console.error(error.message);process.exitCode=1;}
}
