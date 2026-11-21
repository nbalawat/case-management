# Pages, one file each

Each `*.tsx` file here default-exports a `Module` (or an array of them) from `../shell/registry`. The registry
finds them at build time, so adding a page never edits a shared list. See the comment at the top of
`src/shell/registry.tsx` for an example. `src/shell/menu.test.ts` fails on a repeated id or path.
