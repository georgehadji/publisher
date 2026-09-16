/**
 * E5.3 -- ops helper. PUBLISHER_API_TOKENS/PUBLISHER_ADMIN_TOKENS store an
 * argon2id hash of each bearer token, never the plaintext (a leaked env
 * dump or compose file must not hand over a directly usable credential).
 * This is how an operator turns a chosen token into what actually goes in
 * the env var; the plaintext token is only ever given to the caller.
 *
 * Usage: npm run hash-token -- <token>
 */
import argon2 from 'argon2';

const token = process.argv[2];
if (!token) {
  console.error('usage: hash-token <token>');
  process.exit(1);
}
console.log(await argon2.hash(token));
