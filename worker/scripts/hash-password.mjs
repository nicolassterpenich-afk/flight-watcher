// Génère la valeur à mettre dans le secret APP_PASSWORD_HASH.
//   node scripts/hash-password.mjs "mon mot de passe"
import { webcrypto as crypto } from 'node:crypto';

const password = process.argv[2];
if (!password) {
  console.error('Usage : node scripts/hash-password.mjs "<mot de passe>"');
  process.exit(1);
}

// Le runtime Workers refuse au-delà de 100 000 itérations (NotSupportedError).
// Miniflare, lui, les accepte : un hachage plus coûteux passerait donc en
// local et échouerait en production.
const ITERATIONS = 100000;
const salt = crypto.getRandomValues(new Uint8Array(16));
const key = await crypto.subtle.importKey('raw', new TextEncoder().encode(password), 'PBKDF2', false, ['deriveBits']);
const bits = await crypto.subtle.deriveBits({ name: 'PBKDF2', salt, iterations: ITERATIONS, hash: 'SHA-256' }, key, 256);

const b64 = (buf) => Buffer.from(buf).toString('base64');
console.log(`pbkdf2$${ITERATIONS}$${b64(salt)}$${b64(bits)}`);
