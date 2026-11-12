// Entschlüsselung der Daten im Browser (Gegenstück zu crypto_box.py).
// PBKDF2-SHA256 -> AES-256-GCM. Das Passwort verlässt das Gerät nie.

const enc = new TextEncoder();
const dec = new TextDecoder();

export function b64ToBytes(b64) {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function bytesToB64(bytes) {
  let bin = "";
  for (const b of new Uint8Array(bytes)) bin += String.fromCharCode(b);
  return btoa(bin);
}

export async function deriveKey(passphrase, kdf) {
  const base = await crypto.subtle.importKey("raw", enc.encode(passphrase), "PBKDF2", false, ["deriveKey"]);
  return crypto.subtle.deriveKey(
    { name: "PBKDF2", hash: "SHA-256", salt: b64ToBytes(kdf.salt), iterations: kdf.iter },
    base,
    { name: "AES-GCM", length: 256 },
    true,
    ["decrypt", "encrypt"],
  );
}

export async function exportKey(key) {
  return bytesToB64(await crypto.subtle.exportKey("raw", key));
}

export async function importKey(rawB64) {
  return crypto.subtle.importKey("raw", b64ToBytes(rawB64), { name: "AES-GCM" }, true, ["decrypt", "encrypt"]);
}

// Öffnet eine Datei aus dem Export. Unverschlüsselte Dateien (privater Server) gehen auch.
export async function openSealed(key, sealed) {
  if (!sealed.enc) return sealed.data;
  const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv: b64ToBytes(sealed.iv) }, key, b64ToBytes(sealed.ct));
  return JSON.parse(dec.decode(plain));
}

// Kleine Geheimnisse (z. B. einen API-Schlüssel) auf dem Gerät verschlüsselt ablegen
export async function sealText(key, text) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, enc.encode(text));
  return { iv: bytesToB64(iv), ct: bytesToB64(ct) };
}

export async function openText(key, box) {
  const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv: b64ToBytes(box.iv) }, key, b64ToBytes(box.ct));
  return dec.decode(plain);
}
