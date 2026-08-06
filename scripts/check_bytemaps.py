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
PAGES = sorted(pathlib.Path("docs/formats").glob("*.html"))


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
        for m in re.finditer(r'"([^"]+)"\s*:\s*\[\s*"(\w+)"\s*,', table):
            arr1 = table.index("[", m.end() - 1)
            e1 = span(table, arr1, "[", "]")
            arr2 = table.index("[", e1)
            e2 = span(table, arr2, "[", "]")
            maps.append((p.name, m.group(1), m.group(2),
                         table[arr1:e1], table[arr2:e2]))

    # style B: build("mount","unit",[bytes],[fields])
    for m in re.finditer(r'build\(\s*"([^"]+)"\s*,\s*"(\w+)"\s*,', s):
        arr1 = s.index("[", m.end() - 1)
        e1 = span(s, arr1, "[", "]")
        arr2 = s.index("[", e1)
        e2 = span(s, arr2, "[", "]")
        maps.append((p.name, m.group(1), m.group(2), s[arr1:e1], s[arr2:e2]))

print(f"{len(maps)} byte maps across {len(PAGES)} pages")

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
