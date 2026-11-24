// SPDX-License-Identifier: Apache-2.0
import assert from 'node:assert/strict';
import test from 'node:test';
import {
  MAX_DECOMPRESSED,
  crc32c,
  decodeSnappyFrame,
  decodeSnappyRaw,
  maskCrc32c,
} from '../src/snappy.mjs';

function varint(value) {
  const bytes = [];
  let rest = value;
  while (rest > 0x7f) {
    bytes.push((rest & 0x7f) | 0x80);
    rest = Math.floor(rest / 128);
  }
  bytes.push(rest);
  return Buffer.from(bytes);
}

function frameChunk(type, data) {
  const header = Buffer.alloc(4);
  header[0] = type;
  header[1] = data.length & 0xff;
  header[2] = (data.length >> 8) & 0xff;
  header[3] = (data.length >> 16) & 0xff;
  return Buffer.concat([header, data]);
}

function identifier() {
  return frameChunk(0xff, Buffer.from('sNaPpY'));
}

function checksum(data) {
  const crc = Buffer.alloc(4);
  crc.writeUInt32LE(maskCrc32c(crc32c(data)));
  return crc;
}

function uncompressedChunk(data) {
  return frameChunk(0x01, Buffer.concat([checksum(data), Buffer.from(data)]));
}

test('crc32c matches the Castagnoli vector for 123456789', () => {
  assert.equal(crc32c(Buffer.from('123456789')), 0xe3069283);
});

test('a truncated literal does not decode as zeroes', () => {
  assert.throws(() => decodeSnappyRaw(Buffer.from([0x05, 0x10, 0x61, 0x62])), /snappy literal is truncated/);
});

test('a truncated extra-length literal fails', () => {
  assert.throws(() => decodeSnappyRaw(Buffer.from([0x01, 0xf0])), /snappy literal length is truncated/);
});

test('a truncated copy offset fails for 1-byte, 2-byte, and 4-byte forms', () => {
  assert.throws(() => decodeSnappyRaw(Buffer.from([0x04, 0x01])), /snappy copy offset is truncated/);
  assert.throws(() => decodeSnappyRaw(Buffer.from([0x01, 0x02])), /snappy copy offset is truncated/);
  assert.throws(() => decodeSnappyRaw(Buffer.from([0x01, 0x03])), /snappy copy offset is truncated/);
});

test('a truncated frame chunk fails', () => {
  assert.throws(() => decodeSnappyFrame(Buffer.from([0xff, 0x06])), /snappy frame chunk header is truncated/);
  assert.throws(
    () => decodeSnappyFrame(Buffer.concat([Buffer.from([0xff, 0x06, 0x00, 0x00]), Buffer.from('sN')])),
    /snappy frame chunk is truncated/
  );
});

test('a bad frame checksum fails', () => {
  const data = Buffer.from('ab');
  const crc = checksum(data);
  crc[0] ^= 0xff;
  const uncompressed = Buffer.concat([identifier(), frameChunk(0x01, Buffer.concat([crc, data]))]);
  assert.throws(() => decodeSnappyFrame(uncompressed), /snappy frame checksum is wrong/);
  const compressed = Buffer.concat([
    identifier(),
    frameChunk(0x00, Buffer.concat([crc, Buffer.from([0x02, 0x04, 0x61, 0x62])])),
  ]);
  assert.throws(() => decodeSnappyFrame(compressed), /snappy frame checksum is wrong/);
});

test('a valid framed literal round-trips and a missing identifier or unknown chunk fails', () => {
  const data = Buffer.from('ab');
  const compressed = Buffer.concat([
    identifier(),
    frameChunk(0x00, Buffer.concat([checksum(data), Buffer.from([0x02, 0x04, 0x61, 0x62])])),
  ]);
  assert.equal(decodeSnappyFrame(compressed).toString('utf8'), 'ab');
  assert.equal(decodeSnappyFrame(Buffer.concat([identifier(), uncompressedChunk(data)])).toString('utf8'), 'ab');
  assert.throws(() => decodeSnappyFrame(uncompressedChunk(data)), /must start with the stream identifier/);
  assert.throws(
    () => decodeSnappyFrame(Buffer.concat([identifier(), Buffer.from([0x02, 0x00, 0x00, 0x00])])),
    /chunk type 0x2 is not supported/
  );
});

test('an uncompressed length over the cap fails before allocation', () => {
  assert.throws(
    () => decodeSnappyRaw(varint(MAX_DECOMPRESSED + 1)),
    new RegExp(`snappy uncompressed length ${MAX_DECOMPRESSED + 1} exceeds ${MAX_DECOMPRESSED} bytes`)
  );
});

test('a framed total over the cap fails', () => {
  const payload = varint(MAX_DECOMPRESSED);
  const stream = Buffer.concat([
    identifier(),
    uncompressedChunk(Buffer.from('a')),
    frameChunk(0x00, Buffer.concat([Buffer.alloc(4), payload])),
  ]);
  assert.throws(
    () => decodeSnappyFrame(stream),
    new RegExp(`snappy decompressed total ${MAX_DECOMPRESSED + 1} exceeds ${MAX_DECOMPRESSED} bytes`)
  );
});

test('output that does not fill the declared length fails', () => {
  assert.throws(
    () => decodeSnappyRaw(Buffer.from([0x05, 0x04, 0x61, 0x62])),
    /snappy output is not filled\. expected 5 bytes, found 2/
  );
});
