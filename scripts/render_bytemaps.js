// Execute each anatomy page's byte-map script and check the maps actually draw.
//
// check_bytemaps.py reads the build() calls with a regex, which proves the data
// is well formed and nothing else. Nine pages once passed it while every new map
// sat in the theme toggle's IIFE, where build() is not in scope -- valid data,
// blank page. This runs the script instead, against a stub DOM, so a map that
// cannot render is a failure rather than a clean report.
//
//   node scripts/render_bytemaps.js [dir]     (default docs/formats)

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const dir = process.argv[2] || "docs/formats";

function elem(tag) {
  const e = {
    tagName: tag, children: [], dataset: {}, style: {}, classList: { add() {}, remove() {}, toggle() {} },
    className: "", textContent: "", innerHTML: "", title: "",
    appendChild(c) { this.children.push(c); return c; },
    setAttribute() {}, getAttribute() { return null; }, removeAttribute() {},
    addEventListener() {}, removeEventListener() {},
    querySelector() { return elem("div"); }, querySelectorAll() { return []; },
    getBoundingClientRect() { return { width: 0, height: 0, top: 0, left: 0 }; },
    closest() { return null; }, focus() {}, remove() {},
  };
  Object.defineProperty(e, "offsetHeight", { get: () => 0 });
  return e;
}

let failures = 0;
const pages = fs.readdirSync(dir).filter((f) => f.endsWith(".html") && f !== "index.html").sort();

for (const name of pages) {
  const html = fs.readFileSync(path.join(dir, name), "utf8");
  const blocks = html.match(/<script\b[^>]*>[\s\S]*?<\/script>/g) || [];
  const home = blocks.find((b) => /function build\b/.test(b));
  if (!home) continue;

  const mounts = new Map();                       // id -> element, as the page declares them
  for (const m of html.matchAll(/id="([^"]+)"/g)) mounts.set(m[1], elem("div"));

  const drawn = new Set();
  const doc = {
    getElementById(id) { return mounts.get(id) || null; },
    createElement: elem,
    createTextNode: (t) => ({ textContent: t }),
    // permissive for page chrome; getElementById stays strict so a missing
    // mount is still a failure rather than a silently created element
    querySelector() { return elem("div"); }, querySelectorAll() { return []; },
    addEventListener() {}, documentElement: elem("html"), body: elem("body"),
  };
  const sandbox = {
    document: doc, console: { log() {}, warn() {}, error() {} },
    window: { matchMedia: () => ({ matches: false, addEventListener() {} }), addEventListener() {} },
    localStorage: { getItem: () => null, setItem() {} },
    customElements: { get: () => undefined, define() {} },
    HTMLElement: class {}, CustomEvent: class {},
    requestAnimationFrame: (f) => f(), setTimeout: (f) => f(),
    location: { href: "file:///page.html", hash: "", search: "" },
    navigator: { userAgent: "node" }, matchMedia: () => ({ matches: false, addEventListener() {} }),
  };
  sandbox.globalThis = sandbox;

  let code = home.replace(/^<script[^>]*>/, "").replace(/<\/script>$/, "");
  // Expectations come from the WHOLE page, not just the block being run. A call
  // that drifted into another script block would otherwise vanish from both the
  // numerator and the denominator and report a clean 0/0.
  const expected = [...html.matchAll(/\bbuild\(\s*["']([^"']+)["']/g)].map((m) => m[1]);

  // Some maps are built lazily, when a tab is first shown. No click happens
  // here, so call those builders directly -- otherwise a working page reads as
  // a failure, which is the same false-confidence bug pointing the other way.
  const deferred = [...code.matchAll(/function\s+(\w+)\s*\([^)]*\)\s*\{/g)]
    .filter((m) => m[1] !== "build" &&
                   /\bbuild\(/.test(code.slice(m.index, code.indexOf("\n  }", m.index) + 1)))
    .map((m) => m[1]);
  // The other page style is a `var SPECS={mount:[unit,bytes,fields]}` table
  // drawn per panel on first show. Same problem, same answer: drive it directly
  // rather than report a page full of maps as 0/0 and call that a pass.
  const specKeys = [];
  const si = code.indexOf("var SPECS=");
  if (si >= 0) {
    const table = code.slice(si, code.indexOf("\n  }", si));
    for (const m of table.matchAll(/^\s{4}["']([\w-]+)["']\s*:\s*\[/gm)) specKeys.push(m[1]);
  }
  expected.push(...specKeys);

  const tail = code.lastIndexOf("})();");
  if (tail > 0) {
    let inject = deferred.map((n) => `try{${n}()}catch(e){}`).join("");
    if (specKeys.length) {
      inject += "Object.keys(SPECS).forEach(function(k){" +
                "build(k,SPECS[k][0],SPECS[k][1],SPECS[k][2]);});";
    }
    if (inject) code = code.slice(0, tail) + "\n" + inject + "\n" + code.slice(tail);
  }

  try {
    vm.createContext(sandbox);
    new vm.Script(code, { filename: name }).runInContext(sandbox, { timeout: 10000 });
  } catch (e) {
    console.log(`${name}: script threw -- ${e.message}`);
    failures++;
    continue;
  }

  for (const id of expected) {
    const el = mounts.get(id);
    if (!el) { console.log(`${name}: build("${id}") has no mount div`); failures++; }
    else if (el.children.length === 0) { console.log(`${name}: map "${id}" rendered nothing`); failures++; }
    else drawn.add(id);
  }
  console.log(`  ${name}: ${drawn.size}/${expected.length} maps drew`);
}

console.log(failures ? `\n${failures} RENDER FAILURE(S)` : "\nevery map rendered");
process.exit(failures ? 1 : 0);
