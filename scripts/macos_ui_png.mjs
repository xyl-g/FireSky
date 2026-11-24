import { crc32, deflateSync, inflateSync } from 'node:zlib';

// Recompress the PNG data stream without decoding or changing any pixels/metadata.
export function compressPNG(png) {
    if (!png.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))) {
        throw new Error('Invalid PNG signature');
    }
    const chunks = [];
    for (let offset = 8; offset < png.length;) {
        const length = png.readUInt32BE(offset);
        const chunk = png.subarray(offset, offset + length + 12);
        if (chunk.length !== length + 12) throw new Error('Truncated PNG chunk');
        chunks.push({ type: chunk.toString('ascii', 4, 8), bytes: chunk });
        offset += chunk.length;
    }
    if (chunks.some(chunk => chunk.type === 'acTL')) return png;
    const dataChunks = chunks.filter(chunk => chunk.type === 'IDAT');
    const raw = inflateSync(Buffer.concat(dataChunks.map(chunk => chunk.bytes.subarray(8, -4))));
    const compressed = deflateSync(raw, { level: 9 });
    if (!inflateSync(compressed).equals(raw)) throw new Error('PNG data changed during compression');
    const replacement = Buffer.alloc(compressed.length + 12);
    replacement.writeUInt32BE(compressed.length);
    replacement.write('IDAT', 4, 'ascii');
    compressed.copy(replacement, 8);
    replacement.writeUInt32BE(crc32(replacement.subarray(4, -4)), replacement.length - 4);
    let inserted = false;
    const output = Buffer.concat([png.subarray(0, 8), ...chunks.flatMap(chunk => {
        if (chunk.type !== 'IDAT') return [chunk.bytes];
        if (inserted) return [];
        inserted = true;
        return [replacement];
    })]);
    return output.length < png.length ? output : png;
}
