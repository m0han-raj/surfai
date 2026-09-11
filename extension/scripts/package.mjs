/**
 * Package `dist/` into a versioned zip for distribution.
 *
 * Writes the archive itself rather than shelling out to `zip`, which does not
 * exist on a default Windows install and would make the command unreliable on
 * exactly the machine most likely to run it.
 *
 * Entries are stored uncompressed-or-deflated per file and sorted by path, and
 * every timestamp is pinned, so the same `dist/` always produces a
 * byte-identical archive. That makes the build verifiable.
 *
 *     npm run package
 */

import { createHash } from 'node:crypto';
import { deflateRawSync } from 'node:zlib';
import { readFileSync, readdirSync, statSync, writeFileSync, mkdirSync } from 'node:fs';
import { join, relative, resolve, sep } from 'node:path';

const root = resolve(import.meta.dirname, '..');
const dist = join(root, 'dist');
const outDir = join(root, 'build');

/** A fixed DOS timestamp (1980-01-01), so archives are reproducible. */
const DOS_TIME = 0;
const DOS_DATE = 33;

function walk(dir) {
  const found = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      found.push(...walk(full));
    } else {
      found.push(full);
    }
  }
  return found;
}

function crc32(buffer) {
  let table = crc32.table;
  if (!table) {
    table = crc32.table = new Int32Array(256);
    for (let i = 0; i < 256; i++) {
      let c = i;
      for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      table[i] = c;
    }
  }
  let crc = -1;
  for (let i = 0; i < buffer.length; i++) {
    crc = (crc >>> 8) ^ table[(crc ^ buffer[i]) & 0xff];
  }
  return (crc ^ -1) >>> 0;
}

function zip(files) {
  const chunks = [];
  const central = [];
  let offset = 0;

  for (const { name, data } of files) {
    const nameBytes = Buffer.from(name, 'utf8');
    const compressed = deflateRawSync(data, { level: 9 });
    // Store whichever is smaller; a already-compressed PNG can inflate.
    const useDeflate = compressed.length < data.length;
    const payload = useDeflate ? compressed : data;
    const method = useDeflate ? 8 : 0;
    const sum = crc32(data);

    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4); // version needed
    local.writeUInt16LE(0, 6); // flags
    local.writeUInt16LE(method, 8);
    local.writeUInt16LE(DOS_TIME, 10);
    local.writeUInt16LE(DOS_DATE, 12);
    local.writeUInt32LE(sum, 14);
    local.writeUInt32LE(payload.length, 18);
    local.writeUInt32LE(data.length, 22);
    local.writeUInt16LE(nameBytes.length, 26);
    local.writeUInt16LE(0, 28);

    chunks.push(local, nameBytes, payload);

    const entry = Buffer.alloc(46);
    entry.writeUInt32LE(0x02014b50, 0);
    entry.writeUInt16LE(20, 4); // version made by
    entry.writeUInt16LE(20, 6); // version needed
    entry.writeUInt16LE(0, 8);
    entry.writeUInt16LE(method, 10);
    entry.writeUInt16LE(DOS_TIME, 12);
    entry.writeUInt16LE(DOS_DATE, 14);
    entry.writeUInt32LE(sum, 16);
    entry.writeUInt32LE(payload.length, 20);
    entry.writeUInt32LE(data.length, 24);
    entry.writeUInt16LE(nameBytes.length, 28);
    entry.writeUInt16LE(0, 30); // extra
    entry.writeUInt16LE(0, 32); // comment
    entry.writeUInt16LE(0, 34); // disk
    entry.writeUInt16LE(0, 36); // internal attrs
    entry.writeUInt32LE(0, 38); // external attrs
    entry.writeUInt32LE(offset, 42);

    central.push(entry, nameBytes);
    offset += local.length + nameBytes.length + payload.length;
  }

  const centralBuffer = Buffer.concat(central);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(0, 4);
  end.writeUInt16LE(0, 6);
  end.writeUInt16LE(files.length, 8);
  end.writeUInt16LE(files.length, 10);
  end.writeUInt32LE(centralBuffer.length, 12);
  end.writeUInt32LE(offset, 16);
  end.writeUInt16LE(0, 20);

  return Buffer.concat([...chunks, centralBuffer, end]);
}

function main() {
  let manifest;
  try {
    manifest = JSON.parse(readFileSync(join(dist, 'manifest.json'), 'utf8'));
  } catch {
    console.error('No dist/manifest.json. Run `npm run build` first.');
    process.exit(1);
  }

  const files = walk(dist)
    // Source maps are development aids; they roughly double the archive and
    // expose the full source tree to anyone who downloads it.
    .filter((path) => !path.endsWith('.map'))
    .map((path) => ({
      name: relative(dist, path).split(sep).join('/'),
      data: readFileSync(path),
    }))
    .sort((a, b) => (a.name < b.name ? -1 : 1));

  const archive = zip(files);
  mkdirSync(outDir, { recursive: true });

  const name = `surfai-${manifest.version}.zip`;
  const target = join(outDir, name);
  writeFileSync(target, archive);

  const digest = createHash('sha256').update(archive).digest('hex');

  console.log(`  ${name}`);
  console.log(`  ${files.length} files, ${(archive.length / 1024).toFixed(1)} kB`);
  console.log(`  sha256 ${digest}`);
  console.log(`  -> build/${name}`);

  // A zip with no manifest at its root will be rejected on upload, and the
  // failure message there is unhelpful, so check it here instead.
  if (!files.some((f) => f.name === 'manifest.json')) {
    console.error('  manifest.json is not at the archive root');
    process.exit(1);
  }
}

main();
