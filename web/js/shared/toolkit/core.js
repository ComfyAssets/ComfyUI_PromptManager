export function createScope() {
    const controller = new AbortController();
    const cleanups = [];
    return {
        signal: controller.signal,
        listen(target, event, callback) {
            target.addEventListener(event, callback, { signal: controller.signal });
        },
        cleanup(callback) { cleanups.push(callback); },
        debounce(callback, delay) {
            let timer;
            cleanups.push(() => clearTimeout(timer));
            return (...args) => {
                clearTimeout(timer);
                if (!controller.signal.aborted) timer = setTimeout(() => callback(...args), delay);
            };
        },
        dispose() {
            if (controller.signal.aborted) return;
            controller.abort();
            cleanups.splice(0).forEach(callback => callback());
        },
    };
}

export function themeFromColor(color) {
    let values;
    const hex = color.match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/i)?.[1];
    if (hex) {
        const expanded = hex.length === 3 ? [...hex].map(value => value + value).join('') : hex;
        values = expanded.match(/../g).map(value => parseInt(value, 16));
    } else if (/^rgb\(/.test(color)) {
        values = color.match(/[\d.]+/g)?.slice(0, 3).map(Number);
    }
    if (!values || values.length !== 3) return null;
    const linear = values.map(value => value / 255).map(value => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4);
    return linear[0] * .2126 + linear[1] * .7152 + linear[2] * .0722 > .35 ? 'light' : 'dark';
}

export function mountTheme(element, settings, scope) {
    const stylesheet = new URL('./controls.css', import.meta.url).href;
    if (![...document.querySelectorAll('link[rel="stylesheet"]')].some(link => link.href === stylesheet)) {
        const link = document.createElement('link');
        link.rel = 'stylesheet';
        link.href = stylesheet;
        document.head.append(link);
    }
    const preference = matchMedia('(prefers-color-scheme: dark)');
    const update = () => {
        const background = getComputedStyle(document.body).getPropertyValue('--bg-color').trim();
        element.dataset.caTheme = themeFromColor(background) || (preference.matches ? 'dark' : 'light');
    };
    const observer = new MutationObserver(update);
    for (const target of [document.documentElement, document.body]) observer.observe(target, { attributes: true, attributeFilter: ['style', 'class', 'data-theme'] });
    scope.cleanup(() => observer.disconnect());
    scope.listen(preference, 'change', update);
    if (settings?.addEventListener) scope.listen(settings, 'Comfy.ColorPalette.change', update);
    update();
}

export function createButton(text, className = '') {
    const element = document.createElement('button');
    element.type = 'button';
    element.className = className;
    element.textContent = text;
    return element;
}

export function createSlider(label, { id, min, max, step, value, unit }, scope, onInput) {
    const field = document.createElement('div');
    field.className = 'ca-range';
    const caption = document.createElement('label');
    caption.htmlFor = id;
    caption.textContent = label;
    const input = document.createElement('input');
    Object.assign(input, { type: 'range', id, min, max, step, value });
    const output = document.createElement('output');
    output.setAttribute('for', id);
    output.value = `${value} ${unit}`;
    scope.listen(input, 'input', () => {
        output.value = `${input.value} ${unit}`;
        onInput(Number(input.value));
    });
    field.append(caption, input, output);
    return field;
}

export function createField(label, input, id) {
    const field = document.createElement('label');
    field.className = 'ca-field';
    field.htmlFor = id;
    const caption = document.createElement('span');
    caption.className = 'ca-field-label';
    caption.textContent = label;
    input.id = id;
    field.append(caption, input);
    return field;
}

export function createSwitch(label, className, id) {
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.role = 'switch';
    input.checked = true;
    input.className = className;
    const field = createField(label, input, id);
    field.className = 'ca-switch';
    field.replaceChildren(input, document.createTextNode(label));
    return field;
}

export function createSelect(options, className) {
    const select = document.createElement('select');
    select.className = className;
    for (const [value, text] of options) select.add(new Option(text, value));
    return select;
}

export function createSegments(label, options, name, scope, onChange) {
    const fieldset = document.createElement('fieldset');
    fieldset.className = 'ca-segments';
    const legend = document.createElement('legend');
    legend.className = 'ca-visually-hidden';
    legend.textContent = label;
    fieldset.append(legend);
    options.forEach(([value, text], index) => {
        const input = document.createElement('input');
        input.type = 'radio';
        input.name = name;
        input.value = value;
        input.checked = index === 0;
        const field = createField(text, input, `${name}-${value}`);
        field.replaceChildren(input, field.firstChild);
        scope.listen(input, 'change', () => onChange(value));
        fieldset.append(field);
    });
    return fieldset;
}
