// SPDX-License-Identifier: Apache-2.0
// Snappy raw blocks and the framed stream, enough for GitHub attestation bundles.
// https://github.com/google/snappy/blob/main/format_description.txt
// https://github.com/google/snappy/blob/main/framing_format.txt

export const MAX_DECOMPRESSED = 16 * 1024 * 1024;

const CRC32C_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let index = 0; index < 256; index += 1) {
    let crc = index;
    for (let bit = 0; bit < 8; bit += 1) {
      crc = (crc & 1) === 1 ? (0x82f63b78 ^ (crc >>> 1)) : crc >>> 1;
    }
    table[index] = crc >>> 0;
  }
  return table;
})();

/** Castagnoli CRC32C. crc32c("123456789") is 0xe3069283. */
export function crc32c(buf) {
  let crc = 0xffffffff;
  for (let index = 0; index < buf.length; index += 1) {
    crc = CRC32C_TABLE[(crc ^ buf[index]) & 0xff] ^ (crc >>> 8);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

/** Snappy framing mask: ((crc >> 15) | (crc << 17)) + 0xa282ead8. */
export function maskCrc32c(crc) {
  return (((crc >>> 15) | (crc << 17)) + 0xa282ead8) >>> 0;
}

function requireBytes(buf, index, count, label) {
  if (index + count > buf.length) throw new Error(`snappy ${label} is truncated`);
}

function readVarint(buf, offset) {
  let result = 0;
  let shift = 0;
  let index = offset;
  while (shift <= 28) {
    requireBytes(buf, index, 1, 'varint');
    const byte = buf[index];
    index += 1;
    result |= (byte & 0x7f) << shift;
    if ((byte & 0x80) === 0) return [result >>> 0, index];
    shift += 7;
  }
  throw new Error('snappy varint is truncated');
}

function readUint32LE(buf) {
  requireBytes(buf, 0, 4, 'checksum');
  return (buf[0] | (buf[1] << 8) | (buf[2] << 16) | (buf[3] << 24)) >>> 0;
}

function assertChecksum(stored, data) {
  const expected = maskCrc32c(crc32c(data));
  if (stored !== expected) {
    throw new Error(
      `snappy frame checksum is wrong. expected ${expected.toString(16)}, found ${stored.toString(16)}`
    );
  }
}

export function decodeSnappyRaw(buf) {
  const [length, start] = readVarint(buf, 0);
  if (length > MAX_DECOMPRESSED) {
    throw new Error(`snappy uncompressed length ${length} exceeds ${MAX_DECOMPRESSED} bytes`);
  }
  const out = Buffer.alloc(length);
  let index = start;
  let written = 0;
  while (written < length) {
    if (index >= buf.length) {
      throw new Error(`snappy output is not filled. expected ${length} bytes, found ${written}`);
    }
    const tag = buf[index];
    index += 1;
    const kind = tag & 3;
    if (kind === 0) {
      let literal = tag >> 2;
      if (literal >= 60) {
        const extra = literal - 59;
        requireBytes(buf, index, extra, 'literal length');
        literal = 0;
        for (let part = 0; part < extra; part += 1) {
          literal |= buf[index] << (8 * part);
          index += 1;
        }
      }
      const size = (literal >>> 0) + 1;
      requireBytes(buf, index, size, 'literal');
      if (written + size > length) throw new Error('snappy literal overruns the declared length');
      buf.copy(out, written, index, index + size);
      index += size;
      written += size;
      continue;
    }
    let size;
    let offset;
    if (kind === 1) {
      size = ((tag >> 2) & 7) + 4;
      requireBytes(buf, index, 1, 'copy offset');
      offset = ((tag & 0xe0) << 3) | buf[index];
      index += 1;
    } else if (kind === 2) {
      size = (tag >> 2) + 1;
      requireBytes(buf, index, 2, 'copy offset');
      offset = buf[index] | (buf[index + 1] << 8);
      index += 2;
    } else {
      size = (tag >> 2) + 1;
      requireBytes(buf, index, 4, 'copy offset');
      offset = (buf[index] | (buf[index + 1] << 8) | (buf[index + 2] << 16) | (buf[index + 3] << 24)) >>> 0;
      index += 4;
    }
    if (offset <= 0 || offset > written) throw new Error('snappy copy offset is outside the output');
    if (written + size > length) throw new Error('snappy copy overruns the declared length');
    for (let count = 0; count < size; count += 1) {
      out[written] = out[written - offset];
      written += 1;
    }
  }
  if (written !== length) {
    throw new Error(`snappy output is not filled. expected ${length} bytes, found ${written}`);
  }
  return out;
}

export function decodeSnappyFrame(buf) {
  let index = 0;
  const parts = [];
  let produced = 0;
  let identified = false;
  const reserve = (bytes) => {
    if (bytes > MAX_DECOMPRESSED || produced + bytes > MAX_DECOMPRESSED) {
      throw new Error(`snappy decompressed total ${produced + bytes} exceeds ${MAX_DECOMPRESSED} bytes`);
    }
    produced += bytes;
  };
  while (index < buf.length) {
    requireBytes(buf, index, 4, 'frame chunk header');
    const type = buf[index];
    const size = buf[index + 1] | (buf[index + 2] << 8) | (buf[index + 3] << 16);
    requireBytes(buf, index + 4, size, 'frame chunk');
    const chunk = buf.subarray(index + 4, index + 4 + size);
    index += 4 + size;
    if (!identified && type !== 0xff) {
      throw new Error('snappy frame must start with the stream identifier');
    }
    if (type === 0xff) {
      if (chunk.toString('utf8') !== 'sNaPpY') throw new Error('snappy frame identifier is not sNaPpY');
      identified = true;
    } else if (type === 0x00) {
      requireBytes(chunk, 0, 4, 'frame chunk');
      const [declared] = readVarint(chunk.subarray(4), 0);
      reserve(declared);
      const raw = decodeSnappyRaw(chunk.subarray(4));
      assertChecksum(readUint32LE(chunk), raw);
      parts.push(raw);
    } else if (type === 0x01) {
      requireBytes(chunk, 0, 4, 'frame chunk');
      const raw = chunk.subarray(4);
      reserve(raw.length);
      assertChecksum(readUint32LE(chunk), raw);
      parts.push(Buffer.from(raw));
    } else if (type >= 0x02 && type <= 0x7f) {
      throw new Error(`snappy frame chunk type 0x${type.toString(16)} is not supported`);
    }
  }
  if (!identified) throw new Error('snappy frame must start with the stream identifier');
  return Buffer.concat(parts, produced);
}

/** Raw snappy, or a framed stream. JSON bytes are returned unchanged. */
export function decodeMaybeSnappy(buf) {
  if (buf.length >= 4 && buf[0] === 0xff) return decodeSnappyFrame(buf);
  if (buf.length > 0 && buf[0] === 0x7b) return buf;
  return decodeSnappyRaw(buf);
}
