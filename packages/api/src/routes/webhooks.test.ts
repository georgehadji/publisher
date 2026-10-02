/**
 * S4 -- the creation-time SSRF screen. No DB, no DNS: isBlockedHost is the
 * check every literal host and every resolved address goes through.
 */
import { describe, expect, it } from 'vitest';
import { isBlockedHost } from './webhooks.js';

const host = (url: string) => new URL(url).hostname;

describe('isBlockedHost', () => {
  it('sees through the brackets URL.hostname keeps on an IPv6 literal', () => {
    expect(host('https://[::1]/hook')).toBe('[::1]');
    for (const url of ['https://[::1]/', 'https://[fd00::1]/', 'https://[fe80::1]/', 'https://[ff02::1]/']) {
      expect(isBlockedHost(host(url)), url).toBe(true);
    }
  });

  it('judges an IPv4-mapped IPv6 address by the IPv4 address it maps', () => {
    // As DNS returns them (dotted) and as the URL parser rewrites them (hex).
    for (const a of ['::ffff:169.254.169.254', '::ffff:127.0.0.1', '::ffff:10.0.0.1', '::ffff:0.0.0.0']) {
      expect(isBlockedHost(a), a).toBe(true);
    }
    expect(host('https://[::ffff:169.254.169.254]/')).toBe('[::ffff:a9fe:a9fe]');
    expect(isBlockedHost(host('https://[::ffff:169.254.169.254]/'))).toBe(true);
    expect(isBlockedHost('::ffff:8.8.8.8')).toBe(false);
  });

  it('blocks 0.0.0.0, which reaches localhost on Linux', () => {
    expect(isBlockedHost('0.0.0.0')).toBe(true);
  });

  it('passes public addresses and leaves names to DNS', () => {
    for (const a of ['8.8.8.8', '2606:4700:4700::1111', 'example.com']) {
      expect(isBlockedHost(a), a).toBe(false);
    }
  });
});
