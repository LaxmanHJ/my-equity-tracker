#!/usr/bin/env node
/**
 * TOTP diagnostic — prints the 6-digit code our server is generating RIGHT NOW
 * and also tries the Angel login to isolate whether the seed or the API is at fault.
 *
 * Usage: node scripts/test-totp.mjs
 */
import 'dotenv/config';
import { authenticator } from 'otplib';
import axios from 'axios';

const secret = (process.env.ANGEL_TOTP_SECRET || '').replace(/\s+/g, '');

console.log('─── TOTP seed sanity ───');
console.log('Secret length           :', secret.length);
console.log('Secret first/last chars :', secret.length ? `${secret[0]}...${secret[secret.length - 1]}` : '(empty)');
console.log('Looks base32?           :', /^[A-Z2-7]+=*$/i.test(secret) ? 'YES' : 'NO');
console.log('System time (UTC)       :', new Date().toISOString());
console.log('System time (IST)       :', new Date().toLocaleString('en-IN', { timeZone: 'Asia/Kolkata' }));

console.log('\n─── 5 adjacent TOTP windows ───');
const stepSec = 30;
const nowSec = Math.floor(Date.now() / 1000);
for (let offset = -2; offset <= 2; offset++) {
    const epochMs = (nowSec + offset * stepSec) * 1000;
    authenticator.options = { step: stepSec, digits: 6, epoch: epochMs };
    const code = authenticator.generate(secret);
    const label = offset === 0 ? '   ← NOW' : `   (t${offset > 0 ? '+' : ''}${offset * stepSec}s)`;
    console.log(`  ${code}${label}`);
}
authenticator.options = {}; // reset

console.log('\nOpen your authenticator app, find the Angel One entry,');
console.log('and compare the 6-digit code there to the NOW code above.\n');

// Also try the actual Angel login to show the raw server response
console.log('─── Attempting live Angel login ───');
authenticator.options = {};
const liveTotp = authenticator.generate(secret);
console.log('Using TOTP:', liveTotp);

try {
    const res = await axios.post(
        'https://apiconnect.angelone.in/rest/auth/angelbroking/user/v1/loginByPassword',
        {
            clientcode: process.env.ANGEL_CLIENT_ID,
            password: process.env.ANGEL_CLIENT_PIN,
            totp: liveTotp,
            state: process.env.ANGEL_STATE || 'STATE'
        },
        {
            headers: {
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'X-UserType': 'USER',
                'X-SourceID': 'WEB',
                'X-ClientLocalIP': process.env.ANGEL_CLIENT_LOCAL_IP || '127.0.0.1',
                'X-ClientPublicIP': process.env.ANGEL_CLIENT_PUBLIC_IP || '127.0.0.1',
                'X-MACAddress': process.env.ANGEL_MAC_ADDRESS || '00:00:00:00:00:00',
                'X-PrivateKey': process.env.ANGEL_API_KEY
            },
            timeout: 15000,
            validateStatus: () => true
        }
    );
    console.log('HTTP status   :', res.status);
    console.log('Response body :', JSON.stringify(res.data, null, 2));
} catch (e) {
    console.error('Request failed:', e.message);
}
