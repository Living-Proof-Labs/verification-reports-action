// SPDX-License-Identifier: Apache-2.0
// Snappy raw block and framing, enough for GitHub attestation bundle_url bodies.
// Format: https://github.com/google/snappy/blob/main/format_description.txt

function readVarint(buf, offset) {
  let result = 0;
  let shift = 0;
  let index = offset;
  while (index < buf.length) {
    const byte = buf[index];
    index += 1;
    result |= (byte & 0x7f) << shift;
    if ((byte & 0x80) === 0) return [result, index];
    shift += 7;
    if (shift > 28) break;
  }
  throw new Error('snappy varint is truncated');
}

export function decodeSnappyRaw(buf) {
  const [length, start] = readVarint(buf, 0);
  const out = Buffer.alloc(length);
  let index = start;
  let written = 0;
  while (index < buf.length && written < length) {
    const tag = buf[index];
    index += 1;
    const kind = tag & 3;
    if (kind === 0) {
      let literal = tag >> 2;
      if (literal >= 60) {
        const extra = literal - 59;
        literal = 0;
        for (let part = 0; part < extra; part += 1) {
          literal |= buf[index] << (8 * part);
          index += 1;
        }
      }
      const size = literal + 1;
      buf.copy(out, written, index, index + size);
      index += size;
      written += size;
      continue;
    }
    let size;
    let offset;
    if (kind === 1) {
      size = ((tag >> 2) & 7) + 4;
      offset = ((tag & 0xe0) << 3) | buf[index];
      index += 1;
    } else if (kind === 2) {
      size = (tag >> 2) + 1;
      offset = buf[index] | (buf[index + 1] << 8);
      index += 2;
    } else {
      size = (tag >> 2) + 1;
      offset = buf[index] | (buf[index + 1] << 8) | (buf[index + 2] << 16) | (buf[index + 3] << 24);
      index += 4;
    }
    if (offset <= 0 || offset > written) throw new Error('snappy copy offset is outside the output');
    for (let count = 0; count < size; count += 1) {
      out[written] = out[written - offset];
      written += 1;
    }
  }
  if (written !== length) throw new Error('snappy block ended before the declared length');
  return out;
}

export function decodeSnappyFrame(buf) {
  let index = 0;
  const parts = [];
  let identified = false;
  while (index + 4 <= buf.length) {
    const type = buf[index];
    const size = buf[index + 1] | (buf[index + 2] << 8) | (buf[index + 3] << 16);
    index += 4;
    const chunk = buf.subarray(index, index + size);
    index += size;
    if (type === 0xff) {
      if (chunk.toString('utf8') !== 'sNaPpY') throw new Error('snappy frame identifier is not sNaPpY');
      identified = true;
    } else if (type === 0x00) {
      parts.push(decodeSnappyRaw(chunk));
    } else if (type === 0x01) {
      parts.push(chunk.subarray(4));
    } else if ((type & 0x80) === 0) {
      throw new Error(`snappy frame chunk ${type} is not supported`);
    }
  }
  if (!identified) throw new Error('snappy frame has no identifier');
  return Buffer.concat(parts);
}

/** Raw snappy, or a framed stream. JSON bytes are returned unchanged. */
export function decodeMaybeSnappy(buf) {
  if (buf.length >= 4 && buf[0] === 0xff && buf.subarray(1, 4).toString('utf8') === '\u0006\u0000\u0000') {
    return decodeSnappyFrame(buf);
  }
  if (buf.length > 0 && buf[0] === 0x7b) return buf;
  return decodeSnappyRaw(buf);
}
