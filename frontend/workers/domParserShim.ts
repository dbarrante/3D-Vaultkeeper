// frontend/workers/domParserShim.ts
//
// three.js's ThreeMFLoader.parse() calls `new DOMParser().parseFromString(...)`
// unconditionally (node_modules/three/examples/jsm/loaders/3MFLoader.js:215,273),
// referencing the global `DOMParser` directly -- it takes no injectable parser.
// `DOMParser` is a Window-scoped DOM API, not part of the Worker global scope
// (WorkerGlobalScope has no DOM access at all), so this throws in any dedicated
// Worker, causing every .3mf file to fail to parse there (confirmed empirically
// against Chromium: `typeof DOMParser === 'undefined'` inside a plain
// `new Worker(...)`). This previously made 3MF hover-preview and thumbnail
// generation silently fail and fall back (see hoverPreview.integration_test.py's
// former "3MF is a KNOWN, CONFIRMED-BROKEN case" comment).
//
// @xmldom/xmldom provides a pure-JS (no native DOM dependency) DOMParser/Document/
// Element implementation of DOM Level 2 Core, which is real and Worker-safe --
// but it does NOT implement querySelector/querySelectorAll (that's the separate
// Selectors API spec, not part of DOM Level 2 Core), and ThreeMFLoader.parse()
// uses both (confirmed by reading every xmlData./relsXmlData./*Node.-prefixed
// call in 3MFLoader.js: .documentElement, .nodeName, .querySelector(...),
// .querySelectorAll(...), .getAttribute(...), .textContent -- all standard DOM
// Level 2 Core except the two query methods).
//
// Rather than depend on a third-party CSS-selector-engine package (tried
// xmldom-qsa first: it has a real bug in its own selector tokenizer that
// throws "undefined is not an object (evaluating 'c[++k]')" on this app's own
// 3MF test fixture -- a library defect, not a usage error, confirmed by
// isolating the failure to xmldom-qsa/lib/helper.js in a standalone repro
// outside any Worker), this hand-writes the same minimal shim the
// manyfold3d/threejs-webworker-3mf-loader fork uses (a purpose-built fork of
// this exact loader for this exact problem): every selector 3MFLoader.js
// actually uses is either a single tag name or a simple descendant chain of
// tag names (e.g. "vertices vertex", "triangles triangle") -- never a class,
// ID, or attribute selector -- so splitting on whitespace and walking each
// segment via the real, correctly-implemented DOM Level 2 Core method
// `getElementsByTagNameNS("*", tag)` covers the exact surface used, with no
// dependency on any selector-parsing engine at all.
import { DOMParser, Document, Element } from "@xmldom/xmldom";

function selectRecursive(tags: string[], nodearray: Element[]): Element[] {
  if (tags.length === 0 || nodearray.length === 0) {
    return nodearray;
  }
  const result = nodearray
    .map((element) => Array.from(element.getElementsByTagNameNS("*", tags[0])) as Element[])
    .flat(1);
  return selectRecursive(tags.slice(1), result);
}

function monkeyPatchQuerySelectors(proto: typeof Document.prototype | typeof Element.prototype) {
  (proto as unknown as { querySelectorAll: (selector: string) => Element[] }).querySelectorAll = function (
    selector: string,
  ) {
    return selectRecursive(selector.split(/ +/), [this as unknown as Element]);
  };
  (proto as unknown as { querySelector: (selector: string) => Element | undefined }).querySelector = function (
    selector: string,
  ) {
    return selectRecursive(selector.split(/ +/), [this as unknown as Element])[0];
  };
}

monkeyPatchQuerySelectors(Document.prototype);
monkeyPatchQuerySelectors(Element.prototype);

(globalThis as unknown as { DOMParser: unknown }).DOMParser = DOMParser;
