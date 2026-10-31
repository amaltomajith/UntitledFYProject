// Copies a chosen set of Material Icon Theme logos into public/file-icons and writes lib/fileIcons.json.
// Run by hand when the lists change (node scripts/file-icons.mjs). The output is committed, so a build needs no extra step.

import { copyFileSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const web = join(dirname(fileURLToPath(import.meta.url)), "..");
const manifest = createRequire(import.meta.url)("material-icon-theme/dist/material-icons.json");

const EXTENSIONS = `py pyi ipynb js mjs cjs ts mts cts d.ts jsx tsx json jsonc json5 md mdx html htm css scss sass less
  go rs java kt kts scala groovy c h cpp cc cxx hpp cs fs vb rb php pl lua r dart swift m zig ex exs erl hs clj elm nim
  sh bash zsh fish bat cmd ps1 psm1 yaml yml toml ini cfg conf xml xsl svg png jpg jpeg gif webp ico bmp mp4 webm mov
  mp3 wav ogg txt rst tex log csv tsv sql db sqlite lock zip tar gz rar 7z pdf doc docx xls xlsx ppt pptx vue svelte astro
  graphql gql proto tf wasm env key pem crt test.ts test.js test.tsx test.jsx spec.ts spec.js`.split(/\s+/);

const FILES = `package.json package-lock.json yarn.lock pnpm-lock.yaml tsconfig.json dockerfile docker-compose.yml
  docker-compose.yaml compose.yaml .dockerignore .gitignore .gitattributes .gitmodules .env .env.example .env.local
  .editorconfig .prettierrc .eslintrc.json eslint.config.js eslint.config.mjs readme.md license license.md changelog.md
  contributing.md claude.md makefile cmakelists.txt cargo.toml cargo.lock go.mod go.sum pyproject.toml requirements.txt
  poetry.lock uv.lock setup.py pipfile gemfile next.config.js next.config.ts next.config.mjs vite.config.ts vite.config.js
  tailwind.config.js tailwind.config.ts postcss.config.js postcss.config.mjs vercel.json favicon.ico`.split(/\s+/);

const FOLDERS = `src source lib libs test tests __tests__ spec docs doc public static assets images img icons fonts
  components ui pages views layouts app api routes server client services models schemas types utils helpers hooks
  styles css config configs scripts bin dist build out target node_modules vendor .git .github .vscode .claude
  .venv venv env __pycache__ .next migrations database db data templates locales i18n benchmarks examples logs tmp
  temp cache`.split(/\s+/);

const pick = (names, table) => {
  const out = {};
  for (const name of names) if (table[name]) out[name] = table[name];
  return out;
};

const map = {
  file: manifest.file,
  folder: manifest.folder,
  extensions: pick(EXTENSIONS, manifest.fileExtensions),
  names: pick(FILES, manifest.fileNames),
  folders: pick(FOLDERS, manifest.folderNames),
};

const needed = new Set([map.file, map.folder, ...Object.values(map.extensions), ...Object.values(map.names)]);
for (const icon of [map.folder, ...Object.values(map.folders)]) needed.add(icon).add(`${icon}-open`);

const out = join(web, "public", "file-icons");
const icons = join(web, "node_modules", "material-icon-theme", "icons");
rmSync(out, { recursive: true, force: true });
mkdirSync(out, { recursive: true });
for (const icon of needed) copyFileSync(join(icons, `${icon}.svg`), join(out, `${icon}.svg`));
copyFileSync(join(web, "node_modules", "material-icon-theme", "LICENSE"), join(out, "LICENSE.txt"));
writeFileSync(join(web, "lib", "fileIcons.json"), `${JSON.stringify(map)}\n`);
console.log(`${needed.size} icons, ${Object.keys(map.extensions).length} extensions`);
