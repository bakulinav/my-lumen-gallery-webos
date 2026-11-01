// The home collection: works served over LAN from cybervolga (server/gallery_server.py).
// Loaded at startup and merged into the built-in ARTWORKS; failures leave the
// original online collections working on their own.

import type {Artwork} from '../config/art';

export const RADMUSEUM_SERVER = 'http://192.168.1.151:8790';

let base = RADMUSEUM_SERVER;
let extra: Artwork[] = [];

export function radmuseumArtworks (): Artwork[] { return extra; }
export function radmuseumServer (): string { return base; }

export async function loadRadmuseum (server: string): Promise<Artwork[]> {
	const res = await fetch(server.replace(/\/$/, '') + '/manifest.json', {cache: 'no-store'});
	if (!res.ok) throw new Error('HTTP ' + res.status);
	const data = await res.json();
	base = server.replace(/\/$/, '');
	extra = Array.isArray(data.entries) ? data.entries : [];
	return extra;
}
