#!/usr/bin/env node
import { createHash } from 'node:crypto';
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import { compressPNG } from './macos_ui_png.mjs';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const output = resolve(process.argv[2] ?? join(root, 'docs/ui/macos/current'));
const app = resolve(process.env.FIRESKY_NATIVE_APP ?? join(root, 'dist/native-macos/FireSky.app'));
const build = mkdtempSync(join(tmpdir(), 'firesky-macos-gallery-'));
const executable = join(build, 'FireSkyGallery.app/Contents/MacOS/FireSkyGallery');
const env = { ...process.env, DEVELOPER_DIR: process.env.DEVELOPER_DIR ?? '/Applications/Xcode.app/Contents/Developer' };
function run(command, args, options = {}) {
    const result = spawnSync(command, args, { cwd: root, env, encoding: 'utf8', timeout: 600_000, ...options });
    if (result.error || result.status !== 0) {
        throw new Error(`${command}: ${result.error ?? result.stderr ?? result.stdout}`);
    }
    return result.stdout.trim();
}
for (const file of ['Contents/Frameworks/libfiresky_engine.dylib', 'Contents/Resources/app-data']) {
    if (!existsSync(join(app, file))) throw new Error(`Missing runtime: ${join(app, file)}`);
}
mkdirSync(dirname(executable), { recursive: true });
mkdirSync(output, { recursive: true });
const sources = run('rg', ['--files', 'apps/macos/Sources/FireSkyNative', '-g', '*.swift', '-g', '!FireSkyApp.swift'])
    .split('\n').sort();
sources.push('apps/macos/Tests/TaskHistoryChecks.swift', 'apps/macos/Tests/WorkbenchSnapshot.swift');
const sourceHash = createHash('sha256');
for (const source of sources) sourceHash.update(source).update(readFileSync(join(root, source)));
console.log('Compiling macOS screenshot fixture...');
run('xcrun', ['swiftc', '-parse-as-library', '-swift-version', '5', '-target',
    `${run('uname', ['-m'])}-apple-macos14.0`, ...sources, '-o', executable]);
writeFileSync(join(build, 'FireSkyGallery.app/Contents/Info.plist'), `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict><key>CFBundleIdentifier</key><string>com.firesky.ui.gallery</string>
<key>CFBundleName</key><string>FireSky UI Gallery</string><key>CFBundleExecutable</key><string>FireSkyGallery</string>
<key>CFBundlePackageType</key><string>APPL</string><key>NSHighResolutionCapable</key><true/></dict></plist>`);
cpSync(join(app, 'Contents/Resources'), join(build, 'FireSkyGallery.app/Contents/Resources'), { recursive: true });
cpSync(join(app, 'Contents/Frameworks'), join(build, 'FireSkyGallery.app/Contents/Frameworks'), { recursive: true });
run('codesign', ['--force', '--sign', '-', join(build, 'FireSkyGallery.app')]);
const scenarios = [
    ['overview', {}],
    ['profile', { TAB: '大气剖面' }],
    ['diagnostics', { TAB: '诊断与时间窗' }],
    ['ledger', { TAB: '逐时台账' }],
    ['regional', { REGIONAL: '1' }],
    ['nowcast', { NOWCAST: '1', NOWCAST_MAP_UNAVAILABLE: '1' }],
    ['settings-general', { SETTINGS: '1' }],
    ['settings-data', { SETTINGS: '1', SETTINGS_CATEGORY: 'data' }],
    ['settings-advanced', { SETTINGS: '1', SETTINGS_CATEGORY: 'advanced' }],
    ['update-sheet', { SETTINGS: '1', UPDATE_SHEET: '1' }],
    ['inventory', { PAGE: 'data' }],
    ['history-empty', { PAGE: 'history' }],
    ['first-run', { EMPTY: '1' }],
    ['partial', { PARTIAL: '1', TAB: '大气剖面' }],
    ['stale', { STALE: '1', TAB: '大气剖面' }],
];
const entries = [];
for (const [screen, flags] of scenarios) {
    for (const theme of ['light', 'dark']) {
        const variants = screen === 'profile' || screen === 'overview' ? [1440, 1120] : [1440];
        for (const width of variants) {
            const height = width === 1120 ? 720 : 900;
            const filename = `${screen}-${theme}-${width}.png`;
            const workspace = join(build, `${screen}-${theme}-${width}`);
            mkdirSync(workspace);
            const captureEnv = {
                ...env, FIRESKY_WORKSPACE: workspace, FIRESKY_NATIVE_APPEARANCE: theme,
                FIRESKY_ENGINE_FFI_LIBRARY: join(app, 'Contents/Frameworks/libfiresky_engine.dylib'),
                FIRESKY_RESOURCE_DIR: join(app, 'Contents/Resources/app-data'), FIRESKY_REQUIRE_APP_RESOURCES: '1',
                FIRESKY_NATIVE_WIDTH: String(width), FIRESKY_NATIVE_HEIGHT: String(height),
                FIRESKY_NATIVE_SNAPSHOT: join(output, filename), FIRESKY_TEST_CAPTURE_DELAY: '2',
                FIRESKY_TEST_REQUIRE_ENGINE: '1',
                ...Object.fromEntries(Object.entries(flags).map(([key, value]) => [`FIRESKY_TEST_${key}`, value])),
            };
            console.log(`Capturing ${filename}`);
            const log = run('/usr/bin/sandbox-exec', ['-p', '(version 1)(allow default)(deny network*)', executable],
                { env: captureEnv, timeout: 60_000 });
            writeFileSync(join(build, `${filename}.log`), log);
            const png = compressPNG(readFileSync(join(output, filename)));
            writeFileSync(join(output, filename), png);
            entries.push({ screen, theme, viewport: [width, height], file: filename,
                pixels: [png.readUInt32BE(16), png.readUInt32BE(20)],
                data: 'synthetic-fixture-network-denied', flags,
                sha256: createHash('sha256').update(png).digest('hex') });
        }
    }
}
// Hash inputs as well as HEAD because a reference gallery may be captured from a dirty worktree.
writeFileSync(join(output, 'manifest.json'), JSON.stringify({
    capturedAt: new Date().toISOString(), commit: run('git', ['rev-parse', 'HEAD']),
    sourceHash: sourceHash.digest('hex'), runtime: app, platform: run('sw_vers', ['-productVersion']),
    acceptance: 'visual-fixture-only', entries,
}, null, 2) + '\n');
console.log(`Gallery: ${output}\nFixture app and logs: ${build}`);
