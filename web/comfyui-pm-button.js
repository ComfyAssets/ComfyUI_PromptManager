/**
 * Prompt Manager Toolbar Button Extension
 * Adds a PM button to ComfyUI's toolbar for quick access to Prompt Manager
 */

import { app } from "../../scripts/app.js";

const extension = {
    name: "prompt-manager.toolbar-button",
};

app.registerExtension(extension);

const config = {
    newTab: true,
    newWindow: {
        width: 1400,
        height: 900,
    }
};

/**
 * Create the PM button element
 */
const createPMButton = ({ className, text, tooltip, includeIcon, svgMarkup }) => {
    const button = document.createElement('button');
    button.className = className;
    button.setAttribute('aria-label', tooltip);
    button.title = tooltip;

    if (includeIcon && svgMarkup) {
        // Use a span container but make it larger to fill the button
        const iconContainer = document.createElement('span');
        iconContainer.innerHTML = svgMarkup;
        iconContainer.style.display = 'flex';
        iconContainer.style.alignItems = 'center';
        iconContainer.style.justifyContent = 'center';
        iconContainer.style.width = '28px';  // Larger than L button to fill space better
        iconContainer.style.height = '28px';  // Square container
        button.appendChild(iconContainer);
        
        // Make the SVG fill the container
        const svg = iconContainer.querySelector('svg');
        if (svg) {
            svg.style.width = '100%';
            svg.style.height = '100%';
            svg.style.display = 'block';
        }
    }

    if (text) {
        const textNode = document.createTextNode(text);
        button.appendChild(textNode);
    }

    button.addEventListener('click', onPMButtonClick);
    return button;
};

/**
 * Handle PM button click
 */
const onPMButtonClick = (e) => {
    const promptManagerUrl = `${window.location.origin}/prompt_manager/`;

    // Check if Shift key is pressed to determine how to open
    if (e.shiftKey) {
        // Open in new window
        const { width, height } = config.newWindow;
        const windowFeatures = `width=${width},height=${height},resizable=yes,scrollbars=yes,status=yes`;
        window.open(promptManagerUrl, '_blank', windowFeatures);
    } else {
        // Default behavior: open in new tab
        window.open(promptManagerUrl, '_blank');
    }
};

/**
 * Get the PM icon SVG
 */
const getPMIcon = () => {
    // PM text icon optimized to fill button space like the L button
    return `
        <svg viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg">
            <rect x="0" y="0" width="100" height="100" rx="8" ry="8"
                  fill="#2E7EE5" stroke="#1B4A8A" stroke-width="2"/>
            <text x="50" y="62" font-family="Arial, sans-serif" font-size="45" font-weight="bold"
                  fill="#FFFFFF" text-anchor="middle">PM</text>
        </svg>
    `;
};

/**
 * Add PM button to the right menu (mobile/desktop compatible)
 */
const addPMButtonToRightMenu = (menuRight) => {
    // Check if PM button already exists
    if (menuRight.querySelector('.comfy-pm-button')) {
        return;
    }

    // Find or create button group
    let buttonGroup = menuRight.querySelector('.comfyui-button-group');

    if (!buttonGroup) {
        buttonGroup = document.createElement('div');
        buttonGroup.className = 'comfyui-button-group';
        menuRight.appendChild(buttonGroup);
    }

    const pmButton = createPMButton({
        className: 'comfyui-button comfyui-menu-mobile-collapse primary comfy-pm-button',
        text: '',
        tooltip: 'Launch Prompt Manager (Shift+Click to open in new window)',
        includeIcon: true,
        svgMarkup: getPMIcon(),
    });

    // Style button to match other toolbar buttons
    pmButton.style.display = 'flex';
    pmButton.style.alignItems = 'center';
    pmButton.style.justifyContent = 'center';

    // Insert PM button before LoRA Manager button if it exists, otherwise append
    const loraButton = buttonGroup.querySelector('[aria-label*="Lora Manager"]');
    if (loraButton) {
        buttonGroup.insertBefore(pmButton, loraButton);
    } else {
        buttonGroup.appendChild(pmButton);
    }
};

/**
 * Add PM button to the classic menu
 */
const addPMButtonToMenu = (menu) => {
    // Check if PM button already exists
    if (menu.querySelector('.comfy-pm-button-menu')) {
        return;
    }

    const resetViewButton = menu.querySelector('#comfy-reset-view-button');
    if (!resetViewButton) {
        return;
    }

    const pmButton = createPMButton({
        className: 'comfy-pm-button-menu',
        text: 'Prompt Manager',
        tooltip: 'Launch Prompt Manager (Shift+Click to open in new window)',
        includeIcon: false,
    });

    // Add some styling to match ComfyUI buttons
    pmButton.style.marginLeft = '4px';
    pmButton.style.marginRight = '4px';

    resetViewButton.insertAdjacentElement('afterend', pmButton);
};

/**
 * Wait for element and execute callback
 */
const waitForElement = (selector, callback) => {
    const observer = new MutationObserver((mutations, obs) => {
        const element = document.querySelector(selector);
        if (element) {
            callback(element);
            obs.disconnect();
        }
    });

    observer.observe(document.body, { childList: true, subtree: true });

    // Also check immediately in case element already exists
    const element = document.querySelector(selector);
    if (element) {
        callback(element);
        observer.disconnect();
    }
};

/**
 * Initialize PM button widgets
 */
const initializePMButtons = () => {
    // Add to right menu (mobile/desktop toolbar)
    waitForElement('.comfyui-menu-right', addPMButtonToRightMenu);

    // Add to classic menu
    waitForElement('.comfy-menu', addPMButtonToMenu);
};

/**
 * Register about page badge for Prompt Manager
 */
const registerPMAboutBadge = () => {
    app.registerExtension({
        name: 'PromptManager.AboutBadge',
        aboutPageBadges: [
            {
                label: 'Prompt-Manager v2.0',
                url: 'https://github.com/yourusername/ComfyUI-PromptManager',
                icon: 'pi pi-bookmark'
            }
        ]
    });
};

/**
 * Initialize everything when app is ready
 */
const initialize = () => {
    initializePMButtons();
    registerPMAboutBadge();
};

// Wait for app to be ready before initializing
if (app.extensionManager) {
    initialize();
} else {
    // If extension manager not ready, wait for it
    const checkReady = setInterval(() => {
        if (app.extensionManager) {
            clearInterval(checkReady);
            initialize();
        }
    }, 100);
}

// Also initialize on window load as fallback
window.addEventListener('load', () => {
    setTimeout(initialize, 1000);
});