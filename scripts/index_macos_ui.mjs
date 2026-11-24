#!/usr/bin/env node
import { createHash } from 'node:crypto';
import { readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { compressPNG } from './macos_ui_png.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../docs/ui/macos');
const optimize = process.argv.includes('--compress');
const entries = [];
function scan(directory) {
    for (const item of readdirSync(directory, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
        const path = join(directory, item.name);
        if (item.isDirectory()) scan(path);
        else if (item.name.endsWith('.png')) {
            const file = relative(root, path).split('\\').join('/');
            const original = readFileSync(path);
            const png = optimize ? compressPNG(original) : original;
            if (png.length < original.length) writeFileSync(path, png);
            const group = file.split('/')[0];
            entries.push({ file, group, title: file.replace(/\.png$/, ''),
                theme: /(?:^|[/-])dark(?:[.-]|$)/.test(file) ? 'dark' :
                    /(?:^|[/-])light(?:[.-]|$)/.test(file) ? 'light' : 'other',
                pixels: [png.readUInt32BE(16), png.readUInt32BE(20)],
                sha256: createHash('sha256').update(png).digest('hex') });
        }
    }
}
scan(root);
const catalog = { generatedAt: new Date().toISOString(), entries };
writeFileSync(join(root, 'catalog.json'), JSON.stringify(catalog, null, 2) + '\n');
writeFileSync(join(root, 'catalog.js'), `window.FireSkyGallery = ${JSON.stringify(catalog).replaceAll('<', '\\u003c')};\n`);
console.log(`Indexed ${entries.length} images in ${root}`);
