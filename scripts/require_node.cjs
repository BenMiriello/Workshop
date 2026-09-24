// Stop a build, typecheck or test run on a node older than the floor the root
// package.json declares in `engines.node`, and say so in one line.
//
// npm does not enforce `engines` for `npm run`, and an older node does not fail
// here, it fails later and obscurely: an optional dependency silently skipped at
// install, a dependency that refuses to start, a syntax error deep in a tool.
// Kept to syntax any node can parse so it runs on exactly the versions it
// exists to refuse.
var path = require("path");
var want = require(path.join(__dirname, "..", "package.json")).engines.node;
var floor = parseInt(String(want).replace(/^[^0-9]*/, ""), 10);
var have = parseInt(process.versions.node.split(".")[0], 10);
if (have < floor) {
  console.error("Workshop needs node " + want + " (found " + process.version +
    "). See .nvmrc; with nvm: `nvm install && nvm use`.");
  process.exit(1);
}
