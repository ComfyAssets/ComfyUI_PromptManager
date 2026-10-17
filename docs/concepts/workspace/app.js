import {
  createButton,
  createScope,
  createSwitch,
  mountTheme,
} from "/toolkit/core.js";
const scope = createScope();
const imageBatchSize = 12,
  loadedImageCounts = new Map();
let imageScrollObserver = null;
let masonryObserver = null,
  masonryFrame = 0,
  tagSession = null;
mountTheme(document.body, null, scope);
const $ = (id) => document.getElementById(id);
const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const paths = {
  send: "m22 2-7 20-4-9-9-4L22 2ZM22 2 11 13",
  images: "M3 3h18v18H3z M3 16l5-5 5 5 4-4 4 4 M8 7h.01",
  library: "M4 4h5v16H4z M11 4h4v16h-4z M17 5l3-1 3 15-3 1z",
  tags: "M3 3h8l10 10-8 8L3 11V3ZM7 7h.01",
  activity: "M3 12h4l3-8 4 16 3-8h4",
  settings:
    "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8ZM12 2v3m0 14v3M2 12h3m14 0h3M5 5l2 2m10 10 2 2M5 19l2-2M17 7l2-2",
  search: "M10.5 3a7.5 7.5 0 1 0 0 15 7.5 7.5 0 0 0 0-15Zm5.5 13 5 5",
  branch:
    "M6 3v12M18 9a9 9 0 0 1-9 9M9 18a3 3 0 1 1-6 0 3 3 0 0 1 6 0M21 6a3 3 0 1 1-6 0 3 3 0 0 1 6 0",
  masonry: "M3 3h7v11H3zM14 3h7v6h-7zM3 18h7v3H3zM14 13h7v8h-7z",
  tagImage: "M3 4h8l9 9-7 7L3 10V4ZM7 8h.01M18 2v5M15.5 4.5h5",
  grid: "M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z",
  list: "M9 5h12M9 12h12M9 19h12M3 5h.01M3 12h.01M3 19h.01",
  copy: "M8 8h13v13H8z M16 8V3H3v13h5",
  info: "M12 8h.01M12 11v6M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20Z",
  folder: "M3 5h7l2 3h9v12H3z",
  star: "m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-3-5.6 3 1.1-6.2L3 9.6l6.2-.9L12 3Z",
  check: "m5 12 4 4L19 6",
  upload: "M12 16V3m-5 5 5-5 5 5M3 15v6h18v-6",
  review: "M4 3h16v18H4zM8 7h8M8 12h8M8 17h5",
  history: "M3 11a9 9 0 1 1 2 7M3 4v7h7M12 7v6l4 2",
  plus: "M12 4v16M4 12h16",
};
function icon(name) {
  return `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${paths[name] || paths.info}"/></svg>`;
}
function button(label, action, extra = "", className = "") {
  const control = createButton("", className);
  control.dataset.action = action;
  return control.outerHTML.replace(
    "></button>",
    `${extra ? " " + extra : ""}>${label}</button>`,
  );
}
function sendButton(p, role = "pair", variant = 0, withLabel = false) {
  return button(
    icon("send") + (withLabel ? " Send to ComfyUI" : ""),
    "send",
    `data-prompt="${p.id}" data-role="${role}" data-variant="${variant}" aria-label="Send ${role === "pair" ? "prompt pair" : role + " prompt"} to ComfyUI: ${esc(p.title)}"`,
    "send " + (withLabel ? "" : "icon-btn"),
  );
}
const prompts = [
  {
    id: "alpine",
    title: "Alpine observatory",
    category: "Landscapes",
    tags: ["cinematic", "blue_hour", "architecture"],
    positive:
      "A remote alpine observatory at blue hour, warm light through glass, dramatic mountain peaks, cinematic composition, atmospheric mist, fine detail",
    negatives: [
      { name: "Quality baseline", text: "blurry, low quality, low resolution" },
      {
        name: "Clean architecture",
        text: "blurry, low quality, low resolution, distorted architecture, watermark, text",
      },
      { name: "No negative", text: "" },
    ],
    scene: "alpine",
    color: ["#14233c", "#9aaac3", "#d8d9dd"],
    rating: 5,
  },
  {
    id: "dunes",
    title: "Quiet geometry",
    category: "Architecture",
    tags: ["minimal", "warm_light", "editorial"],
    positive:
      "Monolithic sandstone pavilion in a vast desert, sweeping dunes, warm terracotta, late afternoon sun, architectural photography, quiet geometry",
    negatives: [
      {
        name: "Natural materials",
        text: "plastic, harsh shadows, noise, watermark",
      },
      {
        name: "Clean lines",
        text: "plastic, harsh shadows, noise, watermark, distorted geometry",
      },
    ],
    scene: "desert",
    color: ["#6c4948", "#ddad91", "#efe0c5"],
    rating: 4,
  },
  {
    id: "forest",
    title: "Somewhere in the pines",
    category: "Landscapes",
    tags: ["forest", "mist", "peaceful"],
    positive:
      "A quiet cedar cabin above a still forest lake, towering evergreens, soft morning mist, muted jade and moss, a small amber window, cinematic still",
    negatives: [
      {
        name: "Soft atmosphere",
        text: "oversaturated, blurry, low quality, text",
      },
    ],
    scene: "forest",
    color: ["#163b3d", "#6f9691", "#c0cfc1"],
    rating: 5,
  },
  {
    id: "city",
    title: "After the rain",
    category: "Environments",
    tags: ["neon", "night", "cinematic"],
    positive:
      "Rain-soaked city street at night, blue neon and rose reflections, deep perspective, cinematic science fiction, solitary architecture, detailed atmosphere",
    negatives: [
      {
        name: "Clean atmosphere",
        text: "blurry, low resolution, watermark, oversaturated",
      },
      {
        name: "Less signage",
        text: "blurry, low resolution, watermark, oversaturated, text, signs",
      },
    ],
    scene: "city",
    color: ["#1a2143", "#6869a1", "#cf8fbc"],
    rating: 4,
  },
  {
    id: "lunar",
    title: "The long way home",
    category: "Illustration",
    tags: ["space", "minimal", "concept_art"],
    positive:
      "An isolated lunar research outpost, a luminous planet above the horizon, sweeping pale terrain, quiet science fiction, finely detailed concept art",
    negatives: [
      { name: "Quality baseline", text: "blurry, low quality, watermark" },
    ],
    scene: "lunar",
    color: ["#19283b", "#7398ac", "#e2d3c4"],
    rating: 5,
  },
  {
    id: "coast",
    title: "Between sea and sky",
    category: "Landscapes",
    tags: ["coast", "soft_light", "peaceful"],
    positive:
      "A solitary lighthouse on a rugged coastal cliff, sea mist, glints of morning light on turquoise water, atmospheric perspective, fine art landscape",
    negatives: [
      {
        name: "Natural light",
        text: "oversaturated, flat lighting, watermark, text",
      },
    ],
    scene: "coast",
    color: ["#255565", "#8cbcc2", "#e8d9bf"],
    rating: 4,
  },
];
const images = Array.from({ length: 4 }, (_, j) =>
  prompts.map((p, i) => ({
    id: `${p.id}-${j}`,
    prompt: p.id,
    index: j,
    variant: j % p.negatives.length,
    seed: 42001 + i * 100 + j,
    starred: j === 0 && i % 2 === 0,
    filename: `${p.id}_${String(j + 1).padStart(5, "0")}.png`,
  })),
)
  .flat()
  .map((item, index) => {
    const [width, height] = [
      [1536, 1152],
      [1024, 1536],
      [1280, 1280],
      [1600, 900],
      [1152, 1536],
      [1536, 1024],
    ][index % 6];
    return { ...item, width, height, tags: [...prompt(item.prompt).tags] };
  });
const state = {
  page: "images",
  system: "general",
  query: "",
  category: "",
  filter: "all",
  view: "grid",
  sort: "recent",
  size: "comfortable",
  selectedImage: images[0].id,
  selection: new Set(),
  prompt: "alpine",
  variant: 0,
  compare: false,
  reviewImage: images[0].id,
  activity: [
    {
      title: "Library metadata enriched",
      detail:
        "12 existing images linked · 0 duplicates · completed 12 minutes ago",
      done: true,
    },
    {
      title: "3 images need metadata review",
      detail: "Ambiguous sampler paths · your existing links are preserved",
      done: false,
    },
  ],
  saved: [],
  collection: "",
  notice: true,
  settingsDirty: false,
};
let viewer = null,
  viewImage = null,
  sendPayload = null,
  toastTimer,
  slideTimer = null;
