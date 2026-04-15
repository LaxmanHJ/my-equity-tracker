import axios from 'axios';
import dotenv from 'dotenv';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { authenticator } from 'otplib';

dotenv.config();

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SESSION_FILE = path.resolve(__dirname, '../../data/angel_session.json');

const BASE_URL = 'https://apiconnect.angelone.in';
const LOGIN_URL = `${BASE_URL}/rest/auth/angelbroking/user/v1/loginByPassword`;
const REFRESH_URL = `${BASE_URL}/rest/auth/angelbroking/jwt/v1/generateTokens`;

let memorySession = null;

function baseHeaders(jwt = null) {
    const h = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'X-UserType': 'USER',
        'X-SourceID': 'WEB',
        'X-ClientLocalIP': process.env.ANGEL_CLIENT_LOCAL_IP || '127.0.0.1',
        'X-ClientPublicIP': process.env.ANGEL_CLIENT_PUBLIC_IP || '127.0.0.1',
        'X-MACAddress': process.env.ANGEL_MAC_ADDRESS || '00:00:00:00:00:00',
        'X-PrivateKey': process.env.ANGEL_API_KEY
    };
    if (jwt) h['Authorization'] = `Bearer ${jwt}`;
    return h;
}

function generateTotp() {
    const secret = process.env.ANGEL_TOTP_SECRET;
    if (!secret) {
        throw new Error('ANGEL_TOTP_SECRET not set in .env (base32 seed from 2FA setup)');
    }
    return authenticator.generate(secret.replace(/\s+/g, ''));
}

function loadSession() {
    if (memorySession) return memorySession;
    try {
        if (fs.existsSync(SESSION_FILE)) {
            memorySession = JSON.parse(fs.readFileSync(SESSION_FILE, 'utf8'));
            return memorySession;
        }
    } catch (e) {
        console.warn('[angelAuth] Could not read session file:', e.message);
    }
    return null;
}

function saveSession(session) {
    memorySession = session;
    try {
        fs.mkdirSync(path.dirname(SESSION_FILE), { recursive: true });
        fs.writeFileSync(SESSION_FILE, JSON.stringify(session, null, 2));
    } catch (e) {
        console.warn('[angelAuth] Could not persist session file:', e.message);
    }
}

function isExpired(session) {
    if (!session || !session.expiresAt) return true;
    // expire 5 minutes before the real deadline
    return Date.now() >= (session.expiresAt - 5 * 60 * 1000);
}

export async function loginByPassword() {
    const totp = generateTotp();
    const body = {
        clientcode: process.env.ANGEL_CLIENT_ID,
        password: process.env.ANGEL_CLIENT_PIN,
        totp,
        state: process.env.ANGEL_STATE || 'STATE'
    };

    const res = await axios.post(LOGIN_URL, body, { headers: baseHeaders(), timeout: 15000 });
    if (!res.data?.status || !res.data?.data?.jwtToken) {
        throw new Error(`Angel login failed: ${res.data?.message || JSON.stringify(res.data)}`);
    }
    const { jwtToken, refreshToken, feedToken } = res.data.data;
    const session = {
        jwtToken,
        refreshToken,
        feedToken,
        // Angel JWTs are valid ~24h; we give ourselves 20h to be safe.
        expiresAt: Date.now() + 20 * 60 * 60 * 1000,
        createdAt: Date.now()
    };
    saveSession(session);
    console.log('[angelAuth] Login OK, session cached');
    return session;
}

export async function refreshSession() {
    const s = loadSession();
    if (!s?.refreshToken) return loginByPassword();
    try {
        const res = await axios.post(
            REFRESH_URL,
            { refreshToken: s.refreshToken },
            { headers: baseHeaders(s.jwtToken), timeout: 15000 }
        );
        if (!res.data?.status || !res.data?.data?.jwtToken) {
            throw new Error(res.data?.message || 'Unknown refresh failure');
        }
        const { jwtToken, refreshToken, feedToken } = res.data.data;
        const session = {
            jwtToken,
            refreshToken: refreshToken || s.refreshToken,
            feedToken: feedToken || s.feedToken,
            expiresAt: Date.now() + 20 * 60 * 60 * 1000,
            createdAt: Date.now()
        };
        saveSession(session);
        console.log('[angelAuth] Refresh OK');
        return session;
    } catch (e) {
        console.warn('[angelAuth] Refresh failed, falling back to full login:', e.message);
        return loginByPassword();
    }
}

export async function getSession(force = false) {
    if (!force) {
        const cached = loadSession();
        if (cached && !isExpired(cached)) return cached;
    }
    return loginByPassword();
}

export function getAuthHeaders(jwt) {
    return baseHeaders(jwt);
}

export async function invalidateSession() {
    memorySession = null;
    try { fs.unlinkSync(SESSION_FILE); } catch {}
}

// CLI smoke test: node src/services/angelOneAuth.js
if (process.argv[1]?.includes('angelOneAuth')) {
    getSession(true)
        .then(s => console.log('JWT (truncated):', s.jwtToken.substring(0, 40) + '...', '\nExpires at:', new Date(s.expiresAt).toISOString()))
        .catch(err => { console.error('Error:', err.response?.data || err.message); process.exit(1); });
}

export default { loginByPassword, refreshSession, getSession, getAuthHeaders, invalidateSession };
