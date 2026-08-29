<!-- author: self -->
`frontend/src/pages/ProjectDetail.tsx` contained two literal NUL bytes in template-literal map keys, so grep, ripgrep and every text-search tool treated the 72KB file as binary and **skipped it silently**. No warning, no error: searches simply returned nothing from it.

This produced a wrong conclusion twice. On 2026-08-15 two independent agents grepped for `AutonomyPanel`, found only its own definition, and concluded it was dead code with zero importers. It is imported and rendered by `ProjectDetail.tsx:64,1004`. A typecheck caught the deletion; a grep never would have.

Commit `398948e` fixed an earlier instance of the same defect in a different file, so this recurs.

**How to apply:** before concluding that a symbol is unused from a grep, confirm the search actually covered the tree. `grep -rIl '' frontend/src | wc -l` versus the real file count, or `file frontend/src/**/*.tsx | grep -v text`, will name any file being skipped. Write NULs as `\0` escapes; the string is identical and the file stays searchable. Treat 'zero importers' from grep alone as unproven for a deletion.