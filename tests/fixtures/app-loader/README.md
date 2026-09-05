# The app-loader fixture

A complete, fictional Workshop app whose only job is to be installed and removed
while nothing is rebuilt. `scripts/check_app_loader.sh` copies it into a
throwaway instance root, builds it with `@workshop/app-build`, adds it to that
instance's roster, and asserts the platform serves it; then removes it from the
roster and asserts the platform does not.

It is a fixture rather than one of the operator's apps on purpose: it is the one
app guaranteed NOT to be in the shell's build graph, so "it appeared" cannot be
the shell's own bundle answering.

It exercises, in one bundle: a page component that takes no props, a hook (so a
second React would throw), an import of the design kit and of the shell's
`@workshop/app-host` seam (so a missing import-map entry fails loudly), and one
CSS rule whose custom property is the browser-side proof that an app's own
stylesheet actually loaded.