function prompt(id) {
  return prompts.find((p) => p.id === id) || prompts[0];
}
function image(id) {
  return images.find((i) => i.id === id) || images[0];
}
function art(p, n = 0, dimensions = { width: 1536, height: 1152 }) {
  const [a, b, c] = p.color;
  const shift = n * 13;
  let shape = "";
  if (p.scene === "alpine")
    shape = `<path d="M0 330 120 95 210 235 350 65 510 260 650 118 800 310V600H0" fill="url(#rock)"/><path d="m120 95-43 89 40-21 32 33 61 39Zm230-30-68 124 61-34 32 38 42-32Z" fill="${c}" opacity=".8"/><path d="M0 415 180 330 330 385 510 295 800 420V600H0" fill="${a}"/><path d="M${300 + shift} 390h170v94H${300 + shift}Z" fill="#1b2930"/><path d="m${285 + shift} 390 100-65 100 65Z" fill="#5c6d78"/><path d="M${318 + shift} 406h133v47h-133Z" fill="#edc38a"/><path d="M${360 + shift} 405v50m45-50v50" stroke="#263747" stroke-width="6"/>`;
  if (p.scene === "desert")
    shape = `<circle cx="620" cy="135" r="53" fill="${c}" opacity=".6"/><path d="M0 360Q160 240 430 360T800 300V600H0Z" fill="#c0836c"/><path d="M0 450Q350 270 800 470V600H0Z" fill="${c}"/><path d="M${280 + shift} 230h210v240h-210Z" fill="#bb8169"/><path d="m${280 + shift} 230 92-45h205l-87 45Z" fill="#e2b599"/><path d="m${490 + shift} 230 87-45v240l-87 45Z" fill="#865a53"/><path d="M${340 + shift} 312a45 45 0 0 1 90 0v158h-90Z" fill="#473d3d"/><path d="M0 520Q180 405 370 480T800 490V600H0Z" fill="#d2a487" opacity=".65"/>`;
  if (p.scene === "forest") {
    shape = `<path d="M0 330 150 170 300 320 480 120 800 330V600H0" fill="${b}" opacity=".5"/><path d="M0 390Q250 340 800 390V600H0Z" fill="#6a9390"/>`;
    for (let i = 0; i < 15; i++) {
      const x = i * 65 - 30;
      shape += `<path d="m${x} 380 37-170 37 170Z" fill="${i % 2 ? a : "#315b59"}" opacity="${i % 3 ? ".85" : ".5"}"/>`;
    }
    shape += `<path d="M320 345h175v93H320Z" fill="#243f3d"/><path d="m299 345 108-84 108 84Z" fill="#152b2e"/><path d="M350 362h45v49h-45Z" fill="#e6bd85"/><path d="M420 362h43v49h-43Z" fill="#e6bd85"/><path d="M0 470Q250 445 800 480" stroke="${c}" opacity=".4" stroke-width="3"/>`;
  }
  if (p.scene === "city") {
    shape = '<path d="M310 600 390 260 470 600Z" fill="#797b9a"/>';
    for (let i = 0; i < 12; i++) {
      const x = i < 6 ? i * 59 : 465 + (i - 6) * 60;
      const h = 140 + ((i * 81 + shift) % 180);
      shape += `<rect x="${x}" y="${310 - h}" width="50" height="${h + 180}" fill="${i % 2 ? a : "#303455"}"/><path d="M${x + 10} ${330 - h}v${h}" stroke="${i % 2 ? "#a9c7d8" : "#d494ba"}" stroke-width="${i % 3 ? 3 : 7}" opacity=".7"/>`;
    }
    shape += `<path d="m0 600 360-160m440 160L440 440" stroke="#9da6c5" opacity=".5"/><path d="m280 600 90-110m150 110-85-110" stroke="#d09bbc" stroke-width="10" opacity=".45"/>`;
  }
  if (p.scene === "lunar")
    shape = `<circle cx="${540 - shift}" cy="130" r="85" fill="${c}" opacity=".9"/><path d="M0 350Q160 240 350 335T800 300V600H0" fill="${b}"/><path d="M0 455Q180 320 500 430T800 390V600H0" fill="${c}"/><path d="M315 350h170v82H315Z" fill="#405b69"/><path d="M300 350a100 65 0 0 1 200 0Z" fill="#b5c4c8"/><path d="M340 367h120v25H340Z" fill="#c8a884"/><path d="M480 320v-95m-30 0h60" stroke="#4d6374" stroke-width="5"/><path d="m100 600 230-150m-120 150 170-130" stroke="#81969c" opacity=".6" stroke-width="7"/>`;
  if (p.scene === "coast")
    shape = `<path d="M0 340Q400 310 800 340V600H0" fill="#76a4ae"/><path d="M0 420 300 300 500 360 410 480 300 600H0" fill="#607b74"/><path d="M300 300 500 360 410 480 300 600H150l170-170Z" fill="#405b60"/><path d="M350 192h46l12 139h-70Z" fill="${c}"/><path d="M340 180h65v25h-65Z" fill="#b48d79"/><path d="m333 180 40-34 39 34Z" fill="#405b69"/><path d="M350 160h46v20h-46Z" fill="#e8cd8c"/><path d="M480 430q90-25 160 0m-140 35q120-20 250 0M400 520q110-30 260 0" fill="none" stroke="${c}" opacity=".55" stroke-width="3"/>`;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${dimensions.width}" height="${dimensions.height}" viewBox="0 0 800 600" preserveAspectRatio="xMidYMid slice"><defs><linearGradient id="sky" x2=".4" y2="1"><stop stop-color="${a}"/><stop offset="1" stop-color="${b}"/></linearGradient><linearGradient id="rock" x2=".7" y2="1"><stop stop-color="${c}"/><stop offset="1" stop-color="${a}"/></linearGradient><radialGradient id="glow"><stop stop-color="${c}" stop-opacity=".25"/><stop offset="1" stop-color="${c}" stop-opacity="0"/></radialGradient></defs><rect width="800" height="600" fill="url(#sky)"/><ellipse cx="${550 - shift}" cy="160" rx="350" ry="250" fill="url(#glow)"/>${shape}<path d="M0 520Q200 485 500 520T800 500V600H0" fill="${b}" opacity=".13"/></svg>`;
  return "data:image/svg+xml," + encodeURIComponent(svg);
}
function notify(message) {
  $("toast").textContent = message;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => ($("toast").textContent = ""), 6500);
}
function dialog(title, body) {
  $("modal").classList.remove("tagging-modal");
  $("modal-title").textContent = title;
  $("modal-body").innerHTML = body;
  if (!$("modal").open) $("modal").showModal();
}
function closeDialog() {
  $("modal").close();
}
function go(hash) {
  closeDialog();
  $("viewer-dialog").close();
  if (location.hash.slice(1) === hash) render();
  else location.hash = hash;
}
function promptBlock(p, role, variant = 0) {
  const negative = p.negatives[variant] || p.negatives[0];
  const text = role === "positive" ? p.positive : negative.text;
  return `<section class="prompt-section ${role}"><div class="prompt-heading"><span class="role-label">${role === "positive" ? "+ Positive prompt" : "− Negative prompt"}</span><div class="prompt-actions">${button(icon("copy"), "copy", `data-prompt="${p.id}" data-role="${role}" data-variant="${variant}" aria-label="Copy ${role} prompt: ${esc(p.title)}"`, "icon-btn")}${sendButton(p, role, variant)}</div></div><p class="prompt-text">${esc(text || "No negative prompt (explicitly empty)")}</p></section>`;
}
function heading(title, description, actions = "") {
  return `<div class="heading"><div><h1 tabindex="-1" id="page-title">${esc(title)}</h1><p class="muted">${description}</p></div><div class="heading-actions">${actions}</div></div>`;
}
function sectionNav(items, active, action) {
  return `<div class="section-nav" role="group" aria-label="${esc(action)} sections">${items.map(([id, title]) => button(title, action, `data-value="${id}" aria-pressed="${active === id}"`)).join("")}</div>`;
}
function vocabulary() {
  return [
    ...new Set([
      ...prompts.flatMap((p) => p.tags),
      ...images.flatMap((i) => i.tags),
    ]),
  ];
}
function nav() {
  const links = (items) =>
    items
      .map(
        ([id, name, ico, count]) =>
          `<a href="#${id}" ${state.page === id ? 'aria-current="page"' : ""}>${icon(ico)}${esc(name)}${count ? `<span class="nav-count">${count}</span>` : ""}</a>`,
      )
      .join("");
  $("primary-nav").innerHTML = links([
    ["images", "Images", "images", images.length],
    ["library", "Prompt library", "library", prompts.length],
    ["tags", "Tags & categories", "tags", vocabulary().length],
    ["metadata", "Inspect a file", "upload", ""],
  ]);
  $("saved-nav").innerHTML = links([
    ["favorites", "Favorites", "star", images.filter((i) => i.starred).length],
    ["review", "Needs review", "review", "3"],
    ...state.saved.map((view, i) => [`saved-${i}`, view.name, "search", ""]),
  ]);
  $("collections-nav").innerHTML = links([
    ["collection-landscapes", "Landscapes", "folder", "12"],
    ["collection-experiments", "Experiments", "folder", "8"],
  ]);
  $("system-nav").innerHTML = links([
    ["activity", "Activity", "activity", state.activity.length],
    ["system", "System", "settings", ""],
    ["design", "Design review", "info", ""],
  ]);
  $("activity-count").textContent = state.activity.length;
}
function toolbar() {
  return `<search class="toolbar"><label class="search-field" for="search">${icon("search")}<input id="search" type="search" placeholder="Search prompts, tags, filenames…" value="${esc(state.query)}" aria-label="Search workspace"></label><select id="category-filter" aria-label="Filter by category"><option value="">All categories</option>${[...new Set(prompts.map((p) => p.category))].map((c) => `<option ${state.category === c ? "selected" : ""}>${esc(c)}</option>`).join("")}</select><select id="sort" aria-label="Sort results">${[
    ["recent", "Recently added"],
    ["name", "Name A–Z"],
    ["rating", "Highest rated"],
  ]
    .map(
      ([id, label]) =>
        `<option value="${id}" ${state.sort === id ? "selected" : ""}>${label}</option>`,
    )
    .join(
      "",
    )}</select>${button("Save view", "save-view")}${button("Filters", "filters")}</search>`;
}
function matches(p, additional = "") {
  return (
    (!state.category || p.category === state.category) &&
    (!state.query ||
      [
        p.title,
        p.positive,
        p.category,
        ...p.tags,
        ...p.negatives.map((n) => n.text),
        additional,
      ]
        .join(" ")
        .toLowerCase()
        .includes(state.query.toLowerCase()))
  );
}
function visibleImages() {
  let result = images.filter(
    (i) =>
      matches(prompt(i.prompt), [i.filename, ...i.tags].join(" ")) &&
      (state.filter !== "favorites" || i.starred) &&
      (!state.collection ||
        (state.collection === "Landscapes"
          ? prompt(i.prompt).category === "Landscapes"
          : ["Architecture", "Environments"].includes(
              prompt(i.prompt).category,
            ))),
  );
  if (state.sort === "name")
    result.sort((a, b) => a.filename.localeCompare(b.filename));
  if (state.sort === "rating")
    result.sort((a, b) => prompt(b.prompt).rating - prompt(a.prompt).rating);
  return result;
}
function mediaCard(i) {
  const p = prompt(i.prompt);
  return `<article class="media-card ${state.selectedImage === i.id ? "selected" : ""}" data-image-id="${i.id}" style="--media-ratio:${i.width}/${i.height}"><div class="media-art">${button(`<img src="${art(p, i.index, i)}" width="${i.width}" height="${i.height}" alt="${esc(p.title)} · sample illustration ${i.index + 1}" ${i.index > 1 ? 'loading="lazy"' : ""}>`, "open-image", `data-id="${i.id}" aria-label="Open ${esc(i.filename)}"`, "image-open")}<div class="image-overlays"><label><input type="checkbox" data-select="${i.id}" ${state.selection.has(i.id) ? "checked" : ""} aria-label="Select ${esc(i.filename)}"></label>${button(i.starred ? "★" : "☆", "favorite", `data-id="${i.id}" aria-pressed="${i.starred}" aria-label="Favorite ${esc(i.filename)}"`, "star-button")}</div></div><div class="media-info"><div><h3>${esc(p.title)}</h3><p>${i.width} × ${i.height} · PNG · Seed ${i.seed}</p></div><div class="image-card-actions">${button(icon("info"), "inspect", `data-id="${i.id}" aria-label="Inspect ${esc(i.filename)}"`, "icon-btn")}${button(icon("tagImage"), "image-tag", `data-id="${i.id}" aria-label="Tag or auto-tag ${esc(i.filename)}"`, "icon-btn")}</div></div></article>`;
}
function imageDetails(i, full = false) {
  const p = prompt(i.prompt);
  return `${!full ? `<img class="detail-cover" src="${art(p, i.index, i)}" width="800" height="600" alt="">` : ""}<div class="${full ? "" : "detail-inner"}"><div class="panel-heading"><h2>${esc(p.title)}</h2><span class="chip">PNG</span></div><p class="small">${i.filename}</p><div class="tags"><span class="chip blue">Execution recorded</span><span class="chip">${esc(p.category)}</span></div>${promptBlock(p, "positive", i.variant)}${promptBlock(p, "negative", i.variant)}<div class="tags">${i.tags.map((t) => button(esc(t), "tag-filter", `data-value="${esc(t)}"`, "chip")).join("")}</div><dl class="meta-grid">${[
    ["Seed", i.seed],
    ["Steps", "28"],
    ["CFG", "6.5"],
    ["Sampler", "dpmpp_2m"],
    ["Scheduler", "karras"],
    ["Model", "illustrative-model.safetensors"],
  ]
    .map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`)
    .join(
      "",
    )}</dl>${full ? `<details><summary>File & provenance</summary><p class="small">output/concepts/${i.filename}<br>Prompt evidence: execution recorded<br>Run: sample-${i.seed} · Original file unchanged</p></details><details><summary>Workflow & raw metadata</summary><pre tabindex="0"><code>${esc(JSON.stringify({ sample: true, seed: i.seed, positive: p.positive, negative: p.negatives[i.variant].text, workflow: "Demo workflow — no actual graph" }, null, 2))}</code></pre>${button("Download sample JSON", "download-workflow", `data-id="${i.id}"`)}</details>` : ""}</div>${!full ? '<div class="detail-footer">' : ""}${sendButton(p, "pair", i.variant, true)}${button(icon("branch") + " Prompt history", "history", `data-prompt="${p.id}"`)}${!full ? "</div>" : ""}`;
}
function renderImages() {
  const title =
    state.filter === "favorites" ? "Favorites" : state.collection || "Images";
  $("main").innerHTML =
    heading(
      title,
      "Your generations, their prompts, and the next idea.",
      button(icon("upload") + " Import", "import") +
        button(icon("plus") + " New prompt", "new-prompt", "", "ca-primary"),
    ) +
    toolbar() +
    `<div class="filter-row"><div class="segmented" role="group" aria-label="Image layout">${button(icon("grid") + " Grid", "image-view", 'data-value="grid" aria-pressed="' + (state.view === "grid") + '"')}${button(icon("list") + " List", "image-view", 'data-value="list" aria-pressed="' + (state.view === "list") + '"')}${button(icon("masonry") + " Masonry", "image-view", 'data-value="masonry" aria-pressed="' + (state.view === "masonry") + '"')}</div><label class="small" for="image-size">Thumbnail size</label><select id="image-size"><option value="comfortable">Comfortable</option><option value="large">Large</option><option value="compact">Compact</option></select><span class="chip">${icon("folder")} All output folders</span>${button("Group by positive prompt →", "go-library", "", "chip")}<span class="sample-tag">Illustrative previews</span></div><div id="bulkbar"></div><div class="gallery-layout"><div class="gallery-body"><div class="section-caption"><span id="result-count"></span><span>Today · October 17</span></div><div id="image-grid" class="image-grid"></div><div id="gallery-continuation" class="infinite-continuation"></div></div><aside id="image-inspector" class="detail-panel" aria-label="Selected image details"></aside></div>`;
  $("image-size").value = state.size;
  updateImages();
}
function updateBulk() {
  $("bulkbar").innerHTML = state.selection.size
    ? `<div class="bulkbar"><strong>${state.selection.size} selected</strong>${button("Add tags", "bulk-tags")}${button("Set category", "bulk-category")}${button("Add to collection", "bulk-collection")}${button("Export metadata", "bulk-export")}${button("Remove links…", "bulk-remove", "", "danger")}${button("Clear", "bulk-clear")}</div>`
    : "";
}
function updateImages() {
  stopImageScroll();
  stopMasonry();
  const matching = visibleImages();
  const key = imageQueryKey();
  const selectedIndex = matching.findIndex((i) => i.id === state.selectedImage);
  const limit = Math.max(
    loadedImageCounts.get(key) || imageBatchSize,
    Math.ceil((selectedIndex + 1) / imageBatchSize) * imageBatchSize,
  );
  loadedImageCounts.set(key, limit);
  const shown = matching.slice(0, limit);
  if (shown.length && !shown.some((i) => i.id === state.selectedImage))
    state.selectedImage = shown[0].id;
  $("result-count").textContent =
    `${shown.length} of ${matching.length} images · ${new Set(matching.map((i) => i.prompt)).size} positive prompts`;
  $("image-grid").className =
    `image-grid ${state.size === "comfortable" ? "" : state.size} ${state.view === "list" ? "list-grid" : state.view === "masonry" ? "masonry-grid" : ""}`;
  $("image-grid").innerHTML = shown.length
    ? shown.map(mediaCard).join("")
    : '<div class="empty">No matching images. Try another search or category.</div>';
  $("image-inspector").innerHTML = shown.length
    ? imageDetails(image(state.selectedImage))
    : '<div class="detail-inner"><h2>No image selected</h2><p class="small">Clear the filters or choose a matching image.</p></div>';
  updateBulk();
  if (state.view === "masonry" && shown.length) mountMasonry($("image-grid"));
  updateImageContinuation();
}
function imageQueryKey() {
  return JSON.stringify([
    state.query,
    state.category,
    state.filter,
    state.collection,
    state.sort,
  ]);
}
function stopImageScroll() {
  imageScrollObserver?.disconnect();
  imageScrollObserver = null;
}
function updateImageContinuation() {
  stopImageScroll();
  const continuation = $("gallery-continuation");
  if (!continuation) return;
  const total = visibleImages().length,
    loaded = $("image-grid").querySelectorAll(".media-card").length;
  continuation.innerHTML =
    loaded < total
      ? `<p class="small">${loaded} of ${total} images · more load as you scroll</p>${button("Load more images", "load-more-images")}`
      : `<p class="small">${total ? "You’re all caught up" : "No matching images"} · ${total} images</p>`;
  if (loaded >= total) return;
  const key = imageQueryKey();
  imageScrollObserver = new IntersectionObserver(
    (entries) => {
      if (
        entries.some((entry) => entry.isIntersecting) &&
        imageQueryKey() === key &&
        !document.querySelector("dialog[open]")
      )
        loadMoreImages();
    },
    { rootMargin: "180px 0px" },
  );
  imageScrollObserver.observe(continuation);
}
function loadMoreImages(trigger = null) {
  const grid = $("image-grid");
  if (!grid) return;
  const loaded = grid.querySelectorAll(".media-card").length;
  const matching = visibleImages(),
    next = matching.slice(loaded, loaded + imageBatchSize);
  if (!next.length) return;
  stopImageScroll();
  stopMasonry();
  loadedImageCounts.set(imageQueryKey(), loaded + next.length);
  grid.insertAdjacentHTML("beforeend", next.map(mediaCard).join(""));
  $("result-count").textContent =
    `${loaded + next.length} of ${matching.length} images · ${new Set(matching.map((i) => i.prompt)).size} positive prompts`;
  if (state.view === "masonry") mountMasonry(grid);
  updateImageContinuation();
  if (trigger?.dataset?.action === "load-more-images")
    grid
      .querySelector(`[data-action="open-image"][data-id="${next[0].id}"]`)
      ?.focus();
  notify(
    `${next.length} more images loaded. ${loaded + next.length} of ${matching.length}.`,
  );
}
function stopMasonry() {
  masonryObserver?.disconnect();
  masonryObserver = null;
  cancelAnimationFrame(masonryFrame);
}
function mountMasonry(grid) {
  const cards = [...grid.children];
  let previousWidth = 0;
  function layout() {
    if (!grid.isConnected) return;
    const styles = getComputedStyle(grid);
    const columns = styles.gridTemplateColumns.split(/\s+/).length;
    const gap = parseFloat(styles.columnGap) || 13;
    const width = (grid.clientWidth - gap * (columns - 1)) / columns;
    if (width <= 0) return;
    const heights = Array(columns).fill(0);
    for (const card of cards)
      card.style.setProperty("--masonry-width", `${width}px`);
    for (const card of cards) {
      const column = heights.indexOf(Math.min(...heights));
      card.style.setProperty("--masonry-x", `${column * (width + gap)}px`);
      card.style.setProperty("--masonry-y", `${heights[column]}px`);
      heights[column] += card.getBoundingClientRect().height + gap;
    }
    grid.style.setProperty(
      "--masonry-height",
      `${Math.max(...heights) - gap}px`,
    );
  }
  function schedule() {
    cancelAnimationFrame(masonryFrame);
    masonryFrame = requestAnimationFrame(layout);
  }
  masonryObserver = new ResizeObserver((entries) => {
    if (
      entries.some(
        (entry) =>
          entry.target !== grid.parentElement ||
          entry.contentRect.width !== previousWidth,
      )
    )
      schedule();
    previousWidth = grid.parentElement.getBoundingClientRect().width;
  });
  masonryObserver.observe(grid.parentElement);
  cards.forEach((card) => masonryObserver.observe(card));
  layout();
}
function renderLibrary() {
  const shown = prompts.filter((p) => matches(p));
  if (state.sort === "name")
    shown.sort((a, b) => a.title.localeCompare(b.title));
  if (state.sort === "rating") shown.sort((a, b) => b.rating - a.rating);
  $("main").innerHTML =
    heading(
      "Prompt library",
      "One positive. Reusable negatives. Every experiment stays connected.",
      button("Export", "export-library") +
        button("+ New prompt", "new-prompt", "", "ca-primary"),
    ) +
    toolbar() +
    `<div class="section-caption"><span>${shown.length} positive prompts · ${shown.reduce((n, p) => n + p.negatives.length, 0)} negative variants</span><span>Images are grouped by positive prompt</span></div><div class="library-grid">${shown.map((p) => `<article class="prompt-card"><div class="card-art"><img src="${art(p, 0)}" alt="" width="800" height="600"><img src="${art(p, 2)}" alt="" width="800" height="600"></div><div class="card-content"><div class="card-title">${button(esc(p.title), "history", `data-prompt="${p.id}"`)}<span class="chip">${esc(p.category)}</span></div>${promptBlock(p, "positive")}<div class="card-meta"><span class="chip red">− ${p.negatives.length} negative variants</span><span>4 runs</span><span>4 images</span></div><div class="tags">${p.tags.map((t) => `<span class="chip">${t}</span>`).join("")}</div></div><div class="card-footer"><span>${"★".repeat(p.rating)} · Used today</span><div class="row-actions">${sendButton(p, "positive")}${button(icon("branch") + " Explore history", "history", `data-prompt="${p.id}"`)}</div></div></article>`).join("") || '<div class="empty">No matching prompts.</div>'}</div>`;
}
function renderHistory() {
  const p = prompt(state.prompt),
    v = p.negatives[state.variant] || p.negatives[0];
  $("main").innerHTML =
    heading(
      p.title,
      "Prompt history · one positive, one shared gallery",
      button("← Prompt library", "go-library") +
        button("Edit metadata", "edit-prompt", `data-prompt="${p.id}"`) +
        sendButton(p, "pair", state.variant, true),
    ) +
    `<article class="surface"><span class="chip">${esc(p.category)}</span>${promptBlock(p, "positive", state.variant)}${promptBlock(p, "negative", state.variant)}<div class="card-meta"><span>4 tracked runs</span><span>4 images</span><span>${p.negatives.length} negative variants</span></div></article><section class="surface timeline-panel"><div class="panel-heading"><div><h2>Negative variants</h2><p class="small">Choose a red dot to change the negative and filter this gallery.</p></div><div class="row-actions">${button("Compare negatives", "compare-negatives", `aria-pressed="${state.compare}"`)}${button("+ Add negative", "new-negative", `data-prompt="${p.id}"`)}</div></div><div class="timeline"><div class="positive-lane"><span>Positive prompt · unchanged</span></div>${p.negatives.map((n, i) => `<div class="variant-lane" style="--lane-offset:${38 + i * 47}px">${button("−", "variant", `data-index="${i}" aria-pressed="${state.variant === i}" aria-label="Select negative ${esc(n.name)}"`, "variant-dot")}<span>${esc(n.name)} · ${images.filter((im) => im.prompt === p.id && im.variant === i).length} runs</span>${images.filter((im) => im.prompt === p.id && im.variant === i).length > 1 ? button("", "run-filter", `data-prompt="${p.id}" data-variant="${i}" aria-label="Show repeat run for ${esc(n.name)}"`, "run-dot") : ""}</div>`).join("")}</div><div class="timeline-lower"><span class="small">Green + positive · red − negative · small dot repeat run</span>${button("All images for this positive", "history-all")}</div></section>${
      state.compare
        ? `<section class="surface"><h2>Compare negative variants</h2><p>Baseline compared with ${esc(v.name)}. Choose another red dot to change the comparison.</p><div class="compare-grid"><div class="compare-item">${promptBlock(p, "negative", 0)}</div><div class="compare-item">${promptBlock(p, "negative", state.variant)}</div></div><p class="small">Added terms</p>${
            v.text
              .split(",")
              .map((t) => t.trim())
              .filter(
                (t) =>
                  t &&
                  !p.negatives[0].text
                    .split(",")
                    .map((x) => x.trim())
                    .includes(t),
              )
              .map((t) => `<span class="diff-added">+ ${esc(t)}</span>`)
              .join("") || '<p class="small">No added terms.</p>'
          }</section>`
        : ""
    }<section class="history-gallery"><div class="panel-heading"><h2>Shared gallery</h2><span class="small">${esc(v.name)} · same positive prompt</span></div><div class="image-grid">${
      images
        .filter((i) => i.prompt === p.id && i.variant === state.variant)
        .map(mediaCard)
        .join("") || '<div class="empty">Saved variant · no runs yet.</div>'
    }</div></section>`;
}
const config = { "video-auto": false, "console-log": false };
function setting(id, label, description, type = "toggle", options = []) {
  let control = "";
  const val = config[id];
  if (type === "toggle")
    control = `<input id="${id}" data-setting="${id}" type="checkbox" ${val !== false ? "checked" : ""}>`;
  else if (type === "select")
    control = `<select id="${id}" data-setting="${id}">${options.map((o, i) => `<option ${val === o || (val === undefined && i === 0) ? "selected" : ""}>${esc(o)}</option>`).join("")}</select>`;
  else
    control = `<input id="${id}" data-setting="${id}" type="${type}" value="${esc(val ?? options[0] ?? "")}" ${type === "range" ? 'min="1" max="16"' : ""}>`;
  return `<div class="setting-row"><div><label for="${id}">${label}</label><p>${description}</p></div>${control}</div>`;
}
const systemSections = [
  ["general", "Overview"],
  ["appearance", "Appearance"],
  ["sources", "Sources & scans"],
  ["integrations", "Integrations"],
  ["tagging", "Auto-tagging"],
  ["maintenance", "Maintenance"],
  ["backups", "Backup & export"],
  ["diagnostics", "Diagnostics & logs"],
];
function operation(
  title,
  desc,
  action,
  label = "Review",
  extra = "",
  danger = false,
) {
  return `<div class="operation"><div><h3>${esc(title)}</h3><p>${desc}</p></div><div class="row-actions">${button(label, action, extra, danger ? "danger" : "")}</div></div>`;
}
function renderSystem() {
  let body = "";
  switch (state.system) {
    case "general":
      body = `<div class="summary-metrics">${[
        [prompts.length, "Positive prompts"],
        [
          prompts.reduce((n, p) => n + p.negatives.length, 0),
          "Negative variants",
        ],
        [images.length, "Images"],
        ["Healthy", "Database integrity"],
      ]
        .map(
          ([n, s]) =>
            `<div class="metric"><strong>${n}</strong><span>${s}</span></div>`,
        )
        .join(
          "",
        )}</div><div class="two-col"><section class="surface"><h2>Library health</h2>${operation("3 ambiguous image links", "Review metadata before assigning a positive / negative pair.", "go-review", "Review metadata")}${operation("Backup status", "Last sample backup today at 09:30 · verification passed.", "system-tab", "View backups", 'data-value="backups"')}${operation("No missing thumbnails", "Generate only missing previews when necessary.", "job", "Review thumbnails", 'data-job="Thumbnail check"')}</section><section class="surface"><h2>Workspace preferences</h2>${operation("One display configuration", "Grid, density and paging settings are shared across views.", "system-tab", "Appearance", 'data-value="appearance"')}${operation("2 sample output sources", "Monitoring and enrichment share one source list.", "system-tab", "Manage sources", 'data-value="sources"')}${operation("Statistics", "Usage by prompt, category, tag and date.", "stats", "Explore")}</section></div>`;
      break;
    case "appearance":
      body = `<section class="surface"><h2>Browsing & display</h2><p>One set of defaults; toolbar changes apply to the current view.</p>${setting("default-view", "Default layout", "Choose your starting image layout.", "select", ["Grid", "List", "Masonry"])}${setting("density", "Thumbnail size", "Readable previews by default; no fixed column-count setting.", "select", ["Comfortable", "Large", "Compact"])}<div class="setting-row"><div><h3>Continuous browsing</h3><p>Images load automatically as you scroll in Grid, List and Masonry. Batch sizing is handled internally.</p></div><span class="chip blue">Infinite scroll</span></div>${setting("metadata-auto", "Load metadata on selection", "Use the same inspector in the gallery and image viewer.")}${setting("metadata-cache", "Cache metadata in this browser", "Reuse fetched metadata until it changes or you clear this cache.")}${setting("image-quality", "Image loading quality", "Preserve original / optimized / fast image-loading preferences.", "select", ["Optimized", "Original", "Fast"])}${setting("video-auto", "Autoplay videos", "Videos stay within the same gallery and use native playback controls.")}${setting("video-muted", "Mute videos by default", "Keep playback quiet while browsing.")}${setting("video-loop", "Loop videos", "Repeat a selected clip until you navigate away.")}${setting("paths", "Show file paths", "Paths appear under File & provenance, not across every card.")}${setting("open-mode", "Open from ComfyUI", "Keep the node pack’s popup / new-tab preference.", "select", ["New tab", "Popup window"])}${setting("timeout", "Node search auto-hide (seconds)", "Node search behavior only. Use 0 to keep results visible.", "number", [30])}</section>`;
      break;
    case "sources":
      body = `${state.notice ? `<div class="notice"><div><h2>Bring older images into your prompt history</h2><p>The schema upgrades automatically. An optional metadata scan links existing positives and negatives without regenerating all thumbnails.</p></div><div class="row-actions">${button("Later", "dismiss-notice")}${button("Review scan", "scan-review", "", "ca-primary")}</div></div>` : ""}<section class="surface"><div class="panel-heading"><h2>Image sources</h2>${button("+ Add source", "add-source")}</div><p>The same folders power browsing, monitoring and metadata enrichment.</p><div id="source-list">${operation("ComfyUI output", "/ComfyUI/output · watching · sample path", "scan-review", "Scan changes")}${operation("Archive", "/images/archive · manual scan · sample path", "scan-review", "Scan folder")}</div>${setting("monitoring", "Watch for new outputs", "New files enter the same library. Existing scans do not count as new runs.")}${setting("workers", "Worker threads", "Balance background scans and thumbnail work against generation.", "range", [4])}${operation("Enrich existing metadata", "Resolve both prompts, deduplicate recipes, preserve uncertain links.", "scan-review", "Review scan")}${operation("Thumbnail generation", "Generate missing previews; rebuilding all is a separate explicit option.", "thumbnail-review", "Review options")}</section>`;
      break;
    case "integrations":
      body = `<section class="surface"><h2>ComfyUI</h2><p>One send contract across cards, prompt history and every metadata view.</p>${setting("send-destination", "Default send destination", "A destination picker previews both roles before applying.", "select", ["Ask each time", "Selected Prompt Manager node", "Create a Prompt Manager (+/-) node"])}${setting("renderer", "Host compatibility", "Use the shared compatibility layer for Classic and Nodes 2.0.", "select", ["Follow host renderer", "Classic", "Nodes 2.0"])}${setting("return-canvas", "Return to canvas after send", "Apply text without queuing generation.")}</section><section class="surface"><h2>LoRA & resources</h2><p>Preserves LoraManager detection, data import and positive-only trigger injection.</p>${setting("lora-enable", "LoraManager integration", "Sample detection: available. Production would detect the installed extension.")}${setting("lora-triggers", "Automatically include trigger words", "Trigger words apply only to positive text; connected node inputs remain authoritative.")}${setting("civitai-key", "CivitAI API key", "Credentials stay in the server configuration. Do not enter a real key into this mock.", "password", [""])}${operation("LoRA reference data", "Review and import resource metadata.", "job", "Simulate import", 'data-job="LoRA reference import"')}</section>`;
      break;
    case "tagging":
      body = `<section class="surface"><h2>Auto-tagging</h2><p>One tagging configuration for images and prompt-library actions. Every result can be reviewed before applying.</p>${setting("tag-model", "Tagging model", "Preserve all current engines; show download size and loaded status together.", "select", ["WD14 SwinV2 · ~400 MB", "WD14 ViT · ~400 MB", "Ollama · local model", "HuggingFace · ~16 GB"])}${setting("tag-existing", "Existing tags", "Skipping remains the default; replace and merge are explicit.", "select", ["Skip already tagged", "Merge with existing", "Replace after review"])}${setting("threshold-general", "General-tag confidence", "Current WD14 default: 0.35.", "number", [0.35])}${setting("threshold-character", "Character-tag confidence", "Current WD14 default: 0.85.", "number", [0.85])}<div class="field"><label for="tag-template">LLM tag template</label><textarea id="tag-template" data-setting="tag-template">${esc(config["tag-template"] || "Describe visible subjects, composition, environment and lighting as comma-separated tags. No commentary.")}</textarea></div><div class="row-actions">${button("Preview tagging review", "tag-review", "", "ca-primary")}${button("Simulate download", "job", 'data-job="Tagging model download"')}${button("Simulate unload", "job", 'data-job="Unload tagging model"')}</div></section>`;
      break;
    case "maintenance":
      body = `<div class="notice"><div><h2>Check first. Review changes. Then apply.</h2><p>One home for database and media maintenance. Scanning is separate from changing or removing records.</p></div>${button("Run health checks", "health-check", "", "ca-primary")}</div><section class="surface"><h2>Database</h2>${operation("Integrity & relationship checks", "Validate foreign keys, text hashes, pair uniqueness and image associations.", "health-check", "Check")}${operation("Duplicate prompt text", "Review canonical text reuse and dependency-safe merging.", "maintenance-plan", "Review plan", 'data-plan="duplicate prompts"')}${operation("Optimize database", "Reclaim unused SQLite space. This does not delete prompts or media.", "maintenance-plan", "Review plan", 'data-plan="database optimization"')}${operation("Unused prompt components", "Preview unreferenced components. Pair references and history remain protected.", "maintenance-plan", "Review plan", 'data-plan="unused prompt components"', true)}</section><section class="surface"><h2>Images & cache</h2>${operation("Missing-file links", "Remove stale database links only. Files on disk are never deleted by this operation.", "maintenance-plan", "Review plan", 'data-plan="missing-file links"', true)}${operation("Duplicate image files", "Find matching file content, review groups and choose what to keep. Separate from duplicate prompt text.", "duplicates", "Find duplicates")}${operation("Thumbnail cache", "Generate missing thumbnails or explicitly rebuild all.", "thumbnail-review", "Review options")}${operation("Metadata cache", "Clear only Prompt Manager metadata cached by this browser.", "maintenance-plan", "Review plan", 'data-plan="browser metadata cache"')}</section>`;
      break;
    case "backups":
      body = `<div class="two-col"><section class="surface"><h2>Database backup</h2><p>A complete recovery snapshot, including prompt pairs, metadata and image links. Media files are separate.</p><div class="row-actions spaced">${button("Simulate backup", "backup", "", "ca-primary")}${button("Review restore…", "restore", "", "danger")}</div><p class="small">Sample backup · today 09:30 · verified</p></section><section class="surface"><h2>Portable export</h2><p>Export reusable recipes and tags as versioned JSON. This is distinct from a complete database backup.</p><div class="row-actions spaced">${button("Export sample JSON", "export-library")}${button("Import / inspect file", "import")}</div></section></div><section class="surface"><h2>Recovery policy <span class="chip blue">Proposed</span></h2>${setting("backup-before", "Create a safety backup before maintenance", "Preview a verified backup before applying operations that change records.")}${setting("backup-schedule", "Scheduled database backups", "Production feature proposal; backup storage must be configured first.", "select", ["Manual only", "Daily", "Weekly"])}<p class="warning-note">A restore would be staged, migrated and validated before replacing live data.</p></section>`;
      break;
    case "diagnostics":
      body = `<section class="surface"><div class="panel-heading"><h2>Diagnostics</h2>${button("Run checks", "health-check")}</div><div class="meta-grid"><div><dt>Database</dt><dd>Healthy · sample status</dd></div><div><dt>Sources</dt><dd>2 configured · sample status</dd></div><div><dt>Viewer.js</dt><dd>1.15.2 · pinned vendor</dd></div><div><dt>Host renderers</dt><dd>Classic / Nodes 2.0</dd></div></div>${button("Download sample diagnostic report", "diagnostic-export")}${operation("Image-link diagnostics", "Check a file-to-prompt association without changing it.", "metadata-demo", "Inspect sample")}</section><section class="surface"><h2>Logs</h2><div class="toolbar spaced-16"><label for="log-level" class="small">Level</label><select id="log-level"><option>All levels</option><option>WARNING</option><option>INFO</option></select>${button("Refresh", "refresh-logs")}${button("Download sample log", "download-logs")}</div><pre id="log-lines" tabindex="0"><code>09:42:18 INFO  Scan completed: 12 enriched, 0 duplicate links\n09:42:18 WARNING 3 images have ambiguous prompt metadata\n09:39:04 INFO  Database backup verified\n09:38:52 INFO  Source monitor started: output</code></pre>${setting("runtime-level", "Runtime log level", "One control replaces a separate gallery debug toggle.", "select", ["INFO", "WARNING", "DEBUG", "ERROR"])}${setting("console-log", "Console logging", "Keep routine browser and server output quiet unless requested.")}<details><summary>Log files & retention</summary><p class="small">prompt_manager.log · 1.4 MB · rotated copies: 3</p>${button("Review log cleanup", "maintenance-plan", 'data-plan="rotated logs"')}</details></section>`;
      break;
  }
  $("main").innerHTML =
    heading(
      "System",
      "Configuration, maintenance and recovery—with browsing kept in the workspace.",
      '<span class="chip">Sample configuration</span>',
    ) +
    sectionNav(systemSections, state.system, "system-tab") +
    `<div class="settings-layout">${body}<div class="save-row"><span class="small" id="settings-status">${state.settingsDirty ? "Unsaved changes in this prototype" : "Changes apply to this browser tab only."}</span>${button("Save preferences", "save-settings", "", "ca-primary")}</div></div>`;
  hydrateSwitches();
}
function hydrateSwitches() {
  for (const previous of document.querySelectorAll(
    'input[data-setting][type="checkbox"]',
  )) {
    const field = createSwitch("", "", previous.id);
    const control = field.querySelector("input");
    control.checked = previous.checked;
    control.dataset.setting = previous.dataset.setting;
    previous.replaceWith(field);
  }
}
function renderTags() {
  const tags = vocabulary();
  $("main").innerHTML =
    heading(
      "Tags & categories",
      "Organize once. Reuse everywhere.",
      button("+ Create tag", "new-tag") +
        button("Auto-tag images", "tag-review", "", "ca-primary"),
    ) +
    `<div class="two-col"><section class="surface"><h2>Tag vocabulary</h2><p>Category/tag autocomplete uses this same vocabulary across nodes and dialogs.</p><div class="table-wrap"><table><caption>Reusable tags</caption><thead><tr><th scope="col">Tag</th><th scope="col">Prompts</th><th scope="col">Images</th><th scope="col">Actions</th></tr></thead><tbody>${tags.map((t) => `<tr><td>${esc(t)}</td><td>${prompts.filter((p) => p.tags.includes(t)).length}</td><td>${images.filter((i) => i.tags.includes(t)).length}</td><td><div class="row-actions">${button("Browse", "tag-filter", `data-value="${esc(t)}"`)}${button("Rename / merge", "rename-tag", `data-value="${esc(t)}"`)}</div></td></tr>`).join("")}</tbody></table></div></section><section class="surface"><h2>Categories</h2><p>Categories provide a broad home. Tags describe content; proposed collections group projects without changing either.</p>${[...new Set(prompts.map((p) => p.category))].map((c) => operation(c, `${prompts.filter((p) => p.category === c).length} positive prompts`, "category-filter", "Browse", `data-value="${c}"`)).join("")}${operation("Manage category", "Rename or consolidate a category with a preview of affected recipes.", "manage-category", "Review")}</section></div>`;
}
function renderActivity() {
  nav();
  $("main").innerHTML =
    heading(
      "Activity",
      "Scans, tagging, downloads and maintenance share one history.",
      '<span class="chip blue">Unified activity · proposed</span>',
    ) +
    `<section class="surface"><h2>Recent work</h2>${state.activity.map((a, i) => `<div class="activity-item"><div class="panel-heading"><h3>${a.done ? "✓" : "○"} ${esc(a.title)}</h3><span class="chip ${a.done ? "green" : "blue"}">${a.done ? "Completed" : "Needs attention"}</span></div><p>${esc(a.detail)}</p>${a.running ? `<progress value="65" max="100" aria-label="Sample job progress">65%</progress><div class="row-actions">${button("Simulate completion", "complete-job", `data-index="${i}"`)}${button("Cancel job", "cancel-job", `data-index="${i}"`)}</div>` : !a.done ? button("Review metadata", "go-review") : ""}</div>`).join("")}</section>`;
}
function renderMetadata() {
  $("main").innerHTML =
    heading(
      "Inspect a file",
      "A standalone file uses the same image viewer and prompt actions.",
      button("Open sample metadata", "metadata-demo", "", "ca-primary"),
    ) +
    `<section class="surface"><h2>Inspect without importing</h2><p>Keep the existing local-file inspection workflow. Only add the file to your library when you choose to import it.</p><div class="file-drop spaced-20">${icon("upload")}<label for="local-file">Choose an image for a local preview</label><input id="local-file" type="file" accept="image/png,image/jpeg,image/webp"><span class="small">This mock previews your file locally. Sample metadata is available with “Open sample metadata”.</span></div><div id="local-preview"></div></section>`;
}
function renderReview() {
  const target = image(state.reviewImage),
    p = prompt(target.prompt);
  $("main").innerHTML =
    heading(
      "Metadata review",
      "Uncertain evidence stays visible until you decide.",
      button("Open activity", "go-activity"),
    ) +
    `<div class="notice"><div><h2>3 images need a decision</h2><p>Multiple sampler paths were found. The existing image links were preserved. Choose the correct context or leave them unresolved.</p></div><span class="chip">Sample review queue</span></div><div class="gallery-layout"><div class="image-grid">${images.slice(0, 3).map(mediaCard).join("")}</div><section class="surface"><h2>Resolve the sample association</h2><p class="small">${esc(target.filename)}</p><div class="row-actions spaced-15">${button(icon("tagImage") + " Tag image", "image-tag", `data-id="${target.id}"`)}</div><div class="field"><label for="review-context">Sampler context</label><select id="review-context">${p.negatives.map((n, index) => `<option>Sampler ${12 + index * 16} · ${esc(p.title)} / ${esc(n.name)}</option>`).join("")}<option>Leave unresolved</option></select></div>${promptBlock(p, "positive", target.variant)}${promptBlock(p, "negative", target.variant)}<div class="dialog-actions">${button("Leave for later", "go-images")}${button("Apply sample association", "resolve-review", "", "ca-primary")}</div></section></div>`;
}
const featureMap = [
  [
    "Admin prompt cards / per-prompt gallery",
    "Prompt library + one prompt history workspace",
    "Retained, rebuilt",
  ],
  [
    "Standalone image gallery / Admin image modal",
    "One Images workspace + shared Viewer.js viewer",
    "Consolidated",
  ],
  [
    "Video playback, autoplay / mute / loop, viewer slideshow and transforms",
    "Shared media viewer; playback controls in Appearance; same image navigation",
    "Retained",
  ],
  [
    "Metadata page / viewer sidebar / full-text popups",
    "One reusable metadata inspector; inline complete prompt text",
    "Consolidated",
  ],
  [
    "Prompt add, edit, rating, notes, copy, delete",
    "Recipe editor, shared details, contextual bulk actions; reference-safe archive",
    "Retained, clarified",
  ],
  [
    "Categories, tag rename / merge, autocomplete",
    "Tags & categories; same vocabulary in every editor",
    "Retained",
  ],
  [
    "Grid/list, thumbnail size, columns, pages/infinite scroll",
    "Infinite scroll only; one Appearance configuration and contextual layout / density overrides",
    "Simplified",
  ],
  [
    "Scan, rescan, monitoring directories, worker threads",
    "Sources & scans; explicit metadata enrichment vs thumbnail work",
    "Consolidated",
  ],
  [
    "Ollama, HuggingFace, WD14 variants, thresholds, review",
    "One Auto-tagging configuration and review flow",
    "Retained",
  ],
  [
    "Thumbnail quality, size limit, metadata cache, paths",
    "Appearance + cache controls in Maintenance",
    "Retained",
  ],
  [
    "Prompt dedup, hash checks, consistency, unused prompts",
    "Maintenance plan with dependency checks and impact preview",
    "Retained, safer flow",
  ],
  [
    "Image dedup, orphan image links, browser cache",
    "Separate reviewed operations in Maintenance",
    "Retained",
  ],
  [
    "Database backup / restore, JSON export",
    "Backup & export; recovery and portable recipes clearly distinguished",
    "Retained",
  ],
  [
    "Diagnostics, image-link checks, logs, rotation, log levels",
    "System → Diagnostics & logs",
    "Retained",
  ],
  [
    "LoRA detection/import, trigger injection, CivitAI key",
    "System → Integrations; positive-only triggers",
    "Retained",
  ],
  [
    "Statistics / counts",
    "System overview + usage breakdown; context counts in the workspace",
    "Retained",
  ],
  [
    "Send prompts to ComfyUI from every prompt surface",
    "Role-aware paper-plane action + destination / overwrite preview",
    "New proposal",
  ],
  [
    "Reusable negatives / approved branch timeline",
    "Prompt history with selected negative directly beneath positive",
    "Approved concept",
  ],
  [
    "Favorites, saved searches, project collections",
    "Sidebar views; collections do not duplicate recipe text",
    "New proposal",
  ],
  [
    "Ambiguous metadata review, source provenance, run comparisons",
    "Needs review queue; recorded vs inferred evidence; pair/run selection",
    "New / expanded",
  ],
  [
    "Unified jobs, cancellation, resumable progress visibility",
    "Activity center; cancellation distinguished from hiding progress",
    "New proposal",
  ],
  [
    "Scheduled backups, pre-change safety snapshots",
    "Explicit recovery policy after storage configuration",
    "New proposal",
  ],
];
function renderDesign() {
  $("main").innerHTML =
    heading(
      "A workspace for the whole prompt lifecycle",
      "Design review · current functionality mapped to its proposed home",
    ) +
    `<div class="review-intro"><p>Discover an image. Understand its recipe. Compare an experiment. Send exactly the text you want back to ComfyUI. The same controls, vocabulary and inspector follow that journey.</p><p>This is an interactive proposal, not a production migration. All data is illustrative. Existing backend routes, node schemas and vendored libraries remain unchanged. The approved timeline is preserved alongside this new concept.</p></div><div class="two-col"><section class="surface"><h2>A consistent send action</h2><div class="icon-set">${icon("send")}${button(icon("send") + " Send positive", "send", 'data-prompt="alpine" data-role="positive"', "send")}${sendButton(prompts[0], "negative", 0)}</div><p>One original, simple paper-plane outline in ComfyUI blue. A role-labelled action on every displayed positive or negative, plus a pair action. The picker previews the target node and changed fields. Sending does not queue a generation or load an entire workflow.</p></section><section class="surface"><h2>What deserves adding</h2><p>Saved searches, project collections, metadata review, an activity center and explicit recovery plans. A favorites toggle is separate from the existing 1–5 quality rating. These are proposals to evaluate, not promised features.</p></section></div><section class="surface"><div class="table-wrap"><table><caption>Feature inventory & redesign map</caption><thead><tr><th scope="col">Today</th><th scope="col">Proposed home / behavior</th><th scope="col">Disposition</th></tr></thead><tbody>${featureMap.map((row) => `<tr>${row.map((text) => `<td>${text}</td>`).join("")}</tr>`).join("")}</tbody></table></div></section><section class="surface"><h2>Implementation boundaries</h2><p>Keep Viewer.js 1.15.2 and the shared ComfyAssets toolkit as pinned artifacts. This mock runs the actual vendored viewer. Production work should reuse controllers and services, replace overlapping UI shells, and preserve the existing test matrix.</p><p>New services would be needed for persisted execution history, saved searches/collections, job history and the ComfyUI send bridge. The bridge must detect disconnected tabs, reject incompatible or linked target inputs, and never guess a positive/negative destination. Unknown metadata remains unknown; an explicitly empty negative is a deliberate value.</p><p>The maintained node layouts are out of scope for this application rebuild. Each implementation slice needs regressions first, page workflows, and relevant Classic / Nodes 2.0 host tests. No test or coverage gate is removed.</p></section>`;
}
function openSend(p, role = "pair", variant = 0) {
  clearInterval(slideTimer);
  slideTimer = null;
  sendPayload = { p, role, variant };
  dialog(
    "Send to ComfyUI",
    `<p class="demo-note">Prototype: destinations are illustrative. Nothing will be sent to your running ComfyUI.</p><div class="field"><label for="send-target">Destination</label><select id="send-target"><option value="pair">Prompt Manager (+/−) · Node #42 · Current workflow</option><option value="new">Create a new Prompt Manager (+/−) node</option><option value="text" ${role === "pair" ? "disabled" : ""}>Selected text encoder · ${role === "negative" ? "negative" : "positive"} input${role === "pair" ? " (single prompt only)" : ""}</option></select></div><p class="small">${role === "pair" ? "Both prompt fields will be replaced together." : `Only ${role} text will change. The other prompt stays untouched.`} Connected inputs would be checked before applying.</p>${role !== "negative" ? `<div class="send-preview"><h3>+ Positive · replace</h3><p>${esc(p.positive)}</p></div>` : ""}${role !== "positive" ? `<div class="send-preview"><h3>− Negative · ${p.negatives[variant].text ? "replace" : "explicitly clear"}</h3><p>${esc(p.negatives[variant].text || "No negative prompt (explicitly empty)")}</p></div>` : ""}<p class="small spaced-16">Text transfer only · does not queue a run · does not replace your workflow</p><div class="dialog-actions">${button("Cancel", "close-dialog")}${button(icon("send") + " Simulate send", "send-confirm", "", "ca-primary")}</div>`,
  );
}
function openViewer(id) {
  viewImage = image(id);
  state.selectedImage = id;
  const p = prompt(viewImage.prompt);
  $("viewer-title").textContent = `${p.title} · ${viewImage.filename}`;
  $("viewer-details").innerHTML = imageDetails(viewImage, true);
  viewer?.destroy();
  viewer = null;
  $("viewer-stage").replaceChildren();
  const img = document.createElement("img");
  img.src = art(p, viewImage.index, viewImage);
  img.alt = `${p.title}, illustrative generated image`;
  $("viewer-stage").append(img);
  if (!$("viewer-dialog").open) $("viewer-dialog").showModal();
  $("viewer-toolbar").innerHTML =
    button("← Previous", "viewer-prev") +
    button("−", "viewer-zoom-out", 'aria-label="Zoom out"') +
    button("+", "viewer-zoom-in", 'aria-label="Zoom in"') +
    button("Fit", "viewer-fit") +
    button("Rotate", "viewer-rotate") +
    button("Flip", "viewer-flip") +
    button(slideTimer ? "Pause" : "Play", "viewer-play") +
    button("Download", "viewer-download") +
    button("Next →", "viewer-next");
  requestAnimationFrame(() => {
    viewer = new window.Viewer(img, {
      inline: true,
      navbar: false,
      title: false,
      toolbar: false,
      button: false,
      keyboard: false,
      transition: false,
      fullscreen: false,
      initialViewIndex: 0,
    });
  });
}
function exportSample(name, data, type = "application/json") {
  const blob = new Blob(
    [typeof data === "string" ? data : JSON.stringify(data, null, 2)],
    { type },
  );
  const url = URL.createObjectURL(blob),
    a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  notify("Downloaded sample data only.");
}
function startJob(name) {
  closeDialog();
  state.activity.unshift({
    title: name,
    detail:
      "Simulated background job · 65% · closing this view does not cancel it",
    done: false,
    running: true,
  });
  location.hash = "activity";
  if (state.page === "activity") renderActivity();
  notify("Sample job added to Activity.");
}
function maintenancePlan(name) {
  dialog(
    "Review maintenance plan",
    `<p class="demo-note">Sample impact preview. No database, media or cache will be modified.</p><h3>${esc(name)}</h3><dl class="meta-grid"><div><dt>Scope</dt><dd>${name === "missing-file links" ? "2 stale image links" : "Selected operation only"}</dd></div><div><dt>Protected</dt><dd>Referenced components and run history</dd></div><div><dt>Backup</dt><dd>Create and verify before changes</dd></div><div><dt>Media on disk</dt><dd>No source files removed</dd></div></dl><p class="small">Production should calculate impact before enabling Apply. Cancelling here would leave all data untouched.</p><div class="dialog-actions">${button("Cancel", "close-dialog")}${button("Simulate reviewed operation", "job", `data-job="${esc(name)}"`, "ca-primary")}</div>`,
  );
}
function editor(p = null, negativeOnly = false, revision = false) {
  dialog(
    negativeOnly
      ? "Add a negative variant"
      : revision
        ? "Create text revision"
        : p
          ? "Edit recipe metadata"
          : "New prompt",
    `<form id="recipe-form"><p class="demo-note">Changes stay in the prototype. Existing recipe text would be immutable while referenced; text edits create a new recipe.</p>${!negativeOnly ? `<div class="field"><label for="recipe-title">Title</label><input id="recipe-title" required value="${esc(p?.title || "")}"></div><div class="field"><label for="recipe-positive">Positive prompt</label><textarea id="recipe-positive" required ${p && !revision ? "readonly" : ""}>${esc(p?.positive || "")}</textarea></div>` : ""}<div class="field"><label for="recipe-negative">${negativeOnly ? "Negative prompt" : "Negative prompt (optional)"}</label><textarea id="recipe-negative" ${p && !negativeOnly && !revision ? "readonly" : ""}>${esc(negativeOnly ? "" : p?.negatives[0]?.text || "")}</textarea></div>${
      !negativeOnly
        ? `<div class="two-col"><div class="field"><label for="recipe-category">Category</label><input id="recipe-category" value="${esc(p?.category || "")}" list="categories"></div><div class="field"><label for="recipe-tags">Tags, separated by commas</label><input id="recipe-tags" value="${esc(p?.tags.join(", ") || "")}" list="tag-options"></div></div><datalist id="categories">${[...new Set(prompts.map((item) => item.category))].map((c) => `<option value="${esc(c)}"></option>`).join("")}</datalist><datalist id="tag-options">${vocabulary()
            .map((c) => `<option value="${esc(c)},"></option>`)
            .join(
              "",
            )}</datalist><div class="field"><label for="recipe-rating">Rating</label><select id="recipe-rating">${[5, 4, 3, 2, 1, 0].map((n) => `<option value="${n}" ${(p?.rating ?? 0) === n ? "selected" : ""}>${n ? `${n} / 5` : "Unrated"}</option>`).join("")}</select></div><div class="field"><label for="recipe-notes">Notes</label><textarea id="recipe-notes">${esc(p?.notes || "")}</textarea></div>`
        : '<p class="small">Leave empty for an explicitly empty negative. The positive stays unchanged.</p>'
    }<div class="dialog-actions">${p && !negativeOnly && !revision ? button("Create text revision", "revise-prompt", `data-prompt="${p.id}"`) + button("Archive…", "archive-prompt", `data-prompt="${p.id}"`, "danger") : ""}${button("Cancel", "close-dialog")}<button type="submit" class="ca-primary">Save in prototype</button></div></form>`,
  );
  $("recipe-form").addEventListener("submit", (event) => {
    event.preventDefault();
    if (negativeOnly) {
      const text = $("recipe-negative").value.trim();
      const existing = p.negatives.findIndex(
        (n) => n.text.trim().toLowerCase() === text.toLowerCase(),
      );
      if (existing < 0) p.negatives.push({ name: "New experiment", text });
      state.variant = existing < 0 ? p.negatives.length - 1 : existing;
      closeDialog();
      renderHistory();
      notify("Negative saved without adding another positive or gallery.");
      return;
    }
    if (p && !revision) {
      p.title = $("recipe-title").value;
      p.category = $("recipe-category").value;
      p.tags = $("recipe-tags")
        .value.split(",")
        .map((t) => t.trim())
        .filter(Boolean);
      p.rating = Number($("recipe-rating").value);
      p.notes = $("recipe-notes").value;
    } else {
      p = {
        id: `new-${prompts.length}`,
        title: $("recipe-title").value,
        positive: $("recipe-positive").value,
        category: $("recipe-category").value || "Uncategorized",
        tags: $("recipe-tags")
          .value.split(",")
          .map((t) => t.trim())
          .filter(Boolean),
        rating: Number($("recipe-rating").value),
        notes: $("recipe-notes").value,
        negatives: [
          { name: "Initial negative", text: $("recipe-negative").value },
        ],
        scene: "alpine",
        color: prompts[0].color,
      };
      prompts.push(p);
    }
    closeDialog();
    nav();
    location.hash = `prompt/${p.id}`;
    if (state.page === "prompt") {
      state.prompt = p.id;
      renderHistory();
    }
    notify("Saved in this prototype tab.");
  });
}
const taggingModels = [
  ["wd14-swinv2", "WD14 SwinV2 · fast classifier"],
  ["wd14-vit", "WD14 ViT · visual tags"],
  ["ollama", "Ollama · local language model"],
  ["huggingface", "HuggingFace · language model"],
];
function tagReview(id = state.selectedImage) {
  const target = image(id),
    p = prompt(target.prompt);
  tagSession = { imageId: target.id, source: state.page, model: "wd14-swinv2" };
  dialog(
    "Tag image",
    `
    <p class="demo-note">Prototype · suggestions are simulated. No model is downloaded or run.</p>
    <div class="tag-image-heading"><img src="${art(p, target.index, target)}" width="${target.width}" height="${target.height}" alt="${esc(p.title)}" class="tag-image-preview"><div><h3>${esc(p.title)}</h3><p class="small">${esc(target.filename)}</p><span class="chip">${target.width} × ${target.height}</span></div></div>
    ${state.page === "review" ? '<p class="tag-scope-note small">Tags belong to this image. Resolve its prompt association separately in Needs review.</p>' : ""}
    <div class="field"><label for="manual-image-tags">Image tags · edit manually</label><input id="manual-image-tags" value="${esc(target.tags.join(", "))}" aria-describedby="manual-tags-help"><p id="manual-tags-help" class="small">Comma-separated. Generated suggestions are added only after you approve them.</p></div>
    <div class="tag-model-panel"><h3>Auto-tag this image</h3><div class="field"><label for="image-tag-model">Tagging model</label><select id="image-tag-model">${taggingModels.map(([value, label]) => `<option value="${value}">${label}</option>`).join("")}</select></div><div id="image-tag-model-options"></div>${button(icon("tagImage") + " Generate suggestions", "generate-tags", "", "ca-primary")}</div>
    <section class="tag-suggestion-panel" aria-labelledby="tag-suggestions-title"><div class="panel-heading"><h3 id="tag-suggestions-title">Review suggestions</h3><span class="small" id="suggestion-count">Not generated</span></div><div id="tag-suggestions"><p class="small">Choose a model, then generate tags. Uncheck anything you do not want to add.</p></div><div id="tag-generation-status" role="status" class="small"></div></section>
    <div class="dialog-actions">${button("Cancel", "close-dialog")}${button("Save manual tags", "apply-tags", "", "ca-primary")}</div>
  `,
  );
  $("modal").classList.add("tagging-modal");
  renderTagModelOptions();
}
function renderTagModelOptions() {
  const model = $("image-tag-model").value;
  tagSession.model = model;
  $("image-tag-model-options").innerHTML = model.startsWith("wd14")
    ? '<div class="two-col"><div class="field"><label for="image-general-threshold">General confidence</label><input id="image-general-threshold" type="number" min="0" max="1" step="0.05" value="0.35"></div><div class="field"><label for="image-character-threshold">Character confidence</label><input id="image-character-threshold" type="number" min="0" max="1" step="0.05" value="0.85"></div></div>'
    : '<div class="field"><label for="image-tag-instructions">Tagging instructions</label><textarea id="image-tag-instructions">Describe the visible subject, setting, composition and lighting as comma-separated tags.</textarea></div>';
  $("tag-suggestions").innerHTML =
    '<p class="small">Generate suggestions with the selected model before approving.</p>';
  $("suggestion-count").textContent = "Not generated";
  $("tag-generation-status").textContent = "";
  $("modal-body").querySelector('[data-action="apply-tags"]').textContent =
    "Save manual tags";
}
function generateTags() {
  const target = image(tagSession.imageId),
    p = prompt(target.prompt);
  const suggestions = {
    alpine: [
      ["mountain", 0.98],
      ["observatory", 0.95],
      ["snow", 0.88],
      ["blue_hour", 0.81],
    ],
    desert: [
      ["sandstone", 0.98],
      ["desert", 0.96],
      ["pavilion", 0.88],
      ["warm_light", 0.81],
    ],
    forest: [
      ["forest", 0.98],
      ["cabin", 0.94],
      ["lake", 0.87],
      ["mist", 0.81],
    ],
    city: [
      ["city", 0.98],
      ["neon", 0.95],
      ["rain", 0.87],
      ["night", 0.81],
    ],
    lunar: [
      ["moon", 0.98],
      ["space", 0.95],
      ["outpost", 0.88],
      ["planet", 0.81],
    ],
    coast: [
      ["lighthouse", 0.98],
      ["coast", 0.95],
      ["sea", 0.89],
      ["cliff", 0.81],
    ],
  }[p.scene];
  const classifier = tagSession.model.startsWith("wd14");
  const threshold = classifier ? Number($("image-general-threshold").value) : 0;
  if (
    classifier &&
    !["image-general-threshold", "image-character-threshold"].every((id) =>
      $(id).checkValidity(),
    )
  ) {
    ["image-general-threshold", "image-character-threshold"].find(
      (id) => !$(id).reportValidity(),
    );
    return;
  }
  const shown = suggestions.filter(([, confidence]) => confidence >= threshold);
  $("tag-suggestions").innerHTML = shown.length
    ? shown
        .map(
          ([tag, confidence], index) =>
            `<label class="tag-suggestion" for="suggestion-${index}"><input id="suggestion-${index}" type="checkbox" data-tag-suggestion="${tag}" checked><span>${tag}</span><span class="tag-confidence">${classifier ? Math.round(confidence * 100) + "%" : "Suggested"}</span></label>`,
        )
        .join("")
    : '<p class="small">No suggestions met this threshold. Adjust it or enter tags manually.</p>';
  $("suggestion-count").textContent = `${shown.length} suggestions`;
  $("tag-generation-status").textContent =
    `Sample suggestions from ${taggingModels.find(([id]) => id === tagSession.model)[1]}. Approve tags to save.`;
  $("modal-body").querySelector('[data-action="apply-tags"]').textContent =
    "Approve selected tags";
}
function simpleInput(title, label, action, value = "") {
  dialog(
    title,
    `<div class="field"><label for="action-input">${label}</label><input id="action-input" value="${esc(value)}"></div><div class="dialog-actions">${button("Cancel", "close-dialog")}${button("Apply in prototype", action, "", "ca-primary")}</div>`,
  );
}
function navigate(event) {
  const hash = decodeURIComponent(location.hash.slice(1) || "images");
  state.page = hash.split("/")[0];
  if (hash.startsWith("prompt/")) {
    state.prompt = hash.split("/")[1];
    state.variant = 0;
  }
  if (state.page === "favorites") {
    state.filter = "favorites";
    state.collection = "";
  } else if (state.page.startsWith("collection-")) {
    state.collection =
      state.page === "collection-landscapes" ? "Landscapes" : "Experiments";
    state.filter = "all";
  } else if (state.page.startsWith("saved-")) {
    const saved = state.saved[Number(state.page.split("-")[1])];
    state.query = saved?.query || "";
    state.category = saved?.category || "";
    state.filter = saved?.filter || "all";
  } else if (state.page === "images") {
    state.filter = "all";
    state.collection = "";
  }
  render();
  if (event) $("page-title")?.focus();
}
function render() {
  stopImageScroll();
  stopMasonry();
  nav();
  const page = state.page;
  $("breadcrumb").textContent =
    page === "prompt"
      ? "Prompt history"
      : page === "system"
        ? "System"
        : page === "design"
          ? "Design review"
          : page === "library"
            ? "Prompt library"
            : page === "metadata"
              ? "Inspect a file"
              : page.startsWith("collection-")
                ? "Collection"
                : page[0].toUpperCase() + page.slice(1);
  document.title = `${$("breadcrumb").textContent} · Prompt Manager concept`;
  if (page === "library") renderLibrary();
  else if (page === "prompt") renderHistory();
  else if (page === "system") renderSystem();
  else if (page === "tags") renderTags();
  else if (page === "activity") renderActivity();
  else if (page === "metadata") renderMetadata();
  else if (page === "review") renderReview();
  else if (page === "design") renderDesign();
  else renderImages();
}
const actions = {
  "load-more-images": loadMoreImages,
  "close-dialog": closeDialog,
  send: (b) =>
    openSend(
      prompt(b.dataset.prompt),
      b.dataset.role || "pair",
      Number(b.dataset.variant || 0),
    ),
  "send-confirm": () => {
    const { p, role } = sendPayload;
    const destination = $("send-target").selectedOptions[0].textContent;
    closeDialog();
    notify(
      `Demo: ${role === "pair" ? "positive and negative" : role + " prompt"} would be sent to ${destination}. No run queued.`,
    );
    state.activity.unshift({
      title: "Sample prompt handoff",
      detail: `${p.title} · ${role} · no actual ComfyUI changes`,
      done: true,
    });
    nav();
  },
  copy: async (b) => {
    const p = prompt(b.dataset.prompt),
      text =
        b.dataset.role === "negative"
          ? p.negatives[Number(b.dataset.variant || 0)].text
          : p.positive;
    try {
      if (!navigator.clipboard)
        throw new Error("Clipboard requires a secure context");
      await navigator.clipboard.writeText(text);
      notify("Prompt copied.");
    } catch {
      dialog(
        "Copy prompt",
        `<div class="field"><label for="copy-text">Select and copy this text</label><textarea id="copy-text" readonly>${esc(text)}</textarea></div><p class="small">Automatic clipboard access requires a secure browser context.</p>`,
      );
      $("copy-text").select();
    }
  },
  "open-image": (b) => openViewer(b.dataset.id),
  "metadata-demo": () => openViewer(images[0].id),
  inspect: (b) => {
    state.selectedImage = b.dataset.id;
    if (state.page === "review") {
      state.reviewImage = b.dataset.id;
      renderReview();
    } else if ($("image-inspector")) updateImages();
    else openViewer(b.dataset.id);
  },
  "image-tag": (b) => tagReview(b.dataset.id),
  favorite: (b) => {
    const i = image(b.dataset.id);
    i.starred = !i.starred;
    nav();
    if (
      ["images", "favorites"].includes(state.page) ||
      state.page.startsWith("collection-")
    )
      updateImages();
    else render();
    notify(
      i.starred ? "Added to favorites in this tab." : "Removed from favorites.",
    );
  },
  "image-view": (b) => {
    state.view = b.dataset.value;
    renderImages();
  },
  history: (b) => {
    go(`prompt/${b.dataset.prompt}`);
  },
  variant: (b) => {
    state.variant = Number(b.dataset.index);
    renderHistory();
  },
  "compare-negatives": () => {
    state.compare = !state.compare;
    renderHistory();
  },
  "history-all": () => {
    dialog(
      "All images for this positive",
      `<div class="image-grid">${images
        .filter((i) => i.prompt === state.prompt)
        .map(mediaCard)
        .join("")}</div>`,
    );
  },
  "run-filter": (b) => {
    dialog(
      "Repeat run",
      `<p class="small">Same positive and negative, another execution.</p><div class="image-grid spaced-15">${images
        .filter(
          (i) =>
            i.prompt === b.dataset.prompt &&
            i.variant === Number(b.dataset.variant),
        )
        .slice(-1)
        .map(mediaCard)
        .join("")}</div>`,
    );
  },
  "new-prompt": () => editor(),
  "new-negative": (b) => editor(prompt(b.dataset.prompt), true),
  "edit-prompt": (b) => editor(prompt(b.dataset.prompt)),
  "revise-prompt": (b) => editor(prompt(b.dataset.prompt), false, true),
  "archive-prompt": (b) =>
    maintenancePlan("archive " + prompt(b.dataset.prompt).title),
  "go-library": () => go("library"),
  "go-images": () => go("images"),
  "go-activity": () => go("activity"),
  "go-review": () => go("review"),
  "system-tab": (b) => {
    state.system = b.dataset.value;
    if (state.page !== "system") location.hash = "system";
    else renderSystem();
  },
  "save-settings": () => {
    state.settingsDirty = false;
    $("settings-status").textContent =
      "Preferences saved for this prototype tab.";
    notify("Prototype preferences saved. Server configuration unchanged.");
  },
  "dismiss-notice": () => {
    state.notice = false;
    renderSystem();
  },
  "save-view": () =>
    simpleInput(
      "Save this view",
      "View name",
      "save-view-confirm",
      state.query ? `Search: ${state.query}` : "My workspace view",
    ),
  "save-view-confirm": () => {
    state.saved.push({
      name: $("action-input").value || "Saved view",
      query: state.query,
      category: state.category,
      filter: state.filter,
    });
    closeDialog();
    nav();
    notify("Sample saved view added to the sidebar.");
  },
  filters: () => {
    dialog(
      "Refine this view",
      `<div class="field"><label for="filter-folder">Folder</label><select id="filter-folder"><option>All output folders</option><option>Concepts</option><option>Archive</option></select></div><div class="field"><label for="filter-role">Prompt evidence</label><select id="filter-role"><option>All evidence</option><option>Execution recorded</option><option>Workflow inferred</option><option>Needs review</option></select></div><div class="field"><label for="filter-tags">Tags</label><input id="filter-tags" value="${esc(state.query)}" placeholder="e.g. cinematic"></div><p class="small">Tags are interactive in this mock. Folder and evidence options show the proposed filter design.</p><div class="dialog-actions">${button("Clear filters", "clear-filters")}${button("Apply tag filter", "apply-filters", "", "ca-primary")}</div>`,
    );
  },
  "apply-filters": () => {
    state.query = $("filter-tags").value;
    closeDialog();
    render();
  },
  "clear-filters": () => {
    state.query = "";
    state.category = "";
    closeDialog();
    render();
  },
  "category-filter": (b) => {
    state.category = b.dataset.value;
    go("library");
  },
  "tag-filter": (b) => {
    state.query = b.dataset.value;
    if (state.page === "images" && !$("viewer-dialog").open) renderImages();
    else go("library");
  },
  "bulk-clear": () => {
    state.selection.clear();
    updateImages();
  },
  "bulk-tags": () =>
    simpleInput(
      "Tag selected images",
      "Tags, separated by commas",
      "bulk-apply",
      "cinematic, concept",
    ),
  "bulk-category": () =>
    simpleInput(
      "Categorize selected images",
      "Category",
      "bulk-apply",
      "Landscapes",
    ),
  "bulk-collection": () =>
    simpleInput(
      "Add selected images to collection",
      "Collection name",
      "bulk-apply",
      "Experiments",
    ),
  "bulk-apply": () => {
    const value = $("action-input").value;
    closeDialog();
    notify(
      `Demo: ${state.selection.size} selected images would be updated with “${value}”.`,
    );
  },
  "bulk-remove": () => maintenancePlan("selected image links"),
  "bulk-export": () =>
    exportSample(
      "selected-sample-images.json",
      images.filter((i) => state.selection.has(i.id)),
    ),
  job: (b) => startJob(b.dataset.job || "Background operation"),
  "complete-job": (b) => {
    const a = state.activity[Number(b.dataset.index)];
    a.running = false;
    a.done = true;
    a.detail = "Simulated completion · no production state changed";
    renderActivity();
  },
  "cancel-job": (b) => {
    const a = state.activity[Number(b.dataset.index)];
    a.running = false;
    a.done = true;
    a.detail =
      "Cancelled at a sample file boundary · completed work would be preserved";
    renderActivity();
  },
  "scan-review": () =>
    dialog(
      "Review metadata scan",
      `<p class="demo-note">Sample scan plan · nothing is scanned until the production action is started.</p><div class="meta-grid"><div><dt>Sources</dt><dd>2 configured folders</dd></div><div><dt>Scope</dt><dd>New and unresolved files</dd></div></div><p class="small">Enrich positive / negative associations, reuse canonical text and report unchanged files. Do not inflate run counts or recreate existing thumbnails.</p><div class="dialog-actions">${button("Cancel", "close-dialog")}${button("Simulate scan", "job", 'data-job="Metadata enrichment scan"', "ca-primary")}</div>`,
    ),
  "thumbnail-review": () =>
    dialog(
      "Thumbnail maintenance",
      `<div class="field"><label for="thumb-mode">Scope</label><select id="thumb-mode"><option>Missing thumbnails only</option><option>Rebuild all (explicit opt-in)</option></select></div><div class="field"><label for="thumb-quality">Quality</label><select id="thumb-quality"><option>Medium · optimized</option><option>High · original</option><option>Low · fastest</option></select></div><div class="field"><label for="thumb-limit">Preview size limit</label><input id="thumb-limit" type="number" value="512" min="128" max="2048"></div><div class="dialog-actions">${button("Cancel", "close-dialog")}${button("Simulate thumbnail job", "job", 'data-job="Thumbnail generation"', "ca-primary")}</div>`,
    ),
  "maintenance-plan": (b) => maintenancePlan(b.dataset.plan),
  "health-check": () =>
    dialog(
      "Sample health report",
      `<p class="demo-note">Illustrative check results.</p><div class="operation-list">${[
        ["Integrity check", "Passed"],
        ["Foreign keys & pair references", "Passed"],
        ["Canonical hashes", "Passed"],
        ["Missing files", "2 links need review"],
        ["Ambiguous metadata", "3 images need review"],
      ]
        .map(
          ([k, v]) =>
            `<div class="operation"><h3>${k}</h3><span class="chip">${v}</span></div>`,
        )
        .join(
          "",
        )}</div><div class="dialog-actions">${button("Close", "close-dialog")}${button("Review missing links", "maintenance-plan", 'data-plan="missing-file links"', "ca-primary")}</div>`,
    ),
  duplicates: () =>
    dialog(
      "Review duplicate image files",
      `<p class="demo-note">Sample duplicate group · matching content, separate file locations.</p><div class="compare-grid">${[0, 1].map((n) => `<div class="compare-item"><img src="${art(prompts[0])}" width="800" height="600" alt="Sample duplicate ${n + 1}" class="sample-fluid"><label><input type="radio" name="keep-duplicate" ${n === 0 ? "checked" : ""}> Keep ${n === 0 ? "original" : "archive copy"}</label></div>`).join("")}</div><p class="small">Production should preview file and database-link consequences before enabling deletion. This prototype changes neither.</p><div class="dialog-actions">${button("Cancel", "close-dialog")}${button("Review duplicate cleanup", "maintenance-plan", 'data-plan="duplicate image groups"')}</div>`,
    ),
  backup: () => {
    state.activity.unshift({
      title: "Sample database backup",
      detail:
        "Verified recovery snapshot · demonstration only, no real database downloaded",
      done: true,
    });
    nav();
    notify("Sample backup completed. Your real database was not accessed.");
  },
  restore: () =>
    dialog(
      "Restore preview",
      `<p class="demo-note">Production restore must stage, migrate and verify the uploaded database, then back up current data before replacement.</p><dl class="meta-grid"><div><dt>Sample snapshot</dt><dd>Today · 09:30</dd></div><div><dt>Integrity</dt><dd>Verified</dd></div><div><dt>Migration</dt><dd>Compatible pair schema</dd></div><div><dt>Safety backup</dt><dd>Required before applying</dd></div></dl><div class="dialog-actions">${button("Cancel", "close-dialog")}${button("Simulate restore", "job", 'data-job="Staged database restore"', "danger")}</div>`,
    ),
  "export-library": () =>
    exportSample("prompt-manager-sample-recipes.json", {
      version: 1,
      sample: true,
      prompts,
    }),
  import: () =>
    dialog(
      "Import & inspect",
      `<p class="small">Image inspection, output scanning and recipe import have different effects. Choose the workflow you want.</p><div class="operation-list">${operation("Inspect a local image", "Read embedded metadata before deciding to add it.", "inspect-file", "Open inspector")}${operation("Scan an output folder", "Discover and enrich existing files using the configured sources.", "scan-review", "Review scan")}${operation("Import portable recipes", "Proposed: preview conflicts and reuse existing text before applying.", "sample-import", "Preview sample")}</div>`,
    ),
  "inspect-file": () => {
    closeDialog();
    location.hash = "metadata";
  },
  "sample-import": () =>
    dialog(
      "Recipe import preview · proposed",
      `<p class="demo-note">Sample JSON import proposal. Current production JSON export does not yet imply JSON import.</p><div class="meta-grid"><div><dt>New positives</dt><dd>2</dd></div><div><dt>Reused text</dt><dd>4 components</dd></div><div><dt>New negatives</dt><dd>3</dd></div><div><dt>Metadata conflicts</dt><dd>1 · review required</dd></div></div><p class="small">Existing metadata wins by default. Import should be transactional and repeatable.</p><div class="dialog-actions">${button("Close preview", "close-dialog")}</div>`,
    ),
  "add-source": () =>
    simpleInput(
      "Add image source",
      "Folder path (sample only)",
      "source-confirm",
      "/images/new-collection",
    ),
  "source-confirm": () => {
    const path = $("action-input").value;
    closeDialog();
    notify(
      `Demo source “${path}” reviewed. No filesystem access was requested.`,
    );
  },
  "tag-review": () => tagReview(state.selectedImage),
  "generate-tags": generateTags,
  "skip-tag-review": () => {
    closeDialog();
    notify("Sample suggestions skipped.");
  },
  "apply-tags": () => {
    if (!tagSession) return;
    const target = image(tagSession.imageId);
    const manual = $("manual-image-tags")
      .value.split(",")
      .map((t) => t.trim())
      .filter(Boolean);
    const approved = [
      ...document.querySelectorAll("[data-tag-suggestion]:checked"),
    ].map((input) => input.dataset.tagSuggestion);
    const merged = new Map(
      [...manual, ...approved].map((t) => [t.toLowerCase(), t]),
    );
    target.tags = [...merged.values()];
    const source = tagSession.source;
    closeDialog();
    nav();
    if (state.page === "tags") renderTags();
    else if (state.page === "review") renderReview();
    else if ($("image-inspector")) updateImages();
    if ($("viewer-dialog").open && viewImage?.id === target.id)
      $("viewer-details").innerHTML = imageDetails(target, true);
    const trigger =
      document.querySelector(
        `[data-action="image-tag"][data-id="${target.id}"]`,
      ) || document.querySelector('[data-action="tag-review"]');
    trigger?.focus();
    notify(
      `${target.tags.length} tags saved for ${target.filename} in this tab.${source === "review" ? " Prompt association still awaits review." : ""}`,
    );
  },
  "new-tag": () => simpleInput("Create tag", "Tag name", "new-tag-confirm"),
  "new-tag-confirm": () => {
    const tag = $("action-input").value.trim();
    if (tag) prompts[0].tags.push(tag);
    closeDialog();
    renderTags();
    notify("Sample vocabulary updated.");
  },
  "rename-tag": (b) => {
    state.renameTag = b.dataset.value;
    simpleInput(
      "Rename or merge tag",
      "New name; existing names merge",
      "rename-tag-confirm",
      b.dataset.value,
    );
  },
  "rename-tag-confirm": () => {
    const tag = $("action-input").value.trim();
    if (!tag) return;
    prompts.forEach(
      (p) =>
        (p.tags = [
          ...new Set(p.tags.map((t) => (t === state.renameTag ? tag : t))),
        ]),
    );
    images.forEach(
      (i) =>
        (i.tags = [
          ...new Set(i.tags.map((t) => (t === state.renameTag ? tag : t))),
        ]),
    );
    closeDialog();
    renderTags();
    notify("Sample tag renamed or merged without duplicate links.");
  },
  "manage-category": () =>
    simpleInput(
      "Manage categories",
      "Proposed new category name",
      "category-confirm",
    ),
  "category-confirm": () => {
    closeDialog();
    notify("Category management plan previewed; no stored categories changed.");
  },
  "resolve-review": () => {
    notify(
      "Sample association accepted. Production would save an explicit, auditable link.",
    );
    location.hash = "images";
  },
  stats: () =>
    dialog(
      "Usage statistics",
      `<p class="demo-note">Sample usage breakdown. Real history would use persisted run records.</p><table><caption>Runs by category</caption><thead><tr><th scope="col">Category</th><th scope="col">Runs</th><th scope="col">Avg. rating</th></tr></thead><tbody>${[...new Set(prompts.map((p) => p.category))].map((c) => `<tr><td>${c}</td><td>${prompts.filter((p) => p.category === c).length * 4}</td><td>4.5 / 5</td></tr>`).join("")}</tbody></table>`,
    ),
  "diagnostic-export": () =>
    exportSample("sample-diagnostics.json", {
      sample: true,
      database: "healthy",
      viewer: "1.15.2",
      sources: 2,
    }),
  "refresh-logs": () => notify("Sample logs refreshed."),
  "download-logs": () =>
    exportSample(
      "sample-prompt-manager.log",
      $("log-lines").textContent,
      "text/plain",
    ),
  "download-workflow": (b) => {
    const i = image(b.dataset.id),
      p = prompt(i.prompt);
    exportSample("sample-workflow.json", {
      sample: true,
      seed: i.seed,
      positive: p.positive,
      negative: p.negatives[i.variant].text,
    });
  },
  "viewer-prev": () =>
    openViewer(
      images[(images.indexOf(viewImage) + images.length - 1) % images.length]
        .id,
    ),
  "viewer-next": () =>
    openViewer(images[(images.indexOf(viewImage) + 1) % images.length].id),
  "viewer-zoom-in": () => viewer?.zoom(0.2),
  "viewer-zoom-out": () => viewer?.zoom(-0.2),
  "viewer-fit": () => viewer?.reset(),
  "viewer-rotate": () => viewer?.rotate(90),
  "viewer-flip": () => viewer?.scaleX(viewer.imageData.scaleX === 1 ? -1 : 1),
  "viewer-play": (b) => {
    if (slideTimer) {
      clearInterval(slideTimer);
      slideTimer = null;
      b.textContent = "Play";
    } else {
      slideTimer = setInterval(() => actions["viewer-next"](), 3500);
      b.textContent = "Pause";
    }
  },
  "viewer-download": () =>
    exportSample(
      "sample-illustration.svg",
      decodeURIComponent(
        art(prompt(viewImage.prompt), viewImage.index, viewImage).split(",")[1],
      ),
      "image/svg+xml",
    ),
};
scope.listen(document, "click", (event) => {
  const b = event.target.closest("[data-action]");
  if (b) actions[b.dataset.action]?.(b);
});
scope.listen(document, "input", (event) => {
  if (event.target.id === "search") {
    state.query = event.target.value;
    if (
      ["images", "favorites"].includes(state.page) ||
      state.page.startsWith("collection-") ||
      state.page.startsWith("saved-")
    )
      updateImages();
    else if (state.page === "library") {
      const caret = event.target.selectionStart;
      renderLibrary();
      $("search").focus();
      $("search").setSelectionRange(caret, caret);
    }
  }
});
scope.listen(document, "change", (event) => {
  const target = event.target;
  if (target.id === "image-tag-model") renderTagModelOptions();
  if (target.id === "category-filter") {
    state.category = target.value;
    if (state.page === "library") renderLibrary();
    else updateImages();
  }
  if (target.id === "image-size") {
    state.size = target.value;
    updateImages();
  }
  if (target.id === "sort") {
    state.sort = target.value;
    if (state.page === "library") renderLibrary();
    else updateImages();
  }
  if (target.dataset.select) {
    target.checked
      ? state.selection.add(target.dataset.select)
      : state.selection.delete(target.dataset.select);
    updateBulk();
  }
  if (target.dataset.setting) {
    config[target.dataset.setting] =
      target.type === "checkbox" ? target.checked : target.value;
    state.settingsDirty = true;
    $("settings-status").textContent = "Unsaved changes in this prototype";
  }
  if (target.id === "log-level")
    $("log-lines").textContent =
      target.value === "WARNING"
        ? "09:42:18 WARNING 3 images have ambiguous prompt metadata"
        : "09:42:18 INFO Scan completed: 12 enriched, 0 duplicates\n09:42:18 WARNING 3 images have ambiguous prompt metadata";
  if (target.id === "local-file") {
    const file = target.files[0];
    if (!file) return;
    const url = URL.createObjectURL(file),
      img = document.createElement("img");
    img.src = url;
    img.alt = "Local file preview";
    img.className = "local-file-preview";
    img.addEventListener("load", () => URL.revokeObjectURL(url), {
      once: true,
    });
    $("local-preview").replaceChildren(img);
    notify(
      "Local preview only. No file uploaded; sample metadata is available above.",
    );
  }
});
scope.listen($("modal-close"), "click", closeDialog);
scope.listen($("modal"), "close", () => {
  if ($("gallery-continuation")) updateImageContinuation();
});
scope.listen($("viewer-close"), "click", () => $("viewer-dialog").close());
scope.listen($("viewer-dialog"), "close", () => {
  clearInterval(slideTimer);
  slideTimer = null;
  viewer?.destroy();
  viewer = null;
  if ($("image-inspector")) updateImages();
});
scope.listen($("activity-open"), "click", () => {
  location.hash = "activity";
});
scope.listen($("review-link"), "click", () => {
  location.hash = "design";
});
scope.listen(document.querySelector(".canvas-link"), "click", (event) => {
  event.preventDefault();
  openSend(
    prompt(image(state.selectedImage).prompt),
    "pair",
    image(state.selectedImage).variant,
  );
});
function setTheme() {
  const dark =
    $("theme").value === "system"
      ? matchMedia("(prefers-color-scheme: dark)").matches
      : $("theme").value === "dark";
  document.body.style.setProperty("--bg-color", dark ? "#191a1d" : "#f3f4f6");
}
scope.listen($("theme"), "change", setTheme);
scope.listen(matchMedia("(prefers-color-scheme: dark)"), "change", setTheme);
scope.listen(window, "hashchange", navigate);
scope.listen(window, "pagehide", (event) => {
  if (event.persisted) return;
  stopImageScroll();
  stopMasonry();
  viewer?.destroy();
  clearInterval(slideTimer);
  clearTimeout(toastTimer);
  scope.dispose();
});
setTheme();
navigate();
