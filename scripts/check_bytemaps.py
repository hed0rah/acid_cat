"""Validate every byte map in every anatomy page.

Two page styles exist: a lazy `var SPECS={mount:[unit,bytes,fields]}` table, and
direct `build("mount","unit",[bytes],[fields])` calls. Both end up in the same
renderer, so both get the same three checks:

  1. no field range extends past the byte array
  2. no byte is claimed by two fields (overlaps render wrong)
  3. no byte is unclaimed (gaps render as the previous field's colour)
"""
import json
import pathlib
import re
import subprocess
import tempfile
import sys

# the generated JS is a build artifact, so it goes to a temp dir rather
# than next to the script where it would land in the repo
_TMP = pathlib.Path(tempfile.mkdtemp(prefix="acidcat-bytemaps-"))
# defaults to the repo copy; pass a directory to check the published site,
# which is a separate copy and has drifted from this one before
_DIR = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "docs/formats")
# index.html is a listing page, not a datasheet -- it has no maps by design
PAGES = [p for p in sorted(_DIR.glob("*.html")) if p.name != "index.html"]


def span(text, start, open_ch, close_ch):
    """End index (exclusive) of the bracketed run opening at `start`."""
    depth, i, instr, esc = 0, start, None, False
    while i < len(text):
        c = text[i]
        if instr:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == instr:
                instr = None
        elif c in "\"'":
            instr = c
        elif c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise ValueError("unbalanced")


maps = []          # (page, mount, unit, bytes_src, fields_src)

for p in PAGES:
    s = p.read_text(encoding="utf-8", errors="replace")

    # style A: var SPECS={ "mount":[ "unit", [bytes], [fields] ], ... }
    i = s.find("var SPECS=")
    if i >= 0:
        j = s.index("{", i)
        table = s[j:span(s, j, "{", "}")]
        for m in re.finditer(r"""["']([^"']+)["']\s*:\s*\[\s*["'](\w+)["']\s*,""", table):
            arr1 = table.index("[", m.end() - 1)
            e1 = span(table, arr1, "[", "]")
            arr2 = table.index("[", e1)
            e2 = span(table, arr2, "[", "]")
            maps.append((p.name, m.group(1), m.group(2),
                         table[arr1:e1], table[arr2:e2]))

    # style B: build("mount", "unit", [bytes], [fields])
    #
    # Both quote styles, because they are BOTH in use and requiring double
    # quotes silently skipped ten pages while the run still said ALL CLEAN.
    # A filter that produces a confident negative is the bug this script
    # exists to catch, so it had to stop doing it itself.
    for m in re.finditer(r"""build\(\s*["']([^"']+)["']\s*,\s*["'](\w+)["']\s*,""", s):
        arr1 = s.index("[", m.end() - 1)
        e1 = span(s, arr1, "[", "]")
        arr2 = s.index("[", e1)
        e2 = span(s, arr2, "[", "]")
        maps.append((p.name, m.group(1), m.group(2), s[arr1:e1], s[arr2:e2]))

seen = {m[0] for m in maps}
missing = [p.name for p in PAGES if p.name not in seen]
print(f"{len(maps)} byte maps across {len(PAGES)} pages")
if missing:
    print(f"NO MAPS EXTRACTED from {len(missing)} page(s): {missing}")
    print("  a page with maps that this script cannot see is the failure mode "
          "it exists to prevent -- check the call style before trusting a pass.")

# Reading a call is not the same as the call being able to run. Nine pages once
# passed every check below while every new map sat in the theme toggle's IIFE,
# where build() is not in scope -- valid data, dead page. So: the call must live
# in the script that defines build(), and its mount div must exist.
placement = []
for p in PAGES:
    s = p.read_text(encoding="utf-8", errors="replace")
    home = next((m for m in re.finditer(r"<script\b[^>]*>.*?</script>", s, re.S)
                 if re.search(r"function build\b", m.group(0))), None)
    calls = list(re.finditer(r"""\bbuild\(\s*["']([^"']+)["']""", s))
    if calls and home is None:
        placement.append(f"{p.name}: build() called but never defined")
        continue
    for m in calls:
        if not (home.start() <= m.start() < home.end()):
            placement.append(f"{p.name}: build(\"{m.group(1)}\") is outside "
                             f"the script that defines build()")
        if f'id="{m.group(1)}"' not in s:
            placement.append(f"{p.name}: build(\"{m.group(1)}\") has no "
                             f"matching mount div")
if placement:
    print(f"\n{len(placement)} PLACEMENT PROBLEM(S) -- these maps do not render:")
    for line in placement:
        print("   " + line)

js = ["var R=[];"]
for idx, (page, mount, unit, b, f) in enumerate(maps):
    js.append(f"R.push({{page:{json.dumps(page)},mount:{json.dumps(mount)},"
              f"unit:{json.dumps(unit)},bytes:{b},fields:{f}}});")
js.append(r"""
var out=[];
R.forEach(function(r){
  var N = r.unit==="bit" ? r.bytes.length*8 : r.bytes.length;
  var owned=new Array(N), probs=[];
  r.fields.forEach(function(f,fi){
    if(!f.r){probs.push("field "+fi+" ("+(f.label||"?")+") has no range");return;}
    if(f.r[1]>=N) probs.push("'"+(f.label||fi)+"' range ["+f.r+"] past end ("+N+")");
    for(var q=f.r[0];q<=f.r[1] && q<N;q++){
      if(owned[q]!==undefined)
        probs.push("unit "+q+" claimed by '"+(r.fields[owned[q]].label||owned[q])+"' and '"+(f.label||fi)+"'");
      owned[q]=fi;
    }
  });
  var gaps=[];
  for(var q=0;q<N;q++) if(owned[q]===undefined) gaps.push(q);
  if(gaps.length) probs.push("unclaimed units: "+(gaps.length>12?gaps.slice(0,12)+" ...("+gaps.length+")":gaps));
  if(probs.length) out.push({page:r.page,mount:r.mount,unit:r.unit,n:N,probs:probs});
});
if(!out.length) console.log("ALL CLEAN");
out.forEach(function(o){
  console.log("\n"+o.page+"  ["+o.mount+"]  "+o.n+" "+(o.unit==="bit"?"bits":"bytes"));
  o.probs.forEach(function(p){console.log("    "+p);});
});
console.log("\n"+out.length+" of "+R.length+" maps have problems");
""")
(_TMP / "bytemaps.js").write_text("\n".join(js), encoding="utf-8")
r = subprocess.run(["node", str(_TMP / "bytemaps.js")],
                   capture_output=True, text=True)
print(r.stdout or r.stderr[:3000])
