import * as bridge from '../../web/js/shared/toolkit/prompt-bridge.js';
import { registerPromptBridgeContracts } from '../../tools/vendor/node_modules/@comfyassets/toolkit/testing/prompt-bridge-contracts.mjs';

registerPromptBridgeContracts(bridge, () => import('../../web/js/shared/toolkit/prompt-bridge.js?independent-consumer=0.1'));
